"""Recover v1.5 reporting from a complete, already-written raw matrix.

This module never invokes MarketMakerBacktestRunner.  It exists solely because
the first formal report pass referenced a non-existent ``market_ticks`` path
metadata field after all 128 simulations and streams had already completed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from backtest.mm_v1_3a_diagnostic import (
    validate_references,
    validate_schema,
)
from backtest.mm_v1_3c_evidence import reconcile_fill_stream
from backtest.mm_v1_3c_repair import (
    _atomic,
    _complete,
    _exclusive,
    _json,
    _jsonl,
    _verify_completed,
)
from backtest.mm_v1_5_drawdown import (
    REQUIRED_SEVERITIES,
    STATUSES,
    _analyze,
    _event_order_audit,
    _finite,
)
from backtest.mm_v1_5_protocol import (
    PROTOCOL_ID,
    STREAMS,
    file_hash,
    path_disjointness,
    source_hashes,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def recover(
    root: Path,
    specification_dir: Path,
    run_dir: Path,
    analysis_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if (run_dir / "COMPLETED").exists():
        raise FileExistsError("refusing completed v1.5 run recovery")
    if analysis_dir.exists() or decision_dir.exists():
        raise FileExistsError("refusing v1.5 analysis recovery overwrite")
    spec_path = specification_dir / "protocol_spec.json"
    expected_hash = (
        specification_dir / "protocol_spec.sha256"
    ).read_text().split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("v1.5 specification hash mismatch")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered execution source changed after freeze")
    if not path_disjointness()["passed"]:
        raise RuntimeError("v1.5 path disjointness failed")
    if not all((run_dir / name).is_file() for name in STREAMS):
        raise RuntimeError("v1.5 raw stream set is incomplete")

    pre_recovery_stream_hashes = {
        name: file_hash(run_dir / name) for name in STREAMS
    }
    rows = {
        name: _load_jsonl(run_dir / name) for name in STREAMS
    }
    metas = rows["path_results.jsonl"]
    if len(metas) != 128:
        raise RuntimeError("v1.5 raw matrix is not complete")

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
    expected_ticks = (
        int(spec["paths"][0]["index_range"][1])
        - int(spec["paths"][0]["index_range"][0])
        + 1
    )
    quote_modes = all(
        row["unclassified_quote_mode_ticks"] == 0
        and (
            row["two_sided_ticks"]
            + row["one_sided_ticks"]
            + row["no_quote_ticks"]
            == expected_ticks
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
        and len(metas) == 128
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
    recovery_script = Path(__file__).resolve()
    recovery_record = {
        "reason": (
            "Post-simulation reporter referenced absent path metadata field "
            "'market_ticks'; raw 128-path matrix was already complete."
        ),
        "raw_path_result_count": len(metas),
        "simulation_rerun": False,
        "formal_matrix_execution_count": 1,
        "recovery_scope": "ANALYSIS_AND_MANIFEST_ONLY",
        "covered_execution_sources_unchanged": True,
        "recovery_script": str(recovery_script.relative_to(root)),
        "recovery_script_sha256": file_hash(recovery_script),
        "pre_recovery_raw_stream_sha256": pre_recovery_stream_hashes,
    }
    stream_manifest = {
        "streams": {
            name: {
                "records": len(rows[name]),
                "sha256": pre_recovery_stream_hashes[name],
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
        "execution_recovery": recovery_record,
    }
    _atomic(run_dir / "stream_manifest.json", stream_manifest)
    _atomic(run_dir / "stress_summary.json", summary)
    _atomic(run_dir / "execution_recovery.json", recovery_record)
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
        "analysis_recovery": True,
        "simulation_rerun": False,
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
        raise RuntimeError("v1.5 recovered completion hash verification failed")

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
    _json(analysis_dir / "execution_recovery.json", recovery_record)
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
        f"Best profile: `{best['profile_name']}`\n\n"
        "Reporting recovery only; simulation rerun: `False`.\n",
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
        "simulation_rerun": False,
        "analysis_recovery": True,
        "prior_matrix_reruns": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    }
    _atomic(decision_dir / "decision.json", decision)
    _atomic(decision_dir / "execution_recovery.json", recovery_record)
    _exclusive(
        decision_dir / "decision.md",
        "# MM v1.5 Decision\n\n"
        f"`{status}`\n\n"
        f"First failed gate: `{gate}`\n\n"
        f"Best profile: `{best['profile_name']}`\n\n"
        f"Optuna eligible: `{optuna_eligible}`; executed: `False`\n\n"
        "Reporting recovery only; simulation rerun: `False`.\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--specification-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--decision-dir", type=Path, required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = recover(
        root,
        arguments.specification_dir,
        arguments.run_dir,
        arguments.analysis_dir,
        arguments.decision_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
