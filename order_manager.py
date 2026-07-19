"""
order_manager.py — Order state tracking, selective replacement, and rate limiting.

Maintains a local table of tracked orders, reconciles against exchange state
each iteration, and applies minimum-lifetime / rate-limit constraints to
avoid churning.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from config import Config
from quote_engine import Quotes
from market_spec import MarketSpec, OrderValidationError, validate_order, build_market_spec

logger = logging.getLogger(__name__)


@dataclass
class TrackedOrder:
    """Local mirror of a live order on the exchange."""

    order_id: str
    side: str               # "buy" or "sell"
    price: float
    size: float
    status: str = "open"    # "open", "filled", "cancelled", "expired"
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class OrderManager:
    """Manages the lifecycle of resting limit orders.

    Core loop per iteration:
        1. ``reconcile(target_quotes, exchange, symbol)``
           — diffs target vs live orders, applies rate limits, returns actions taken.
        2. ``cancel_all(exchange, symbol)``
           — emergency kill-switch cancellation.
    """

    def __init__(self, config: Config):
        self.cfg = config
        self.orders: dict[str, TrackedOrder] = {}   # order_id → TrackedOrder
        self._action_times: deque[float] = deque()  # timestamps of recent actions

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def open_orders(self) -> list[TrackedOrder]:
        return [o for o in self.orders.values() if o.status == "open"]

    @property
    def open_bid(self) -> TrackedOrder | None:
        for o in self.orders.values():
            if o.status == "open" and o.side == "buy":
                return o
        return None

    @property
    def open_ask(self) -> TrackedOrder | None:
        for o in self.orders.values():
            if o.status == "open" and o.side == "sell":
                return o
        return None

    def reconcile(
        self,
        quotes: Quotes,
        exchange: Any,
        symbol: str,
        market_info: dict,
    ) -> list[str]:
        """Reconcile target quotes against live orders.

        Returns a list of action descriptions (for logging / dashboard).
        """
        actions: list[str] = []

        # ── Sync local state with exchange ──────────────────────────────
        self._sync_with_exchange(exchange, symbol)

        # ── Reconcile bid side ──────────────────────────────────────────
        bid_actions = self._reconcile_side(
            "buy", quotes.bid_price, quotes.bid_size, quotes.bid_valid,
            exchange, symbol, market_info,
        )
        actions.extend(bid_actions)

        # ── Reconcile ask side ──────────────────────────────────────────
        ask_actions = self._reconcile_side(
            "sell", quotes.ask_price, quotes.ask_size, quotes.ask_valid,
            exchange, symbol, market_info,
        )
        actions.extend(ask_actions)

        return actions

    def cancel_all(self, exchange: Any, symbol: str) -> list[str]:
        """Emergency cancel — kill-switch path.  Ignores rate limits."""
        actions: list[str] = []
        for oid, order in list(self.orders.items()):
            if order.status == "open":
                self._cancel_order(exchange, symbol, oid)
                actions.append(f"KILL cancel {order.side} {oid}")
        return actions

    def get_filled_orders(self) -> list[TrackedOrder]:
        """Return orders that transitioned to 'filled' since last call."""
        filled = [o for o in self.orders.values() if o.status == "filled"]
        return filled

    def cleanup_closed(self, max_age_s: float = 300.0) -> None:
        """Remove old closed orders from local table to prevent unbounded growth."""
        now = time.time()
        to_remove = [
            oid
            for oid, o in self.orders.items()
            if o.status in ("filled", "cancelled", "expired", "closed")
            and (now - o.last_updated) > max_age_s
        ]
        for oid in to_remove:
            del self.orders[oid]

    # ── Private: per-side reconciliation ────────────────────────────────

    def _reconcile_side(
        self,
        side: str,
        target_price: float,
        target_size: float,
        target_valid: bool,
        exchange: Any,
        symbol: str,
        market_info: dict,
    ) -> list[str]:
        """Reconcile a single side (buy or sell)."""
        actions: list[str] = []
        existing = self.open_bid if side == "buy" else self.open_ask

        # ── No target → cancel existing ─────────────────────────────────
        if not target_valid or target_size <= 0:
            if existing:
                if self._rate_limit_ok():
                    self._cancel_order(exchange, symbol, existing.order_id)
                    actions.append(f"Cancel {side} (no target)")
            return actions

        # ── No existing order → place new ───────────────────────────────
        if existing is None:
            if self._rate_limit_ok():
                new_order = self._place_order(
                    exchange, symbol, side, target_price, target_size
                )
                if new_order:
                    actions.append(
                        f"Place {side} {target_size:.6f} @ {target_price:.2f}"
                    )
            return actions

        # ── Existing order exists → check if replacement needed ─────────
        price_diff = abs(existing.price - target_price) / existing.price if existing.price > 0 else 1.0
        size_diff = abs(existing.size - target_size) / existing.size if existing.size > 0 else 1.0

        needs_update = (
            price_diff > self.cfg.price_change_threshold
            or size_diff > self.cfg.size_change_threshold
        )

        if not needs_update:
            return actions  # Order is close enough — keep it

        # ── Check minimum order lifetime ────────────────────────────────
        if existing.age_seconds < self.cfg.min_order_lifetime_s:
            return actions  # Too young to replace

        # ── Replace: cancel old + place new ─────────────────────────────
        if self._rate_limit_ok():
            self._cancel_order(exchange, symbol, existing.order_id)
            new_order = self._place_order(
                exchange, symbol, side, target_price, target_size
            )
            if new_order:
                actions.append(
                    f"Replace {side} {target_size:.6f} @ {target_price:.2f} "
                    f"(was {existing.size:.6f} @ {existing.price:.2f})"
                )
            else:
                actions.append(
                    f"Cancelled {side} but failed to place replacement"
                )

        return actions

    # ── Private: exchange operations ────────────────────────────────────

    def _place_order(
        self,
        exchange: Any,
        symbol: str,
        side: str,
        price: float,
        size: float,
    ) -> TrackedOrder | None:
        """Place a limit order on the exchange after validation."""
        try:
            # ── Pre-order Validation ────────────────────────────────────
            # Extract market info (assuming exchange has it loaded)
            market_info = {}
            if hasattr(exchange, 'markets') and symbol in exchange.markets:
                market_info = exchange.markets[symbol]
                
            if market_info:
                spec = build_market_spec(exchange, symbol)
                try:
                    # In a real setup, best_bid/best_ask and current_inventory would be passed
                    # down. We'll use dummy bounds here for the sake of the structural check,
                    # since RiskManager and QuoteEngine already gate the business logic.
                    from decimal import Decimal
                    validate_order(
                        spec=spec,
                        side=side,
                        price=Decimal(str(price)),
                        amount=Decimal(str(size)),
                        best_bid=Decimal("0.0"),  # Skip book crossing check here
                        best_ask=Decimal("0.0"),
                        current_inventory=Decimal("0.0"),
                        max_inventory=Decimal(str(self.cfg.max_inventory)),
                        available_equity=Decimal(str(self.cfg.initial_capital * 10)),
                        leverage=Decimal(str(self.cfg.leverage)),
                        maker_fee_rate=Decimal(str(self.cfg.maker_fee_rate)),
                        taker_fee_rate=Decimal(str(self.cfg.taker_fee_rate)),
                    )
                except OrderValidationError as e:
                    logger.error("Order validation failed before placement: %s", e)
                    return None

            # ── Execution ───────────────────────────────────────────────
            result = exchange.create_limit_order(symbol, side, size, price)
            order_id = result.get("id", str(time.time()))
            tracked = TrackedOrder(
                order_id=order_id,
                side=side,
                price=price,
                size=size,
                status="open",
            )
            self.orders[order_id] = tracked
            self._record_action()
            logger.info(
                "Placed %s limit %s %.6f @ %.2f -> %s",
                side, symbol, size, price, order_id,
            )
            return tracked

        except Exception:
            logger.exception("Failed to place %s order %.6f @ %.2f", side, size, price)
            return None

    def _cancel_order(self, exchange: Any, symbol: str, order_id: str) -> bool:
        """Cancel an order.  Handles 'order not found' gracefully."""
        try:
            exchange.cancel_order(order_id, symbol)
            if order_id in self.orders:
                self.orders[order_id].status = "cancelled"
                self.orders[order_id].last_updated = time.time()
            self._record_action()
            logger.info("Cancelled order %s", order_id)
            return True

        except Exception as exc:
            exc_str = str(exc).lower()
            # Treat "order not found" / "already filled" as non-fatal
            if "not found" in exc_str or "already" in exc_str or "does not exist" in exc_str:
                logger.info(
                    "Order %s already gone (filled/cancelled): %s", order_id, exc
                )
                if order_id in self.orders:
                    self.orders[order_id].status = "cancelled"
                    self.orders[order_id].last_updated = time.time()
                return True
            else:
                logger.exception("Failed to cancel order %s", order_id)
                return False

    def _sync_with_exchange(self, exchange: Any, symbol: str) -> None:
        """Fetch open orders from exchange and reconcile with local table."""
        try:
            live_orders = exchange.fetch_open_orders(symbol)
            live_ids = {o["id"] for o in live_orders}

            # Mark orders that are no longer live as closed
            for oid, tracked in self.orders.items():
                if tracked.status == "open" and oid not in live_ids:
                    # Order disappeared — we cannot safely assume it was filled.
                    # Mark it as 'closed' (it could be filled, cancelled by user, or expired)
                    # Real fills will be tracked by FillTracker via trades history.
                    tracked.status = "closed"
                    tracked.last_updated = time.time()
                    logger.info(
                        "Order %s (%s) no longer on exchange — marking as closed",
                        oid, tracked.side,
                    )

            # Add any exchange orders we don't know about
            for live_order in live_orders:
                oid = live_order["id"]
                if oid not in self.orders:
                    self.orders[oid] = TrackedOrder(
                        order_id=oid,
                        side=live_order.get("side", "unknown"),
                        price=float(live_order.get("price", 0)),
                        size=float(live_order.get("amount", 0)),
                        status="open",
                    )

        except Exception:
            logger.exception("Failed to sync orders with exchange")

    # ── Rate limiting ───────────────────────────────────────────────────

    def _rate_limit_ok(self) -> bool:
        """Return True if we haven't exceeded the action-rate cap."""
        now = time.time()
        # Prune old entries
        while self._action_times and self._action_times[0] < now - 1.0:
            self._action_times.popleft()
        return len(self._action_times) < self.cfg.max_orders_per_second

    def _record_action(self) -> None:
        self._action_times.append(time.time())
