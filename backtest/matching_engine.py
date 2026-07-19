"""
backtest/matching_engine.py — Limit-order fill simulation for backtesting.

Fill model modes (AGENTS.md §11.1):
    optimistic:     Full fill at limit price on any touch/cross. No queue, no slippage.
    probabilistic:  Fill probability based on distance from mid. Partial fills possible.
    conservative:   Requires full cross (not just touch). Higher slippage.

Usage::

    engine = MatchingEngine(maker_fee_rate=0.0002, taker_fee_rate=0.0005)
    engine.place_order("buy", bid_price, bid_size)
    engine.place_order("sell", ask_price, ask_size)

    fills = engine.check_fills(current_tick, fill_tracker)
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from fill_tracker import FillTracker

from fill_tracker import Fill, FillTracker  # noqa: E402


# ── PendingOrder ─────────────────────────────────────────────────────────────

@dataclass
class PendingOrder:
    """A resting limit order waiting to be matched."""

    order_id: str
    side: str         # "buy" or "sell"
    price: float      # limit price
    size: float       # in base currency
    placed_tick: int  # tick index when placed


# ── MatchingEngine ────────────────────────────────────────────────────────────

class MatchingEngine:
    """Simulate limit-order fills against synthetic tick data.

    Supports three fill model modes and configurable fee rates.

    Parameters
    ----------
    fill_mode : str
        One of "optimistic", "probabilistic", "conservative".
    maker_fee_rate : float
        Fee rate for passive (resting) fills.
    taker_fee_rate : float
        Fee rate for aggressive/marketable fills.
    """

    def __init__(
        self,
        fill_mode: Literal["optimistic", "probabilistic", "conservative"] = "optimistic",
        maker_fee_rate: float = 0.0,
        taker_fee_rate: float = 0.0,
    ) -> None:
        self._pending: dict[str, PendingOrder] = {}   # order_id → PendingOrder
        self._tick_index: int = 0
        self._fill_id_counter = itertools.count(1)    # Per-instance, not global
        self.total_bid_fills: int = 0
        self.total_ask_fills: int = 0
        self.total_fees: float = 0.0

        self.fill_mode = fill_mode
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate

    def _next_fill_id(self) -> str:
        return f"bt-fill-{next(self._fill_id_counter):08d}"

    # ── Public API ──────────────────────────────────────────────────────────

    def place_order(self, side: str, price: float, size: float) -> str:
        """Register a new resting limit order.

        Returns
        -------
        str
            A unique order ID for this backtest run.
        """
        if size <= 0 or price <= 0:
            return ""

        order_id = f"bt-order-{self._tick_index:06d}-{side}"
        self._pending[order_id] = PendingOrder(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            placed_tick=self._tick_index,
        )
        return order_id

    def cancel_all(self) -> None:
        """Cancel all resting orders (e.g. after a re-quote or kill-switch)."""
        self._pending.clear()

    def cancel_order(self, order_id: str) -> None:
        """Cancel a single order by ID (no-op if not found)."""
        self._pending.pop(order_id, None)

    def check_fills(
        self,
        current_tick: dict,
        fill_tracker: FillTracker,
    ) -> list[Fill]:
        """Check all pending orders against *current_tick* for fills.

        Parameters
        ----------
        current_tick:
            CCXT-format order book dict with keys ``bids``, ``asks``,
            ``timestamp``.  The best bid/ask prices are used for matching.
        fill_tracker:
            The FillTracker instance.  Matched fills are injected via
            the public ``process_fill()`` method.

        Returns
        -------
        list[Fill]
            Newly created Fill objects (also already processed by fill_tracker).
        """
        self._tick_index += 1

        bids = current_tick.get("bids", [])
        asks = current_tick.get("asks", [])
        ts_ms = current_tick.get("timestamp", int(time.time() * 1000))

        if not bids or not asks:
            return []

        market_bid = float(bids[0][0])   # highest bid (what buyers will pay)
        market_ask = float(asks[0][0])   # lowest ask (what sellers want)
        mid_price = (market_bid + market_ask) / 2.0
        ts_s = float(ts_ms) / 1000.0

        new_fills: list[Fill] = []
        filled_ids: list[str] = []

        for order_id, order in self._pending.items():
            fill_result = self._check_single_fill(
                order, market_bid, market_ask, mid_price
            )

            if fill_result is not None:
                fill_price, fill_size, is_maker = fill_result

                # Compute fee
                fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
                fee = fill_price * fill_size * fee_rate

                fill = Fill(
                    fill_id=self._next_fill_id(),
                    order_id=order_id,
                    side=order.side,
                    price=fill_price,
                    size=fill_size,
                    timestamp=ts_s,
                    fee=fee,
                    fee_currency="USDT",
                )
                fill_tracker.process_fill(fill)  # Public API
                new_fills.append(fill)
                filled_ids.append(order_id)
                self.total_fees += fee

                if order.side == "buy":
                    self.total_bid_fills += 1
                else:
                    self.total_ask_fills += 1

        # Remove filled orders
        for oid in filled_ids:
            del self._pending[oid]

        return new_fills

    def _check_single_fill(
        self,
        order: PendingOrder,
        market_bid: float,
        market_ask: float,
        mid_price: float,
    ) -> tuple[float, float, bool] | None:
        """Check if a single order fills under the current fill mode.

        Returns
        -------
        tuple[fill_price, fill_size, is_maker] or None
        """
        if self.fill_mode == "optimistic":
            return self._check_optimistic(order, market_bid, market_ask)
        elif self.fill_mode == "probabilistic":
            return self._check_probabilistic(order, market_bid, market_ask, mid_price)
        elif self.fill_mode == "conservative":
            return self._check_conservative(order, market_bid, market_ask)
        else:
            return self._check_optimistic(order, market_bid, market_ask)

    def _check_optimistic(
        self,
        order: PendingOrder,
        market_bid: float,
        market_ask: float,
    ) -> tuple[float, float, bool] | None:
        """Optimistic: fill at limit price on any touch/cross."""
        if order.side == "buy":
            if market_ask <= order.price:
                return (order.price, order.size, True)
        else:
            if market_bid >= order.price:
                return (order.price, order.size, True)
        return None

    def _check_probabilistic(
        self,
        order: PendingOrder,
        market_bid: float,
        market_ask: float,
        mid_price: float,
    ) -> tuple[float, float, bool] | None:
        """Probabilistic: fill probability based on distance from mid.

        Fill probability decreases exponentially with distance from the
        best opposing price. Orders that touch/cross always fill.
        Orders near the touch get a probability between 0.3 and 0.9.
        """
        import random

        if order.side == "buy":
            if market_ask <= order.price:
                # Touched/crossed — always fills (passive)
                return (order.price, order.size, True)
            # Near-touch: probability based on distance
            if mid_price > 0:
                distance = (market_ask - order.price) / mid_price
                # Exponential decay: p = 0.9 * exp(-100 * distance)
                fill_prob = 0.9 * math.exp(-100.0 * distance)
                if fill_prob > 0.05 and random.random() < fill_prob:
                    # Partial fill: 30-100% of order
                    fill_frac = 0.3 + 0.7 * random.random()
                    return (order.price, order.size * fill_frac, True)
        else:
            if market_bid >= order.price:
                return (order.price, order.size, True)
            if mid_price > 0:
                distance = (order.price - market_bid) / mid_price
                fill_prob = 0.9 * math.exp(-100.0 * distance)
                if fill_prob > 0.05 and random.random() < fill_prob:
                    fill_frac = 0.3 + 0.7 * random.random()
                    return (order.price, order.size * fill_frac, True)

        return None

    def _check_conservative(
        self,
        order: PendingOrder,
        market_bid: float,
        market_ask: float,
    ) -> tuple[float, float, bool] | None:
        """Conservative: requires full cross (strict inequality). Adds slippage."""
        if order.side == "buy":
            # Must cross, not just touch
            if market_ask < order.price:
                # Slippage: fill at midpoint between order price and market ask
                fill_price = (order.price + market_ask) / 2.0
                return (fill_price, order.size, True)
        else:
            if market_bid > order.price:
                fill_price = (order.price + market_bid) / 2.0
                return (fill_price, order.size, True)
        return None

    def advance_tick(self) -> None:
        """Manually advance the tick counter (called when no fill check needed)."""
        self._tick_index += 1

    @property
    def pending_count(self) -> int:
        """Number of currently resting orders."""
        return len(self._pending)

    @property
    def pending_orders(self) -> dict[str, PendingOrder]:
        """Read-only view of pending orders."""
        return dict(self._pending)
