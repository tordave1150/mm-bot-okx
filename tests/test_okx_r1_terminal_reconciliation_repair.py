import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import okx_fill_restart_executor
from okx_fill_restart_executor import (
    FormalExecutionDriver,
    FormalExecutionError,
    FrozenPackage,
    HashChainStream,
)
from okx_fill_restart_formal import (
    FormalActionType,
    FormalOrchestrator,
    FormalSafetyError,
    FormalStage,
    FormalStateStore,
    make_fixture_observation,
    make_fixture_quote,
    make_fixture_spec,
)
from okx_fill_restart_gateway import GatewayAccountSnapshot
from okx_fill_restart_validation import (
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    RiskBudget,
    RunPhase,
    RuntimeBinding,
)


def _pair(spec, start: int = 1):
    return tuple(
        make_fixture_observation(
            spec,
            sequence=start + index,
            observed_at_ms=(start + index) * 1_000,
            market_timestamp_ms=(start + index) * 1_000,
        )
        for index in range(2)
    )


def _armed_one_buy(tmp_path: Path):
    spec = make_fixture_spec()
    controller = FormalOrchestrator.prepare(
        spec, FormalStateStore(tmp_path / "formal.json")
    )
    controller.arm_and_start(
        arm_token=spec.expected_arm_token,
        snapshots=_pair(spec),
        now_ms=100,
    )
    initial = _pair(spec)[1]
    quote = make_fixture_quote(spec, initial)
    quote = type(quote)(
        **{
            **quote.__dict__,
            "ask_suppressed": True,
        }
    )
    action = controller.plan_quotes(
        observation=initial, quote=quote, now_ms=3_000
    )[0]
    client_id = str(action.payload["client_order_id"])
    controller.record_create_dispatch(client_order_id=client_id, now_ms=3_000)
    controller.record_post_only_ack(
        client_order_id=client_id,
        side="buy",
        post_only_confirmed=True,
        now_ms=3_000,
    )
    return spec, controller, client_id


def _fill_observation(spec, *, side: str, open_orders=()):
    buy = side == "buy"
    return make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        position_btc="0.01" if buy else "-0.01",
        average_entry_usdt="50000",
        open_owned_orders=open_orders,
        normal_bid_fills_total=1 if buy else 0,
        normal_ask_fills_total=0 if buy else 1,
        fill_cursor_timestamp_ms=3_000,
        fill_cursor_trade_id=f"trade-{side}",
        fill_deduplication_sha256="9" * 64,
        actual_fees_usdt="0.10",
        net_realized_pnl_usdt="-0.10",
    )


def test_filled_order_is_removed_before_r1_checkpoint_action(tmp_path: Path) -> None:
    spec, controller, client_id = _armed_one_buy(tmp_path)
    actions = controller.observe(_fill_observation(spec, side="buy"), now_ms=4_000)
    assert [action.action for action in actions] == [
        FormalActionType.PERSIST_R1_CHECKPOINT
    ]
    assert client_id not in controller.state.owned_orders
    controller.validate_r1_checkpoint_ready()


def test_wrong_side_or_side_drift_cannot_clear_controller_ownership(
    tmp_path: Path,
) -> None:
    spec, controller, _client_id = _armed_one_buy(tmp_path / "wrong-fill")
    actions = controller.observe(_fill_observation(spec, side="sell"), now_ms=4_000)
    assert actions[-1].action is FormalActionType.HALT
    assert (
        controller.state.halted_reason
        == "MISSING_OWNED_ORDER_NOT_EXPLAINED_BY_FILL"
    )

    spec, controller, client_id = _armed_one_buy(tmp_path / "side-drift")
    observation = make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        open_owned_orders=((client_id, "sell"),),
    )
    actions = controller.observe(observation, now_ms=4_000)
    assert actions[-1].action is FormalActionType.HALT
    assert controller.state.halted_reason == "AUTHORITATIVE_OWNED_ORDER_SIDE_MISMATCH"


def test_r1_controller_rejection_precedes_engine_checkpoint_and_flatten(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class RejectingController:
        state = SimpleNamespace(process_generation=0)

        @staticmethod
        def validate_r1_checkpoint_ready() -> None:
            calls.append("controller_validate")
            raise FormalSafetyError("fixture controller rejection")

        @staticmethod
        def persist_r1_checkpoint(checkpoint_id: str) -> None:
            calls.append("controller_persist")

    class Engine:
        state = SimpleNamespace(
            ledger=SimpleNamespace(),
            phase=RunPhase.RUNNING,
        )

        @staticmethod
        def validate_checkpoint_after_actual_fill() -> None:
            calls.append("engine_validate")

        @staticmethod
        def checkpoint_after_actual_fill() -> None:
            calls.append("engine_checkpoint")

    driver = object.__new__(FormalExecutionDriver)
    driver.controller = RejectingController()
    driver.engine = Engine()
    driver.package = SimpleNamespace(output=tmp_path)
    driver.gateway = SimpleNamespace(flatten_dispatches=0)
    with pytest.raises(FormalSafetyError, match="fixture controller"):
        driver._checkpoint_r1()
    assert calls == ["controller_validate"]
    assert driver.engine.state.phase is RunPhase.RUNNING
    assert not (tmp_path / "formal_run" / "restart" / "R1_handoff.json").exists()
    assert driver.gateway.flatten_dispatches == 0


class AuditGateway:
    def __init__(self) -> None:
        self.live_endpoint_attempts = 0
        self.mutation_calls = ["create_order", "create_order"]
        self.flatten_dispatches = 1

    def public_audit(self) -> dict[str, object]:
        return {
            "sandbox_mode": True,
            "simulated_trading_header": True,
            "hostname": "www.okx.com",
            "read_call_count": 4,
            "read_methods": ["fetch_account"],
            "mutation_call_count": 2,
            "mutation_methods": ["create_order"],
            "mutation_method_counts": {
                "create_order": 2,
                "cancel_order": 0,
            },
            "flatten_dispatches": 1,
            "fill_history_queries": 1,
            "fill_recent_tail_queries": 1,
            "fill_union_duplicates": 0,
            "fill_union_conflicts": 0,
            "last_fill_union_audit": {},
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        }


def _account(binding: str, *, position: str = "0") -> GatewayAccountSnapshot:
    return GatewayAccountSnapshot(
        account_binding=binding,
        permissions=("read_only", "trade"),
        position_mode="net_mode",
        leverage=Decimal("3"),
        total_equity_usdt=Decimal("750"),
        free_equity_usdt=Decimal("750"),
        position_btc=Decimal(position),
        average_entry_usdt=(
            Decimal("0") if position == "0" else Decimal("50000")
        ),
        maintenance_margin_usdt=Decimal("0"),
        open_orders=(),
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.0005"),
        clock_skew_ms=25,
    )


def _terminal_driver(tmp_path: Path) -> tuple[FormalExecutionDriver, str]:
    spec = make_fixture_spec(
        formal_run_id="formal-r1-terminal-fixture",
        package_id="formal-package-r1-terminal-fixture",
    )
    binding_value = "fixture-account-binding"
    binding = RuntimeBinding(
        run_id=spec.formal_run_id,
        execution_mode="OKX_DEMO",
        account_binding=binding_value,
        market_fingerprint=spec.market_fingerprint,
        profile_binding_sha256=spec.profile_binding_sha256,
        runtime_configuration_sha256=spec.runtime_configuration_sha256,
        source_manifest_sha256=spec.source_manifest_sha256,
    )
    controller = FormalOrchestrator.prepare(
        spec, FormalStateStore(tmp_path / "controller.json")
    )
    controller.state.stage = FormalStage.HALTED
    controller.state.halted_reason = "fixture"
    controller.state.account_binding = binding_value
    controller.state.position_btc = Decimal("0.01")
    controller.state.average_entry_usdt = Decimal("50000")
    controller.state.client_order_generation = 1
    controller.state.normal_create_count = 1
    controller.state.normal_bid_fills_total = 1
    controller.state.actual_fees_usdt = Decimal("0.10")
    controller.state.net_realized_pnl_usdt = Decimal("-0.10")
    controller.state.owned_orders = {"normal-buy": "buy"}

    engine = FillRestartEngine.create(
        store=HashChainStateStore(tmp_path / "validation.json"),
        binding=binding,
        risk_budget=RiskBudget(),
    )
    normal = OwnedOrder(
        client_order_id="normal-buy",
        order_id="normal-order",
        side="buy",
        quantity_btc=Decimal("0.01"),
        remaining_btc=Decimal("0"),
        reduce_only=False,
        post_only_acknowledged=True,
        status="FILLED",
    )
    flatten = OwnedOrder(
        client_order_id="flatten-sell",
        order_id="flatten-order",
        side="sell",
        quantity_btc=Decimal("0.01"),
        remaining_btc=Decimal("0"),
        reduce_only=True,
        post_only_acknowledged=False,
        status="FILLED",
    )
    engine.state.owned_orders = {
        normal.client_order_id: normal,
        flatten.client_order_id: flatten,
    }
    engine.state.normal_acknowledgements = 1
    engine.state.last_reconciled_position_btc = Decimal("0")
    ledger = engine.state.ledger
    ledger.inventory_btc = Decimal("0")
    ledger.average_entry_price = Decimal("0")
    ledger.normal_bid_fills = 1
    ledger.special_fill_count = 2
    ledger.normal_gross_realized_pnl_usdt = Decimal("0")
    ledger.normal_fees_usdt = Decimal("0.10")
    ledger.normal_net_realized_pnl_usdt = Decimal("-0.10")
    ledger.special_gross_realized_pnl_usdt = Decimal("0.20")
    ledger.special_fees_usdt = Decimal("0.15")
    ledger.special_net_realized_pnl_usdt = Decimal("0.05")
    ledger.gross_realized_pnl_usdt = Decimal("0.20")
    ledger.total_fees_usdt = Decimal("0.25")
    ledger.net_realized_pnl_usdt = Decimal("-0.05")

    package = FrozenPackage(
        root=tmp_path,
        output=tmp_path / "package",
        package_id=spec.package_id,
        spec=spec,
        source_hashes={},
    )
    driver = object.__new__(FormalExecutionDriver)
    driver.package = package
    driver.spec = spec
    driver.gateway = AuditGateway()
    driver.controller = controller
    driver.engine = engine
    driver.streams = {
        name: HashChainStream(
            package.output / "formal_run" / "streams" / f"{name}.jsonl"
        )
        for name in ("market", "order", "gateway_audit")
    }
    driver.streams["order"].append({
        "event": "NORMAL_CREATE_RESOLVED",
        "client_order_id": "normal-buy",
    })
    return driver, binding_value


def test_terminal_close_reconciles_stale_controller_and_historical_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, binding = _terminal_driver(tmp_path)
    monkeypatch.setattr(
        okx_fill_restart_executor,
        "_credential_config",
        lambda: SimpleNamespace(api_key="", api_secret="", api_passphrase=""),
    )
    result = driver.close(
        "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        "fixture post-fill failure",
        account_only_snapshots=(_account(binding), _account(binding)),
    )
    assert result["final_position_btc"] == "0"
    assert result["final_open_order_count"] == 0
    assert result["special_fill_count"] == 2
    assert driver.controller.state.owned_orders == {}
    assert driver.controller.state.position_btc == 0
    assert len(driver.engine.state.owned_orders) == 2
    assert driver.engine.state.open_orders == {}
    evidence = json.loads((
        driver.package.output / "formal_run" / "terminal"
        / "durable_reconciliation.json"
    ).read_text(encoding="utf-8"))
    assert evidence["historical_closed_orders_retained"] is True
    assert evidence["controller_closed_ownership_cleared"] == ["normal-buy"]
    assert evidence["flatten_dispatches"] == 1
    assert evidence["flatten_fill_parts"] == 2
    assert (driver.package.output / "COMPLETED.json").is_file()


def test_terminal_reconciliation_rejects_open_engine_or_unknown_controller_order(
    tmp_path: Path,
) -> None:
    driver, binding = _terminal_driver(tmp_path / "open")
    driver.engine.state.owned_orders["normal-buy"].status = "ACKNOWLEDGED"
    driver.engine.state.owned_orders["normal-buy"].remaining_btc = Decimal("0.01")
    with pytest.raises(FormalExecutionError, match="durable state"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account(binding)),
        )

    driver, binding = _terminal_driver(tmp_path / "unknown")
    driver.controller.state.owned_orders = {"unknown": "buy"}
    with pytest.raises(FormalExecutionError, match="unexplained"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account(binding)),
        )


def test_terminal_evidence_write_failure_creates_no_new_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, binding = _terminal_driver(tmp_path)
    baseline = list(driver.gateway.mutation_calls)
    original = okx_fill_restart_executor._write_new_json

    def fail_reconciliation(path: Path, value: object) -> None:
        if path.name == "durable_reconciliation.json":
            raise FormalExecutionError("fixture terminal evidence write failure")
        original(path, value)

    monkeypatch.setattr(
        okx_fill_restart_executor, "_write_new_json", fail_reconciliation
    )
    with pytest.raises(FormalExecutionError, match="evidence write failure"):
        driver.close(
            "FAILED", "fixture",
            account_only_snapshots=(_account(binding), _account(binding)),
        )
    assert driver.gateway.mutation_calls == baseline


def test_fail_closed_uses_two_post_flatten_snapshots(tmp_path: Path) -> None:
    binding = "binding"
    accounts = [
        _account(binding, position="0.01"),
        _account(binding),
        _account(binding),
    ]

    class Gateway:
        live_endpoint_attempts = 0
        flatten_dispatches = 0

        @staticmethod
        def fetch_account() -> GatewayAccountSnapshot:
            return accounts.pop(0)

    driver = object.__new__(FormalExecutionDriver)
    driver.package = SimpleNamespace(output=tmp_path)
    driver.gateway = Gateway()
    driver.engine = SimpleNamespace(state=SimpleNamespace(
        open_orders={},
        ledger=SimpleNamespace(inventory_btc=Decimal("0.01")),
    ))
    controller_state = SimpleNamespace(stage=FormalStage.SEEK_R1_FILL)

    class Controller:
        state = controller_state

        @staticmethod
        def _halt(reason: str) -> None:
            controller_state.stage = FormalStage.HALTED

    driver.controller = Controller()
    driver.streams = {"safety": HashChainStream(tmp_path / "safety.jsonl")}

    def flatten(position: Decimal) -> None:
        assert position == Decimal("0.01")
        driver.gateway.flatten_dispatches = 1
        driver.engine.state.ledger.inventory_btc = Decimal("0")

    captured: dict[str, object] = {}

    def close(status: str, reason: str, *, account_only_snapshots):
        captured["snapshots"] = account_only_snapshots
        return {"status": status, "reason": reason}

    driver._flatten = flatten
    driver.close = close
    result = driver.fail_closed(FormalSafetyError("fixture"))
    assert result["status"] == "OKX_DEMO_FILL_RESTART_SAFETY_FAILED"
    first, second = captured["snapshots"]
    assert first.position_btc == 0 and second.position_btc == 0
    assert accounts == []
