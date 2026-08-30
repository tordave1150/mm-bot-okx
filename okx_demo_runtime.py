"""Frozen lifecycle runner shared by offline fixtures and formal OKX demo."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Callable

from okx_demo_adapter import (
    AccountSnapshot,
    AmbiguousExchangeState,
    DemoAdapterError,
    OkxDemoAdapter,
    SubmittedOrder,
)
from okx_demo_profile import DefensiveOverlayController


class MarketDataSafetyError(DemoAdapterError):
    """Market data is unsafe for order decisions."""


def signed_book_age_ms(
    *,
    observed_at_ms: int,
    exchange_timestamp_ms: int,
    maximum_age_ms: int,
    maximum_clock_skew_ms: int,
) -> int:
    """Return signed book age under asymmetric stale/future budgets."""
    values = (
        observed_at_ms,
        exchange_timestamp_ms,
        maximum_age_ms,
        maximum_clock_skew_ms,
    )
    if any(type(value) is not int for value in values):
        raise ValueError("book timestamp and age budgets must be integers")
    if observed_at_ms <= 0 or exchange_timestamp_ms <= 0:
        raise ValueError("book timestamps must be positive")
    if maximum_age_ms <= 0 or maximum_clock_skew_ms <= 0:
        raise ValueError("book age budgets must be positive")
    age_ms = observed_at_ms - exchange_timestamp_ms
    if age_ms > maximum_age_ms:
        raise ValueError("order book exceeds positive staleness budget")
    if age_ms < -maximum_clock_skew_ms:
        raise ValueError("order book exceeds future clock-skew budget")
    return age_ms


@dataclass
class MarketDataGate:
    maximum_age_ms: int = 1_000
    maximum_clock_skew_ms: int = 1_500
    maximum_clock_regression_ms: int = 0
    last_exchange_timestamp_ms: int = 0

    def validate(self, order_book: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
        bids = order_book.get("bids") or []
        asks = order_book.get("asks") or []
        if not bids or not asks:
            raise MarketDataSafetyError("empty order book")
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        if bid <= 0 or ask <= bid:
            raise MarketDataSafetyError("crossed or invalid order book")
        timestamp = order_book.get("timestamp")
        if type(timestamp) is not int or timestamp <= 0:
            raise MarketDataSafetyError("order book lacks exchange timestamp")
        try:
            age = signed_book_age_ms(
                observed_at_ms=now_ms,
                exchange_timestamp_ms=timestamp,
                maximum_age_ms=self.maximum_age_ms,
                maximum_clock_skew_ms=self.maximum_clock_skew_ms,
            )
        except ValueError as exc:
            raise MarketDataSafetyError("stale or future-dated order book") from exc
        if (
            type(self.maximum_clock_regression_ms) is not int
            or self.maximum_clock_regression_ms < 0
        ):
            raise MarketDataSafetyError("invalid clock-regression budget")
        if timestamp < self.last_exchange_timestamp_ms - self.maximum_clock_regression_ms:
            raise MarketDataSafetyError("non-monotonic exchange timestamp")
        if timestamp == self.last_exchange_timestamp_ms and self.last_exchange_timestamp_ms:
            raise MarketDataSafetyError("duplicate exchange timestamp")
        self.last_exchange_timestamp_ms = timestamp
        return {
            "timestamp": timestamp,
            "bids": bids,
            "asks": asks,
            "best_bid": bid,
            "best_ask": ask,
            "mid_price": (bid + ask) / 2.0,
            "age_ms": age,
        }


@dataclass(frozen=True)
class DemoMatrixSpec:
    warmup_ticks: int = 14
    lifecycle_cycles: int = 3
    tick_interval_seconds: float = 0.35
    resting_seconds: float = 0.50
    flatten_poll_count: int = 8
    flatten_poll_seconds: float = 0.50

    @property
    def offline_control_order_intents(self) -> int:
        return self.lifecycle_cycles * 2

    def validate(self) -> None:
        if self.warmup_ticks < 13:
            raise ValueError(
                "warmup must cover one initial price plus 12 frozen return samples"
            )
        if self.lifecycle_cycles < 1:
            raise ValueError("at least one lifecycle cycle is required")
        for value in (
            self.tick_interval_seconds,
            self.resting_seconds,
            self.flatten_poll_seconds,
        ):
            if value < 0 or value > 5:
                raise ValueError("invalid bounded wait")
        if not 1 <= self.flatten_poll_count <= 20:
            raise ValueError("invalid flatten poll budget")


@dataclass
class DemoMatrixResult:
    status: str = "RUNNING"
    preflight: dict[str, Any] = field(default_factory=dict)
    market_events: list[dict[str, Any]] = field(default_factory=list)
    order_events: list[dict[str, Any]] = field(default_factory=list)
    trade_events: list[dict[str, Any]] = field(default_factory=list)
    defensive_events: list[dict[str, Any]] = field(default_factory=list)
    safety_events: list[dict[str, Any]] = field(default_factory=list)
    acknowledged_normal_orders: int = 0
    cancelled_normal_orders: int = 0
    normal_fill_count: int = 0
    special_fill_count: int = 0
    unknown_fill_count: int = 0
    bid_normal_fills: int = 0
    ask_normal_fills: int = 0
    final_position_btc: float = 0.0
    final_open_order_count: int = 0
    actual_fees_usdt: float = 0.0
    gross_execution_pnl_usdt: float = 0.0
    net_execution_pnl_usdt: float = 0.0
    activity_retention: float = 0.0
    stale_placements: int = 0
    duplicate_orders: int = 0
    ambiguous_retries: int = 0
    flatten_submission_count: int = 0
    flatten_confirmation_count: int = 0
    live_endpoint_attempts: int = 0
    fatal_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_trade_record(
    trade: dict[str, Any],
    adapter: OkxDemoAdapter,
    *,
    classification: str,
) -> dict[str, Any]:
    assert adapter.market_spec is not None
    contracts = Decimal(str(trade.get("amount") or 0))
    quantity_btc = adapter.market_spec.contracts_to_base(contracts)
    fee = trade.get("fee") or {}
    fee_cost = abs(float(fee.get("cost") or 0.0))
    fee_currency = str(fee.get("currency") or "USDT").upper()
    price = float(trade.get("price") or 0.0)
    fee_usdt = fee_cost if fee_currency == "USDT" else fee_cost * price
    return {
        "trade_id": str(trade.get("id") or ""),
        "order_id": str(trade.get("order") or ""),
        "side": str(trade.get("side") or ""),
        "price": price,
        "quantity_btc": float(quantity_btc),
        "fee_usdt": fee_usdt,
        "liquidity": str(trade.get("takerOrMaker") or "unknown"),
        "timestamp_ms": int(trade.get("timestamp") or 0),
        "classification": classification,
        "normal_activity_eligible": classification == "NORMAL_MAKER",
    }


def _execution_economics(trades: list[dict[str, Any]]) -> tuple[float, float, float]:
    cash = 0.0
    fees = 0.0
    for trade in trades:
        signed_cash = trade["price"] * trade["quantity_btc"]
        cash += signed_cash if trade["side"] == "sell" else -signed_cash
        fees += trade["fee_usdt"]
    return cash, fees, cash - fees


class DemoMatrixRunner:
    def __init__(
        self,
        *,
        adapter: OkxDemoAdapter,
        spec: DemoMatrixSpec,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        spec.validate()
        self.adapter = adapter
        self.spec = spec
        self.sleep = sleep
        self.now_ms = now_ms
        self.event_sink = event_sink
        self.market_gate = MarketDataGate()
        self.strategy = adapter.profile.build_strategy()
        self.overlay = DefensiveOverlayController(adapter.profile.defensive_overlay)
        self.normal_order_ids: set[str] = set()
        self.flatten_order_ids: set[str] = set()

    def _record(
        self,
        result: DemoMatrixResult,
        stream: str,
        row: dict[str, Any],
    ) -> None:
        getattr(result, stream).append(row)
        if self.event_sink is not None:
            self.event_sink(stream, row)

    def _record_many(
        self,
        result: DemoMatrixResult,
        stream: str,
        rows: list[dict[str, Any]],
    ) -> None:
        for row in rows:
            self._record(result, stream, row)

    def _snapshot(self, result: DemoMatrixResult) -> AccountSnapshot:
        snapshot = self.adapter.preflight()
        for trade in self.adapter.last_new_trades:
            order_id = str(trade.get("order") or "")
            if order_id and order_id in self.flatten_order_ids:
                classification = "SPECIAL_REDUCE_ONLY"
            elif order_id and order_id in self.normal_order_ids:
                classification = "NORMAL_MAKER"
            else:
                classification = "UNKNOWN"
            record = _safe_trade_record(
                trade,
                self.adapter,
                classification=classification,
            )
            self._record(result, "trade_events", record)
            if classification == "SPECIAL_REDUCE_ONLY":
                result.special_fill_count += 1
            elif classification == "NORMAL_MAKER":
                result.normal_fill_count += 1
                if record["side"] == "buy":
                    result.bid_normal_fills += 1
                elif record["side"] == "sell":
                    result.ask_normal_fills += 1
            else:
                result.unknown_fill_count += 1
                raise AmbiguousExchangeState(
                    "new trade does not map to a formal normal or flatten order"
                )
        return snapshot

    def _market(self, result: DemoMatrixResult) -> dict[str, Any]:
        try:
            raw = self.adapter.exchange.fetch_order_book(self.adapter.config.symbol)
            market = self.market_gate.validate(raw, now_ms=self.now_ms())
            self._record(result, "market_events", {
                "event": "MARKET_ACCEPTED",
                "timestamp_ms": market["timestamp"],
                "best_bid": market["best_bid"],
                "best_ask": market["best_ask"],
                "age_ms": market["age_ms"],
            })
            return market
        except Exception as exc:
            try:
                self.adapter.cancel_all_owned()
            finally:
                self.adapter._halt("MARKET_DATA_UNSAFE")
            self._record(result, "safety_events", {
                "event": "MARKET_DATA_HALT",
                "error_type": type(exc).__name__,
            })
            raise

    def _persist_overlay(self) -> None:
        if self.adapter.state is None:
            raise DemoAdapterError("runtime state unavailable")
        self.adapter.state.defensive_overlay_state = self.overlay.to_dict()
        self.adapter.state_store.save(self.adapter.state)

    def run(self) -> DemoMatrixResult:
        result = DemoMatrixResult()
        submitted: list[SubmittedOrder] = []
        try:
            initial = self._snapshot(result)
            result.preflight = initial.public_dict()
            self.adapter.configure_and_verify_account_mode()
            configured = self._snapshot(result)
            if configured.position_mode != self.adapter.config.position_mode:
                raise DemoAdapterError("configured position mode mismatch")
            if configured.leverage != float(self.adapter.config.leverage):
                raise DemoAdapterError("configured leverage mismatch")
            if self.adapter.state and self.adapter.state.defensive_overlay_state:
                self.overlay = DefensiveOverlayController.from_dict(
                    self.adapter.state.defensive_overlay_state,
                    self.adapter.profile.defensive_overlay,
                )

            decision = None
            for _ in range(self.spec.warmup_ticks):
                snapshot = self._snapshot(result)
                market = self._market(result)
                decision = self.strategy.decide(
                    {
                        "timestamp": market["timestamp"],
                        "bids": market["bids"],
                        "asks": market["asks"],
                    },
                    inventory_btc=snapshot.position_btc,
                    market_data_age_ms=market["age_ms"],
                    staleness_limit_ms=self.market_gate.maximum_age_ms,
                )
                self.sleep(self.spec.tick_interval_seconds)
            if decision is None:
                raise DemoAdapterError("volatility warmup produced no decision")

            for cycle in range(self.spec.lifecycle_cycles):
                snapshot = self._snapshot(result)
                market = self._market(result)
                decision = self.strategy.decide(
                    {
                        "timestamp": market["timestamp"],
                        "bids": market["bids"],
                        "asks": market["asks"],
                    },
                    inventory_btc=snapshot.position_btc,
                    market_data_age_ms=market["age_ms"],
                    staleness_limit_ms=self.market_gate.maximum_age_ms,
                )
                if decision is None or not decision.quote_allowed:
                    raise DemoAdapterError("formal quote decision unavailable")
                peak = max(snapshot.total_equity_usdt, self.adapter.state.peak_equity_usdt)
                drawdown = max(0.0, (peak - snapshot.total_equity_usdt) / peak)
                overlay = self.overlay.evaluate(
                    mid_price=market["mid_price"],
                    inventory_btc=snapshot.position_btc,
                    drawdown=drawdown,
                    normal_fill_observed=self.adapter.last_new_trade_count > 0,
                )
                self._record_many(result, "defensive_events", overlay.events)
                self._persist_overlay()
                if not overlay.allow_quoting:
                    self.adapter.cancel_all_owned()
                    raise DemoAdapterError("defensive drawdown guard halted formal matrix")
                desired = (
                    ("buy", decision.rounded_bid, decision.bid_suppressed),
                    ("sell", decision.rounded_ask, decision.ask_suppressed),
                )
                cycle_orders: list[SubmittedOrder] = []
                for side, price, profile_suppressed in desired:
                    if profile_suppressed or overlay.suppress_side == side:
                        continue
                    order = self.adapter.submit_post_only(
                        side=side,
                        price=price,
                        best_bid=market["best_bid"],
                        best_ask=market["best_ask"],
                        market_timestamp_ms=market["timestamp"],
                    )
                    submitted.append(order)
                    cycle_orders.append(order)
                    self.normal_order_ids.add(order.order_id)
                    result.acknowledged_normal_orders += 1
                    self._record(result, "order_events", {
                        "event": "POST_ONLY_ACKNOWLEDGED",
                        "cycle": cycle,
                        "order_id": order.order_id,
                        "client_order_id": order.client_order_id,
                        "side": side,
                        "price": price,
                        "contracts": order.contracts,
                    })
                self.sleep(self.spec.resting_seconds)
                self._snapshot(result)
                cancelled = self.adapter.cancel_all_owned()
                result.cancelled_normal_orders += len(cancelled)
                for order_id in cancelled:
                    self._record(result, "order_events", {
                        "event": "CANCEL_CONFIRMED",
                        "cycle": cycle,
                        "order_id": order_id,
                    })
                self.sleep(self.spec.tick_interval_seconds)

            final_snapshot = self._snapshot(result)
            self.adapter.cancel_all_owned()
            if abs(final_snapshot.position_btc) > 1e-12:
                self.adapter.state.kill_switch.activate("TERMINAL_RESIDUAL_INVENTORY")
                self.adapter.state_store.save(self.adapter.state)
                flatten_order = self.adapter.submit_emergency_flatten(
                    position_btc=final_snapshot.position_btc,
                    reference_price=result.market_events[-1]["best_bid"],
                )
                if flatten_order is None:
                    raise DemoAdapterError("nonzero inventory produced no flatten order")
                self.flatten_order_ids.add(flatten_order.order_id)
                result.flatten_submission_count += 1
                confirmed = False
                for _ in range(self.spec.flatten_poll_count):
                    self.sleep(self.spec.flatten_poll_seconds)
                    polled = self._snapshot(result)
                    if self.adapter.reconcile_flatten(polled):
                        confirmed = True
                        result.flatten_confirmation_count += 1
                        break
                if not confirmed:
                    raise AmbiguousExchangeState("terminal flatten not confirmed")
            terminal = self._snapshot(result)
            result.final_position_btc = terminal.position_btc
            result.final_open_order_count = len(terminal.open_orders)
            gross, fees, net = _execution_economics(result.trade_events)
            result.gross_execution_pnl_usdt = gross
            result.actual_fees_usdt = fees
            result.net_execution_pnl_usdt = net
            result.activity_retention = (
                result.acknowledged_normal_orders
                / self.spec.offline_control_order_intents
            )
            if result.final_open_order_count or abs(result.final_position_btc) > 1e-12:
                raise DemoAdapterError("terminal account state is not flat and empty")
            if result.activity_retention < 0.80:
                result.status = "OKX_DEMO_ACTIVITY_INSUFFICIENT"
            else:
                result.status = "OKX_DEMO_EXECUTION_SAFETY_SUPPORT"
            return result
        except Exception as exc:
            result.fatal_error = type(exc).__name__
            result.status = (
                "OKX_DEMO_RECONCILIATION_FAILED"
                if isinstance(exc, AmbiguousExchangeState)
                else "OKX_DEMO_SAFETY_FAILED"
            )
            try:
                self.adapter.cancel_all_owned()
            except Exception as cancel_exc:
                self._record(result, "safety_events", {
                    "event": "SAFE_SHUTDOWN_CANCEL_FAILED",
                    "error_type": type(cancel_exc).__name__,
                })
            raise DemoRunFailed(result) from exc


class DemoRunFailed(RuntimeError):
    def __init__(self, result: DemoMatrixResult):
        super().__init__(result.status)
        self.result = result
