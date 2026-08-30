"""Frozen design for MM v1.6 economic viability before optimization."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from backtest.mm_v1_3_protocol import (
    BALANCED_SCENARIOS,
    balanced_paths as v13_balanced_paths,
    balanced_ticks as v13_balanced_ticks,
)
from backtest.mm_v1_3c_protocol import (
    SEVERITY as V13C_SEVERITY,
    paths as v13c_stress_paths,
)
from backtest.mm_v1_4_protocol import paths as v14_stress_paths
from backtest.mm_v1_5_protocol import (
    SCENARIOS as STRESS_SCENARIOS,
    paths as v15_stress_paths,
    profiles as v15_profiles,
    ticks as segmented_stress_ticks,
)
from market_maker.as_config import MarketMakerV1Config


PROTOCOL_ID = "MM_V1_6_ECONOMIC_VIABILITY_20260801"
BALANCED_MODEL = "BALANCED_CAUSAL_FILL_MODEL"
STRESS_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
CAPITAL = 750.0
ADDED_COST_BPS = 2.0
SEVERITY = deepcopy(V13C_SEVERITY)
BALANCED_STREAMS = (
    "market_events.jsonl",
    "trade_events.jsonl",
    "quote_events.jsonl",
    "quote_decisions.jsonl",
    "margin_events.jsonl",
    "order_events.jsonl",
    "fills.jsonl",
    "round_trips.jsonl",
    "markouts.jsonl",
    "profile_path_results.jsonl",
)
STRESS_STREAMS = (
    "market_events.jsonl",
    "trade_events.jsonl",
    "quote_events.jsonl",
    "defensive_events.jsonl",
    "margin_events.jsonl",
    "order_events.jsonl",
    "fills.jsonl",
    "round_trips.jsonl",
    "markouts.jsonl",
    "hard_kill_events.jsonl",
    "path_results.jsonl",
)
SOURCE_FILES = (
    "AGENTS.md",
    "fill_classification.py",
    "fill_tracker.py",
    "market_maker/as_config.py",
    "market_maker/as_strategy.py",
    "market_maker/execution_accounting.py",
    "market_maker/margin.py",
    "backtest/matching_engine.py",
    "backtest/mm_execution_microstructure.py",
    "backtest/mm_runner.py",
    "backtest/mm_v1_3_protocol.py",
    "backtest/mm_v1_3_smoke.py",
    "backtest/mm_v1_3c_evidence.py",
    "backtest/mm_v1_3c_repair.py",
    "backtest/mm_v1_4_targeted.py",
    "backtest/mm_v1_5_protocol.py",
    "backtest/mm_v1_5_drawdown.py",
    "backtest/mm_v1_6_attribution.py",
    "backtest/mm_v1_6_protocol.py",
    "backtest/mm_v1_6_economics.py",
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    return {name: file_hash(root / name) for name in SOURCE_FILES}


def closed_evidence_hashes(root: Path) -> dict[str, str]:
    paths = (
        "artifacts/mm_v1_3_balanced_causal_smoke/decision_20260728T162056Z/decision.json",
        "artifacts/mm_v1_5_timeboxed_drawdown_repair/decision_20260729T162630Z/decision.json",
        "artifacts/mm_v1_6_economic_viability/closed_evidence_20260801T065835Z/closed_v1_5_attribution.json",
    )
    return {name: file_hash(root / name) for name in paths}


def profiles() -> list[dict[str, Any]]:
    prior = v15_profiles()[7]
    base_parameters = deepcopy(prior["parameters"])
    overlay = deepcopy(prior["defensive_overlay"])
    definitions = (
        ("COMPOSITE_V1_5_CONTROL", {}),
        ("FEE_AWARE_SPREAD_6", {
            "minimum_half_spread_bps": 6.0,
        }),
        ("WIDER_SPREAD_8", {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
        }),
        ("WIDER_SPREAD_10", {
            "minimum_half_spread_bps": 10.0,
            "maximum_half_spread_bps": 40.0,
        }),
        ("WIDER_SPREAD_INVENTORY_SKEW", {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
            "inventory_skew_strength": 1.5,
        }),
        ("WIDER_SPREAD_LOWER_CHURN", {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
            "minimum_order_lifetime_ticks": 3,
            "maximum_order_age_ticks": 12,
            "requote_threshold_ticks": 4,
        }),
        ("WIDER_SPREAD_HIGHER_RISK_AVERSION", {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
            "risk_aversion_gamma": 0.12,
        }),
        ("ECONOMIC_COMPOSITE", {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
            "inventory_skew_strength": 1.5,
            "risk_aversion_gamma": 0.12,
            "minimum_order_lifetime_ticks": 3,
            "maximum_order_age_ticks": 12,
            "requote_threshold_ticks": 4,
        }),
    )
    output: list[dict[str, Any]] = []
    for index, (name, overrides) in enumerate(definitions, start=1):
        parameters = {**base_parameters, **overrides}
        config = MarketMakerV1Config(**parameters)
        config.validate()
        output.append({
            "profile_id": f"mm-v1-6-profile-{index:02d}",
            "profile_name": name,
            "parameters": parameters,
            "defensive_overlay": deepcopy(overlay),
            "v1_5_overlay_exact": overlay == prior["defensive_overlay"],
            "profile_fingerprint": digest({
                "protocol_id": PROTOCOL_ID,
                "profile_name": name,
                "parameters": parameters,
                "defensive_overlay": overlay,
            }),
            "adaptive": False,
        })
    return output


def balanced_ticks(path: dict[str, Any]) -> list[dict[str, Any]]:
    return v13_balanced_ticks(
        path["scenario"],
        path["market_seed"],
        path["aggressor_flow_seed"],
        path["parameters"]["tick_count"],
    )


def balanced_paths() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, scenario in enumerate(BALANCED_SCENARIOS, start=1):
        row = {
            "path_id": f"mm-v1-6-balanced-{index:02d}-{scenario}",
            "scenario": scenario,
            "parameters": {"tick_count": 180},
            "market_seed": 610_000 + index,
            "aggressor_flow_seed": 620_000 + index,
            "fill_seed": 630_000 + index,
            "source_block": f"v1-6-balanced-{index:02d}-{scenario}",
            "generator_version": "mm-v1-6-balanced-causal-v1",
            "index_range": [0, 179],
        }
        row["path_hash"] = digest(balanced_ticks(row))
        output.append(row)
    return output


def stress_ticks(path: dict[str, Any]) -> list[dict[str, Any]]:
    return segmented_stress_ticks(path)


def stress_paths() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    counter = 0
    for severity, parameters in SEVERITY.items():
        for scenario in STRESS_SCENARIOS:
            counter += 1
            row = {
                "path_id": (
                    f"mm-v1-6-stress-{severity.lower()}-{counter:02d}-"
                    f"{scenario}"
                ),
                "severity": severity,
                "scenario": scenario,
                "parameters": deepcopy(parameters),
                "market_seed": 640_000 + counter,
                "fill_seed": 650_000 + counter,
                "source_block": (
                    f"v1-6-stress-{counter:02d}-{severity.lower()}-"
                    f"{scenario}"
                ),
                "generator_version": "mm-v1-6-segmented-stress-v1",
                "index_range": [0, 239],
                "segments": {
                    "NORMAL_PRELUDE": [0, 59],
                    "STRESS": [60, 179],
                    "RECOVERY": [180, 239],
                },
            }
            row["path_hash"] = digest(stress_ticks(row))
            output.append(row)
    return output


def path_disjointness() -> dict[str, Any]:
    old_balanced = v13_balanced_paths()
    new_balanced = balanced_paths()
    old_stress = [
        *v13c_stress_paths(),
        *v14_stress_paths(),
        *v15_stress_paths(),
    ]
    new_stress = stress_paths()

    def checks(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, bool]:
        result = {
            "ids": not (
                {row["path_id"] for row in old}
                & {row["path_id"] for row in new}
            ),
            "hashes": not (
                {row["path_hash"] for row in old}
                & {row["path_hash"] for row in new}
            ),
            "market_seeds": not (
                {row["market_seed"] for row in old}
                & {row["market_seed"] for row in new}
            ),
            "fill_seeds": not (
                {row.get("fill_seed") for row in old}
                & {row.get("fill_seed") for row in new}
            ),
            "source_blocks": not (
                {row["source_block"] for row in old}
                & {row["source_block"] for row in new}
            ),
        }
        result["passed"] = all(result.values())
        return result

    balanced = checks(old_balanced, new_balanced)
    stress = checks(old_stress, new_stress)
    return {
        "balanced": balanced,
        "stress": stress,
        "passed": balanced["passed"] and stress["passed"],
    }


def balanced_trigger_audit() -> dict[str, Any]:
    worst_move = 0.0
    for path in balanced_paths():
        generated = balanced_ticks(path)
        for prior, current in zip(generated, generated[1:]):
            move = abs(float(current["mid"]) / float(prior["mid"]) - 1) * 10_000
            worst_move = max(worst_move, move)
    return {
        "maximum_causal_move_bps": worst_move,
        "one_sided_shock_threshold_bps": 4.5,
        "defensive_shock_overlay_inactive_by_construction": worst_move < 4.5,
        "drawdown_guard_requires_runtime_drawdown_audit": True,
    }


def build_spec(root: Path) -> dict[str, Any]:
    stress_budgets = {
        "S2_5": {"hard_kills": 0, "worst_drawdown_max": 0.035},
        "S3_LOW": {"hard_kills": 0, "worst_drawdown_max": 0.040},
        "S3_MID": {"hard_kills": 0, "worst_drawdown_max": 0.050},
        "S3_HIGH": {"boundary_diagnostic_only": True},
    }
    return {
        "schema_version": "mm-v1-6-economic-viability-v1",
        "protocol_id": PROTOCOL_ID,
        "primary_fill_model": BALANCED_MODEL,
        "strict_stress_fill_model": STRESS_MODEL,
        "capital_policy": {
            "capital_usdt": CAPITAL,
            "leverage": 3,
            "lot_size_btc": 0.01,
            "maximum_inventory_btc": 0.01,
            "maximum_margin_utilization": 0.80,
            "soft_session_loss": 0.03,
            "hard_kill_drawdown": 0.05,
        },
        "profiles": profiles(),
        "balanced_paths": balanced_paths(),
        "stress_paths": stress_paths(),
        "severity_ladder": deepcopy(SEVERITY),
        "path_disjointness": path_disjointness(),
        "balanced_trigger_audit": balanced_trigger_audit(),
        "primary_activity_gates": {
            "normal_maker_fills_min": 50,
            "bid_fills_min": 15,
            "ask_fills_min": 15,
            "normal_fifo_round_trips_min": 10,
            "represented_scenarios_min": 4,
            "retention_vs_control_min": 0.80,
        },
        "primary_economic_gates": {
            "net_pnl_gt": 0,
            "expectancy_gt": 0,
            "profit_factor_gt": 1.0,
            "net_realized_spread_gt": 0,
            "gross_execution_gt_total_fees": True,
            "added_cost_bps": ADDED_COST_BPS,
            "pnl_after_added_cost_gt": 0,
            "pnl_after_top_10pct_removal_gt": 0,
            "average_5_tick_markout_min": -0.05,
        },
        "stress_activity_gates": {
            "severities": ["S2_5", "S3_LOW"],
            "normal_fill_retention_vs_control_min": 0.70,
            "stress_normal_fills_min": 4,
            "recovery_normal_fills_min": 2,
            "both_sides_required": True,
            "normal_fifo_round_trips_min": 2,
            "scenario_families_min": 3,
            "two_sided_quote_rate_min": 0.05,
            "no_quote_rate_max": 0.85,
        },
        "stress_budgets": deepcopy(stress_budgets),
        # Compatibility alias consumed by the closed v1.3C severity analyzer.
        "resilience_budget": deepcopy(stress_budgets),
        "causal_reentry_requirements": {
            "normal_fill_before_first_activation": True,
            "normal_fill_after_completed_reentry": True,
            "defensive_mode_exit_min": 1,
            "not_permanently_one_sided": True,
        },
        "selection_policy": [
            "all_gates_passed",
            "pnl_after_added_2bps",
            "pnl_after_top_10pct_removal",
            "net_pnl",
            "profit_factor",
            "activity_retention",
            "lower_drawdown",
        ],
        "balanced_streams": list(BALANCED_STREAMS),
        "stress_streams": list(STRESS_STREAMS),
        "closed_evidence_hashes": closed_evidence_hashes(root),
        "source_hashes": source_hashes(root),
        "formal_balanced_execution_count": 1,
        "formal_stress_execution_count": 1,
        "prior_matrix_reruns": False,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
    }
