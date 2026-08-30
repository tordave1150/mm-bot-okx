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
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal

import numpy as np

if TYPE_CHECKING:
    from fill_tracker import FillTracker

from fill_tracker import Fill, FillTracker  # noqa: E402
from fill_classification import (
    FillTrigger,
    passive_classification,
    special_exit_classification,
)


class OrderState(str, Enum):
    CREATED = "CREATED"
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    OPEN_AT_TERMINAL = "OPEN_AT_TERMINAL"


class CancellationReason(str, Enum):
    CANCEL_MAXIMUM_ORDER_AGE = "CANCEL_MAXIMUM_ORDER_AGE"
    CANCEL_REQUOTE_PRICE_CHANGE = "CANCEL_REQUOTE_PRICE_CHANGE"
    CANCEL_REQUOTE_SIZE_CHANGE = "CANCEL_REQUOTE_SIZE_CHANGE"
    CANCEL_INVENTORY_REBALANCE = "CANCEL_INVENTORY_REBALANCE"
    CANCEL_INVENTORY_LIMIT = "CANCEL_INVENTORY_LIMIT"
    CANCEL_VOLATILITY_LIMIT = "CANCEL_VOLATILITY_LIMIT"
    CANCEL_SPREAD_INVALID = "CANCEL_SPREAD_INVALID"
    CANCEL_MARKET_DATA_STALE = "CANCEL_MARKET_DATA_STALE"
    CANCEL_RISK_SOFT_STOP = "CANCEL_RISK_SOFT_STOP"
    CANCEL_RISK_HARD_KILL = "CANCEL_RISK_HARD_KILL"
    CANCEL_MARGIN_PROTECTION = "CANCEL_MARGIN_PROTECTION"
    CANCEL_TERMINAL_CLEANUP = "CANCEL_TERMINAL_CLEANUP"
    CANCEL_EMERGENCY_CLEANUP = "CANCEL_EMERGENCY_CLEANUP"
    CANCEL_SHUTDOWN = "CANCEL_SHUTDOWN"
    CANCEL_PROTOCOL_ABORT = "CANCEL_PROTOCOL_ABORT"
    CANCEL_STRATEGY_REPLACE = "CANCEL_STRATEGY_REPLACE"
    CANCEL_UNKNOWN_ERROR = "CANCEL_UNKNOWN_ERROR"


TERMINAL_ORDER_STATES = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
    OrderState.OPEN_AT_TERMINAL,
})


# ── PendingOrder ─────────────────────────────────────────────────────────────

@dataclass
class PendingOrder:
    """A resting limit order waiting to be matched."""

    order_id: str
    side: str         # "buy" or "sell"
    price: float      # limit price
    size: float       # remaining canonical base-asset quantity
    placed_tick: int  # tick index when placed
    reduce_only: bool = False
    cancel_effective_tick: int | None = None
    original_size: float = 0.0
    filled_quantity: float = 0.0
    state: OrderState = OrderState.CREATED
    ever_partially_filled: bool = False
    cancellation_context: dict[str, Any] | None = None
    terminal_event_count: int = 0


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
        fill_seed: int | None = None,
        quantity_step: float | None = None,
        cancel_latency_ticks: int = 0,
        event_observer: Callable[[dict[str, Any]], None] | None = None,
        canonical_fill_classification: bool = False,
        passive_fill_trigger: str = FillTrigger.STRICT_TRADE_THROUGH,
        identity_prefix: str = "",
    ) -> None:
        if fill_mode not in {"optimistic", "probabilistic", "conservative"}:
            raise ValueError(f"unknown fill mode: {fill_mode!r}")
        self._pending: dict[str, PendingOrder] = {}   # order_id → PendingOrder
        self._tick_index: int = 0
        self._order_ledger: dict[str, PendingOrder] = {}
        self._lifecycle_events: list[dict[str, Any]] = []
        self._duplicate_terminal_transitions: int = 0
        self._fill_id_counter = itertools.count(1)    # Per-instance, not global
        self.total_bid_fills: int = 0
        self.total_ask_fills: int = 0
        self.total_fees: float = 0.0
        self.total_taker_fills: int = 0
        self.flatten_fees: float = 0.0
        self.flatten_slippage: float = 0.0

        self.fill_mode = fill_mode
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.quantity_step = quantity_step
        self.cancel_latency_ticks = max(0, int(cancel_latency_ticks))
        self.event_observer = event_observer
        self.canonical_fill_classification = bool(
            canonical_fill_classification
        )
        self.passive_fill_trigger = passive_fill_trigger
        self.identity_prefix = identity_prefix.strip()
        if self.canonical_fill_classification:
            passive_classification(self.passive_fill_trigger)
        self._order_id_counter = itertools.count(1)
        # Explicit seeded RNG — makes probabilistic fills deterministic (AGENTS.md §5.1)
        self._rng: np.random.Generator = np.random.default_rng(fill_seed)

    def _next_fill_id(self) -> str:
        local = f"bt-fill-{next(self._fill_id_counter):08d}"
        return f"{self.identity_prefix}-{local}" if self.identity_prefix else local

    # ── Public API ──────────────────────────────────────────────────────────

    def place_order(
        self, side: str, price: float, size: float, reduce_only: bool = False
    ) -> str:
        """Register a new resting limit order.

        Returns
        -------
        str
            A unique order ID for this backtest run.
        """
        if size <= 0 or price <= 0:
            return ""

        local_id = f"bt-order-{next(self._order_id_counter):08d}-{side}"
        order_id = (
            f"{self.identity_prefix}-{local_id}"
            if self.identity_prefix else local_id
        )
        order = PendingOrder(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            placed_tick=self._tick_index,
            reduce_only=reduce_only,
            original_size=size,
            state=OrderState.ACTIVE,
        )
        self._pending[order_id] = order
        self._order_ledger[order_id] = order
        self._emit({
            "event": "passive_order_created",
            "order_id": order_id,
            "side": side,
            "price": price,
            "size": size,
            "placed_tick": self._tick_index,
        })
        return order_id

    @staticmethod
    def _cancellation_reason(
        reason: CancellationReason | str,
    ) -> CancellationReason:
        try:
            return reason if isinstance(reason, CancellationReason) else CancellationReason(reason)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown cancellation reason: {reason!r}") from exc

    @classmethod
    def _cancel_context(
        cls,
        *,
        reason: CancellationReason | str,
        source_component: str,
        source_event: str,
        timestamp_ms: int,
        strategy_version: str,
        profile_fingerprint: str,
        protocol_id: str,
    ) -> dict[str, Any]:
        if (
            not source_component
            or not source_event
            or not strategy_version
            or not profile_fingerprint
            or not protocol_id
        ):
            raise ValueError(
                "source_component, source_event, strategy_version, "
                "profile_fingerprint, and protocol_id are required"
            )
        if not isinstance(timestamp_ms, int):
            raise TypeError("timestamp_ms must be an integer")
        return {
            "reason": cls._cancellation_reason(reason),
            "source_component": source_component,
            "source_event": source_event,
            "timestamp_ms": timestamp_ms,
            "strategy_version": strategy_version,
            "profile_fingerprint": profile_fingerprint,
            "protocol_id": protocol_id,
        }

    def cancel_all(
        self,
        *,
        reason: CancellationReason | str,
        source_component: str,
        source_event: str,
        timestamp_ms: int,
        strategy_version: str,
        profile_fingerprint: str,
        protocol_id: str,
    ) -> tuple[str, ...]:
        """Terminally cancel every resting order with one explicit reason."""
        context = self._cancel_context(
            reason=reason,
            source_component=source_component,
            source_event=source_event,
            timestamp_ms=timestamp_ms,
            strategy_version=strategy_version,
            profile_fingerprint=profile_fingerprint,
            protocol_id=protocol_id,
        )
        affected = tuple(self._pending)
        for order_id in affected:
            order = self._pending.pop(order_id)
            self._terminal_transition(
                order,
                OrderState.CANCELLED,
                timestamp_ms=timestamp_ms,
                cancellation_context=context,
            )
        self._emit({
            "event": "bulk_cancel",
            **self._serialized_cancel_context(context),
            "tick": self._tick_index,
            "affected_order_ids": list(affected),
            "affected_order_count": len(affected),
        })
        return affected

    def cancel_order(
        self,
        order_id: str,
        *,
        reason: CancellationReason | str,
        source_component: str,
        source_event: str,
        timestamp_ms: int,
        strategy_version: str,
        profile_fingerprint: str,
        protocol_id: str,
    ) -> bool:
        """Cancel one order with auditable context; return whether it was live."""
        context = self._cancel_context(
            reason=reason,
            source_component=source_component,
            source_event=source_event,
            timestamp_ms=timestamp_ms,
            strategy_version=strategy_version,
            profile_fingerprint=profile_fingerprint,
            protocol_id=protocol_id,
        )
        order = self._pending.get(order_id)
        if order is None:
            known = self._order_ledger.get(order_id)
            self._emit({
                "event": "cancel_attempt_ignored",
                "order_id": order_id,
                "terminal_state": known.state.value if known else None,
                **self._serialized_cancel_context(context),
                "tick": self._tick_index,
            })
            return False
        if self.cancel_latency_ticks == 0:
            self._pending.pop(order_id, None)
            self._terminal_transition(
                order,
                OrderState.CANCELLED,
                timestamp_ms=timestamp_ms,
                cancellation_context=context,
            )
        else:
            order.state = OrderState.CANCEL_REQUESTED
            order.cancel_effective_tick = self._tick_index + self.cancel_latency_ticks
            order.cancellation_context = context
            self._emit({
                "event": "cancel_requested",
                "order_id": order_id,
                "tick": self._tick_index,
                "effective_tick": order.cancel_effective_tick,
                **self._serialized_cancel_context(context),
            })
        return True

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

        for order_id, order in list(self._pending.items()):
            if (
                order.cancel_effective_tick is not None
                and order.cancel_effective_tick <= self._tick_index
            ):
                del self._pending[order_id]
                context = order.cancellation_context
                if context is None:
                    raise RuntimeError("cancel-requested order has no cancellation context")
                self._terminal_transition(
                    order,
                    OrderState.CANCELLED,
                    timestamp_ms=int(current_tick.get("timestamp", 0)),
                    cancellation_context=context,
                )

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
            if order.reduce_only:
                if (
                    fill_tracker.position == 0
                    or (fill_tracker.position > 0 and order.side != "sell")
                    or (fill_tracker.position < 0 and order.side != "buy")
                ):
                    filled_ids.append(order_id)
                    self._terminal_transition(
                        order,
                        OrderState.REJECTED,
                        timestamp_ms=int(ts_ms),
                        rejection_reason="REDUCE_ONLY_NO_POSITION",
                    )
                    continue
            if self.fill_mode == "conservative":
                strict = (
                    market_ask < order.price
                    if order.side == "buy"
                    else market_bid > order.price
                )
                touch = (
                    market_ask == order.price
                    if order.side == "buy"
                    else market_bid == order.price
                )
                self._emit({
                    "event": (
                        "strict_trade_through" if strict
                        else "touch_without_trade_through" if touch
                        else "no_touch"
                    ),
                    "order_id": order_id,
                    "side": order.side,
                    "tick": self._tick_index,
                })
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
                    liquidity="maker" if is_maker else "taker",
                    classification=(
                        passive_classification(self.passive_fill_trigger)
                        if self.canonical_fill_classification else None
                    ),
                )
                if order.reduce_only:
                    fill.size = min(fill.size, abs(fill_tracker.position))
                    if fill.size <= 1e-12:
                        filled_ids.append(order_id)
                        continue
                    fill.fee = fill.price * fill.size * fee_rate
                    fill_size = fill.size
                    fee = fill.fee
                fill_tracker.process_fill(fill)  # Public API
                new_fills.append(fill)
                residual = order.size - fill_size
                order.filled_quantity += fill_size
                self._emit({
                    "event": "full_fill" if residual <= 1e-12 else "partial_fill",
                    "order_id": order_id,
                    "side": order.side,
                    "tick": self._tick_index,
                    "fill_mode": self.fill_mode,
                    "fill_size": fill_size,
                })
                if residual <= 1e-12:
                    filled_ids.append(order_id)
                    order.size = 0.0
                    self._terminal_transition(
                        order,
                        OrderState.FILLED,
                        timestamp_ms=int(ts_ms),
                    )
                else:
                    order.size = residual
                    order.state = OrderState.PARTIALLY_FILLED
                    order.ever_partially_filled = True
                self.total_fees += fee

                if order.side == "buy":
                    self.total_bid_fills += 1
                else:
                    self.total_ask_fills += 1

        # Remove filled orders
        for oid in filled_ids:
            del self._pending[oid]

        return new_fills

    def _emit(self, event: dict[str, Any]) -> None:
        self._lifecycle_events.append(dict(event))
        if self.event_observer is not None:
            self.event_observer(dict(event))

    @staticmethod
    def _serialized_cancel_context(context: dict[str, Any]) -> dict[str, Any]:
        return {**context, "reason": context["reason"].value}

    def _terminal_transition(
        self,
        order: PendingOrder,
        state: OrderState,
        *,
        timestamp_ms: int,
        cancellation_context: dict[str, Any] | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        if state not in TERMINAL_ORDER_STATES:
            raise ValueError(f"not a terminal order state: {state!r}")
        if order.state in TERMINAL_ORDER_STATES or order.terminal_event_count:
            self._duplicate_terminal_transitions += 1
            raise RuntimeError(f"duplicate terminal transition for {order.order_id}")
        order.state = state
        order.terminal_event_count += 1
        event = {
            "event": "order_terminal",
            "order_id": order.order_id,
            "terminal_state": state.value,
            "tick": self._tick_index,
            "timestamp_ms": timestamp_ms,
            "original_quantity": order.original_size,
            "filled_quantity": order.filled_quantity,
            "cancelled_quantity": order.size if state == OrderState.CANCELLED else 0.0,
            "rejected_quantity": order.size if state == OrderState.REJECTED else 0.0,
            "expired_quantity": order.size if state == OrderState.EXPIRED else 0.0,
            "open_quantity": order.size if state == OrderState.OPEN_AT_TERMINAL else 0.0,
        }
        if cancellation_context is not None:
            order.cancellation_context = cancellation_context
            event.update(self._serialized_cancel_context(cancellation_context))
        if rejection_reason is not None:
            event["rejection_reason"] = rejection_reason
        self._emit(event)
        if state == OrderState.CANCELLED:
            self._emit({
                "event": "cancel_effective",
                "order_id": order.order_id,
                "tick": self._tick_index,
                "timestamp_ms": timestamp_ms,
                **self._serialized_cancel_context(cancellation_context),
            })

    def finalize_open_orders(
        self,
        *,
        timestamp_ms: int,
        source_component: str,
        source_event: str,
        strategy_version: str,
        profile_fingerprint: str,
        protocol_id: str,
    ) -> tuple[str, ...]:
        """Classify every live order as explicitly open at terminal."""
        if (
            not source_component
            or not source_event
            or not strategy_version
            or not profile_fingerprint
            or not protocol_id
        ):
            raise ValueError(
                "source_component, source_event, strategy_version, "
                "profile_fingerprint, and protocol_id are required"
            )
        affected = tuple(self._pending)
        for order_id in affected:
            order = self._pending.pop(order_id)
            self._terminal_transition(
                order,
                OrderState.OPEN_AT_TERMINAL,
                timestamp_ms=timestamp_ms,
            )
        self._emit({
            "event": "terminal_open_classification",
            "source_component": source_component,
            "source_event": source_event,
            "strategy_version": strategy_version,
            "profile_fingerprint": profile_fingerprint,
            "protocol_id": protocol_id,
            "timestamp_ms": timestamp_ms,
            "affected_order_ids": list(affected),
        })
        return affected

    def record_rejected_before_activation(
        self,
        *,
        side: str,
        price: float,
        size: float,
        timestamp_ms: int,
        rejection_reason: str,
    ) -> str:
        """Record an attempted passive order rejected before activation."""
        if side not in {"buy", "sell"} or size <= 0:
            raise ValueError("rejected order still requires valid side and size")
        order_id = f"bt-order-{next(self._order_id_counter):08d}-{side}"
        order = PendingOrder(
            order_id=order_id,
            side=side,
            price=price,
            size=size,
            placed_tick=self._tick_index,
            original_size=size,
            state=OrderState.CREATED,
        )
        self._order_ledger[order_id] = order
        self._emit({
            "event": "passive_order_created",
            "order_id": order_id,
            "side": side,
            "price": price,
            "size": size,
            "placed_tick": self._tick_index,
            "activation": "REJECTED",
        })
        self._terminal_transition(
            order,
            OrderState.REJECTED,
            timestamp_ms=timestamp_ms,
            rejection_reason=rejection_reason,
        )
        return order_id

    def expire_order(
        self,
        order_id: str,
        *,
        timestamp_ms: int,
        source_component: str,
        source_event: str,
    ) -> bool:
        """Terminally expire one live order."""
        if not source_component or not source_event:
            raise ValueError("source_component and source_event are required")
        order = self._pending.pop(order_id, None)
        if order is None:
            return False
        self._terminal_transition(
            order,
            OrderState.EXPIRED,
            timestamp_ms=timestamp_ms,
        )
        self._emit({
            "event": "order_expired",
            "order_id": order_id,
            "timestamp_ms": timestamp_ms,
            "source_component": source_component,
            "source_event": source_event,
        })
        return True

    def reconciliation(self) -> dict[str, Any]:
        """Return exact order-count and base-quantity reconciliation."""
        orders = tuple(self._order_ledger.values())
        by_state = {
            state: sum(order.state == state for order in orders)
            for state in TERMINAL_ORDER_STATES
        }
        created_quantity = sum(order.original_size for order in orders)
        filled_quantity = sum(order.filled_quantity for order in orders)
        cancelled_quantity = sum(
            order.size for order in orders if order.state == OrderState.CANCELLED
        )
        rejected_quantity = sum(
            order.size for order in orders if order.state == OrderState.REJECTED
        )
        expired_quantity = sum(
            order.size for order in orders if order.state == OrderState.EXPIRED
        )
        open_quantity = sum(
            order.size for order in orders if order.state == OrderState.OPEN_AT_TERMINAL
        )
        zero_terminal = sum(order.terminal_event_count == 0 for order in orders)
        multiple_terminal = sum(order.terminal_event_count > 1 for order in orders)
        unknown_cancel = sum(
            order.state == OrderState.CANCELLED
            and order.cancellation_context is not None
            and order.cancellation_context["reason"]
            == CancellationReason.CANCEL_UNKNOWN_ERROR
            for order in orders
        )
        terminal_count = sum(by_state.values())
        terminal_quantity = (
            filled_quantity + cancelled_quantity + rejected_quantity
            + expired_quantity + open_quantity
        )
        count_ok = len(orders) == terminal_count
        quantity_ok = math.isclose(
            created_quantity, terminal_quantity, rel_tol=0.0, abs_tol=1e-12
        )
        return {
            "passive_orders_created": len(orders),
            "fully_filled_orders": by_state[OrderState.FILLED],
            "partially_filled_orders": sum(
                order.ever_partially_filled for order in orders
            ),
            "fully_cancelled_orders": by_state[OrderState.CANCELLED],
            "rejected_before_activation": by_state[OrderState.REJECTED],
            "expired_orders": by_state[OrderState.EXPIRED],
            "open_orders_at_terminal": by_state[OrderState.OPEN_AT_TERMINAL],
            "unclassified_order_removals": zero_terminal + unknown_cancel,
            "duplicate_terminal_transitions": self._duplicate_terminal_transitions,
            "multiple_terminal_reason_orders": multiple_terminal,
            "zero_terminal_reason_orders": zero_terminal,
            "created_quantity": created_quantity,
            "filled_quantity": filled_quantity,
            "cancelled_quantity": cancelled_quantity,
            "rejected_quantity": rejected_quantity,
            "expired_quantity": expired_quantity,
            "open_quantity_at_terminal": open_quantity,
            "order_count_reconciliation": count_ok,
            "order_quantity_reconciliation": quantity_ok,
            "funnel_accounting_reconciliation": (
                count_ok
                and quantity_ok
                and zero_terminal == 0
                and multiple_terminal == 0
                and self._duplicate_terminal_transitions == 0
                and unknown_cancel == 0
            ),
        }

    @property
    def lifecycle_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(event) for event in self._lifecycle_events)

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
            raise ValueError(f"unknown fill mode: {self.fill_mode!r}")

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

        Uses self._rng (seeded numpy Generator) for full determinism.
        """
        if order.side == "buy":
            if market_ask <= order.price:
                # Touched/crossed — always fills (passive)
                return (order.price, order.size, True)
            # Near-touch: probability based on distance
            if mid_price > 0:
                distance = (market_ask - order.price) / mid_price
                # Exponential decay: p = 0.9 * exp(-100 * distance)
                fill_prob = 0.9 * math.exp(-100.0 * distance)
                if fill_prob > 0.05 and self._rng.random() < fill_prob:
                    # Partial fill: 30-100% of order
                    fill_frac = 0.3 + 0.7 * self._rng.random()
                    fill_size = self._quantize_fill_size(order.size, fill_frac)
                    return (order.price, fill_size, True) if fill_size > 0 else None
        else:
            if market_bid >= order.price:
                return (order.price, order.size, True)
            if mid_price > 0:
                distance = (order.price - market_bid) / mid_price
                fill_prob = 0.9 * math.exp(-100.0 * distance)
                if fill_prob > 0.05 and self._rng.random() < fill_prob:
                    fill_frac = 0.3 + 0.7 * self._rng.random()
                    fill_size = self._quantize_fill_size(order.size, fill_frac)
                    return (order.price, fill_size, True) if fill_size > 0 else None

        return None

    def _quantize_fill_size(self, order_size: float, fill_fraction: float) -> float:
        """Respect exchange quantity granularity in promotion simulations."""
        raw = order_size * fill_fraction
        if self.quantity_step is None or self.quantity_step <= 0:
            return raw
        steps = math.floor((raw + 1e-12) / self.quantity_step)
        if steps <= 0:
            # A one-step order cannot be partially filled below one step.
            return order_size if order_size <= self.quantity_step + 1e-12 else 0.0
        return min(order_size, steps * self.quantity_step)

    def _check_conservative(
        self,
        order: PendingOrder,
        market_bid: float,
        market_ask: float,
    ) -> tuple[float, float, bool] | None:
        """Conservative: requires strict trade-through and fills at the limit.

        A passive order may receive price improvement in reality, but granting
        it here would make the supposedly conservative model optimistic.
        """
        if order.side == "buy":
            # Must cross, not just touch
            if market_ask < order.price:
                return (order.price, order.size, True)
        else:
            if market_bid > order.price:
                return (order.price, order.size, True)
        return None

    def execute_market_flatten(
        self,
        current_tick: dict,
        fill_tracker: FillTracker,
        *,
        slippage_bps: float,
        reason: str,
    ) -> Fill | None:
        """Flatten the current base position with a modeled reduce-only IOC."""
        position = fill_tracker.position
        if abs(position) <= 1e-12:
            return None
        bids = current_tick.get("bids", [])
        asks = current_tick.get("asks", [])
        if not bids or not asks:
            raise ValueError("Cannot flatten without a two-sided book")

        slip = max(slippage_bps, 0.0) / 10_000.0
        if position > 0:
            side = "sell"
            reference = float(bids[0][0])
            fill_price = reference * (1.0 - slip)
        else:
            side = "buy"
            reference = float(asks[0][0])
            fill_price = reference * (1.0 + slip)

        size = abs(position)
        fee = fill_price * size * self.taker_fee_rate
        ts_s = float(current_tick.get("timestamp", 0)) / 1000.0
        flatten_order_id = f"bt-{reason}-flatten"
        if self.identity_prefix:
            flatten_order_id = f"{self.identity_prefix}-{flatten_order_id}"
        fill = Fill(
            fill_id=self._next_fill_id(),
            order_id=flatten_order_id,
            side=side,
            price=fill_price,
            size=size,
            timestamp=ts_s,
            fee=fee,
            fee_currency="USDT",
            liquidity="taker",
            reason=reason,
            classification=(
                special_exit_classification(reason)
                if self.canonical_fill_classification else None
            ),
        )
        fill_tracker.process_fill(fill)
        self.total_fees += fee
        self.total_taker_fills += 1
        self.flatten_fees += fee
        self.flatten_slippage += abs(fill_price - reference) * size
        return fill

    def advance_tick(self) -> None:
        """Manually advance the tick counter (called when no fill check needed)."""
        self._tick_index += 1

    @property
    def pending_count(self) -> int:
        """Number of currently resting orders."""
        return len(self._pending)

    @property
    def pending_orders(self) -> tuple[PendingOrder, ...]:
        """Immutable snapshot of currently resting order objects."""
        return tuple(self._pending.values())
