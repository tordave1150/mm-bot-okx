"""Frozen MM v1.3C evidence-repair protocol and fresh stress matrix."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from backtest.mm_v1_3b_protocol import (
    OVERLAYS as V13B_OVERLAYS,
    SEVERITY as V13B_SEVERITY,
    paths as v13b_paths,
    profiles as v13b_profiles,
)


PROTOCOL_ID = "MM_V1_3C_FILL_TRIGGER_ACTIVITY_REPAIR_20260729"
SCHEMA_VERSION = "mm-v1-3c-evidence-repair-v1"
CAPITAL = 750.0
FILL_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
SEVERITY = deepcopy(V13B_SEVERITY)
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
    "backtest/mm_v1_3a_diagnostic.py",
    "backtest/mm_v1_3b_protocol.py",
    "backtest/mm_v1_3c_evidence.py",
    "backtest/mm_v1_3c_protocol.py",
    "backtest/mm_v1_3c_repair.py",
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


def profiles() -> list[dict[str, Any]]:
    """Carry forward exact v1.3B vectors with new identities."""
    prior = v13b_profiles()
    output: list[dict[str, Any]] = []
    for index, old in enumerate(prior, start=1):
        parameters = deepcopy(old["parameters"])
        overlay = deepcopy(old["defensive_overlay"])
        output.append({
            "profile_id": f"mm-v1-3c-profile-{index:02d}",
            "profile_name": old["profile_name"],
            "prior_profile_reference": old["profile_id"],
            "prior_profile_fingerprint": old["profile_fingerprint"],
            "parameters": parameters,
            "defensive_overlay": overlay,
            "changed_fields": deepcopy(old["changed_fields"]),
            "unchanged_safety": True,
            "profile_fingerprint": digest({
                "protocol_id": PROTOCOL_ID,
                "profile_name": old["profile_name"],
                "parameters": parameters,
                "defensive_overlay": overlay,
            }),
        })
    return output


def profile_carry_forward_exact() -> bool:
    prior = v13b_profiles()
    carried = profiles()
    return (
        len(prior) == len(carried) == len(V13B_OVERLAYS) == 8
        and all(
            new["profile_name"] == old["profile_name"]
            and new["parameters"] == old["parameters"]
            and new["defensive_overlay"] == old["defensive_overlay"]
            for old, new in zip(prior, carried, strict=True)
        )
    )


def ticks(path: dict[str, Any], count: int = 180) -> list[dict[str, Any]]:
    params = path["parameters"]
    rng = random.Random(path["market_seed"])
    mid = 50_000.0
    output: list[dict[str, Any]] = []
    for index in range(count):
        trend = params["trend_bps"]
        scenario = path["scenario"]
        if scenario == "downward_toxic_trend":
            move = -trend
        elif scenario == "gap_through_quote":
            move = -params["gap_bps"] if index in {60, 120} else trend * 0.25
        elif scenario == "delayed_cancel_one_sided_flow":
            move = -trend if index % 14 < 10 else trend * 1.5
        else:
            move = trend
        move += (
            rng.uniform(-0.12, 0.12) * params["volatility_multiplier"]
        )
        mid *= 1 + move / 10_000
        half_spread = mid / 10_000
        output.append({
            "bids": [[round(mid - half_spread, 1), 1.0]],
            "asks": [[round(mid + half_spread, 1), 1.0]],
            "timestamp": (
                3_100_000_000_000 + path["market_seed"] + index * 300_000
            ),
        })
    return output


def paths() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    counter = 0
    for level, parameters in SEVERITY.items():
        for scenario in SCENARIOS:
            counter += 1
            row = {
                "path_id": (
                    f"mm-v1-3c-{level.lower()}-{counter:02d}-{scenario}"
                ),
                "severity": level,
                "scenario": scenario,
                "parameters": deepcopy(parameters),
                "market_seed": 310_000 + counter,
                "fill_seed": 320_000 + counter,
                "source_block": (
                    f"v1-3c-fresh-{counter:02d}-{level.lower()}-{scenario}"
                ),
                "generator_version": "mm-v1-3c-refined-v1",
                "index_range": [0, 179],
            }
            row["path_hash"] = digest(ticks(row))
            output.append(row)
    return output


def path_disjointness() -> dict[str, Any]:
    prior = v13b_paths()
    current = paths()
    checks = {
        "path_ids_disjoint": not (
            {row["path_id"] for row in prior}
            & {row["path_id"] for row in current}
        ),
        "path_hashes_disjoint": not (
            {row["path_hash"] for row in prior}
            & {row["path_hash"] for row in current}
        ),
        "market_seeds_disjoint": not (
            {row["market_seed"] for row in prior}
            & {row["market_seed"] for row in current}
        ),
        "fill_seeds_disjoint": not (
            {row["fill_seed"] for row in prior}
            & {row["fill_seed"] for row in current}
        ),
        "source_blocks_disjoint": not (
            {row["source_block"] for row in prior}
            & {row["source_block"] for row in current}
        ),
        "exact_four_paths_per_severity": all(
            sum(row["severity"] == severity for row in current) == 4
            for severity in SEVERITY
        ),
    }
    checks["passed"] = all(checks.values())
    return checks


def build_spec(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "fill_model": FILL_MODEL,
        "canonical_trigger_taxonomy": [
            "STRICT_TRADE_THROUGH",
            "AGGRESSOR_TRADE_AT_QUOTE",
            "TERMINAL_EXECUTION",
            "HARD_KILL_EXECUTION",
            "EMERGENCY_EXECUTION",
        ],
        "profiles": profiles(),
        "profile_carry_forward_exact": profile_carry_forward_exact(),
        "capital_policy": {
            "capital_usdt": CAPITAL,
            "lot_size_btc": 0.01,
            "leverage": 3.0,
            "maximum_margin_utilization": 0.80,
            "maximum_inventory_btc": 0.01,
            "soft_session_loss": 0.03,
            "hard_kill_drawdown": 0.05,
        },
        "severity_ladder": deepcopy(SEVERITY),
        "paths": paths(),
        "path_disjointness": path_disjointness(),
        "activity_floor": {
            "normal_strict_maker_fills_min": 8,
            "both_normal_fill_sides_required": True,
            "normal_fifo_round_trips_min": 2,
            "quote_eligible_ticks_gt": 0,
            "two_sided_quote_rate_min": 0.05,
            "no_quote_rate_max": 0.85,
            "represented_scenario_families_min": 3,
            "severities": ["S2_5", "S3_LOW"],
        },
        "reentry_requirements": {
            "normal_fill_before_first_defensive_activation": True,
            "normal_fill_after_completed_reentry": True,
            "defensive_mode_exits_min": 1,
            "not_paused_entire_run": True,
            "not_permanently_one_sided": True,
            "one_sided_additional": {
                "both_normal_fill_sides": True,
                "mode_enters_and_exits": True,
                "activity_floor": True,
                "represented_scenario_families_min": 3,
            },
            "composite_additional": {
                "normal_fills_at_S2_5_gt": 0,
                "normal_fills_at_S3_LOW_gt": 0,
                "normal_fifo_round_trips_min": 2,
                "completed_reentry": True,
                "no_quote_rate_max": 0.85,
            },
        },
        "resilience_budget": {
            "S2_5": {"hard_kills": 0, "worst_drawdown_max": 0.035},
            "S3_LOW": {"hard_kills": 0, "worst_drawdown_max": 0.040},
            "S3_MID": {"hard_kills": 0, "worst_drawdown_max": 0.050},
            "S3_HIGH": {"boundary_diagnostic_only": True},
            "activity_floor_required": True,
            "reentry_required": True,
            "no_unexplained_hard_kill": True,
            "no_catastrophic_terminal_loss": True,
            "markout_improves_versus_baseline": True,
        },
        "mandatory_streams": list(STREAMS),
        "profile_extension": None,
        "path_extension": None,
        "balanced_v1_3_matrix_rerun": False,
        "stress_v1_3a_matrix_rerun": False,
        "repair_v1_3b_matrix_rerun": False,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
        "source_hashes": source_hashes(root),
    }
