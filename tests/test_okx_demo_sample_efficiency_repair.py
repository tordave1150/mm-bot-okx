from decimal import Decimal

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    SampleEfficiencyQuotePolicy,
    fee_aware_quote_pair,
    quote_still_valid,
)
from okx_demo_multi_session_campaign import CampaignError


def _replacement_count(
    targets: list[Decimal], *, threshold_ticks: int
) -> int:
    existing = targets[0]
    creates = 1
    for target in targets[1:]:
        if quote_still_valid(
            existing_price_usdt=existing,
            target_price_usdt=target,
            tick_size_usdt=Decimal("0.1"),
            threshold_ticks=threshold_ticks,
        ):
            continue
        existing = target
        creates += 1
    return creates


def test_successor_policy_is_fee_positive_and_keeps_risk_partition_frozen() -> None:
    policy = SampleEfficiencyQuotePolicy()
    policy.validate()
    assert policy.retention_threshold_ticks(Decimal("0")) == 10
    assert policy.retention_threshold_ticks(Decimal("0.01")) == 20
    assert policy.retention_threshold_ticks(Decimal("-0.01")) == 20
    assert policy.retention_threshold_ticks(Decimal("0.01"), draining=True) == 5
    assert policy.refresh_draining_workoff(
        observations=5, refreshes=0, inventory_btc=Decimal("0.01")
    ) is False
    assert policy.refresh_draining_workoff(
        observations=6, refreshes=0, inventory_btc=Decimal("0.01")
    ) is True
    assert policy.refresh_draining_workoff(
        observations=6, refreshes=3, inventory_btc=Decimal("0.01")
    ) is False
    assert policy.refresh_draining_workoff(
        observations=99, refreshes=0, inventory_btc=Decimal("0")
    ) is False
    assert policy.to_dict()["total_create_cap"] == 60
    assert policy.to_dict()["risk_expansion"] is False
    decision = fee_aware_quote_pair(
        best_bid_usdt=Decimal("64999.9"),
        best_ask_usdt=Decimal("65000.1"),
        quantity_btc=Decimal("0.01"),
        maker_fee_rate=policy.maker_fee_rate,
        minimum_half_spread_bps=policy.minimum_half_spread_bps,
        tick_size_usdt=Decimal("0.1"),
        safety_buffer_usdt=policy.fee_edge_safety_buffer_usdt,
    )
    assert decision.eligible is True
    assert decision.net_edge_usdt > 0


def test_bounded_retention_reduces_create_cancel_churn_without_budget_growth() -> None:
    targets = [
        Decimal("65000.0"),
        Decimal("65000.3"),
        Decimal("65000.6"),
        Decimal("65000.9"),
        Decimal("65001.2"),
        Decimal("65001.5"),
        Decimal("65001.8"),
    ]
    predecessor_creates = _replacement_count(targets, threshold_ticks=2)
    successor_creates = _replacement_count(targets, threshold_ticks=10)
    defense_creates = _replacement_count(targets, threshold_ticks=20)
    assert predecessor_creates == 7
    assert successor_creates == 2
    assert defense_creates == 1
    assert defense_creates <= successor_creates < predecessor_creates
    assert successor_creates <= 48
    assert defense_creates <= 12


def test_causal_maker_workoff_completes_without_taker_flatten() -> None:
    controller = EconomicSessionController(
        session_id="economic:sample-efficiency:p0:fixture",
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
    assert controller.bind_maker_reentry_order(
        client_order_id="maker-workoff-order",
        side="sell",
        quantity_btc=Decimal("0.01"),
        timestamp_ms=3,
    ) == ("entry",)
    controller.observe_maker_workoff(
        trade_id="entry",
        timestamp_ms=4,
        workoff_trade_id="maker-workoff-fill",
        workoff_order_id="maker-workoff-order",
        matched_quantity_btc=Decimal("0.01"),
    )
    controller.observe_markout(
        trade_id="entry", timestamp_ms=5, markout_usdt=Decimal("0.25")
    )
    evidence = controller.evidence(require_complete=True)
    assert len(evidence["causal_reentry"]) == 1
    assert evidence["causal_reentry"][0]["maker_workoff_observed"] is True
    assert evidence["causal_reentry"][0]["immediate_taker_flatten"] is False
    assert evidence["unclassified_quote_mode_ticks"] == 0


@pytest.mark.parametrize(
    "policy, message",
    [
        (
            SampleEfficiencyQuotePolicy(minimum_half_spread_bps=Decimal("2")),
            "fee positive",
        ),
        (
            SampleEfficiencyQuotePolicy(balanced_retention_threshold_ticks=11),
            "balanced quote retention",
        ),
        (
            SampleEfficiencyQuotePolicy(defense_retention_threshold_ticks=21),
            "defense quote retention",
        ),
        (
            SampleEfficiencyQuotePolicy(admission_create_cap=49),
            "partition drift",
        ),
        (
            SampleEfficiencyQuotePolicy(draining_workoff_max_refreshes=4),
            "refresh bound",
        ),
    ],
)
def test_policy_drift_fails_closed(
    policy: SampleEfficiencyQuotePolicy, message: str
) -> None:
    with pytest.raises(CampaignError, match=message):
        policy.validate()


def test_inventory_above_frozen_cap_cannot_select_retention_policy() -> None:
    with pytest.raises(CampaignError, match="frozen cap"):
        SampleEfficiencyQuotePolicy().retention_threshold_ticks(Decimal("0.011"))
