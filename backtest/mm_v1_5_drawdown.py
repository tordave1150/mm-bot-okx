"""Freeze, execute once, and analyze the MM v1.5 drawdown repair matrix."""

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
from backtest.mm_v1_3c_evidence import reconcile_fill_stream
from backtest.mm_v1_3c_repair import (
    _analysis as legacy_analysis,
    _atomic,
    _complete,
    _exclusive,
    _json,
    _jsonl,
    _verify_completed,
)
from backtest.mm_v1_4_targeted import (
    _activity,
    _canonical_rows,
    _finite,
    _reentry,
)
from backtest.mm_v1_5_protocol import (
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
    "MM_V1_5_EVIDENCE_FAILED",
    "MM_V1_5_SAFETY_FAILED",
    "MM_V1_5_ACTIVITY_INSUFFICIENT",
    "MM_V1_5_CAUSAL_REENTRY_FAILED",
    "MM_V1_5_REQUIRED_BUDGETS_FAILED",
    "MM_V1_5_DRAWDOWN_REPAIR_SUPPORTED",
)
REQUIRED_SEVERITIES = ("S2_5", "S3_LOW", "S3_MID")


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing v1.5 specification overwrite")
    spec = build_spec(root)
    if len(spec["profiles"]) != 8 or len(spec["paths"]) != 16:
        raise RuntimeError("v1.5 fixed matrix cardinality failed")
    if not spec["path_disjointness"]["passed"]:
        raise RuntimeError("v1.5 paths are not disjoint")
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
        ("optuna_unlock_gate.json", "optuna_unlock_gate"),
        ("selection_policy.json", "selection_policy"),
    ):
        _json(directory / filename, spec[key])
    _json(directory / "fill_model_policy.json", {
        "fill_model": FILL_MODEL,
        "canonical_classification_at_creation": True,
        "strict_trade_through_only": True,
        "emergency_execution_is_special_exit": True,
        "emergency_execution_activity_eligible": False,
    })
    _json(directory / "artifact_contract.json", {
        "streams": list(STREAMS),
        "write_during_execution": True,
        "hash_every_stream": True,
        "completed_last": True,
        "refuse_overwrite": True,
        "formal_matrix_execution_count": 1,
    })
    _exclusive(
        directory / "execution_plan.md",
        "# MM v1.5 Drawdown Repair Plan\n\n"
        "1. Preserve the closed v1.4 timeboxed profile as a control.\n"
        "2. Isolate longer timebox, causal reentry confirmation, and "
        "inventory-aware reentry.\n"
        "3. Test a fee-inclusive 3% capital-preservation guard separately "
        "and in the causal composite.\n"
        "4. Reject any profile that loses the activity floor, causal "
        "reentry, or any S2_5/S3_LOW/S3_MID drawdown budget.\n"
        "5. Do not execute Optuna, validation, or holdout in this protocol.\n",
    )
    _exclusive(
        directory / "protocol_spec.md",
        "# MM v1.5 Timeboxed Drawdown Repair\n\n"
        f"`{PROTOCOL_ID}`\n\n"
        f"SHA-256: `{specification_hash}`\n",
    )
    return {
        "protocol_id": PROTOCOL_ID,
        "specification_sha256": specification_hash,
        "profiles": 8,
        "paths": 16,
        "formal_matrix_execution_count": 0,
        "optuna_executed": False,
    }


def _event_order_audit(
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    emergency = [
        row for row in rows["fills.jsonl"]
        if row["fill_trigger"] == "EMERGENCY_EXECUTION"
    ]
    entries = [
        row for row in rows["defensive_events.jsonl"]
        if row["event"] == "DRAWDOWN_GUARD_ENTRY"
    ]
    entry_by_key = {
        (row["profile_id"], row["path_id"], int(row["tick"])): row
        for row in entries
    }
    missing_entry = 0
    sequence_failures = 0
    for fill in emergency:
        entry = entry_by_key.get((
            fill["profile_id"],
            fill["path_id"],
            int(fill["tick"]),
        ))
        if entry is None:
            missing_entry += 1
            continue
        if not int(fill["event_sequence"]) < int(entry["event_sequence"]):
            sequence_failures += 1
    result = {
        "emergency_fill_count": len(emergency),
        "drawdown_guard_entry_count": len(entries),
        "missing_same_tick_guard_entry": missing_entry,
        "fill_before_guard_entry_failures": sequence_failures,
        "event_order": (
            "FILL_EVALUATION < DEFENSIVE_ACTIVATION < "
            "DEFENSIVE_MAINTENANCE < DEFENSIVE_EXIT < "
            "EMERGENCY_EXECUTION < CAPITAL_PRESERVATION < HARD_KILL"
        ),
    }
    result["passed"] = missing_entry == 0 and sequence_failures == 0
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
    timeboxed_id = spec["profiles"][1]["profile_id"]
    timeboxed_fills = max(
        int(activities[timeboxed_id]["strict_trade_through_maker_fills"]),
        1,
    )
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        retention = (
            int(activities[profile_id]["strict_trade_through_maker_fills"])
            / timeboxed_fills
        )
        activities[profile_id]["normal_fill_retention_vs_timeboxed"] = (
            retention
        )
        activities[profile_id]["retention_passed"] = (
            True
            if profile_id == spec["profiles"][0]["profile_id"]
            else retention
            >= spec["activity_floor"][
                "normal_fill_retention_vs_timeboxed_min"
            ]
        )
        activities[profile_id]["passed_with_retention"] = (
            activities[profile_id]["passed"]
            and activities[profile_id]["retention_passed"]
        )
    reentry = {
        profile["profile_id"]: _reentry(
            profile, activities[profile["profile_id"]], rows
        )
        for profile in spec["profiles"]
    }
    timeboxed_severity = analysis["severity_results"][timeboxed_id]
    resilience: dict[str, Any] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        budget = {
            severity: (
                analysis["severity_results"][profile_id][severity][
                    "hard_kills"
                ]
                <= spec["resilience_budget"][severity]["hard_kills"]
                and analysis["severity_results"][profile_id][severity][
                    "worst_drawdown"
                ]
                <= spec["resilience_budget"][severity][
                    "worst_drawdown_max"
                ]
            )
            for severity in REQUIRED_SEVERITIES
        }
        path_metas = [
            row for row in metas if row["profile_id"] == profile_id
        ]
        accounting = all(
            bool(row["accounting_reconciles"]) for row in path_metas
        )
        worst_required_drawdown = max(
            analysis["severity_results"][profile_id][severity][
                "worst_drawdown"
            ]
            for severity in REQUIRED_SEVERITIES
        )
        timeboxed_worst = max(
            timeboxed_severity[severity]["worst_drawdown"]
            for severity in REQUIRED_SEVERITIES
        )
        result = {
            "budget_by_severity": budget,
            "all_required_budgets_passed": all(budget.values()),
            "activity_passed": activities[profile_id][
                "passed_with_retention"
            ],
            "causal_reentry_passed": reentry[profile_id]["passed"],
            "accounting_reconciles": accounting,
            "worst_required_drawdown": worst_required_drawdown,
            "drawdown_reduction_vs_timeboxed": (
                timeboxed_worst - worst_required_drawdown
            ),
            "drawdown_by_severity_vs_timeboxed": {
                severity: (
                    timeboxed_severity[severity]["worst_drawdown"]
                    - analysis["severity_results"][profile_id][severity][
                        "worst_drawdown"
                    ]
                )
                for severity in REQUIRED_SEVERITIES
            },
        }
        result["passed"] = (
            result["all_required_budgets_passed"]
            and result["activity_passed"]
            and result["causal_reentry_passed"]
            and result["accounting_reconciles"]
        )
        resilience[profile_id] = result
    analysis["activity_floor_results"] = activities
    analysis["reentry_results"] = reentry
    analysis["resilience_results"] = resilience
    return analysis


def run(
    root: Path,
    specification_dir: Path,
    run_dir: Path,
    analysis_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if any(path.exists() for path in (run_dir, analysis_dir, decision_dir)):
        raise FileExistsError("refusing v1.5 run-ID reuse")
    spec_path = specification_dir / "protocol_spec.json"
    expected_hash = (
        specification_dir / "protocol_spec.sha256"
    ).read_text().split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("v1.5 specification hash mismatch")
    spec = json.loads(spec_path.read_text())
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after v1.5 freeze")
    if not path_disjointness()["passed"]:
        raise RuntimeError("v1.5 path disjointness failed")

    run_dir.mkdir(parents=True)
    handles = {
        name: (run_dir / name).open("x", encoding="utf-8", newline="\n")
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
    event_order = _event_order_audit(rows)
    analysis = _analyze(spec, metas, rows)
    quote_modes = all(
        row["unclassified_quote_mode_ticks"] == 0
        and (
            row["two_sided_ticks"]
            + row["one_sided_ticks"]
            + row["no_quote_ticks"]
            == row["market_ticks"]
        )
        for row in metas
    )
    semantic_integrity = (
        classification["passed"]
        and schema_invalid == 0
        and references["passed"]
        and quote_modes
        and event_order["passed"]
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

    candidates = spec["profiles"][2:]
    activity_passers = [
        profile for profile in candidates
        if analysis["activity_floor_results"][profile["profile_id"]][
            "passed_with_retention"
        ]
    ]
    reentry_passers = [
        profile for profile in activity_passers
        if analysis["reentry_results"][profile["profile_id"]]["passed"]
    ]
    budget_passers = [
        profile for profile in reentry_passers
        if analysis["resilience_results"][profile["profile_id"]][
            "all_required_budgets_passed"
        ]
    ]
    supported = [
        profile for profile in budget_passers
        if analysis["resilience_results"][profile["profile_id"]]["passed"]
    ]
    if not semantic_integrity:
        status, gate = STATUSES[0], "GATE_0_EVIDENCE"
    elif not safety["passed"]:
        status, gate = STATUSES[1], "GATE_1_SAFETY"
    elif not activity_passers:
        status, gate = STATUSES[2], "GATE_2_ACTIVITY"
    elif not reentry_passers:
        status, gate = STATUSES[3], "GATE_3_CAUSAL_REENTRY"
    elif not budget_passers:
        status, gate = STATUSES[4], "GATE_4_REQUIRED_BUDGETS"
    else:
        status, gate = STATUSES[5], None

    best = max(
        candidates,
        key=lambda profile: (
            analysis["resilience_results"][profile["profile_id"]]["passed"],
            sum(
                analysis["resilience_results"][profile["profile_id"]][
                    "budget_by_severity"
                ].values()
            ),
            analysis["activity_floor_results"][profile["profile_id"]][
                "passed_with_retention"
            ],
            analysis["reentry_results"][profile["profile_id"]]["passed"],
            -analysis["resilience_results"][profile["profile_id"]][
                "worst_required_drawdown"
            ],
            analysis["profile_comparison"][profile["profile_id"]]["net_pnl"],
        ),
    )
    best_id = best["profile_id"]
    optuna_eligible = bool(supported) and semantic_integrity and safety["passed"]
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
        "event_order_audit": event_order,
        "safety": safety,
        "analysis": analysis,
        "profiles_passing_activity_and_retention": len(activity_passers),
        "profiles_passing_causal_reentry": len(reentry_passers),
        "profiles_passing_required_budgets": len(budget_passers),
        "profiles_supported": len(supported),
        "best_profile": best["profile_name"],
        "best_profile_resilience": analysis["resilience_results"][best_id],
        "best_profile_activity": analysis["activity_floor_results"][best_id],
        "best_profile_reentry": analysis["reentry_results"][best_id],
        "optuna_eligible_after_protocol": optuna_eligible,
        "optuna_executed": False,
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
        "prior_matrix_reruns": False,
        "optimization": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    })
    _complete(run_dir)
    if not _verify_completed(run_dir):
        raise RuntimeError("v1.5 completion hash verification failed")

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
    _json(analysis_dir / "event_order_audit.json", event_order)
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
        "# MM v1.5 Drawdown Analysis\n\n"
        f"`{status}`\n\n"
        f"First failed gate: `{gate}`\n\n"
        f"Best profile: `{best['profile_name']}`\n",
    )
    decision_dir.mkdir(parents=True)
    decision = {
        "status": status,
        "first_failed_gate": gate,
        "semantic_integrity": semantic_integrity,
        "safety_passed": safety["passed"],
        "best_profile": best["profile_name"],
        "best_profile_id": best_id,
        "required_budget_results": analysis["resilience_results"][best_id][
            "budget_by_severity"
        ],
        "worst_required_drawdown": analysis["resilience_results"][best_id][
            "worst_required_drawdown"
        ],
        "normal_fill_retention_vs_timeboxed": analysis[
            "activity_floor_results"
        ][best_id]["normal_fill_retention_vs_timeboxed"],
        "activity_passed": analysis["activity_floor_results"][best_id][
            "passed_with_retention"
        ],
        "causal_reentry_passed": analysis["reentry_results"][best_id][
            "passed"
        ],
        "emergency_execution_count": event_order[
            "emergency_fill_count"
        ],
        "profiles_passing_required_budgets": len(budget_passers),
        "profiles_supported": len(supported),
        "optuna_eligible_after_protocol": optuna_eligible,
        "optuna_executed": False,
        "matrix_execution_count": 1,
        "prior_matrix_reruns": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    }
    _atomic(decision_dir / "decision.json", decision)
    _exclusive(
        decision_dir / "decision.md",
        "# MM v1.5 Decision\n\n"
        f"`{status}`\n\n"
        f"First failed gate: `{gate}`\n\n"
        f"Best profile: `{best['profile_name']}`\n\n"
        f"Optuna eligible: `{optuna_eligible}`; executed: `False`\n",
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
