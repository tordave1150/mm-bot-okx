"""Deterministic bounded sampler and candidate evaluation for MM v1 smoke."""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Any

from backtest.mm_protocol import (
    MAX_EVALUATED_CANDIDATES,
    PROPOSAL_CEILING,
    SAMPLER_SEED,
    SEARCH_SPACE,
    VALID_CANDIDATE_TARGET,
    payload_hash,
    scenario_ticks,
)
from backtest.mm_runner import MarketMakerBacktestRunner
from market_maker.as_config import MarketMakerV1Config
from market_maker.diagnostics import conservative_objective, population_variance


def sample_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(SAMPLER_SEED)
    proposals = []
    candidates = []
    seen: set[str] = set()
    for attempt in range(1, PROPOSAL_CEILING + 1):
        parameters = {
            name: rng.choice(values) for name, values in SEARCH_SPACE.items()
        }
        proposal: dict[str, Any] = {
            "attempt": attempt,
            "parameters": parameters,
        }
        try:
            profile = MarketMakerV1Config(**parameters)
            profile.validate()
            if profile.fingerprint in seen:
                proposal["status"] = "DUPLICATE"
            else:
                seen.add(profile.fingerprint)
                proposal["status"] = "VALID"
                proposal["profile_fingerprint"] = profile.fingerprint
                candidates.append({
                    "candidate_index": len(candidates),
                    "profile_fingerprint": profile.fingerprint,
                    "parameters": parameters,
                })
        except ValueError as exc:
            proposal["status"] = "INVALID_CONSTRAINT"
            proposal["reason"] = str(exc)
        proposals.append(proposal)
        if len(candidates) == VALID_CANDIDATE_TARGET:
            break
    if len(candidates) != VALID_CANDIDATE_TARGET:
        raise RuntimeError("bounded sampler did not reach valid-candidate target")
    return proposals, candidates


def evaluate_candidate(
    candidate_record: dict[str, Any],
    paths: list[dict[str, Any]],
    *,
    protocol_id: str,
) -> dict[str, Any]:
    profile = MarketMakerV1Config(**candidate_record["parameters"])
    runs = []
    for path in paths:
        ticks = scenario_ticks(
            path["scenario"],
            int(path["market_seed"]),
            count=int(path["scenario_parameters"]["tick_count"]),
        )
        if payload_hash(ticks) != path["market_path_hash"]:
            raise RuntimeError("market-path hash mismatch")
        runner = MarketMakerBacktestRunner(
            profile,
            protocol_id=protocol_id,
            scenario=path["scenario"],
            source_block=path["source_block"],
            fill_seed=int(path["fill_seed"]),
            cancel_latency_ticks=int(path["cancel_latency_ticks"]),
        )
        runs.append(runner.run(ticks))

    maker_fills = [
        fill for run in runs for fill in run.fills
        if fill["liquidity"] == "maker"
    ]
    trips = [trip for run in runs for trip in run.round_trips]
    trip_pnls = [float(trip["net_pnl_usdt"]) for trip in trips]
    total_profit = sum(max(0.0, value) for value in trip_pnls)
    total_loss = sum(max(0.0, -value) for value in trip_pnls)
    profit_factor = (
        total_profit / total_loss if total_loss > 0
        else 10.0 if total_profit > 0 else 0.0
    )
    net_pnl = sum(float(run.economics["net_pnl_usdt"]) for run in runs)
    gross_spread = sum(
        float(run.economics["gross_spread_capture_usdt"]) for run in runs
    )
    after_top = sum(
        float(run.economics["after_top_10pct_removal_usdt"]) for run in runs
    )
    added_2bps = sum(
        float(fill["price"]) * float(fill["size"]) * 0.0002
        for fill in maker_fills
    )
    markouts = [
        float(fill["markout_5_tick_usdt_per_btc"]) * float(fill["size"])
        for fill in maker_fills
    ]
    quote_uptime = sum(run.quote_eligible_ticks for run in runs) / max(
        sum(run.total_ticks for run in runs), 1
    )
    requotes = sum(run.requotes for run in runs)
    orders_created = sum(
        run.bid_orders_created + run.ask_orders_created for run in runs
    )
    cancelled = sum(
        run.order_reconciliation["fully_cancelled_orders"] for run in runs
    )
    metrics = {
        "net_pnl_usdt": net_pnl,
        "gross_spread_capture_usdt": gross_spread,
        "average_spread_capture_usdt": (
            gross_spread / len(trips) if trips else 0.0
        ),
        "profit_factor": profit_factor,
        "profit_factor_clipped": min(10.0, profit_factor),
        "net_pnl_after_added_2bps_usdt": net_pnl - added_2bps,
        "after_top_10pct_removal_usdt": after_top,
        "average_5_tick_markout_usdt": mean(markouts) if markouts else 0.0,
        "quote_uptime": quote_uptime,
        "max_drawdown": max(run.maximum_drawdown for run in runs),
        "inventory_variance": population_variance(
            [value for run in runs for value in run.inventory_curve]
        ),
        "adverse_markout_loss_usdt": sum(max(0.0, -value) for value in markouts),
        "terminal_liquidation_cost_usdt": sum(
            float(run.economics["terminal_liquidation_cost_usdt"]) for run in runs
        ),
        "quote_churn": requotes / max(orders_created, 1),
        "cancel_to_fill_ratio": cancelled / max(len(maker_fills), 1),
    }
    integrity = {
        "order_count_reconciliation": all(
            run.order_reconciliation["order_count_reconciliation"] for run in runs
        ),
        "order_quantity_reconciliation": all(
            run.order_reconciliation["order_quantity_reconciliation"] for run in runs
        ),
        "funnel_reconciliation": all(
            run.order_reconciliation["funnel_accounting_reconciliation"] for run in runs
        ),
        "unclassified_order_removals": sum(
            run.order_reconciliation["unclassified_order_removals"] for run in runs
        ),
        "trade_accounting_reconciliation": all(
            abs(run.terminal_residual_inventory_btc) <= 1e-12 for run in runs
        ),
        "all_evidence_finite": all(
            math.isfinite(float(value)) for value in metrics.values()
        ),
    }
    integrity["passed"] = (
        integrity["order_count_reconciliation"]
        and integrity["order_quantity_reconciliation"]
        and integrity["funnel_reconciliation"]
        and integrity["unclassified_order_removals"] == 0
        and integrity["trade_accounting_reconciliation"]
        and integrity["all_evidence_finite"]
    )
    activity = {
        "unique_conservative_maker_fills": len(maker_fills),
        "bid_fills": sum(run.bid_fills for run in runs),
        "ask_fills": sum(run.ask_fills for run in runs),
        "round_trip_inventory_cycles": len(trips),
        "represented_scenarios": sum(
            any(fill["liquidity"] == "maker" for fill in run.fills)
            for run in runs
        ),
    }
    activity["passed"] = (
        activity["unique_conservative_maker_fills"] >= 50
        and activity["bid_fills"] >= 15
        and activity["ask_fills"] >= 15
        and activity["round_trip_inventory_cycles"] >= 10
        and activity["represented_scenarios"] >= 4
    )
    safety = {
        "inventory_breaches": sum(run.inventory_breaches for run in runs),
        "margin_breaches": sum(run.margin_breaches for run in runs),
        "hard_kills": sum(run.hard_kills for run in runs),
        "unreconciled_accounting": sum(
            not run.order_reconciliation["funnel_accounting_reconciliation"]
            for run in runs
        ),
        "terminal_residual_inventory_btc": sum(
            run.terminal_residual_inventory_btc for run in runs
        ),
        "worst_drawdown": max(run.maximum_drawdown for run in runs),
    }
    safety["passed"] = (
        safety["inventory_breaches"] == 0
        and safety["margin_breaches"] == 0
        and safety["hard_kills"] == 0
        and safety["unreconciled_accounting"] == 0
        and safety["terminal_residual_inventory_btc"] <= 1e-12
        and safety["worst_drawdown"] <= 0.025
    )
    economics_passed = (
        metrics["net_pnl_usdt"] > 0
        and metrics["average_spread_capture_usdt"] > 0
        and metrics["profit_factor"] > 1
        and metrics["net_pnl_after_added_2bps_usdt"] > 0
        and metrics["after_top_10pct_removal_usdt"] > 0
        and metrics["average_5_tick_markout_usdt"] >= -0.05
    )
    score = (
        conservative_objective(metrics)
        if integrity["passed"] and activity["passed"] and safety["passed"]
        else -1.0
    )
    return {
        **candidate_record,
        "integrity": integrity,
        "activity": activity,
        "safety": safety,
        "economics": metrics,
        "economics_passed": economics_passed,
        "objective_score": score,
        "runs": runs,
    }


def evaluate_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    _, candidates = sample_candidates()
    if [
        item["profile_fingerprint"] for item in candidates
    ] != protocol["sampler"]["frozen_profile_fingerprints"]:
        raise RuntimeError("frozen candidate sequence mismatch")
    trials = [
        evaluate_candidate(
            candidate,
            protocol["paths"],
            protocol_id=protocol["protocol_id"],
        )
        for candidate in candidates[:MAX_EVALUATED_CANDIDATES]
    ]
    digest_payload = [
        {
            "profile_fingerprint": trial["profile_fingerprint"],
            "integrity": trial["integrity"],
            "activity": trial["activity"],
            "safety": trial["safety"],
            "economics": trial["economics"],
            "objective_score": trial["objective_score"],
        }
        for trial in trials
    ]
    replay = [
        evaluate_candidate(
            candidate,
            protocol["paths"],
            protocol_id=protocol["protocol_id"],
        )
        for candidate in candidates[:MAX_EVALUATED_CANDIDATES]
    ]
    replay_payload = [
        {
            "profile_fingerprint": trial["profile_fingerprint"],
            "integrity": trial["integrity"],
            "activity": trial["activity"],
            "safety": trial["safety"],
            "economics": trial["economics"],
            "objective_score": trial["objective_score"],
        }
        for trial in replay
    ]
    determinism = payload_hash(digest_payload) == payload_hash(replay_payload)
    feasible = [
        trial for trial in trials
        if trial["integrity"]["passed"]
        and trial["activity"]["passed"]
        and trial["safety"]["passed"]
    ]
    selected = max(
        feasible,
        key=lambda item: (
            item["objective_score"],
            item["profile_fingerprint"],
        ),
        default=None,
    )
    if not all(trial["integrity"]["passed"] for trial in trials):
        status, first_failure = "MM_V1_SMOKE_REJECTED", "GATE_0_INTEGRITY"
    elif not determinism:
        status, first_failure = "MM_V1_SMOKE_REJECTED", "GATE_0_DETERMINISM"
    elif not any(trial["activity"]["passed"] for trial in trials):
        status, first_failure = (
            "MM_V1_SMOKE_INSUFFICIENT_EVIDENCE",
            "GATE_1_ACTIVITY",
        )
    elif selected is None or not selected["safety"]["passed"]:
        status, first_failure = "MM_V1_SMOKE_REJECTED", "GATE_2_SAFETY"
    elif not selected["economics_passed"]:
        status, first_failure = (
            "MM_V1_SMOKE_REJECTED",
            "GATE_3_CONSERVATIVE_ECONOMICS",
        )
    else:
        status, first_failure = "MM_V1_SMOKE_SUPPORTED", None
    return {
        "status": status,
        "first_failed_gate": first_failure,
        "determinism_passed": determinism,
        "evaluated_candidate_count": len(trials),
        "selected_profile_fingerprint": (
            selected["profile_fingerprint"] if selected else None
        ),
        "selected_activity": selected["activity"] if selected else None,
        "selected_safety": selected["safety"] if selected else None,
        "selected_economics": selected["economics"] if selected else None,
        "trials": trials,
    }
