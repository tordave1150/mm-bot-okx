from decimal import Decimal
from types import SimpleNamespace

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    SampleEfficiencyQuotePolicy,
    SessionPhase,
)
from okx_demo_multi_session_campaign import CampaignError
from okx_demo_soak_executor import BoundedSoakExecutor


def _partial_controller() -> EconomicSessionController:
    controller = EconomicSessionController(
        session_id="economic:special-closure:p0:fixture",
        source_sha256="a" * 64,
    )
    controller.observe_fill(
        trade_id="entry",
        side="buy",
        timestamp_ms=1,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.01"),
        fill_order_id="entry-order",
        fill_quantity_btc=Decimal("0.01"),
        fill_price_usdt=Decimal("65000"),
    )
    controller.observe_inventory_defense(trade_id="entry", timestamp_ms=2)
    controller.observe_maker_reentry(
        trade_id="entry",
        timestamp_ms=3,
        client_order_id="exit-order",
        side="sell",
        quantity_btc=Decimal("0.01"),
    )
    controller.observe_maker_workoff(
        trade_id="entry",
        timestamp_ms=4,
        workoff_trade_id="partial-exit",
        workoff_order_id="exit-order",
        matched_quantity_btc=Decimal("0.004"),
    )
    controller.observe_markout(
        trade_id="entry", timestamp_ms=5, markout_usdt=Decimal("0.25")
    )
    return controller


def test_special_flatten_closes_partial_remainder_without_causal_credit() -> None:
    controller = _partial_controller()
    closed = controller.close_with_special_flatten(
        timestamp_ms=6,
        inventory_before_btc=Decimal("0.006"),
        flatten_quantity_btc=Decimal("0.006"),
    )
    assert closed == ("entry",)
    assert controller.pending_fills == {}
    assert controller.completed_fills == {}
    controller.finish(timestamp_ms=7)
    evidence = controller.evidence(require_complete=True)
    assert evidence["causal_reentry"] == []
    assert evidence["pending_causal_fills"] == []
    row = evidence["special_closed_causal_fills"][0]
    assert row["maker_workoff_observed"] is False
    assert row["workoff_timestamp_ms"] == 0
    assert row["immediate_taker_flatten"] is True
    assert controller.events[-2]["event"] == "SPECIAL_FLATTEN_CAUSAL_CLOSURE"
    assert controller.events[-2]["payload"]["maker_workoff_credit"] is False


def test_special_flatten_three_ledger_quantity_mismatch_fails_closed() -> None:
    controller = _partial_controller()
    with pytest.raises(CampaignError, match="does not reconcile"):
        controller.close_with_special_flatten(
            timestamp_ms=6,
            inventory_before_btc=Decimal("0.005"),
            flatten_quantity_btc=Decimal("0.005"),
        )


def test_alternating_cross_zero_fills_net_to_special_closure_inventory() -> None:
    """Reproduce R2 Session 1's five alternating normal maker fills."""
    controller = EconomicSessionController(
        session_id="economic:session1-special-closure:p0:fixture",
        source_sha256="a" * 64,
    )
    inventory = Decimal("0")
    rows = (
        ("fill-1", "sell", Decimal("0.0055")),
        ("fill-2", "buy", Decimal("0.010")),
        ("fill-3", "sell", Decimal("0.010")),
        ("fill-4", "buy", Decimal("0.010")),
        ("fill-5", "sell", Decimal("0.010")),
    )
    for index, (trade_id, side, quantity) in enumerate(rows, start=1):
        after = inventory + (quantity if side == "buy" else -quantity)
        timestamp = index * 10
        controller.observe_fill(
            trade_id=trade_id,
            side=side,
            timestamp_ms=timestamp,
            inventory_before_btc=inventory,
            inventory_after_btc=after,
            fill_order_id=f"order-{index}",
            fill_quantity_btc=quantity,
            fill_price_usdt=Decimal("78000"),
        )
        controller.observe_inventory_defense(
            trade_id=trade_id,
            timestamp_ms=timestamp + 1,
        )
        controller.observe_markout(
            trade_id=trade_id,
            timestamp_ms=timestamp + 2,
            markout_usdt=Decimal("0.01"),
        )
        inventory = after

    assert inventory == Decimal("-0.0055")
    candidates = [
        item for item in (*controller.pending_fills.values(), *controller.completed_fills.values())
        if item.remaining_workoff_btc > 0
    ]
    causal_inventory = sum(
        (item.remaining_workoff_btc if item.fill_side == "buy" else -item.remaining_workoff_btc for item in candidates),
        Decimal("0"),
    )
    assert causal_inventory == inventory
    assert controller.close_with_special_flatten(
        timestamp_ms=100,
        inventory_before_btc=inventory,
        flatten_quantity_btc=abs(inventory),
    ) == ("fill-5",)
    controller.finish(timestamp_ms=101)
    evidence = controller.evidence(require_complete=True)
    assert evidence["pending_causal_fills"] == []
    assert [row["trade_id"] for row in evidence["special_closed_causal_fills"]] == [
        "fill-5"
    ]


def test_fill_inventory_transition_conflict_fails_closed() -> None:
    controller = EconomicSessionController(
        session_id="economic:session1-transition-conflict:p0:fixture",
        source_sha256="a" * 64,
    )
    with pytest.raises(CampaignError, match="transition does not reconcile"):
        controller.observe_fill(
            trade_id="conflict",
            side="buy",
            timestamp_ms=1,
            inventory_before_btc=Decimal("0"),
            inventory_after_btc=Decimal("0.005"),
            fill_order_id="conflict-order",
            fill_quantity_btc=Decimal("0.01"),
            fill_price_usdt=Decimal("78000"),
        )


def test_timeboxed_draining_preserves_budget_and_refreshes_workoff_quotes() -> None:
    controller = EconomicSessionController(
        session_id="economic:timebox:p0:fixture", source_sha256="b" * 64
    )
    controller.enter_draining(
        timestamp_ms=1, normal_creates=20, timebox_expiring=True
    )
    assert controller.phase is SessionPhase.DRAINING
    policy = SampleEfficiencyQuotePolicy()
    assert policy.retention_threshold_ticks(Decimal("0.01")) == 20
    assert policy.retention_threshold_ticks(
        Decimal("0.01"), draining=True
    ) == 5
    assert policy.to_dict()["total_create_cap"] == 60


def test_account_engine_lag_gets_bounded_read_convergence() -> None:
    driver = object.__new__(BoundedSoakExecutor)
    driver.package = SimpleNamespace(
        spec={"risk_budget": {"read_retry_attempts": 3}}
    )
    ledger = SimpleNamespace(inventory_btc=Decimal("0"))
    driver.engine = SimpleNamespace(state=SimpleNamespace(ledger=ledger))
    account = SimpleNamespace(position_btc=Decimal("0.01"))
    calls = {"ingest": 0, "reads": 0}

    def ingest() -> int:
        calls["ingest"] += 1
        if calls["ingest"] == 2:
            ledger.inventory_btc = Decimal("0.01")
        return int(calls["ingest"] == 2)

    driver._ingest_trades = ingest
    driver._reconcile_economic_fill_latch = lambda current: current
    driver._commit_controller = lambda *args, **kwargs: None
    driver.sleep = lambda _: None
    driver.gateway = SimpleNamespace(fetch_account=lambda: account)

    def read(*args, **kwargs):
        calls["reads"] += 1
        return account

    driver._read = read
    result = driver._reconcile_account_engine_inventory(account)
    assert result is account
    assert calls == {"ingest": 2, "reads": 1}
