from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    SessionPhase,
    fee_aware_quote_pair,
    quote_still_valid,
)
from okx_demo_multi_session_a0_offline import verify_failed_a2_predecessor
from okx_demo_multi_session_campaign import CampaignError
from okx_fill_restart_validation import FillEvent, FillLedger, OwnedOrder


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _controller() -> EconomicSessionController:
    return EconomicSessionController(
        session_id="economic:economic-repair:p0:fixture",
        source_sha256="a" * 64,
    )


def _order(side: str, suffix: str, quantity: str = "0.01") -> OwnedOrder:
    return OwnedOrder(
        client_order_id=f"client-{side}-{suffix}",
        order_id=f"order-{side}-{suffix}",
        side=side,
        quantity_btc=Decimal(quantity),
        remaining_btc=Decimal(quantity),
        reduce_only=False,
        post_only_acknowledged=True,
    )


def _fill(
    order: OwnedOrder,
    trade_id: str,
    timestamp_ms: int,
    quantity: str,
    price: str,
) -> FillEvent:
    return FillEvent(
        trade_id=trade_id,
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        timestamp_ms=timestamp_ms,
        side=order.side,
        price=Decimal(price),
        quantity_btc=Decimal(quantity),
        fee_cost=Decimal("0.01"),
        fee_currency="USDT",
        liquidity="maker",
        reduce_only=False,
    )


def test_failed_a2_predecessor_is_frozen_and_not_reusable() -> None:
    audit = verify_failed_a2_predecessor(PROJECT_ROOT)
    assert audit["passed"] is True
    assert audit["normal_creates"] == 60
    assert audit["normal_bid_fills"] == 6
    assert audit["normal_ask_fills"] == 13
    assert audit["pending_causal_fill_count"] == 1
    assert audit["historical_campaign_omitted_attempted_session"] is True
    assert audit["rerun_or_resume_allowed"] is False


def test_draining_partition_blocks_new_exposure_and_preserves_total_cap() -> None:
    controller = _controller()
    with pytest.raises(CampaignError, match="reserved budget"):
        controller.enter_draining(timestamp_ms=1, normal_creates=47)
    controller.enter_draining(timestamp_ms=2, normal_creates=48)
    assert controller.phase is SessionPhase.DRAINING
    assert controller.admission_create_cap == 48
    assert controller.workoff_create_reserve == 12
    assert controller.admission_create_cap + controller.workoff_create_reserve == 60
    controller.record_placement_reason(
        reason="DRAINING_NEW_EXPOSURE_BLOCKED", timestamp_ms=3
    )
    assert controller.placement_reason_counters[
        "DRAINING_NEW_EXPOSURE_BLOCKED"
    ] == 1


def test_reentry_order_binds_only_oldest_eligible_quantity() -> None:
    controller = _controller()
    controller.observe_fill(
        trade_id="first",
        side="buy",
        timestamp_ms=10,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.005"),
        fill_order_id="order-first",
        fill_quantity_btc=Decimal("0.005"),
        fill_price_usdt=Decimal("65000"),
    )
    controller.observe_inventory_defense(trade_id="first", timestamp_ms=11)
    controller.observe_fill(
        trade_id="second",
        side="buy",
        timestamp_ms=12,
        inventory_before_btc=Decimal("0.005"),
        inventory_after_btc=Decimal("0.010"),
        fill_order_id="order-second",
        fill_quantity_btc=Decimal("0.005"),
        fill_price_usdt=Decimal("65001"),
    )
    controller.observe_inventory_defense(trade_id="second", timestamp_ms=13)
    bound = controller.bind_maker_reentry_order(
        client_order_id="reentry-one",
        side="sell",
        quantity_btc=Decimal("0.005"),
        timestamp_ms=14,
    )
    assert bound == ("first",)
    assert controller.completed_fills["first"].reentry_client_order_id == (
        "reentry-one"
    )
    assert controller.pending_fills["second"].maker_reentry_observed is False


def test_partial_fifo_fragments_count_one_completed_lot_only() -> None:
    ledger = FillLedger()
    entry = _order("buy", "entry")
    assert ledger.apply(_fill(entry, "entry-fill", 1, "0.01", "65000"), entry)
    exit_order = _order("sell", "exit")
    assert ledger.apply(
        _fill(exit_order, "exit-part-1", 2, "0.004", "65040"),
        exit_order,
    )
    assert len(ledger.normal_fifo_match_fragments) == 1
    assert ledger.normal_round_trips == []
    assert ledger.normal_inventory_cycles == []
    assert ledger.apply(
        _fill(exit_order, "exit-part-2", 3, "0.006", "65050"),
        exit_order,
    )
    assert len(ledger.normal_fifo_match_fragments) == 2
    assert len(ledger.normal_round_trips) == 1
    assert ledger.normal_round_trips[0]["quantity_btc"] == "0.01"
    assert ledger.normal_round_trips[0]["status"] == "FULLY_CLOSED_FIFO_LOT"
    assert len(ledger.normal_inventory_cycles) == 1


def test_one_exit_fill_can_close_multiple_partial_fifo_entry_lots() -> None:
    ledger = FillLedger()
    for index in range(5):
        entry = _order("sell", f"partial-entry-{index}", "0.002")
        assert ledger.apply(
            _fill(entry, f"entry-part-{index}", index + 1, "0.002", "65000"),
            entry,
        )
    exit_order = _order("buy", "shared-exit", "0.01")
    assert ledger.apply(
        _fill(exit_order, "shared-exit-fill", 10, "0.01", "64950"),
        exit_order,
    )

    assert ledger.normal_fill_count == 6
    assert len(ledger.normal_round_trips) == 5
    assert len(ledger.normal_fifo_match_fragments) == 5
    assert {
        row["entry_fill_id"] for row in ledger.normal_round_trips
    } == {f"entry-part-{index}" for index in range(5)}
    assert {
        row["exit_fill_id"] for row in ledger.normal_round_trips
    } == {"shared-exit-fill"}
    assert sum(
        Decimal(str(row["quantity_btc"]))
        for row in ledger.normal_round_trips
    ) == Decimal("0.010")
    assert ledger.inventory_btc == 0


def test_fee_positive_prices_and_quote_retention_are_decimal_exact() -> None:
    decision = fee_aware_quote_pair(
        best_bid_usdt=Decimal("64999.9"),
        best_ask_usdt=Decimal("65000.1"),
        quantity_btc=Decimal("0.01"),
        maker_fee_rate=Decimal("0.0002"),
        minimum_half_spread_bps=Decimal("6.0"),
        tick_size_usdt=Decimal("0.1"),
        safety_buffer_usdt=Decimal("0.01"),
    )
    assert decision.eligible is True
    assert decision.net_edge_usdt > 0
    assert decision.bid_price_usdt % Decimal("0.1") == 0
    assert decision.ask_price_usdt % Decimal("0.1") == 0
    assert quote_still_valid(
        existing_price_usdt=decision.bid_price_usdt,
        target_price_usdt=decision.bid_price_usdt + Decimal("0.2"),
        tick_size_usdt=Decimal("0.1"),
        threshold_ticks=2,
    ) is True
    assert quote_still_valid(
        existing_price_usdt=decision.bid_price_usdt,
        target_price_usdt=decision.bid_price_usdt + Decimal("0.3"),
        tick_size_usdt=Decimal("0.1"),
        threshold_ticks=2,
    ) is False
