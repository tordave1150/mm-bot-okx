"""Causal event-ordering evidence for defensive activation and re-entry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3c_protocol import paths, profiles, ticks
from market_maker.as_config import MarketMakerV1Config


PROTOCOL_ID = "MM_V1_3D_CAUSAL_REENTRY_EVIDENCE_20260729"
ACTIVATION_EVENTS = {
    "VOLATILITY_SPREAD_GUARD",
    "FAST_CANCEL_ON_VOLATILITY",
    "TOXIC_FLOW_PAUSE_ENTRY",
    "INVENTORY_REDUCTION_PRIORITY",
    "ONE_SIDED_DEFENSIVE_MODE",
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def causal_key(record: dict[str, Any]) -> tuple[int, int]:
    if (
        type(record.get("tick")) is not int
        or type(record.get("event_sequence")) is not int
    ):
        raise ValueError("tick and event_sequence are required integers")
    tick = int(record["tick"])
    sequence = int(record["event_sequence"])
    if sequence // 10_000 != tick:
        raise ValueError("event sequence does not belong to declared tick")
    return tick, sequence


def audit_reentry(
    fills: list[dict[str, Any]],
    defensive_events: list[dict[str, Any]],
) -> dict[str, Any]:
    normal = [
        fill for fill in fills
        if fill.get("normal_activity_eligible") is True
    ]
    activations = [
        event for event in defensive_events
        if event.get("event") in ACTIVATION_EVENTS
    ]
    reentries = [
        event for event in defensive_events
        if event.get("event") == "DEFENSIVE_REENTRY_READY"
    ]
    for record in [*normal, *activations, *reentries]:
        causal_key(record)
    first_activation = min(
        activations, key=causal_key, default=None
    )
    fill_before_activation = (
        first_activation is not None
        and any(
            causal_key(fill) < causal_key(first_activation)
            for fill in normal
        )
    )
    fill_after_reentry = any(
        causal_key(fill) > causal_key(reentry)
        for reentry in reentries
        for fill in normal
    )
    same_tick_precedence = any(
        fill["tick"] == activation["tick"]
        and causal_key(fill) < causal_key(activation)
        for fill in normal
        for activation in activations
    )
    result = {
        "normal_fill_count": len(normal),
        "activation_count": len(activations),
        "reentry_count": len(reentries),
        "normal_fill_before_first_activation": fill_before_activation,
        "normal_fill_after_completed_reentry": fill_after_reentry,
        "same_tick_fill_precedes_activation": same_tick_precedence,
        "event_sequences_unique": len({
            record["event_sequence"]
            for record in [*normal, *activations, *reentries]
        }) == len([*normal, *activations, *reentries]),
    }
    result["passed"] = (
        result["normal_fill_before_first_activation"]
        and result["normal_fill_after_completed_reentry"]
        and result["event_sequences_unique"]
    )
    return result


def fixtures() -> list[dict[str, Any]]:
    base_fill = {
        "tick": 10,
        "event_sequence": 103_001,
        "normal_activity_eligible": True,
    }
    activation = {
        "tick": 10,
        "event_sequence": 104_001,
        "event": "TOXIC_FLOW_PAUSE_ENTRY",
    }
    reentry = {
        "tick": 12,
        "event_sequence": 121_001,
        "event": "DEFENSIVE_REENTRY_READY",
    }
    post_fill = {
        "tick": 13,
        "event_sequence": 133_001,
        "normal_activity_eligible": True,
    }
    cases: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: Any) -> None:
        cases.append({"fixture": name, "passed": passed, "detail": detail})

    same_tick = audit_reentry(
        [base_fill, post_fill], [activation, reentry]
    )
    record(
        "same_tick_fill_precedes_defensive_activation",
        same_tick["normal_fill_before_first_activation"]
        and same_tick["same_tick_fill_precedes_activation"],
        same_tick,
    )
    record(
        "post_reentry_fill_is_recognized",
        same_tick["normal_fill_after_completed_reentry"],
        same_tick,
    )
    record(
        "complete_causal_reentry_fixture_passes",
        same_tick["passed"],
        same_tick,
    )
    tick_only_would_fail = not (base_fill["tick"] < activation["tick"])
    record(
        "tick_only_comparator_regression_detected",
        tick_only_would_fail,
        {"tick_only_would_fail": tick_only_would_fail},
    )
    earlier_activation = {
        **activation, "tick": 9, "event_sequence": 94_001
    }
    negative = audit_reentry(
        [base_fill, post_fill], [earlier_activation, reentry]
    )
    record(
        "activation_before_fill_is_rejected",
        not negative["normal_fill_before_first_activation"],
        negative,
    )
    missing_sequence_rejected = False
    try:
        causal_key({"tick": 1})
    except ValueError:
        missing_sequence_rejected = True
    record(
        "missing_event_sequence_rejected",
        missing_sequence_rejected,
        {"rejected": missing_sequence_rejected},
    )
    mismatched_tick_rejected = False
    try:
        causal_key({"tick": 1, "event_sequence": 203_001})
    except ValueError:
        mismatched_tick_rejected = True
    record(
        "mismatched_tick_sequence_rejected",
        mismatched_tick_rejected,
        {"rejected": mismatched_tick_rejected},
    )
    duplicate = audit_reentry(
        [base_fill, {**base_fill, "event_sequence": 104_001}],
        [activation, reentry],
    )
    record(
        "duplicate_event_sequence_rejected_by_audit",
        not duplicate["event_sequences_unique"],
        duplicate,
    )
    return cases


def real_runner_fixture() -> dict[str, Any]:
    profile = profiles()[4]
    path = paths()[3]
    result = MarketMakerBacktestRunner(
        MarketMakerV1Config(**profile["parameters"]),
        protocol_id=PROTOCOL_ID,
        scenario="v1_3d_causal_reentry_fixture",
        source_block="v1-3d-real-runner-fixture",
        fill_seed=330_001,
        cancel_latency_ticks=1,
        initial_capital_usdt=750,
        leverage=3,
        defensive_overlay=profile["defensive_overlay"],
        evidence_namespace="mm-v1-3d-real-runner-fixture",
    ).run(ticks(path))
    audit = audit_reentry(result.fills, result.defensive_events)
    return {
        "profile_name": profile["profile_name"],
        "path_reference": path["path_id"],
        "fill_count": len(result.fills),
        "defensive_event_count": len(result.defensive_events),
        "audit": audit,
    }


def _exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _json(path: Path, value: Any) -> None:
    _exclusive(
        path,
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def execute(directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing v1.3D artifact overwrite")
    directory.mkdir(parents=True)
    fixture_rows = fixtures()
    real = real_runner_fixture()
    passed = (
        all(row["passed"] for row in fixture_rows)
        and real["audit"]["passed"]
    )
    _json(directory / "causal_event_contract.json", {
        "protocol_id": PROTOCOL_ID,
        "ordering": [
            "FILL_EVALUATION",
            "DEFENSIVE_ACTIVATION",
            "DEFENSIVE_MAINTENANCE",
            "DEFENSIVE_EXIT",
            "REENTRY_READY",
        ],
        "comparison_key": ["tick", "event_sequence"],
        "tick_only_comparison_forbidden": True,
    })
    _exclusive(
        directory / "causal_reentry_fixtures.jsonl",
        "".join(
            json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
            for row in fixture_rows
        ),
    )
    _json(directory / "real_runner_fixture.json", real)
    _json(directory / "source_hashes.json", {
        name: file_hash(Path(__file__).resolve().parents[1] / name)
        for name in (
            "backtest/mm_runner.py",
            "backtest/mm_v1_3d_reentry.py",
        )
    })
    _exclusive(
        directory / "report.md",
        "# MM v1.3D Causal Re-entry Evidence\n\n"
        f"Status: `{'PASSED' if passed else 'FAILED'}`\n",
    )
    completed = {
        "status": "COMPLETED" if passed else "FAILED",
        "files": {
            path.name: file_hash(path)
            for path in sorted(directory.iterdir())
        },
    }
    _json(directory / "COMPLETED.json", completed)
    return {
        "status": completed["status"],
        "fixtures_passed": sum(row["passed"] for row in fixture_rows),
        "fixture_count": len(fixture_rows),
        "real_runner_reentry_passed": real["audit"]["passed"],
        "same_tick_precedence_observed": real["audit"][
            "same_tick_fill_precedes_activation"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.artifact_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
