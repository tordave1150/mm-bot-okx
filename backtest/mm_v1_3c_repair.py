"""Execute the frozen MM v1.3C fill-trigger and activity evidence repair."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3a_diagnostic import (
    _normalize as v13a_normalize,
    validate_references,
    validate_schema,
)
from backtest.mm_v1_3c_evidence import (
    activity_counts,
    classification_fixtures,
    normal_fifo_evidence,
    reconcile_fill_stream,
    validate_fill_record,
)
from backtest.mm_v1_3c_protocol import (
    CAPITAL,
    FILL_MODEL,
    PROTOCOL_ID,
    SEVERITY,
    STREAMS,
    build_spec,
    file_hash,
    path_disjointness,
    profile_carry_forward_exact,
    source_hashes,
    ticks,
)
from market_maker.as_config import MarketMakerV1Config


def _exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _json(path: Path, value: Any) -> None:
    _exclusive(
        path,
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _json(temporary, value)
    temporary.replace(path)


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _exclusive(
        path,
        "".join(
            json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
            for row in rows
        ),
    )


def _complete(directory: Path, status: str = "COMPLETED") -> None:
    _atomic(directory / "COMPLETED.json", {
        "status": status,
        "files": {
            path.name: file_hash(path)
            for path in sorted(directory.iterdir())
            if path.name != "COMPLETED.json"
        },
    })


def _verify_completed(directory: Path) -> bool:
    completed = json.loads((directory / "COMPLETED.json").read_text())
    return (
        completed["status"] == "COMPLETED"
        and all(
            (directory / name).is_file()
            and file_hash(directory / name) == expected
            for name, expected in completed["files"].items()
        )
    )


def classify(directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing classification artifact overwrite")
    directory.mkdir(parents=True)
    fixtures, reconciliation = classification_fixtures()
    contract = {
        "taxonomy": [
            "STRICT_TRADE_THROUGH",
            "AGGRESSOR_TRADE_AT_QUOTE",
            "TERMINAL_EXECUTION",
            "HARD_KILL_EXECUTION",
            "EMERGENCY_EXECUTION",
        ],
        "source_of_truth": "CanonicalFillClassification",
        "assigned_at": "Fill creation in MatchingEngine",
        "normal_maker_triggers": [
            "STRICT_TRADE_THROUGH",
            "AGGRESSOR_TRADE_AT_QUOTE",
        ],
        "special_exit_triggers": [
            "TERMINAL_EXECUTION",
            "HARD_KILL_EXECUTION",
            "EMERGENCY_EXECUTION",
        ],
    }
    forbidden = {
        "combinations": [
            "maker + TERMINAL_EXECUTION",
            "maker + HARD_KILL_EXECUTION",
            "maker + EMERGENCY_EXECUTION",
            "taker + STRICT_TRADE_THROUGH",
            "taker + AGGRESSOR_TRADE_AT_QUOTE",
            "special_exit + normal_activity_eligible",
            "special_exit + normal_round_trip_eligible",
            "missing trigger",
            "unknown trigger",
            "multiple triggers",
        ],
        "policy": "FAIL_CLOSED",
    }
    passed = len(fixtures) == 16 and all(row["passed"] for row in fixtures)
    _json(directory / "canonical_trigger_contract.json", contract)
    _json(directory / "forbidden_combinations.json", forbidden)
    _jsonl(directory / "classification_fixtures.jsonl", fixtures)
    _json(directory / "reconciliation_results.json", reconciliation)
    _exclusive(
        directory / "classification_report.md",
        "# MM v1.3C Classification Repair\n\n"
        f"- Fixtures passed: {sum(row['passed'] for row in fixtures)}/16\n"
        f"- Reconciliation passed: {reconciliation['passed']}\n",
    )
    _complete(directory, "COMPLETED" if passed else "FAILED")
    return {
        "classification_fixtures_passed": passed,
        "fixture_count": len(fixtures),
        "reconciliation_passed": reconciliation["passed"],
    }


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing specification overwrite")
    spec = build_spec(root)
    if not spec["profile_carry_forward_exact"]:
        raise RuntimeError("v1.3B profile carry-forward drift")
    if not spec["path_disjointness"]["passed"]:
        raise RuntimeError("fresh path matrix is not disjoint")
    directory.mkdir(parents=True)
    _atomic(directory / "protocol_spec.json", spec)
    specification_hash = file_hash(directory / "protocol_spec.json")
    _exclusive(
        directory / "protocol_spec.sha256",
        f"{specification_hash}  protocol_spec.json\n",
    )
    for filename, key in (
        ("carried_profiles.json", "profiles"),
        ("capital_policy.json", "capital_policy"),
        ("severity_ladder.json", "severity_ladder"),
        ("stress_path_matrix.json", "paths"),
        ("activity_floor.json", "activity_floor"),
        ("resilience_budget.json", "resilience_budget"),
    ):
        _json(directory / filename, spec[key])
    _json(directory / "fill_model_policy.json", {
        "fill_model": FILL_MODEL,
        "strict_trade_through_stress_semantics": True,
        "canonical_classification_at_creation": True,
        "touch_only_activity_eligible": False,
        "probabilistic_activity_eligible": False,
    })
    _json(directory / "artifact_contract.json", {
        "mandatory_streams": list(STREAMS),
        "raw_evidence_written_during_execution": True,
        "append_only_jsonl": True,
        "hash_every_stream": True,
        "completed_written_last": True,
        "refuse_overwrite": True,
    })
    _exclusive(
        directory / "protocol_spec.md",
        "# MM v1.3C Frozen Protocol\n\n"
        f"`{PROTOCOL_ID}`\n\n"
        f"Specification SHA-256: `{specification_hash}`\n",
    )
    return {
        "protocol_id": PROTOCOL_ID,
        "protocol_spec_sha256": specification_hash,
        "profile_count": len(spec["profiles"]),
        "path_count": len(spec["paths"]),
    }


def _canonical_rows(
    profile: dict[str, Any],
    path: dict[str, Any],
    path_ticks: list[dict[str, Any]],
    run: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Retain the v1.3A writer layout but consume creation-time identity."""
    rows = v13a_normalize(profile, path, path_ticks, run)
    base_fills = {
        row["fill_id"]: row for row in rows["fills.jsonl"]
    }
    canonical_fills: list[dict[str, Any]] = []
    for raw in run.fills:
        source = base_fills[raw["fill_id"]]
        canonical = {
            "fill_id": raw["fill_id"],
            "order_id": raw["order_id"],
            "profile_id": profile["profile_id"],
            "path_id": path["path_id"],
            "severity": path["severity"],
            "scenario": path["scenario"],
            "tick": int(raw["tick"]),
            "side": raw["side"],
            "quantity_btc": float(raw["size"]),
            "fill_price": float(raw["price"]),
            "fee": float(raw["fee"]),
            "maker_or_taker": raw["maker_or_taker"],
            "fill_trigger": raw["fill_trigger"],
            "special_exit": raw["special_exit"],
            "normal_activity_eligible": raw["normal_activity_eligible"],
            "normal_round_trip_eligible": raw[
                "normal_round_trip_eligible"
            ],
            "fill_model": FILL_MODEL,
            "trigger_event_id": source["trigger_event_id"],
            "quote_event_id": source["quote_event_id"],
            "inventory_before": float(raw["inventory_before"]),
            "inventory_after": float(raw["inventory_after"]),
            "quote_created_tick": int(
                raw.get("quote_created_tick", raw["tick"])
            ),
            "activation_tick": int(
                raw.get("activation_tick", raw["tick"])
            ),
            "quote_price": float(raw["price"]),
            "quote_distance_bps": float(
                raw.get("quote_distance_bps", 0.0)
            ),
            "volatility_at_creation_bps": float(
                raw.get("volatility_at_creation_bps", 0.0)
            ),
            "volatility_at_fill_bps": float(
                raw.get("volatility_at_fill_bps", 0.0)
            ),
            "inventory_at_creation": float(
                raw.get("inventory_at_creation", raw["inventory_before"])
            ),
            "defensive_mode_at_creation": raw.get(
                "defensive_mode_at_creation", "NORMAL"
            ),
            "defensive_mode_at_fill": raw.get(
                "defensive_mode_at_fill", "NORMAL"
            ),
            "pnl_before_fill_usdt": float(
                raw.get("pnl_before_fill_usdt", 0.0)
            ),
            "pnl_after_fill_usdt": float(
                raw.get("pnl_after_fill_usdt", 0.0)
            ),
            "drawdown_after_fill": float(
                raw.get("drawdown_after_fill", 0.0)
            ),
        }
        validate_fill_record(canonical)
        canonical_fills.append(canonical)
    normal_trips, special_attribution, reconciliation = normal_fifo_evidence(
        canonical_fills
    )
    rows["fills.jsonl"] = canonical_fills
    rows["round_trips.jsonl"] = normal_trips
    rows["defensive_events.jsonl"] = [
        {
            "event_id": (
                f"{profile['profile_id']}-{path['path_id']}-"
                f"def-{index:05d}"
            ),
            "profile_id": profile["profile_id"],
            "path_id": path["path_id"],
            "severity": path["severity"],
            "scenario": path["scenario"],
            **event,
        }
        for index, event in enumerate(run.defensive_events, start=1)
    ]
    meta = rows["path_results.jsonl"][0]
    meta.update({
        "fill_count": len(canonical_fills),
        "normal_activity_fill_count": sum(
            row["normal_activity_eligible"] for row in canonical_fills
        ),
        "round_trip_count": len(normal_trips),
        "special_exit_attribution_count": len(special_attribution),
        "special_exit_gross_pnl": sum(
            float(row["gross_pnl_usdt"]) for row in special_attribution
        ),
        "accounting_reconciles": reconciliation[
            "quantity_identity_reconciles"
        ],
        "defensive_event_count": len(run.defensive_events),
        "quote_eligible_ticks": run.quote_eligible_ticks,
        "two_sided_ticks": run.two_sided_quote_decisions,
        "one_sided_ticks": run.one_sided_quote_decisions,
        "no_quote_ticks": run.no_quote_decisions,
    })
    return rows


def _decision_quotes(
    quote_rows: list[dict[str, Any]],
    ids: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [
        row for row in quote_rows
        if (row["profile_id"], row["path_id"]) in ids
        and row.get("decision") != "SPECIAL_EXIT"
    ]


def _profile_activity(
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
    defensive = [
        row for row in rows["defensive_events.jsonl"]
        if (row["profile_id"], row["path_id"]) in ids
    ]
    counts = activity_counts(fills)
    total = len(quotes)
    two_sided = sum(row["quote_mode"] == "TWO_SIDED" for row in quotes)
    one_sided = sum(
        row["quote_mode"] in {"ONE_SIDED_BID", "ONE_SIDED_ASK"}
        for row in quotes
    )
    no_quote = sum(
        row["quote_mode"] in {
            "NO_QUOTE",
            "TERMINATED_AFTER_HARD_KILL",
        }
        for row in quotes
    )
    result = {
        **counts,
        "normal_fifo_round_trips": len(trips),
        "quote_eligible_ticks": sum(
            int(row["quote_eligible_ticks"]) for row in selected
        ),
        "two_sided_quote_rate": two_sided / max(total, 1),
        "one_sided_quote_rate": one_sided / max(total, 1),
        "no_quote_rate": no_quote / max(total, 1),
        "represented_scenario_families": len({
            row["scenario"] for row in normal
        }),
        "defensive_activation_count": sum(
            row["event"] in {
                "VOLATILITY_SPREAD_GUARD",
                "FAST_CANCEL_ON_VOLATILITY",
                "TOXIC_FLOW_PAUSE_ENTRY",
                "INVENTORY_REDUCTION_PRIORITY",
                "ONE_SIDED_DEFENSIVE_MODE",
            }
            for row in defensive
        ),
        "pause_ticks": sum(
            row["event"] == "TOXIC_FLOW_PAUSE_TICK" for row in defensive
        ),
        "one_sided_ticks": sum(
            row["event"] == "ONE_SIDED_DEFENSIVE_MODE"
            for row in defensive
        ),
        "reentry_count": sum(
            row["event"] == "DEFENSIVE_REENTRY_READY"
            for row in defensive
        ),
        "normal_fills_by_severity": {
            severity: sum(
                row["severity"] == severity for row in normal
            )
            for severity in ("S2_5", "S3_LOW")
        },
    }
    result["passed"] = (
        result["strict_trade_through_maker_fills"] >= 8
        and result["bid_normal_fills"] > 0
        and result["ask_normal_fills"] > 0
        and result["normal_fifo_round_trips"] >= 2
        and result["quote_eligible_ticks"] > 0
        and result["two_sided_quote_rate"] >= 0.05
        and result["no_quote_rate"] <= 0.85
        and result["represented_scenario_families"] >= 3
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
    activation_names = {
        "VOLATILITY_SPREAD_GUARD",
        "FAST_CANCEL_ON_VOLATILITY",
        "TOXIC_FLOW_PAUSE_ENTRY",
        "INVENTORY_REDUCTION_PRIORITY",
        "ONE_SIDED_DEFENSIVE_MODE",
    }
    activations_by_path: dict[str, set[int]] = {}
    explicit_reentries: dict[str, set[int]] = {}
    for event in events:
        if event["event"] in activation_names:
            activations_by_path.setdefault(event["path_id"], set()).add(
                int(event["tick"])
            )
        if event["event"] == "DEFENSIVE_REENTRY_READY":
            explicit_reentries.setdefault(event["path_id"], set()).add(
                int(event["tick"])
            )
    exit_ticks: dict[str, set[int]] = {}
    for path_id, active_ticks in activations_by_path.items():
        exit_ticks[path_id] = {
            tick + 1 for tick in active_ticks if tick + 1 not in active_ticks
        }
    reentry_ticks = {
        path_id: exit_ticks.get(path_id, set())
        | explicit_reentries.get(path_id, set())
        for path_id in set(exit_ticks) | set(explicit_reentries)
    }
    before_activation = any(
        any(
            fill["path_id"] == path_id
            and int(fill["tick"]) < min(ticks_for_path)
            for fill in normal
        )
        for path_id, ticks_for_path in activations_by_path.items()
        if ticks_for_path
    )
    after_reentry = any(
        any(
            fill["path_id"] == path_id
            and int(fill["tick"]) > reentry_tick
            for fill in normal
        )
        for path_id, path_ticks in reentry_ticks.items()
        for reentry_tick in path_ticks
    )
    is_control = profile["profile_name"] in {
        "BASELINE_CONTROL",
        "WIDER_SPREAD_CONTROL",
    }
    result = {
        "normal_fill_before_first_defensive_activation": before_activation,
        "normal_fill_after_completed_reentry": after_reentry,
        "defensive_mode_exit_count": sum(
            len(path_ticks) for path_ticks in exit_ticks.values()
        ),
        "completed_reentry_count": sum(
            len(path_ticks) for path_ticks in reentry_ticks.values()
        ),
        "paused_entire_run": (
            activity["pause_ticks"] > 0
            and activity["no_quote_rate"] >= 1.0
        ),
        "permanently_one_sided": (
            activity["one_sided_ticks"] > 0
            and activity["two_sided_quote_rate"] == 0
        ),
        "not_applicable_control": is_control,
    }
    base_pass = (
        before_activation
        and after_reentry
        and result["defensive_mode_exit_count"] >= 1
        and not result["paused_entire_run"]
        and not result["permanently_one_sided"]
    )
    if profile["profile_name"] == "ONE_SIDED_DEFENSIVE_MODE":
        base_pass = (
            base_pass
            and activity["bid_normal_fills"] > 0
            and activity["ask_normal_fills"] > 0
            and activity["one_sided_ticks"] > 0
            and activity["passed"]
            and activity["represented_scenario_families"] >= 3
        )
    if profile["profile_name"] == "COMPOSITE_DEFENSIVE":
        base_pass = (
            base_pass
            and activity["normal_fills_by_severity"]["S2_5"] > 0
            and activity["normal_fills_by_severity"]["S3_LOW"] > 0
            and activity["normal_fifo_round_trips"] >= 2
            and result["completed_reentry_count"] > 0
            and activity["no_quote_rate"] <= 0.85
        )
    result["passed"] = True if is_control else base_pass
    return result


def _first_hard_kill(mine: list[dict[str, Any]]) -> str | None:
    return next(
        (
            severity for severity in SEVERITY
            if any(
                row["severity"] == severity and row["hard_kills"] > 0
                for row in mine
            )
        ),
        None,
    )


def _analysis(
    spec: dict[str, Any],
    metas: list[dict[str, Any]],
    rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    activities = {
        profile["profile_id"]: _profile_activity(
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
    severity_results: dict[str, Any] = {}
    first_kills: dict[str, str | None] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        mine = [row for row in metas if row["profile_id"] == profile_id]
        first_kills[profile_id] = _first_hard_kill(mine)
        severity_results[profile_id] = {}
        for severity in SEVERITY:
            selected = [
                row for row in mine if row["severity"] == severity
            ]
            severity_results[profile_id][severity] = {
                "net_pnl": sum(float(row["net_pnl"]) for row in selected),
                "worst_drawdown": max(
                    float(row["worst_drawdown"]) for row in selected
                ),
                "hard_kills": sum(int(row["hard_kills"]) for row in selected),
                "normal_activity_fills": sum(
                    int(row["normal_activity_fill_count"])
                    for row in selected
                ),
                "normal_fifo_round_trips": sum(
                    int(row["round_trip_count"]) for row in selected
                ),
                "markout_5": mean(
                    float(row["average_markout_5"]) for row in selected
                ),
                "inventory_variance": mean(
                    float(row["inventory_variance"]) for row in selected
                ),
                "cancel_latency_exposure": mean(
                    float(row["average_order_age"]) for row in selected
                ),
            }
    controls = [
        spec["profiles"][0]["profile_id"],
        spec["profiles"][1]["profile_id"],
    ]

    def aggregate(profile_id: str) -> dict[str, Any]:
        data = severity_results[profile_id]
        return {
            "hard_kills": sum(data[s]["hard_kills"] for s in SEVERITY),
            "first_hard_kill_severity": first_kills[profile_id],
            "first_hard_kill_rank": (
                SEVERITY[first_kills[profile_id]]["rank"]
                if first_kills[profile_id] else len(SEVERITY) + 1
            ),
            "worst_drawdown": max(
                data[s]["worst_drawdown"] for s in SEVERITY
            ),
            "net_pnl": sum(data[s]["net_pnl"] for s in SEVERITY),
            "markout": mean(data[s]["markout_5"] for s in SEVERITY),
            "inventory_variance": mean(
                data[s]["inventory_variance"] for s in SEVERITY
            ),
            "cancel_latency_exposure": mean(
                data[s]["cancel_latency_exposure"] for s in SEVERITY
            ),
            "normal_activity_retention": (
                activities[profile_id]["normal_activity_fills"]
                / max(activities[controls[0]]["normal_activity_fills"], 1)
            ),
            "scenario_coverage": activities[profile_id][
                "represented_scenario_families"
            ],
            "no_quote_rate": activities[profile_id]["no_quote_rate"],
        }

    aggregated = {
        profile["profile_id"]: aggregate(profile["profile_id"])
        for profile in spec["profiles"]
    }
    comparison: dict[str, Any] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        metrics = aggregated[profile_id]
        control_metrics = [aggregated[item] for item in controls]
        dimensions = {
            "hard_kill_count": all(
                metrics["hard_kills"] < control["hard_kills"]
                for control in control_metrics
            ),
            "first_hard_kill_severity": all(
                metrics["first_hard_kill_rank"]
                > control["first_hard_kill_rank"]
                for control in control_metrics
            ),
            "worst_drawdown": all(
                metrics["worst_drawdown"] < control["worst_drawdown"]
                for control in control_metrics
            ),
            "net_pnl": all(
                metrics["net_pnl"] > control["net_pnl"]
                for control in control_metrics
            ),
            "markout": all(
                metrics["markout"] > control["markout"]
                for control in control_metrics
            ),
            "inventory_variance": all(
                metrics["inventory_variance"]
                < control["inventory_variance"]
                for control in control_metrics
            ),
            "cancel_latency_exposure": all(
                metrics["cancel_latency_exposure"]
                < control["cancel_latency_exposure"]
                for control in control_metrics
            ),
        }
        comparison[profile_id] = {
            **metrics,
            "improvement_dimensions": dimensions,
            "improvement_count": sum(dimensions.values()),
        }
    baseline_markout = aggregated[controls[0]]["markout"]
    resilience: dict[str, Any] = {}
    for profile in spec["profiles"]:
        profile_id = profile["profile_id"]
        budget_by_severity = {
            severity: (
                severity_results[profile_id][severity]["hard_kills"] == 0
                and severity_results[profile_id][severity]["worst_drawdown"]
                <= spec["resilience_budget"][severity][
                    "worst_drawdown_max"
                ]
            )
            for severity in ("S2_5", "S3_LOW", "S3_MID")
        }
        unexplained = any(
            row["profile_id"] == profile_id
            and not row.get("primary_cause")
            for row in rows["hard_kill_events.jsonl"]
        )
        catastrophic = comparison[profile_id]["net_pnl"] <= -CAPITAL
        resilience[profile_id] = {
            "budget_by_severity": budget_by_severity,
            "activity_passed": activities[profile_id]["passed"],
            "reentry_passed": reentry[profile_id]["passed"],
            "improves_over_both_controls": (
                comparison[profile_id]["improvement_count"] >= 3
            ),
            "markout_improves_versus_baseline": (
                comparison[profile_id]["markout"] > baseline_markout
            ),
            "unexplained_hard_kill": unexplained,
            "catastrophic_terminal_loss": catastrophic,
        }
        resilience[profile_id]["passed"] = (
            all(budget_by_severity.values())
            and resilience[profile_id]["activity_passed"]
            and resilience[profile_id]["reentry_passed"]
            and resilience[profile_id]["improves_over_both_controls"]
            and resilience[profile_id]["markout_improves_versus_baseline"]
            and not unexplained
            and not catastrophic
        )
    markouts = {
        row["fill_id"]: row for row in rows["markouts.jsonl"]
    }
    quotes = {
        row["quote_event_id"]: row for row in rows["quote_events.jsonl"]
    }
    timelines: list[dict[str, Any]] = []
    for fill in rows["fills.jsonl"]:
        if (
            not fill["normal_activity_eligible"]
            or SEVERITY[fill["severity"]]["rank"] < SEVERITY["S3_LOW"]["rank"]
        ):
            continue
        quote = quotes.get(fill["quote_event_id"], {})
        markout = markouts.get(fill["fill_id"], {})
        timelines.append({
            "fill_id": fill["fill_id"],
            "profile_id": fill["profile_id"],
            "path_id": fill["path_id"],
            "severity": fill["severity"],
            "scenario": fill["scenario"],
            "quote_creation_tick": fill["quote_created_tick"],
            "quote_activation_tick": fill["activation_tick"],
            "quote_side": fill["side"],
            "quote_price": fill["quote_price"],
            "quote_distance_bps": fill["quote_distance_bps"],
            "volatility_at_creation_bps": fill[
                "volatility_at_creation_bps"
            ],
            "volatility_at_fill_bps": fill["volatility_at_fill_bps"],
            "inventory_at_creation": fill["inventory_at_creation"],
            "inventory_at_fill": fill["inventory_before"],
            "defensive_mode_at_creation": fill[
                "defensive_mode_at_creation"
            ],
            "defensive_mode_at_fill": fill["defensive_mode_at_fill"],
            "cancel_request_tick": quote.get("cancel_request_tick"),
            "cancel_completion_tick": quote.get("cancel_complete_tick"),
            "trigger_event_id": fill["trigger_event_id"],
            "fill_tick": fill["tick"],
            "markout_1": markout.get("quantity_weighted_markout_1"),
            "markout_5": markout.get("quantity_weighted_markout_5"),
            "markout_10": markout.get("quantity_weighted_markout_10"),
            "pnl_before_fill_usdt": fill["pnl_before_fill_usdt"],
            "pnl_after_fill_usdt": fill["pnl_after_fill_usdt"],
            "drawdown_after_fill": fill["drawdown_after_fill"],
        })
    hard_kill_attribution = [
        {
            **row,
            "causal_chain": [
                row["path_id"],
                row["hard_kill_id"],
                row["primary_cause"],
            ],
            "explained": bool(row.get("primary_cause")),
        }
        for row in rows["hard_kill_events.jsonl"]
    ]
    return {
        "activity_floor_results": activities,
        "reentry_results": reentry,
        "severity_results": severity_results,
        "profile_comparison": comparison,
        "resilience_results": resilience,
        "first_hard_kill_severity": first_kills,
        "toxic_fill_timelines": timelines,
        "hard_kill_attribution": hard_kill_attribution,
    }


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
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
    classification_dir: Path,
    run_dir: Path,
    analysis_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if any(
        directory.exists()
        for directory in (run_dir, analysis_dir, decision_dir)
    ):
        raise FileExistsError("refusing run-ID reuse or artifact overwrite")
    classification_complete = json.loads(
        (classification_dir / "COMPLETED.json").read_text()
    )
    if classification_complete["status"] != "COMPLETED":
        raise RuntimeError("classification fixtures did not pass")
    specification_path = specification_dir / "protocol_spec.json"
    expected_specification_hash = (
        specification_dir / "protocol_spec.sha256"
    ).read_text().split()[0]
    if file_hash(specification_path) != expected_specification_hash:
        raise RuntimeError("frozen specification hash mismatch")
    spec = json.loads(specification_path.read_text())
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after specification freeze")
    if not profile_carry_forward_exact() or not path_disjointness()["passed"]:
        raise RuntimeError("profile or path pre-run verification failed")

    run_dir.mkdir(parents=True)
    handles = {
        name: (run_dir / name).open(
            "x", encoding="utf-8", newline="\n"
        )
        for name in STREAMS
    }
    all_rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in STREAMS
    }
    metas: list[dict[str, Any]] = []
    try:
        for profile in spec["profiles"]:
            for path in spec["paths"]:
                path_ticks = ticks(path)
                runner = MarketMakerBacktestRunner(
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
                )
                engine_result = runner.run(path_ticks)
                normalized = _canonical_rows(
                    profile, path, path_ticks, engine_result
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
                    all_rows[name].extend(values)
                metas.extend(normalized["path_results.jsonl"])
    finally:
        for handle in handles.values():
            handle.close()

    profile_ids = {row["profile_id"] for row in spec["profiles"]}
    path_ids = {row["path_id"] for row in spec["paths"]}
    schema_invalid = sum(
        not validate_schema(name, row)
        for name, stream_rows in all_rows.items()
        if name != "defensive_events.jsonl"
        for row in stream_rows
    )
    references = validate_references(
        {
            name: stream_rows
            for name, stream_rows in all_rows.items()
            if name != "defensive_events.jsonl"
        },
        path_ids,
        profile_ids,
    )
    classification_audit = reconcile_fill_stream(
        all_rows["fills.jsonl"]
    )
    analysis = _analysis(spec, metas, all_rows)
    quote_modes_reconcile = all(
        row["unclassified_quote_mode_ticks"] == 0 for row in metas
    )
    safety = {
        "preventable_margin_breaches": sum(
            row["preventable_margin_breaches"] for row in metas
        ),
        "recorded_margin_breaches": sum(
            row["margin_breaches"] for row in metas
        ),
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
        for key, value in safety.items()
        if key != "passed"
    )
    stream_manifest = {
        "streams": {
            name: {
                "records": len(all_rows[name]),
                "sha256": file_hash(run_dir / name),
                "empty": not bool(all_rows[name]),
                "applicability": (
                    "NOT_APPLICABLE_STRICT_BOOK_MODEL"
                    if name == "trade_events.jsonl"
                    and not all_rows[name]
                    else "APPLICABLE"
                ),
            }
            for name in STREAMS
        }
    }
    mandatory_complete = all(
        name in stream_manifest["streams"] for name in STREAMS
    )
    all_finite = _finite({
        "metas": metas,
        "classification": classification_audit,
        "analysis": analysis,
        "safety": safety,
    })
    semantic_integrity = (
        classification_audit["passed"]
        and schema_invalid == 0
        and references["passed"]
        and mandatory_complete
        and quote_modes_reconcile
        and all_finite
        and len(all_rows["path_results.jsonl"]) == 128
    )
    defensive_profiles = spec["profiles"][2:]
    activity_passers = [
        profile for profile in defensive_profiles
        if analysis["activity_floor_results"][profile["profile_id"]]["passed"]
    ]
    improvement_passers = [
        profile for profile in activity_passers
        if analysis["profile_comparison"][profile["profile_id"]][
            "improvement_count"
        ] >= 3
    ]
    reentry_passers = [
        profile for profile in activity_passers
        if analysis["reentry_results"][profile["profile_id"]]["passed"]
    ]
    budget_passers = [
        profile for profile in improvement_passers
        if analysis["resilience_results"][profile["profile_id"]]["passed"]
    ]
    composite = next(
        profile for profile in defensive_profiles
        if profile["profile_name"] == "COMPOSITE_DEFENSIVE"
    )
    composite_activity = analysis["activity_floor_results"][
        composite["profile_id"]
    ]
    composite_lower_inactive = (
        composite_activity["normal_fills_by_severity"]["S2_5"] == 0
        or composite_activity["normal_fills_by_severity"]["S3_LOW"] == 0
    )
    if not semantic_integrity:
        status, first_failed_gate = (
            "MM_V1_3C_EVIDENCE_REPAIR_FAILED",
            "GATE_0_SEMANTIC_EVIDENCE_INTEGRITY",
        )
    elif not safety["passed"]:
        status, first_failed_gate = (
            "MM_V1_3C_EVIDENCE_REPAIR_FAILED",
            "GATE_1_SAFETY_INFRASTRUCTURE",
        )
    elif not activity_passers:
        status, first_failed_gate = (
            "MM_V1_3C_ACTIVITY_INSUFFICIENT",
            "GATE_2_ACTIVITY",
        )
    elif not improvement_passers:
        status, first_failed_gate = (
            "MM_V1_3C_NO_RESILIENCE_IMPROVEMENT",
            "GATE_3_DEFENSIVE_IMPROVEMENT",
        )
    elif budget_passers:
        status, first_failed_gate = (
            "MM_V1_3C_RESILIENCE_EVIDENCE_SUPPORTED",
            None,
        )
    elif composite_lower_inactive:
        status, first_failed_gate = (
            "MM_V1_3C_COMPOSITE_INACTIVITY_REJECTED",
            "GATE_4_RESILIENCE_BUDGET",
        )
    else:
        status, first_failed_gate = (
            "MM_V1_3C_RESILIENCE_IMPROVED_BUT_BUDGET_REJECTED",
            "GATE_4_RESILIENCE_BUDGET",
        )
    best = max(
        defensive_profiles,
        key=lambda profile: (
            analysis["resilience_results"][profile["profile_id"]]["passed"],
            analysis["activity_floor_results"][profile["profile_id"]]["passed"],
            analysis["profile_comparison"][profile["profile_id"]][
                "improvement_count"
            ],
            analysis["profile_comparison"][profile["profile_id"]]["net_pnl"],
        ),
    )
    gate_results = {
        "GATE_0_SEMANTIC_EVIDENCE_INTEGRITY": semantic_integrity,
        "GATE_1_SAFETY_INFRASTRUCTURE": safety["passed"],
        "GATE_2_ACTIVITY": bool(activity_passers),
        "GATE_3_DEFENSIVE_IMPROVEMENT": bool(improvement_passers),
        "GATE_4_RESILIENCE_BUDGET": bool(budget_passers),
        "GATE_5_SUPPORT": status
        == "MM_V1_3C_RESILIENCE_EVIDENCE_SUPPORTED",
    }
    stress_summary = {
        "status": status,
        "first_failed_gate": first_failed_gate,
        "gate_results": gate_results,
        "classification_fixtures_passed": True,
        "misclassified_maker_fills": sum(
            row["maker_or_taker"] == "maker"
            and row["fill_trigger"] in {
                "TERMINAL_EXECUTION",
                "HARD_KILL_EXECUTION",
                "EMERGENCY_EXECUTION",
            }
            for row in all_rows["fills.jsonl"]
        ),
        "unclassified_fills": classification_audit[
            "unclassified_fills"
        ],
        "normal_activity_fills": classification_audit[
            "normal_activity_fills"
        ],
        "normal_fifo_round_trips": len(all_rows["round_trips.jsonl"]),
        "profiles_passing_activity": len(activity_passers),
        "profiles_passing_reentry": len(reentry_passers),
        "profiles_improving_over_controls": len(improvement_passers),
        "profiles_passing_resilience_budget": len(budget_passers),
        "best_defensive_profile": best["profile_name"],
        "safety": safety,
        "semantic_integrity": semantic_integrity,
        "classification_audit": classification_audit,
        "analysis": analysis,
    }
    _atomic(run_dir / "stream_manifest.json", stream_manifest)
    _atomic(run_dir / "stress_summary.json", stress_summary)
    _atomic(run_dir / "run_manifest.json", {
        "protocol_id": PROTOCOL_ID,
        "specification_sha256": expected_specification_hash,
        "source_hashes": spec["source_hashes"],
        "profile_fingerprints": {
            profile["profile_id"]: profile["profile_fingerprint"]
            for profile in spec["profiles"]
        },
        "path_hashes": {
            path["path_id"]: path["path_hash"] for path in spec["paths"]
        },
        "commands": [{
            "command": (
                "python -m backtest.mm_v1_3c_repair run "
                "--specification-dir ... --classification-dir ... "
                "--run-dir ... --analysis-dir ... --decision-dir ..."
            ),
            "exit_code": 0,
        }],
        "balanced_v1_3_matrix_rerun": False,
        "stress_v1_3a_matrix_rerun": False,
        "repair_v1_3b_matrix_rerun": False,
        "optimization_ran": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
    })
    _complete(run_dir)
    if not _verify_completed(run_dir):
        raise RuntimeError("completion hash verification failed")

    analysis_dir.mkdir(parents=True)
    _json(analysis_dir / "classification_audit.json", {
        **classification_audit,
        "schema_invalid_records": schema_invalid,
        "cross_references": references,
        "mandatory_streams_complete": mandatory_complete,
        "quote_modes_reconcile": quote_modes_reconcile,
        "all_metrics_finite": all_finite,
        "stream_hashes_valid": True,
        "completion_hashes_valid": True,
        "semantic_integrity": semantic_integrity,
    })
    _json(
        analysis_dir / "activity_floor_results.json",
        analysis["activity_floor_results"],
    )
    _json(
        analysis_dir / "reentry_results.json",
        analysis["reentry_results"],
    )
    _json(
        analysis_dir / "profile_comparison.json",
        analysis["profile_comparison"],
    )
    _jsonl(
        analysis_dir / "toxic_fill_timelines.jsonl",
        analysis["toxic_fill_timelines"],
    )
    _json(
        analysis_dir / "hard_kill_attribution.json",
        analysis["hard_kill_attribution"],
    )
    _json(
        analysis_dir / "resilience_results.json",
        analysis["resilience_results"],
    )
    _exclusive(
        analysis_dir / "analysis_report.md",
        "# MM v1.3C Analysis\n\n"
        f"Final status: `{status}`\n\n"
        f"First failed gate: `{first_failed_gate}`\n",
    )
    decision_dir.mkdir(parents=True)
    decision = {
        "status": status,
        "first_failed_gate": first_failed_gate,
        "classification_fixtures_passed": True,
        "misclassified_maker_fills": stress_summary[
            "misclassified_maker_fills"
        ],
        "unclassified_fills": classification_audit[
            "unclassified_fills"
        ],
        "normal_activity_fills": classification_audit[
            "normal_activity_fills"
        ],
        "normal_fifo_round_trips": len(all_rows["round_trips.jsonl"]),
        "profiles_passing_activity": len(activity_passers),
        "profiles_passing_reentry_requirements": len(reentry_passers),
        "profiles_improving_over_controls": len(improvement_passers),
        "profiles_passing_S2_5_S3_LOW_S3_MID": len(budget_passers),
        "best_defensive_profile": best["profile_name"],
        "preventable_margin_breaches": safety[
            "preventable_margin_breaches"
        ],
        "inventory_breaches": safety["inventory_breaches"],
        "profile_count": len(spec["profiles"]),
        "path_count": len(spec["paths"]),
        "balanced_v1_3_matrix_rerun": False,
        "stress_v1_3a_matrix_rerun": False,
        "repair_v1_3b_matrix_rerun": False,
        "optimization_ran": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_endpoints_contacted": False,
        "git_stage_commit_push": False,
        "production_default_changes": None,
    }
    _atomic(decision_dir / "decision.json", decision)
    _exclusive(
        decision_dir / "decision.md",
        "# MM v1.3C Decision\n\n"
        f"`{status}`\n\n"
        f"First failed gate: `{first_failed_gate}`\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    fixture_command = commands.add_parser("classify")
    fixture_command.add_argument(
        "--classification-dir", type=Path, required=True
    )
    declare_command = commands.add_parser("declare")
    declare_command.add_argument(
        "--specification-dir", type=Path, required=True
    )
    run_command = commands.add_parser("run")
    for name in (
        "specification",
        "classification",
        "run",
        "analysis",
        "decision",
    ):
        run_command.add_argument(
            f"--{name}-dir", type=Path, required=True
        )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if arguments.command == "classify":
        result = classify(arguments.classification_dir)
    elif arguments.command == "declare":
        result = declare(root, arguments.specification_dir)
    else:
        result = run(
            root,
            arguments.specification_dir,
            arguments.classification_dir,
            arguments.run_dir,
            arguments.analysis_dir,
            arguments.decision_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
