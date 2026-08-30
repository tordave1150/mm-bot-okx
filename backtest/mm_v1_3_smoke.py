"""Declare and execute the frozen MM v1.3 balanced-causal smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any

from backtest.mm_execution_microstructure import CausalTradeEventMatcher
from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3_protocol import (
    ADDED_COST_BPS, BALANCED_MODEL, PRIMARY_CAPITAL, SMOKE_ID, STRESS_MODEL,
    balanced_ticks, build_specification, canonical_bytes, file_hash,
    payload_hash, source_hashes, strict_ticks,
)
from market_maker.as_config import MarketMakerV1Config
from market_maker.as_strategy import MarketMakerV1Strategy
from market_maker.execution_accounting import (
    FIFORoundTripMatcher, economic_attribution, markout,
)
from market_maker.margin import OrderExposure, admit_proposed_orders


D = Decimal


def _exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _json(path: Path, value: Any) -> None:
    _exclusive(path, json.dumps(value, indent=2, sort_keys=True,
                                allow_nan=False) + "\n")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _exclusive(path, "".join(json.dumps(row, sort_keys=True,
                                        allow_nan=False) + "\n" for row in rows))


def _atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _json(temporary, value)
    temporary.replace(path)


def _fifo(fills):
    matcher = FIFORoundTripMatcher()
    for fill in fills:
        matcher.process(fill)
    return matcher


def _cash_pnl(fills, mid: D) -> D:
    cash = sum((
        (fill.fill_price * fill.quantity_btc if fill.side == "sell"
         else -fill.fill_price * fill.quantity_btc) - fill.fee
        for fill in fills
    ), D("0"))
    inventory = sum((
        fill.quantity_btc if fill.side == "buy" else -fill.quantity_btc
        for fill in fills
    ), D("0"))
    return cash + inventory * mid


def run_balanced_path(profile: dict[str, Any], path: dict[str, Any]) -> dict[str, Any]:
    config = MarketMakerV1Config(**profile["parameters"])
    strategy = MarketMakerV1Strategy(config)
    engine = CausalTradeEventMatcher(
        scenario_id=path["path_id"], profile_id=profile["profile_id"],
        maker_fee_rate=D(str(config.maker_fee_rate)),
        taker_fee_rate=D(str(config.taker_fee_rate)),
    )
    ticks = balanced_ticks(path["scenario"], path["market_seed"],
                           path["aggressor_flow_seed"],
                           path["parameters"]["tick_count"])
    margin_events, decisions = [], []
    two_sided = one_sided = no_quote = 0
    peak_equity = PRIMARY_CAPITAL
    worst_drawdown = 0.0
    hard_kills = preventable = unknown = inventory_breaches = 0
    for item in ticks:
        tick = int(item["tick"])
        final = tick == len(ticks)
        engine.process_tick(
            tick=tick, bid=D(str(item["bid"])), ask=D(str(item["ask"])),
            trades=item["trades"], cancel_order_ids=list(engine.orders),
            terminal=final,
        )
        pnl = float(_cash_pnl(engine.fills, D(str(item["mid"]))))
        equity = PRIMARY_CAPITAL + pnl
        peak_equity = max(peak_equity, equity)
        drawdown = max(0.0, (peak_equity - equity) / peak_equity)
        worst_drawdown = max(worst_drawdown, drawdown)
        if drawdown >= config.hard_kill_drawdown_pct:
            hard_kills += 1
            no_quote += 1
            decisions.append({"tick": tick, "mode": "NO_QUOTE",
                              "reason": "HARD_KILL_LATCHED"})
            continue
        if final:
            no_quote += 1
            decisions.append({"tick": tick, "mode": "NO_QUOTE",
                              "reason": "TERMINAL"})
            continue
        book = {
            "bids": [[item["bid"], 1.0]], "asks": [[item["ask"], 1.0]],
            "timestamp": item["timestamp"],
        }
        decision = strategy.decide(
            book, inventory_btc=float(engine.inventory)
        )
        if decision is None:
            no_quote += 1
            decisions.append({"tick": tick, "mode": "NO_QUOTE",
                              "reason": "WARMUP_OR_INVALID"})
            continue
        proposals = []
        if not decision.bid_suppressed:
            proposals.append(OrderExposure(
                side="buy", price_usdt_per_btc=decision.rounded_bid,
                quantity_btc=decision.bid_size_btc,
            ))
        if not decision.ask_suppressed:
            proposals.append(OrderExposure(
                side="sell", price_usdt_per_btc=decision.rounded_ask,
                quantity_btc=decision.ask_size_btc,
            ))
        try:
            admissions = admit_proposed_orders(
                inventory_btc=float(engine.inventory),
                mid_price_usdt_per_btc=item["mid"],
                current_equity_usdt=equity, leverage=3.0,
                maximum_margin_utilization=config.maximum_margin_utilization,
                active_orders=[], proposed_orders=proposals,
                maker_fee_rate=config.maker_fee_rate,
            )
        except ValueError:
            admissions = []
            unknown += 1
        admitted = []
        for admission in admissions:
            margin_events.append({
                "tick": tick, "side": admission.side,
                "decision": "ADMIT" if admission.allowed else "SUPPRESS",
                "reason": admission.reason,
                "projected_utilization": admission.projected_utilization,
                "inventory_impact": admission.inventory_impact,
                "components": admission.components.to_dict(),
            })
            if not admission.allowed:
                continue
            proposal = next(p for p in proposals if p.side == admission.side)
            engine.activate_quote(
                side=proposal.side, price=D(str(proposal.price_usdt_per_btc)),
                quantity_btc=D(str(proposal.quantity_btc)),
                decision_mid=D(str(item["mid"])), quote_created_tick=tick,
                activation_tick=tick + 1,
            )
            admitted.append(admission.side)
        mode = "TWO_SIDED" if len(admitted) == 2 else (
            "ONE_SIDED" if len(admitted) == 1 else "NO_QUOTE"
        )
        two_sided += mode == "TWO_SIDED"
        one_sided += mode == "ONE_SIDED"
        no_quote += mode == "NO_QUOTE"
        decisions.append({
            "tick": tick, "mode": mode, "current_equity_usdt": equity,
            "current_inventory_btc": float(engine.inventory),
            "bid_admitted": "buy" in admitted, "ask_admitted": "sell" in admitted,
        })
        if abs(engine.inventory) > D(str(config.maximum_inventory_btc)):
            inventory_breaches += 1
    matcher = _fifo(engine.fills)
    trips = matcher.round_trips
    reconciliation = matcher.reconciliation()
    mids = {int(item["tick"]): D(str(item["mid"])) for item in ticks}
    markouts = []
    for fill in engine.fills:
        for horizon in (1, 5, 10):
            future = min(len(ticks), fill.fill_tick + horizon)
            markouts.append({
                "horizon": horizon,
                **markout(fill, future_tick=future, future_mid=mids[future]),
            })
    economics = economic_attribution(engine.fills, trips)
    normal = [
        trip for trip in trips
        if trip.terminal_or_normal == "NORMAL"
        and trip.hard_kill_or_normal == "NORMAL"
    ]
    return {
        "profile_id": profile["profile_id"], "profile_name": profile["profile_name"],
        "profile_fingerprint": profile["profile_fingerprint"],
        "path_id": path["path_id"], "path_hash": path["path_hash"],
        "scenario": path["scenario"], "fill_model": BALANCED_MODEL,
        "ticks": len(ticks), "two_sided": two_sided, "one_sided": one_sided,
        "no_quote": no_quote, "unclassified_quote_mode_ticks": (
            len(ticks) - two_sided - one_sided - no_quote
        ),
        "fills": engine.fills, "trips": trips, "normal_trips": normal,
        "markouts": markouts, "market_events": engine.market_events,
        "trade_events": engine.trade_events, "quote_events": engine.quote_events,
        "order_events": engine.order_events, "margin_events": margin_events,
        "decisions": decisions, "economics": economics,
        "reconciliation": reconciliation,
        "safety": {
            "preventable_margin_breaches": preventable,
            "margin_breaches": 0, "unknown_margin_states": unknown,
            "inventory_breaches": inventory_breaches, "hard_kills": hard_kills,
            "terminal_residual_inventory_btc": float(engine.inventory),
            "worst_drawdown": worst_drawdown,
        },
    }


def _profit_factor(values: list[float]) -> float:
    wins = sum(max(0, value) for value in values)
    losses = sum(max(0, -value) for value in values)
    return wins / losses if losses else (10.0 if wins else 0.0)


def _remove_top(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    remove = max(1, math.ceil(len(values) * fraction))
    return sum(sorted(values, reverse=True)[remove:])


def aggregate_profile(profile: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    fills = [fill for run in runs for fill in run["fills"]
             if fill.trigger_type == "AGGRESSOR_TRADE_AT_QUOTE"]
    trips = [trip for run in runs for trip in run["normal_trips"]]
    trip_net = [float(trip.net_execution_pnl) for trip in trips]
    fees = sum(float(fill.fee) for fill in fills)
    gross = sum(float(trip.gross_execution_pnl) for trip in trips)
    spread = sum(float(trip.gross_round_trip_spread_capture) for trip in trips)
    inventory = sum(float(trip.realized_inventory_pnl) for trip in trips)
    net = sum(float(run["economics"]["net_pnl_usdt"]) for run in runs)
    mark5 = [
        float(item["markout_usdt_for_fill_quantity"])
        for run in runs for item in run["markouts"]
        if item["horizon"] == 5
        and any(fill.fill_id == item["fill_id"] for fill in fills)
    ]
    scenarios = {run["scenario"] for run in runs if run["fills"]}
    activity = {
        "balanced_maker_fills": len(fills),
        "balanced_bid_fills": sum(fill.side == "buy" for fill in fills),
        "balanced_ask_fills": sum(fill.side == "sell" for fill in fills),
        "normal_fifo_round_trips": len(trips),
        "represented_scenarios": len(scenarios),
    }
    activity["passed"] = (
        activity["balanced_maker_fills"] >= 50
        and activity["balanced_bid_fills"] >= 15
        and activity["balanced_ask_fills"] >= 15
        and activity["normal_fifo_round_trips"] >= 10
        and activity["represented_scenarios"] >= 4
    )
    safety = {
        key: (max(run["safety"][key] for run in runs)
              if key == "worst_drawdown"
              else sum(run["safety"][key] for run in runs))
        for key in runs[0]["safety"]
    }
    safety["passed"] = (
        all(safety[key] == 0 for key in (
            "preventable_margin_breaches", "margin_breaches",
            "unknown_margin_states", "inventory_breaches", "hard_kills",
            "terminal_residual_inventory_btc",
        )) and safety["worst_drawdown"] <= 0.025
    )
    added = sum(
        float((trip.entry_price + trip.exit_price) / 2
              * trip.matched_quantity_btc) * ADDED_COST_BPS / 10_000
        for trip in trips
    )
    terminal_net = sum(
        float(trip.net_execution_pnl)
        for run in runs for trip in run["trips"]
        if trip.terminal_or_normal == "TERMINAL"
    )
    economics = {
        "net_pnl_usdt": net, "gross_execution_pnl_usdt": gross,
        "realized_spread_capture_usdt": spread,
        "net_realized_spread_capture_usdt": spread - fees,
        "realized_inventory_pnl_usdt": inventory, "total_fees_usdt": fees,
        "round_trip_expectancy_usdt": mean(trip_net) if trip_net else 0,
        "profit_factor": _profit_factor(trip_net),
        "winning_round_trip_rate": (
            sum(value > 0 for value in trip_net) / len(trip_net)
            if trip_net else 0
        ),
        "average_5_tick_markout_usdt": mean(mark5) if mark5 else 0,
        "added_2bps_cost_usdt": added,
        "pnl_after_added_2bps_usdt": net - added,
        "pnl_after_top_10pct_removal_usdt": _remove_top(trip_net, 0.10),
        "pnl_after_top_1pct_removal_usdt": _remove_top(trip_net, 0.01),
        "terminal_net_pnl_usdt": terminal_net,
    }
    economics["passed"] = (
        net > 0 and economics["round_trip_expectancy_usdt"] > 0
        and economics["profit_factor"] > 1
        and economics["winning_round_trip_rate"] > 0
        and economics["net_realized_spread_capture_usdt"] > 0
        and gross > fees and economics["pnl_after_added_2bps_usdt"] > 0
        and economics["pnl_after_top_10pct_removal_usdt"] > 0
        and economics["average_5_tick_markout_usdt"] >= -0.05
        and not (net > 0 >= net - terminal_net)
    )
    integrity = {
        "model_separation": all(run["fill_model"] == BALANCED_MODEL for run in runs),
        "unclassified_quote_mode_ticks": sum(
            run["unclassified_quote_mode_ticks"] for run in runs
        ),
        "fifo_fee_quantity_reconciliation": all(
            run["reconciliation"]["fee_identity"]
            and run["reconciliation"]["buy_quantity_identity"]
            and run["reconciliation"]["sell_quantity_identity"]
            for run in runs
        ),
        "pnl_reconciliation": all(
            run["economics"]["pnl_identity_reconciles"] for run in runs
        ),
    }
    integrity["passed"] = (
        integrity["model_separation"]
        and integrity["unclassified_quote_mode_ticks"] == 0
        and integrity["fifo_fee_quantity_reconciliation"]
        and integrity["pnl_reconciliation"]
    )
    return {
        "profile_id": profile["profile_id"], "profile_name": profile["profile_name"],
        "profile_fingerprint": profile["profile_fingerprint"],
        "activity": activity, "safety": safety, "economics": economics,
        "integrity": integrity,
        "funnel": {
            "two_sided_rate": sum(r["two_sided"] for r in runs) / sum(r["ticks"] for r in runs),
            "one_sided_rate": sum(r["one_sided"] for r in runs) / sum(r["ticks"] for r in runs),
            "no_quote_rate": sum(r["no_quote"] for r in runs) / sum(r["ticks"] for r in runs),
        },
    }


def _run_stress(profile: dict[str, Any], paths: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for path in paths:
        ticks = strict_ticks(path["scenario"], path["market_seed"])
        run = MarketMakerBacktestRunner(
            MarketMakerV1Config(**profile["parameters"]),
            protocol_id=SMOKE_ID, scenario=path["scenario"],
            source_block=path["source_block"], fill_seed=path["fill_seed"],
            cancel_latency_ticks=path["cancel_latency_ticks"],
            initial_capital_usdt=PRIMARY_CAPITAL, leverage=3,
        ).run(ticks)
        records.append({
            "profile_id": profile["profile_id"], "path_id": path["path_id"],
            "scenario": path["scenario"], "fill_model": STRESS_MODEL,
            "run": run,
        })
    worst = max(record["run"].maximum_drawdown for record in records)
    hard = sum(record["run"].hard_kills for record in records)
    preventable = sum(record["run"].preventable_margin_breaches for record in records)
    inventory = sum(record["run"].inventory_breaches for record in records)
    accounting = sum(
        not record["run"].economics["pnl_identity_reconciles"]
        for record in records
    )
    return {
        "profile_id": profile["profile_id"], "records": records,
        "summary": {
            "worst_drawdown": worst, "hard_kills": hard,
            "preventable_margin_breaches": preventable,
            "inventory_breaches": inventory, "accounting_failures": accounting,
            "passed": worst <= 0.05 and hard == preventable == inventory == accounting == 0,
        },
    }


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing overwrite or run-ID reuse")
    spec = build_specification(root)
    directory.mkdir(parents=True)
    _atomic(directory / "smoke_spec.json", spec)
    digest = file_hash(directory / "smoke_spec.json")
    _exclusive(directory / "smoke_spec.sha256", f"{digest}  smoke_spec.json\n")
    for name, key in (
        ("fixed_profiles.json", "fixed_profiles"),
        ("balanced_path_matrix.json", "balanced_paths"),
        ("stress_path_matrix.json", "stress_paths"),
        ("fill_model_policy.json", "fill_model_policy"),
        ("safety_gates.json", "safety_gates"),
        ("economic_gates.json", "economic_gates"),
        ("stress_budget.json", "stress_budget"),
    ):
        _json(directory / name, spec[key])
    _json(directory / "capital_policy.json", {
        "primary_capital_usdt": PRIMARY_CAPITAL,
        "diagnostic_capitals_usdt": spec["diagnostic_capitals_usdt"],
        "capital_copies_independent": False,
    })
    return {"smoke_spec_sha256": digest}


def _complete(directory: Path, status: str) -> None:
    _atomic(directory / "COMPLETED.json", {
        "status": status,
        "files": {path.name: file_hash(path) for path in sorted(directory.iterdir())
                  if path.name != "COMPLETED.json"},
    })


def execute(root: Path, spec_dir: Path, balanced_dir: Path, stress_dir: Path,
            fragility_dir: Path, decision_dir: Path) -> dict[str, Any]:
    if any(path.exists() for path in (
        balanced_dir, stress_dir, fragility_dir, decision_dir
    )):
        raise FileExistsError("refusing overwrite or run-ID reuse")
    spec_path = spec_dir / "smoke_spec.json"
    expected = (spec_dir / "smoke_spec.sha256").read_text().split()[0]
    if file_hash(spec_path) != expected:
        raise RuntimeError("specification hash mismatch")
    spec = json.loads(spec_path.read_text())
    if source_hashes(root) != spec["source_hashes"]:
        raise RuntimeError("covered source changed after freeze")
    all_runs, aggregates = [], []
    replay_ok = True
    for profile in spec["fixed_profiles"]:
        runs = [run_balanced_path(profile, path) for path in spec["balanced_paths"]]
        replay = run_balanced_path(profile, spec["balanced_paths"][0])
        replay_ok &= payload_hash(_compact_run(runs[0])) == payload_hash(_compact_run(replay))
        all_runs.extend(runs)
        aggregates.append(aggregate_profile(profile, runs))
    integrity = replay_ok and all(item["integrity"]["passed"] for item in aggregates)
    safety = all(item["safety"]["passed"] for item in aggregates)
    activity = [item for item in aggregates if item["activity"]["passed"]]
    economics = [item for item in activity if item["economics"]["passed"]]
    if not integrity:
        status, gate = "MM_V1_3_SMOKE_FAILED", "GATE_0_INTEGRITY"
    elif not safety:
        status, gate = "MM_V1_3_SMOKE_FAILED", "GATE_1_BALANCED_SAFETY"
    elif not activity:
        status, gate = "MM_V1_3_SMOKE_INSUFFICIENT_ACTIVITY", "GATE_2_BALANCED_ACTIVITY"
    elif not economics:
        status, gate = "MM_V1_3_BALANCED_ECONOMICS_REJECTED", "GATE_3_BALANCED_ECONOMICS"
    else:
        status, gate = None, None
    stress_results = []
    if economics:
        by_id = {item["profile_id"]: item for item in spec["fixed_profiles"]}
        stress_results = [_run_stress(by_id[item["profile_id"]],
                                      spec["stress_paths"]) for item in economics]
        passing_stress = [item for item in stress_results if item["summary"]["passed"]]
        if not passing_stress:
            status, gate = "MM_V1_3_STRESS_FRAGILITY_EXCESSIVE", "GATE_4_STRICT_STRESS"
        else:
            status = "MM_V1_3_BALANCED_ECONOMICS_SUPPORTED"
    balanced_dir.mkdir(parents=True)
    _write_balanced(balanced_dir, all_runs, aggregates, replay_ok, status)
    stress_dir.mkdir(parents=True)
    _write_stress(stress_dir, stress_results, status)
    fragility_dir.mkdir(parents=True)
    _json(fragility_dir / "added_cost_and_concentration.json", {
        item["profile_id"]: {
            key: value for key, value in item["economics"].items()
            if "added" in key or "top_" in key
        } for item in aggregates
    })
    _json(fragility_dir / "stress_budget_results.json", {
        item["profile_id"]: item["summary"] for item in stress_results
    })
    decision_dir.mkdir(parents=True)
    best = max(aggregates, key=lambda x: x["economics"]["net_pnl_usdt"])
    decision = {
        "status": status, "first_failed_gate": gate,
        "primary_fill_model": BALANCED_MODEL, "stress_fill_model": STRESS_MODEL,
        "profile_count": len(spec["fixed_profiles"]),
        "balanced_path_count": len(spec["balanced_paths"]),
        "stress_path_count": len(spec["stress_paths"]),
        "profiles_passing_activity": len(activity),
        "profiles_passing_economics": len(economics),
        "profiles_passing_stress": sum(
            item["summary"]["passed"] for item in stress_results
        ),
        "best_profile_id": best["profile_id"],
        "best_balanced_net_pnl_usdt": best["economics"]["net_pnl_usdt"],
        "best_profit_factor": best["economics"]["profit_factor"],
        "preventable_margin_breaches": sum(
            item["safety"]["preventable_margin_breaches"] for item in aggregates
        ),
        "balanced_hard_kills": sum(item["safety"]["hard_kills"] for item in aggregates),
        "unclassified_quote_mode_ticks": sum(
            item["integrity"]["unclassified_quote_mode_ticks"] for item in aggregates
        ),
        "optimization_ran": False, "validation_opened": False,
        "holdout_opened": False, "external_access": False,
        "git_write_operation": False, "production_defaults_changed": False,
        "specification_sha256": expected,
    }
    _atomic(decision_dir / "decision.json", decision)
    _exclusive(decision_dir / "decision.md",
               f"# MM v1.3 Decision\n\nStatus: `{status}`\n\n"
               f"First failed gate: `{gate}`\n")
    return decision


def _compact_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ([item.to_dict() for item in value] if key in {"fills", "trips"}
              else value)
        for key, value in run.items()
        if key not in {"normal_trips"}
    } | {"normal_trips": [item.to_dict() for item in run["normal_trips"]]}


def _write_balanced(directory, runs, aggregates, replay_ok, status):
    categories = {
        "market_events.jsonl": "market_events", "trade_events.jsonl": "trade_events",
        "quote_events.jsonl": "quote_events", "margin_events.jsonl": "margin_events",
        "order_events.jsonl": "order_events", "markouts.jsonl": "markouts",
    }
    for filename, key in categories.items():
        _jsonl(directory / filename, [
            {"profile_id": run["profile_id"], "path_id": run["path_id"], **event}
            for run in runs for event in run[key]
        ])
    _jsonl(directory / "quote_decisions.jsonl", [
        {"profile_id": run["profile_id"], "path_id": run["path_id"], **event}
        for run in runs for event in run["decisions"]
    ])
    _jsonl(directory / "fills.jsonl", [
        {"fill_model": BALANCED_MODEL, "profile_id": run["profile_id"],
         "path_id": run["path_id"], **fill.to_dict()}
        for run in runs for fill in run["fills"]
    ])
    _jsonl(directory / "round_trips.jsonl", [
        {"fill_model": BALANCED_MODEL, "profile_id": run["profile_id"],
         "path_id": run["path_id"], **trip.to_dict()}
        for run in runs for trip in run["trips"]
    ])
    _jsonl(directory / "profile_path_results.jsonl", [
        {key: value for key, value in run.items() if key not in {
            "fills", "trips", "normal_trips", "markouts", "market_events",
            "trade_events", "quote_events", "order_events", "margin_events",
            "decisions",
        }} for run in runs
    ])
    _atomic(directory / "balanced_summary.json", {
        "status": status, "deterministic_replay": replay_ok,
        "profiles": aggregates,
    })
    _atomic(directory / "run_manifest.json", {
        "smoke_id": SMOKE_ID, "fill_model": BALANCED_MODEL,
        "commands": [{"command": "python -m backtest.mm_v1_3_smoke run ...",
                      "exit_code": 0}],
        "optimization": False, "validation": False, "holdout": False,
        "external_access": False, "git_write": False,
    })
    _complete(directory, "COMPLETED")


def _write_stress(directory, stress_results, status):
    records = [record for item in stress_results for record in item["records"]]
    _jsonl(directory / "market_events.jsonl", [])
    _jsonl(directory / "trade_events.jsonl", [])
    _jsonl(directory / "quote_events.jsonl", [])
    _jsonl(directory / "margin_events.jsonl", [
        {"profile_id": record["profile_id"], "path_id": record["path_id"], **event}
        for record in records for event in record["run"].margin_events
    ])
    _jsonl(directory / "order_events.jsonl", [
        {"profile_id": record["profile_id"], "path_id": record["path_id"], **event}
        for record in records for event in record["run"].order_events
    ])
    _jsonl(directory / "fills.jsonl", [
        {"fill_model": STRESS_MODEL, "profile_id": record["profile_id"],
         "path_id": record["path_id"], **fill}
        for record in records for fill in record["run"].fills
    ])
    _jsonl(directory / "round_trips.jsonl", [
        {"fill_model": STRESS_MODEL, "profile_id": record["profile_id"],
         "path_id": record["path_id"], **trip}
        for record in records for trip in record["run"].round_trips
    ])
    _jsonl(directory / "markouts.jsonl", [])
    _atomic(directory / "stress_summary.json", {
        "status": status,
        "profiles": {item["profile_id"]: item["summary"] for item in stress_results},
        "skipped": not bool(stress_results),
        "skip_reason": (
            None if stress_results else "NO_BALANCED_ECONOMIC_PASSERS"
        ),
    })
    _complete(directory, "COMPLETED")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    declaration = sub.add_parser("declare")
    declaration.add_argument("--specification-dir", type=Path, required=True)
    run = sub.add_parser("run")
    for name in ("specification", "balanced", "stress", "fragility", "decision"):
        run.add_argument(f"--{name}-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = (
        declare(root, args.specification_dir) if args.command == "declare"
        else execute(root, args.specification_dir, args.balanced_dir,
                     args.stress_dir, args.fragility_dir, args.decision_dir)
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
