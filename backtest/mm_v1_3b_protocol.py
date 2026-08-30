"""Frozen targeted defensive profiles and refined stress boundary."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from backtest.mm_v1_3a_protocol import carried_profiles as v13a_profiles


PROTOCOL_ID = "MM_V1_3B_TARGETED_STRESS_RESILIENCE_20260728"
CAPITAL = 750.0
FILL_MODEL = "ADVERSE_SELECTION_STRESS_MODEL"
SEVERITY = {
    "S2_5": {"rank": 1, "trend_bps": 5.0, "volatility_multiplier": 1.8,
             "gap_bps": 15.0, "cancel_delay": 1, "drawdown_max": 0.035},
    "S3_LOW": {"rank": 2, "trend_bps": 6.0, "volatility_multiplier": 2.0,
               "gap_bps": 20.0, "cancel_delay": 1, "drawdown_max": 0.04},
    "S3_MID": {"rank": 3, "trend_bps": 7.0, "volatility_multiplier": 2.2,
               "gap_bps": 25.0, "cancel_delay": 2, "drawdown_max": 0.05},
    "S3_HIGH": {"rank": 4, "trend_bps": 8.0, "volatility_multiplier": 2.5,
                "gap_bps": 30.0, "cancel_delay": 2, "drawdown_max": None},
}
SCENARIOS = ("upward_toxic_trend", "downward_toxic_trend",
             "gap_through_quote", "delayed_cancel_one_sided_flow")
OVERLAYS = (
    ("BASELINE_CONTROL", {}),
    ("WIDER_SPREAD_CONTROL", {}),
    ("VOLATILITY_SPREAD_GUARD", {"volatility_spread_guard": True,
        "spread_multiplier_per_bps": 0.15, "maximum_spread_multiplier": 2.5}),
    ("FAST_CANCEL_ON_VOLATILITY", {"fast_cancel": True,
        "shock_threshold_bps": 4.5}),
    ("TOXIC_FLOW_PAUSE", {"toxic_pause_ticks": 3}),
    ("INVENTORY_REDUCTION_PRIORITY", {"inventory_reduction_priority": True,
        "shock_threshold_bps": 4.5}),
    ("ONE_SIDED_DEFENSIVE_MODE", {"one_sided_defensive": True,
        "shock_threshold_bps": 4.5}),
    ("COMPOSITE_DEFENSIVE", {"volatility_spread_guard": True,
        "spread_multiplier_per_bps": 0.12, "maximum_spread_multiplier": 2.2,
        "fast_cancel": True, "shock_threshold_bps": 4.5,
        "toxic_pause_ticks": 2, "inventory_reduction_priority": True}),
)
STREAMS = ("market_events.jsonl", "trade_events.jsonl", "quote_events.jsonl",
    "defensive_events.jsonl", "margin_events.jsonl", "order_events.jsonl",
    "fills.jsonl", "round_trips.jsonl", "markouts.jsonl",
    "hard_kill_events.jsonl", "path_results.jsonl")
SOURCE_FILES = ("backtest/mm_runner.py", "backtest/mm_v1_3b_protocol.py",
    "backtest/mm_v1_3b_repair.py", "backtest/mm_v1_3a_diagnostic.py",
    "backtest/matching_engine.py", "market_maker/as_config.py",
    "market_maker/margin.py", "fill_tracker.py")


def canonical(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(root: Path) -> dict[str,str]:
    return {name:file_hash(root/name) for name in SOURCE_FILES}


def profiles() -> list[dict[str,Any]]:
    prior=v13a_profiles()
    baseline=prior[0]["parameters"]; wider=prior[1]["parameters"]
    output=[]
    for index,(name,overlay) in enumerate(OVERLAYS,1):
        parameters=wider if name=="WIDER_SPREAD_CONTROL" else baseline
        output.append({"profile_id":f"mm-v1-3b-profile-{index:02d}",
            "profile_name":name,"prior_profile_reference":(
                prior[1]["profile_id"] if name=="WIDER_SPREAD_CONTROL"
                else prior[0]["profile_id"]),
            "parameters":parameters,"defensive_overlay":overlay,
            "changed_fields":sorted(overlay),"unchanged_safety":True,
            "profile_fingerprint":digest({"id":PROTOCOL_ID,"name":name,
                "parameters":parameters,"overlay":overlay}),
            "hypothesis":f"Predeclared {name} resilience hypothesis",
            "expected_activity_cost":"bounded","expected_markout_effect":"less adverse"})
    return output


def ticks(path:dict[str,Any],count:int=180)->list[dict[str,Any]]:
    params=path["parameters"];rng=random.Random(path["market_seed"]);mid=50000.
    out=[]
    for i in range(count):
        trend=params["trend_bps"];scenario=path["scenario"]
        if scenario=="downward_toxic_trend": move=-trend
        elif scenario=="gap_through_quote":
            move=-params["gap_bps"] if i in {60,120} else trend*.25
        elif scenario=="delayed_cancel_one_sided_flow":
            move=-trend if i%14<10 else trend*1.5
        else: move=trend
        move+=rng.uniform(-.12,.12)*params["volatility_multiplier"]
        mid*=1+move/10000;half=mid/10000
        out.append({"bids":[[round(mid-half,1),1.]],"asks":[[round(mid+half,1),1.]],
            "timestamp":2_900_000_000_000+path["market_seed"]+i*300000})
    return out


def paths()->list[dict[str,Any]]:
    out=[];counter=0
    for level,params in SEVERITY.items():
        for scenario in SCENARIOS:
            counter+=1
            row={"path_id":f"mm-v1-3b-{level.lower()}-{counter:02d}-{scenario}",
                "severity":level,"scenario":scenario,"parameters":params,
                "market_seed":190000+counter,"fill_seed":200000+counter,
                "source_block":f"v1-3b-{level.lower()}-{scenario}",
                "generator_version":"mm-v1-3b-refined-v1","index_range":[0,179]}
            row["path_hash"]=digest(ticks(row));out.append(row)
    return out


def build_spec(root:Path)->dict[str,Any]:
    return {"schema_version":"mm-v1-3b-repair-v1","protocol_id":PROTOCOL_ID,
        "capital_usdt":CAPITAL,"fill_model":FILL_MODEL,"profiles":profiles(),
        "severity_ladder":SEVERITY,"paths":paths(),
        "activity_floor":{"fills_min":8,"both_sides":True,"normal_round_trips_min":2,
            "quote_eligible_ticks_gt":0,"two_sided_rate_min":.05,
            "no_quote_rate_max":.85,"scenario_families_min":3},
        "resilience_budget":{k:{"hard_kills":0,
            "worst_drawdown_max":v["drawdown_max"]} for k,v in SEVERITY.items()
            if k!="S3_HIGH"},"mandatory_streams":list(STREAMS),
        "profile_extension":None,"path_extension":None,
        "balanced_matrix_rerun":False,"v13a_matrix_rerun":False,
        "optimization":False,"validation_opened":False,"holdout_opened":False,
        "external_access":False,"git_write_operation":False,
        "source_hashes":source_hashes(root)}
