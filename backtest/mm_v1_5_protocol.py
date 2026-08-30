"""Frozen MM v1.5 drawdown repair protocol.

The matrix is deliberately small and fixed.  It isolates the causal mechanisms
identified in the closed v1.4 evidence and does not perform parameter search.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from backtest.mm_v1_3c_protocol import (
    CAPITAL,
    SEVERITY as V13C_SEVERITY,
    profiles as v13c_profiles,
)
from backtest.mm_v1_4_protocol import ticks as segmented_ticks


PROTOCOL_ID = "MM_V1_5_TIMEBOXED_DRAWDOWN_REPAIR_20260729"
FILL_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
SEVERITY = deepcopy(V13C_SEVERITY)
SCENARIOS = (
    "upward_toxic_trend",
    "downward_toxic_trend",
    "gap_through_quote",
    "delayed_cancel_one_sided_flow",
)
STREAMS = (
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
    "fill_classification.py",
    "fill_tracker.py",
    "backtest/matching_engine.py",
    "backtest/mm_runner.py",
    "backtest/mm_v1_3c_evidence.py",
    "backtest/mm_v1_3c_repair.py",
    "backtest/mm_v1_3d_reentry.py",
    "backtest/mm_v1_4_protocol.py",
    "backtest/mm_v1_5_protocol.py",
    "backtest/mm_v1_5_drawdown.py",
    "market_maker/as_config.py",
    "market_maker/margin.py",
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


def _timeboxed_overlay() -> dict[str, Any]:
    return {
        "one_sided_defensive": True,
        "shock_threshold_bps": 4.5,
        "one_sided_max_ticks": 3,
        "one_sided_cooldown_ticks": 2,
    }


def _confirmed_overlay() -> dict[str, Any]:
    return {
        **_timeboxed_overlay(),
        "one_sided_hold_until_reentry_confirmation": True,
        "one_sided_reentry_threshold_bps": 4.2,
        "one_sided_reentry_confirmation_ticks": 2,
    }


def profiles() -> list[dict[str, Any]]:
    """Return the exact predeclared eight-profile ablation matrix."""
    baseline = deepcopy(v13c_profiles()[0]["parameters"])
    definitions = (
        ("BASELINE_CONTROL", {}),
        ("TIMEBOXED_V1_4_CONTROL", _timeboxed_overlay()),
        (
            "EXTENDED_TIMEBOX_6",
            {
                **_timeboxed_overlay(),
                "one_sided_max_ticks": 6,
            },
        ),
        ("REVERSAL_CONFIRMED_REENTRY_2", _confirmed_overlay()),
        (
            "INVENTORY_AWARE_REENTRY",
            {
                **_timeboxed_overlay(),
                "inventory_aware_reentry": True,
            },
        ),
        (
            "CONFIRMED_INVENTORY_AWARE",
            {
                **_confirmed_overlay(),
                "inventory_aware_reentry": True,
            },
        ),
        (
            "TIMEBOXED_DRAWDOWN_GUARD_3PCT",
            {
                **_timeboxed_overlay(),
                "drawdown_guard_pct": 0.03,
                "drawdown_guard_slippage_bps": 10.0,
            },
        ),
        (
            "COMPOSITE_DRAWDOWN_REPAIR",
            {
                **_confirmed_overlay(),
                "inventory_aware_reentry": True,
                "drawdown_guard_pct": 0.03,
                "drawdown_guard_slippage_bps": 10.0,
            },
        ),
    )
    output: list[dict[str, Any]] = []
    for index, (name, overlay) in enumerate(definitions, start=1):
        output.append({
            "profile_id": f"mm-v1-5-profile-{index:02d}",
            "profile_name": name,
            "parameters": deepcopy(baseline),
            "defensive_overlay": deepcopy(overlay),
            "profile_fingerprint": digest({
                "protocol_id": PROTOCOL_ID,
                "profile_name": name,
                "parameters": baseline,
                "defensive_overlay": overlay,
            }),
            "adaptive": False,
        })
    return output


def ticks(path: dict[str, Any], count: int = 240) -> list[dict[str, Any]]:
    """Reuse the audited three-phase generator with fresh v1.5 seeds."""
    return segmented_ticks(path, count=count)


def paths() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    counter = 0
    for severity, parameters in SEVERITY.items():
        for scenario in SCENARIOS:
            counter += 1
            row = {
                "path_id": (
                    f"mm-v1-5-{severity.lower()}-{counter:02d}-{scenario}"
                ),
                "severity": severity,
                "scenario": scenario,
                "parameters": deepcopy(parameters),
                "market_seed": 510_000 + counter,
                "fill_seed": 520_000 + counter,
                "source_block": (
                    f"v1-5-prelude-stress-recovery-{counter:02d}-"
                    f"{severity.lower()}-{scenario}"
                ),
                "generator_version": "mm-v1-5-segmented-v1",
                "index_range": [0, 239],
                "segments": {
                    "NORMAL_PRELUDE": [0, 59],
                    "STRESS": [60, 179],
                    "RECOVERY": [180, 239],
                },
            }
            row["path_hash"] = digest(ticks(row))
            output.append(row)
    return output


def path_disjointness() -> dict[str, bool]:
    from backtest.mm_v1_3c_protocol import paths as v13c_paths
    from backtest.mm_v1_4_protocol import paths as v14_paths

    old = [*v13c_paths(), *v14_paths()]
    current = paths()
    checks = {
        "ids": not (
            {row["path_id"] for row in old}
            & {row["path_id"] for row in current}
        ),
        "hashes": not (
            {row["path_hash"] for row in old}
            & {row["path_hash"] for row in current}
        ),
        "market_seeds": not (
            {row["market_seed"] for row in old}
            & {row["market_seed"] for row in current}
        ),
        "fill_seeds": not (
            {row["fill_seed"] for row in old}
            & {row["fill_seed"] for row in current}
        ),
        "source_blocks": not (
            {row["source_block"] for row in old}
            & {row["source_block"] for row in current}
        ),
    }
    checks["passed"] = all(checks.values())
    return checks


def build_spec(root: Path) -> dict[str, Any]:
    return {
        "schema_version": "mm-v1-5-timeboxed-drawdown-repair-v1",
        "protocol_id": PROTOCOL_ID,
        "fill_model": FILL_MODEL,
        "hypothesis": (
            "Periodic two-sided probes reopen adverse inventory while a toxic "
            "trend persists. Causal reentry confirmation, inventory-aware "
            "reentry, and a fee-inclusive capital-preservation guard should "
            "reduce drawdown without erasing normal activity."
        ),
        "profiles": profiles(),
        "capital_policy": {
            "capital_usdt": CAPITAL,
            "lot_size_btc": 0.01,
            "leverage": 3,
            "maximum_margin_utilization": 0.80,
            "maximum_inventory_btc": 0.01,
            "soft_session_loss": 0.03,
            "hard_kill_drawdown": 0.05,
            "risk_parameters_unchanged": True,
        },
        "severity_ladder": deepcopy(SEVERITY),
        "paths": paths(),
        "path_disjointness": path_disjointness(),
        "activity_floor": {
            "severities": ["S2_5", "S3_LOW"],
            "normal_strict_maker_fills_min": 8,
            "stress_normal_fills_min": 4,
            "recovery_normal_fills_min": 2,
            "both_normal_fill_sides_required": True,
            "normal_fifo_round_trips_min": 2,
            "represented_scenario_families_min": 3,
            "two_sided_quote_rate_min": 0.05,
            "no_quote_rate_max": 0.85,
            "normal_fill_retention_vs_timeboxed_min": 0.70,
        },
        "reentry_requirements": {
            "causal_event_sequence_required": True,
            "normal_fill_before_first_activation": True,
            "normal_fill_after_completed_reentry": True,
            "defensive_mode_exit_min": 1,
            "recovery_normal_fills_min": 2,
            "not_permanently_one_sided": True,
            "confirmation_must_not_use_future_ticks": True,
        },
        "resilience_budget": {
            "S2_5": {"hard_kills": 0, "worst_drawdown_max": 0.035},
            "S3_LOW": {"hard_kills": 0, "worst_drawdown_max": 0.040},
            "S3_MID": {"hard_kills": 0, "worst_drawdown_max": 0.050},
            "S3_HIGH": {"boundary_diagnostic_only": True},
        },
        "optuna_unlock_gate": {
            "required_severity_budgets": ["S2_5", "S3_LOW", "S3_MID"],
            "activity_floor_required": True,
            "causal_reentry_required": True,
            "accounting_reconciliation_required": True,
            "this_protocol_executes_optuna": False,
        },
        "selection_policy": [
            "semantic_integrity_and_safety",
            "all_three_required_severity_budgets",
            "activity_and_retention",
            "causal_reentry",
            "lowest_worst_drawdown",
            "highest_net_pnl",
        ],
        "mandatory_streams": list(STREAMS),
        "prior_matrix_reruns": False,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
        "source_hashes": source_hashes(root),
    }
