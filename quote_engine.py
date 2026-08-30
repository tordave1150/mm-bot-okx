"""
quote_engine.py — Avellaneda-Stoikov and volatility-based quote generation.

Computes bid/ask prices and sizes given the current market state and inventory.
Applies inventory skewing, imbalance skewing, size adjustment, tick rounding,
and minimum notional validation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal

from config import Config
from market_state import MarketState
from market_spec import MarketSpec, OrderValidationError
from utils import (
    round_size,
    round_to_tick,
    round_to_tick_up,
    validate_min_notional,
)

logger = logging.getLogger(__name__)


@dataclass
class Quotes:
    """A pair of quotes ready for submission (or flagged as invalid)."""

    bid_price: float = 0.0
    bid_size: float = 0.0
    ask_price: float = 0.0
    ask_size: float = 0.0
    bid_valid: bool = False      # False → don't submit bid
    ask_valid: bool = False      # False → don't submit ask
    reservation_price: float = 0.0
    half_spread: float = 0.0
    raw_bid_price: float = 0.0
    raw_ask_price: float = 0.0
    bid_reduce_only: bool = False
    ask_reduce_only: bool = False
    post_only_clamps: int = 0
    bid_skip_reason: str = ""
    ask_skip_reason: str = ""


class QuoteEngine:
    """Generates market-maker quotes using A-S or volatility-based models.

    Public API:
        generate(market_state, inventory, market_info, regime_multipliers)
            → Quotes
    """

    def __init__(self, config: Config):
        self.cfg = config

    def generate(
        self,
        ms: MarketState,
        inventory: float,
        market_info: dict,
        spread_multiplier: float = 1.0,
        size_multiplier: float = 1.0,
        remaining_drawdown_budget_usdt: float | None = None,
    ) -> Quotes:
        """Build a Quotes object from current state.

        Parameters
        ----------
        ms : MarketState
            Current market snapshot.
        inventory : float
            Current net position in base currency.
        market_info : dict
            Keys: tick_size, lot_size, min_notional, contract_size.
        spread_multiplier : float
            Applied to half_spread (from regime detector).  >1 widens.
        size_multiplier : float
            Applied to base order size (from regime detector).  <1 shrinks.
        """
        if ms.mid_price <= 0 or ms.volatility <= 0:
            logger.debug("Skipping quote generation: mid=%.4f vol=%.6f", ms.mid_price, ms.volatility)
            return Quotes()

        tick = market_info.get("tick_size", 0.1)
        # Quote sizes are canonical base-asset quantities. ``lot_size`` is an
        # exchange contract step and must never be used as a base step.
        lot = market_info.get("base_step", market_info.get("lot_size", 0.001))
        min_notional = market_info.get("min_notional", 5.0)
        spec: MarketSpec | None = market_info.get("market_spec")

        # ── 1. Core model ───────────────────────────────────────────────
        if self.cfg.strategy_mode == "avellaneda":
            reservation, half = self._avellaneda(
                ms.mid_price, inventory, ms.volatility
            )
        else:
            reservation, half = self._volatility(ms.mid_price, ms.volatility, tick)

        # ── 2. Apply regime multiplier to spread ────────────────────────
        half *= spread_multiplier

        # ── 3. Inventory skewing ────────────────────────────────────────
        reservation, half = self._apply_inventory_skew(
            reservation, half, inventory, self.cfg.max_inventory
        )

        # ── 4. Raw bid/ask ──────────────────────────────────────────────
        bid_raw = reservation - half
        ask_raw = reservation + half

        # ── 5. Imbalance skewing ────────────────────────────────────────
        bid_raw, ask_raw = self._apply_imbalance_skew(
            bid_raw, ask_raw, ms.order_book_imbalance
        )

        # ── 6. Queue positioning ────────────────────────────────────────
        model_bid_raw = bid_raw
        model_ask_raw = ask_raw

        if self.cfg.queue_positioning == "improve":
            # Improve only model quotes already competing inside the spread.
            # Pulling an intentionally defensive quote from outside the book
            # to the touch erases gamma/volatility activity and systematically
            # selects stale orders after adverse price moves.
            if bid_raw >= ms.best_bid:
                bid_raw = min(bid_raw + tick, ms.best_ask - tick)
            if ask_raw <= ms.best_ask:
                ask_raw = max(ask_raw - tick, ms.best_bid + tick)

        at_long_limit = inventory >= self.cfg.max_inventory - 1e-12
        at_short_limit = inventory <= -self.cfg.max_inventory + 1e-12
        if at_long_limit:
            ask_raw = min(ask_raw, ms.best_ask)
        if at_short_limit:
            bid_raw = max(bid_raw, ms.best_bid)

        clamps = 0
        bid_ceiling = ms.best_ask - tick
        ask_floor = ms.best_bid + tick
        if bid_raw > bid_ceiling:
            bid_raw = bid_ceiling
            clamps += 1
        if ask_raw < ask_floor:
            ask_raw = ask_floor
            clamps += 1

        # ── 7. Tick rounding ────────────────────────────────────────────
        bid_price = round_to_tick(bid_raw, tick)
        ask_price = round_to_tick_up(ask_raw, tick)

        bid_post_only = (
            ms.best_bid > 0 and ms.best_ask > ms.best_bid and bid_price < ms.best_ask
        )
        ask_post_only = (
            ms.best_bid > 0 and ms.best_ask > ms.best_bid and ask_price > ms.best_bid
        )

        # ── 8. Order sizing ─────────────────────────────────────────────
        base_size = self._base_order_size(ms.mid_price) * size_multiplier
        bid_size = self._compute_order_size(base_size, inventory, "buy", lot)
        ask_size = self._compute_order_size(base_size, inventory, "sell", lot)

        # ── 9. Contract/min-notional/inventory validation ───────────────
        bid_valid = bid_post_only and self._quote_size_valid(
            spec, bid_price, bid_size, inventory, "buy", min_notional
        )
        ask_valid = ask_post_only and self._quote_size_valid(
            spec, ask_price, ask_size, inventory, "sell", min_notional
        )
        bid_reduce_only = inventory < 0 and bid_size <= abs(inventory) + 1e-12
        ask_reduce_only = inventory > 0 and ask_size <= abs(inventory) + 1e-12
        bid_skip_reason = "LOCAL_POST_ONLY_REJECT" if not bid_post_only else ""
        ask_skip_reason = "LOCAL_POST_ONLY_REJECT" if not ask_post_only else ""

        if remaining_drawdown_budget_usdt is not None:
            loss_fraction = (
                2.33 * ms.volatility
                + self.cfg.maker_fee_rate
                + self.cfg.taker_fee_rate
                + self.cfg.emergency_slippage_bps / 10_000.0
            )
            if (
                bid_valid and not bid_reduce_only
                and bid_price * bid_size * loss_fraction
                > remaining_drawdown_budget_usdt
            ):
                bid_valid = False
                bid_skip_reason = "LOCAL_RISK_REJECT:DRAWDOWN_BUDGET"
            if (
                ask_valid and not ask_reduce_only
                and ask_price * ask_size * loss_fraction
                > remaining_drawdown_budget_usdt
            ):
                ask_valid = False
                ask_skip_reason = "LOCAL_RISK_REJECT:DRAWDOWN_BUDGET"

        return Quotes(
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
            bid_valid=bid_valid,
            ask_valid=ask_valid,
            reservation_price=reservation,
            half_spread=half,
            raw_bid_price=model_bid_raw,
            raw_ask_price=model_ask_raw,
            bid_reduce_only=bid_reduce_only,
            ask_reduce_only=ask_reduce_only,
            post_only_clamps=clamps,
            bid_skip_reason=bid_skip_reason,
            ask_skip_reason=ask_skip_reason,
        )

    # ── Strategy Models ─────────────────────────────────────────────────

    def _avellaneda(
        self, S: float, q: float, sigma: float
    ) -> tuple[float, float]:
        """Return quotes using a documented return-variance parameterization.

        ``MarketState.volatility`` is a per-observation log-return volatility.
        ``S * sigma * sqrt(2*tau)`` is therefore a two-sided diffusion
        distance over the configured horizon. Gamma scales that
        adverse-selection buffer.
        ``S * sigma**2 * tau`` is retained as the smaller variance premium.
        Squaring ``S * sigma`` would introduce an extra price factor and create
        multi-thousand-dollar spreads.

        ``q`` is converted to fixed lots before applying inventory risk. The
        configured gamma is dimensionless and ``k`` parameterizes the
        liquidity-distance term in quote-price units.
        """
        gamma = self.cfg.gamma
        k = self.cfg.k
        tau = self.cfg.tau

        variance_distance = S * (sigma ** 2) * tau
        adverse_distance = gamma * S * sigma * math.sqrt(max(2.0 * tau, 0.0))
        inventory_lots = q / max(self.cfg.fixed_lot_size, 1e-12)
        reservation = S - inventory_lots * adverse_distance

        # Guard against gamma/k edge cases
        if gamma <= 0 or k <= 0:
            half = max(S * sigma * math.sqrt(max(tau, 0.0)), 0.0)
        else:
            half = (
                (1.0 / gamma) * math.log(1.0 + gamma / k)
                + adverse_distance
                + gamma * variance_distance / 2.0
            )

        return reservation, half

    def _volatility(
        self, S: float, sigma: float, tick_size: float
    ) -> tuple[float, float]:
        """Volatility-based fallback: simpler spread calculation.

        half_spread = volatility_multiplier * sigma + base_spread_ticks * tick_size
        """
        sigma_price = S * sigma
        half = (
            self.cfg.volatility_multiplier * sigma_price
            + self.cfg.base_spread_ticks * tick_size
        )
        return S, half

    # ── Skewing ─────────────────────────────────────────────────────────

    def _apply_inventory_skew(
        self,
        reservation: float,
        half: float,
        q: float,
        max_q: float,
    ) -> tuple[float, float]:
        """Shift reservation price based on inventory position.

        As |q| → max_q, we push the reservation price to encourage
        inventory-reducing trades.  The skew factor controls aggressiveness.
        """
        if max_q <= 0:
            return reservation, half

        # Normalised inventory: -1 to +1
        q_norm = q / max_q
        skew = self.cfg.inventory_skew_factor

        # Shift reservation price opposite to inventory direction
        # Positive q → lower reservation → cheaper ask to sell more
        reservation -= q_norm * skew * half

        return reservation, half

    def _apply_imbalance_skew(
        self,
        bid: float,
        ask: float,
        imbalance: float,
    ) -> tuple[float, float]:
        """Adjust quotes based on order book imbalance.

        Positive imbalance (bid-heavy) → skew quotes down slightly
        (anticipate price decline from large bid wall being hit).
        """
        factor = self.cfg.imbalance_skew_factor
        # imbalance ∈ (-1, 1); multiply by half the spread for scaling
        spread = ask - bid
        shift = imbalance * factor * spread * 0.5

        bid -= shift
        ask -= shift

        return bid, ask

    # ── Sizing ──────────────────────────────────────────────────────────

    def _base_order_size(self, mid_price: float) -> float:
        """Return the fixed lot size — no percentage-based sizing."""
        return self.cfg.fixed_lot_size

    def _compute_order_size(
        self,
        base_size: float,
        inventory: float,
        side: str,
        lot_size: float,
    ) -> float:
        """Linearly reduce size as |inventory| → max_inventory.

        Stop quoting the breaching side entirely at the limit.
        """
        max_q = self.cfg.max_inventory
        if max_q <= 0:
            return round_size(base_size, lot_size)

        # Check if this side would breach the inventory limit
        if side == "buy" and inventory >= max_q:
            return 0.0
        if side == "sell" and inventory <= -max_q:
            return 0.0

        # Linear reduction factor: 1.0 at q=0, 0.0 at |q|=max_q
        q_abs = abs(inventory)
        reduction = max(0.0, 1.0 - q_abs / max_q)

        # Only reduce the side that would increase |inventory|
        if (side == "buy" and inventory > 0) or (side == "sell" and inventory < 0):
            adjusted = base_size * reduction
        else:
            adjusted = base_size  # Full size on the side that reduces inventory

        return round_size(adjusted, lot_size)

    def _quote_size_valid(
        self,
        spec: MarketSpec | None,
        price: float,
        base_size: float,
        inventory_base: float,
        side: str,
        legacy_min_notional: float,
    ) -> bool:
        """Validate a canonical base quantity without increasing exposure."""
        if price <= 0 or base_size <= 0:
            return False

        signed = base_size if side == "buy" else -base_size
        if abs(inventory_base + signed) > self.cfg.max_inventory + 1e-12:
            return False

        if spec is None:
            return validate_min_notional(price, base_size, legacy_min_notional)

        try:
            contracts = spec.base_to_contracts(Decimal(str(base_size)), exact=True)
        except OrderValidationError:
            return False
        if contracts < spec.min_amount:
            return False
        if spec.min_notional is not None:
            notional = spec.compute_notional(Decimal(str(price)), contracts)
            if notional < spec.min_notional:
                return False
        return True
