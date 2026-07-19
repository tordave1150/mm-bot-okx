"""
market_spec.py — Typed exchange market specification and pre-order validation.

Provides a frozen MarketSpec dataclass with conversion utilities between
requested lot size, base-asset quantity, contract quantity, notional value,
and required margin.

All exchange-facing quantities use Decimal arithmetic to avoid floating-point
drift in prices and contract sizes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)


class OrderValidationError(Exception):
    """Raised when a proposed order fails pre-submission validation."""


@dataclass(frozen=True)
class MarketSpec:
    """Typed specification of exchange market/instrument metadata.

    All numeric fields use Decimal for precision.
    """

    symbol: str
    contract_size: Decimal         # Base units per contract (e.g. 0.01 for BTC perp)
    amount_step: Decimal           # Minimum amount increment (lot step)
    min_amount: Decimal            # Minimum order amount
    min_notional: Decimal | None   # Minimum order value (price × amount), or None
    price_tick: Decimal            # Minimum price increment
    amount_precision: int | None   # Decimal places for amount, or None
    price_precision: int | None    # Decimal places for price, or None
    linear: bool                   # True for linear (USDT-margined) contracts
    inverse: bool                  # True for inverse (coin-margined) contracts

    # ── Price rounding ──────────────────────────────────────────────────

    def round_price_down(self, price: Decimal) -> Decimal:
        """Round price down to the nearest tick (for bids)."""
        if self.price_tick <= 0:
            return price
        return (price / self.price_tick).to_integral_value(rounding=ROUND_DOWN) * self.price_tick

    def round_price_up(self, price: Decimal) -> Decimal:
        """Round price up to the nearest tick (for asks)."""
        if self.price_tick <= 0:
            return price
        return (price / self.price_tick).to_integral_value(rounding=ROUND_UP) * self.price_tick

    # ── Amount rounding ─────────────────────────────────────────────────

    def round_amount_down(self, amount: Decimal) -> Decimal:
        """Round amount down to the nearest lot step."""
        if self.amount_step <= 0:
            return amount
        return (amount / self.amount_step).to_integral_value(rounding=ROUND_DOWN) * self.amount_step

    # ── Notional / margin calculations ──────────────────────────────────

    def compute_notional(self, price: Decimal, amount: Decimal) -> Decimal:
        """Compute order notional value in quote currency."""
        if self.linear:
            return price * amount * self.contract_size
        elif self.inverse:
            if price <= 0:
                return Decimal("0")
            return amount * self.contract_size / price
        else:
            return price * amount

    def compute_required_margin(
        self,
        price: Decimal,
        amount: Decimal,
        leverage: Decimal,
    ) -> Decimal:
        """Estimate initial margin required for an order."""
        notional = self.compute_notional(price, amount)
        if leverage <= 0:
            return notional
        return notional / leverage

    def estimate_fee(
        self,
        price: Decimal,
        amount: Decimal,
        is_maker: bool,
        maker_rate: Decimal,
        taker_rate: Decimal,
    ) -> Decimal:
        """Estimate the fee for an order."""
        notional = self.compute_notional(price, amount)
        rate = maker_rate if is_maker else taker_rate
        return notional * rate


def validate_order(
    spec: MarketSpec,
    side: str,
    price: Decimal,
    amount: Decimal,
    best_bid: Decimal,
    best_ask: Decimal,
    current_inventory: Decimal,
    max_inventory: Decimal,
    available_equity: Decimal,
    leverage: Decimal,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    safety_buffer: Decimal = Decimal("0.05"),
) -> None:
    """Validate an order against all pre-submission checks (AGENTS.md §6).

    Raises OrderValidationError if any check fails.
    """
    errors: list[str] = []

    # Price must be positive
    if price <= 0:
        errors.append(f"Price must be positive, got {price}")

    # Amount must be positive
    if amount <= 0:
        errors.append(f"Amount must be positive, got {amount}")

    # Amount conforms to step size
    if spec.amount_step > 0:
        remainder = amount % spec.amount_step
        if remainder != 0:
            errors.append(
                f"Amount {amount} does not conform to step size {spec.amount_step}"
            )

    # Price conforms to tick size
    if spec.price_tick > 0 and price > 0:
        remainder = price % spec.price_tick
        if remainder != 0:
            errors.append(
                f"Price {price} does not conform to tick size {spec.price_tick}"
            )

    # Minimum amount check
    if amount < spec.min_amount:
        errors.append(
            f"Amount {amount} below minimum {spec.min_amount}"
        )

    # Minimum notional check
    if spec.min_notional is not None and price > 0 and amount > 0:
        notional = spec.compute_notional(price, amount)
        if notional < spec.min_notional:
            errors.append(
                f"Notional {notional} below minimum {spec.min_notional}"
            )

    # Bid must be below ask (no book crossing)
    if best_bid > 0 and best_ask > 0 and price > 0:
        if side == "buy" and price >= best_ask:
            errors.append(
                f"Buy price {price} would cross the ask at {best_ask}"
            )
        if side == "sell" and price <= best_bid:
            errors.append(
                f"Sell price {price} would cross the bid at {best_bid}"
            )

    # Post-fill inventory check
    signed_amount = amount if side == "buy" else -amount
    post_fill_inventory = current_inventory + signed_amount
    if abs(post_fill_inventory) > max_inventory:
        errors.append(
            f"Post-fill inventory {post_fill_inventory} would exceed "
            f"max inventory {max_inventory}"
        )

    # Margin sufficiency check
    if price > 0 and amount > 0:
        required_margin = spec.compute_required_margin(price, amount, leverage)
        estimated_fee = spec.estimate_fee(
            price, amount, is_maker=True,
            maker_rate=maker_fee_rate, taker_rate=taker_fee_rate,
        )
        total_required = required_margin + estimated_fee + safety_buffer
        if total_required > available_equity:
            errors.append(
                f"Insufficient equity: need {total_required} "
                f"(margin={required_margin} + fee={estimated_fee} + buffer={safety_buffer}), "
                f"have {available_equity}"
            )

    if errors:
        msg = f"Order validation failed ({side} {amount} @ {price}):\n" + \
              "\n".join(f"  - {e}" for e in errors)
        raise OrderValidationError(msg)


def build_market_spec(exchange: Any, symbol: str) -> MarketSpec:
    """Build a MarketSpec from exchange market metadata.

    Loads markets from the exchange and extracts all relevant fields.
    Falls back to conservative defaults if fields are missing.

    Parameters
    ----------
    exchange : ccxt.Exchange
        A CCXT exchange instance with markets loaded.
    symbol : str
        The trading symbol (e.g., "BTC/USDT:USDT").

    Returns
    -------
    MarketSpec
    """
    try:
        if not exchange.markets:
            exchange.load_markets()
        market = exchange.market(symbol)

        # ── Contract type ────────────────────────────────────────────────
        linear = bool(market.get("linear", False))
        inverse = bool(market.get("inverse", False))
        contract_size = Decimal(str(market.get("contractSize", 1) or 1))

        # ── Precision — CCXT may report as step size OR decimal count ────
        precision = market.get("precision", {})
        raw_price_prec = precision.get("price")
        raw_amount_prec = precision.get("amount")

        # Determine tick/step from limits first (more reliable), fall back to precision
        limits = market.get("limits", {})

        price_limits = limits.get("price", {})
        amount_limits = limits.get("amount", {})

        # Price tick
        price_tick = _resolve_step(raw_price_prec, price_limits.get("min"), default="0.1")

        # Amount step
        amount_step = _resolve_step(raw_amount_prec, amount_limits.get("min"), default="0.001")

        # Min amount
        min_amount_val = amount_limits.get("min")
        min_amount = Decimal(str(min_amount_val)) if min_amount_val else amount_step

        # Min notional
        cost_limits = limits.get("cost", {})
        min_notional_val = cost_limits.get("min")
        min_notional = Decimal(str(min_notional_val)) if min_notional_val else None

        # Precision counts
        amount_precision = int(raw_amount_prec) if isinstance(raw_amount_prec, int) else None
        price_precision = int(raw_price_prec) if isinstance(raw_price_prec, int) else None

        spec = MarketSpec(
            symbol=symbol,
            contract_size=contract_size,
            amount_step=amount_step,
            min_amount=min_amount,
            min_notional=min_notional,
            price_tick=price_tick,
            amount_precision=amount_precision,
            price_precision=price_precision,
            linear=linear,
            inverse=inverse,
        )
        logger.info("MarketSpec for %s: %s", symbol, spec)
        return spec

    except Exception:
        logger.exception("Failed to build MarketSpec for %s; using defaults", symbol)
        return MarketSpec(
            symbol=symbol,
            contract_size=Decimal("0.01"),
            amount_step=Decimal("0.01"),
            min_amount=Decimal("0.01"),
            min_notional=Decimal("5"),
            price_tick=Decimal("0.1"),
            amount_precision=None,
            price_precision=None,
            linear=True,
            inverse=False,
        )


def _resolve_step(
    precision_val: Any,
    limit_min: Any,
    default: str = "0.1",
) -> Decimal:
    """Resolve a step size from CCXT precision/limits.

    CCXT may report precision as:
    - An integer (number of decimal places)
    - A float/Decimal (the actual step size)
    """
    # Prefer limits.min if it looks like a step
    if limit_min is not None:
        try:
            val = Decimal(str(limit_min))
            if val > 0:
                return val
        except (InvalidOperation, ValueError):
            pass

    if precision_val is None:
        return Decimal(default)

    try:
        if isinstance(precision_val, int):
            # Integer = number of decimal places → convert to step
            return Decimal(10) ** Decimal(-precision_val)
        val = Decimal(str(precision_val))
        if val > 0:
            return val
    except (InvalidOperation, ValueError):
        pass

    return Decimal(default)
