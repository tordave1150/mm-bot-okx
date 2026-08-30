"""Frozen data and search-space definitions for Market Maker v1 smoke."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any


PROTOCOL_SCHEMA = "market-maker-v1-smoke-protocol-v1"
PROTOCOL_DECLARATION = "MM_V1_SMOKE_PROTOCOL_DECLARED"
SAMPLER_SEED = 83_001
PROPOSAL_CEILING = 24
VALID_CANDIDATE_TARGET = 12
MAX_EVALUATED_CANDIDATES = 12

SOURCE_FILES = (
    "market_maker/registry.py",
    "market_maker/as_config.py",
    "market_maker/as_strategy.py",
    "market_maker/quote_model.py",
    "market_maker/volatility.py",
    "market_maker/arrival_intensity.py",
    "market_maker/inventory.py",
    "market_maker/diagnostics.py",
    "backtest/mm_runner.py",
    "backtest/mm_protocol.py",
    "backtest/mm_optimize.py",
    "backtest/mm_fragility.py",
    "backtest/mm_smoke.py",
    "backtest/matching_engine.py",
    "fill_tracker.py",
)

PATH_DEFINITIONS = (
    ("mm-v1-normal-range-s51001", "normal_range", 51001, 61001, 0),
    ("mm-v1-high-vol-range-s51002", "high_volatility_range", 51002, 61002, 0),
    ("mm-v1-uptrend-s51003", "uptrend", 51003, 61003, 0),
    ("mm-v1-downtrend-s51004", "downtrend", 51004, 61004, 0),
    ("mm-v1-mean-reversion-s51005", "mean_reversion", 51005, 61005, 0),
    ("mm-v1-spread-widening-s51006", "spread_widening", 51006, 61006, 0),
    ("mm-v1-thin-liquidity-s51007", "thin_liquidity", 51007, 61007, 0),
    ("mm-v1-gap-s51008", "gap", 51008, 61008, 0),
    ("mm-v1-stale-data-s51009", "stale_data", 51009, 61009, 0),
    ("mm-v1-partial-fixture-s51010", "partial_fill_fixture", 51010, 61010, 0),
    ("mm-v1-delayed-cancel-s51011", "delayed_cancellation", 51011, 61011, 2),
    ("mm-v1-terminal-liquidation-s51012", "terminal_liquidation", 51012, 61012, 0),
)

SEARCH_SPACE = {
    "risk_aversion_gamma": [0.04, 0.08, 0.12],
    "arrival_decay_k_or_proxy": [8_000.0, 15_000.0, 25_000.0],
    "volatility_ewma_decay": [0.85, 0.92, 0.97],
    "minimum_half_spread_bps": [3.0, 5.0, 7.0],
    "maximum_half_spread_bps": [20.0, 30.0, 40.0],
    "inventory_skew_strength": [0.5, 1.0, 1.5],
    "imbalance_skew_strength": [0.0, 0.25, 0.5],
    "minimum_order_lifetime_ticks": [1, 2, 3],
    "maximum_order_age_ticks": [6, 8, 12],
    "requote_threshold_ticks": [1, 2, 4],
}


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


def scenario_ticks(
    scenario: str, seed: int, *, count: int = 240
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    mid = 50_000.0
    ticks: list[dict[str, Any]] = []
    for index in range(count):
        phase = index % 12
        base_move_bps = 8.0 if index % 2 else -8.0
        if scenario == "high_volatility_range":
            move_bps = base_move_bps * 1.8
        elif scenario == "uptrend":
            move_bps = 9.0 if phase < 8 else -14.0
        elif scenario == "downtrend":
            move_bps = -9.0 if phase < 8 else 14.0
        elif scenario == "mean_reversion":
            move_bps = (phase - 5.5) * -2.2
        elif scenario == "gap":
            move_bps = 35.0 if phase == 5 else -35.0 if phase == 6 else base_move_bps
        elif scenario == "terminal_liquidation" and index > count - 5:
            move_bps = 12.0
        else:
            move_bps = base_move_bps
        move_bps += rng.uniform(-0.6, 0.6)
        mid *= 1.0 + move_bps / 10_000.0
        spread_bps = (
            8.0 if scenario == "spread_widening" and phase in {4, 5, 6}
            else 2.0
        )
        half = mid * spread_bps / 20_000.0
        size = 0.12 if scenario == "thin_liquidity" else 1.0
        imbalance = 0.35 * math.sin(index / 4.0)
        bid_size = max(0.01, size * (1.0 + imbalance))
        ask_size = max(0.01, size * (1.0 - imbalance))
        tick = {
            "bids": [[round(mid - half, 1), bid_size]],
            "asks": [[round(mid + half, 1), ask_size]],
            "timestamp": 2_000_000_000_000 + seed + index * 300_000,
            "scenario": scenario,
            "synthetic_generator_version": "mm-v1-synthetic-v1",
        }
        if scenario == "stale_data" and phase == 7:
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
            "source_block": f"mm-v1-smoke-train-{scenario}",
            "synthetic_generator_version": "mm-v1-synthetic-v1",
            "index_range": [0, len(ticks) - 1],
            "cancel_latency_ticks": latency,
            "split": "SMOKE_TRAIN_RESEARCH_ONLY",
        })
    return paths


def assert_no_path_collision(root: Path, paths: list[dict[str, Any]]) -> None:
    artifact_roots = [
        root / "artifacts" / "baseline",
        root / "artifacts" / "comparisons",
        root / "artifacts" / "diagnostics",
        root / "artifacts" / "robust_optuna",
        root / "artifacts" / "verification",
    ]
    needles = [
        str(value)
        for path in paths
        for value in (
            path["market_path_id"],
            f'\"market_seed\": {path["market_seed"]}',
            f'\"fill_seed\": {path["fill_seed"]}',
        )
    ]
    collisions: list[str] = []
    for artifact_root in artifact_roots:
        if not artifact_root.exists():
            continue
        for artifact in artifact_root.rglob("*"):
            if (
                not artifact.is_file()
                or artifact.suffix.lower() not in {".json", ".jsonl", ".md", ".csv"}
            ):
                continue
            text = artifact.read_text(encoding="utf-8", errors="ignore")
            if any(needle in text for needle in needles):
                collisions.append(str(artifact.relative_to(root)))
    if collisions:
        raise RuntimeError(f"prior-data collision: {sorted(set(collisions))}")
