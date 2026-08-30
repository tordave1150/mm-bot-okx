from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from okx_fill_restart_executor import FormalExecutionDriver, FormalExecutionError
from okx_fill_restart_formal import (
    FormalAction,
    FormalActionType,
    FormalOrchestrator,
    FormalStateStore,
    make_fixture_observation,
    make_fixture_quote,
    make_fixture_spec,
)


def _armed_at_budget(tmp_path: Path, position: Decimal):
    spec = make_fixture_spec(
        formal_run_id=f"formal-budget-{str(position).replace('-', 'n')}",
        package_id=f"formal-package-budget-{str(position).replace('-', 'n')}",
    )
    store = FormalStateStore(tmp_path / "formal_state.json")
    controller = FormalOrchestrator.prepare(spec, store)
    first = make_fixture_observation(
        spec, sequence=1, observed_at_ms=1_000, market_timestamp_ms=1_000
    )
    second = make_fixture_observation(
        spec, sequence=2, observed_at_ms=2_000, market_timestamp_ms=2_000
    )
    controller.arm_and_start(
        arm_token=spec.expected_arm_token,
        snapshots=(first, second),
        now_ms=100,
    )
    controller.state.normal_create_count = spec.maximum_normal_creates
    controller.state.position_btc = position
    controller._save()
    observation = make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        position_btc=position,
        average_entry_usdt="50000",
        normal_bid_fills_total=1 if position > 0 else 0,
        normal_ask_fills_total=1 if position < 0 else 0,
        fill_cursor_timestamp_ms=3_000,
        fill_cursor_trade_id="budget-fill",
        fill_deduplication_sha256="3" * 64,
        actual_fees_usdt="0.01",
        net_realized_pnl_usdt="-0.01",
    )
    return spec, controller, observation


@pytest.mark.parametrize(
    ("position", "flatten_side"),
    ((Decimal("0.01"), "sell"), (Decimal("-0.01"), "buy")),
)
def test_budget_exhaustion_emits_flatten_before_halt(
    tmp_path: Path, position: Decimal, flatten_side: str
) -> None:
    spec, controller, observation = _armed_at_budget(tmp_path, position)
    actions = controller.plan_quotes(
        observation=observation,
        quote=make_fixture_quote(spec, observation),
        now_ms=4_000,
    )
    assert [item.action for item in actions] == [
        FormalActionType.FLATTEN_REDUCE_ONLY,
        FormalActionType.HALT,
    ]
    assert actions[0].payload["side"] == flatten_side
    assert actions[0].payload["quantity_btc"] == "0.01"
    assert actions[1].payload["reason"] == "NORMAL_CREATE_BUDGET_EXHAUSTED"
    assert controller.state.flatten_submission_count == 1


@pytest.mark.parametrize("position", (Decimal("0.01"), Decimal("-0.01")))
def test_executor_halt_fallback_cancels_flattens_reconciles_then_closes(
    position: Decimal,
) -> None:
    driver = object.__new__(FormalExecutionDriver)
    events: list[str] = []
    current = {"position": position}
    ledger = SimpleNamespace(inventory_btc=position)
    state = SimpleNamespace(open_orders={"owned": object()}, ledger=ledger)
    driver.engine = SimpleNamespace(state=state)

    def cancel_and_reconcile() -> None:
        events.append("cancel")
        state.open_orders.clear()

    def collect_until_reconciled():
        events.append("reconcile")
        return SimpleNamespace(
            account=SimpleNamespace(
                open_orders=(), position_btc=current["position"]
            )
        )

    def flatten(value: Decimal) -> None:
        events.append(f"flatten:{value}")
        current["position"] = Decimal("0")
        ledger.inventory_btc = Decimal("0")

    def close(status: str, reason: str) -> dict[str, object]:
        events.append("close")
        return {"status": status, "reason": reason}

    driver.cancel_and_reconcile = cancel_and_reconcile
    driver.collect_until_reconciled = collect_until_reconciled
    driver._flatten = flatten
    driver.close = close
    result = driver.handle_actions((FormalAction(
        FormalActionType.HALT,
        {"reason": "NORMAL_CREATE_BUDGET_EXHAUSTED"},
    ),))
    assert events == [
        "cancel",
        "reconcile",
        f"flatten:{position}",
        "reconcile",
        "close",
    ]
    assert result == {
        "status": "OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT",
        "reason": "NORMAL_CREATE_BUDGET_EXHAUSTED",
    }


def test_activity_close_position_mismatch_fails_before_flatten() -> None:
    driver = object.__new__(FormalExecutionDriver)
    driver.engine = SimpleNamespace(
        state=SimpleNamespace(
            open_orders={}, ledger=SimpleNamespace(inventory_btc=Decimal("-0.01"))
        )
    )
    driver.collect_until_reconciled = lambda: SimpleNamespace(
        account=SimpleNamespace(open_orders=(), position_btc=Decimal("0.01"))
    )
    driver._flatten = lambda value: pytest.fail("flatten must not dispatch")
    with pytest.raises(FormalExecutionError, match="position/accounting mismatch"):
        driver._close_activity_insufficient("NORMAL_CREATE_BUDGET_EXHAUSTED")
