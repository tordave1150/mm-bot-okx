"""Frozen design for the MM v1.3 balanced-causal economic smoke."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any

from market_maker.as_config import MarketMakerV1Config


SMOKE_ID = "MM_V1_3_BALANCED_CAUSAL_ECONOMIC_SMOKE_20260728"
PRIMARY_CAPITAL = 750.0
STRESS_CAPITALS = (300.0, 500.0, 1000.0)
BALANCED_MODEL = "BALANCED_CAUSAL_FILL_MODEL"
STRESS_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
ADDED_COST_BPS = 2.0

PROFILE_DEFINITIONS = (
    ("BASELINE", {}, "reference", "reference", "reference", "reference"),
    ("WIDER_SPREAD", {"minimum_half_spread_bps": 8.0,
     "maximum_half_spread_bps": 40.0}, "wider", "lower", "similar", "lower"),
    ("STRONGER_INVENTORY_SKEW", {"inventory_skew_strength": 1.5},
     "similar", "lower", "similar", "similar"),
    ("LOWER_CHURN", {"minimum_order_lifetime_ticks": 3,
     "maximum_order_age_ticks": 12, "requote_threshold_ticks": 4},
     "similar", "similar", "lower", "similar"),
    ("HIGHER_RISK_AVERSION", {"risk_aversion_gamma": 0.12},
     "wider", "lower", "similar", "similar"),
    ("LOWER_RISK_AVERSION", {"risk_aversion_gamma": 0.04},
     "narrower", "higher", "similar", "higher"),
)

BALANCED_SCENARIOS = (
    "stationary_symmetric", "low_volatility_range", "normal_volatility_range",
    "high_volatility_range", "mean_reversion", "shallow_upward_trend",
    "shallow_downward_trend", "false_breakout",
    "spread_widening_then_normalization", "balanced_aggressor_flow_burst",
    "temporary_one_sided_flow_imbalance", "inventory_recovery",
)
STRESS_SCENARIOS = (
    "fast_upward_trend", "fast_downward_trend", "high_volatility_whipsaw",
    "toxic_one_sided_flow", "gap_through_quote", "delayed_cancellation",
)

SOURCE_FILES = (
    "market_maker/execution_accounting.py",
    "backtest/mm_execution_microstructure.py",
    "backtest/mm_runner.py",
    "backtest/mm_v1_3_protocol.py",
    "backtest/mm_v1_3_smoke.py",
    "market_maker/as_strategy.py",
    "market_maker/margin.py",
    "backtest/matching_engine.py",
    "fill_tracker.py",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    return {name: file_hash(root / name) for name in SOURCE_FILES}


def fixed_profiles() -> list[dict[str, Any]]:
    output = []
    for index, (name, overrides, spread, inventory, churn, activity) in enumerate(
        PROFILE_DEFINITIONS, 1
    ):
        config = MarketMakerV1Config(**overrides)
        config.validate()
        parameters = asdict(config)
        fingerprint = payload_hash({
            "smoke_id": SMOKE_ID, "profile": name, "parameters": parameters
        })
        output.append({
            "profile_id": f"mm-v1-3-profile-{index:02d}",
            "profile_name": name,
            "profile_fingerprint": fingerprint,
            "config_fingerprint": config.fingerprint,
            "parameters": parameters,
            "economic_hypothesis": f"{name} fixed diagnostic family",
            "expected_spread_effect": spread,
            "expected_inventory_effect": inventory,
            "expected_churn_effect": churn,
            "expected_activity_effect": activity,
        })
    return output


def balanced_ticks(scenario: str, market_seed: int, flow_seed: int,
                   count: int = 180) -> list[dict[str, Any]]:
    market = random.Random(market_seed)
    flow = random.Random(flow_seed)
    mid = 50_000.0
    ticks = []
    for index in range(count):
        phase = index % 24
        move = 0.0
        if scenario == "low_volatility_range":
            move = 0.25 if index % 2 else -0.25
        elif scenario == "normal_volatility_range":
            move = 0.8 if index % 2 else -0.8
        elif scenario == "high_volatility_range":
            move = 2.5 if index % 2 else -2.5
        elif scenario == "mean_reversion":
            move = -(phase - 11.5) * 0.18
        elif scenario == "shallow_upward_trend":
            move = 0.35
        elif scenario == "shallow_downward_trend":
            move = -0.35
        elif scenario == "false_breakout":
            move = 3.0 if 70 <= index < 82 else -3.0 if 82 <= index < 94 else 0
        elif scenario == "spread_widening_then_normalization":
            move = 0.6 if index % 2 else -0.6
        elif scenario == "inventory_recovery":
            move = -0.6 if phase < 8 else 0.3
        move += market.uniform(-0.08, 0.08)
        mid *= 1 + move / 10_000
        spread_bps = 8.0 if (
            scenario == "spread_widening_then_normalization"
            and 55 <= index < 105
        ) else 2.0
        half = mid * spread_bps / 20_000
        side = "sell" if index % 2 == 0 else "buy"
        if scenario == "temporary_one_sided_flow_imbalance" and 55 <= index < 75:
            side = "sell"
        if scenario == "balanced_aggressor_flow_burst" and 60 <= index < 85:
            side = "sell" if index % 3 else "buy"
        quantity = (0.004, 0.006, 0.01)[index % 3]
        sweep = 260.0 + flow.uniform(0, 5)
        trade_price = mid - sweep if side == "sell" else mid + sweep
        ticks.append({
            "tick": index + 1,
            "bid": round(mid - half, 2), "ask": round(mid + half, 2),
            "mid": round(mid, 2),
            "timestamp": 2_600_000_000_000 + market_seed + index * 300_000,
            "trades": [{
                "event_id": f"{scenario}-trade-{index + 1:04d}",
                "aggressor_side": side,
                "price": round(trade_price, 2),
                "quantity_btc": quantity,
            }],
        })
    return ticks


def strict_ticks(scenario: str, seed: int, count: int = 180) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    mid = 50_000.0
    output = []
    for index in range(count):
        if scenario == "fast_upward_trend":
            move = 8
        elif scenario == "fast_downward_trend":
            move = -8
        elif scenario == "high_volatility_whipsaw":
            move = 18 if index % 2 else -18
        elif scenario == "toxic_one_sided_flow":
            move = -10 if index % 8 < 6 else 4
        elif scenario == "gap_through_quote":
            move = -55 if index in {60, 120} else 1
        else:
            move = 7 if index % 12 < 9 else -14
        mid *= 1 + (move + rng.uniform(-0.2, 0.2)) / 10_000
        half = mid / 10_000
        output.append({
            "bids": [[round(mid - half, 1), 1.0]],
            "asks": [[round(mid + half, 1), 1.0]],
            "timestamp": 2_700_000_000_000 + seed + index * 300_000,
        })
    return output


def balanced_paths() -> list[dict[str, Any]]:
    output = []
    for index, scenario in enumerate(BALANCED_SCENARIOS, 1):
        market_seed, flow_seed, fill_seed = 120_000 + index, 130_000 + index, 140_000 + index
        ticks = balanced_ticks(scenario, market_seed, flow_seed)
        output.append({
            "path_id": f"mm-v1-3-balanced-{index:02d}-{scenario}",
            "path_hash": payload_hash(ticks), "scenario": scenario,
            "parameters": {"tick_count": len(ticks)},
            "market_seed": market_seed, "aggressor_flow_seed": flow_seed,
            "fill_seed": fill_seed, "source_block": f"v1-3-balanced-{scenario}",
            "generator_version": "mm-v1-3-balanced-v1",
            "index_range": [0, len(ticks) - 1],
        })
    return output


def stress_paths() -> list[dict[str, Any]]:
    output = []
    for index, scenario in enumerate(STRESS_SCENARIOS, 1):
        seed = 150_000 + index
        ticks = strict_ticks(scenario, seed)
        output.append({
            "path_id": f"mm-v1-3-stress-{index:02d}-{scenario}",
            "path_hash": payload_hash(ticks), "scenario": scenario,
            "market_seed": seed, "fill_seed": 160_000 + index,
            "source_block": f"v1-3-stress-{scenario}",
            "generator_version": "mm-v1-3-strict-v1",
            "index_range": [0, len(ticks) - 1],
            "cancel_latency_ticks": 2 if scenario == "delayed_cancellation" else 0,
        })
    return output


def build_specification(root: Path) -> dict[str, Any]:
    return {
        "schema_version": "mm-v1-3-balanced-smoke-v1", "smoke_id": SMOKE_ID,
        "primary_capital_usdt": PRIMARY_CAPITAL,
        "diagnostic_capitals_usdt": list(STRESS_CAPITALS),
        "capital_copies_independent": False,
        "fill_model_policy": {
            "primary": BALANCED_MODEL, "stress": STRESS_MODEL,
            "touch_ranking_weight": 0, "probabilistic_ranking_weight": 0,
            "mixed_or_unknown_fails": True,
        },
        "fixed_profiles": fixed_profiles(),
        "balanced_paths": balanced_paths(), "stress_paths": stress_paths(),
        "safety_gates": {
            "preventable_margin_breaches": 0, "margin_breaches": 0,
            "unknown_margin_states": 0, "inventory_breaches": 0,
            "hard_kills": 0, "terminal_residual_inventory_btc": 0,
            "worst_drawdown_max": 0.025,
        },
        "activity_gates": {
            "fills_min": 50, "bid_fills_min": 15, "ask_fills_min": 15,
            "normal_round_trips_min": 10, "scenarios_min": 4,
        },
        "economic_gates": {
            "net_pnl_gt": 0, "expectancy_gt": 0, "profit_factor_gt": 1,
            "net_spread_gt": 0, "gross_gt_fees": True,
            "added_2bps_gt": 0, "top10_removal_gt": 0,
            "markout_5_min": -0.05,
        },
        "stress_budget": {
            "worst_drawdown_max": 0.05, "hard_kills": 0,
            "preventable_margin_breaches": 0, "inventory_breaches": 0,
            "accounting_failures": 0,
        },
        "added_cost_bps": ADDED_COST_BPS,
        "profile_extension": None, "balanced_path_extension": None,
        "stress_path_extension": None, "optimization": False,
        "validation_opened": False, "holdout_opened": False,
        "external_access": False, "git_write_operation": False,
        "source_hashes": source_hashes(root),
    }
