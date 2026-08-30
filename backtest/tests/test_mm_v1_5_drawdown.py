"""Pre-freeze regression tests for the v1.5 drawdown repair protocol."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_4_protocol import (
    paths as v14_paths,
    profiles as v14_profiles,
    ticks as v14_ticks,
)
from backtest.mm_v1_5_drawdown import STATUSES
from backtest.mm_v1_5_protocol import (
    PROTOCOL_ID,
    SEVERITY,
    build_spec,
    path_disjointness,
    paths,
    profiles,
)
from market_maker.as_config import MarketMakerV1Config


ROOT = Path(__file__).resolve().parents[2]


def _engineering_run(profile_index: int, path_index: int):
    profile = profiles()[profile_index]
    path = v14_paths()[path_index]
    return MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id="MM_V1_5_ENGINEERING_FIXTURE",
        scenario=path["scenario"],
        source_block="v1-5-engineering-fixture-on-closed-v1-4-path",
        fill_seed=path["fill_seed"],
        cancel_latency_ticks=path["parameters"]["cancel_delay"],
        initial_capital_usdt=750,
        leverage=3,
        defensive_overlay=profile["defensive_overlay"],
        evidence_namespace=f"fixture-{profile['profile_id']}",
    ).run(v14_ticks(path))


def test_exact_fixed_profile_set():
    assert [row["profile_name"] for row in profiles()] == [
        "BASELINE_CONTROL",
        "TIMEBOXED_V1_4_CONTROL",
        "EXTENDED_TIMEBOX_6",
        "REVERSAL_CONFIRMED_REENTRY_2",
        "INVENTORY_AWARE_REENTRY",
        "CONFIRMED_INVENTORY_AWARE",
        "TIMEBOXED_DRAWDOWN_GUARD_3PCT",
        "COMPOSITE_DRAWDOWN_REPAIR",
    ]


def test_timeboxed_control_is_exact_v14_carry_forward():
    current = profiles()[1]
    prior = v14_profiles()[6]
    assert current["parameters"] == prior["parameters"]
    assert current["defensive_overlay"] == prior["defensive_overlay"]


def test_profiles_are_unique_fixed_and_nonadaptive():
    matrix = profiles()
    assert len(matrix) == 8
    assert len({row["profile_fingerprint"] for row in matrix}) == 8
    assert all(not row["adaptive"] for row in matrix)


def test_fresh_paths_are_disjoint_and_balanced():
    matrix = paths()
    assert len(matrix) == 16
    assert path_disjointness()["passed"]
    assert all(
        sum(row["severity"] == severity for row in matrix) == 4
        for severity in SEVERITY
    )


def test_confirmed_reentry_holds_until_current_tick_evidence():
    run = _engineering_run(3, 0)
    confirmations = [
        row for row in run.defensive_events
        if row["event"] == "ONE_SIDED_REENTRY_CONFIRMATION"
    ]
    assert confirmations
    assert any(not row["confirmation_signal"] for row in confirmations)
    assert any(row["confirmation_streak"] >= 2 for row in confirmations)
    names = Counter(row["event"] for row in run.defensive_events)
    assert names["DEFENSIVE_MODE_EXIT"] > 0
    assert names["DEFENSIVE_REENTRY_READY"] > 0
    assert all(
        "future" not in key.lower()
        for row in confirmations
        for key in row
    )


def test_inventory_aware_reentry_is_scoped_after_exit():
    run = _engineering_run(4, 3)
    events = [
        row for row in run.defensive_events
        if row["event"] == "INVENTORY_AWARE_REENTRY"
    ]
    assert events
    assert all(abs(row["inventory_btc"]) > 0 for row in events)
    assert all(
        row["suppressed_side"]
        == ("buy" if row["inventory_btc"] > 0 else "sell")
        for row in events
    )


def test_drawdown_guard_is_special_fee_inclusive_exit():
    run = _engineering_run(6, 8)
    emergency = [
        row for row in run.fills
        if row["fill_trigger"] == "EMERGENCY_EXECUTION"
    ]
    entries = [
        row for row in run.defensive_events
        if row["event"] == "DRAWDOWN_GUARD_ENTRY"
    ]
    assert len(emergency) == 1
    assert len(entries) == 1
    fill = emergency[0]
    entry = entries[0]
    assert fill["maker_or_taker"] == "taker"
    assert fill["special_exit"]
    assert not fill["normal_activity_eligible"]
    assert not fill["normal_round_trip_eligible"]
    assert fill["fee"] > 0
    assert fill["event_phase"] == "EMERGENCY_EXECUTION"
    assert fill["event_sequence"] < entry["event_sequence"]
    assert run.hard_kills == 0
    assert run.terminal_residual_inventory_btc == 0
    assert run.economics["emergency_execution_cost_usdt"] > 0
    assert run.economics["emergency_execution_pnl_usdt"] < 0
    assert run.economics["pnl_identity_reconciles"]


def test_quote_mode_counters_reconcile_under_guard_latch():
    run = _engineering_run(6, 8)
    assert (
        run.two_sided_quote_decisions
        + run.one_sided_quote_decisions
        + run.no_quote_decisions
        == run.total_ticks
    )


def test_budget_and_optuna_gate_are_explicit():
    spec = build_spec(ROOT)
    assert spec["resilience_budget"]["S2_5"]["worst_drawdown_max"] == 0.035
    assert spec["resilience_budget"]["S3_LOW"]["worst_drawdown_max"] == 0.040
    assert spec["resilience_budget"]["S3_MID"]["worst_drawdown_max"] == 0.050
    assert spec["optuna_unlock_gate"]["required_severity_budgets"] == [
        "S2_5", "S3_LOW", "S3_MID"
    ]
    assert not spec["optuna_unlock_gate"]["this_protocol_executes_optuna"]


def test_status_contract_is_closed():
    assert len(STATUSES) == 6
    assert STATUSES[-1] == "MM_V1_5_DRAWDOWN_REPAIR_SUPPORTED"


def test_no_optuna_validation_holdout_git_external_or_prior_rerun():
    spec = build_spec(ROOT)
    assert not any((
        spec["optimization"],
        spec["validation_opened"],
        spec["holdout_opened"],
        spec["external_access"],
        spec["git_write_operation"],
        spec["prior_matrix_reruns"],
        spec["production_defaults_changed"],
    ))


def test_protocol_and_executor_have_no_forbidden_imports():
    for name in (
        "backtest/mm_v1_5_protocol.py",
        "backtest/mm_v1_5_drawdown.py",
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
    assert PROTOCOL_ID.startswith("MM_V1_5_")
