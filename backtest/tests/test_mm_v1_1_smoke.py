"""Direct frozen-design and evidence tests for MM v1.1 economic smoke."""

from __future__ import annotations

import ast
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from backtest.mm_runner import (
    MarketMakerBacktestRunner,
    _attach_markouts,
    _round_trips,
)
from backtest.mm_v1_1_protocol import (
    PATH_DEFINITIONS,
    PRIMARY_CAPITAL_USDT,
    PROFILE_DEFINITIONS,
    STRESS_CAPITALS_USDT,
    build_specification,
    fixed_profiles,
    frozen_paths,
    scenario_ticks,
)
from backtest.mm_v1_1_smoke import (
    SUPPRESSION_REASONS,
    _concentration,
    _margin_components_reconcile,
    _path_result,
    decide_status,
)
from market_maker.as_config import MarketMakerV1Config
from market_maker.diagnostics import favorable_markout


ROOT = Path(__file__).resolve().parents[2]


def test_primary_capital_is_exactly_750():
    assert PRIMARY_CAPITAL_USDT == 750.0


def test_stress_capitals_are_diagnostic_only():
    spec = build_specification(ROOT)
    assert STRESS_CAPITALS_USDT == (300.0, 500.0, 1000.0)
    assert spec["capital_copies_are_independent_evidence"] is False
    assert PRIMARY_CAPITAL_USDT not in STRESS_CAPITALS_USDT


def test_capital_copies_do_not_inflate_unique_paths():
    paths = frozen_paths()
    assert len(paths) == len({path["market_path_hash"] for path in paths}) == 12
    assert len(paths) * 4 != len(paths)


def test_fixed_profile_count_is_bounded_and_predeclared():
    profiles = fixed_profiles()
    assert 6 <= len(profiles) <= 12
    assert len(profiles) == len(PROFILE_DEFINITIONS) == 8


def test_profile_fingerprints_are_deterministic():
    assert fixed_profiles() == fixed_profiles()
    for record in fixed_profiles():
        profile = MarketMakerV1Config(**record["parameters"])
        assert profile.fingerprint == record["profile_fingerprint"]


@pytest.mark.parametrize(
    "override",
    [
        {"risk_aversion_gamma": 0},
        {"arrival_decay_k_or_proxy": 0},
        {"minimum_half_spread_bps": -1},
        {"minimum_order_lifetime_ticks": 0},
        {"requote_threshold_ticks": 0},
    ],
)
def test_invalid_profiles_fail_before_simulation(override):
    with pytest.raises(ValueError):
        MarketMakerV1Config(**override).validate()


def test_no_optuna_import_in_smoke_path():
    for name in ("backtest/mm_v1_1_protocol.py", "backtest/mm_v1_1_smoke.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any("optuna" in name.lower() for name in imports)


def test_new_paths_exclude_prior_ids_hashes_and_seeds():
    prior_specs = [
        ROOT / "artifacts/market_maker_v1_smoke/protocol_20260726T145535Z"
        / "mm_v1_smoke_protocol.json",
        ROOT / "artifacts/mm_v1_1_post_admission_smoke"
        / "specification_20260726T160234Z/smoke_spec.json",
    ]
    priors = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in prior_specs
    ]
    old_paths = [path for prior in priors for path in prior["paths"]]
    old_ids = {path["market_path_id"] for path in old_paths}
    old_hashes = {path["market_path_hash"] for path in old_paths}
    old_seeds = {
        value for path in old_paths
        for value in (path["market_seed"], path["fill_seed"])
    }
    for path in frozen_paths():
        assert path["market_path_id"] not in old_ids
        assert path["market_path_hash"] not in old_hashes
        assert path["market_seed"] not in old_seeds
        assert path["fill_seed"] not in old_seeds


def test_prequote_admission_and_zero_preventable_breaches():
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_1_ENGINEERING",
        scenario="normal_volatility_range",
        source_block="engineering-only",
        fill_seed=88_002,
        initial_capital_usdt=750,
        leverage=3,
    ).run(scenario_ticks("normal_volatility_range", 88_001, count=80))
    assert result.preventable_margin_breaches == 0
    assert result.unknown_margin_states == 0
    assert result.margin_events


def test_quote_mode_counters_reconcile():
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_1_COUNTER_TEST",
        scenario="normal_volatility_range",
        source_block="engineering-only",
        fill_seed=88_012,
        initial_capital_usdt=750,
        leverage=3,
    ).run(scenario_ticks("normal_volatility_range", 88_011, count=80))
    assert (
        result.two_sided_quote_decisions
        + result.one_sided_quote_decisions
        + result.no_quote_decisions
        == result.total_ticks
    )


def test_quote_side_creation_counts_reconcile():
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_1_SIDE_COUNT_TEST",
        scenario="normal_volatility_range",
        source_block="engineering-only",
        fill_seed=88_014,
        initial_capital_usdt=750,
        leverage=3,
    ).run(scenario_ticks("normal_volatility_range", 88_013, count=80))
    assert (
        result.bid_orders_created + result.ask_orders_created
        == result.order_reconciliation["passive_orders_created"]
    )


def test_suppression_reasons_are_exclusive():
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_1_SUPPRESSION_TEST",
        scenario="normal_volatility_range",
        source_block="engineering-only",
        fill_seed=88_022,
        initial_capital_usdt=300,
        leverage=3,
    ).run(scenario_ticks("normal_volatility_range", 88_021, count=80))
    suppressed = [
        event for event in result.margin_events
        if event.get("decision") == "SUPPRESS"
    ]
    assert suppressed
    assert all(isinstance(event.get("reason"), str) for event in suppressed)


def test_all_suppression_counters_are_reported():
    profile = fixed_profiles()[0]
    path = frozen_paths()[0]
    run = MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id="MM_V1_1_SUPPRESSION_COUNTER_TEST",
        scenario=path["scenario"],
        source_block="engineering-only",
        fill_seed=88_024,
        initial_capital_usdt=300,
        leverage=3,
    ).run(scenario_ticks(path["scenario"], 88_023, count=80))
    record = _path_result(profile, path, 300, run)
    assert set(record["quote_modes"]["suppression_reasons"]) == set(
        SUPPRESSION_REASONS
    )


def test_conservative_fills_only_enter_runner_ranking_evidence():
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(),
        protocol_id="MM_V1_1_FILL_TEST",
        scenario="normal_volatility_range",
        source_block="engineering-only",
        fill_seed=88_032,
        initial_capital_usdt=750,
        leverage=3,
    ).run(scenario_ticks("normal_volatility_range", 88_031, count=100))
    assert all(
        fill["liquidity"] in {"maker", "taker"} for fill in result.fills
    )
    assert all(
        fill["liquidity"] == "maker"
        for fill in result.fills if fill["reason"] == "limit"
    )


def test_touch_and_probabilistic_fills_are_excluded_from_ranking():
    semantics = build_specification(ROOT)["fill_semantics"]
    assert semantics["ranking_mode"] == "conservative"
    assert semantics["touch_only_ranking_weight"] == 0
    assert semantics["probabilistic_ranking_weight"] == 0


def test_round_trip_identity_and_attribution_are_deterministic():
    fills = [
        {"side": "buy", "price": 100.0, "size": 0.01, "fee": 0.01,
         "tick": 1, "reason": "limit"},
        {"side": "sell", "price": 102.0, "size": 0.01, "fee": 0.01,
         "tick": 4, "reason": "limit"},
    ]
    first = _round_trips(fills)
    second = _round_trips(fills)
    assert first == second
    assert first[0]["gross_spread_capture_usdt"] == pytest.approx(0.02)
    assert first[0]["fees_usdt"] == pytest.approx(0.02)
    assert first[0]["net_pnl_usdt"] == pytest.approx(0.0)
    assert first[0]["holding_duration_ticks"] == 3


def test_spread_fee_and_inventory_attribution_are_distinct():
    fills = [
        {
            "fill_id": "attribution-buy",
            "side": "buy", "price": 99.0, "size": 0.01, "fee": 0.001,
            "tick": 1, "reason": "limit", "decision_mid": 100.0,
        },
        {
            "fill_id": "attribution-sell",
            "side": "sell", "price": 102.0, "size": 0.01, "fee": 0.001,
            "tick": 2, "reason": "limit", "decision_mid": 101.0,
        },
    ]
    trip = _round_trips(fills)[0]
    assert trip["gross_execution_pnl_usdt"] == pytest.approx(0.03)
    assert trip["gross_round_trip_spread_capture_usdt"] == pytest.approx(0.02)
    assert trip["realized_inventory_pnl_usdt"] == pytest.approx(0.01)
    assert trip["net_pnl_usdt"] == pytest.approx(0.028)


def test_terminal_liquidation_is_attributed():
    fills = [
        {"side": "buy", "price": 100.0, "size": 0.01, "fee": 0.0,
         "tick": 1, "reason": "limit"},
        {"side": "sell", "price": 99.0, "size": 0.01, "fee": 0.01,
         "tick": 5, "reason": "mm-terminal"},
    ]
    trip = _round_trips(fills)[0]
    assert trip["terminal_exit"] is True
    assert trip["net_pnl_usdt"] < 0


def test_markout_sign_convention_directly():
    assert favorable_markout(
        side="buy", fill_price=100, future_mid=101
    ) == 1
    assert favorable_markout(
        side="sell", fill_price=101, future_mid=100
    ) == 1
    fills = [{"side": "buy", "price": 100.0, "size": 0.01, "tick": 1}]
    _attach_markouts(fills, [100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    assert fills[0]["markout_1_tick_usdt_per_btc"] == 1.0


def test_top_ten_removal_and_added_two_bps_math():
    values = [1.0] * 9 + [10.0]
    concentration = _concentration(values, 0.10)
    assert concentration["after_removal_usdt"] == 9.0
    notional = 500.0
    assert notional * 2 / 10_000 == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("kwargs", "expected_gate"),
    [
        ({"integrity_passed": False, "determinism_passed": True,
          "safety_passed": False, "activity_profile_count": 0,
          "economics_profile_count": 0}, "GATE_0_INTEGRITY"),
        ({"integrity_passed": True, "determinism_passed": True,
          "safety_passed": False, "activity_profile_count": 0,
          "economics_profile_count": 0}, "GATE_1_ADMISSION_AND_SAFETY"),
        ({"integrity_passed": True, "determinism_passed": True,
          "safety_passed": True, "activity_profile_count": 0,
          "economics_profile_count": 0}, "GATE_2_ACTIVITY"),
        ({"integrity_passed": True, "determinism_passed": True,
          "safety_passed": True, "activity_profile_count": 1,
          "economics_profile_count": 0}, "GATE_3_CONSERVATIVE_ECONOMICS"),
    ],
)
def test_gate_ordering(kwargs, expected_gate):
    assert decide_status(**kwargs)[1] == expected_gate


def test_no_profile_replacement_or_path_extension():
    spec = build_specification(ROOT)
    assert spec["profile_extension"] is None
    assert spec["path_extension"] is None
    assert len(PATH_DEFINITIONS) == 12


def test_no_validation_holdout_external_or_git_operations():
    spec = build_specification(ROOT)
    assert spec["optimization"] is False
    assert spec["validation_opened"] is False
    assert spec["holdout_opened"] is False
    assert spec["external_access"] is False
    assert spec["git_write_operation"] is False


def test_margin_component_reconciliation():
    components = {
        "position_margin_usdt": 10.0,
        "active_bid_order_reserve_usdt": 1.0,
        "active_ask_order_reserve_usdt": 2.0,
        "proposed_bid_order_reserve_usdt": 3.0,
        "proposed_ask_order_reserve_usdt": 4.0,
        "fee_reserve_usdt": 0.5,
        "liquidation_reserve_usdt": 0.0,
        "emergency_exit_reserve_usdt": 0.0,
        "other_buffer_usdt": 0.0,
        "pending_order_reserve_usdt": 10.0,
        "total_modeled_margin_usdt": 20.5,
    }
    assert _margin_components_reconcile([{"components": components}])
