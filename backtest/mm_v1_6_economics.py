"""Freeze, execute once, and decide MM v1.6 economic viability."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3_smoke import (
    _compact_run,
    aggregate_profile,
    run_balanced_path,
)
from backtest.mm_v1_3a_diagnostic import validate_references, validate_schema
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
from backtest.mm_v1_5_drawdown import _event_order_audit
from backtest.mm_v1_6_protocol import (
    BALANCED_MODEL,
    BALANCED_STREAMS,
    CAPITAL,
    PROTOCOL_ID,
    SEVERITY,
    STRESS_MODEL,
    STRESS_STREAMS,
    balanced_paths,
    build_spec,
    canonical,
    closed_evidence_hashes,
    digest,
    file_hash,
    path_disjointness,
    source_hashes,
    stress_ticks,
)
from market_maker.as_config import MarketMakerV1Config


STATUSES = (
    "MM_V1_6_EVIDENCE_FAILED",
    "MM_V1_6_SAFETY_FAILED",
    "MM_V1_6_ACTIVITY_INSUFFICIENT",
    "MM_V1_6_CAUSAL_REENTRY_FAILED",
    "MM_V1_6_ECONOMIC_GATES_FAILED",
    "MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT",
)
REQUIRED_SEVERITIES = ("S2_5", "S3_LOW", "S3_MID")


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing v1.6 specification overwrite")
    spec = build_spec(root)
    if len(spec["profiles"]) != 8:
        raise RuntimeError("v1.6 requires exactly eight profiles")
    if len(spec["balanced_paths"]) != 12:
        raise RuntimeError("v1.6 requires exactly twelve balanced paths")
    if len(spec["stress_paths"]) != 16:
        raise RuntimeError("v1.6 requires exactly sixteen stress paths")
    if not spec["path_disjointness"]["passed"]:
        raise RuntimeError("v1.6 fresh paths are not disjoint")
    if not all(row["v1_5_overlay_exact"] for row in spec["profiles"]):
        raise RuntimeError("v1.5 defensive overlay drift")
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
        ("balanced_path_matrix.json", "balanced_paths"),
        ("stress_path_matrix.json", "stress_paths"),
        ("severity_ladder.json", "severity_ladder"),
        ("primary_activity_gates.json", "primary_activity_gates"),
        ("primary_economic_gates.json", "primary_economic_gates"),
        ("stress_activity_gates.json", "stress_activity_gates"),
        ("stress_budgets.json", "stress_budgets"),
        ("causal_reentry_requirements.json", "causal_reentry_requirements"),
        ("selection_policy.json", "selection_policy"),
        ("path_disjointness.json", "path_disjointness"),
        ("balanced_trigger_audit.json", "balanced_trigger_audit"),
        ("closed_evidence_hashes.json", "closed_evidence_hashes"),
    ):
        _json(directory / filename, spec[key])
    _json(directory / "fill_model_policy.json", {
        "primary_ranking_model": BALANCED_MODEL,
        "strict_stress_model": STRESS_MODEL,
        "mixed_model_ranking_forbidden": True,
    })
    _json(directory / "artifact_contract.json", {
        "balanced_streams": list(BALANCED_STREAMS),
        "stress_streams": list(STRESS_STREAMS),
        "balanced_matrix_execution_count": 1,
        "stress_matrix_execution_count": 1,
        "refuse_overwrite": True,
        "completed_last": True,
        "recovery_never_reruns_simulation": True,
    })
    _exclusive(
        directory / "protocol_spec.md",
        "# MM v1.6 Economic Viability Before Optimization\n\n"
        f"`{PROTOCOL_ID}`\n\nSHA-256: `{specification_hash}`\n",
    )
    return {
        "protocol_id": PROTOCOL_ID,
        "specification_sha256": specification_hash,
        "profiles": 8,
        "balanced_paths": 12,
        "stress_paths": 16,
        "optuna_executed": False,
    }


def _primary_gate_details(
    aggregate: dict[str, Any], retention: float
) -> dict[str, Any]:
    activity = aggregate["activity"]
    economics = aggregate["economics"]
    details = {
        "integrity": aggregate["integrity"]["passed"],
        "balanced_safety": aggregate["safety"]["passed"],
        "normal_maker_fills": activity["balanced_maker_fills"] >= 50,
        "bid_fills": activity["balanced_bid_fills"] >= 15,
        "ask_fills": activity["balanced_ask_fills"] >= 15,
        "normal_fifo_round_trips": (
            activity["normal_fifo_round_trips"] >= 10
        ),
        "represented_scenarios": activity["represented_scenarios"] >= 4,
        "activity_retention": retention >= 0.80,
        "net_pnl": economics["net_pnl_usdt"] > 0,
        "expectancy": economics["round_trip_expectancy_usdt"] > 0,
        "profit_factor": economics["profit_factor"] > 1.0,
        "net_realized_spread": (
            economics["net_realized_spread_capture_usdt"] > 0
        ),
        "gross_execution_gt_fees": (
            economics["gross_execution_pnl_usdt"]
            > economics["total_fees_usdt"]
        ),
        "pnl_after_added_2bps": (
            economics["pnl_after_added_2bps_usdt"] > 0
        ),
        "pnl_after_top_10pct_removal": (
            economics["pnl_after_top_10pct_removal_usdt"] > 0
        ),
        "average_5_tick_markout": (
            economics["average_5_tick_markout_usdt"] >= -0.05
        ),
    }
    return {**details, "passed": all(details.values())}


def _write_balanced(
    directory: Path,
    runs: list[dict[str, Any]],
    aggregates: list[dict[str, Any]],
    deterministic_replay: bool,
) -> None:
    directory.mkdir(parents=True)
    categories = {
        "market_events.jsonl": "market_events",
        "trade_events.jsonl": "trade_events",
        "quote_events.jsonl": "quote_events",
        "margin_events.jsonl": "margin_events",
        "order_events.jsonl": "order_events",
        "markouts.jsonl": "markouts",
    }
    for filename, key in categories.items():
        _jsonl(directory / filename, [
            {
                "profile_id": run["profile_id"],
                "path_id": run["path_id"],
                **event,
            }
            for run in runs for event in run[key]
        ])
    _jsonl(directory / "quote_decisions.jsonl", [
        {
            "profile_id": run["profile_id"],
            "path_id": run["path_id"],
            **event,
        }
        for run in runs for event in run["decisions"]
    ])
    _jsonl(directory / "fills.jsonl", [
        {
            "fill_model": BALANCED_MODEL,
            "profile_id": run["profile_id"],
            "path_id": run["path_id"],
            **fill.to_dict(),
        }
        for run in runs for fill in run["fills"]
    ])
    _jsonl(directory / "round_trips.jsonl", [
        {
            "fill_model": BALANCED_MODEL,
            "profile_id": run["profile_id"],
            "path_id": run["path_id"],
            **trip.to_dict(),
        }
        for run in runs for trip in run["trips"]
    ])
    _jsonl(directory / "profile_path_results.jsonl", [
        {
            key: value for key, value in run.items()
            if key not in {
                "fills", "trips", "normal_trips", "markouts",
                "market_events", "trade_events", "quote_events",
                "order_events", "margin_events", "decisions",
            }
        }
        for run in runs
    ])
    summary = {
        "fill_model": BALANCED_MODEL,
        "deterministic_replay": deterministic_replay,
        "profile_path_count": len(runs),
        "profiles": aggregates,
        "semantic_integrity": (
            deterministic_replay
            and all(row["integrity"]["passed"] for row in aggregates)
        ),
    }
    _atomic(directory / "balanced_summary.json", summary)
    stream_manifest = {
        name: {
            "records": sum(1 for _ in (directory / name).open()),
            "sha256": file_hash(directory / name),
        }
        for name in BALANCED_STREAMS
    }
    _atomic(directory / "stream_manifest.json", stream_manifest)
    _atomic(directory / "run_manifest.json", {
        "protocol_id": PROTOCOL_ID,
        "fill_model": BALANCED_MODEL,
        "matrix_execution_count": 1,
        "profile_path_count": len(runs),
        "prior_matrix_reruns": False,
        "optimization": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    })
    _complete(directory)


def _run_balanced_matrix(
    spec: dict[str, Any], directory: Path
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    replay_ok = True
    for profile in spec["profiles"]:
        mine = [
            run_balanced_path(profile, path)
            for path in spec["balanced_paths"]
        ]
        replay = run_balanced_path(profile, spec["balanced_paths"][0])
        replay_ok = replay_ok and (
            digest(_compact_run(mine[0])) == digest(_compact_run(replay))
        )
        runs.extend(mine)
        aggregates.append(aggregate_profile(profile, mine))
    control_fills = max(
        aggregates[0]["activity"]["balanced_maker_fills"], 1
    )
    for aggregate in aggregates:
        retention = (
            aggregate["activity"]["balanced_maker_fills"] / control_fills
        )
        aggregate["activity"]["retention_vs_control"] = retention
        aggregate["primary_gate_details"] = _primary_gate_details(
            aggregate, retention
        )
        aggregate["primary_gates_passed"] = aggregate[
            "primary_gate_details"
        ]["passed"]
        aggregate["defensive_overlay_runtime_audit"] = {
            "shock_overlay_inactive_by_path_construction": spec[
                "balanced_trigger_audit"
            ]["defensive_shock_overlay_inactive_by_construction"],
            "drawdown_guard_not_reached": (
                aggregate["safety"]["worst_drawdown"] < 0.03
            ),
        }
    _write_balanced(directory, runs, aggregates, replay_ok)
    return aggregates


def _stress_analysis(
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
    control_id = spec["profiles"][0]["profile_id"]
    control_fills = max(
        activities[control_id]["strict_trade_through_maker_fills"], 1
    )
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        retention = (
            activities[profile_id]["strict_trade_through_maker_fills"]
            / control_fills
        )
        activities[profile_id]["normal_fill_retention_vs_control"] = retention
        activities[profile_id]["retention_passed"] = retention >= 0.70
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
    resilience: dict[str, Any] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        budgets = {
            severity: (
                analysis["severity_results"][profile_id][severity][
                    "hard_kills"
                ] == 0
                and analysis["severity_results"][profile_id][severity][
                    "worst_drawdown"
                ] <= spec["stress_budgets"][severity][
                    "worst_drawdown_max"
                ]
            )
            for severity in REQUIRED_SEVERITIES
        }
        mine = [row for row in metas if row["profile_id"] == profile_id]
        accounting = all(row["accounting_reconciles"] for row in mine)
        result = {
            "budget_by_severity": budgets,
            "all_required_budgets_passed": all(budgets.values()),
            "activity_passed": activities[profile_id][
                "passed_with_retention"
            ],
            "causal_reentry_passed": reentry[profile_id]["passed"],
            "accounting_reconciles": accounting,
        }
        result["passed"] = all((
            result["all_required_budgets_passed"],
            result["activity_passed"],
            result["causal_reentry_passed"],
            result["accounting_reconciles"],
        ))
        resilience[profile_id] = result
    analysis["activity_floor_results"] = activities
    analysis["reentry_results"] = reentry
    analysis["resilience_results"] = resilience
    return analysis


def _run_stress_matrix(
    spec: dict[str, Any], directory: Path
) -> dict[str, Any]:
    directory.mkdir(parents=True)
    handles = {
        name: (directory / name).open("x", encoding="utf-8", newline="\n")
        for name in STRESS_STREAMS
    }
    rows = {name: [] for name in STRESS_STREAMS}
    metas: list[dict[str, Any]] = []
    try:
        for profile in spec["profiles"]:
            for path in spec["stress_paths"]:
                path_ticks = stress_ticks(path)
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
                            ) + "\n"
                        )
                    handles[name].flush()
                    rows[name].extend(values)
                metas.extend(normalized["path_results.jsonl"])
    finally:
        for handle in handles.values():
            handle.close()

    profile_ids = {row["profile_id"] for row in spec["profiles"]}
    path_ids = {row["path_id"] for row in spec["stress_paths"]}
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
    analysis = _stress_analysis(spec, metas, rows)
    quote_modes = all(
        row["unclassified_quote_mode_ticks"] == 0
        and row["two_sided_ticks"]
        + row["one_sided_ticks"]
        + row["no_quote_ticks"] == 240
        for row in metas
    )
    semantic_integrity = all((
        classification["passed"],
        schema_invalid == 0,
        references["passed"],
        event_order["passed"],
        quote_modes,
        _finite({"metas": metas, "analysis": analysis}),
        len(metas) == 128,
    ))
    safety = {
        "preventable_margin_breaches": sum(
            row["preventable_margin_breaches"] for row in metas
        ),
        "margin_breaches": sum(row["margin_breaches"] for row in metas),
        "inventory_breaches": sum(row["inventory_breaches"] for row in metas),
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
    summary = {
        "fill_model": STRESS_MODEL,
        "profile_path_count": len(metas),
        "semantic_integrity": semantic_integrity,
        "safety": safety,
        "classification_audit": classification,
        "event_order_audit": event_order,
        "quote_modes_reconcile": quote_modes,
        "schema_invalid": schema_invalid,
        "cross_references": references,
        "analysis": analysis,
    }
    _atomic(directory / "stress_summary.json", summary)
    stream_manifest = {
        name: {
            "records": len(rows[name]),
            "sha256": file_hash(directory / name),
        }
        for name in STRESS_STREAMS
    }
    _atomic(directory / "stream_manifest.json", stream_manifest)
    _atomic(directory / "run_manifest.json", {
        "protocol_id": PROTOCOL_ID,
        "fill_model": STRESS_MODEL,
        "matrix_execution_count": 1,
        "profile_path_count": len(metas),
        "prior_matrix_reruns": False,
        "optimization": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    })
    _complete(directory)
    if not _verify_completed(directory):
        raise RuntimeError("v1.6 stress completion hash verification failed")
    return summary


def _rank_key(
    profile: dict[str, Any],
    balanced_by_id: dict[str, dict[str, Any]],
    stress: dict[str, Any],
) -> tuple[Any, ...]:
    profile_id = profile["profile_id"]
    balanced = balanced_by_id[profile_id]
    economics = balanced["economics"]
    stress_passed = stress["analysis"]["resilience_results"][profile_id][
        "passed"
    ]
    return (
        balanced["primary_gates_passed"] and stress_passed,
        balanced["primary_gates_passed"],
        economics["pnl_after_added_2bps_usdt"],
        economics["pnl_after_top_10pct_removal_usdt"],
        economics["net_pnl_usdt"],
        economics["profit_factor"],
        balanced["activity"]["retention_vs_control"],
        -balanced["safety"]["worst_drawdown"],
    )


def _decide(
    spec: dict[str, Any],
    balanced: list[dict[str, Any]],
    stress: dict[str, Any],
    analysis_dir: Path,
    decision_dir: Path,
    specification_hash: str,
) -> dict[str, Any]:
    balanced_by_id = {row["profile_id"]: row for row in balanced}
    primary_activity = [
        row for row in balanced
        if row["activity"]["passed"]
        and row["activity"]["retention_vs_control"] >= 0.80
    ]
    primary_economic = [
        row for row in primary_activity if row["primary_gates_passed"]
    ]
    economic_ids = {row["profile_id"] for row in primary_economic}
    stress_activity_ids = {
        profile_id
        for profile_id, row in stress["analysis"][
            "activity_floor_results"
        ].items()
        if row["passed_with_retention"]
    }
    reentry_ids = {
        profile_id
        for profile_id, row in stress["analysis"]["reentry_results"].items()
        if row["passed"]
    }
    stress_pass_ids = {
        profile_id
        for profile_id, row in stress["analysis"][
            "resilience_results"
        ].items()
        if row["passed"]
    }
    supported_ids = economic_ids & stress_pass_ids
    evidence = (
        all(row["integrity"]["passed"] for row in balanced)
        and stress["semantic_integrity"]
    )
    safety = (
        all(row["safety"]["passed"] for row in balanced)
        and stress["safety"]["passed"]
    )
    if not evidence:
        status, gate = STATUSES[0], "GATE_0_EVIDENCE"
    elif not safety:
        status, gate = STATUSES[1], "GATE_1_SAFETY"
    elif not primary_activity or not stress_activity_ids:
        status, gate = STATUSES[2], "GATE_2_ACTIVITY"
    elif not reentry_ids:
        status, gate = STATUSES[3], "GATE_3_CAUSAL_REENTRY"
    elif not primary_economic:
        status, gate = STATUSES[4], "GATE_4_PRIMARY_ECONOMICS"
    elif not supported_ids:
        status, gate = STATUSES[1], "GATE_5_STRICT_STRESS_BUDGET"
    else:
        status, gate = STATUSES[5], None

    best = max(
        spec["profiles"],
        key=lambda profile: _rank_key(profile, balanced_by_id, stress),
    )
    best_id = best["profile_id"]
    optuna_eligible = best_id in supported_ids and status == STATUSES[5]
    analysis_dir.mkdir(parents=True)
    _json(analysis_dir / "primary_profile_economics.json", {
        row["profile_id"]: row for row in balanced
    })
    for filename, key in (
        ("stress_activity_results.json", "activity_floor_results"),
        ("stress_reentry_results.json", "reentry_results"),
        ("stress_severity_results.json", "severity_results"),
        ("stress_resilience_results.json", "resilience_results"),
        ("stress_hard_kill_attribution.json", "hard_kill_attribution"),
    ):
        _json(analysis_dir / filename, stress["analysis"][key])
    _json(analysis_dir / "classification_audit.json", {
        **stress["classification_audit"],
        "semantic_integrity": stress["semantic_integrity"],
        "quote_modes_reconcile": stress["quote_modes_reconcile"],
        "schema_invalid": stress["schema_invalid"],
        "cross_references": stress["cross_references"],
    })
    _json(analysis_dir / "event_order_audit.json", stress[
        "event_order_audit"
    ])
    _json(analysis_dir / "combined_gate_results.json", {
        profile["profile_id"]: {
            "profile_name": profile["profile_name"],
            "primary_gates_passed": balanced_by_id[
                profile["profile_id"]
            ]["primary_gates_passed"],
            "stress_gates_passed": stress["analysis"][
                "resilience_results"
            ][profile["profile_id"]]["passed"],
            "supported": profile["profile_id"] in supported_ids,
        }
        for profile in spec["profiles"]
    })
    _exclusive(
        analysis_dir / "analysis_report.md",
        "# MM v1.6 Economic and Stress Analysis\n\n"
        f"Status: `{status}`\n\nBest profile: `{best['profile_name']}`\n",
    )
    _complete(analysis_dir)

    decision = {
        "status": status,
        "first_failed_gate": gate,
        "best_profile": best["profile_name"],
        "best_profile_id": best_id,
        "best_primary_economics": balanced_by_id[best_id]["economics"],
        "best_primary_activity": balanced_by_id[best_id]["activity"],
        "best_primary_gate_details": balanced_by_id[best_id][
            "primary_gate_details"
        ],
        "best_stress_activity": stress["analysis"][
            "activity_floor_results"
        ][best_id],
        "best_stress_reentry": stress["analysis"]["reentry_results"][
            best_id
        ],
        "best_stress_resilience": stress["analysis"][
            "resilience_results"
        ][best_id],
        "profiles_passing_primary_activity": len(primary_activity),
        "profiles_passing_primary_economics": len(primary_economic),
        "profiles_passing_stress": len(stress_pass_ids),
        "profiles_supported": len(supported_ids),
        "semantic_integrity": evidence,
        "safety_passed": safety,
        "specification_sha256": specification_hash,
        "balanced_matrix_execution_count": 1,
        "stress_matrix_execution_count": 1,
        "optuna_eligible": optuna_eligible,
        "optuna_executed": False,
        "prior_matrix_reruns": False,
        "validation": False,
        "holdout": False,
        "external": False,
        "git": False,
        "production_defaults_changed": False,
    }
    decision_dir.mkdir(parents=True)
    _atomic(decision_dir / "decision.json", decision)
    _exclusive(
        decision_dir / "decision.md",
        "# MM v1.6 Decision\n\n"
        f"`{status}`\n\nFirst failed gate: `{gate}`\n\n"
        f"Best profile: `{best['profile_name']}`\n\n"
        f"Optuna eligible: `{optuna_eligible}`; executed: `False`\n",
    )
    _complete(decision_dir)
    return decision


def execute(
    root: Path,
    specification_dir: Path,
    balanced_dir: Path,
    stress_dir: Path,
    analysis_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if any(path.exists() for path in (
        balanced_dir, stress_dir, analysis_dir, decision_dir
    )):
        raise FileExistsError("refusing v1.6 run-ID reuse")
    spec_path = specification_dir / "protocol_spec.json"
    expected_hash = (
        specification_dir / "protocol_spec.sha256"
    ).read_text().split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("v1.6 specification hash mismatch")
    spec = json.loads(spec_path.read_text())
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered v1.6 source changed after freeze")
    if closed_evidence_hashes(root) != spec["closed_evidence_hashes"]:
        raise RuntimeError("closed evidence changed after v1.6 freeze")
    if not path_disjointness()["passed"]:
        raise RuntimeError("v1.6 path disjointness failed")
    balanced = _run_balanced_matrix(spec, balanced_dir)
    if not _verify_completed(balanced_dir):
        raise RuntimeError("v1.6 balanced completion hash verification failed")
    stress = _run_stress_matrix(spec, stress_dir)
    return _decide(
        spec,
        balanced,
        stress,
        analysis_dir,
        decision_dir,
        expected_hash,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    declaration = commands.add_parser("declare")
    declaration.add_argument("--specification-dir", type=Path, required=True)
    run = commands.add_parser("run")
    for name in ("specification", "balanced", "stress", "analysis", "decision"):
        run.add_argument(f"--{name}-dir", type=Path, required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = (
        declare(root, arguments.specification_dir)
        if arguments.command == "declare"
        else execute(
            root,
            arguments.specification_dir,
            arguments.balanced_dir,
            arguments.stress_dir,
            arguments.analysis_dir,
            arguments.decision_dir,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
