"""Frozen design for the Market Maker v1.1 post-admission economic smoke."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any

from market_maker.as_config import MarketMakerV1Config


SMOKE_ID = "MM_V1_1_POST_ADMISSION_ECONOMIC_SMOKE_20260728_FRESH"
SCHEMA_VERSION = "mm-v1-1-post-admission-smoke-v1"
PRIMARY_CAPITAL_USDT = 750.0
STRESS_CAPITALS_USDT = (300.0, 500.0, 1_000.0)
LEVERAGE = 3.0
MAX_MARGIN_UTILIZATION = 0.80
ADDED_COST_BPS_PER_ROUND_TRIP = 2.0

PROFILE_DEFINITIONS = (
    {
        "name": "BASELINE",
        "overrides": {},
        "hypothesis": "Declared existing baseline.",
        "activity_effect": "reference",
        "inventory_effect": "reference",
        "churn_effect": "reference",
    },
    {
        "name": "WIDER_SPREAD",
        "overrides": {
            "minimum_half_spread_bps": 8.0,
            "maximum_half_spread_bps": 40.0,
        },
        "hypothesis": "More width may offset fees and adverse selection.",
        "activity_effect": "lower",
        "inventory_effect": "lower fill-driven inventory",
        "churn_effect": "similar",
    },
    {
        "name": "STRONGER_INVENTORY_SKEW",
        "overrides": {"inventory_skew_strength": 1.5},
        "hypothesis": "Stronger skew may shorten risky inventory holding.",
        "activity_effect": "similar",
        "inventory_effect": "lower",
        "churn_effect": "possibly higher",
    },
    {
        "name": "LOWER_CHURN",
        "overrides": {
            "minimum_order_lifetime_ticks": 3,
            "maximum_order_age_ticks": 12,
            "requote_threshold_ticks": 4,
        },
        "hypothesis": "Longer-lived quotes may reduce cancellations.",
        "activity_effect": "possibly higher resting exposure",
        "inventory_effect": "similar",
        "churn_effect": "lower",
    },
    {
        "name": "HIGHER_RISK_AVERSION",
        "overrides": {"risk_aversion_gamma": 0.12},
        "hypothesis": "Higher risk aversion may improve inventory control.",
        "activity_effect": "similar",
        "inventory_effect": "lower",
        "churn_effect": "similar",
    },
    {
        "name": "LOWER_RISK_AVERSION",
        "overrides": {"risk_aversion_gamma": 0.04},
        "hypothesis": "Lower risk aversion may increase competitive quoting.",
        "activity_effect": "higher",
        "inventory_effect": "higher",
        "churn_effect": "similar",
    },
    {
        "name": "SLOWER_VOLATILITY_RESPONSE",
        "overrides": {"volatility_ewma_decay": 0.97},
        "hypothesis": "Slower volatility response may stabilize quote width.",
        "activity_effect": "similar",
        "inventory_effect": "similar",
        "churn_effect": "lower",
    },
    {
        "name": "FASTER_VOLATILITY_RESPONSE",
        "overrides": {"volatility_ewma_decay": 0.85},
        "hypothesis": "Faster response may protect during abrupt moves.",
        "activity_effect": "possibly lower",
        "inventory_effect": "lower in volatility",
        "churn_effect": "higher",
    },
)

PATH_DEFINITIONS = (
    ("mm-v1-1-fresh-normal-range-s92001", "normal_volatility_range", 92001, 93001, 0),
    ("mm-v1-1-fresh-high-vol-range-s92002", "high_volatility_range", 92002, 93002, 0),
    ("mm-v1-1-fresh-uptrend-s92003", "steady_upward_trend", 92003, 93003, 0),
    ("mm-v1-1-fresh-downtrend-s92004", "steady_downward_trend", 92004, 93004, 0),
    ("mm-v1-1-fresh-mean-reversion-s92005", "mean_reversion", 92005, 93005, 0),
    ("mm-v1-1-fresh-false-breakout-s92006", "false_breakout_reversal", 92006, 93006, 0),
    ("mm-v1-1-fresh-spread-widening-s92007", "spread_widening", 92007, 93007, 0),
    ("mm-v1-1-fresh-thin-liquidity-s92008", "thin_liquidity_proxy", 92008, 93008, 0),
    ("mm-v1-1-fresh-stale-interruption-s92009", "stale_data_interruption", 92009, 93009, 0),
    ("mm-v1-1-fresh-inventory-stress-s92010", "inventory_stress", 92010, 93010, 0),
    ("mm-v1-1-fresh-delayed-cancel-s92011", "delayed_cancellation", 92011, 93011, 2),
    ("mm-v1-1-fresh-terminal-liquidation-s92012", "terminal_liquidation", 92012, 93012, 0),
)

SOURCE_FILES = (
    "market_maker/as_config.py",
    "market_maker/as_strategy.py",
    "market_maker/quote_model.py",
    "market_maker/inventory.py",
    "market_maker/volatility.py",
    "market_maker/diagnostics.py",
    "market_maker/margin.py",
    "backtest/matching_engine.py",
    "backtest/mm_runner.py",
    "backtest/mm_fragility.py",
    "backtest/mm_v1_1_protocol.py",
    "backtest/mm_v1_1_smoke.py",
    "fill_tracker.py",
)


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    return {name: file_hash(root / name) for name in SOURCE_FILES}


def fixed_profiles() -> list[dict[str, Any]]:
    profiles = []
    for index, definition in enumerate(PROFILE_DEFINITIONS, start=1):
        profile = MarketMakerV1Config(**definition["overrides"])
        profile.validate()
        profiles.append({
            "profile_id": f"mm-v1-1-profile-{index:02d}",
            "profile_name": definition["name"],
            "profile_fingerprint": profile.fingerprint,
            "parameters": asdict(profile),
            "overrides_from_baseline": definition["overrides"],
            "economic_hypothesis": definition["hypothesis"],
            "expected_activity_effect": definition["activity_effect"],
            "expected_inventory_effect": definition["inventory_effect"],
            "expected_churn_effect": definition["churn_effect"],
        })
    if not 6 <= len(profiles) <= 12:
        raise RuntimeError("fixed profile count outside declared bounds")
    varying = {
        key for definition in PROFILE_DEFINITIONS
        for key in definition["overrides"]
    }
    if len(varying) > 8:
        raise RuntimeError("more than eight controls vary")
    return profiles


def scenario_ticks(
    scenario: str, seed: int, *, count: int = 180
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    mid = 50_000.0
    ticks = []
    for index in range(count):
        phase = index % 18
        oscillation = 7.0 if index % 2 else -7.0
        if scenario == "high_volatility_range":
            move_bps = oscillation * 2.2
        elif scenario == "steady_upward_trend":
            move_bps = 5.0 if phase < 14 else -10.0
        elif scenario == "steady_downward_trend":
            move_bps = -5.0 if phase < 14 else 10.0
        elif scenario == "mean_reversion":
            move_bps = (phase - 8.5) * -1.7
        elif scenario == "false_breakout_reversal":
            move_bps = 22.0 if 50 <= index < 60 else -22.0 if 60 <= index < 70 else oscillation
        elif scenario == "inventory_stress":
            move_bps = -10.0 if phase < 11 else 15.0
        elif scenario == "terminal_liquidation" and index >= count - 8:
            move_bps = 14.0
        else:
            move_bps = oscillation
        move_bps += rng.uniform(-0.5, 0.5)
        mid *= 1.0 + move_bps / 10_000.0
        spread_bps = (
            10.0
            if scenario == "spread_widening" and phase in range(6, 12)
            else 2.0
        )
        half = mid * spread_bps / 20_000.0
        liquidity = 0.10 if scenario == "thin_liquidity_proxy" else 1.0
        imbalance = (
            0.75 if scenario == "inventory_stress"
            else 0.30 * math.sin(index / 5.0)
        )
        tick = {
            "bids": [[round(mid - half, 1), max(0.01, liquidity * (1 + imbalance))]],
            "asks": [[round(mid + half, 1), max(0.01, liquidity * (1 - imbalance))]],
            "timestamp": 2_200_000_000_000 + seed + index * 300_000,
            "scenario": scenario,
            "generator_version": "mm-v1-1-synthetic-v2",
        }
        if scenario == "stale_data_interruption" and phase in {8, 9}:
            tick["stale"] = True
        ticks.append(tick)
    return ticks


def frozen_paths() -> list[dict[str, Any]]:
    paths = []
    for path_id, scenario, market_seed, fill_seed, latency in PATH_DEFINITIONS:
        ticks = scenario_ticks(scenario, market_seed)
        paths.append({
            "market_path_id": path_id,
            "market_path_hash": payload_hash(ticks),
            "scenario": scenario,
            "scenario_parameters": {"tick_count": len(ticks)},
            "market_seed": market_seed,
            "fill_seed": fill_seed,
            "source_block": f"mm-v1-1-fresh-smoke-{scenario}",
            "generator_version": "mm-v1-1-synthetic-v2",
            "index_range": [0, len(ticks) - 1],
            "cancel_latency_ticks": latency,
            "split": "SMOKE_RESEARCH_ONLY",
        })
    return paths


def build_specification(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "smoke_id": SMOKE_ID,
        "purpose": "FIXED_PROFILE_POST_ADMISSION_ECONOMIC_SMOKE",
        "primary_capital_usdt": PRIMARY_CAPITAL_USDT,
        "stress_capitals_usdt": list(STRESS_CAPITALS_USDT),
        "capital_copies_are_independent_evidence": False,
        "leverage": LEVERAGE,
        "maximum_margin_utilization": MAX_MARGIN_UTILIZATION,
        "fixed_profiles": fixed_profiles(),
        "paths": frozen_paths(),
        "fill_semantics": {
            "ranking_mode": "conservative",
            "strict_trade_through": True,
            "maker_passive_fills_only": True,
            "fill_before_cancel": True,
            "touch_only_ranking_weight": 0,
            "probabilistic_ranking_weight": 0,
            "terminal_liquidation_charged": True,
        },
        "gates": {
            "integrity": {
                "unclassified_order_removals": 0,
                "determinism": True,
                "all_metrics_finite": True,
            },
            "safety": {
                "preventable_margin_breaches": 0,
                "unknown_margin_states": 0,
                "inventory_breaches": 0,
                "hard_kills": 0,
                "terminal_residual_inventory_btc": 0,
                "unreconciled_accounting": 0,
                "worst_drawdown_max": 0.025,
            },
            "activity": {
                "maker_fills_min": 50,
                "bid_fills_min": 15,
                "ask_fills_min": 15,
                "round_trips_min": 10,
                "represented_scenarios_min": 4,
            },
            "economics": {
                "net_pnl_gt": 0,
                "round_trip_expectancy_gt": 0,
                "profit_factor_gt": 1,
                "added_2bps_pnl_gt": 0,
                "after_top_10pct_removal_gt": 0,
                "average_net_spread_capture_gt": 0,
                "average_5_tick_markout_min": -0.05,
                "gross_spread_capture_gt_total_fees": True,
                "terminal_liquidation_not_total_loss": True,
            },
        },
        "added_cost_bps_per_round_trip": ADDED_COST_BPS_PER_ROUND_TRIP,
        "profile_extension": None,
        "path_extension": None,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "source_hashes": source_hashes(root),
    }
