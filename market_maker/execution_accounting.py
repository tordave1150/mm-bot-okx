"""Canonical Decimal FIFO execution accounting for MM diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal


D = Decimal
ZERO = D("0")

FILL_TRIGGERS = frozenset({
    "STRICT_TRADE_THROUGH",
    "AGGRESSOR_TRADE_AT_QUOTE",
    "QUEUE_DEPLETION",
    "PARTIAL_QUEUE_DEPLETION",
    "TERMINAL_EXECUTION",
    "HARD_KILL_EXECUTION",
    "EMERGENCY_EXECUTION",
})


@dataclass(frozen=True)
class CanonicalFill:
    fill_id: str
    order_id: str
    side: Literal["buy", "sell"]
    quantity_btc: Decimal
    fill_price: Decimal
    fee: Decimal
    fee_role: Literal["MAKER", "TAKER"]
    maker_or_taker: Literal["maker", "taker"]
    fee_rate: Decimal
    fee_base_usdt: Decimal
    fee_currency: str
    quote_created_tick: int
    activation_tick: int
    fill_tick: int
    cancel_requested_tick: int | None
    decision_mid: Decimal
    mid_before_fill: Decimal
    mid_at_fill: Decimal
    bid_at_fill: Decimal
    ask_at_fill: Decimal
    quote_distance_bps: Decimal
    inventory_before: Decimal
    inventory_after: Decimal
    scenario_id: str
    profile_id: str
    trigger_type: str
    triggering_event_id: str
    triggering_trade_price: Decimal | None = None
    triggering_trade_quantity: Decimal | None = None

    def validate(self) -> None:
        required_text = (
            self.fill_id, self.order_id, self.scenario_id, self.profile_id,
            self.trigger_type, self.triggering_event_id, self.fee_currency,
        )
        if any(not value for value in required_text):
            raise ValueError("fill identity and provenance are required")
        if self.side not in {"buy", "sell"}:
            raise ValueError("fill side must be buy or sell")
        decimals = (
            self.quantity_btc, self.fill_price, self.fee, self.fee_rate,
            self.fee_base_usdt, self.decision_mid, self.mid_before_fill,
            self.mid_at_fill, self.bid_at_fill, self.ask_at_fill,
            self.quote_distance_bps, self.inventory_before,
            self.inventory_after,
        )
        optional_decimals = (
            self.triggering_trade_price, self.triggering_trade_quantity,
        )
        if (
            any(not value.is_finite() for value in decimals)
            or any(
                value is not None and not value.is_finite()
                for value in optional_decimals
            )
        ):
            raise ValueError("all numeric fill fields must be finite")
        if self.quantity_btc <= ZERO or self.fill_price <= ZERO:
            raise ValueError("fill quantity and price must be positive")
        if (
            self.fee < ZERO or self.fee_rate < ZERO
            or self.fee_base_usdt <= ZERO or self.quote_distance_bps < ZERO
        ):
            raise ValueError("fill fee fields and distance are invalid")
        if (
            self.decision_mid <= ZERO or self.mid_before_fill <= ZERO
            or self.mid_at_fill <= ZERO or self.bid_at_fill <= ZERO
            or self.ask_at_fill <= self.bid_at_fill
        ):
            raise ValueError("fill market context is invalid")
        tick_values = (
            self.quote_created_tick, self.activation_tick, self.fill_tick,
        )
        if (
            any(type(value) is not int or value < 0 for value in tick_values)
            or (
                self.cancel_requested_tick is not None
                and (
                    type(self.cancel_requested_tick) is not int
                    or self.cancel_requested_tick < 0
                )
            )
        ):
            raise ValueError("fill ticks must be non-negative integers")
        if self.activation_tick < self.quote_created_tick:
            raise ValueError("activation cannot precede quote creation")
        if self.fill_tick < self.activation_tick:
            raise ValueError("fill cannot precede activation")
        if self.trigger_type not in FILL_TRIGGERS:
            raise ValueError("unknown fill trigger")
        expected_after = self.inventory_before + (
            self.quantity_btc if self.side == "buy" else -self.quantity_btc
        )
        if expected_after != self.inventory_after:
            raise ValueError("fill inventory transition does not reconcile")
        expected_fee = self.fee_base_usdt * self.fee_rate
        if expected_fee != self.fee:
            raise ValueError("fill fee does not equal fee base times rate")
        if (self.fee_role, self.maker_or_taker) not in {
            ("MAKER", "maker"), ("TAKER", "taker")
        } or self.fee_currency != "USDT":
            raise ValueError("fee role or currency is inconsistent")
        is_trade_trigger = self.trigger_type in {
            "STRICT_TRADE_THROUGH", "AGGRESSOR_TRADE_AT_QUOTE",
            "QUEUE_DEPLETION", "PARTIAL_QUEUE_DEPLETION",
        }
        has_trade_context = (
            self.triggering_trade_price is not None
            and self.triggering_trade_quantity is not None
            and self.triggering_trade_price > ZERO
            and self.triggering_trade_quantity > ZERO
        )
        if is_trade_trigger != has_trade_context:
            raise ValueError("triggering trade context is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass
class _OpenLot:
    fill: CanonicalFill
    remaining_quantity_btc: Decimal
    remaining_fee: Decimal


@dataclass(frozen=True)
class CanonicalRoundTrip:
    round_trip_id: str
    entry_fill_id: str
    exit_fill_id: str
    entry_side: Literal["buy", "sell"]
    matched_quantity_btc: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    gross_execution_pnl: Decimal
    gross_round_trip_spread_capture: Decimal
    realized_inventory_pnl: Decimal
    net_execution_pnl: Decimal
    entry_tick: int
    exit_tick: int
    holding_ticks: int
    entry_mid: Decimal
    exit_mid: Decimal
    terminal_or_normal: Literal["NORMAL", "TERMINAL"]
    hard_kill_or_normal: Literal["NORMAL", "HARD_KILL"]

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


class FIFORoundTripMatcher:
    """Match opposing fill quantities against oldest open inventory lots."""

    def __init__(self) -> None:
        self._open_lots: list[_OpenLot] = []
        self._fill_ids: set[str] = set()
        self._input_fills: list[CanonicalFill] = []
        self.round_trips: list[CanonicalRoundTrip] = []
        self._matched_by_fill: dict[str, Decimal] = {}

    def process(self, fill: CanonicalFill) -> list[CanonicalRoundTrip]:
        fill.validate()
        if fill.fill_id in self._fill_ids:
            raise ValueError(f"duplicate fill identity: {fill.fill_id}")
        if self._input_fills:
            prior = self._input_fills[-1]
            if prior.inventory_after != fill.inventory_before:
                raise ValueError("fill sequence inventory does not reconcile")
        self._fill_ids.add(fill.fill_id)
        self._input_fills.append(fill)

        incoming_quantity = fill.quantity_btc
        incoming_fee = fill.fee
        created: list[CanonicalRoundTrip] = []
        while (
            incoming_quantity > ZERO
            and self._open_lots
            and self._open_lots[0].fill.side != fill.side
        ):
            lot = self._open_lots[0]
            matched = min(lot.remaining_quantity_btc, incoming_quantity)
            entry_fee = (
                lot.remaining_fee * matched / lot.remaining_quantity_btc
            )
            exit_fee = incoming_fee * matched / incoming_quantity
            entry = lot.fill
            if entry.side == "buy":
                gross = (fill.fill_price - entry.fill_price) * matched
                spread = (
                    (entry.decision_mid - entry.fill_price)
                    + (fill.fill_price - fill.decision_mid)
                ) * matched
            else:
                gross = (entry.fill_price - fill.fill_price) * matched
                spread = (
                    (entry.fill_price - entry.decision_mid)
                    + (fill.decision_mid - fill.fill_price)
                ) * matched
            inventory_pnl = gross - spread
            terminal = fill.trigger_type == "TERMINAL_EXECUTION"
            hard_kill = fill.trigger_type == "HARD_KILL_EXECUTION"
            trip = CanonicalRoundTrip(
                round_trip_id=f"fifo-round-trip-{len(self.round_trips)+1:06d}",
                entry_fill_id=entry.fill_id,
                exit_fill_id=fill.fill_id,
                entry_side=entry.side,
                matched_quantity_btc=matched,
                entry_price=entry.fill_price,
                exit_price=fill.fill_price,
                entry_fee=entry_fee,
                exit_fee=exit_fee,
                gross_execution_pnl=gross,
                gross_round_trip_spread_capture=spread,
                realized_inventory_pnl=inventory_pnl,
                net_execution_pnl=gross - entry_fee - exit_fee,
                entry_tick=entry.fill_tick,
                exit_tick=fill.fill_tick,
                holding_ticks=fill.fill_tick - entry.fill_tick,
                entry_mid=entry.decision_mid,
                exit_mid=fill.decision_mid,
                terminal_or_normal="TERMINAL" if terminal else "NORMAL",
                hard_kill_or_normal="HARD_KILL" if hard_kill else "NORMAL",
            )
            self.round_trips.append(trip)
            created.append(trip)
            self._matched_by_fill[entry.fill_id] = (
                self._matched_by_fill.get(entry.fill_id, ZERO) + matched
            )
            self._matched_by_fill[fill.fill_id] = (
                self._matched_by_fill.get(fill.fill_id, ZERO) + matched
            )
            lot.remaining_quantity_btc -= matched
            lot.remaining_fee -= entry_fee
            incoming_quantity -= matched
            incoming_fee -= exit_fee
            if lot.remaining_quantity_btc == ZERO:
                self._open_lots.pop(0)

        if incoming_quantity > ZERO:
            self._open_lots.append(
                _OpenLot(fill, incoming_quantity, incoming_fee)
            )
        return created

    @property
    def unmatched_open_quantity_btc(self) -> Decimal:
        return sum(
            (lot.remaining_quantity_btc for lot in self._open_lots), ZERO
        )

    @property
    def unmatched_signed_inventory_btc(self) -> Decimal:
        return sum(
            (
                lot.remaining_quantity_btc
                if lot.fill.side == "buy"
                else -lot.remaining_quantity_btc
                for lot in self._open_lots
            ),
            ZERO,
        )

    def reconciliation(self) -> dict[str, Any]:
        buy_input = sum(
            (fill.quantity_btc for fill in self._input_fills if fill.side == "buy"),
            ZERO,
        )
        sell_input = sum(
            (fill.quantity_btc for fill in self._input_fills if fill.side == "sell"),
            ZERO,
        )
        matched = sum(
            (trip.matched_quantity_btc for trip in self.round_trips), ZERO
        )
        unmatched_buy = sum(
            (
                lot.remaining_quantity_btc
                for lot in self._open_lots if lot.fill.side == "buy"
            ),
            ZERO,
        )
        unmatched_sell = sum(
            (
                lot.remaining_quantity_btc
                for lot in self._open_lots if lot.fill.side == "sell"
            ),
            ZERO,
        )
        total_input = buy_input + sell_input
        total_fees = sum((fill.fee for fill in self._input_fills), ZERO)
        matched_fees = sum(
            (trip.entry_fee + trip.exit_fee for trip in self.round_trips), ZERO
        )
        unmatched_fees = sum(
            (lot.remaining_fee for lot in self._open_lots), ZERO
        )
        return _serialize({
            "unique_fill_ids": len(self._fill_ids) == len(self._input_fills),
            "buy_input_quantity_btc": buy_input,
            "sell_input_quantity_btc": sell_input,
            "matched_quantity_btc": matched,
            "unmatched_buy_quantity_btc": unmatched_buy,
            "unmatched_sell_quantity_btc": unmatched_sell,
            "buy_quantity_identity": buy_input == matched + unmatched_buy,
            "sell_quantity_identity": sell_input == matched + unmatched_sell,
            "absolute_quantity_identity": (
                total_input == matched * D("2") + unmatched_buy + unmatched_sell
            ),
            "total_unique_fill_fees_usdt": total_fees,
            "matched_allocated_fees_usdt": matched_fees,
            "unmatched_allocated_fees_usdt": unmatched_fees,
            "fee_identity": total_fees == matched_fees + unmatched_fees,
            "duplicate_matched_quantity": any(
                quantity > next(
                    fill.quantity_btc for fill in self._input_fills
                    if fill.fill_id == fill_id
                )
                for fill_id, quantity in self._matched_by_fill.items()
            ),
            "unmatched_signed_inventory_btc": self.unmatched_signed_inventory_btc,
        })


def economic_attribution(
    fills: list[CanonicalFill],
    round_trips: list[CanonicalRoundTrip],
    *,
    funding_usdt: Decimal = ZERO,
    residual_inventory_mark_usdt: Decimal = ZERO,
) -> dict[str, Any]:
    """Return distinct spread, inventory, special-exit, fee, and total fields."""
    terminal_exit_ids = {
        fill.fill_id for fill in fills
        if fill.trigger_type == "TERMINAL_EXECUTION"
    }
    hard_kill_exit_ids = {
        fill.fill_id for fill in fills
        if fill.trigger_type == "HARD_KILL_EXECUTION"
    }
    emergency_exit_ids = {
        fill.fill_id for fill in fills
        if fill.trigger_type == "EMERGENCY_EXECUTION"
    }
    normal = [
        trip for trip in round_trips
        if trip.exit_fill_id not in (
            terminal_exit_ids | hard_kill_exit_ids | emergency_exit_ids
        )
    ]
    terminal = [
        trip for trip in round_trips
        if trip.exit_fill_id in terminal_exit_ids
    ]
    hard_kill = [
        trip for trip in round_trips
        if trip.exit_fill_id in hard_kill_exit_ids
    ]
    emergency = [
        trip for trip in round_trips
        if trip.exit_fill_id in emergency_exit_ids
    ]
    maker_fees = sum(
        (fill.fee for fill in fills if fill.fee_role == "MAKER"), ZERO
    )
    taker_fees = sum(
        (fill.fee for fill in fills if fill.fee_role == "TAKER"), ZERO
    )
    normal_gross = sum(
        (trip.gross_execution_pnl for trip in normal), ZERO
    )
    terminal_pnl = sum(
        (trip.gross_execution_pnl for trip in terminal), ZERO
    )
    hard_kill_pnl = sum(
        (trip.gross_execution_pnl for trip in hard_kill), ZERO
    )
    emergency_pnl = sum(
        (trip.gross_execution_pnl for trip in emergency), ZERO
    )
    net = (
        normal_gross - maker_fees - taker_fees + funding_usdt
        + terminal_pnl + hard_kill_pnl + emergency_pnl
        + residual_inventory_mark_usdt
    )
    payload = {
        "gross_execution_pnl_usdt": normal_gross,
        "gross_round_trip_spread_capture_usdt": sum(
            (trip.gross_round_trip_spread_capture for trip in normal), ZERO
        ),
        "net_round_trip_spread_capture_usdt": sum(
            (
                trip.gross_round_trip_spread_capture
                - trip.entry_fee - trip.exit_fee
                for trip in normal
            ),
            ZERO,
        ),
        "realized_inventory_pnl_usdt": sum(
            (trip.realized_inventory_pnl for trip in normal), ZERO
        ),
        "inventory_mark_to_market_usdt": residual_inventory_mark_usdt,
        "maker_fees_usdt": maker_fees,
        "taker_fees_usdt": taker_fees,
        "funding_usdt": funding_usdt,
        "terminal_liquidation_pnl_usdt": terminal_pnl,
        "hard_kill_execution_pnl_usdt": hard_kill_pnl,
        "emergency_execution_pnl_usdt": emergency_pnl,
        "net_pnl_usdt": net,
        "pnl_identity_reconciles": (
            net == normal_gross - maker_fees - taker_fees + funding_usdt
            + terminal_pnl + hard_kill_pnl + emergency_pnl
            + residual_inventory_mark_usdt
        ),
        "spread_inventory_decomposition_reconciles": all(
            trip.gross_execution_pnl
            == trip.gross_round_trip_spread_capture
            + trip.realized_inventory_pnl
            for trip in round_trips
        ),
    }
    return _serialize(payload)


def markout(
    fill: CanonicalFill, *, future_tick: int, future_mid: Decimal
) -> dict[str, Any]:
    if future_tick < fill.fill_tick:
        raise ValueError("future markout tick cannot precede fill")
    per_btc = (
        future_mid - fill.fill_price
        if fill.side == "buy"
        else fill.fill_price - future_mid
    )
    return _serialize({
        "fill_id": fill.fill_id,
        "side": fill.side,
        "fill_tick": fill.fill_tick,
        "future_tick": future_tick,
        "fill_price": fill.fill_price,
        "future_mid": future_mid,
        "markout_usdt_per_btc": per_btc,
        "markout_usdt_for_fill_quantity": per_btc * fill.quantity_btc,
    })


def quoted_spread(
    bid_quote_price: Decimal, ask_quote_price: Decimal
) -> dict[str, str]:
    spread = ask_quote_price - bid_quote_price
    mid = (ask_quote_price + bid_quote_price) / D("2")
    return {
        "quoted_spread_usdt_per_btc": str(spread),
        "quoted_spread_bps": str(spread / mid * D("10000")),
    }


def effective_fill_edge(fill: CanonicalFill) -> Decimal:
    return (
        fill.decision_mid - fill.fill_price
        if fill.side == "buy"
        else fill.fill_price - fill.decision_mid
    )
