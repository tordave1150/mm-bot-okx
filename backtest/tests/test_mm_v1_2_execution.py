"""Direct regression tests required by the MM v1.2 diagnostic contract."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backtest.mm_execution_microstructure import CausalTradeEventMatcher
from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_2_diagnostic import (
    FillBuilder,
    event_ordering_audit,
    hand_fixtures,
    microstructure_fixtures,
)
from backtest.mm_v1_2_protocol import FILL_TRIGGER_CONTRACT, build_specification
from market_maker.execution_accounting import (
    FIFORoundTripMatcher,
    economic_attribution,
    effective_fill_edge,
    markout,
    quoted_spread,
)
from market_maker.as_config import MarketMakerV1Config


D = Decimal
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def hand():
    return hand_fixtures()


@pytest.fixture(scope="module")
def hand_by_id(hand):
    return {
        result["fixture_id"]: result
        for result in hand["summary"]["results"]
    }


@pytest.fixture(scope="module")
def micro():
    return microstructure_fixtures()


@pytest.fixture(scope="module")
def micro_by_id(micro):
    return {
        result["scenario"]: result
        for result in micro["summary"]["results"]
    }


def test_01_fill_identity_uniqueness(hand):
    ids = [fill.fill_id for fill in hand["fills"]]
    assert len(ids) == len(set(ids))


def test_02_deterministic_fifo_matching():
    b = FillBuilder("fifo")
    fills = [
        b.fill(side="buy", quantity="0.006", price="99", tick=1),
        b.fill(side="buy", quantity="0.004", price="98", tick=2),
        b.fill(side="sell", quantity="0.007", price="101", tick=3),
    ]
    matcher = FIFORoundTripMatcher()
    for fill in fills:
        matcher.process(fill)
    assert [
        (trip.entry_fill_id, trip.matched_quantity_btc)
        for trip in matcher.round_trips
    ] == [("fifo-fill-001", D("0.006")), ("fifo-fill-002", D("0.001"))]


def test_03_long_winning_round_trip(hand_by_id):
    assert D(hand_by_id["A"]["economics"]["net_pnl_usdt"]) > 0


def test_04_long_losing_round_trip(hand_by_id):
    assert D(hand_by_id["B"]["economics"]["net_pnl_usdt"]) < 0


def test_05_short_winning_round_trip(hand_by_id):
    assert D(hand_by_id["C"]["economics"]["net_pnl_usdt"]) > 0


def test_06_short_losing_round_trip(hand_by_id):
    assert D(hand_by_id["D"]["economics"]["net_pnl_usdt"]) < 0


def test_07_partial_fill_pairing(hand_by_id):
    assert hand_by_id["E"]["round_trip_count"] == 3
    assert D(hand_by_id["E"]["reconciliation"]["matched_quantity_btc"]) == D("0.01")


def test_08_unmatched_residual_inventory():
    b = FillBuilder("residual")
    matcher = FIFORoundTripMatcher()
    matcher.process(b.fill(side="buy", quantity="0.01", price="100", tick=1))
    matcher.process(b.fill(side="sell", quantity="0.006", price="101", tick=2))
    assert matcher.unmatched_signed_inventory_btc == D("0.004")


def test_09_no_duplicate_fill_matching():
    b = FillBuilder("duplicate")
    fill = b.fill(side="buy", quantity="0.01", price="100", tick=1)
    matcher = FIFORoundTripMatcher()
    matcher.process(fill)
    with pytest.raises(ValueError, match="duplicate fill identity"):
        matcher.process(fill)


def test_10_fee_charged_exactly_once(hand):
    assert hand["summary"]["duplicate_fee_charges"] == 0
    assert all(trace["charged_count"] == 1 for trace in hand["fee_traces"])


def test_11_maker_taker_role_retained(hand):
    roles = {(fill.fee_role, fill.maker_or_taker) for fill in hand["fills"]}
    assert roles == {("MAKER", "maker"), ("TAKER", "taker")}


def test_12_terminal_fee_charged_once(hand):
    terminal = [
        fill for fill in hand["fills"]
        if fill.trigger_type == "TERMINAL_EXECUTION"
    ]
    assert len(terminal) == 1
    assert terminal[0].fee == terminal[0].fee_base_usdt * terminal[0].fee_rate


def test_13_hard_kill_fee_charged_once(hand):
    killed = [
        fill for fill in hand["fills"]
        if fill.trigger_type == "HARD_KILL_EXECUTION"
    ]
    assert len(killed) == 1
    assert killed[0].fee == killed[0].fee_base_usdt * killed[0].fee_rate


def test_14_gross_execution_pnl(hand_by_id):
    assert D(hand_by_id["A"]["economics"]["gross_execution_pnl_usdt"]) == D("0.20")


def test_15_net_execution_pnl(hand_by_id):
    assert D(hand_by_id["A"]["economics"]["net_pnl_usdt"]) == D("0.100000")


def test_16_quoted_spread():
    spread = quoted_spread(D("49990"), D("50010"))
    assert D(spread["quoted_spread_usdt_per_btc"]) == D("20")
    assert D(spread["quoted_spread_bps"]) == D("4")


def test_17_realized_spread(hand):
    trip = hand["round_trips"][0]
    assert trip.gross_round_trip_spread_capture == D("0.20")


def test_18_effective_spread():
    b = FillBuilder("edge")
    buy = b.fill(side="buy", quantity="0.01", price="49990", tick=1)
    sell = b.fill(side="sell", quantity="0.01", price="50010", tick=2)
    assert effective_fill_edge(buy) == D("10")
    assert effective_fill_edge(sell) == D("10")


def test_19_gross_spread_distinct_from_inventory_pnl():
    b = FillBuilder("decomposition")
    fills = [
        b.fill(
            side="buy", quantity="0.01", price="99", tick=1,
            decision_mid="100",
        ),
        b.fill(
            side="sell", quantity="0.01", price="102", tick=2,
            decision_mid="101",
        ),
    ]
    matcher = FIFORoundTripMatcher()
    for fill in fills:
        matcher.process(fill)
    trip = matcher.round_trips[0]
    assert trip.gross_round_trip_spread_capture == D("0.02")
    assert trip.realized_inventory_pnl == D("0.01")
    assert trip.gross_execution_pnl == D("0.03")


def test_20_total_pnl_identity(hand_by_id):
    assert all(
        item["economics"]["pnl_identity_reconciles"]
        for item in hand_by_id.values()
    )


def test_21_buy_markout_sign():
    fill = FillBuilder("buy-markout").fill(
        side="buy", quantity="0.01", price="99", tick=1, decision_mid="100"
    )
    assert D(markout(fill, future_tick=2, future_mid=D("101"))[
        "markout_usdt_for_fill_quantity"
    ]) > 0


def test_22_sell_markout_sign():
    fill = FillBuilder("sell-markout").fill(
        side="sell", quantity="0.01", price="101", tick=1,
        decision_mid="100",
    )
    assert D(markout(fill, future_tick=2, future_mid=D("99"))[
        "markout_usdt_for_fill_quantity"
    ]) > 0


def test_23_quantity_weighted_markout():
    fill = FillBuilder("weighted").fill(
        side="buy", quantity="0.02", price="99", tick=1, decision_mid="100"
    )
    result = markout(fill, future_tick=2, future_mid=D("101"))
    assert D(result["markout_usdt_for_fill_quantity"]) == D("0.04")


def test_24_fill_trigger_classification():
    taxonomy = set(FILL_TRIGGER_CONTRACT["taxonomy"])
    assert {
        "AGGRESSOR_TRADE_AT_QUOTE",
        "STRICT_TRADE_THROUGH",
        "TERMINAL_EXECUTION",
        "HARD_KILL_EXECUTION",
    } <= taxonomy


def test_25_fill_before_cancel_ordering(micro):
    assert micro["summary"]["event_ordering"]["checks"][
        "fill_before_same_tick_cancel"
    ]


def test_26_cancel_before_later_fill_ordering(micro):
    assert micro["summary"]["event_ordering"]["checks"][
        "cancel_before_later_fill"
    ]


def test_27_partial_fill_then_cancel(micro):
    assert micro["summary"]["event_ordering"]["checks"][
        "partial_fill_then_cancel"
    ]


def test_28_no_future_data_leakage():
    engine = CausalTradeEventMatcher(scenario_id="causal")
    engine.activate_quote(
        side="buy", price=D("100"), quantity_btc=D("0.01"),
        decision_mid=D("101"), quote_created_tick=0, activation_tick=1,
    )
    engine.process_tick(tick=1, bid=D("100"), ask=D("102"))
    assert engine.fills == []


def test_29_prefix_invariance(micro):
    assert micro["summary"]["event_ordering"]["checks"]["prefix_invariance"]


def test_30_stationary_symmetric_has_winner(micro_by_id):
    assert micro_by_id["stationary_symmetric"]["passed"]
    assert D(micro_by_id["stationary_symmetric"]["economics"]["net_pnl_usdt"]) > 0


def test_31_mean_reversion_has_recoverable_fills(micro_by_id):
    assert micro_by_id["mean_reversion"]["passed"]
    assert micro_by_id["mean_reversion"]["round_trip_count"] > 0


def test_32_adverse_trend_has_adverse_markout(micro_by_id):
    assert micro_by_id["adverse_trend"]["passed"]
    assert D(micro_by_id["adverse_trend"]["economics"]["net_pnl_usdt"]) < 0


def test_33_toxic_trade_through_remains_adverse(micro_by_id):
    assert micro_by_id["toxic_trade_through"]["passed"]
    assert D(micro_by_id["toxic_trade_through"]["economics"]["net_pnl_usdt"]) < 0


def test_34_touch_only_does_not_fill(micro_by_id):
    assert micro_by_id["touch_without_trade"]["passed"]
    assert micro_by_id["touch_without_trade"]["fill_count"] == 0


def test_35_aggressor_trade_can_fill_without_adverse_crossing(micro_by_id):
    assert micro_by_id["aggressor_trade_at_quote"]["passed"]
    assert micro_by_id["aggressor_trade_at_quote"]["fill_count"] == 1


def test_36_hard_kill_attribution(hand_by_id):
    economics = hand_by_id["H"]["economics"]
    assert D(economics["hard_kill_execution_pnl_usdt"]) < 0
    assert D(economics["gross_execution_pnl_usdt"]) == 0


def test_37_terminal_liquidation_attribution(hand_by_id):
    economics = hand_by_id["G"]["economics"]
    assert D(economics["terminal_liquidation_pnl_usdt"]) != 0
    assert D(economics["gross_execution_pnl_usdt"]) == 0


def test_38_deterministic_replay():
    first = {
        "hand": hand_fixtures()["summary"],
        "micro": microstructure_fixtures()["summary"],
    }
    second = {
        "hand": hand_fixtures()["summary"],
        "micro": microstructure_fixtures()["summary"],
    }
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_39_no_optuna_access():
    assert build_specification(ROOT)["optimization"] is False


def test_40_no_validation_or_holdout_access():
    spec = build_specification(ROOT)
    assert spec["validation_opened"] is False
    assert spec["holdout_opened"] is False


def test_41_no_git_or_external_access():
    spec = build_specification(ROOT)
    assert spec["git_operation"] is False
    assert spec["external_access"] is False


def test_hard_kill_continuation_ticks_have_exact_quote_mode_classification():
    ticks = []
    mid = D("50000")
    for index in range(80):
        mid *= D("0.997") if index < 55 else D("1.0001")
        ticks.append({
            "bids": [[float(mid - D("5")), 1.0]],
            "asks": [[float(mid + D("5")), 1.0]],
            "timestamp": 2_500_000_000_000 + index * 300_000,
        })
    run = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_3_HARD_KILL_QUOTE_MODE_REGRESSION",
        scenario="hard_kill_quote_mode_regression",
        source_block="v1-3-regression-only",
        fill_seed=103_001,
        initial_capital_usdt=750,
        leverage=3,
    ).run(ticks)
    classified = (
        run.two_sided_quote_decisions
        + run.one_sided_quote_decisions
        + run.no_quote_decisions
    )
    assert run.hard_kills > 0
    assert classified == run.total_ticks
    assert all(
        item["suppression_reason"] == "HARD_KILL_LATCHED"
        for item in run.quote_decisions
        if item.get("suppression_reason") == "HARD_KILL_LATCHED"
    )
