"""Causal explicit-trade matcher for balanced MM infrastructure fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from market_maker.execution_accounting import CanonicalFill


D = Decimal
ZERO = D("0")
EVENT_ORDER = (
    "MARKET_UPDATE",
    "TRADE_EVENT",
    "FILL_EVALUATION",
    "CANCEL_REQUEST",
    "CANCEL_COMPLETION",
    "RISK_EVALUATION",
    "HARD_KILL",
    "QUOTE_DECISION",
    "ORDER_ACTIVATION",
    "TERMINAL_CLEANUP",
)


@dataclass
class DiagnosticOrder:
    order_id: str
    side: str
    price: Decimal
    remaining_quantity_btc: Decimal
    original_quantity_btc: Decimal
    decision_mid: Decimal
    quote_created_tick: int
    activation_tick: int
    profile_id: str
    scenario_id: str
    cancel_requested_tick: int | None = None


class CausalTradeEventMatcher:
    """Fill prior-tick passive quotes only from explicit current trade events."""

    def __init__(
        self,
        *,
        scenario_id: str,
        profile_id: str = "mm-v1-2-fixed-diagnostic",
        maker_fee_rate: Decimal = D("0.0002"),
        taker_fee_rate: Decimal = D("0.0005"),
    ) -> None:
        self.scenario_id = scenario_id
        self.profile_id = profile_id
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.tick = 0
        self.inventory = ZERO
        self.previous_mid = ZERO
        self.orders: dict[str, DiagnosticOrder] = {}
        self.fills: list[CanonicalFill] = []
        self.market_events: list[dict[str, Any]] = []
        self.trade_events: list[dict[str, Any]] = []
        self.quote_events: list[dict[str, Any]] = []
        self.order_events: list[dict[str, Any]] = []
        self._order_counter = 0
        self._fill_counter = 0

    def activate_quote(
        self,
        *,
        side: str,
        price: Decimal,
        quantity_btc: Decimal,
        decision_mid: Decimal,
        quote_created_tick: int,
        activation_tick: int,
    ) -> str:
        if side not in {"buy", "sell"} or price <= 0 or quantity_btc <= 0:
            raise ValueError("invalid diagnostic quote")
        if activation_tick < quote_created_tick:
            raise ValueError("activation cannot precede quote creation")
        self._order_counter += 1
        order_id = f"diag-order-{self._order_counter:06d}"
        self.orders[order_id] = DiagnosticOrder(
            order_id=order_id,
            side=side,
            price=price,
            remaining_quantity_btc=quantity_btc,
            original_quantity_btc=quantity_btc,
            decision_mid=decision_mid,
            quote_created_tick=quote_created_tick,
            activation_tick=activation_tick,
            profile_id=self.profile_id,
            scenario_id=self.scenario_id,
        )
        self.quote_events.append({
            "event": "QUOTE_DECISION",
            "tick": quote_created_tick,
            "order_id": order_id,
            "side": side,
            "price": str(price),
            "quantity_btc": str(quantity_btc),
            "decision_mid": str(decision_mid),
        })
        self.order_events.append({
            "event": "ORDER_ACTIVATION",
            "tick": activation_tick,
            "order_id": order_id,
        })
        return order_id

    def process_tick(
        self,
        *,
        tick: int,
        bid: Decimal,
        ask: Decimal,
        trades: list[dict[str, Any]] | None = None,
        cancel_order_ids: list[str] | None = None,
        stale: bool = False,
        hard_kill: bool = False,
        terminal: bool = False,
    ) -> list[CanonicalFill]:
        if tick <= self.tick or bid <= 0 or ask <= bid:
            raise ValueError("ticks must increase and book must be valid")
        self.tick = tick
        mid = (bid + ask) / D("2")
        mid_before = self.previous_mid if self.previous_mid > 0 else mid
        self.market_events.append({
            "event": "MARKET_UPDATE",
            "tick": tick,
            "bid": str(bid),
            "ask": str(ask),
            "mid_before": str(mid_before),
            "mid_after": str(mid),
            "stale": stale,
        })
        created: list[CanonicalFill] = []
        for trade in trades or []:
            event_id = str(trade["event_id"])
            aggressor_side = str(trade["aggressor_side"])
            trade_price = D(str(trade["price"]))
            remaining_trade = D(str(trade["quantity_btc"]))
            if aggressor_side not in {"buy", "sell"} or remaining_trade <= 0:
                raise ValueError("invalid explicit trade event")
            self.trade_events.append({
                "event": "TRADE_EVENT",
                "tick": tick,
                "event_id": event_id,
                "aggressor_side": aggressor_side,
                "price": str(trade_price),
                "quantity_btc": str(remaining_trade),
            })
            for order in list(self.orders.values()):
                if remaining_trade <= 0:
                    break
                if order.activation_tick > tick:
                    continue
                eligible = (
                    aggressor_side == "sell"
                    and order.side == "buy"
                    and trade_price <= order.price
                ) or (
                    aggressor_side == "buy"
                    and order.side == "sell"
                    and trade_price >= order.price
                )
                if not eligible:
                    continue
                quantity = min(order.remaining_quantity_btc, remaining_trade)
                fill = self._make_fill(
                    order=order,
                    quantity=quantity,
                    tick=tick,
                    bid=bid,
                    ask=ask,
                    mid_before=mid_before,
                    mid_at=mid,
                    trigger_type="AGGRESSOR_TRADE_AT_QUOTE",
                    triggering_event_id=event_id,
                    triggering_trade_price=trade_price,
                    triggering_trade_quantity=D(str(trade["quantity_btc"])),
                    fee_role="MAKER",
                )
                created.append(fill)
                remaining_trade -= quantity
                order.remaining_quantity_btc -= quantity
                self.order_events.append({
                    "event": (
                        "FULL_FILL"
                        if order.remaining_quantity_btc == ZERO
                        else "PARTIAL_FILL"
                    ),
                    "tick": tick,
                    "order_id": order.order_id,
                    "fill_id": fill.fill_id,
                    "quantity_btc": str(quantity),
                    "trigger_type": fill.trigger_type,
                })
                if order.remaining_quantity_btc == ZERO:
                    del self.orders[order.order_id]

        requested = list(cancel_order_ids or [])
        if stale:
            requested.extend(self.orders)
        for order_id in dict.fromkeys(requested):
            order = self.orders.get(order_id)
            if order is None:
                continue
            order.cancel_requested_tick = tick
            reason = "STALE_DATA" if stale else "EXPLICIT_CANCEL"
            self.order_events.append({
                "event": "CANCEL_REQUEST",
                "tick": tick,
                "order_id": order_id,
                "reason": reason,
            })
            del self.orders[order_id]
            self.order_events.append({
                "event": "CANCEL_COMPLETION",
                "tick": tick,
                "order_id": order_id,
                "reason": reason,
                "released_quantity_btc": str(order.remaining_quantity_btc),
            })

        self.order_events.append({
            "event": "RISK_EVALUATION",
            "tick": tick,
            "inventory_btc": str(self.inventory),
        })
        if hard_kill:
            self.order_events.append({"event": "HARD_KILL", "tick": tick})
            created.extend(self._flatten(
                tick=tick,
                bid=bid,
                ask=ask,
                mid_before=mid_before,
                mid_at=mid,
                trigger_type="HARD_KILL_EXECUTION",
                event_id=f"hard-kill-{tick}",
            ))
        if terminal:
            self.order_events.append({
                "event": "TERMINAL_CLEANUP", "tick": tick
            })
            self.orders.clear()
            created.extend(self._flatten(
                tick=tick,
                bid=bid,
                ask=ask,
                mid_before=mid_before,
                mid_at=mid,
                trigger_type="TERMINAL_EXECUTION",
                event_id=f"terminal-{tick}",
            ))
        self.previous_mid = mid
        return created

    def _make_fill(
        self,
        *,
        order: DiagnosticOrder,
        quantity: Decimal,
        tick: int,
        bid: Decimal,
        ask: Decimal,
        mid_before: Decimal,
        mid_at: Decimal,
        trigger_type: str,
        triggering_event_id: str,
        triggering_trade_price: Decimal | None,
        triggering_trade_quantity: Decimal | None,
        fee_role: str,
    ) -> CanonicalFill:
        self._fill_counter += 1
        rate = self.maker_fee_rate if fee_role == "MAKER" else self.taker_fee_rate
        fee_base = order.price * quantity
        before = self.inventory
        after = before + (quantity if order.side == "buy" else -quantity)
        fill = CanonicalFill(
            fill_id=f"diag-fill-{self._fill_counter:06d}",
            order_id=order.order_id,
            side=order.side,
            quantity_btc=quantity,
            fill_price=order.price,
            fee=fee_base * rate,
            fee_role=fee_role,
            maker_or_taker="maker" if fee_role == "MAKER" else "taker",
            fee_rate=rate,
            fee_base_usdt=fee_base,
            fee_currency="USDT",
            quote_created_tick=order.quote_created_tick,
            activation_tick=order.activation_tick,
            fill_tick=tick,
            cancel_requested_tick=order.cancel_requested_tick,
            decision_mid=order.decision_mid,
            mid_before_fill=mid_before,
            mid_at_fill=mid_at,
            bid_at_fill=bid,
            ask_at_fill=ask,
            quote_distance_bps=(
                abs(order.price - order.decision_mid)
                / order.decision_mid * D("10000")
            ),
            inventory_before=before,
            inventory_after=after,
            scenario_id=self.scenario_id,
            profile_id=self.profile_id,
            trigger_type=trigger_type,
            triggering_event_id=triggering_event_id,
            triggering_trade_price=triggering_trade_price,
            triggering_trade_quantity=triggering_trade_quantity,
        )
        fill.validate()
        self.inventory = after
        self.fills.append(fill)
        return fill

    def _flatten(
        self,
        *,
        tick: int,
        bid: Decimal,
        ask: Decimal,
        mid_before: Decimal,
        mid_at: Decimal,
        trigger_type: str,
        event_id: str,
    ) -> list[CanonicalFill]:
        if self.inventory == ZERO:
            return []
        side = "sell" if self.inventory > 0 else "buy"
        price = bid if side == "sell" else ask
        order = DiagnosticOrder(
            order_id=f"{event_id}-order",
            side=side,
            price=price,
            remaining_quantity_btc=abs(self.inventory),
            original_quantity_btc=abs(self.inventory),
            decision_mid=mid_at,
            quote_created_tick=tick,
            activation_tick=tick,
            profile_id=self.profile_id,
            scenario_id=self.scenario_id,
        )
        return [self._make_fill(
            order=order,
            quantity=abs(self.inventory),
            tick=tick,
            bid=bid,
            ask=ask,
            mid_before=mid_before,
            mid_at=mid_at,
            trigger_type=trigger_type,
            triggering_event_id=event_id,
            triggering_trade_price=None,
            triggering_trade_quantity=None,
            fee_role="TAKER",
        )]
