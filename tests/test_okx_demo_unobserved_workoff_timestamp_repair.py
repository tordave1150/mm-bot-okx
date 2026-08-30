from decimal import Decimal

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    PendingCausalFill,
)
from okx_demo_multi_session_campaign import CampaignError


def _controller() -> EconomicSessionController:
    controller = EconomicSessionController(
        session_id="economic:workoff-repair:p0:fixture",
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
    controller.bind_maker_reentry_order(
        client_order_id="exit-order",
        side="sell",
        quantity_btc=Decimal("0.01"),
        timestamp_ms=3,
    )
    return controller


def test_partial_workoff_has_no_completion_timestamp_and_serializes() -> None:
    controller = _controller()
    controller.observe_maker_workoff(
        trade_id="entry",
        timestamp_ms=4,
        workoff_trade_id="exit-part-1",
        workoff_order_id="exit-order",
        matched_quantity_btc=Decimal("0.004"),
    )
    row = controller.evidence(require_complete=False)["causal_reentry"][0]
    assert row["maker_workoff_observed"] is False
    assert row["workoff_timestamp_ms"] == 0
    assert row["causal_binding"]["remaining_workoff_btc"] == "0.006"
    assert controller.events[-1]["event"] == "MAKER_WORKOFF_PARTIAL_OBSERVED"


def test_partial_workoff_survives_restart_then_completion_sets_timestamp() -> None:
    controller = _controller()
    controller.observe_maker_workoff(
        trade_id="entry",
        timestamp_ms=4,
        workoff_trade_id="exit-part-1",
        workoff_order_id="exit-order",
        matched_quantity_btc=Decimal("0.004"),
    )
    resumed = EconomicSessionController.restore(controller.snapshot())
    assert resumed.completed_fills["entry"].workoff_timestamp_ms == 0
    resumed.observe_maker_workoff(
        trade_id="entry",
        timestamp_ms=5,
        workoff_trade_id="exit-part-2",
        workoff_order_id="exit-order",
        matched_quantity_btc=Decimal("0.006"),
    )
    row = resumed.evidence(require_complete=False)["causal_reentry"][0]
    assert row["maker_workoff_observed"] is True
    assert row["workoff_timestamp_ms"] == 5
    assert row["causal_binding"]["workoff_trade_ids"] == [
        "exit-part-1", "exit-part-2"
    ]


def test_legacy_ambiguous_unobserved_timestamp_fails_closed_on_load() -> None:
    row = _controller().completed_fills["entry"].to_dict()
    row["workoff_timestamp_ms"] = 4
    with pytest.raises(CampaignError, match="unobserved maker work-off state"):
        PendingCausalFill.from_dict(row)
