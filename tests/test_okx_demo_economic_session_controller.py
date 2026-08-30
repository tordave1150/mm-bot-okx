from __future__ import annotations

from decimal import Decimal

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    QuoteMode,
)
from okx_demo_multi_session_campaign import CampaignError


def _controller() -> EconomicSessionController:
    return EconomicSessionController(
        session_id="economic:economic-fixture:p0:controller",
        source_sha256="a" * 64,
    )


def test_every_tick_including_hard_kill_is_classified_and_reconciles() -> None:
    controller = _controller()
    assert controller.classify_tick(
        timestamp_ms=1, inventory_btc=Decimal("0")
    ) is QuoteMode.BALANCED_TWO_SIDED
    assert controller.classify_tick(
        timestamp_ms=2, inventory_btc=Decimal("0.01")
    ) is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert controller.classify_tick(
        timestamp_ms=3, inventory_btc=Decimal("-0.01")
    ) is QuoteMode.ONE_SIDED_BUY_DEFENSE
    assert controller.classify_tick(
        timestamp_ms=4,
        inventory_btc=Decimal("0"),
        market_gate_open=False,
    ) is QuoteMode.PLACEMENT_BLOCKED
    assert controller.classify_tick(
        timestamp_ms=5,
        inventory_btc=Decimal("0.01"),
        hard_kill=True,
    ) is QuoteMode.HARD_KILL_CONTINUATION
    evidence = controller.evidence()
    assert evidence["quote_mode_ticks"] == 5
    assert sum(evidence["quote_mode_counters"].values()) == 5
    assert evidence["unclassified_quote_mode_ticks"] == 0


def test_fill_defense_maker_reentry_workoff_and_markout_form_causal_chain() -> None:
    controller = _controller()
    controller.observe_fill(
        trade_id="bid-fill",
        side="buy",
        timestamp_ms=10,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.01"),
    )
    controller.observe_inventory_defense(trade_id="bid-fill", timestamp_ms=11)
    controller.observe_maker_reentry(trade_id="bid-fill", timestamp_ms=12)
    assert controller.classify_tick(
        timestamp_ms=13, inventory_btc=Decimal("0.01")
    ) is QuoteMode.ONE_SIDED_SELL_DEFENSE
    controller.observe_markout(
        trade_id="bid-fill", timestamp_ms=14, markout_usdt=Decimal("0.02")
    )
    controller.observe_fill(
        trade_id="ask-fill",
        side="sell",
        timestamp_ms=20,
        inventory_before_btc=Decimal("0.01"),
        inventory_after_btc=Decimal("0"),
    )
    controller.observe_inventory_defense(trade_id="ask-fill", timestamp_ms=21)
    controller.observe_maker_reentry(trade_id="ask-fill", timestamp_ms=22)
    controller.observe_markout(
        trade_id="ask-fill", timestamp_ms=23, markout_usdt=Decimal("0.03")
    )
    evidence = controller.evidence()
    by_trade = {row["trade_id"]: row for row in evidence["causal_reentry"]}
    assert by_trade["bid-fill"]["maker_reentry_observed"] is True
    assert by_trade["bid-fill"]["maker_workoff_observed"] is True
    assert by_trade["ask-fill"]["maker_reentry_observed"] is True
    assert evidence["markouts_usdt"] == ["0.02", "0.03"]


def test_pending_fill_cannot_be_exported_as_complete_economic_evidence() -> None:
    controller = _controller()
    controller.observe_fill(
        trade_id="fill",
        side="buy",
        timestamp_ms=10,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.01"),
    )
    controller.observe_inventory_defense(trade_id="fill", timestamp_ms=11)
    with pytest.raises(CampaignError, match="incomplete"):
        controller.evidence()


def test_immediate_taker_flatten_never_satisfies_causal_evidence() -> None:
    controller = _controller()
    with pytest.raises(CampaignError, match="not authorized"):
        controller.authorize_taker_flatten(reason="NORMAL_WORKOFF", timestamp_ms=1)
    controller.authorize_taker_flatten(reason="SHUTDOWN", timestamp_ms=2)
    assert controller.events[-1]["payload"]["normal_economic_evidence"] is False


def test_event_and_snapshot_hashes_fail_closed_on_corruption() -> None:
    controller = _controller()
    controller.classify_tick(timestamp_ms=1, inventory_btc=Decimal("0"))
    snapshot = controller.snapshot()
    restored = EconomicSessionController.restore(snapshot)
    assert restored.tail_sha256 == controller.tail_sha256
    assert restored.quote_mode_ticks == 1

    corrupted = controller.snapshot()
    corrupted["quote_mode_ticks"] = 2
    with pytest.raises(CampaignError, match="snapshot seal"):
        EconomicSessionController.restore(corrupted)

    chain = controller.snapshot()
    chain["events"][0]["payload"]["quote_mode"] = "DRIFT"
    chain_payload = {key: value for key, value in chain.items() if key != "snapshot_sha256"}
    from okx_fill_restart_validation import canonical_sha256

    chain["snapshot_sha256"] = canonical_sha256(chain_payload)
    with pytest.raises(CampaignError, match="event chain"):
        EconomicSessionController.restore(chain)


def test_timestamp_inventory_and_event_ordering_are_fail_closed() -> None:
    controller = _controller()
    controller.classify_tick(timestamp_ms=10, inventory_btc=Decimal("0"))
    with pytest.raises(CampaignError, match="timestamp regression"):
        controller.classify_tick(timestamp_ms=9, inventory_btc=Decimal("0"))
    with pytest.raises(CampaignError, match="inventory exceeds"):
        controller.classify_tick(timestamp_ms=11, inventory_btc=Decimal("0.02"))

    second = _controller()
    second.observe_fill(
        trade_id="fill",
        side="buy",
        timestamp_ms=10,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.01"),
    )
    with pytest.raises(CampaignError, match="precedes fill"):
        second.observe_inventory_defense(trade_id="fill", timestamp_ms=9)


def test_duplicate_fill_and_conflicting_markout_are_rejected() -> None:
    controller = _controller()
    controller.observe_fill(
        trade_id="fill",
        side="buy",
        timestamp_ms=10,
        inventory_before_btc=Decimal("0"),
        inventory_after_btc=Decimal("0.01"),
    )
    with pytest.raises(CampaignError, match="duplicated"):
        controller.observe_fill(
            trade_id="fill",
            side="buy",
            timestamp_ms=11,
            inventory_before_btc=Decimal("0"),
            inventory_after_btc=Decimal("0.01"),
        )
    controller.observe_inventory_defense(trade_id="fill", timestamp_ms=12)
    controller.observe_maker_reentry(trade_id="fill", timestamp_ms=13)
    controller.observe_markout(
        trade_id="fill", timestamp_ms=14, markout_usdt=Decimal("0.01")
    )
    with pytest.raises(CampaignError, match="markout changed"):
        controller.observe_markout(
            trade_id="fill", timestamp_ms=15, markout_usdt=Decimal("0.02")
        )
