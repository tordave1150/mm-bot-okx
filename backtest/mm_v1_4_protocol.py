"""Frozen targeted defensive redesign with normal/stress/recovery paths."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from backtest.mm_v1_3c_protocol import (
    CAPITAL,
    SEVERITY as V13C_SEVERITY,
    profiles as v13c_profiles,
)


PROTOCOL_ID = "MM_V1_4_TARGETED_CAUSAL_DEFENSE_20260729"
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
    "backtest/mm_v1_4_targeted.py",
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
    prior = v13c_profiles()
    baseline = deepcopy(prior[0]["parameters"])
    wider = deepcopy(prior[1]["parameters"])
    definitions = (
        ("BASELINE_CONTROL", baseline, {}),
        ("WIDER_SPREAD_CONTROL", wider, {}),
        (
            "EXISTING_FAST_CANCEL_CONTROL",
            baseline,
            {"fast_cancel": True, "shock_threshold_bps": 4.5},
        ),
        (
            "SIDE_SPECIFIC_FAST_CANCEL",
            baseline,
            {
                "fast_cancel": True,
                "shock_threshold_bps": 4.5,
                "fast_cancel_scope": "adverse_side",
            },
        ),
        (
            "FAST_CANCEL_HYSTERESIS",
            baseline,
            {
                "fast_cancel": True,
                "shock_threshold_bps": 4.5,
                "fast_cancel_scope": "adverse_side",
                "fast_cancel_exit_threshold_bps": 4.2,
                "fast_cancel_cooldown_ticks": 2,
            },
        ),
        (
            "CAUSAL_TWO_HIT_TOXIC_PAUSE",
            baseline,
            {
                "toxic_pause_ticks": 2,
                "toxic_fill_streak_required": 2,
                "toxic_requires_adverse_fill": True,
            },
        ),
        (
            "TIMEBOXED_ONE_SIDED_DEFENSE",
            baseline,
            {
                "one_sided_defensive": True,
                "shock_threshold_bps": 4.5,
                "one_sided_max_ticks": 3,
                "one_sided_cooldown_ticks": 2,
            },
        ),
        (
            "COMPOSITE_LIGHT",
            baseline,
            {
                "fast_cancel": True,
                "shock_threshold_bps": 4.5,
                "fast_cancel_scope": "adverse_side",
                "fast_cancel_exit_threshold_bps": 4.2,
                "fast_cancel_cooldown_ticks": 2,
                "toxic_pause_ticks": 2,
                "toxic_fill_streak_required": 2,
                "toxic_requires_adverse_fill": True,
                "inventory_reduction_priority": True,
            },
        ),
    )
    output: list[dict[str, Any]] = []
    for index, (name, parameters, overlay) in enumerate(
        definitions, start=1
    ):
        output.append({
            "profile_id": f"mm-v1-4-profile-{index:02d}",
            "profile_name": name,
            "prior_profile_reference": (
                prior[1]["profile_id"]
                if name == "WIDER_SPREAD_CONTROL"
                else prior[0]["profile_id"]
            ),
            "parameters": deepcopy(parameters),
            "defensive_overlay": deepcopy(overlay),
            "profile_fingerprint": digest({
                "protocol_id": PROTOCOL_ID,
                "profile_name": name,
                "parameters": parameters,
                "defensive_overlay": overlay,
            }),
            "adaptive": False,
        })
    return output


def ticks(path: dict[str, Any], count: int = 240) -> list[dict[str, Any]]:
    if count != 240:
        raise ValueError("v1.4 paths are frozen at 240 ticks")
    params = path["parameters"]
    rng = random.Random(path["market_seed"])
    mid = 50_000.0
    output: list[dict[str, Any]] = []
    for index in range(count):
        if index < 60:
            phase = "NORMAL_PRELUDE"
            cycle = (-4.0, -4.0, 4.0, 4.0)
            move = cycle[index % len(cycle)]
            move += rng.uniform(-0.05, 0.05)
        elif index < 180:
            phase = "STRESS"
            stress_index = index - 60
            trend = params["trend_bps"]
            scenario = path["scenario"]
            if scenario == "downward_toxic_trend":
                move = -trend
            elif scenario == "gap_through_quote":
                move = (
                    -params["gap_bps"]
                    if stress_index in {40, 80}
                    else trend * 0.25
                )
            elif scenario == "delayed_cancel_one_sided_flow":
                move = (
                    -trend
                    if stress_index % 14 < 10
                    else trend * 1.5
                )
            else:
                move = trend
            move += (
                rng.uniform(-0.12, 0.12)
                * params["volatility_multiplier"]
            )
        else:
            phase = "RECOVERY"
            recovery_index = index - 180
            cycle = (-4.0, -4.0, 4.0, 4.0)
            move = cycle[recovery_index % len(cycle)]
            move += rng.uniform(-0.05, 0.05)
        mid *= 1 + move / 10_000
        half_spread = mid / 10_000
        output.append({
            "bids": [[round(mid - half_spread, 1), 1.0]],
            "asks": [[round(mid + half_spread, 1), 1.0]],
            "timestamp": (
                4_100_000_000_000 + path["market_seed"] + index * 300_000
            ),
            "regime_phase": phase,
        })
    return output


def paths() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    counter = 0
    for severity, parameters in SEVERITY.items():
        for scenario in SCENARIOS:
            counter += 1
            row = {
                "path_id": (
                    f"mm-v1-4-{severity.lower()}-{counter:02d}-{scenario}"
                ),
                "severity": severity,
                "scenario": scenario,
                "parameters": deepcopy(parameters),
                "market_seed": 410_000 + counter,
                "fill_seed": 420_000 + counter,
                "source_block": (
                    f"v1-4-prelude-stress-recovery-{counter:02d}-"
                    f"{severity.lower()}-{scenario}"
                ),
                "generator_version": "mm-v1-4-segmented-v1",
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
    from backtest.mm_v1_3c_protocol import paths as old_paths

    old = old_paths()
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
        "schema_version": "mm-v1-4-targeted-causal-defense-v1",
        "protocol_id": PROTOCOL_ID,
        "fill_model": FILL_MODEL,
        "profiles": profiles(),
        "capital_policy": {
            "capital_usdt": CAPITAL,
            "lot_size_btc": 0.01,
            "leverage": 3,
            "maximum_margin_utilization": 0.80,
            "maximum_inventory_btc": 0.01,
            "soft_session_loss": 0.03,
            "hard_kill_drawdown": 0.05,
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
        },
        "reentry_requirements": {
            "causal_event_sequence_required": True,
            "normal_fill_before_first_activation": True,
            "normal_fill_after_completed_reentry": True,
            "defensive_mode_exit_min": 1,
            "recovery_normal_fills_min": 2,
            "not_permanently_one_sided": True,
        },
        "resilience_budget": {
            "S2_5": {"hard_kills": 0, "worst_drawdown_max": 0.035},
            "S3_LOW": {"hard_kills": 0, "worst_drawdown_max": 0.040},
            "S3_MID": {"hard_kills": 0, "worst_drawdown_max": 0.050},
            "S3_HIGH": {"boundary_diagnostic_only": True},
        },
        "mandatory_streams": list(STREAMS),
        "balanced_v1_3_matrix_rerun": False,
        "stress_v1_3a_matrix_rerun": False,
        "repair_v1_3b_matrix_rerun": False,
        "evidence_v1_3c_matrix_rerun": False,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
        "source_hashes": source_hashes(root),
    }
