"""
utils.py — Shared utility functions for the market maker bot.

Tick rounding, minimum notional validation, and exchange market info helpers.

NOTE: The typed MarketSpec in market_spec.py is the preferred path for new code.
These float-based helpers are kept for backward compatibility with existing
callers (quote_engine, order_manager, backtest).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from market_spec import MarketSpec, build_market_spec

logger = logging.getLogger(__name__)


def round_to_tick(price: float, tick_size: float) -> float:
    """Round *price* down to the nearest multiple of *tick_size*.

    Exchange APIs reject prices that are not aligned to the instrument's
    tick size.  We always round *toward* the mid (i.e. bids round down,
    asks round up) but this function simply rounds to the nearest tick.
    The caller is responsible for directional rounding when needed.

    >>> round_to_tick(100.123, 0.01)
    100.12
    >>> round_to_tick(100.125, 0.05)
    100.1
    """
    if tick_size <= 0:
        return price
    # Use Decimal-style rounding to avoid floating-point drift
    precision = _tick_precision(tick_size)
    return round(math.floor(price / tick_size) * tick_size, precision)


def round_to_tick_up(price: float, tick_size: float) -> float:
    """Round *price* up to the nearest multiple of *tick_size* (for asks)."""
    if tick_size <= 0:
        return price
    precision = _tick_precision(tick_size)
    return round(math.ceil(price / tick_size) * tick_size, precision)


def round_size(size: float, lot_size: float) -> float:
    """Round *size* down to the nearest lot/step size."""
    if lot_size <= 0:
        return size
    precision = _tick_precision(lot_size)
    return round(math.floor(size / lot_size) * lot_size, precision)


def _tick_precision(tick: float) -> int:
    """Return the number of decimal places implied by *tick*."""
    s = f"{tick:.15g}"
    if "." in s:
        return len(s.rstrip("0").split(".")[1])
    return 0


def validate_min_notional(price: float, size: float, min_notional: float) -> bool:
    """Return True if price * size meets the exchange's minimum notional."""
    if min_notional <= 0:
        return True
    return price * size >= min_notional


def adjust_size_for_notional(
    price: float,
    size: float,
    min_notional: float,
    lot_size: float,
) -> float:
    """Increase *size* to the minimum that satisfies *min_notional*.

    Returns the adjusted size rounded to *lot_size*, or 0.0 if even a
    single lot doesn't meet the notional requirement (shouldn't happen
    in practice for reasonable instruments).
    """
    if price <= 0:
        return 0.0
    if validate_min_notional(price, size, min_notional):
        return size
    required = min_notional / price
    adjusted = round_size(required, lot_size)
    # If rounding down dropped below notional, step up by one lot
    if adjusted * price < min_notional:
        adjusted += lot_size
    adjusted = round(adjusted, _tick_precision(lot_size))
    return adjusted


def fetch_market_info(exchange: Any, symbol: str) -> dict:
    """Fetch tick_size, lot_size, and min_notional from the exchange.

    Returns a dict with keys:
        tick_size:     float — minimum price increment
        lot_size:      float — minimum order size increment
        min_notional:  float — minimum order value (price * size)
        contract_size: float — contract multiplier (1 for spot)
        market_spec:   MarketSpec — typed specification (new)

    The dict format is maintained for backward compatibility with
    quote_engine and order_manager. New code should use the MarketSpec
    directly.
    """
    spec = build_market_spec(exchange, symbol)

    return {
        "tick_size": float(spec.price_tick),
        "lot_size": float(spec.amount_step),
        "min_notional": float(spec.min_notional) if spec.min_notional is not None else 0.0,
        "contract_size": float(spec.contract_size),
        "market_spec": spec,
    }
