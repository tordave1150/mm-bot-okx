"""Pre-freeze regression tests for the v1.4 targeted causal matrix."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_4_protocol import (
    PROTOCOL_ID,
    SEVERITY,
    build_spec,
    path_disjointness,
    paths,
    profiles,
    ticks,
)
from backtest.mm_v1_4_targeted import STATUSES
from market_maker.as_config import MarketMakerV1Config


ROOT = Path(__file__).resolve().parents[2]


def _run(profile_index: int, path_index: int = 3):
    profile = profiles()[profile_index]
    path = paths()[path_index]
    return MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id="MM_V1_4_ENGINEERING_FIXTURE",
        scenario=path["scenario"],
        source_block="v1-4-engineering-fixture",
        fill_seed=path["fill_seed"],
        cancel_latency_ticks=path["parameters"]["cancel_delay"],
        initial_capital_usdt=750,
        leverage=3,
        defensive_overlay=profile["defensive_overlay"],
        evidence_namespace=f"fixture-{profile['profile_id']}",
    ).run(ticks(path))


def test_exact_fixed_profile_set():
    assert [row["profile_name"] for row in profiles()] == [
        "BASELINE_CONTROL",
        "WIDER_SPREAD_CONTROL",
        "EXISTING_FAST_CANCEL_CONTROL",
        "SIDE_SPECIFIC_FAST_CANCEL",
        "FAST_CANCEL_HYSTERESIS",
        "CAUSAL_TWO_HIT_TOXIC_PAUSE",
        "TIMEBOXED_ONE_SIDED_DEFENSE",
        "COMPOSITE_LIGHT",
    ]


def test_all_profiles_are_non_adaptive_and_unique():
    matrix = profiles()
    assert len(matrix) == 8
    assert all(not row["adaptive"] for row in matrix)
    assert len({row["profile_fingerprint"] for row in matrix}) == 8


def test_exact_four_paths_per_severity():
    matrix = paths()
    assert len(matrix) == 16
    assert all(
        sum(row["severity"] == severity for row in matrix) == 4
        for severity in SEVERITY
    )


def test_paths_are_disjoint_from_v13c():
    assert path_disjointness()["passed"]


def test_paths_have_frozen_three_phase_layout():
    path = paths()[0]
    generated = ticks(path)
    assert len(generated) == 240
    assert Counter(row["regime_phase"] for row in generated) == {
        "NORMAL_PRELUDE": 60,
        "STRESS": 120,
        "RECOVERY": 60,
    }
    assert path["segments"]["NORMAL_PRELUDE"] == [0, 59]
    assert path["segments"]["RECOVERY"] == [180, 239]


def test_prelude_and_recovery_are_below_entry_shock_threshold():
    generated = ticks(paths()[0])
    for index in list(range(1, 60)) + list(range(181, 240)):
        previous = sum(generated[index - 1]["bids"][0][0:1])
        previous += generated[index - 1]["asks"][0][0]
        previous /= 2
        current = (
            generated[index]["bids"][0][0]
            + generated[index]["asks"][0][0]
        ) / 2
        assert abs(current / previous - 1) * 10_000 < 4.5


def test_side_specific_fast_cancel_records_scope():
    run = _run(3)
    events = [
        row for row in run.defensive_events
        if row["event"] == "FAST_CANCEL_ON_VOLATILITY"
    ]
    assert events
    assert all(row["cancel_scope"] == "adverse_side" for row in events)


def test_fast_cancel_hysteresis_enters_exits_and_reenters():
    run = _run(4)
    names = Counter(row["event"] for row in run.defensive_events)
    assert names["FAST_CANCEL_ON_VOLATILITY"] >= 1
    assert names["DEFENSIVE_MODE_EXIT"] >= 1
    assert names["DEFENSIVE_REENTRY_READY"] >= 1


def test_two_hit_pause_uses_causal_adverse_fill_evidence():
    run = _run(5)
    entries = [
        row for row in run.defensive_events
        if row["event"] == "TOXIC_FLOW_PAUSE_ENTRY"
    ]
    assert entries
    assert all(row["toxic_fill_streak_required"] == 2 for row in entries)
    assert all(row["adverse_fill_observed"] for row in entries)


def test_timeboxed_one_sided_mode_exits():
    run = _run(6)
    names = Counter(row["event"] for row in run.defensive_events)
    assert names["ONE_SIDED_DEFENSIVE_ENTRY"] > 0
    assert names["DEFENSIVE_MODE_EXIT"] > 0
    assert names["DEFENSIVE_REENTRY_READY"] > 0


def test_composite_light_excludes_spread_guard():
    overlay = profiles()[7]["defensive_overlay"]
    assert "volatility_spread_guard" not in overlay
    assert overlay["fast_cancel_scope"] == "adverse_side"
    assert overlay["toxic_fill_streak_required"] == 2


def test_causal_sequences_are_unique_and_phase_ordered():
    run = _run(5)
    sequences = [
        row["event_sequence"] for row in run.defensive_events
    ]
    assert len(sequences) == len(set(sequences))
    for fill in run.fills:
        assert fill["event_phase"] in {
            "FILL_EVALUATION", "HARD_KILL", "TERMINAL_CLEANUP"
        }
        assert fill["event_sequence"] // 10_000 == fill["tick"]


def test_activity_contract_requires_stress_and_recovery_fills():
    floor = build_spec(ROOT)["activity_floor"]
    assert floor["stress_normal_fills_min"] == 4
    assert floor["recovery_normal_fills_min"] == 2
    assert floor["represented_scenario_families_min"] == 3


def test_status_contract_is_closed_and_support_is_last():
    assert len(STATUSES) == 6
    assert STATUSES[-1] == "MM_V1_4_TARGETED_RESILIENCE_SUPPORTED"


def test_no_optuna_validation_holdout_git_external_or_prior_rerun():
    spec = build_spec(ROOT)
    assert not any((
        spec["optimization"],
        spec["validation_opened"],
        spec["holdout_opened"],
        spec["external_access"],
        spec["git_write_operation"],
        spec["balanced_v1_3_matrix_rerun"],
        spec["stress_v1_3a_matrix_rerun"],
        spec["repair_v1_3b_matrix_rerun"],
        spec["evidence_v1_3c_matrix_rerun"],
    ))


def test_protocol_and_executor_have_no_forbidden_imports():
    for name in (
        "backtest/mm_v1_4_protocol.py",
        "backtest/mm_v1_4_targeted.py",
    ):
        tree = ast.parse((ROOT / name).read_text())
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        assert not any(
            token in imported.lower()
            for imported in imports
            for token in ("optuna", "requests", "github")
        )


def test_protocol_identity_is_new():
    assert PROTOCOL_ID.startswith("MM_V1_4_")


def test_deterministic_runner_replay():
    first = _run(4)
    second = _run(4)
    assert first.fills == second.fills
    assert first.defensive_events == second.defensive_events
    assert first.economics == second.economics
