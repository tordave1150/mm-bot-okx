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

from config import Config
from market_state import MarketState
from utils import (
    adjust_size_for_notional,
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
        lot = market_info.get("lot_size", 0.001)
        min_notional = market_info.get("min_notional", 5.0)

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
        if self.cfg.queue_positioning == "improve":
            bid_raw = max(bid_raw, ms.best_bid + tick)
            ask_raw = min(ask_raw, ms.best_ask - tick)

        # ── 7. Tick rounding ────────────────────────────────────────────
        bid_price = round_to_tick(bid_raw, tick)
        ask_price = round_to_tick_up(ask_raw, tick)

        # Ensure bid < ask
        if bid_price >= ask_price:
            bid_price = round_to_tick(ms.mid_price - tick, tick)
            ask_price = round_to_tick_up(ms.mid_price + tick, tick)

        # ── 8. Order sizing ─────────────────────────────────────────────
        base_size = self._base_order_size(ms.mid_price) * size_multiplier
        bid_size = self._compute_order_size(base_size, inventory, "buy", lot)
        ask_size = self._compute_order_size(base_size, inventory, "sell", lot)

        # ── 9. Min notional validation ──────────────────────────────────
        bid_valid = True
        ask_valid = True

        if bid_size > 0:
            bid_size = adjust_size_for_notional(bid_price, bid_size, min_notional, lot)
            if not validate_min_notional(bid_price, bid_size, min_notional):
                bid_valid = False
        else:
            bid_valid = False

        if ask_size > 0:
            ask_size = adjust_size_for_notional(ask_price, ask_size, min_notional, lot)
            if not validate_min_notional(ask_price, ask_size, min_notional):
                ask_valid = False
        else:
            ask_valid = False

        return Quotes(
            bid_price=bid_price,
            bid_size=bid_size,
            ask_price=ask_price,
            ask_size=ask_size,
            bid_valid=bid_valid,
            ask_valid=ask_valid,
            reservation_price=reservation,
            half_spread=half,
        )

    # ── Strategy Models ─────────────────────────────────────────────────

    def _avellaneda(
        self, S: float, q: float, sigma: float
    ) -> tuple[float, float]:
        """Avellaneda-Stoikov reservation price and optimal half-spread.

        reservation = S - q * gamma * sigma^2 * tau
        half_spread = (1/gamma) * ln(1 + gamma/k) + (gamma * sigma^2 * tau) / 2
        """
        gamma = self.cfg.gamma
        k = self.cfg.k
        tau = self.cfg.tau

        reservation = S - q * gamma * (sigma ** 2) * tau

        # Guard against gamma/k edge cases
        if gamma <= 0 or k <= 0:
            half = sigma * 2  # fallback
        else:
            half = (1.0 / gamma) * math.log(1.0 + gamma / k) + (
                gamma * (sigma ** 2) * tau
            ) / 2.0

        return reservation, half

    def _volatility(
        self, S: float, sigma: float, tick_size: float
    ) -> tuple[float, float]:
        """Volatility-based fallback: simpler spread calculation.

        half_spread = volatility_multiplier * sigma + base_spread_ticks * tick_size
        """
        half = (
            self.cfg.volatility_multiplier * sigma
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
