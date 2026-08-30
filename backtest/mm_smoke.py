"""Declare, freeze, execute, and finalize Market Maker v1 smoke evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backtest.mm_optimize import evaluate_protocol, sample_candidates
from backtest.mm_protocol import (
    MAX_EVALUATED_CANDIDATES,
    PROPOSAL_CEILING,
    PROTOCOL_DECLARATION,
    PROTOCOL_SCHEMA,
    SAMPLER_SEED,
    SEARCH_SPACE,
    VALID_CANDIDATE_TARGET,
    assert_no_path_collision,
    file_hash,
    frozen_paths,
    source_hashes,
)
from market_maker.as_config import (
    PROFILE_SCHEMA_VERSION,
    STRATEGY_NAME,
    UNIT_CONTRACT,
)
from market_maker.registry import ACTIVE_RESEARCH_STRATEGIES, select_research_strategy


def _write_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write_exclusive(
        temporary, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temporary.replace(path)


def build_protocol(root: Path) -> dict[str, Any]:
    paths = frozen_paths()
    assert_no_path_collision(root, paths)
    proposals, candidates = sample_candidates()
    protocol_id = "MM_V1_BOUNDED_OFFLINE_SMOKE_20260726"
    return {
        "schema_version": PROTOCOL_SCHEMA,
        "declaration": PROTOCOL_DECLARATION,
        "protocol_id": protocol_id,
        "strategy_name": STRATEGY_NAME,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "purpose": "BOUNDED_OFFLINE_RESEARCH_SMOKE_ONLY",
        "active_research_strategies": sorted(ACTIVE_RESEARCH_STRATEGIES),
        "removed_strategy_fallback": False,
        "search_space": {
            name: {
                "type": "int" if all(isinstance(v, int) for v in values) else "float",
                "minimum": min(values),
                "maximum": max(values),
                "distribution": "frozen_discrete",
                "values": values,
                "unit": {
                    "risk_aversion_gamma": "dimensionless",
                    "arrival_decay_k_or_proxy": "inverse fractional-price distance proxy",
                    "volatility_ewma_decay": "ratio",
                    "minimum_half_spread_bps": "bps",
                    "maximum_half_spread_bps": "bps",
                    "inventory_skew_strength": "dimensionless",
                    "imbalance_skew_strength": "dimensionless",
                    "minimum_order_lifetime_ticks": "ticks",
                    "maximum_order_age_ticks": "ticks",
                    "requote_threshold_ticks": "ticks",
                }[name],
            }
            for name, values in SEARCH_SPACE.items()
        },
        "static_constraints": [
            "risk_aversion_gamma > 0",
            "arrival_decay_k_or_proxy > 0",
            "minimum_half_spread_bps > 0",
            "maximum_half_spread_bps >= minimum_half_spread_bps",
            "minimum_order_lifetime_ticks >= 1",
            "maximum_order_age_ticks >= minimum_order_lifetime_ticks",
            "fixed safety and accounting values are not tunable",
        ],
        "sampler": {
            "kind": "constraint-aware deterministic discrete sampler",
            "seed": SAMPLER_SEED,
            "proposal_ceiling": PROPOSAL_CEILING,
            "valid_candidate_target": VALID_CANDIDATE_TARGET,
            "maximum_evaluated_candidates": MAX_EVALUATED_CANDIDATES,
            "frozen_proposal_count": len(proposals),
            "frozen_profile_fingerprints": [
                candidate["profile_fingerprint"] for candidate in candidates
            ],
            "optional_extension": None,
        },
        "paths": paths,
        "fill_mode": "conservative",
        "touch_only_positive_score_weight": 0.0,
        "probabilistic_positive_score_weight": 0.0,
        "objective": {
            "name": "frozen_dimensionless_mm_score_v1",
            "weights": {
                "net_pnl": 0.20,
                "average_spread_capture": 0.15,
                "profit_factor": 0.10,
                "after_top_10pct_removal": 0.10,
                "quote_uptime": 0.10,
                "maximum_drawdown": -0.10,
                "inventory_variance": -0.05,
                "adverse_markout_loss": -0.05,
                "terminal_liquidation_cost": -0.05,
                "quote_churn": -0.05,
                "cancel_to_fill_ratio": -0.05,
            },
            "tie_break": ["objective_score", "profile_fingerprint"],
            "raw_net_pnl_only": False,
        },
        "gates": {
            "integrity": {
                "unclassified_order_removals": 0,
                "finite_evidence": True,
                "determinism": True,
            },
            "activity": {
                "unique_conservative_maker_fills_min": 50,
                "bid_fills_min": 15,
                "ask_fills_min": 15,
                "round_trip_inventory_cycles_min": 10,
                "represented_scenarios_min": 4,
            },
            "safety": {
                "inventory_breaches": 0,
                "margin_breaches": 0,
                "hard_kills": 0,
                "terminal_residual_inventory_btc": 0,
                "worst_drawdown_max": 0.025,
            },
            "economics": {
                "net_pnl_usdt_gt": 0,
                "average_spread_capture_usdt_gt": 0,
                "profit_factor_gt": 1,
                "after_added_2bps_usdt_gt": 0,
                "after_top_10pct_removal_usdt_gt": 0,
                "average_5_tick_markout_usdt_min": -0.05,
            },
        },
        "source_hashes": source_hashes(root),
        "validation_opened": False,
        "holdout_opened": False,
        "external_endpoint_contacted": False,
        "production_defaults_changed": False,
    }


def declare(root: Path, hypothesis_dir: Path, protocol_dir: Path) -> dict[str, Any]:
    if hypothesis_dir.exists() or protocol_dir.exists():
        raise FileExistsError("refusing overwrite or run-ID reuse")
    select_research_strategy("market_maker_v1")
    protocol = build_protocol(root)
    strategy_spec = {
        "schema_version": "market-maker-v1-strategy-spec-v1",
        "strategy_name": STRATEGY_NAME,
        "two_sided_passive": True,
        "directional_signal_required": False,
        "arrival_decay_mode": "FIXED_DECLARED_PROXY",
        "empirically_calibrated": False,
        "exchange_ready": False,
        "fixed_safety": {
            "lot_size_btc": 0.01,
            "maximum_inventory_lots": 1,
            "maximum_margin_utilization": 0.80,
            "soft_session_loss_pct": 0.03,
            "hard_kill_drawdown_pct": 0.05,
        },
    }
    market_state_spec = {
        "schema_version": "market-maker-v1-market-state-v1",
        "features": [
            {
                "name": name,
                "timestamp_semantics": "current observed synthetic tick",
                "causal": True,
                "missing_behavior": "no quote",
                "staleness_limit_ms": 1000,
            }
            for name in (
                "best_bid", "best_ask", "mid_price", "spread", "microprice",
                "top_book_imbalance", "causal_ewma_volatility",
                "inventory", "equity", "margin_utilization",
            )
        ],
        "fabricated_queue_features": False,
    }
    hypothesis_dir.mkdir(parents=True)
    _atomic_json(hypothesis_dir / "strategy_spec.json", strategy_spec)
    _atomic_json(hypothesis_dir / "unit_contract.json", UNIT_CONTRACT)
    _atomic_json(hypothesis_dir / "market_state_spec.json", market_state_spec)
    _write_exclusive(
        hypothesis_dir / "strategy_spec.md",
        "# Market Maker v1 Strategy\n\n"
        "A causal, two-sided, passive Avellaneda–Stoikov research strategy with "
        "fixed inventory, margin, session-loss, and drawdown limits.\n\n"
        "The arrival-decay input is a declared smoke-only proxy and is not "
        "empirically calibrated or exchange-ready.\n",
    )
    protocol_dir.mkdir(parents=True)
    protocol_path = protocol_dir / "mm_v1_smoke_protocol.json"
    _atomic_json(protocol_path, protocol)
    digest = file_hash(protocol_path)
    _write_exclusive(
        protocol_dir / "mm_v1_smoke_protocol.sha256",
        digest + "  mm_v1_smoke_protocol.json\n",
    )
    _write_exclusive(
        protocol_dir / "mm_v1_smoke_protocol.md",
        "# Market Maker v1 Smoke Protocol\n\n"
        f"Declaration: `{PROTOCOL_DECLARATION}`\n\n"
        f"- Candidates: {VALID_CANDIDATE_TARGET}\n"
        f"- Proposal ceiling: {PROPOSAL_CEILING}\n"
        f"- Market paths: {len(protocol['paths'])}\n"
        "- Conservative fills only\n"
        "- Validation opened: No\n"
        "- Holdout opened: No\n",
    )
    return {"protocol_sha256": digest, "protocol": protocol}


def run_frozen(
    root: Path,
    protocol_dir: Path,
    run_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    if run_dir.exists() or decision_dir.exists():
        raise FileExistsError("refusing overwrite or run-ID reuse")
    protocol_path = protocol_dir / "mm_v1_smoke_protocol.json"
    expected_hash = (
        protocol_dir / "mm_v1_smoke_protocol.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if file_hash(protocol_path) != expected_hash:
        raise RuntimeError("protocol hash mismatch")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if source_hashes(root) != protocol["source_hashes"]:
        raise RuntimeError("covered source changed after protocol freeze")
    assert_no_path_collision(root, protocol["paths"])
    if sorted(ACTIVE_RESEARCH_STRATEGIES) != ["market_maker_v1"]:
        raise RuntimeError("active strategy isolation failed")
    proposals, _ = sample_candidates()
    result = evaluate_protocol(protocol)

    run_dir.mkdir(parents=True)
    _write_exclusive(
        run_dir / "proposals.jsonl",
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in proposals),
    )
    _write_exclusive(
        run_dir / "quote_funnels.jsonl",
        "".join(
            json.dumps({
                "profile_fingerprint": trial["profile_fingerprint"],
                "runs": [
                    {
                        "scenario": run.scenario,
                        "source_block": run.source_block,
                        "funnel": run.funnel(),
                    }
                    for run in trial["runs"]
                ],
            }, sort_keys=True) + "\n"
            for trial in result["trials"]
        ),
    )
    _write_exclusive(
        run_dir / "order_events.jsonl",
        "".join(
            json.dumps({
                **event,
                "profile_fingerprint": trial["profile_fingerprint"],
                "scenario": run.scenario,
                "source_block": run.source_block,
            }, sort_keys=True) + "\n"
            for trial in result["trials"]
            for run in trial["runs"]
            for event in run.order_events
        ),
    )
    _write_exclusive(
        run_dir / "fills.jsonl",
        "".join(
            json.dumps({
                **fill,
                "profile_fingerprint": trial["profile_fingerprint"],
            }, sort_keys=True) + "\n"
            for trial in result["trials"]
            for run in trial["runs"]
            for fill in run.fills
        ),
    )
    _write_exclusive(
        run_dir / "round_trips.jsonl",
        "".join(
            json.dumps({
                **trip,
                "profile_fingerprint": trial["profile_fingerprint"],
                "scenario": run.scenario,
                "source_block": run.source_block,
            }, sort_keys=True) + "\n"
            for trial in result["trials"]
            for run in trial["runs"]
            for trip in run.round_trips
        ),
    )
    compact_trials = [
        {
            key: trial[key]
            for key in (
                "candidate_index",
                "profile_fingerprint",
                "parameters",
                "integrity",
                "activity",
                "safety",
                "economics",
                "economics_passed",
                "objective_score",
            )
        }
        for trial in result["trials"]
    ]
    _write_exclusive(
        run_dir / "smoke_trials.jsonl",
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in compact_trials),
    )
    summary = {key: value for key, value in result.items() if key != "trials"}
    _atomic_json(run_dir / "smoke_summary.json", summary)
    raw_names = (
        "proposals.jsonl",
        "quote_funnels.jsonl",
        "order_events.jsonl",
        "fills.jsonl",
        "round_trips.jsonl",
        "smoke_trials.jsonl",
        "smoke_summary.json",
    )
    manifest = {
        "schema_version": "market-maker-v1-smoke-manifest-v1",
        "protocol_sha256": expected_hash,
        "source_hashes": protocol["source_hashes"],
        "commands": [{
            "command": "python -m backtest.mm_smoke run ...",
            "exit_code": 0,
        }],
        "raw_file_hashes": {
            name: file_hash(run_dir / name) for name in raw_names
        },
        "validation_opened": False,
        "holdout_opened": False,
        "external_endpoint_contacted": False,
        "production_defaults_changed": False,
    }
    _atomic_json(run_dir / "smoke_manifest.json", manifest)
    completed = {
        "schema_version": "market-maker-v1-smoke-completion-v1",
        "status": "COMPLETED",
        "decision": result["status"],
        "protocol_sha256": expected_hash,
        "files": {
            path.name: file_hash(path)
            for path in sorted(run_dir.iterdir())
            if path.name != "COMPLETED.json"
        },
    }
    _atomic_json(run_dir / "COMPLETED.json", completed)

    decision_dir.mkdir(parents=True)
    decision = {
        "schema_version": "market-maker-v1-smoke-decision-v1",
        "status": result["status"],
        "first_failed_gate": result["first_failed_gate"],
        "selected_profile_fingerprint": result["selected_profile_fingerprint"],
        "selected_activity": result["selected_activity"],
        "selected_safety": result["selected_safety"],
        "selected_economics": result["selected_economics"],
        "validation_opened": False,
        "holdout_opened": False,
        "external_endpoint_contacted": False,
    }
    _atomic_json(decision_dir / "decision.json", decision)
    _write_exclusive(
        decision_dir / "decision.md",
        "# Market Maker v1 Smoke Decision\n\n"
        f"Status: `{result['status']}`\n\n"
        f"First failed gate: `{result['first_failed_gate']}`\n\n"
        "This result is offline research only. Validation, holdout, exchange "
        "contact, demo, and live trading remain unauthorized.\n",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    declare_parser = commands.add_parser("declare")
    declare_parser.add_argument("--hypothesis-dir", type=Path, required=True)
    declare_parser.add_argument("--protocol-dir", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--protocol-dir", type=Path, required=True)
    run_parser.add_argument("--run-dir", type=Path, required=True)
    run_parser.add_argument("--decision-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.command == "declare":
        result = declare(root, args.hypothesis_dir, args.protocol_dir)
        output = {"protocol_sha256": result["protocol_sha256"]}
    else:
        output = run_frozen(
            root, args.protocol_dir, args.run_dir, args.decision_dir
        )
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
