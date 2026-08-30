import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

import okx_demo_adapter
from market_spec import MarketSpec
from okx_demo_adapter import (
    AmbiguousExchangeState,
    DemoAdapterConfig,
    DemoAdapterError,
    ExecutionMode,
    OkxDemoAdapter,
)
from okx_demo_profile import load_promoted_profile
from okx_demo_state import DemoStateStore
from okx_demo_state import DemoStateError
from okx_demo_runtime import (
    DemoMatrixRunner,
    DemoMatrixResult,
    DemoMatrixSpec,
    DemoRunFailed,
    MarketDataGate,
    MarketDataSafetyError,
)


ROOT = Path(__file__).resolve().parents[1]


def _market_spec() -> MarketSpec:
    return MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.01"),
        amount_step=Decimal("1"),
        min_amount=Decimal("1"),
        min_notional=None,
        price_tick=Decimal("0.1"),
        amount_precision=0,
        price_precision=1,
        linear=True,
        inverse=False,
    )


class FaultExchange:
    def __init__(self) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.orders: dict[str, dict] = {}
        self.order_history: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.create_calls = 0
        self.cancel_calls = 0
        self.create_response_lost = False
        self.create_rejected = False
        self.cancel_response_lost = False
        self.cancel_keeps_order = False
        self.fetch_open_fails = False
        self.fetch_trades_fails = False
        self.fetch_balance_fails = False
        self.fetch_positions_fails = False
        self.fetch_leverage_fails = False
        self.fetch_fee_fails = False
        self.clock_offset_ms = 0
        self.total_equity = 10_000.0
        self.free_equity = 10_000.0
        self.position_btc = 0.0
        self.position_mode = "net_mode"
        self.leverage = 3.0
        self.market_sequence = 0
        self.market_fault = ""
        self.flatten_fraction = 1.0

    def fetch_time(self):
        return int(time.time() * 1000) + self.clock_offset_ms

    def fetch_markets(self):
        return [{
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "info": {"instType": "SWAP", "state": "live"},
        }]

    def set_markets(self, markets):
        self.markets = {row["symbol"]: row for row in markets}
        return self.markets

    def fetch_order_book(self, symbol):
        self.market_sequence += 1
        timestamp = int(time.time() * 1000) + self.market_sequence
        if self.market_fault == "stale":
            timestamp -= 10_000
        if self.market_fault == "duplicate" and self.market_sequence > 1:
            timestamp = self.last_market_timestamp
        if self.market_fault == "error":
            raise TimeoutError("injected REST market outage")
        if self.market_fault == "rate_limit":
            raise RuntimeError("RateLimitExceeded: injected market outage")
        bid, ask = 49_999.0, 50_001.0
        if self.market_fault == "crossed":
            bid, ask = 50_002.0, 50_001.0
        self.last_market_timestamp = timestamp
        return {
            "timestamp": timestamp,
            "bids": [[bid, 10.0]],
            "asks": [[ask, 10.0]],
        }

    def fetch_balance(self):
        if self.fetch_balance_fails:
            raise TimeoutError("injected balance outage")
        return {"USDT": {"total": self.total_equity, "free": self.free_equity}}

    def privateGetAccountConfig(self):
        return {"data": [{"uid": "demo-uid", "posMode": self.position_mode}]}

    def fetch_positions(self, symbols):
        if self.fetch_positions_fails:
            raise TimeoutError("injected position outage")
        if not self.position_btc:
            return []
        return [{
            "symbol": "BTC/USDT:USDT",
            "contracts": abs(self.position_btc) / 0.01,
            "side": "long" if self.position_btc > 0 else "short",
            "entryPrice": 50_000.0,
            "maintenanceMargin": 1.0,
        }]

    def fetch_open_orders(self, symbol):
        if self.fetch_open_fails:
            raise TimeoutError("injected open-order outage")
        return [row.copy() for row in self.orders.values()]

    def fetch_my_trades(self, symbol, limit=100):
        if self.fetch_trades_fails:
            raise TimeoutError("injected trade outage")
        return [row.copy() for row in self.trades[-limit:]]

    def fetch_leverage(self, symbol, params):
        if self.fetch_leverage_fails:
            raise TimeoutError("injected leverage outage")
        return {"longLeverage": self.leverage, "shortLeverage": self.leverage}

    def fetch_trading_fee(self, symbol):
        if self.fetch_fee_fails:
            raise TimeoutError("injected fee outage")
        return {"maker": 0.0002, "taker": 0.0005}

    def set_position_mode(self, hedged, symbol):
        self.position_mode = "long_short_mode" if hedged else "net_mode"
        return {"code": "0"}

    def fetch_position_mode(self, symbol):
        return {"hedged": self.position_mode != "net_mode"}

    def set_leverage(self, leverage, symbol, params):
        self.leverage = float(leverage)
        return {"code": "0"}

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_calls += 1
        cid = params["clOrdId"]
        if self.create_rejected:
            raise RuntimeError("injected create rejection")
        order = {
            "id": f"order-{self.create_calls}",
            "clientOrderId": cid,
            "side": side,
            "price": price,
            "amount": amount,
            "status": "open",
            "postOnly": True,
            "info": {"clOrdId": cid, "ordType": "post_only"},
        }
        self.orders[cid] = order
        self.order_history[cid] = order.copy()
        if order_type == "market" and params.get("reduceOnly"):
            before = self.position_btc
            base_amount = float(amount) * 0.01 * self.flatten_fraction
            if side == "sell":
                self.position_btc = max(0.0, before - base_amount)
            else:
                self.position_btc = min(0.0, before + base_amount)
            self.trades.append({
                "id": f"trade-{self.create_calls}",
                "order": order["id"],
                "side": side,
                "price": 50_000.0,
                "amount": float(amount) * self.flatten_fraction,
                "fee": {"cost": 0.25, "currency": "USDT"},
                "takerOrMaker": "taker",
                "timestamp": int(time.time() * 1000) + self.create_calls,
            })
            del self.orders[cid]
        if self.create_response_lost:
            raise TimeoutError("accepted but response lost")
        return order.copy()

    def fetch_order(self, order_id, symbol, params):
        cid = params.get("clientOrderId") or order_id
        if cid in self.orders:
            return self.orders[cid].copy()
        if cid not in self.order_history:
            raise RuntimeError("order does not exist")
        return self.order_history[cid].copy()

    def cancel_order(self, order_id, symbol):
        self.cancel_calls += 1
        match = next(
            (cid for cid, row in self.orders.items() if row["id"] == order_id), None
        )
        if match and not self.cancel_keeps_order:
            del self.orders[match]
        if self.cancel_response_lost:
            raise TimeoutError("cancel response lost")
        return {"id": order_id}

    def close(self):
        return None


def _unstarted_adapter(
    tmp_path: Path,
    exchange: FaultExchange | None = None,
    *,
    session_id: str = "test-session",
) -> OkxDemoAdapter:
    exchange = exchange or FaultExchange()
    profile = load_promoted_profile(ROOT)
    adapter = OkxDemoAdapter(
        exchange=exchange,
        config=DemoAdapterConfig(
            mode=ExecutionMode.OKX_DEMO,
            explicit_arm_token=f"OKX_DEMO:{session_id}",
        ),
        promoted_profile=profile,
        session_id=session_id,
        state_store=DemoStateStore(tmp_path / "state.json"),
    )
    okx_demo_adapter.fetch_market_info = lambda *args, **kwargs: {
        "market_spec": _market_spec(),
        "tick_size": 0.1,
        "base_step": 0.01,
    }
    return adapter


def _adapter(tmp_path: Path, exchange: FaultExchange | None = None) -> OkxDemoAdapter:
    adapter = _unstarted_adapter(tmp_path, exchange)
    adapter.preflight()
    return adapter


def _submit(adapter: OkxDemoAdapter, *, side: str = "buy", price: float = 49_970.0):
    return adapter.submit_post_only(
        side=side,
        price=price,
        best_bid=49_999.0,
        best_ask=50_001.0,
        market_timestamp_ms=int(time.time() * 1000),
    )


def test_live_mode_is_structurally_unavailable() -> None:
    with pytest.raises(DemoAdapterError, match="LIVE mode"):
        DemoAdapterConfig(mode=ExecutionMode.LIVE).validate("session")


def test_demo_mode_requires_session_scoped_explicit_arm() -> None:
    with pytest.raises(DemoAdapterError, match="explicitly armed"):
        DemoAdapterConfig(mode=ExecutionMode.OKX_DEMO).validate("session")


def test_empty_position_snapshot_explicitly_reconciles_zero(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter.state is not None
    assert adapter.state.inventory_btc == 0.0
    assert adapter.state.average_entry_price == 0.0


def test_account_mode_and_leverage_are_set_then_verified(tmp_path: Path) -> None:
    exchange = FaultExchange()
    exchange.position_mode = "long_short_mode"
    exchange.leverage = 1.0
    adapter = _adapter(tmp_path, exchange)
    adapter.configure_and_verify_account_mode()
    assert exchange.position_mode == "net_mode"
    assert exchange.leverage == 3.0


def test_create_accepted_response_lost_resolves_once_without_retry(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    exchange.create_response_lost = True
    submitted = _submit(adapter)
    assert exchange.create_calls == 1
    assert submitted.client_order_id in exchange.orders
    assert adapter.halted_reason == "AMBIGUOUS_CREATE_RESPONSE"


def test_create_rejected_is_ambiguous_and_never_retried(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    exchange.create_rejected = True
    with pytest.raises(AmbiguousExchangeState, match="automatic retry"):
        _submit(adapter)
    assert exchange.create_calls == 1
    assert adapter.allow_new_orders is False


def test_emergency_flatten_is_single_flight_and_reduce_only(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    assert adapter.state is not None
    adapter.state.inventory_btc = 0.02
    submitted = adapter.submit_emergency_flatten(
        position_btc=0.02, reference_price=50_000.0
    )
    assert submitted is not None
    assert submitted.reduce_only is True
    assert adapter.state.flatten_attempts == 1
    with pytest.raises(AmbiguousExchangeState, match="already submitted"):
        adapter.submit_emergency_flatten(
            position_btc=0.02, reference_price=50_000.0
        )


def test_delayed_flatten_position_update_does_not_resubmit(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    assert adapter.state is not None
    adapter.state.inventory_btc = -0.02
    adapter.submit_emergency_flatten(
        position_btc=-0.02, reference_price=50_000.0
    )
    create_count = exchange.create_calls
    snapshot = adapter.preflight()
    assert adapter.reconcile_flatten(snapshot) is True
    assert exchange.create_calls == create_count
    assert adapter.state.flatten_state == "CONFIRMED"


def test_cancel_timeout_but_accepted_is_confirmed_by_snapshot(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    order = _submit(adapter)
    exchange.cancel_response_lost = True
    assert adapter.cancel_confirmed(order) is True
    assert exchange.cancel_calls == 1
    assert adapter.state is not None
    assert adapter.state.owned_open_orders == {}


def test_cancel_timeout_and_order_still_open_halts_without_replacement(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    order = _submit(adapter)
    exchange.cancel_response_lost = True
    exchange.cancel_keeps_order = True
    assert adapter.cancel_confirmed(order) is False
    assert adapter.allow_new_orders is False
    create_count = exchange.create_calls
    with pytest.raises(DemoAdapterError, match="halted"):
        _submit(adapter, price=49_960.0)
    assert exchange.create_calls == create_count


def test_cancel_confirmation_outage_halts_new_orders(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    order = _submit(adapter)
    exchange.fetch_open_fails = True
    with pytest.raises(AmbiguousExchangeState, match="confirmation unavailable"):
        adapter.cancel_confirmed(order)
    assert adapter.allow_new_orders is False


def test_fresh_session_rejects_foreign_order(tmp_path: Path) -> None:
    exchange = FaultExchange()
    exchange.orders["foreign"] = {
        "id": "foreign-order", "clientOrderId": "foreign", "side": "buy",
        "price": 49_000.0, "amount": 1.0,
    }
    profile = load_promoted_profile(ROOT)
    adapter = OkxDemoAdapter(
        exchange=exchange,
        config=DemoAdapterConfig(
            mode=ExecutionMode.OKX_DEMO,
            explicit_arm_token="OKX_DEMO:test-session",
        ),
        promoted_profile=profile,
        session_id="test-session",
        state_store=DemoStateStore(tmp_path / "state.json"),
    )
    okx_demo_adapter.fetch_market_info = lambda *args, **kwargs: {
        "market_spec": _market_spec(), "tick_size": 0.1, "base_step": 0.01,
    }
    with pytest.raises(DemoAdapterError, match="unowned"):
        adapter.preflight()
    assert adapter.allow_new_orders is False
    diagnostic = adapter.last_account_only_diagnostic
    assert diagnostic is not None
    assert diagnostic["signed_position_btc"] == "0"
    assert diagnostic["position_row_count"] == 0
    assert diagnostic["open_order_count"] == 1
    assert diagnostic["account_binding_sha256"] == hashlib.sha256(
        b"demo-uid"
    ).hexdigest()
    serialized = json.dumps(diagnostic, sort_keys=True)
    assert "demo-uid" not in serialized
    assert "foreign-order" not in serialized
    assert '"foreign"' not in serialized


def test_fresh_session_exposure_records_only_hashed_account_diagnostic(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    exchange.position_btc = -0.01
    adapter = _unstarted_adapter(tmp_path, exchange)

    with pytest.raises(DemoAdapterError, match="exposure"):
        adapter.preflight()

    diagnostic = adapter.last_account_only_diagnostic
    assert diagnostic is not None
    assert diagnostic == {
        "signed_position_btc": "-0.01",
        "position_row_count": 1,
        "open_order_count": 0,
        "account_binding_sha256": hashlib.sha256(b"demo-uid").hexdigest(),
        "position_identifiers_sha256": diagnostic[
            "position_identifiers_sha256"
        ],
        "open_order_identifiers_sha256": diagnostic[
            "open_order_identifiers_sha256"
        ],
    }
    assert len(str(diagnostic["position_identifiers_sha256"])) == 64
    assert len(str(diagnostic["open_order_identifiers_sha256"])) == 64
    serialized = json.dumps(diagnostic, sort_keys=True)
    assert "demo-uid" not in serialized
    assert "BTC/USDT:USDT" not in serialized
    assert exchange.create_calls == 0
    assert exchange.cancel_calls == 0


def test_offline_demo_matrix_completes_full_order_lifecycle(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    runner = DemoMatrixRunner(
        adapter=adapter,
        spec=DemoMatrixSpec(
            warmup_ticks=13,
            lifecycle_cycles=2,
            tick_interval_seconds=0.0,
            resting_seconds=0.0,
            flatten_poll_seconds=0.0,
        ),
        sleep=lambda _: None,
    )
    result = runner.run()
    assert result.status == "OKX_DEMO_EXECUTION_SAFETY_SUPPORT"
    assert result.acknowledged_normal_orders == 4
    assert result.cancelled_normal_orders == 4
    assert result.activity_retention == 1.0
    assert result.final_open_order_count == 0
    assert result.final_position_btc == 0.0
    assert exchange.create_calls == 4


def test_special_reduce_only_fill_is_excluded_from_normal_activity(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    adapter.preflight()
    runner = DemoMatrixRunner(
        adapter=adapter,
        spec=DemoMatrixSpec(
            warmup_ticks=13,
            lifecycle_cycles=1,
            tick_interval_seconds=0.0,
            resting_seconds=0.0,
            flatten_poll_seconds=0.0,
        ),
        sleep=lambda _: None,
    )
    runner.flatten_order_ids.add("flatten-order-1")
    exchange.trades.append({
        "id": "flatten-trade-1",
        "order": "flatten-order-1",
        "side": "sell",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.25, "currency": "USDT"},
        "takerOrMaker": "taker",
        "timestamp": int(time.time() * 1000),
    })
    exchange.position_btc = -0.01
    result = DemoMatrixResult()
    runner._snapshot(result)
    assert result.normal_fill_count == 0
    assert result.special_fill_count == 1
    assert result.trade_events[0]["classification"] == "SPECIAL_REDUCE_ONLY"
    assert result.trade_events[0]["normal_activity_eligible"] is False


def test_unknown_new_trade_fails_closed(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    adapter.preflight()
    runner = DemoMatrixRunner(
        adapter=adapter,
        spec=DemoMatrixSpec(
            warmup_ticks=13,
            lifecycle_cycles=1,
            tick_interval_seconds=0.0,
            resting_seconds=0.0,
            flatten_poll_seconds=0.0,
        ),
        sleep=lambda _: None,
    )
    exchange.trades.append({
        "id": "foreign-trade-1",
        "order": "foreign-order-1",
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": int(time.time() * 1000),
    })
    exchange.position_btc = 0.01
    result = DemoMatrixResult()
    with pytest.raises(AmbiguousExchangeState, match="does not map"):
        runner._snapshot(result)
    assert result.unknown_fill_count == 1


@pytest.mark.parametrize(
    "fault", ["stale", "crossed", "error", "duplicate", "rate_limit"]
)
def test_unsafe_market_data_proves_zero_new_submissions(
    tmp_path: Path, fault: str
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    exchange.market_fault = fault
    runner = DemoMatrixRunner(
        adapter=adapter,
        spec=DemoMatrixSpec(
            warmup_ticks=13,
            lifecycle_cycles=1,
            tick_interval_seconds=0.0,
            resting_seconds=0.0,
            flatten_poll_seconds=0.0,
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(DemoRunFailed) as failure:
        runner.run()
    assert failure.value.result.status == "OKX_DEMO_SAFETY_FAILED"
    assert exchange.create_calls == 0
    assert adapter.allow_new_orders is False


@pytest.mark.parametrize(
    "failure_flag",
    [
        "fetch_open_fails",
        "fetch_trades_fails",
        "fetch_balance_fails",
        "fetch_positions_fails",
        "fetch_leverage_fails",
        "fetch_fee_fails",
    ],
)
def test_authoritative_snapshot_outage_proves_zero_new_submissions(
    tmp_path: Path,
    failure_flag: str,
) -> None:
    exchange = FaultExchange()
    setattr(exchange, failure_flag, True)
    adapter = _unstarted_adapter(tmp_path, exchange)
    with pytest.raises(DemoAdapterError):
        adapter.preflight()
    assert exchange.create_calls == 0
    assert adapter.allow_new_orders is False


def test_clock_skew_preflight_proves_zero_new_submissions(tmp_path: Path) -> None:
    exchange = FaultExchange()
    exchange.clock_offset_ms = 10_000
    adapter = _unstarted_adapter(tmp_path, exchange)
    with pytest.raises(DemoAdapterError, match="clock skew"):
        adapter.preflight()
    assert exchange.create_calls == 0
    assert adapter.allow_new_orders is False


def test_duplicate_same_side_intent_is_rejected_before_create(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    _submit(adapter, side="buy")
    create_count = exchange.create_calls
    with pytest.raises(DemoAdapterError, match="duplicate same-side"):
        _submit(adapter, side="buy", price=49_960.0)
    assert exchange.create_calls == create_count


def test_authoritative_free_equity_limits_order_before_create(tmp_path: Path) -> None:
    exchange = FaultExchange()
    exchange.free_equity = 1.0
    adapter = _adapter(tmp_path, exchange)
    with pytest.raises(DemoAdapterError, match="margin limit"):
        _submit(adapter)
    assert exchange.create_calls == 0


def test_missing_expected_order_without_trade_fails_closed(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    submitted = _submit(adapter)
    exchange.orders.pop(submitted.client_order_id)
    with pytest.raises(DemoAdapterError, match="missing expected"):
        adapter.preflight()
    assert adapter.allow_new_orders is False


def test_partial_fill_reconciles_live_remainder_and_persistent_accounting(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    submitted = _submit(adapter)
    exchange.trades.append({
        "id": "partial-fill-1",
        "order": submitted.order_id,
        "side": "buy",
        "price": 50_000.0,
        "amount": 0.4,
        "fee": {"cost": 0.04, "currency": "USDT"},
        "timestamp": int(time.time() * 1000) + 1,
    })
    exchange.position_btc = 0.004
    adapter.preflight()
    assert adapter.state is not None
    assert submitted.client_order_id in adapter.state.owned_open_orders
    assert adapter.state.inventory_btc == pytest.approx(0.004)
    assert adapter.state.average_entry_price == pytest.approx(50_000.0)
    assert adapter.state.total_fees_usdt == pytest.approx(0.04)
    assert adapter.state.net_realized_pnl_usdt == pytest.approx(-0.04)


def test_late_fill_reconciles_disappeared_order_and_position(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    submitted = _submit(adapter)
    exchange.orders.pop(submitted.client_order_id)
    exchange.trades.append({
        "id": "late-fill-1",
        "order": submitted.order_id,
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "timestamp": int(time.time() * 1000) + 1,
    })
    exchange.position_btc = 0.01
    adapter.preflight()
    assert adapter.state is not None
    assert adapter.state.owned_open_orders == {}
    assert adapter.state.inventory_btc == pytest.approx(0.01)


def test_restart_before_fill_restores_owned_order_generation(tmp_path: Path) -> None:
    exchange = FaultExchange()
    first = _adapter(tmp_path, exchange)
    submitted = _submit(first)
    generation = first.state.client_order_generation
    restarted = _unstarted_adapter(tmp_path, exchange)
    restarted.preflight()
    assert restarted.state is not None
    assert restarted.state.client_order_generation == generation
    assert submitted.client_order_id in restarted.state.owned_open_orders


def test_restart_after_fill_restores_fill_cursor_position_and_fees(tmp_path: Path) -> None:
    exchange = FaultExchange()
    first = _adapter(tmp_path, exchange)
    submitted = _submit(first)
    exchange.orders.pop(submitted.client_order_id)
    exchange.trades.append({
        "id": "restart-fill-1",
        "order": submitted.order_id,
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "timestamp": int(time.time() * 1000) + 1,
    })
    exchange.position_btc = 0.01
    first.preflight()
    restarted = _unstarted_adapter(tmp_path, exchange)
    restarted.preflight()
    assert restarted.state is not None
    assert restarted.state.inventory_btc == pytest.approx(0.01)
    assert restarted.state.total_fees_usdt == pytest.approx(0.10)
    assert restarted.last_new_trade_count == 0


@pytest.mark.parametrize("position_btc", [0.01, -0.01])
def test_long_and_short_position_restore_reconcile_exactly(
    tmp_path: Path,
    position_btc: float,
) -> None:
    exchange = FaultExchange()
    first = _adapter(tmp_path, exchange)
    assert first.state is not None
    first.state.inventory_btc = position_btc
    first.state.average_entry_price = 50_000.0
    first.state_store.save(first.state)
    exchange.position_btc = position_btc
    restarted = _unstarted_adapter(tmp_path, exchange)
    snapshot = restarted.preflight()
    assert snapshot.position_btc == pytest.approx(position_btc)
    assert restarted.state.inventory_btc == pytest.approx(position_btc)


def test_kill_switch_survives_restart_and_blocks_new_orders(tmp_path: Path) -> None:
    exchange = FaultExchange()
    first = _adapter(tmp_path, exchange)
    assert first.state is not None
    activation_id = first.state.kill_switch.activate("restart-fixture", now=100.0)
    first.state_store.save(first.state)
    restarted = _unstarted_adapter(tmp_path, exchange)
    restarted.preflight()
    assert restarted.state is not None
    assert restarted.state.kill_switch.activation_id == activation_id
    with pytest.raises(DemoAdapterError, match="kill switch"):
        _submit(restarted)
    assert exchange.create_calls == 0


def test_ambiguous_cancel_latch_survives_supervisor_restart(tmp_path: Path) -> None:
    exchange = FaultExchange()
    first = _adapter(tmp_path, exchange)
    order = _submit(first)
    exchange.cancel_keeps_order = True
    assert first.cancel_confirmed(order) is False
    restarted = _unstarted_adapter(tmp_path, exchange)
    restarted.preflight()
    assert restarted.state is not None
    assert restarted.state.kill_switch.active is True
    with pytest.raises(DemoAdapterError, match="kill switch"):
        _submit(restarted, side="sell", price=50_030.0)
    assert exchange.create_calls == 1


def test_websocket_disconnect_plus_rest_failure_halts_without_create(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    exchange.market_fault = "error"
    runner = DemoMatrixRunner(
        adapter=adapter,
        spec=DemoMatrixSpec(
            warmup_ticks=13,
            lifecycle_cycles=1,
            tick_interval_seconds=0.0,
            resting_seconds=0.0,
            flatten_poll_seconds=0.0,
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(DemoRunFailed):
        runner.run()
    assert exchange.create_calls == 0
    assert adapter.allow_new_orders is False


def test_websocket_duplicate_and_reordered_snapshots_are_not_made_fresh() -> None:
    gate = MarketDataGate(maximum_age_ms=1_000)
    now_ms = int(time.time() * 1000)
    valid = {
        "timestamp": now_ms,
        "bids": [[49_999.0, 1.0]],
        "asks": [[50_001.0, 1.0]],
    }
    gate.validate(valid, now_ms=now_ms)
    with pytest.raises(MarketDataSafetyError, match="duplicate"):
        gate.validate(valid, now_ms=now_ms)
    reordered = {**valid, "timestamp": now_ms - 1}
    with pytest.raises(MarketDataSafetyError, match="non-monotonic"):
        gate.validate(reordered, now_ms=now_ms)


def test_flatten_response_lost_resolves_by_client_identity_without_retry(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    assert adapter.state is not None
    adapter.state.inventory_btc = 0.01
    adapter.state.average_entry_price = 50_000.0
    adapter.state_store.save(adapter.state)
    exchange.position_btc = 0.01
    exchange.create_response_lost = True
    submitted = adapter.submit_emergency_flatten(
        position_btc=0.01,
        reference_price=50_000.0,
    )
    assert submitted is not None
    assert submitted.reduce_only is True
    assert exchange.create_calls == 1
    assert adapter.state.flatten_attempts == 1


def test_partial_flatten_reconciles_without_resubmission(tmp_path: Path) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    assert adapter.state is not None
    adapter.state.inventory_btc = 0.02
    adapter.state.average_entry_price = 50_000.0
    adapter.state_store.save(adapter.state)
    exchange.position_btc = 0.02
    exchange.flatten_fraction = 0.5
    adapter.submit_emergency_flatten(
        position_btc=0.02,
        reference_price=50_000.0,
    )
    snapshot = adapter.preflight()
    assert adapter.reconcile_flatten(snapshot) is False
    assert adapter.state.inventory_btc == pytest.approx(0.01)
    with pytest.raises(AmbiguousExchangeState, match="already submitted"):
        adapter.submit_emergency_flatten(
            position_btc=0.01,
            reference_price=50_000.0,
        )
    assert exchange.create_calls == 1


def test_persistence_failure_after_create_cancels_and_blocks_restart(
    tmp_path: Path,
) -> None:
    exchange = FaultExchange()
    adapter = _adapter(tmp_path, exchange)
    original_save = adapter.state_store.save
    save_calls = 0

    def fail_second_save(state):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise DemoStateError("injected post-create save failure")
        original_save(state)

    adapter.state_store.save = fail_second_save
    with pytest.raises(DemoStateError, match="post-create"):
        _submit(adapter)
    assert exchange.create_calls == 1
    assert exchange.cancel_calls == 1
    assert exchange.orders == {}
    restarted = _unstarted_adapter(tmp_path, exchange)
    with pytest.raises(DemoAdapterError, match="missing expected"):
        restarted.preflight()
    assert exchange.create_calls == 1
