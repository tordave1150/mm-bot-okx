"""Frozen v1.3A strict-stress fragility design."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any

from backtest.mm_v1_3_protocol import fixed_profiles as v13_profiles


DIAGNOSTIC_ID = "MM_V1_3A_STRESS_FRAGILITY_20260728"
CAPITAL = 750.0
FILL_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
SEVERITY = {
    "S1": {"rank": 1, "trend_bps": 2.0, "volatility_multiplier": 1.0,
           "trade_through_depth_bps": 1.0, "toxic_duration": 18,
           "cancel_latency_ticks": 0, "gap_bps": 0.0, "drawdown_max": 0.025},
    "S2": {"rank": 2, "trend_bps": 4.0, "volatility_multiplier": 1.5,
           "trade_through_depth_bps": 3.0, "toxic_duration": 30,
           "cancel_latency_ticks": 1, "gap_bps": 10.0, "drawdown_max": 0.035},
    "S3": {"rank": 3, "trend_bps": 8.0, "volatility_multiplier": 2.5,
           "trade_through_depth_bps": 7.0, "toxic_duration": 48,
           "cancel_latency_ticks": 2, "gap_bps": 30.0, "drawdown_max": 0.05},
    "S4": {"rank": 4, "trend_bps": 14.0, "volatility_multiplier": 4.0,
           "trade_through_depth_bps": 14.0, "toxic_duration": 72,
           "cancel_latency_ticks": 3, "gap_bps": 80.0, "drawdown_max": None},
}
SCENARIOS = {
    "S1": ("upward_toxic_trend", "downward_toxic_trend", "high_volatility_whipsaw"),
    "S2": ("one_sided_aggressor_pressure", "gap_through_resting_quote", "delayed_cancellation"),
    "S3": ("upward_toxic_trend", "gap_through_resting_quote", "delayed_cancellation"),
    "S4": ("high_volatility_whipsaw", "one_sided_aggressor_pressure", "gap_through_resting_quote"),
}
MANDATORY_STREAMS = (
    "market_events.jsonl", "trade_events.jsonl", "quote_events.jsonl",
    "margin_events.jsonl", "order_events.jsonl", "fills.jsonl",
    "round_trips.jsonl", "markouts.jsonl", "hard_kill_events.jsonl",
    "path_results.jsonl",
)
SOURCE_FILES = (
    "backtest/mm_runner.py", "backtest/mm_v1_3a_protocol.py",
    "backtest/mm_v1_3a_diagnostic.py", "backtest/matching_engine.py",
    "market_maker/as_config.py", "market_maker/margin.py", "fill_tracker.py",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    return {name: file_hash(root / name) for name in SOURCE_FILES}


def carried_profiles() -> list[dict[str, Any]]:
    output = []
    for index, prior in enumerate(v13_profiles(), 1):
        parameters = prior["parameters"]
        output.append({
            "prior_profile_reference": prior["profile_id"],
            "profile_id": f"mm-v1-3a-profile-{index:02d}",
            "profile_name": prior["profile_name"],
            "profile_fingerprint": digest({
                "diagnostic_id": DIAGNOSTIC_ID, "parameters": parameters,
                "name": prior["profile_name"],
            }),
            "parameters": parameters,
        })
    return output


def stress_ticks(path: dict[str, Any], count: int = 180) -> list[dict[str, Any]]:
    severity = SEVERITY[path["severity"]]
    rng = random.Random(path["market_seed"])
    mid = 50_000.0
    output = []
    scenario = path["scenario"]
    for index in range(count):
        trend = severity["trend_bps"]
        if scenario == "downward_toxic_trend":
            move = -trend
        elif scenario == "high_volatility_whipsaw":
            move = trend * severity["volatility_multiplier"] * (1 if index % 2 else -1)
        elif scenario == "one_sided_aggressor_pressure":
            move = -trend if index % 10 < 8 else trend * 0.5
        elif scenario == "gap_through_resting_quote":
            move = -severity["gap_bps"] if index in {60, 120} else trend * 0.15
        elif scenario == "delayed_cancellation":
            move = trend if index % 16 < 12 else -trend * 2
        else:
            move = trend
        move += rng.uniform(-0.15, 0.15)
        mid *= 1 + move / 10_000
        half = mid / 10_000
        output.append({
            "bids": [[round(mid - half, 1), 1.0]],
            "asks": [[round(mid + half, 1), 1.0]],
            "timestamp": 2_800_000_000_000 + path["market_seed"] + index * 300_000,
        })
    return output


def stress_paths() -> list[dict[str, Any]]:
    output = []
    counter = 0
    for severity, scenarios in SCENARIOS.items():
        for scenario in scenarios:
            counter += 1
            record = {
                "path_id": f"mm-v1-3a-{severity.lower()}-{counter:02d}-{scenario}",
                "severity": severity, "scenario": scenario,
                "scenario_parameters": SEVERITY[severity],
                "market_seed": 170_000 + counter, "fill_seed": 180_000 + counter,
                "source_block": f"v1-3a-{severity.lower()}-{scenario}",
                "generator_version": "mm-v1-3a-stress-v1",
                "index_range": [0, 179],
            }
            record["path_hash"] = digest(stress_ticks(record))
            output.append(record)
    return output


def build_spec(root: Path) -> dict[str, Any]:
    return {
        "schema_version": "mm-v1-3a-stress-v1",
        "diagnostic_id": DIAGNOSTIC_ID, "capital_usdt": CAPITAL,
        "fill_model": FILL_MODEL, "profiles": carried_profiles(),
        "severity_ladder": SEVERITY, "paths": stress_paths(),
        "stress_budget": {
            level: {"hard_kills": 0, "worst_drawdown_max": data["drawdown_max"]}
            for level, data in SEVERITY.items() if level != "S4"
        },
        "integrity_budget": {
            "missing_streams": 0, "invalid_schemas": 0,
            "broken_references": 0, "hash_mismatches": 0,
            "unclassified_quote_mode_ticks": 0,
            "unclassified_order_removals": 0, "accounting_failures": 0,
        },
        "safety_budget": {
            "preventable_margin_breaches": 0, "margin_breaches": 0,
            "inventory_breaches": 0, "terminal_residual_inventory": 0,
            "unknown_margin_states": 0,
        },
        "mandatory_streams": list(MANDATORY_STREAMS),
        "balanced_matrix_rerun": False, "profile_extension": None,
        "path_extension": None, "optimization": False,
        "validation_opened": False, "holdout_opened": False,
        "external_access": False, "git_write_operation": False,
        "source_hashes": source_hashes(root),
    }
