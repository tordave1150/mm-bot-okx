"""Direct contracts for the MM v1.3 balanced-causal smoke."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from backtest.mm_v1_3_protocol import (
    BALANCED_MODEL, PRIMARY_CAPITAL, STRESS_CAPITALS, STRESS_MODEL,
    balanced_paths, build_specification, fixed_profiles, stress_paths,
)
from backtest.mm_v1_3_smoke import (
    _remove_top, aggregate_profile, run_balanced_path,
)


ROOT = Path(__file__).resolve().parents[2]


def test_models_are_separate_and_diagnostic_models_have_zero_weight():
    policy = build_specification(ROOT)["fill_model_policy"]
    assert policy["primary"] == BALANCED_MODEL
    assert policy["stress"] == STRESS_MODEL
    assert policy["touch_ranking_weight"] == 0
    assert policy["probabilistic_ranking_weight"] == 0


def test_primary_capital_and_copies_are_frozen():
    spec = build_specification(ROOT)
    assert PRIMARY_CAPITAL == 750
    assert STRESS_CAPITALS == (300.0, 500.0, 1000.0)
    assert spec["capital_copies_independent"] is False


def test_profiles_are_bounded_new_and_deterministic():
    first, second = fixed_profiles(), fixed_profiles()
    assert first == second
    assert 6 <= len(first) <= 8
    assert all(item["profile_id"].startswith("mm-v1-3-") for item in first)


def test_balanced_and_stress_paths_are_frozen_and_disjoint():
    balanced, stress = balanced_paths(), stress_paths()
    assert len(balanced) == 12
    assert len(stress) == 6
    assert not ({item["path_id"] for item in balanced}
                & {item["path_id"] for item in stress})
    assert len({item["path_hash"] for item in balanced}) == 12
    assert len({item["path_hash"] for item in stress}) == 6


def test_paths_are_disjoint_from_closed_v1_1_and_v1_2_evidence():
    prior = json.loads((
        ROOT / "artifacts/mm_v1_1_post_admission_smoke"
        / "specification_20260726T160234Z/smoke_spec.json"
    ).read_text())
    old_ids = {item["market_path_id"] for item in prior["paths"]}
    old_hashes = {item["market_path_hash"] for item in prior["paths"]}
    assert not old_ids & {item["path_id"] for item in balanced_paths()}
    assert not old_hashes & {item["path_hash"] for item in balanced_paths()}


def test_explicit_aggressor_provenance_and_no_future_leakage():
    profile, path = fixed_profiles()[0], balanced_paths()[0]
    run = run_balanced_path(profile, path)
    assert run["fills"]
    assert all(fill.trigger_type == "AGGRESSOR_TRADE_AT_QUOTE"
               for fill in run["fills"] if fill.maker_or_taker == "maker")
    assert all(fill.triggering_event_id and fill.triggering_trade_price is not None
               for fill in run["fills"] if fill.maker_or_taker == "maker")
    assert all(fill.activation_tick <= fill.fill_tick for fill in run["fills"])


def test_balanced_only_activity_and_normal_round_trip_exclusion():
    profile, path = fixed_profiles()[0], balanced_paths()[0]
    run = run_balanced_path(profile, path)
    aggregate = aggregate_profile(profile, [run])
    assert aggregate["activity"]["balanced_maker_fills"] == sum(
        fill.trigger_type == "AGGRESSOR_TRADE_AT_QUOTE" for fill in run["fills"]
    )
    assert all(trip.terminal_or_normal == "NORMAL"
               for trip in run["normal_trips"])


def test_fifo_fee_quantity_pnl_and_spread_inventory_reconcile():
    run = run_balanced_path(fixed_profiles()[0], balanced_paths()[0])
    assert run["reconciliation"]["fee_identity"]
    assert run["reconciliation"]["buy_quantity_identity"]
    assert run["reconciliation"]["sell_quantity_identity"]
    assert run["economics"]["pnl_identity_reconciles"]
    assert run["economics"]["spread_inventory_decomposition_reconciles"]


def test_quote_modes_reconcile_exactly():
    run = run_balanced_path(fixed_profiles()[0], balanced_paths()[0])
    assert run["unclassified_quote_mode_ticks"] == 0
    assert run["two_sided"] + run["one_sided"] + run["no_quote"] == run["ticks"]


def test_added_cost_top_ten_and_markout_are_defined():
    profile = fixed_profiles()[0]
    aggregate = aggregate_profile(
        profile, [run_balanced_path(profile, balanced_paths()[0])]
    )
    economics = aggregate["economics"]
    assert economics["added_2bps_cost_usdt"] >= 0
    assert "pnl_after_top_10pct_removal_usdt" in economics
    assert _remove_top([1.0] * 9 + [10.0], 0.10) == 9.0
    assert "average_5_tick_markout_usdt" in economics


def test_no_extensions_or_forbidden_access():
    spec = build_specification(ROOT)
    assert spec["profile_extension"] is None
    assert spec["balanced_path_extension"] is None
    assert spec["stress_path_extension"] is None
    assert not any((
        spec["optimization"], spec["validation_opened"], spec["holdout_opened"],
        spec["external_access"], spec["git_write_operation"],
    ))
    for filename in ("backtest/mm_v1_3_protocol.py", "backtest/mm_v1_3_smoke.py"):
        tree = ast.parse((ROOT / filename).read_text())
        imports = [
            alias.name for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        assert not any("optuna" in name.lower() for name in imports)
