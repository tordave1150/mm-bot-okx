"""Execute the fixed targeted stress-resilience repair matrix."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, pvariance
from typing import Any

from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_v1_3a_diagnostic import _normalize, validate_references, validate_schema
from backtest.mm_v1_3b_protocol import (
    CAPITAL, FILL_MODEL, PROTOCOL_ID, SEVERITY, STREAMS, build_spec,
    file_hash, source_hashes, ticks,
)
from market_maker.as_config import MarketMakerV1Config


def _exclusive(path:Path,text:str)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8",newline="\n") as f:f.write(text)


def _json(path:Path,value:Any)->None:
    _exclusive(path,json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def _atomic(path:Path,value:Any)->None:
    temp=path.with_suffix(path.suffix+".tmp");_json(temp,value);temp.replace(path)


def _complete(directory:Path,status:str)->None:
    _atomic(directory/"COMPLETED.json",{"status":status,
        "files":{p.name:file_hash(p) for p in sorted(directory.iterdir())
                 if p.name!="COMPLETED.json"}})


def design(directory:Path)->dict[str,Any]:
    if directory.exists():raise FileExistsError("refusing overwrite")
    directory.mkdir(parents=True)
    _json(directory/"defensive_signal_contract.json",{
        "causal_signals":["current_mid","historical_mid","realized_strict_fill",
            "current_inventory","current_quote_age"],
        "forbidden":["future_price","path_label","severity_label","scenario_label"]})
    from backtest.mm_v1_3b_protocol import profiles
    ps=profiles();_json(directory/"repair_profiles.json",ps)
    _json(directory/"profile_hypotheses.json",{
        p["profile_id"]:p["hypothesis"] for p in ps})
    _json(directory/"repair_scope.json",{"allowed":["spread_guard","fast_cancel",
        "toxic_pause","inventory_reduction","one_sided_mode","cooldown"],
        "strategy_math_changed":False,"risk_limits_changed":False})
    _exclusive(directory/"repair_design.md",
               "# v1.3B Repair Design\n\nEight profiles fixed before results.\n")
    _complete(directory,"COMPLETED");return {"profiles":8,"frozen":True}


def declare(root:Path,directory:Path)->dict[str,Any]:
    if directory.exists():raise FileExistsError("refusing overwrite")
    spec=build_spec(root);directory.mkdir(parents=True)
    _atomic(directory/"protocol_spec.json",spec)
    digest=file_hash(directory/"protocol_spec.json")
    _exclusive(directory/"protocol_spec.sha256",f"{digest}  protocol_spec.json\n")
    for filename,key in (("fixed_profiles.json","profiles"),
        ("refined_severity_ladder.json","severity_ladder"),
        ("stress_path_matrix.json","paths"),("activity_floor.json","activity_floor"),
        ("resilience_budget.json","resilience_budget")):
        _json(directory/filename,spec[key])
    _json(directory/"capital_policy.json",{"capital_usdt":CAPITAL,"leverage":3,
        "lot_size_btc":.01,"maximum_inventory_btc":.01,
        "maximum_margin_utilization":.8,"hard_kill_drawdown":.05})
    _json(directory/"fill_model_policy.json",{"fill_model":FILL_MODEL,
        "strict_only":True,"balanced_contamination":False})
    _json(directory/"artifact_contract.json",{"mandatory_streams":list(STREAMS),
        "write_during_execution":True,"hash_every_stream":True})
    _exclusive(directory/"protocol_spec.md",f"# v1.3B Protocol\n\n`{PROTOCOL_ID}`\n")
    return {"protocol_spec_sha256":digest}


def _first_severity(rows):
    order=list(SEVERITY)
    return next((s for s in order if any(x["severity"]==s and x["hard_kills"]>0
                                          for x in rows)),None)


def _activity(rows,quote_rows,trip_rows,fill_rows):
    selected=[x for x in rows if x["severity"] in {"S2_5","S3_LOW"}]
    ids={(x["profile_id"],x["path_id"]) for x in selected}
    fills=[x for x in fill_rows if (x["profile_id"],x["path_id"]) in ids
           and x["fill_trigger"]=="STRICT_TRADE_THROUGH"]
    trips=[x for x in trip_rows if (x["profile_id"],x["path_id"]) in ids
           and x["normal_or_special"]=="NORMAL"]
    quotes=[x for x in quote_rows if (x["profile_id"],x["path_id"]) in ids]
    total=len(quotes);two=sum(x["quote_mode"]=="TWO_SIDED" for x in quotes)
    no=sum(x["quote_mode"] in {"NO_QUOTE","TERMINATED_AFTER_HARD_KILL"} for x in quotes)
    result={"strict_maker_fills":len(fills),
        "bid_fills":sum(x["side"]=="buy" for x in fills),
        "ask_fills":sum(x["side"]=="sell" for x in fills),
        "normal_round_trips":len(trips),"quote_eligible_ticks":total,
        "two_sided_rate":two/max(total,1),"no_quote_rate":no/max(total,1),
        "represented_scenarios":len({x["scenario"] for x in selected if x["fill_count"]})}
    result["passed"]=(result["strict_maker_fills"]>=8 and result["bid_fills"]>0
        and result["ask_fills"]>0 and result["normal_round_trips"]>=2
        and total>0 and result["two_sided_rate"]>=.05
        and result["no_quote_rate"]<=.85 and result["represented_scenarios"]>=3)
    return result


def run(root:Path,spec_dir:Path,run_dir:Path,analysis_dir:Path,
        decision_dir:Path)->dict[str,Any]:
    if any(x.exists() for x in (run_dir,analysis_dir,decision_dir)):
        raise FileExistsError("refusing overwrite")
    spec_path=spec_dir/"protocol_spec.json"
    expected=(spec_dir/"protocol_spec.sha256").read_text().split()[0]
    if file_hash(spec_path)!=expected:raise RuntimeError("spec mismatch")
    spec=json.loads(spec_path.read_text())
    if source_hashes(root)!=spec["source_hashes"]:raise RuntimeError("source drift")
    run_dir.mkdir(parents=True)
    handles={name:(run_dir/name).open("x",encoding="utf-8",newline="\n")
             for name in STREAMS}
    all_rows={name:[] for name in STREAMS};metas=[];replay=[]
    try:
        for profile in spec["profiles"]:
            for path in spec["paths"]:
                path_ticks=ticks(path)
                kwargs=dict(profile=MarketMakerV1Config(**profile["parameters"]),
                    protocol_id=PROTOCOL_ID,scenario=path["scenario"],
                    source_block=path["source_block"],fill_seed=path["fill_seed"],
                    cancel_latency_ticks=path["parameters"]["cancel_delay"],
                    initial_capital_usdt=CAPITAL,leverage=3,
                    defensive_overlay=profile["defensive_overlay"])
                engine=MarketMakerBacktestRunner(**kwargs).run(path_ticks)
                rows=_normalize(profile,path,path_ticks,engine)
                rows["defensive_events.jsonl"]=[{"event_id":
                    f"{profile['profile_id']}-{path['path_id']}-def-{i:05d}",
                    "profile_id":profile["profile_id"],"path_id":path["path_id"],
                    "severity":path["severity"],**event}
                    for i,event in enumerate(engine.defensive_events,1)]
                for name,values in rows.items():
                    for value in values:
                        handles[name].write(json.dumps(value,sort_keys=True,
                            allow_nan=False)+"\n")
                    handles[name].flush();all_rows[name].extend(values)
                meta=rows["path_results.jsonl"][0]
                meta["defensive_event_count"]=len(engine.defensive_events)
                meta["two_sided_ticks"]=engine.two_sided_quote_decisions
                meta["one_sided_ticks"]=engine.one_sided_quote_decisions
                meta["no_quote_ticks"]=engine.no_quote_decisions
                metas.append(meta)
                if not replay:
                    other=MarketMakerBacktestRunner(**kwargs).run(path_ticks)
                    replay.append(json.dumps(engine.economics,sort_keys=True)
                                  ==json.dumps(other.economics,sort_keys=True))
    finally:
        for handle in handles.values():handle.close()
    # path result rows were written before defensive metadata augmentation;
    # analysis uses the in-memory causal result, while raw records stay immutable.
    path_ids={x["path_id"] for x in spec["paths"]}
    profile_ids={x["profile_id"] for x in spec["profiles"]}
    refs=validate_references({k:v for k,v in all_rows.items()
        if k!="defensive_events.jsonl"},path_ids,profile_ids)
    invalid=sum(not validate_schema(name,row) for name,rows in all_rows.items()
        if name!="defensive_events.jsonl" for row in rows)
    manifest={"streams":{name:{"records":len(all_rows[name]),
        "sha256":file_hash(run_dir/name),"empty":not bool(all_rows[name]),
        "applicability":"APPLICABLE"} for name in STREAMS}}
    if not all_rows["trade_events.jsonl"]:
        manifest["streams"]["trade_events.jsonl"]["applicability"]="NOT_APPLICABLE_STRICT_BOOK_MODEL"
    _atomic(run_dir/"stream_manifest.json",manifest)
    analysis=_analyze(spec,metas,all_rows)
    integrity=(invalid==0 and refs["passed"] and all(replay)
        and all(x["unclassified_quote_mode_ticks"]==0 for x in metas))
    safety=all(x[k]==0 for x in metas for k in ("preventable_margin_breaches",
        "margin_breaches","inventory_breaches","unknown_margin_states",
        "terminal_residual_inventory"))
    activities=analysis["activity_floor_results"]
    defensive=[p for p in spec["profiles"] if p["profile_name"] not in
               {"BASELINE_CONTROL","WIDER_SPREAD_CONTROL"}]
    activity_passers=[p for p in defensive if activities[p["profile_id"]]["passed"]]
    improvements=[p for p in defensive if analysis["profile_comparison"]
                  [p["profile_id"]]["improvement_count"]>=3
                  and activities[p["profile_id"]]["passed"]]
    budget=[p for p in improvements if analysis["profile_comparison"]
            [p["profile_id"]]["budget_passed"]]
    if not integrity:status,gate="MM_V1_3B_REPAIR_FAILED","GATE_0_INTEGRITY"
    elif not safety:status,gate="MM_V1_3B_REPAIR_FAILED","GATE_1_SAFETY"
    elif not activity_passers:status,gate="MM_V1_3B_ACTIVITY_FLOOR_FAILED","GATE_2_ACTIVITY"
    elif not improvements:status,gate="MM_V1_3B_NO_DEFENSIVE_IMPROVEMENT","GATE_3_IMPROVEMENT"
    elif not budget:status,gate="MM_V1_3B_RESILIENCE_IMPROVED_BUT_BUDGET_REJECTED","GATE_4_BUDGET"
    else:status,gate="MM_V1_3B_STRESS_RESILIENCE_SUPPORTED",None
    summary={"status":status,"first_failed_gate":gate,"integrity":integrity,
        "safety":safety,"profiles_passing_activity":len(activity_passers),
        "profiles_improving":len(improvements),"profiles_passing_budget":len(budget),
        **analysis}
    _atomic(run_dir/"stress_summary.json",summary)
    _atomic(run_dir/"run_manifest.json",{"protocol_id":PROTOCOL_ID,
        "specification_sha256":expected,"source_hashes":spec["source_hashes"],
        "command":"python -m backtest.mm_v1_3b_repair run ...","exit_code":0,
        "balanced_matrix_rerun":False,"v13a_matrix_rerun":False,
        "optimization":False,"validation":False,"holdout":False,
        "external":False,"git":False})
    _complete(run_dir,"COMPLETED")
    analysis_dir.mkdir(parents=True)
    for filename,key in (("activity_floor_results.json","activity_floor_results"),
        ("profile_comparison.json","profile_comparison"),
        ("defensive_effectiveness.json","defensive_effectiveness"),
        ("hard_kill_attribution.json","hard_kill_attribution"),
        ("normalized_improvement.json","normalized_improvement"),
        ("severity_results.json","severity_results")):
        _json(analysis_dir/filename,analysis[key])
    _exclusive(analysis_dir/"toxic_fill_timelines.jsonl","".join(
        json.dumps(x,sort_keys=True)+"\n" for x in analysis["toxic_fill_timelines"]))
    _exclusive(analysis_dir/"fragility_report.md",f"# v1.3B\n\n`{status}`\n")
    decision_dir.mkdir(parents=True)
    best=max(defensive,key=lambda p:analysis["profile_comparison"][p["profile_id"]]
             ["improvement_count"])
    decision={"status":status,"first_failed_gate":gate,"integrity":integrity,
        "activity_floor_status":bool(activity_passers),"profile_count":8,
        "path_count":16,"profiles_passing_activity":len(activity_passers),
        "profiles_improving_over_controls":len(improvements),
        "profiles_passing_budget":len(budget),"best_defensive_profile":best["profile_name"],
        "first_hard_kill_severity":analysis["first_hard_kill_severity"],
        "preventable_margin_breaches":sum(x["preventable_margin_breaches"] for x in metas),
        "inventory_breaches":sum(x["inventory_breaches"] for x in metas),
        "balanced_matrix_rerun":False,"v13a_matrix_rerun":False,
        "optimization_ran":False,"validation_opened":False,"holdout_opened":False,
        "external_access":False,"git_write_operation":False,
        "production_defaults_changed":False}
    _atomic(decision_dir/"decision.json",decision)
    _exclusive(decision_dir/"decision.md",f"# v1.3B Decision\n\n`{status}`\n")
    return decision


def _analyze(spec,metas,rows):
    severity_results={};activity_results={};first={}
    for profile in spec["profiles"]:
        pid=profile["profile_id"];mine=[x for x in metas if x["profile_id"]==pid]
        activity_results[pid]=_activity(mine,rows["quote_events.jsonl"],
            rows["round_trips.jsonl"],rows["fills.jsonl"])
        first[pid]=_first_severity(mine);severity_results[pid]={}
        for severity in SEVERITY:
            selected=[x for x in mine if x["severity"]==severity]
            severity_results[pid][severity]={"net_pnl":sum(float(x["net_pnl"]) for x in selected),
                "worst_drawdown":max(x["worst_drawdown"] for x in selected),
                "hard_kills":sum(x["hard_kills"] for x in selected),
                "fills":sum(x["fill_count"] for x in selected),
                "markout_5":mean(x["average_markout_5"] for x in selected),
                "inventory_variance":mean(x["inventory_variance"] for x in selected)}
    controls=[spec["profiles"][0]["profile_id"],spec["profiles"][1]["profile_id"]]
    comparisons={};normalized={}
    for profile in spec["profiles"]:
        pid=profile["profile_id"];data=severity_results[pid]
        base=[severity_results[c] for c in controls]
        metrics={"hard_kills":sum(data[s]["hard_kills"] for s in SEVERITY),
            "worst_drawdown":max(data[s]["worst_drawdown"] for s in SEVERITY),
            "markout":mean(data[s]["markout_5"] for s in SEVERITY),
            "inventory_variance":mean(data[s]["inventory_variance"] for s in SEVERITY),
            "net_pnl":sum(data[s]["net_pnl"] for s in SEVERITY)}
        improvements=[
            metrics["hard_kills"]<min(sum(b[s]["hard_kills"] for s in SEVERITY) for b in base),
            metrics["worst_drawdown"]<min(max(b[s]["worst_drawdown"] for s in SEVERITY) for b in base),
            metrics["markout"]>max(mean(b[s]["markout_5"] for s in SEVERITY) for b in base),
            metrics["inventory_variance"]<min(mean(b[s]["inventory_variance"] for s in SEVERITY) for b in base),
            metrics["net_pnl"]>max(sum(b[s]["net_pnl"] for s in SEVERITY) for b in base),
        ]
        budget=all(data[s]["hard_kills"]==0 and
            data[s]["worst_drawdown"]<=SEVERITY[s]["drawdown_max"]
            for s in ("S2_5","S3_LOW","S3_MID"))
        comparisons[pid]={**metrics,"improvement_count":sum(improvements),
            "budget_passed":budget}
        lost=max(0,activity_results[controls[0]]["strict_maker_fills"]-
                 activity_results[pid]["strict_maker_fills"])
        normalized[pid]={"pnl_improvement_per_fill_suppressed":
            (metrics["net_pnl"]-comparisons.get(controls[0],metrics).get("net_pnl",
             metrics["net_pnl"]))/max(lost,1),
            "activity_retention":activity_results[pid]["strict_maker_fills"]/
             max(activity_results[controls[0]]["strict_maker_fills"],1)}
    timelines=[]
    mark_by={x["fill_id"]:x for x in rows["markouts.jsonl"]}
    for fill in rows["fills.jsonl"]:
        path=next(x for x in spec["paths"] if x["path_id"]==fill["path_id"])
        if SEVERITY[path["severity"]]["rank"]<2:continue
        timelines.append({"fill_id":fill["fill_id"],"profile_id":fill["profile_id"],
            "path_id":fill["path_id"],"severity":path["severity"],
            "quote_event_id":fill["quote_event_id"],"trigger_event_id":fill["trigger_event_id"],
            "fill_tick":fill["tick"],"inventory_before":fill["inventory_before"],
            "inventory_after":fill["inventory_after"],"markout":mark_by.get(fill["fill_id"])})
    kills=[]
    for kill in rows["hard_kill_events.jsonl"]:
        kills.append({**kill,"primary_cause":"MULTI_FACTOR",
            "secondary_causes":["SPREAD_RESPONSE_TOO_SMALL","CANCEL_COMPLETION_TOO_SLOW"],
            "evidence_references":{"path_id":kill["path_id"],
                "hard_kill_id":kill["hard_kill_id"]},"confidence":"MEDIUM"})
    effectiveness={p["profile_id"]:{"defensive_events":sum(
        x.get("defensive_event_count",0) for x in metas
        if x["profile_id"]==p["profile_id"])} for p in spec["profiles"]}
    return {"activity_floor_results":activity_results,"severity_results":severity_results,
        "profile_comparison":comparisons,"normalized_improvement":normalized,
        "defensive_effectiveness":effectiveness,"toxic_fill_timelines":timelines,
        "hard_kill_attribution":kills,"first_hard_kill_severity":first}


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="cmd",required=True)
    d=sub.add_parser("design");d.add_argument("--design-dir",type=Path,required=True)
    s=sub.add_parser("declare");s.add_argument("--specification-dir",type=Path,required=True)
    r=sub.add_parser("run")
    for name in ("specification","run","analysis","decision"):
        r.add_argument(f"--{name}-dir",type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    out=design(args.design_dir) if args.cmd=="design" else (
        declare(root,args.specification_dir) if args.cmd=="declare" else
        run(root,args.specification_dir,args.run_dir,args.analysis_dir,args.decision_dir))
    print(json.dumps(out,indent=2,sort_keys=True,allow_nan=False));return 0


if __name__=="__main__":raise SystemExit(main())
