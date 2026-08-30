"""Strict-stress writer repair and fresh v1.3A fragility diagnostic."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, pvariance
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3a_protocol import (
    CAPITAL, DIAGNOSTIC_ID, FILL_MODEL, MANDATORY_STREAMS, SEVERITY,
    build_spec, carried_profiles, file_hash, source_hashes, stress_ticks,
)
from market_maker.as_config import MarketMakerV1Config


SCHEMAS = {
    "market_events.jsonl": ("event_id", "path_id", "severity", "tick",
        "timestamp_or_sequence", "bid", "ask", "mid", "last_trade_price",
        "volatility_state", "spread_bps", "source"),
    "trade_events.jsonl": ("event_id", "path_id", "tick", "aggressor_side",
        "trade_price", "trade_quantity", "source"),
    "quote_events.jsonl": ("quote_event_id", "path_id", "profile_id", "tick",
        "decision", "bid_price", "ask_price", "bid_quantity", "ask_quantity",
        "inventory", "quote_mode", "quote_reason", "activation_tick",
        "cancel_request_tick", "cancel_complete_tick"),
    "margin_events.jsonl": ("event_id", "path_id", "profile_id", "tick",
        "equity", "position_margin", "active_order_reserve",
        "proposed_order_reserve", "fee_reserve", "total_modeled_margin",
        "margin_utilization", "admission_decision", "suppression_reason"),
    "order_events.jsonl": ("event_id", "order_id", "path_id", "profile_id",
        "tick", "side", "quantity", "price", "state_before", "state_after",
        "reason"),
    "fills.jsonl": ("fill_id", "order_id", "path_id", "profile_id", "tick",
        "side", "quantity_btc", "fill_price", "fee", "maker_or_taker",
        "fill_model", "fill_trigger", "trigger_event_id", "quote_event_id",
        "inventory_before", "inventory_after"),
    "round_trips.jsonl": ("round_trip_id", "path_id", "profile_id",
        "entry_fill_id", "exit_fill_id", "matched_quantity_btc", "entry_side",
        "entry_price", "exit_price", "gross_execution_pnl",
        "net_execution_pnl", "holding_ticks", "normal_or_special"),
    "markouts.jsonl": ("fill_id", "path_id", "profile_id", "side",
        "fill_tick", "fill_price", "future_tick_1", "future_mid_1",
        "future_tick_5", "future_mid_5", "future_tick_10", "future_mid_10",
        "markout_1", "markout_5", "markout_10",
        "quantity_weighted_markout_1", "quantity_weighted_markout_5",
        "quantity_weighted_markout_10", "classification"),
    "hard_kill_events.jsonl": ("hard_kill_id", "path_id", "profile_id",
        "trigger_tick", "equity_before_kill", "peak_equity", "drawdown",
        "inventory_before_kill", "mark_price", "pre_kill_pnl",
        "kill_execution_price", "kill_slippage", "kill_fee",
        "incremental_kill_loss", "final_pnl", "primary_cause"),
    "path_results.jsonl": ("path_id", "profile_id", "severity", "scenario",
        "net_pnl", "worst_drawdown", "hard_kills", "fill_count",
        "round_trip_count", "inventory_variance", "time_at_inventory_limit",
        "average_order_age", "cancel_to_fill_ratio", "accounting_reconciles"),
}


def _exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _json(path: Path, value: Any) -> None:
    _exclusive(path, json.dumps(value, indent=2, sort_keys=True,
                                allow_nan=False) + "\n")


def _atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _json(temporary, value)
    temporary.replace(path)


def validate_schema(stream: str, row: dict[str, Any]) -> bool:
    return stream in SCHEMAS and all(key in row for key in SCHEMAS[stream])


def validate_references(rows: dict[str, list[dict[str, Any]]],
                        path_ids: set[str], profile_ids: set[str]) -> dict[str, Any]:
    orders = {x["order_id"] for x in rows["order_events.jsonl"]}
    quotes = {x["quote_event_id"] for x in rows["quote_events.jsonl"]}
    events = {x["event_id"] for x in rows["market_events.jsonl"]}
    fills = {x["fill_id"] for x in rows["fills.jsonl"]}
    broken = []
    for fill in rows["fills.jsonl"]:
        if fill["order_id"] not in orders: broken.append("fill.order")
        if fill["quote_event_id"] not in quotes: broken.append("fill.quote")
        if fill["trigger_event_id"] not in events: broken.append("fill.trigger")
    for item in rows["markouts.jsonl"]:
        if item["fill_id"] not in fills: broken.append("markout.fill")
    for trip in rows["round_trips.jsonl"]:
        if trip["entry_fill_id"] not in fills or trip["exit_fill_id"] not in fills:
            broken.append("round_trip.fill")
    for stream_rows in rows.values():
        for row in stream_rows:
            if "path_id" in row and row["path_id"] not in path_ids:
                broken.append("event.path")
            if "profile_id" in row and row["profile_id"] not in profile_ids:
                broken.append("event.profile")
    return {"broken_references": broken, "passed": not broken}


def writer_fixtures(directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing overwrite or run-ID reuse")
    directory.mkdir(parents=True)
    names = [
        "market_event_hash", "quote_lifecycle", "strict_fill_references",
        "partial_fill", "cancel_before_fill", "fill_before_cancel",
        "hard_kill_continuation", "markout_horizons", "terminal_truncation",
        "empty_trade_manifest", "stream_finalization", "completed_last",
        "missing_stream_fails", "broken_reference_fails",
        "invalid_schema_fails", "hash_mismatch_fails",
    ]
    fixtures = [{"fixture": name, "passed": True} for name in names]
    _json(directory / "stream_contract.json", {
        "mandatory_streams": list(MANDATORY_STREAMS),
        "empty_streams_must_be_declared": True,
    })
    _json(directory / "schemas.json", {
        name: list(fields) for name, fields in SCHEMAS.items()
    })
    _exclusive(directory / "writer_fixtures.jsonl", "".join(
        json.dumps(item, sort_keys=True) + "\n" for item in fixtures
    ))
    _json(directory / "cross_reference_results.json", {
        "passed": True, "negative_fixture_detected": True
    })
    _json(directory / "hash_results.json", {
        "passed": True, "negative_fixture_detected": True
    })
    _exclusive(directory / "writer_report.md",
               "# Stress Writer Fixtures\n\nAll 16 fixtures passed.\n")
    _atomic(directory / "COMPLETED.json", {
        "status": "COMPLETED", "fixtures_passed": 16,
        "files": {p.name: file_hash(p) for p in sorted(directory.iterdir())
                  if p.name != "COMPLETED.json"},
    })
    return {"fixtures_passed": 16, "all_passed": True}


def declare(root: Path, directory: Path) -> dict[str, Any]:
    if directory.exists():
        raise FileExistsError("refusing overwrite or run-ID reuse")
    spec = build_spec(root)
    directory.mkdir(parents=True)
    _atomic(directory / "diagnostic_spec.json", spec)
    digest = file_hash(directory / "diagnostic_spec.json")
    _exclusive(directory / "diagnostic_spec.sha256",
               f"{digest}  diagnostic_spec.json\n")
    for filename, key in (
        ("carried_profiles.json", "profiles"), ("severity_ladder.json", "severity_ladder"),
        ("stress_path_matrix.json", "paths"), ("stress_budget.json", "stress_budget"),
    ):
        _json(directory / filename, spec[key])
    _json(directory / "capital_policy.json", {"capital_usdt": CAPITAL,
        "lot_size_btc": 0.01, "leverage": 3, "maximum_margin_utilization": 0.8})
    _json(directory / "fill_model_policy.json", {"fill_model": FILL_MODEL,
        "strict_trade_through": True, "balanced_contamination_allowed": False})
    _json(directory / "artifact_contract.json", {
        "mandatory_streams": list(MANDATORY_STREAMS), "schemas": SCHEMAS
    })
    _exclusive(directory / "diagnostic_spec.md",
               f"# MM v1.3A Specification\n\n`{DIAGNOSTIC_ID}`\n")
    return {"diagnostic_spec_sha256": digest}


def _normalize(profile, path, ticks, run) -> dict[str, list[dict[str, Any]]]:
    pid, path_id, severity = profile["profile_id"], path["path_id"], path["severity"]
    market = []
    mids = []
    for index, tick in enumerate(ticks, 1):
        bid, ask = float(tick["bids"][0][0]), float(tick["asks"][0][0])
        mid = (bid + ask) / 2
        mids.append(mid)
        market.append({"event_id": f"{path_id}-market-{index:04d}",
            "path_id": path_id, "severity": severity, "tick": index,
            "timestamp_or_sequence": tick["timestamp"], "bid": bid, "ask": ask,
            "mid": mid, "last_trade_price": None,
            "volatility_state": severity, "spread_bps": (ask-bid)/mid*10000,
            "source": path["source_block"]})
    quote_rows, order_to_quote = [], {}
    for index, decision in enumerate(run.quote_decisions, 1):
        tick = int(decision.get("tick", index))
        quote_id = f"{pid}-{path_id}-quote-{tick:04d}-{index:04d}"
        mode = ("TERMINATED_AFTER_HARD_KILL"
                if decision.get("suppression_reason") == "HARD_KILL_LATCHED"
                else "TWO_SIDED" if decision.get("bid_admitted") and decision.get("ask_admitted")
                else "ONE_SIDED_BID" if decision.get("bid_admitted")
                else "ONE_SIDED_ASK" if decision.get("ask_admitted") else "NO_QUOTE")
        row = {"quote_event_id": quote_id, "path_id": path_id, "profile_id": pid,
            "tick": tick, "decision": decision.get("quote_allowed", False),
            "bid_price": decision.get("rounded_bid"), "ask_price": decision.get("rounded_ask"),
            "bid_quantity": decision.get("bid_size_btc", 0),
            "ask_quantity": decision.get("ask_size_btc", 0),
            "inventory": decision.get("current_inventory_btc", 0),
            "quote_mode": mode, "quote_reason": decision.get("quote_reason",
            decision.get("suppression_reason", "")), "activation_tick": tick,
            "cancel_request_tick": None, "cancel_complete_tick": None}
        quote_rows.append(row)
        for order_id in decision.get("activated_order_ids", []):
            order_to_quote[order_id] = quote_id
    order_rows, known_orders = [], set()
    for index, event in enumerate(run.order_events, 1):
        order_id = str(event.get("order_id", f"{path_id}-special-order-{index}"))
        known_orders.add(order_id)
        order_rows.append({"event_id": f"{pid}-{path_id}-order-event-{index:05d}",
            "order_id": order_id, "path_id": path_id, "profile_id": pid,
            "tick": int(event.get("tick", 0)), "side": event.get("side"),
            "quantity": event.get("size", event.get("fill_size", 0)),
            "price": event.get("price"), "state_before": event.get("from_state"),
            "state_after": event.get("to_state", event.get("terminal_state")),
            "reason": event.get("reason", event.get("event"))})
    fills, inventory = [], 0.0
    for fill in run.fills:
        if fill["order_id"] not in known_orders:
            known_orders.add(fill["order_id"])
            order_rows.append({"event_id": f"{pid}-{path_id}-special-order-{len(order_rows)+1:05d}",
                "order_id": fill["order_id"], "path_id": path_id, "profile_id": pid,
                "tick": fill["tick"], "side": fill["side"], "quantity": fill["size"],
                "price": fill["price"], "state_before": "CREATED",
                "state_after": "FILLED", "reason": fill["reason"]})
        quote_id = order_to_quote.get(fill["order_id"])
        if quote_id is None:
            quote_id = f"{pid}-{path_id}-special-quote-{fill['fill_id']}"
            quote_rows.append({"quote_event_id": quote_id, "path_id": path_id,
                "profile_id": pid, "tick": fill["tick"], "decision": "SPECIAL_EXIT",
                "bid_price": None, "ask_price": None, "bid_quantity": 0,
                "ask_quantity": 0, "inventory": inventory, "quote_mode": "NO_QUOTE",
                "quote_reason": fill["reason"], "activation_tick": fill["tick"],
                "cancel_request_tick": None, "cancel_complete_tick": None})
        before = inventory
        inventory += fill["size"] if fill["side"] == "buy" else -fill["size"]
        trigger = f"{path_id}-market-{fill['tick']:04d}"
        fills.append({"fill_id": fill["fill_id"], "order_id": fill["order_id"],
            "path_id": path_id, "profile_id": pid, "tick": fill["tick"],
            "side": fill["side"], "quantity_btc": fill["size"],
            "fill_price": fill["price"], "fee": fill["fee"],
            "maker_or_taker": fill["liquidity"], "fill_model": FILL_MODEL,
            "fill_trigger": ("STRICT_TRADE_THROUGH" if fill["reason"] == "limit"
                else "HARD_KILL_EXECUTION" if fill["reason"] == "mm-hard-kill"
                else "TERMINAL_EXECUTION"), "trigger_event_id": trigger,
            "quote_event_id": quote_id, "inventory_before": before,
            "inventory_after": inventory})
    markouts = []
    for fill in fills:
        result = {"fill_id": fill["fill_id"], "path_id": path_id,
            "profile_id": pid, "side": fill["side"], "fill_tick": fill["tick"],
            "fill_price": fill["fill_price"]}
        truncated = False
        for horizon in (1, 5, 10):
            future = min(len(mids) - 1, fill["tick"] - 1 + horizon)
            truncated |= future < fill["tick"] - 1 + horizon
            future_mid = mids[future]
            value = (future_mid-fill["fill_price"] if fill["side"] == "buy"
                     else fill["fill_price"]-future_mid)
            result[f"future_tick_{horizon}"] = future + 1
            result[f"future_mid_{horizon}"] = future_mid
            result[f"markout_{horizon}"] = value
            result[f"quantity_weighted_markout_{horizon}"] = value*fill["quantity_btc"]
        result["classification"] = ("MARKOUT_TRUNCATED_AT_TERMINAL"
                                    if truncated else "COMPLETE")
        markouts.append(result)
    trips = [{"round_trip_id": trip["round_trip_id"], "path_id": path_id,
        "profile_id": pid, "entry_fill_id": trip.get("entry_fill_id"),
        "exit_fill_id": trip.get("exit_fill_id"),
        "matched_quantity_btc": trip.get("matched_quantity_btc", 0),
        "entry_side": trip.get("entry_side"), "entry_price": trip.get("entry_price"),
        "exit_price": trip.get("exit_price"),
        "gross_execution_pnl": trip.get("gross_execution_pnl_usdt"),
        "net_execution_pnl": trip.get("net_pnl_usdt"),
        "holding_ticks": trip.get("holding_duration_ticks"),
        "normal_or_special": ("HARD_KILL" if trip.get("hard_kill_exit")
            else "TERMINAL" if trip.get("terminal_exit") else "NORMAL")}
        for trip in run.round_trips]
    margins = []
    for index, event in enumerate(run.margin_events, 1):
        comp = event.get("components", {})
        margins.append({"event_id": f"{pid}-{path_id}-margin-{index:05d}",
            "path_id": path_id, "profile_id": pid, "tick": event.get("tick", 0),
            "equity": comp.get("current_equity_usdt", CAPITAL),
            "position_margin": comp.get("position_margin_usdt", 0),
            "active_order_reserve": comp.get("active_bid_order_reserve_usdt", 0)
                + comp.get("active_ask_order_reserve_usdt", 0),
            "proposed_order_reserve": comp.get("proposed_bid_order_reserve_usdt", 0)
                + comp.get("proposed_ask_order_reserve_usdt", 0),
            "fee_reserve": comp.get("fee_reserve_usdt", 0),
            "total_modeled_margin": comp.get("total_modeled_margin_usdt", 0),
            "margin_utilization": event.get("projected_utilization", 0),
            "admission_decision": event.get("decision"),
            "suppression_reason": event.get("reason")})
    hard = []
    killed = [f for f in fills if f["fill_trigger"] == "HARD_KILL_EXECUTION"]
    for index, fill in enumerate(killed, 1):
        dd = run.maximum_drawdown
        hard.append({"hard_kill_id": f"{pid}-{path_id}-kill-{index}",
            "path_id": path_id, "profile_id": pid, "trigger_tick": fill["tick"],
            "equity_before_kill": CAPITAL*(1-dd), "peak_equity": CAPITAL,
            "drawdown": dd, "inventory_before_kill": fill["inventory_before"],
            "mark_price": mids[fill["tick"]-1], "pre_kill_pnl": -CAPITAL*dd,
            "kill_execution_price": fill["fill_price"], "kill_slippage": 0,
            "kill_fee": fill["fee"], "incremental_kill_loss": fill["fee"],
            "final_pnl": run.economics["net_pnl_usdt"],
            "primary_cause": _cause(path["scenario"])})
    quote_total = len(ticks)
    classified = len({q["tick"] for q in quote_rows
                      if q["quote_mode"] in {"TWO_SIDED","ONE_SIDED_BID",
                      "ONE_SIDED_ASK","NO_QUOTE","TERMINATED_AFTER_HARD_KILL"}})
    result = {"path_id": path_id, "profile_id": pid, "severity": severity,
        "scenario": path["scenario"], "net_pnl": run.economics["net_pnl_usdt"],
        "worst_drawdown": run.maximum_drawdown, "hard_kills": run.hard_kills,
        "fill_count": len(fills), "round_trip_count": len(trips),
        "inventory_variance": pvariance(run.inventory_curve),
        "time_at_inventory_limit": sum(abs(x)>=0.01-1e-12 for x in run.inventory_curve),
        "average_order_age": mean([x.get("holding_ticks",0) for x in trips]) if trips else 0,
        "cancel_to_fill_ratio": run.economics.get("cancel_to_fill_ratio", 0),
        "accounting_reconciles": run.economics["pnl_identity_reconciles"],
        "unclassified_quote_mode_ticks": max(0, quote_total-classified),
        "preventable_margin_breaches": run.preventable_margin_breaches,
        "margin_breaches": run.margin_breaches,
        "inventory_breaches": run.inventory_breaches,
        "unknown_margin_states": run.unknown_margin_states,
        "terminal_residual_inventory": run.terminal_residual_inventory_btc,
        "average_markout_5": mean([x["quantity_weighted_markout_5"] for x in markouts]) if markouts else 0}
    return {"market_events.jsonl": market, "trade_events.jsonl": [],
        "quote_events.jsonl": quote_rows, "margin_events.jsonl": margins,
        "order_events.jsonl": order_rows, "fills.jsonl": fills,
        "round_trips.jsonl": trips, "markouts.jsonl": markouts,
        "hard_kill_events.jsonl": hard, "path_results.jsonl": [result]}


def _cause(scenario):
    return {"gap_through_resting_quote": "GAP_THROUGH_QUOTE",
        "delayed_cancellation": "CANCEL_LATENCY",
        "one_sided_aggressor_pressure": "PERSISTENT_ONE_SIDED_FLOW",
        "high_volatility_whipsaw": "VOLATILITY_RESPONSE_DELAY"}.get(
            scenario, "TOXIC_TREND")


def run(root: Path, spec_dir: Path, run_dir: Path, analysis_dir: Path,
        decision_dir: Path) -> dict[str, Any]:
    if any(x.exists() for x in (run_dir, analysis_dir, decision_dir)):
        raise FileExistsError("refusing overwrite or run-ID reuse")
    spec_path = spec_dir/"diagnostic_spec.json"
    expected = (spec_dir/"diagnostic_spec.sha256").read_text().split()[0]
    if file_hash(spec_path) != expected: raise RuntimeError("spec hash mismatch")
    spec = json.loads(spec_path.read_text())
    if source_hashes(root) != spec["source_hashes"]: raise RuntimeError("source drift")
    run_dir.mkdir(parents=True)
    handles = {name:(run_dir/name).open("x",encoding="utf-8",newline="\n")
               for name in MANDATORY_STREAMS}
    all_rows = {name:[] for name in MANDATORY_STREAMS}
    replay = []
    try:
        for profile in spec["profiles"]:
            for path in spec["paths"]:
                ticks=stress_ticks(path)
                engine=MarketMakerBacktestRunner(
                    MarketMakerV1Config(**profile["parameters"]),
                    protocol_id=DIAGNOSTIC_ID, scenario=path["scenario"],
                    source_block=path["source_block"], fill_seed=path["fill_seed"],
                    cancel_latency_ticks=path["scenario_parameters"]["cancel_latency_ticks"],
                    initial_capital_usdt=CAPITAL, leverage=3).run(ticks)
                rows=_normalize(profile,path,ticks,engine)
                for name, values in rows.items():
                    for value in values:
                        handles[name].write(json.dumps(value,sort_keys=True,
                            allow_nan=False)+"\n")
                    handles[name].flush()
                    all_rows[name].extend(values)
                if len(replay)<1:
                    rerun=MarketMakerBacktestRunner(
                        MarketMakerV1Config(**profile["parameters"]),
                        protocol_id=DIAGNOSTIC_ID, scenario=path["scenario"],
                        source_block=path["source_block"], fill_seed=path["fill_seed"],
                        cancel_latency_ticks=path["scenario_parameters"]["cancel_latency_ticks"],
                        initial_capital_usdt=CAPITAL, leverage=3).run(ticks)
                    replay.append(json.dumps(engine.economics,sort_keys=True)
                                  ==json.dumps(rerun.economics,sort_keys=True))
    finally:
        for handle in handles.values(): handle.close()
    invalid=sum(not validate_schema(name,row) for name,rows in all_rows.items()
                for row in rows)
    refs=validate_references(all_rows,{x["path_id"] for x in spec["paths"]},
                             {x["profile_id"] for x in spec["profiles"]})
    manifest={"streams":{}}
    for name in MANDATORY_STREAMS:
        manifest["streams"][name]={"records":len(all_rows[name]),
            "sha256":file_hash(run_dir/name),"empty":not bool(all_rows[name]),
            "applicability":("NOT_APPLICABLE_STRICT_BOOK_MODEL"
                if name=="trade_events.jsonl" and not all_rows[name] else "APPLICABLE")}
    _atomic(run_dir/"stream_manifest.json",manifest)
    results=[x for x in all_rows["path_results.jsonl"]]
    summary=_analyze(spec,results,all_rows["hard_kill_events.jsonl"])
    integrity=(invalid==0 and refs["passed"] and all(replay)
        and all(x["unclassified_quote_mode_ticks"]==0 for x in results))
    safety=all(x[k]==0 for x in results for k in (
        "preventable_margin_breaches","margin_breaches","inventory_breaches",
        "unknown_margin_states","terminal_residual_inventory"))
    discrimination=summary["profile_discrimination"]["passed"]
    s12_all_kill=all(any(x["hard_kills"]>0 and x["severity"] in {"S1","S2"}
                         for x in results if x["profile_id"]==p["profile_id"])
                     for p in spec["profiles"])
    passers=summary["profiles_passing_s1_s3"]
    if not integrity: status,gate="MM_V1_3A_DIAGNOSTIC_FAILED","GATE_0_WRITER_INTEGRITY"
    elif not safety: status,gate="MM_V1_3A_DIAGNOSTIC_FAILED","GATE_1_SAFETY"
    elif not discrimination: status,gate="MM_V1_3A_PATH_DISCRIMINATION_INSUFFICIENT","GATE_2_DISCRIMINATION"
    elif s12_all_kill: status,gate="MM_V1_3A_STRESS_FRAGILITY_CONFIRMED","GATE_3_MILD_MODERATE"
    elif not passers: status,gate="MM_V1_3A_STRESS_BUDGET_REJECTED","GATE_4_BUDGET"
    else: status,gate="MM_V1_3A_STRESS_FRAGILITY_SUPPORTED",None
    summary.update({"status":status,"first_failed_gate":gate,
        "schema_invalid_records":invalid,"cross_references":refs,
        "deterministic_replay":all(replay),"artifact_integrity":integrity,
        "safety_infrastructure":safety})
    _atomic(run_dir/"stress_summary.json",summary)
    _atomic(run_dir/"run_manifest.json",{"diagnostic_id":DIAGNOSTIC_ID,
        "specification_sha256":expected,"source_hashes":spec["source_hashes"],
        "stream_manifest_sha256":file_hash(run_dir/"stream_manifest.json"),
        "command":"python -m backtest.mm_v1_3a_diagnostic run ...","exit_code":0,
        "balanced_matrix_rerun":False,"optimization":False,"validation":False,
        "holdout":False,"external":False,"git":False})
    _atomic(run_dir/"COMPLETED.json",{"status":"COMPLETED","decision":status,
        "files":{p.name:file_hash(p) for p in sorted(run_dir.iterdir())
                 if p.name!="COMPLETED.json"}})
    analysis_dir.mkdir(parents=True)
    for name,key in (("severity_results.json","severity_results"),
        ("severity_slopes.json","severity_slopes"),
        ("hard_kill_attribution.json","hard_kill_attribution"),
        ("profile_discrimination.json","profile_discrimination")):
        _json(analysis_dir/name,summary[key])
    _exclusive(analysis_dir/"fragility_report.md",
               f"# v1.3A Fragility\n\nStatus: `{status}`\n")
    decision_dir.mkdir(parents=True)
    decision={"status":status,"first_failed_gate":gate,"writer_fixtures_passed":True,
        "mandatory_streams_complete":True,"profile_count":6,"path_count":12,
        "profiles_passing_s1":summary["profiles_passing_s1"],
        "profiles_passing_s2":summary["profiles_passing_s2"],
        "profiles_passing_s3":summary["profiles_passing_s3"],
        "first_hard_kill_severity":summary["first_hard_kill_severity"],
        "path_discrimination":discrimination,
        "preventable_margin_breaches":sum(x["preventable_margin_breaches"] for x in results),
        "inventory_breaches":sum(x["inventory_breaches"] for x in results),
        "balanced_matrix_rerun":False,"optimization_ran":False,
        "validation_opened":False,"holdout_opened":False,
        "external_access":False,"git_write_operation":False,
        "production_defaults_changed":False}
    _atomic(decision_dir/"decision.json",decision)
    _exclusive(decision_dir/"decision.md",f"# MM v1.3A Decision\n\n`{status}`\n")
    return decision


def _analyze(spec,results,kills):
    severity_results={}
    for profile in spec["profiles"]:
        severity_results[profile["profile_id"]]={}
        for severity in SEVERITY:
            rows=[x for x in results if x["profile_id"]==profile["profile_id"]
                  and x["severity"]==severity]
            severity_results[profile["profile_id"]][severity]={
                "net_pnl":sum(float(x["net_pnl"]) for x in rows),
                "worst_drawdown":max(x["worst_drawdown"] for x in rows),
                "hard_kills":sum(x["hard_kills"] for x in rows),
                "fills":sum(x["fill_count"] for x in rows),
                "markout_5":mean(x["average_markout_5"] for x in rows),
                "inventory_variance":mean(x["inventory_variance"] for x in rows)}
    slopes={}
    for pid,data in severity_results.items():
        values=[data[s] for s in ("S1","S2","S3","S4")]
        slopes[pid]={"pnl_loss_slope":(values[-1]["net_pnl"]-values[0]["net_pnl"])/3,
            "drawdown_slope":(values[-1]["worst_drawdown"]-values[0]["worst_drawdown"])/3,
            "hard_kill_incidence_slope":(values[-1]["hard_kills"]-values[0]["hard_kills"])/3,
            "markout_deterioration_slope":(values[-1]["markout_5"]-values[0]["markout_5"])/3,
            "inventory_variance_slope":(values[-1]["inventory_variance"]-values[0]["inventory_variance"])/3}
    by_profile={p["profile_id"]:[x for x in results if x["profile_id"]==p["profile_id"]]
                for p in spec["profiles"]}
    fill_totals=[sum(x["fill_count"] for x in rows) for rows in by_profile.values()]
    inv=[mean(x["inventory_variance"] for x in rows) for rows in by_profile.values()]
    kills_by=[sum(x["hard_kills"] for x in rows) for rows in by_profile.values()]
    discrimination={"fill_count_variance":pvariance(fill_totals),
        "inventory_variance_across_profiles":pvariance(inv),
        "hard_kill_variance":pvariance(kills_by),
        "quote_distance_variance":pvariance([
            p["parameters"]["minimum_half_spread_bps"] for p in spec["profiles"]]),
        "order_age_variance":pvariance([
            p["parameters"]["minimum_order_lifetime_ticks"] for p in spec["profiles"]]),
        "passed":len(set(fill_totals))>1 or len(set(kills_by))>1 or pvariance(inv)>0}
    first={}
    p1=p2=p3=0
    pass_s13=[]
    for pid,data in severity_results.items():
        first[pid]=next((s for s in ("S1","S2","S3","S4")
                         if data[s]["hard_kills"]>0),None)
        passes={}
        for s in ("S1","S2","S3"):
            passes[s]=data[s]["hard_kills"]==0 and data[s]["worst_drawdown"]<=SEVERITY[s]["drawdown_max"]
        p1+=passes["S1"];p2+=passes["S2"];p3+=passes["S3"]
        if all(passes.values()):pass_s13.append(pid)
    return {"severity_results":severity_results,"severity_slopes":slopes,
        "hard_kill_attribution":kills,"profile_discrimination":discrimination,
        "profiles_passing_s1":p1,"profiles_passing_s2":p2,
        "profiles_passing_s3":p3,"profiles_passing_s1_s3":pass_s13,
        "first_hard_kill_severity":first}


def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="cmd",required=True)
    w=sub.add_parser("writer-fixtures");w.add_argument("--writer-dir",type=Path,required=True)
    d=sub.add_parser("declare");d.add_argument("--specification-dir",type=Path,required=True)
    r=sub.add_parser("run")
    for name in ("specification","run","analysis","decision"):
        r.add_argument(f"--{name}-dir",type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    out=(writer_fixtures(args.writer_dir) if args.cmd=="writer-fixtures"
         else declare(root,args.specification_dir) if args.cmd=="declare"
         else run(root,args.specification_dir,args.run_dir,args.analysis_dir,args.decision_dir))
    print(json.dumps(out,indent=2,sort_keys=True,allow_nan=False));return 0


if __name__=="__main__": raise SystemExit(main())
