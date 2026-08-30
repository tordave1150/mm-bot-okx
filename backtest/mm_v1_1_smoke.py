"""Declare and execute the fixed Market Maker v1.1 economic smoke once."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_1_protocol import (
    ADDED_COST_BPS_PER_ROUND_TRIP,
    LEVERAGE,
    PRIMARY_CAPITAL_USDT,
    SMOKE_ID,
    STRESS_CAPITALS_USDT,
    build_specification,
    canonical_bytes,
    file_hash,
    payload_hash,
    scenario_ticks,
    source_hashes,
)
from market_maker.as_config import MarketMakerV1Config
from market_maker.diagnostics import top_profit_removal


SUPPRESSION_REASONS = (
    "SUPPRESS_INSUFFICIENT_MARGIN",
    "SUPPRESS_PENDING_ORDER_RESERVE",
    "SUPPRESS_POSITION_MARGIN",
    "SUPPRESS_INVENTORY_LIMIT",
    "SUPPRESS_EQUITY_NONPOSITIVE",
    "SUPPRESS_MARGIN_STATE_UNKNOWN",
)


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _write_json(path: Path, payload: Any) -> None:
    _write_exclusive(
        path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    _write_exclusive(
        path,
        "".join(
            json.dumps(record, sort_keys=True, allow_nan=False) + "\n"
            for record in records
        ),
    )


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write_json(temporary, payload)
    temporary.replace(path)


def _finite(payload: Any) -> bool:
    if isinstance(payload, bool) or payload is None or isinstance(payload, str):
        return True
    if isinstance(payload, (int, float)):
        return math.isfinite(float(payload))
    if isinstance(payload, dict):
        return all(_finite(value) for value in payload.values())
    if isinstance(payload, (list, tuple)):
        return all(_finite(value) for value in payload)
    return False


def _margin_components_reconcile(events: list[dict[str, Any]]) -> bool:
    for event in events:
        components = event.get("components")
        if not components:
            continue
        expected_pending = sum(
            float(components[key])
            for key in (
                "active_bid_order_reserve_usdt",
                "active_ask_order_reserve_usdt",
                "proposed_bid_order_reserve_usdt",
                "proposed_ask_order_reserve_usdt",
            )
        )
        expected_total = expected_pending + sum(
            float(components[key])
            for key in (
                "position_margin_usdt",
                "fee_reserve_usdt",
                "liquidation_reserve_usdt",
                "emergency_exit_reserve_usdt",
                "other_buffer_usdt",
            )
        )
        if not math.isclose(
            expected_pending,
            float(components["pending_order_reserve_usdt"]),
            abs_tol=1e-9,
        ):
            return False
        if not math.isclose(
            expected_total,
            float(components["total_modeled_margin_usdt"]),
            abs_tol=1e-9,
        ):
            return False
    return True


def _profit_factor(values: list[float]) -> float:
    profit = sum(max(0.0, value) for value in values)
    loss = sum(max(0.0, -value) for value in values)
    return profit / loss if loss > 0 else 10.0 if profit > 0 else 0.0


def _concentration(values: list[float], fraction: float) -> dict[str, float]:
    if not values:
        return {"contribution_usdt": 0.0, "after_removal_usdt": 0.0}
    after = top_profit_removal(values, fraction)
    return {
        "contribution_usdt": sum(values) - after,
        "after_removal_usdt": after,
    }


def _run_integrity(run: Any) -> dict[str, Any]:
    reconciliation = run.order_reconciliation
    trip_total = sum(
        float(trip["net_pnl_usdt"]) for trip in run.round_trips
    )
    trade_ok = math.isclose(
        trip_total,
        float(run.economics["net_pnl_usdt"]),
        rel_tol=0.0,
        abs_tol=1e-7,
    )
    quote_mode_ok = (
        run.two_sided_quote_decisions
        + run.one_sided_quote_decisions
        + run.no_quote_decisions
        == run.total_ticks
    )
    return {
        "order_count_reconciliation": bool(
            reconciliation["order_count_reconciliation"]
        ),
        "order_quantity_reconciliation": bool(
            reconciliation["order_quantity_reconciliation"]
        ),
        "unclassified_order_removals": int(
            reconciliation["unclassified_order_removals"]
        ),
        "margin_components_reconcile": _margin_components_reconcile(
            run.margin_events
        ),
        "trade_accounting_reconciles": trade_ok,
        "quote_mode_counters_reconcile": quote_mode_ok,
        "all_required_metrics_finite": _finite(asdict(run)),
    }


def _path_result(
    profile_record: dict[str, Any],
    path: dict[str, Any],
    capital: float,
    run: Any,
) -> dict[str, Any]:
    maker_fills = [
        fill for fill in run.fills if fill["liquidity"] == "maker"
    ]
    suppressions: dict[str, int] = {
        reason: 0 for reason in SUPPRESSION_REASONS
    }
    admissions = 0
    reducing_admissions = 0
    increasing_suppressions = 0
    for event in run.margin_events:
        reason = event.get("reason")
        if event.get("decision") == "SUPPRESS" and reason:
            suppressions[reason] = suppressions.get(reason, 0) + 1
            if event.get("inventory_impact") == "INCREASING":
                increasing_suppressions += 1
        if event.get("decision") == "ADMIT":
            admissions += 1
            if event.get("inventory_impact") == "REDUCING":
                reducing_admissions += 1
    inventory_suppressions = sum(
        bool(decision.get("bid_suppressed"))
        + bool(decision.get("ask_suppressed"))
        for decision in run.quote_decisions
    )
    suppressions["SUPPRESS_INVENTORY_LIMIT"] += inventory_suppressions
    margin_suppression_count = sum(
        count for reason, count in suppressions.items()
        if reason != "SUPPRESS_INVENTORY_LIMIT"
    )
    quote_total = max(run.total_ticks, 1)
    return {
        "profile_id": profile_record["profile_id"],
        "profile_name": profile_record["profile_name"],
        "profile_fingerprint": profile_record["profile_fingerprint"],
        "capital_usdt": capital,
        "ranking_eligible": capital == PRIMARY_CAPITAL_USDT,
        "market_path_id": path["market_path_id"],
        "market_path_hash": path["market_path_hash"],
        "scenario": path["scenario"],
        "funnel": run.funnel(),
        "quote_modes": {
            "two_sided_quote_decisions": run.two_sided_quote_decisions,
            "one_sided_quote_decisions": run.one_sided_quote_decisions,
            "no_quote_decisions": run.no_quote_decisions,
            "two_sided_quote_rate": run.two_sided_quote_decisions / quote_total,
            "one_sided_quote_rate": run.one_sided_quote_decisions / quote_total,
            "no_quote_rate": run.no_quote_decisions / quote_total,
            "margin_suppression_count": margin_suppression_count,
            "margin_suppression_rate": (
                margin_suppression_count / max(run.quote_eligible_ticks, 1)
            ),
            "inventory_reducing_quote_rate": (
                reducing_admissions / max(admissions, 1)
            ),
            "inventory_increasing_suppression_rate": (
                increasing_suppressions / max(sum(suppressions.values()), 1)
            ),
            "suppression_reasons": suppressions,
        },
        "activity": {
            "unique_conservative_maker_fills": len(maker_fills),
            "bid_fills": sum(fill["side"] == "buy" for fill in maker_fills),
            "ask_fills": sum(fill["side"] == "sell" for fill in maker_fills),
            "round_trip_inventory_cycles": len(run.round_trips),
            "partial_fills": sum(
                event.get("to_state") == "PARTIALLY_FILLED"
                for event in run.order_events
            ),
        },
        "safety": {
            "preventable_margin_breaches": run.preventable_margin_breaches,
            "unknown_margin_states": run.unknown_margin_states,
            "margin_breaches": run.margin_breaches,
            "inventory_breaches": run.inventory_breaches,
            "hard_kills": run.hard_kills,
            "terminal_residual_inventory_btc": run.terminal_residual_inventory_btc,
            "worst_drawdown": run.maximum_drawdown,
        },
        "integrity": _run_integrity(run),
        "economics": run.economics,
    }


def _aggregate_profile(
    profile_record: dict[str, Any],
    capital: float,
    paths: list[dict[str, Any]],
    runs: list[Any],
) -> dict[str, Any]:
    path_records = [
        _path_result(profile_record, path, capital, run)
        for path, run in zip(paths, runs)
    ]
    fills = [
        fill for run in runs for fill in run.fills
        if fill["liquidity"] == "maker"
    ]
    trips = [trip for run in runs for trip in run.round_trips]
    trip_net = [float(trip["net_pnl_usdt"]) for trip in trips]
    normal_trips = [
        trip for trip in trips
        if not trip["terminal_exit"] and not trip["hard_kill_exit"]
    ]
    trip_gross = [
        float(trip["gross_round_trip_spread_capture_usdt"])
        for trip in normal_trips
    ]
    trip_net_spread = [
        float(trip["gross_round_trip_spread_capture_usdt"])
        - float(trip["fees_usdt"])
        for trip in normal_trips
    ]
    added_costs = [
        float(trip["round_trip_notional_usdt"])
        * ADDED_COST_BPS_PER_ROUND_TRIP / 10_000.0
        for trip in trips
    ]
    net_pnl = sum(float(run.economics["net_pnl_usdt"]) for run in runs)
    gross_spread = sum(
        float(run.economics["gross_spread_capture_usdt"]) for run in runs
    )
    maker_fees = sum(
        float(run.economics["maker_fees_usdt"]) for run in runs
    )
    taker_fees = sum(
        float(run.economics["taker_fees_usdt"]) for run in runs
    )
    terminal_cost = sum(
        float(run.economics["terminal_liquidation_cost_usdt"]) for run in runs
    )
    terminal_pnl = sum(
        float(run.economics["terminal_liquidation_pnl_usdt"]) for run in runs
    )
    terminal_net = sum(
        float(trip["net_pnl_usdt"])
        for trip in trips if trip["terminal_exit"]
    )
    hard_kill_pnl = sum(
        float(run.economics["hard_kill_execution_pnl_usdt"]) for run in runs
    )
    markouts = {
        horizon: [
            float(fill[f"markout_{horizon}_tick_usdt_per_btc"])
            * float(fill["size"])
            for fill in fills
        ]
        for horizon in (1, 5, 10)
    }
    quote_ticks = sum(run.total_ticks for run in runs)
    two_sided = sum(run.two_sided_quote_decisions for run in runs)
    one_sided = sum(run.one_sided_quote_decisions for run in runs)
    no_quote = sum(run.no_quote_decisions for run in runs)
    margin_suppressions = sum(
        record["quote_modes"]["margin_suppression_count"]
        for record in path_records
    )
    scenarios = {
        record["scenario"] for record in path_records
        if record["activity"]["unique_conservative_maker_fills"] > 0
    }
    activity = {
        "unique_conservative_maker_fills": len(fills),
        "bid_fills": sum(fill["side"] == "buy" for fill in fills),
        "ask_fills": sum(fill["side"] == "sell" for fill in fills),
        "round_trip_inventory_cycles": len(trips),
        "represented_scenarios": len(scenarios),
        "two_sided_quote_rate": two_sided / max(quote_ticks, 1),
        "one_sided_quote_rate": one_sided / max(quote_ticks, 1),
        "no_quote_rate": no_quote / max(quote_ticks, 1),
        "margin_suppression_rate": margin_suppressions / max(quote_ticks, 1),
    }
    activity["passed"] = (
        activity["unique_conservative_maker_fills"] >= 50
        and activity["bid_fills"] >= 15
        and activity["ask_fills"] >= 15
        and activity["round_trip_inventory_cycles"] >= 10
        and activity["represented_scenarios"] >= 4
    )
    safety = {
        "preventable_margin_breaches": sum(
            run.preventable_margin_breaches for run in runs
        ),
        "unknown_margin_states": sum(run.unknown_margin_states for run in runs),
        "margin_breaches": sum(run.margin_breaches for run in runs),
        "inventory_breaches": sum(run.inventory_breaches for run in runs),
        "hard_kills": sum(run.hard_kills for run in runs),
        "terminal_residual_inventory_btc": sum(
            run.terminal_residual_inventory_btc for run in runs
        ),
        "unreconciled_accounting": sum(
            not record["integrity"]["trade_accounting_reconciles"]
            for record in path_records
        ),
        "worst_drawdown": max(
            (run.maximum_drawdown for run in runs), default=0.0
        ),
    }
    safety["passed"] = (
        safety["preventable_margin_breaches"] == 0
        and safety["unknown_margin_states"] == 0
        and safety["inventory_breaches"] == 0
        and safety["hard_kills"] == 0
        and abs(safety["terminal_residual_inventory_btc"]) <= 1e-12
        and safety["unreconciled_accounting"] == 0
        and safety["worst_drawdown"] <= 0.025 + 1e-12
    )
    concentration = {
        "top_1pct": _concentration(trip_net, 0.01),
        "top_5pct": _concentration(trip_net, 0.05),
        "top_10pct": _concentration(trip_net, 0.10),
    }
    economics = {
        "gross_spread_capture_usdt": gross_spread,
        "net_spread_capture_usdt": gross_spread - maker_fees,
        "realized_inventory_pnl_usdt": sum(
            float(run.economics["realized_inventory_pnl_usdt"])
            for run in runs
        ),
        "inventory_mark_to_market_usdt": sum(
            float(run.economics["inventory_mark_to_market_usdt"])
            for run in runs
        ),
        "maker_fees_usdt": maker_fees,
        "taker_fees_usdt": taker_fees,
        "funding_usdt": 0.0,
        "average_1_tick_markout_usdt": (
            mean(markouts[1]) if markouts[1] else 0.0
        ),
        "average_5_tick_markout_usdt": (
            mean(markouts[5]) if markouts[5] else 0.0
        ),
        "average_10_tick_markout_usdt": (
            mean(markouts[10]) if markouts[10] else 0.0
        ),
        "hard_kill_execution_loss_usdt": max(0.0, -hard_kill_pnl),
        "terminal_liquidation_loss_usdt": max(0.0, -terminal_pnl),
        "terminal_liquidation_cost_usdt": terminal_cost,
        "emergency_execution_loss_usdt": sum(
            float(run.economics["emergency_execution_cost_usdt"])
            for run in runs
        ),
        "net_pnl_usdt": net_pnl,
        "round_trip_count": len(trips),
        "average_gross_spread_capture_usdt": (
            mean(trip_gross) if trip_gross else 0.0
        ),
        "average_net_spread_capture_usdt": (
            mean(trip_net_spread) if trip_net_spread else 0.0
        ),
        "median_net_spread_capture_usdt": (
            median(trip_net) if trip_net else 0.0
        ),
        "round_trip_expectancy_usdt": (
            mean(trip_net) if trip_net else 0.0
        ),
        "round_trip_profit_factor": _profit_factor(trip_net),
        "winning_round_trip_rate": (
            sum(value > 0 for value in trip_net) / max(len(trip_net), 1)
        ),
        "average_holding_duration_ticks": (
            mean(float(trip["holding_duration_ticks"]) for trip in trips)
            if trips else 0.0
        ),
        "added_2bps_cost_usdt": sum(added_costs),
        "net_pnl_after_added_2bps_usdt": net_pnl - sum(added_costs),
        "stressed_expectancy_usdt": (
            mean(
                value - cost for value, cost in zip(trip_net, added_costs)
            )
            if trip_net else 0.0
        ),
        "after_top_1pct_removal_usdt": (
            concentration["top_1pct"]["after_removal_usdt"]
        ),
        "after_top_10pct_removal_usdt": (
            concentration["top_10pct"]["after_removal_usdt"]
        ),
        "top_1pct_profit_contribution_usdt": (
            concentration["top_1pct"]["contribution_usdt"]
        ),
        "top_5pct_profit_contribution_usdt": (
            concentration["top_5pct"]["contribution_usdt"]
        ),
        "top_10pct_profit_contribution_usdt": (
            concentration["top_10pct"]["contribution_usdt"]
        ),
    }
    terminal_did_not_create_loss = not (
        net_pnl <= 0 < net_pnl - terminal_net
    )
    economics["passed"] = (
        net_pnl > 0
        and economics["round_trip_expectancy_usdt"] > 0
        and economics["round_trip_profit_factor"] > 1
        and economics["net_pnl_after_added_2bps_usdt"] > 0
        and economics["after_top_10pct_removal_usdt"] > 0
        and economics["average_net_spread_capture_usdt"] > 0
        and economics["average_5_tick_markout_usdt"] >= -0.05
        and gross_spread > maker_fees + taker_fees
        and terminal_did_not_create_loss
    )
    integrity = {
        "order_counts_reconcile": all(
            record["integrity"]["order_count_reconciliation"]
            for record in path_records
        ),
        "order_quantities_reconcile": all(
            record["integrity"]["order_quantity_reconciliation"]
            for record in path_records
        ),
        "margin_components_reconcile": all(
            record["integrity"]["margin_components_reconcile"]
            for record in path_records
        ),
        "trade_accounting_reconciles": all(
            record["integrity"]["trade_accounting_reconciles"]
            for record in path_records
        ),
        "unclassified_order_removals": sum(
            record["integrity"]["unclassified_order_removals"]
            for record in path_records
        ),
        "all_required_metrics_finite": all(
            record["integrity"]["all_required_metrics_finite"]
            for record in path_records
        ) and _finite(activity) and _finite(safety) and _finite(economics),
    }
    integrity["passed"] = (
        integrity["order_counts_reconcile"]
        and integrity["order_quantities_reconcile"]
        and integrity["margin_components_reconcile"]
        and integrity["trade_accounting_reconciles"]
        and integrity["unclassified_order_removals"] == 0
        and integrity["all_required_metrics_finite"]
    )
    return {
        "profile_id": profile_record["profile_id"],
        "profile_name": profile_record["profile_name"],
        "profile_fingerprint": profile_record["profile_fingerprint"],
        "capital_usdt": capital,
        "ranking_eligible": capital == PRIMARY_CAPITAL_USDT,
        "unique_market_observation_count": len(paths),
        "capital_copy_inflates_evidence": False,
        "integrity": integrity,
        "safety": safety,
        "activity": activity,
        "economics": economics,
        "concentration": concentration,
        "path_results": path_records,
    }


def _markout_analysis(
    primary_runs: list[tuple[dict[str, Any], dict[str, Any], Any]]
) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for profile, path, run in primary_runs:
        inventory = 0.0
        for fill in run.fills:
            before = inventory
            inventory += (
                float(fill["size"]) if fill["side"] == "buy"
                else -float(fill["size"])
            )
            if fill["liquidity"] != "maker":
                continue
            inventory_state = (
                "FLAT" if abs(before) <= 1e-12
                else "LONG" if before > 0 else "SHORT"
            )
            distance = float(fill["quote_distance_bps"])
            distance_bucket = (
                "LE_5_BPS" if distance <= 5
                else "LE_10_BPS" if distance <= 10 else "GT_10_BPS"
            )
            volatility_regime = (
                "HIGH" if "high_volatility" in path["scenario"] else "NORMAL"
            )
            key_fields = {
                "profile_id": profile["profile_id"],
                "side": fill["side"],
                "scenario": path["scenario"],
                "volatility_regime": volatility_regime,
                "inventory_state": inventory_state,
                "quote_distance_bucket": distance_bucket,
            }
            key = json.dumps(key_fields, sort_keys=True)
            group = groups.setdefault(key, {
                **key_fields, "count": 0, "markout_1": [],
                "markout_5": [], "markout_10": [],
            })
            group["count"] += 1
            for horizon in (1, 5, 10):
                group[f"markout_{horizon}"].append(
                    float(fill[f"markout_{horizon}_tick_usdt_per_btc"])
                    * float(fill["size"])
                )
    output = []
    for group in groups.values():
        output.append({
            key: value for key, value in group.items()
            if not key.startswith("markout_")
        } | {
            f"average_{horizon}_tick_markout_usdt": (
                mean(group[f"markout_{horizon}"])
                if group[f"markout_{horizon}"] else 0.0
            )
            for horizon in (1, 5, 10)
        })
    return {
        "sign_convention": {
            "buy": "future_mid - fill_price",
            "sell": "fill_price - future_mid",
            "positive": "favorable execution",
        },
        "groups": sorted(
            output,
            key=lambda item: (
                item["profile_id"], item["scenario"], item["side"],
                item["inventory_state"], item["quote_distance_bucket"],
            ),
        ),
    }


def decide_status(
    *,
    integrity_passed: bool,
    determinism_passed: bool,
    safety_passed: bool,
    activity_profile_count: int,
    economics_profile_count: int,
) -> tuple[str, str | None]:
    """Apply the frozen gates in their declared order."""
    if not integrity_passed or not determinism_passed:
        return "MM_V1_1_SMOKE_FAILED", "GATE_0_INTEGRITY"
    if not safety_passed:
        return "MM_V1_1_SMOKE_FAILED", "GATE_1_ADMISSION_AND_SAFETY"
    if activity_profile_count == 0:
        return "MM_V1_1_SMOKE_INSUFFICIENT_ACTIVITY", "GATE_2_ACTIVITY"
    if economics_profile_count == 0:
        return (
            "MM_V1_1_SMOKE_ECONOMICS_REJECTED",
            "GATE_3_CONSERVATIVE_ECONOMICS",
        )
    return "MM_V1_1_SMOKE_ECONOMICS_SUPPORTED", None


def evaluate_specification(spec: dict[str, Any]) -> dict[str, Any]:
    paths = spec["paths"]
    profiles = spec["fixed_profiles"]
    all_runs: list[tuple[float, dict[str, Any], dict[str, Any], Any]] = []
    aggregates: list[dict[str, Any]] = []
    for capital in (PRIMARY_CAPITAL_USDT, *STRESS_CAPITALS_USDT):
        for profile_record in profiles:
            profile = MarketMakerV1Config(**profile_record["parameters"])
            if profile.fingerprint != profile_record["profile_fingerprint"]:
                raise RuntimeError("profile fingerprint mismatch")
            runs = []
            for path in paths:
                ticks = scenario_ticks(
                    path["scenario"],
                    int(path["market_seed"]),
                    count=int(path["scenario_parameters"]["tick_count"]),
                )
                if payload_hash(ticks) != path["market_path_hash"]:
                    raise RuntimeError("path hash mismatch")
                run = MarketMakerBacktestRunner(
                    profile,
                    protocol_id=SMOKE_ID,
                    scenario=path["scenario"],
                    source_block=path["source_block"],
                    fill_seed=int(path["fill_seed"]),
                    cancel_latency_ticks=int(path["cancel_latency_ticks"]),
                    initial_capital_usdt=capital,
                    leverage=LEVERAGE,
                ).run(ticks)
                runs.append(run)
                all_runs.append((capital, profile_record, path, run))
            aggregates.append(
                _aggregate_profile(profile_record, capital, paths, runs)
            )

    primary_runs = [
        (profile, path, run)
        for capital, profile, path, run in all_runs
        if capital == PRIMARY_CAPITAL_USDT
    ]
    replay_hashes = []
    for profile_record, path, original in primary_runs:
        profile = MarketMakerV1Config(**profile_record["parameters"])
        ticks = scenario_ticks(
            path["scenario"],
            int(path["market_seed"]),
            count=int(path["scenario_parameters"]["tick_count"]),
        )
        replay = MarketMakerBacktestRunner(
            profile,
            protocol_id=SMOKE_ID,
            scenario=path["scenario"],
            source_block=path["source_block"],
            fill_seed=int(path["fill_seed"]),
            cancel_latency_ticks=int(path["cancel_latency_ticks"]),
            initial_capital_usdt=PRIMARY_CAPITAL_USDT,
            leverage=LEVERAGE,
        ).run(ticks)
        replay_hashes.append(
            hashlib.sha256(canonical_bytes(asdict(original))).hexdigest()
            == hashlib.sha256(canonical_bytes(asdict(replay))).hexdigest()
        )

    primary = [
        aggregate for aggregate in aggregates if aggregate["ranking_eligible"]
    ]
    integrity_passed = all(item["integrity"]["passed"] for item in primary)
    determinism_passed = all(replay_hashes)
    safety_passed = all(item["safety"]["passed"] for item in primary)
    activity_profiles = [
        item for item in primary if item["activity"]["passed"]
    ]
    economics_profiles = [
        item for item in activity_profiles if item["economics"]["passed"]
    ]
    status, first_failed_gate = decide_status(
        integrity_passed=integrity_passed,
        determinism_passed=determinism_passed,
        safety_passed=safety_passed,
        activity_profile_count=len(activity_profiles),
        economics_profile_count=len(economics_profiles),
    )
    ranked = sorted(
        activity_profiles,
        key=lambda item: (
            item["economics"]["net_pnl_usdt"],
            item["economics"]["round_trip_profit_factor"],
            item["profile_fingerprint"],
        ),
        reverse=True,
    )
    return {
        "status": status,
        "first_failed_gate": first_failed_gate,
        "integrity_passed": integrity_passed,
        "determinism_passed": determinism_passed,
        "safety_passed": safety_passed,
        "fixed_profile_count": len(profiles),
        "path_count": len(paths),
        "profiles_passing_activity": len(activity_profiles),
        "profiles_passing_economics": len(economics_profiles),
        "best_profile_id": ranked[0]["profile_id"] if ranked else None,
        "best_net_pnl_usdt": (
            ranked[0]["economics"]["net_pnl_usdt"] if ranked else None
        ),
        "best_profit_factor": (
            ranked[0]["economics"]["round_trip_profit_factor"]
            if ranked else None
        ),
        "primary_aggregates": primary,
        "stress_aggregates": [
            item for item in aggregates if not item["ranking_eligible"]
        ],
        "all_runs": all_runs,
        "markout_analysis": _markout_analysis(primary_runs),
    }


def declare(root: Path, specification_dir: Path) -> dict[str, Any]:
    if specification_dir.exists():
        raise FileExistsError("refusing specification overwrite or run-ID reuse")
    specification = build_specification(root)
    prior_protocol = (
        root / "artifacts" / "market_maker_v1_smoke"
        / "protocol_20260726T145535Z" / "mm_v1_smoke_protocol.json"
    )
    if prior_protocol.exists():
        prior = json.loads(prior_protocol.read_text(encoding="utf-8"))
        old_ids = {path["market_path_id"] for path in prior["paths"]}
        old_hashes = {path["market_path_hash"] for path in prior["paths"]}
        if any(
            path["market_path_id"] in old_ids
            or path["market_path_hash"] in old_hashes
            for path in specification["paths"]
        ):
            raise RuntimeError("new path matrix overlaps prior smoke")
    specification_dir.mkdir(parents=True)
    spec_path = specification_dir / "smoke_spec.json"
    _atomic_json(spec_path, specification)
    digest = file_hash(spec_path)
    _write_exclusive(
        specification_dir / "smoke_spec.sha256",
        f"{digest}  smoke_spec.json\n",
    )
    _write_json(
        specification_dir / "fixed_profiles.json",
        specification["fixed_profiles"],
    )
    _write_json(
        specification_dir / "capital_policy.json",
        {
            "primary_capital_usdt": PRIMARY_CAPITAL_USDT,
            "stress_capitals_usdt": list(STRESS_CAPITALS_USDT),
            "stress_capitals_ranking_eligible": False,
            "capital_copies_increase_evidence": False,
        },
    )
    _write_json(
        specification_dir / "path_matrix.json", specification["paths"]
    )
    _write_json(
        specification_dir / "economic_gates.json", specification["gates"]
    )
    _write_exclusive(
        specification_dir / "smoke_spec.md",
        "# Market Maker v1.1 Fixed Economic Smoke\n\n"
        f"- Smoke ID: `{SMOKE_ID}`\n"
        f"- Primary capital: {PRIMARY_CAPITAL_USDT:.0f} USDT\n"
        f"- Fixed profiles: {len(specification['fixed_profiles'])}\n"
        f"- New paths: {len(specification['paths'])}\n"
        "- Conservative fills only determine ranking\n"
        "- Optuna: No\n"
        "- Validation/Holdout: No/No\n",
    )
    return {"specification_sha256": digest}


def run_frozen(
    root: Path,
    specification_dir: Path,
    run_dir: Path,
    fragility_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if any(path.exists() for path in (run_dir, fragility_dir, decision_dir)):
        raise FileExistsError("refusing artifact overwrite or run-ID reuse")
    spec_path = specification_dir / "smoke_spec.json"
    expected_hash = (
        specification_dir / "smoke_spec.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if file_hash(spec_path) != expected_hash:
        raise RuntimeError("smoke specification hash mismatch")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after specification freeze")
    result = evaluate_specification(spec)
    all_runs = result.pop("all_runs")
    markout_analysis = result.pop("markout_analysis")

    run_dir.mkdir(parents=True)
    quote_decisions = []
    margin_events = []
    order_events = []
    fills = []
    round_trips = []
    for capital, profile, path, run in all_runs:
        common = {
            "capital_usdt": capital,
            "ranking_eligible": capital == PRIMARY_CAPITAL_USDT,
            "profile_id": profile["profile_id"],
            "profile_fingerprint": profile["profile_fingerprint"],
            "market_path_id": path["market_path_id"],
            "scenario": path["scenario"],
        }
        quote_decisions.extend(
            [{**common, **item} for item in run.quote_decisions]
        )
        margin_events.extend(
            [{**common, **item} for item in run.margin_events]
        )
        order_events.extend(
            [{**common, **item} for item in run.order_events]
        )
        fills.extend([{**common, **item} for item in run.fills])
        round_trips.extend(
            [{**common, **item} for item in run.round_trips]
        )
    _write_jsonl(run_dir / "quote_decisions.jsonl", quote_decisions)
    _write_jsonl(run_dir / "margin_events.jsonl", margin_events)
    _write_jsonl(run_dir / "order_events.jsonl", order_events)
    _write_jsonl(run_dir / "fills.jsonl", fills)
    _write_jsonl(run_dir / "round_trips.jsonl", round_trips)
    _write_jsonl(
        run_dir / "profile_path_results.jsonl",
        [
            path_result
            for aggregate in result["primary_aggregates"]
            for path_result in aggregate["path_results"]
        ],
    )
    _write_jsonl(
        run_dir / "capital_stress_results.jsonl",
        [
            {
                key: value for key, value in aggregate.items()
                if key != "path_results"
            }
            for aggregate in result["stress_aggregates"]
        ],
    )
    compact_primary = [
        {
            key: value for key, value in aggregate.items()
            if key != "path_results"
        }
        for aggregate in result["primary_aggregates"]
    ]
    summary = {
        key: value for key, value in result.items()
        if key not in {"primary_aggregates", "stress_aggregates"}
    }
    summary["primary_profiles"] = compact_primary
    _atomic_json(run_dir / "smoke_summary.json", summary)
    raw_names = (
        "quote_decisions.jsonl",
        "margin_events.jsonl",
        "order_events.jsonl",
        "fills.jsonl",
        "round_trips.jsonl",
        "profile_path_results.jsonl",
        "capital_stress_results.jsonl",
        "smoke_summary.json",
    )
    manifest = {
        "schema_version": "mm-v1-1-run-manifest-v1",
        "smoke_id": SMOKE_ID,
        "specification_sha256": expected_hash,
        "source_hashes": spec["source_hashes"],
        "profile_fingerprints": {
            item["profile_id"]: item["profile_fingerprint"]
            for item in spec["fixed_profiles"]
        },
        "path_hashes": {
            item["market_path_id"]: item["market_path_hash"]
            for item in spec["paths"]
        },
        "raw_file_hashes": {
            name: file_hash(run_dir / name) for name in raw_names
        },
        "commands": [{
            "command": "python -m backtest.mm_v1_1_smoke run ...",
            "exit_code": 0,
        }],
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
    }
    _atomic_json(run_dir / "run_manifest.json", manifest)
    _atomic_json(run_dir / "COMPLETED.json", {
        "status": "COMPLETED",
        "decision": summary["status"],
        "files": {
            path.name: file_hash(path)
            for path in sorted(run_dir.iterdir())
            if path.name != "COMPLETED.json"
        },
    })

    fragility_dir.mkdir(parents=True)
    _write_json(
        fragility_dir / "cost_stress.json",
        {
            aggregate["profile_id"]: {
                "base_net_pnl_usdt": aggregate["economics"]["net_pnl_usdt"],
                "added_cost_usdt": aggregate["economics"]["added_2bps_cost_usdt"],
                "stressed_net_pnl_usdt": (
                    aggregate["economics"]["net_pnl_after_added_2bps_usdt"]
                ),
                "base_expectancy_usdt": (
                    aggregate["economics"]["round_trip_expectancy_usdt"]
                ),
                "stressed_expectancy_usdt": (
                    aggregate["economics"]["stressed_expectancy_usdt"]
                ),
            }
            for aggregate in result["primary_aggregates"]
        },
    )
    _write_json(
        fragility_dir / "concentration.json",
        {
            aggregate["profile_id"]: aggregate["concentration"]
            for aggregate in result["primary_aggregates"]
        },
    )
    _write_json(
        fragility_dir / "markout_analysis.json", markout_analysis
    )
    _write_exclusive(
        fragility_dir / "fragility_report.md",
        "# Market Maker v1.1 Fragility\n\n"
        "Fixed +2 bps per completed round-trip, top-1/5/10% concentration, "
        "and signed 1/5/10-tick markouts were evaluated for every primary "
        "profile. No stress result can enter ranking.\n",
    )

    decision_dir.mkdir(parents=True)
    decision = {
        "schema_version": "mm-v1-1-decision-v1",
        "status": summary["status"],
        "first_failed_gate": summary["first_failed_gate"],
        "primary_capital_usdt": PRIMARY_CAPITAL_USDT,
        "fixed_profile_count": summary["fixed_profile_count"],
        "path_count": summary["path_count"],
        "profiles_passing_activity": summary["profiles_passing_activity"],
        "profiles_passing_economics": summary["profiles_passing_economics"],
        "best_profile_id": summary["best_profile_id"],
        "best_net_pnl_usdt": summary["best_net_pnl_usdt"],
        "best_profit_factor": summary["best_profit_factor"],
        "preventable_margin_breaches": sum(
            item["safety"]["preventable_margin_breaches"]
            for item in result["primary_aggregates"]
        ),
        "hard_kills": sum(
            item["safety"]["hard_kills"]
            for item in result["primary_aggregates"]
        ),
        "optimization_ran": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
    }
    _atomic_json(decision_dir / "decision.json", decision)
    _write_exclusive(
        decision_dir / "decision.md",
        "# Market Maker v1.1 Economic Smoke Decision\n\n"
        f"Status: `{decision['status']}`\n\n"
        f"First failed gate: `{decision['first_failed_gate']}`\n\n"
        "This fixed-profile result is offline research only. Optuna, "
        "validation, holdout, exchange access, and deployment remain unauthorized.\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    declare_parser = commands.add_parser("declare")
    declare_parser.add_argument("--specification-dir", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--specification-dir", type=Path, required=True)
    run_parser.add_argument("--run-dir", type=Path, required=True)
    run_parser.add_argument("--fragility-dir", type=Path, required=True)
    run_parser.add_argument("--decision-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "declare":
        output = declare(root, args.specification_dir)
    else:
        output = run_frozen(
            root,
            args.specification_dir,
            args.run_dir,
            args.fragility_dir,
            args.decision_dir,
        )
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
