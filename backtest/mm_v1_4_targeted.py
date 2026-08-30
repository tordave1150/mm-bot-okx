"""Freeze, execute once, and analyze the MM v1.4 targeted causal matrix."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3a_diagnostic import (
    validate_references,
    validate_schema,
)
from backtest.mm_v1_3c_evidence import (
    activity_counts,
    reconcile_fill_stream,
)
from backtest.mm_v1_3c_repair import (
    _analysis as legacy_analysis,
    _atomic,
    _canonical_rows as base_canonical_rows,
    _complete,
    _exclusive,
    _json,
    _jsonl,
    _verify_completed,
)
from backtest.mm_v1_3d_reentry import ACTIVATION_EVENTS, causal_key
from backtest.mm_v1_4_protocol import (
    CAPITAL,
    FILL_MODEL,
    PROTOCOL_ID,
    SEVERITY,
    STREAMS,
    build_spec,
    file_hash,
    path_disjointness,
    source_hashes,
    ticks,
)
from market_maker.as_config import MarketMakerV1Config


STATUSES = (
    "MM_V1_4_EVIDENCE_FAILED",
    "MM_V1_4_SAFETY_FAILED",
    "MM_V1_4_ACTIVITY_INSUFFICIENT",
    "MM_V1_4_NO_RESILIENCE_IMPROVEMENT",
    "MM_V1_4_RESILIENCE_IMPROVED_BUT_BUDGET_REJECTED",
    "MM_V1_4_TARGETED_RESILIENCE_SUPPORTED",
)


def _phase(tick: int) -> str:
    if tick <= 60:
        return "NORMAL_PRELUDE"
    if tick <= 180:
        return "STRESS"
    return "RECOVERY"


def _canonical_rows(
    profile: dict[str, Any],
    path: dict[str, Any],
    path_ticks: list[dict[str, Any]],
    result: Any,
) -> dict[str, list[dict[str, Any]]]:
    rows = base_canonical_rows(profile, path, path_ticks, result)
    raw_fills = {row["fill_id"]: row for row in result.fills}
    for fill in rows["fills.jsonl"]:
        raw = raw_fills[fill["fill_id"]]
        fill["event_phase"] = raw["event_phase"]
        fill["event_sequence"] = raw["event_sequence"]
        fill["regime_phase"] = _phase(int(fill["tick"]))
    for event in rows["defensive_events.jsonl"]:
        event["regime_phase"] = _phase(int(event["tick"]))
    for event in rows["market_events.jsonl"]:
        event["regime_phase"] = _phase(int(event["tick"]))
    for event in rows["quote_events.jsonl"]:
        event["regime_phase"] = _phase(int(event["tick"]))
    meta = rows["path_results.jsonl"][0]
    normal = [
        row for row in rows["fills.jsonl"]
        if row["normal_activity_eligible"]
    ]
    meta["normal_fills_by_phase"] = {
        phase: sum(row["regime_phase"] == phase for row in normal)
        for phase in ("NORMAL_PRELUDE", "STRESS", "RECOVERY")
    }
    return rows


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing v1.4 specification overwrite")
    spec = build_spec(root)
    if len(spec["profiles"]) != 8 or len(spec["paths"]) != 16:
        raise RuntimeError("v1.4 fixed matrix cardinality failed")
    if not spec["path_disjointness"]["passed"]:
        raise RuntimeError("v1.4 paths are not disjoint")
    directory.mkdir(parents=True)
    _atomic(directory / "protocol_spec.json", spec)
    specification_hash = file_hash(directory / "protocol_spec.json")
    _exclusive(
        directory / "protocol_spec.sha256",
        f"{specification_hash}  protocol_spec.json\n",
    )
    for filename, key in (
        ("fixed_profiles.json", "profiles"),
        ("capital_policy.json", "capital_policy"),
        ("severity_ladder.json", "severity_ladder"),
        ("stress_path_matrix.json", "paths"),
        ("activity_floor.json", "activity_floor"),
        ("reentry_requirements.json", "reentry_requirements"),
        ("resilience_budget.json", "resilience_budget"),
    ):
        _json(directory / filename, spec[key])
    _json(directory / "fill_model_policy.json", {
        "fill_model": FILL_MODEL,
        "canonical_classification_at_creation": True,
        "strict_trade_through_only": True,
    })
    _json(directory / "artifact_contract.json", {
        "streams": list(STREAMS),
        "write_during_execution": True,
        "hash_every_stream": True,
        "completed_last": True,
        "refuse_overwrite": True,
    })
    _exclusive(
        directory / "protocol_spec.md",
        "# MM v1.4 Targeted Causal Defense\n\n"
        f"`{PROTOCOL_ID}`\n\n"
        f"SHA-256: `{specification_hash}`\n",
    )
    return {
        "protocol_id": PROTOCOL_ID,
        "specification_sha256": specification_hash,
        "profiles": 8,
        "paths": 16,
    }


def _decision_quotes(
    rows: list[dict[str, Any]],
    ids: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if (row["profile_id"], row["path_id"]) in ids
        and row.get("decision") != "SPECIAL_EXIT"
    ]


def _activity(
    profile_id: str,
    metas: list[dict[str, Any]],
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    selected = [
        row for row in metas
        if row["profile_id"] == profile_id
        and row["severity"] in {"S2_5", "S3_LOW"}
    ]
    ids = {(row["profile_id"], row["path_id"]) for row in selected}
    fills = [
        row for row in rows["fills.jsonl"]
        if (row["profile_id"], row["path_id"]) in ids
    ]
    normal = [row for row in fills if row["normal_activity_eligible"]]
    trips = [
        row for row in rows["round_trips.jsonl"]
        if (row["profile_id"], row["path_id"]) in ids
    ]
    quotes = _decision_quotes(rows["quote_events.jsonl"], ids)
    counts = activity_counts(fills)
    total = len(quotes)
    result = {
        **counts,
        "normal_fifo_round_trips": len(trips),
        "normal_fills_by_phase": {
            phase: sum(row["regime_phase"] == phase for row in normal)
            for phase in ("NORMAL_PRELUDE", "STRESS", "RECOVERY")
        },
        "represented_scenario_families": len({
            row["scenario"] for row in normal
        }),
        "two_sided_quote_rate": sum(
            row["quote_mode"] == "TWO_SIDED" for row in quotes
        ) / max(total, 1),
        "one_sided_quote_rate": sum(
            row["quote_mode"] in {"ONE_SIDED_BID", "ONE_SIDED_ASK"}
            for row in quotes
        ) / max(total, 1),
        "no_quote_rate": sum(
            row["quote_mode"] in {
                "NO_QUOTE", "TERMINATED_AFTER_HARD_KILL"
            }
            for row in quotes
        ) / max(total, 1),
        "quote_eligible_ticks": sum(
            int(row["quote_eligible_ticks"]) for row in selected
        ),
    }
    result["passed"] = (
        result["strict_trade_through_maker_fills"] >= 8
        and result["normal_fills_by_phase"]["STRESS"] >= 4
        and result["normal_fills_by_phase"]["RECOVERY"] >= 2
        and result["bid_normal_fills"] > 0
        and result["ask_normal_fills"] > 0
        and result["normal_fifo_round_trips"] >= 2
        and result["represented_scenario_families"] >= 3
        and result["two_sided_quote_rate"] >= 0.05
        and result["no_quote_rate"] <= 0.85
        and result["quote_eligible_ticks"] > 0
    )
    return result


def _reentry(
    profile: dict[str, Any],
    activity: dict[str, Any],
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    profile_id = profile["profile_id"]
    normal = [
        row for row in rows["fills.jsonl"]
        if row["profile_id"] == profile_id
        and row["severity"] in {"S2_5", "S3_LOW"}
        and row["normal_activity_eligible"]
    ]
    events = [
        row for row in rows["defensive_events.jsonl"]
        if row["profile_id"] == profile_id
        and row["severity"] in {"S2_5", "S3_LOW"}
    ]
    activations = [
        row for row in events
        if row["event"] in ACTIVATION_EVENTS
        or row["event"] == "ONE_SIDED_DEFENSIVE_ENTRY"
    ]
    reentries = [
        row for row in events
        if row["event"] == "DEFENSIVE_REENTRY_READY"
    ]
    exits = [
        row for row in events if row["event"] == "DEFENSIVE_MODE_EXIT"
    ]
    for row in [*normal, *activations, *reentries, *exits]:
        causal_key(row)
    first_by_path: dict[str, dict[str, Any]] = {}
    for activation in activations:
        existing = first_by_path.get(activation["path_id"])
        if existing is None or causal_key(activation) < causal_key(existing):
            first_by_path[activation["path_id"]] = activation
    before = any(
        fill["path_id"] == path_id
        and causal_key(fill) < causal_key(activation)
        for path_id, activation in first_by_path.items()
        for fill in normal
    )
    after = any(
        fill["path_id"] == reentry["path_id"]
        and causal_key(fill) > causal_key(reentry)
        for reentry in reentries
        for fill in normal
    )
    same_tick = any(
        fill["path_id"] == activation["path_id"]
        and fill["tick"] == activation["tick"]
        and causal_key(fill) < causal_key(activation)
        for fill in normal
        for activation in activations
    )
    is_control = profile["profile_name"] in {
        "BASELINE_CONTROL", "WIDER_SPREAD_CONTROL"
    }
    exit_count = max(len(exits), len(reentries))
    result = {
        "normal_fill_before_first_activation": before,
        "normal_fill_after_completed_reentry": after,
        "same_tick_fill_precedes_activation": same_tick,
        "activation_count": len(activations),
        "defensive_mode_exit_count": exit_count,
        "completed_reentry_count": len(reentries),
        "recovery_normal_fills": activity["normal_fills_by_phase"][
            "RECOVERY"
        ],
        "permanently_one_sided": (
            activity["one_sided_quote_rate"] > 0
            and activity["two_sided_quote_rate"] == 0
        ),
        "not_applicable_control": is_control,
    }
    result["passed"] = (
        True
        if is_control
        else (
            before
            and after
            and exit_count >= 1
            and result["recovery_normal_fills"] >= 2
            and not result["permanently_one_sided"]
        )
    )
    return result


def _analyze(
    spec: dict[str, Any],
    metas: list[dict[str, Any]],
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    analysis = legacy_analysis(spec, metas, rows)
    activities = {
        profile["profile_id"]: _activity(
            profile["profile_id"], metas, rows
        )
        for profile in spec["profiles"]
    }
    reentry = {
        profile["profile_id"]: _reentry(
            profile, activities[profile["profile_id"]], rows
        )
        for profile in spec["profiles"]
    }
    baseline_id = spec["profiles"][0]["profile_id"]
    baseline_markout = analysis["profile_comparison"][baseline_id][
        "markout"
    ]
    resilience: dict[str, Any] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        budget = {
            severity: (
                analysis["severity_results"][profile_id][severity][
                    "hard_kills"
                ] == 0
                and analysis["severity_results"][profile_id][severity][
                    "worst_drawdown"
                ]
                <= spec["resilience_budget"][severity][
                    "worst_drawdown_max"
                ]
            )
            for severity in ("S2_5", "S3_LOW", "S3_MID")
        }
        comparison = analysis["profile_comparison"][profile_id]
        resilience[profile_id] = {
            "budget_by_severity": budget,
            "activity_passed": activities[profile_id]["passed"],
            "reentry_passed": reentry[profile_id]["passed"],
            "improves_over_both_controls": (
                comparison["improvement_count"] >= 3
            ),
            "markout_improves_versus_baseline": (
                comparison["markout"] > baseline_markout
            ),
        }
        resilience[profile_id]["passed"] = (
            all(budget.values())
            and resilience[profile_id]["activity_passed"]
            and resilience[profile_id]["reentry_passed"]
            and resilience[profile_id]["improves_over_both_controls"]
            and resilience[profile_id]["markout_improves_versus_baseline"]
        )
    analysis["activity_floor_results"] = activities
    analysis["reentry_results"] = reentry
    analysis["resilience_results"] = resilience
    return analysis


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def run(
    root: Path,
    specification_dir: Path,
    run_dir: Path,
    analysis_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if any(path.exists() for path in (run_dir, analysis_dir, decision_dir)):
        raise FileExistsError("refusing v1.4 run-ID reuse")
    spec_path = specification_dir / "protocol_spec.json"
    expected_hash = (
        specification_dir / "protocol_spec.sha256"
    ).read_text().split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("v1.4 specification hash mismatch")
    spec = json.loads(spec_path.read_text())
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after v1.4 freeze")
    if not path_disjointness()["passed"]:
        raise RuntimeError("v1.4 path disjointness failed")
    run_dir.mkdir(parents=True)
    handles = {
        name: (run_dir / name).open(
            "x", encoding="utf-8", newline="\n"
        )
        for name in STREAMS
    }
    rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in STREAMS
    }
    metas: list[dict[str, Any]] = []
    try:
        for profile in spec["profiles"]:
            for path in spec["paths"]:
                path_ticks = ticks(path)
                result = MarketMakerBacktestRunner(
                    MarketMakerV1Config(**profile["parameters"]),
                    protocol_id=PROTOCOL_ID,
                    scenario=path["scenario"],
                    source_block=path["source_block"],
                    fill_seed=path["fill_seed"],
                    cancel_latency_ticks=path["parameters"]["cancel_delay"],
                    initial_capital_usdt=CAPITAL,
                    leverage=3,
                    defensive_overlay=profile["defensive_overlay"],
                    evidence_namespace=(
                        f"{profile['profile_id']}-{path['path_id']}"
                    ),
                ).run(path_ticks)
                normalized = _canonical_rows(
                    profile, path, path_ticks, result
                )
                for name, values in normalized.items():
                    for value in values:
                        handles[name].write(
                            json.dumps(
                                value, sort_keys=True, allow_nan=False
                            )
                            + "\n"
                        )
                    handles[name].flush()
                    rows[name].extend(values)
                metas.extend(normalized["path_results.jsonl"])
    finally:
        for handle in handles.values():
            handle.close()

    profile_ids = {row["profile_id"] for row in spec["profiles"]}
    path_ids = {row["path_id"] for row in spec["paths"]}
    schema_invalid = sum(
        not validate_schema(name, row)
        for name, values in rows.items()
        if name != "defensive_events.jsonl"
        for row in values
    )
    references = validate_references(
        {
            name: values for name, values in rows.items()
            if name != "defensive_events.jsonl"
        },
        path_ids,
        profile_ids,
    )
    classification = reconcile_fill_stream(rows["fills.jsonl"])
    analysis = _analyze(spec, metas, rows)
    quote_modes = all(
        row["unclassified_quote_mode_ticks"] == 0 for row in metas
    )
    semantic_integrity = (
        classification["passed"]
        and schema_invalid == 0
        and references["passed"]
        and quote_modes
        and _finite({"metas": metas, "analysis": analysis})
        and len(rows["path_results.jsonl"]) == 128
    )
    safety = {
        "preventable_margin_breaches": sum(
            row["preventable_margin_breaches"] for row in metas
        ),
        "margin_breaches": sum(row["margin_breaches"] for row in metas),
        "inventory_breaches": sum(
            row["inventory_breaches"] for row in metas
        ),
        "unknown_margin_states": sum(
            row["unknown_margin_states"] for row in metas
        ),
        "terminal_residual_inventory": sum(
            row["terminal_residual_inventory"] for row in metas
        ),
    }
    safety["passed"] = all(
        math.isclose(float(value), 0.0, abs_tol=1e-12)
        for value in safety.values()
    )
    defensive = spec["profiles"][2:]
    activity_passers = [
        profile for profile in defensive
        if analysis["activity_floor_results"][profile["profile_id"]][
            "passed"
        ]
    ]
    reentry_passers = [
        profile for profile in activity_passers
        if analysis["reentry_results"][profile["profile_id"]]["passed"]
    ]
    improvements = [
        profile for profile in reentry_passers
        if analysis["profile_comparison"][profile["profile_id"]][
            "improvement_count"
        ] >= 3
    ]
    budget_passers = [
        profile for profile in improvements
        if analysis["resilience_results"][profile["profile_id"]]["passed"]
    ]
    if not semantic_integrity:
        status, gate = STATUSES[0], "GATE_0_EVIDENCE"
    elif not safety["passed"]:
        status, gate = STATUSES[1], "GATE_1_SAFETY"
    elif not activity_passers:
        status, gate = STATUSES[2], "GATE_2_ACTIVITY"
    elif not improvements:
        status, gate = STATUSES[3], "GATE_3_IMPROVEMENT"
    elif not budget_passers:
        status, gate = STATUSES[4], "GATE_4_BUDGET"
    else:
        status, gate = STATUSES[5], None
    best = max(
        defensive,
        key=lambda profile: (
            analysis["resilience_results"][profile["profile_id"]]["passed"],
            analysis["reentry_results"][profile["profile_id"]]["passed"],
            analysis["activity_floor_results"][profile["profile_id"]][
                "passed"
            ],
            analysis["profile_comparison"][profile["profile_id"]][
                "improvement_count"
            ],
            analysis["profile_comparison"][profile["profile_id"]][
                "net_pnl"
            ],
        ),
    )
    stream_manifest = {
        "streams": {
            name: {
                "records": len(rows[name]),
                "sha256": file_hash(run_dir / name),
                "empty": not rows[name],
                "applicability": (
                    "NOT_APPLICABLE_STRICT_BOOK_MODEL"
                    if name == "trade_events.jsonl" and not rows[name]
                    else "APPLICABLE"
                ),
            }
            for name in STREAMS
        }
    }
    summary = {
        "status": status,
        "first_failed_gate": gate,
        "semantic_integrity": semantic_integrity,
        "classification_audit": classification,
        "safety": safety,
        "analysis": analysis,
        "profiles_passing_activity": len(activity_passers),
        "profiles_passing_reentry": len(reentry_passers),
        "profiles_improving_over_controls": len(improvements),
        "profiles_passing_budget": len(budget_passers),
        "best_defensive_profile": best["profile_name"],
    }
    _atomic(run_dir / "stream_manifest.json", stream_manifest)
    _atomic(run_dir / "stress_summary.json", summary)
    _atomic(run_dir / "run_manifest.json", {
        "protocol_id": PROTOCOL_ID,
        "specification_sha256": expected_hash,
        "source_hashes": spec["source_hashes"],
        "profile_fingerprints": {
            profile["profile_id"]: profile["profile_fingerprint"]
            for profile in spec["profiles"]
        },
        "path_hashes": {
            path["path_id"]: path["path_hash"] for path in spec["paths"]
        },
        "matrix_execution_count": 1,
        "prior_matrix_reruns": {
            "v1_3": False,
            "v1_3a": False,
            "v1_3b": False,
            "v1_3c": False,
        },
        "optimization": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    })
    _complete(run_dir)
    if not _verify_completed(run_dir):
        raise RuntimeError("v1.4 completion hash verification failed")

    analysis_dir.mkdir(parents=True)
    _json(analysis_dir / "classification_audit.json", {
        **classification,
        "schema_invalid": schema_invalid,
        "cross_references": references,
        "quote_modes_reconcile": quote_modes,
        "stream_hashes_valid": True,
        "completion_hashes_valid": True,
        "semantic_integrity": semantic_integrity,
    })
    for filename, key in (
        ("activity_floor_results.json", "activity_floor_results"),
        ("reentry_results.json", "reentry_results"),
        ("profile_comparison.json", "profile_comparison"),
        ("severity_results.json", "severity_results"),
        ("resilience_results.json", "resilience_results"),
        ("hard_kill_attribution.json", "hard_kill_attribution"),
    ):
        _json(analysis_dir / filename, analysis[key])
    _jsonl(
        analysis_dir / "toxic_fill_timelines.jsonl",
        analysis["toxic_fill_timelines"],
    )
    _exclusive(
        analysis_dir / "analysis_report.md",
        "# MM v1.4 Analysis\n\n"
        f"`{status}`\n\nFirst failed gate: `{gate}`\n",
    )
    decision_dir.mkdir(parents=True)
    decision = {
        "status": status,
        "first_failed_gate": gate,
        "misclassified_maker_fills": sum(
            row["maker_or_taker"] == "maker"
            and row["special_exit"]
            for row in rows["fills.jsonl"]
        ),
        "unclassified_fills": classification["unclassified_fills"],
        "normal_activity_fills": classification["normal_activity_fills"],
        "normal_fifo_round_trips": len(rows["round_trips.jsonl"]),
        "profiles_passing_activity": len(activity_passers),
        "profiles_passing_reentry": len(reentry_passers),
        "profiles_improving_over_controls": len(improvements),
        "profiles_passing_budget": len(budget_passers),
        "best_defensive_profile": best["profile_name"],
        "preventable_margin_breaches": safety[
            "preventable_margin_breaches"
        ],
        "inventory_breaches": safety["inventory_breaches"],
        "matrix_execution_count": 1,
        "prior_matrix_reruns": False,
        "optuna": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    }
    _atomic(decision_dir / "decision.json", decision)
    _exclusive(
        decision_dir / "decision.md",
        "# MM v1.4 Decision\n\n"
        f"`{status}`\n\nFirst failed gate: `{gate}`\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    declare_command = commands.add_parser("declare")
    declare_command.add_argument(
        "--specification-dir", type=Path, required=True
    )
    run_command = commands.add_parser("run")
    for name in ("specification", "run", "analysis", "decision"):
        run_command.add_argument(f"--{name}-dir", type=Path, required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = (
        declare(root, arguments.specification_dir)
        if arguments.command == "declare"
        else run(
            root,
            arguments.specification_dir,
            arguments.run_dir,
            arguments.analysis_dir,
            arguments.decision_dir,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
