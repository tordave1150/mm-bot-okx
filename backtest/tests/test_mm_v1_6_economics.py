"""Pre-freeze tests for MM v1.6 economic viability."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from backtest.mm_v1_3_protocol import (
    balanced_paths as v13_balanced_paths,
)
from backtest.mm_v1_3_smoke import aggregate_profile, run_balanced_path
from backtest.mm_v1_5_protocol import profiles as v15_profiles
from backtest.mm_v1_6_economics import STATUSES, _primary_gate_details
from backtest.mm_v1_6_protocol import (
    BALANCED_MODEL,
    PROTOCOL_ID,
    STRESS_MODEL,
    balanced_paths,
    balanced_trigger_audit,
    build_spec,
    path_disjointness,
    profiles,
    stress_paths,
)


ROOT = Path(__file__).resolve().parents[2]


def test_root_agents_controls_v16_process():
    first = (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0]
    original = "# AGENTS.md — Market Maker v1.6 Economic Viability Before Optimization"
    if first == original:
        return
    allowed_successors = {
        "# AGENTS.md — OKX Demo Execution Safety Before Production": (
            "AGENTS_OKX_DEMO_EXECUTION_SAFETY.md"
        ),
        "# AGENTS.md - OKX Demo Fill and Restart Validation": (
            "AGENTS_OKX_DEMO_FILL_RESTART_VALIDATION.md"
        ),
    }
    assert first in allowed_successors
    assert (ROOT / "AGENTS.md").read_bytes() == (
        ROOT / allowed_successors[first]
    ).read_bytes()
    pre_activation = json.loads((
        ROOT / "artifacts/okx_production_readiness/"
        "audit_20260801T170923Z/readiness.json"
    ).read_text(encoding="utf-8"))
    assert pre_activation["closed_evidence"]["source_hash_mismatches"] == []


def test_exact_fixed_profile_names():
    assert [row["profile_name"] for row in profiles()] == [
        "COMPOSITE_V1_5_CONTROL",
        "FEE_AWARE_SPREAD_6",
        "WIDER_SPREAD_8",
        "WIDER_SPREAD_10",
        "WIDER_SPREAD_INVENTORY_SKEW",
        "WIDER_SPREAD_LOWER_CHURN",
        "WIDER_SPREAD_HIGHER_RISK_AVERSION",
        "ECONOMIC_COMPOSITE",
    ]


def test_profiles_are_fixed_unique_and_carry_exact_v15_overlay():
    matrix = profiles()
    prior = v15_profiles()[7]["defensive_overlay"]
    assert len(matrix) == 8
    assert len({row["profile_fingerprint"] for row in matrix}) == 8
    assert all(not row["adaptive"] for row in matrix)
    assert all(row["defensive_overlay"] == prior for row in matrix)
    assert all(row["v1_5_overlay_exact"] for row in matrix)


def test_risk_and_capital_controls_do_not_drift():
    control = profiles()[0]["parameters"]
    for profile in profiles():
        parameters = profile["parameters"]
        for key in (
            "fixed_lot_size_btc",
            "maximum_inventory_lots",
            "maximum_margin_utilization",
            "soft_session_loss_pct",
            "hard_kill_drawdown_pct",
            "maker_fee_rate",
            "taker_fee_rate",
        ):
            assert parameters[key] == control[key]


def test_fresh_matrix_cardinality_and_disjointness():
    assert len(balanced_paths()) == 12
    assert len(stress_paths()) == 16
    assert path_disjointness()["passed"]
    assert all(
        sum(row["severity"] == severity for row in stress_paths()) == 4
        for severity in ("S2_5", "S3_LOW", "S3_MID", "S3_HIGH")
    )


def test_balanced_shocks_stay_below_defensive_trigger():
    audit = balanced_trigger_audit()
    assert audit["defensive_shock_overlay_inactive_by_construction"]
    assert audit["maximum_causal_move_bps"] < 4.5


def test_closed_v15_attribution_is_complete_and_reconciled():
    path = (
        ROOT
        / "artifacts/mm_v1_6_economic_viability"
        / "closed_evidence_20260801T065835Z"
    )
    assert json.loads((path / "COMPLETED.json").read_text())["status"] == "COMPLETED"
    audit = json.loads((path / "closed_v1_5_attribution.json").read_text())
    assert audit["execution_attribution"]["accounting_reconciles"]
    assert not audit["drawdown_guard"][
        "emergency_cost_alone_explains_total_loss"
    ]


def test_old_balanced_engineering_fixture_favors_wider_spread():
    old_path = v13_balanced_paths()[0]
    control_profile = profiles()[0]
    wider_profile = profiles()[2]
    control_runs = [run_balanced_path(control_profile, old_path)]
    wider_runs = [run_balanced_path(wider_profile, old_path)]
    control = aggregate_profile(control_profile, control_runs)
    wider = aggregate_profile(wider_profile, wider_runs)
    assert wider["economics"]["net_pnl_usdt"] > control["economics"][
        "net_pnl_usdt"
    ]
    assert wider["integrity"]["passed"]


def test_primary_gate_details_are_fail_closed():
    old_path = v13_balanced_paths()[0]
    profile = profiles()[2]
    aggregate = aggregate_profile(profile, [run_balanced_path(profile, old_path)])
    details = _primary_gate_details(aggregate, 1.0)
    assert set(details) >= {
        "net_pnl",
        "profit_factor",
        "pnl_after_added_2bps",
        "pnl_after_top_10pct_removal",
        "activity_retention",
        "passed",
    }
    assert not details["passed"]  # One path cannot satisfy scenario breadth.


def test_fill_models_are_separated_and_no_optimization_is_opened():
    spec = build_spec(ROOT)
    assert spec["primary_fill_model"] == BALANCED_MODEL
    assert spec["strict_stress_fill_model"] == STRESS_MODEL
    assert not any((
        spec["optimization"],
        spec["validation_opened"],
        spec["holdout_opened"],
        spec["external_access"],
        spec["git_write_operation"],
        spec["prior_matrix_reruns"],
        spec["production_defaults_changed"],
    ))


def test_status_contract_is_closed():
    assert STATUSES == (
        "MM_V1_6_EVIDENCE_FAILED",
        "MM_V1_6_SAFETY_FAILED",
        "MM_V1_6_ACTIVITY_INSUFFICIENT",
        "MM_V1_6_CAUSAL_REENTRY_FAILED",
        "MM_V1_6_ECONOMIC_GATES_FAILED",
        "MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT",
    )


def test_protocol_and_executor_have_no_forbidden_imports():
    for name in (
        "backtest/mm_v1_6_attribution.py",
        "backtest/mm_v1_6_protocol.py",
        "backtest/mm_v1_6_economics.py",
    ):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
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
    assert PROTOCOL_ID.startswith("MM_V1_6_")
