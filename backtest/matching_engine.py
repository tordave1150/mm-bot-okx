"""
backtest/matching_engine.py — Limit-order fill simulation for backtesting.

v1 (Optimistic Fill):
    If the next tick's price crosses or touches our resting bid/ask,
    we fill the full order at that price.  No queue modelling, no slippage.

v2 (Future):
    Add fill probability weighted by synthetic depth/imbalance + slippage.

Usage::

    engine = MatchingEngine()
    engine.place_order("buy", bid_price, bid_size)
    engine.place_order("sell", ask_price, ask_size)

    fills = engine.check_fills(current_tick, fill_tracker)
    # fills is a list[Fill] — each fill has already been sent to fill_tracker
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fill_tracker import FillTracker

# ── Import Fill from the production fill_tracker ────────────────────────────
import sys
import os

# Ensure the project root is importable when running from inside backtest/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fill_tracker import Fill, FillTracker  # noqa: E402 — after sys.path patch


# ── Counter for deterministic fill IDs ─────────────────────────────────────
_fill_id_counter = itertools.count(1)


def _next_fill_id() -> str:
    return f"bt-fill-{next(_fill_id_counter):08d}"


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

    v1 Logic
    --------
    - A **buy** order fills when ``next_tick.best_ask <= order.price``
      (the market has come down to hit our bid).
    - A **sell** order fills when ``next_tick.best_bid >= order.price``
      (the market has come up to hit our ask).

    Both sides fill at ``order.price`` (not the crossing price), which is
    the optimistic / market-maker-favourable assumption for v1.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingOrder] = {}   # order_id → PendingOrder
        self._tick_index: int = 0
        self.total_bid_fills: int = 0
        self.total_ask_fills: int = 0

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
            The production FillTracker instance.  Matched fills are injected
            via ``_process_fill()`` directly (bypassing exchange detection).

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
        ts_s = float(ts_ms) / 1000.0

        new_fills: list[Fill] = []
        filled_ids: list[str] = []

        for order_id, order in self._pending.items():
            fill_price: float | None = None

            if order.side == "buy":
                # Our bid is filled if the market ask comes down to our level
                if market_ask <= order.price:
                    fill_price = order.price  # v1: fill at our limit price
            else:  # "sell"
                # Our ask is filled if the market bid comes up to our level
                if market_bid >= order.price:
                    fill_price = order.price  # v1: fill at our limit price

            if fill_price is not None:
                fill = Fill(
                    fill_id=_next_fill_id(),
                    order_id=order_id,
                    side=order.side,
                    price=fill_price,
                    size=order.size,
                    timestamp=ts_s,
                    fee=0.0,           # no fee modelling in v1
                    fee_currency="USDT",
                )
                fill_tracker._process_fill(fill)
                new_fills.append(fill)
                filled_ids.append(order_id)

                if order.side == "buy":
                    self.total_bid_fills += 1
                else:
                    self.total_ask_fills += 1

        # Remove filled orders
        for oid in filled_ids:
            del self._pending[oid]

        return new_fills

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
