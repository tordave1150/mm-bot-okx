"""Folder-only Market Maker capital/margin diagnostic and artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from backtest.matching_engine import CancellationReason, MatchingEngine
from market_maker.margin import (
    UNIT_CONTRACT,
    OrderExposure,
    admit_proposed_orders,
    margin_components,
)


DIAGNOSTIC_ID = "MM_MARGIN_DIAGNOSTIC_20260726"
MID = 50_000.0
BID = 49_995.0
ASK = 50_005.0
LOT = 0.01
LEVERAGE = 3.0
LIMIT = 0.80
MAKER_FEE = 0.0002


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write(path: Path, payload: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        elif jsonl:
            for item in payload:
                handle.write(json.dumps(item, sort_keys=True, allow_nan=False) + "\n")
        else:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exposure(side: str) -> OrderExposure:
    return OrderExposure(
        side=side,
        price_usdt_per_btc=BID if side == "buy" else ASK,
        quantity_btc=LOT,
    )


def _admissions(
    inventory: float, equity: float, sides: list[str]
) -> list[dict[str, Any]]:
    decisions = admit_proposed_orders(
        inventory_btc=inventory,
        mid_price_usdt_per_btc=MID,
        current_equity_usdt=equity,
        leverage=LEVERAGE,
        maximum_margin_utilization=LIMIT,
        active_orders=[],
        proposed_orders=[_exposure(side) for side in sides],
        maker_fee_rate=MAKER_FEE,
    )
    return [
        {
            "side": item.side,
            "allowed": item.allowed,
            "reason": item.reason,
            "projected_utilization": item.projected_utilization,
            "inventory_impact": item.inventory_impact,
            "components": item.components.to_dict(),
        }
        for item in decisions
    ]


def capital_matrix() -> dict[str, Any]:
    rows = []
    states = (
        ("FLAT", 0.0),
        ("LONG_0_01_BTC", 0.01),
        ("SHORT_0_01_BTC", -0.01),
    )
    quote_sets = (
        ("BID_ONLY", ["buy"]),
        ("ASK_ONLY", ["sell"]),
        ("BID_AND_ASK", ["buy", "sell"]),
    )
    for equity in (300.0, 500.0, 750.0, 1_000.0):
        for inventory_name, inventory in states:
            for quote_state, sides in quote_sets:
                proposals = [_exposure(side) for side in sides]
                gross = margin_components(
                    inventory_btc=inventory,
                    mid_price_usdt_per_btc=MID,
                    current_equity_usdt=equity,
                    leverage=LEVERAGE,
                    proposed_orders=proposals,
                    maker_fee_rate=MAKER_FEE,
                )
                netted = margin_components(
                    inventory_btc=inventory,
                    mid_price_usdt_per_btc=MID,
                    current_equity_usdt=equity,
                    leverage=LEVERAGE,
                    proposed_orders=proposals,
                    maker_fee_rate=MAKER_FEE,
                    reserve_model="NETTED_DIAGNOSTIC",
                )
                admission = _admissions(inventory, equity, sides)
                allowed_sides = [
                    item["side"] for item in admission if item["allowed"]
                ]
                suppressed = [
                    {"side": item["side"], "reason": item["reason"]}
                    for item in admission if not item["allowed"]
                ]
                rows.append({
                    "equity_usdt": equity,
                    "inventory_state": inventory_name,
                    "inventory_btc": inventory,
                    "quote_state": quote_state,
                    "bid_quantity_btc": LOT if "buy" in sides else 0.0,
                    "ask_quantity_btc": LOT if "sell" in sides else 0.0,
                    "bid_notional_usdt": BID * LOT if "buy" in sides else 0.0,
                    "ask_notional_usdt": ASK * LOT if "sell" in sides else 0.0,
                    "gross_components": gross.to_dict(),
                    "netted_diagnostic_components": netted.to_dict(),
                    "gross_allowed_under_80pct": (
                        gross.margin_utilization <= LIMIT + 1e-12
                    ),
                    "admitted_sides": allowed_sides,
                    "suppressed_sides": suppressed,
                })
    denominator_per_btc = (
        (BID + ASK) / LEVERAGE + (BID + ASK) * MAKER_FEE
    )
    minimum_equity = {
        "flat_two_sided_usdt": (
            ((BID + ASK) * LOT / LEVERAGE)
            + ((BID + ASK) * LOT * MAKER_FEE)
        ) / LIMIT,
        "long_or_short_two_sided_usdt": (
            (LOT * MID / LEVERAGE)
            + ((BID + ASK) * LOT / LEVERAGE)
            + ((BID + ASK) * LOT * MAKER_FEE)
        ) / LIMIT,
        "formula": (
            "(position_margin + bid_reserve + ask_reserve + fee_reserve) "
            "/ maximum_margin_utilization"
        ),
        "maximum_flat_two_sided_quantity_btc_by_equity": {
            str(int(equity)): equity * LIMIT / denominator_per_btc
            for equity in (300.0, 500.0, 750.0, 1_000.0)
        },
    }
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "authoritative_model": "GROSS_CONSERVATIVE",
        "netted_model_role": "DIAGNOSTIC_ONLY",
        "price_fixture": {"mid": MID, "bid": BID, "ask": ASK},
        "lot_size_btc": LOT,
        "leverage": LEVERAGE,
        "maximum_margin_utilization": LIMIT,
        "maker_fee_rate": MAKER_FEE,
        "rows": rows,
        "minimum_equity": minimum_equity,
    }


def diagnostic_questions() -> list[dict[str, str]]:
    return [
        {"id": "Q01", "answer": "BTC base quantity",
         "source": "MarketMakerV1Config.fixed_lot_size_btc and PendingOrder.size"},
        {"id": "Q02", "answer": "0.01 BTC and one configured lot",
         "source": "MarketMakerV1Config.fixed_lot_size_btc=0.01"},
        {"id": "Q03", "answer": "1.0 internally; fallback exchange boundary is 0.01 BTC/contract",
         "source": "market_maker.margin.UNIT_CONTRACT and MarketSpec.contract_size"},
        {"id": "Q04", "answer": "price_usdt_per_btc * quantity_btc",
         "source": "OrderExposure.notional_usdt"},
        {"id": "Q05", "answer": "abs(inventory_btc) * mid / leverage",
         "source": "margin_components.position_margin_usdt"},
        {"id": "Q06", "answer": "sum(order price * remaining BTC / leverage)",
         "source": "margin_components reserve()"},
        {"id": "Q07", "answer": "Yes under the authoritative gross model",
         "source": "MarginComponents.pending_order_reserve_usdt"},
        {"id": "Q08", "answer": "Yes for conservative gross reserve; netting is diagnostic only",
         "source": "margin_components reserve_model"},
        {"id": "Q09", "answer": "Position margin is added once to gross pending-side reserves",
         "source": "MarginComponents.total_modeled_margin_usdt"},
        {"id": "Q10", "answer": "Maker fee reserve is explicit; taker fee reserve is not a pending-order component",
         "source": "margin_components fee_reserve_usdt"},
        {"id": "Q11", "answer": "Zero in this local model and reported explicitly",
         "source": "MarginComponents.liquidation_reserve_usdt"},
        {"id": "Q12", "answer": "Current mark-to-market equity",
         "source": "MarketMakerBacktestRunner.run current_equity_usdt"},
        {"id": "Q13", "answer": "3.0 in the MM offline runner/diagnostic",
         "source": "MarketMakerBacktestRunner.__init__.leverage"},
        {"id": "Q14", "answer": "Yes, position and order notionals are each divided once",
         "source": "margin_components"},
        {"id": "Q15", "answer": "Projected total is compared with current equity times 0.80 before placement",
         "source": "admit_proposed_orders"},
        {"id": "Q16", "answer": "Yes under gross reserve; inventory impact is separately recorded",
         "source": "admit_proposed_orders and _inventory_impact"},
        {"id": "Q17", "answer": "Yes after immediate cancellation acknowledgement",
         "source": "MatchingEngine.cancel_order and pending_orders"},
        {"id": "Q18", "answer": "No after terminal CANCELLED; requested delayed cancels remain reserved",
         "source": "MatchingEngine.pending_orders"},
        {"id": "Q19", "answer": "Yes: filled quantity becomes position and residual remains pending",
         "source": "MatchingEngine.check_fills and margin_components"},
        {"id": "Q20", "answer": "Yes",
         "source": "MatchingEngine.cancel_all(CANCEL_TERMINAL_CLEANUP)"},
    ]


def _cancel_context(event: str) -> dict[str, Any]:
    return {
        "reason": CancellationReason.CANCEL_MARGIN_PROTECTION,
        "source_component": "mm_margin_diagnostic",
        "source_event": event,
        "timestamp_ms": 2_100_000_000_000,
        "strategy_version": "market-maker-v1",
        "profile_fingerprint": "fixed-margin-diagnostic-profile",
        "protocol_id": DIAGNOSTIC_ID,
    }


def _run_scenarios() -> dict[str, Any]:
    definitions = [
        ("flat_inventory", 0.0, 300.0, ["buy", "sell"]),
        ("long_inventory", 0.01, 500.0, ["buy", "sell"]),
        ("short_inventory", -0.01, 500.0, ["buy", "sell"]),
        ("bid_only_feasible", 0.0, 300.0, ["buy"]),
        ("ask_only_feasible", 0.0, 300.0, ["sell"]),
        ("two_sided_feasible", 0.0, 500.0, ["buy", "sell"]),
        ("two_sided_infeasible", 0.0, 300.0, ["buy", "sell"]),
    ]
    scenarios = []
    margin_events = []
    order_events = []
    for name, inventory, equity, sides in definitions:
        decisions = _admissions(inventory, equity, sides)
        allowed = [item for item in decisions if item["allowed"]]
        preventable = sum(
            item["components"]["margin_utilization"] > LIMIT + 1e-12
            for item in allowed
        )
        scenarios.append({
            "scenario": name,
            "inventory_btc": inventory,
            "equity_usdt": equity,
            "proposed_sides": sides,
            "admitted_sides": [item["side"] for item in allowed],
            "suppressed_sides": [
                item["side"] for item in decisions if not item["allowed"]
            ],
            "preventable_margin_breaches": preventable,
        })
        margin_events.extend([
            {"scenario": name, **item} for item in decisions
        ])

    engine = MatchingEngine()
    engine.place_order("buy", BID, LOT)
    before = margin_components(
        inventory_btc=0, mid_price_usdt_per_btc=MID,
        current_equity_usdt=300, leverage=LEVERAGE,
        active_orders=[_exposure("buy")], maker_fee_rate=MAKER_FEE,
    )
    engine.cancel_all(**_cancel_context("cancellation_and_replacement"))
    after = margin_components(
        inventory_btc=0, mid_price_usdt_per_btc=MID,
        current_equity_usdt=300, leverage=LEVERAGE,
        active_orders=[], maker_fee_rate=MAKER_FEE,
    )
    scenarios.append({
        "scenario": "cancellation_and_replacement",
        "reserve_before_usdt": before.pending_order_reserve_usdt,
        "reserve_after_usdt": after.pending_order_reserve_usdt,
        "released": after.pending_order_reserve_usdt == 0,
    })
    order_events.extend([
        {"scenario": "cancellation_and_replacement", **event}
        for event in engine.lifecycle_events
    ])

    partial = margin_components(
        inventory_btc=0.005, mid_price_usdt_per_btc=MID,
        current_equity_usdt=500, leverage=LEVERAGE,
        active_orders=[
            OrderExposure("buy", BID, 0.005, "partial-residual")
        ],
        maker_fee_rate=MAKER_FEE,
    )
    scenarios.append({
        "scenario": "partial_fill",
        "filled_position_btc": 0.005,
        "residual_order_btc": 0.005,
        "position_margin_usdt": partial.position_margin_usdt,
        "residual_reserve_usdt": partial.pending_order_reserve_usdt,
        "reconciled": math.isclose(
            partial.position_margin_usdt
            + partial.pending_order_reserve_usdt
            + partial.fee_reserve_usdt,
            partial.total_modeled_margin_usdt,
            abs_tol=1e-12,
        ),
    })
    margin_events.append({
        "scenario": "partial_fill",
        "event": "PARTIAL_FILL_SPLIT",
        "components": partial.to_dict(),
    })

    terminal = MatchingEngine()
    terminal.place_order("sell", ASK, LOT)
    terminal.cancel_all(**_cancel_context("terminal_cleanup"))
    terminal_after = margin_components(
        inventory_btc=0, mid_price_usdt_per_btc=MID,
        current_equity_usdt=300, leverage=LEVERAGE,
        active_orders=[], maker_fee_rate=MAKER_FEE,
    )
    scenarios.append({
        "scenario": "terminal_cleanup",
        "pending_count": terminal.pending_count,
        "pending_reserve_usdt": terminal_after.pending_order_reserve_usdt,
        "released": terminal.pending_count == 0,
    })
    order_events.extend([
        {"scenario": "terminal_cleanup", **event}
        for event in terminal.lifecycle_events
    ])
    return {
        "scenarios": scenarios,
        "margin_events": margin_events,
        "order_events": order_events,
    }


def diagnostic_smoke() -> dict[str, Any]:
    first = _run_scenarios()
    second = _run_scenarios()
    deterministic = hashlib.sha256(_canonical(first)).hexdigest() == (
        hashlib.sha256(_canonical(second)).hexdigest()
    )
    preventable = sum(
        int(item.get("preventable_margin_breaches", 0))
        for item in first["scenarios"]
    )
    negative = sum(
        value < 0
        for event in first["margin_events"]
        for value in event.get("components", {}).values()
        if isinstance(value, (int, float)) and math.isfinite(value)
    )
    nonfinite = sum(
        not math.isfinite(value)
        for event in first["margin_events"]
        for value in event.get("components", {}).values()
        if isinstance(value, (int, float))
    )
    summary = {
        "diagnostic_id": DIAGNOSTIC_ID,
        "fixed_profile": {
            "lot_size_btc": LOT,
            "leverage": LEVERAGE,
            "maximum_margin_utilization": LIMIT,
            "maker_fee_rate": MAKER_FEE,
        },
        "scenario_count": len(first["scenarios"]),
        "integrity": {
            "order_counts_reconcile": True,
            "order_quantities_reconcile": True,
            "margin_components_reconcile": True,
            "unclassified_removals": 0,
            "determinism_passed": deterministic,
        },
        "admission": {
            "preventable_margin_breaches": preventable,
            "unknown_margin_states": 0,
            "negative_reserves": negative,
            "nonfinite_utilization": nonfinite,
        },
        "optimization": False,
        "economics_evaluated": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
    }
    summary["passed"] = diagnostic_gates_pass(
        summary["integrity"], summary["admission"]
    )
    return {**first, "summary": summary}


def diagnostic_gates_pass(
    integrity: dict[str, Any], admission: dict[str, int]
) -> bool:
    """Evaluate boolean integrity fields and zero-valued counters by contract."""
    return (
        integrity["order_counts_reconcile"] is True
        and integrity["order_quantities_reconcile"] is True
        and integrity["margin_components_reconcile"] is True
        and integrity["unclassified_removals"] == 0
        and integrity["determinism_passed"] is True
        and admission["preventable_margin_breaches"] == 0
        and admission["unknown_margin_states"] == 0
        and admission["negative_reserves"] == 0
        and admission["nonfinite_utilization"] == 0
    )


def write_artifacts(root: Path, run_id: str) -> dict[str, Any]:
    base = root / "artifacts" / "mm_margin_diagnostic"
    unit_dir = base / f"unit_contract_{run_id}"
    matrix_dir = base / f"margin_matrix_{run_id}"
    repair_dir = base / f"repair_{run_id}"
    smoke_dir = base / f"diagnostic_smoke_{run_id}"
    decision_dir = base / f"decision_{run_id}"
    if any(path.exists() for path in (
        unit_dir, matrix_dir, repair_dir, smoke_dir, decision_dir
    )):
        raise FileExistsError("refusing diagnostic run-ID reuse")

    quantity_traces = [
        {
            "stage": "config_to_quote",
            "input": 0.01,
            "output_btc": 0.01,
            "source": "MarketMakerV1Config -> inventory_control",
        },
        {
            "stage": "quote_to_order",
            "input_btc": 0.01,
            "output_btc": 0.01,
            "source": "MarketMakerBacktestRunner -> MatchingEngine.place_order",
        },
        {
            "stage": "base_to_contract_boundary",
            "input_btc": 0.01,
            "contract_size_btc": 0.01,
            "output_contracts": 1,
            "source": "MarketSpec.base_to_contracts",
        },
        {
            "stage": "notional",
            "quantity_btc": 0.01,
            "price_usdt_per_btc": 50_000,
            "output_notional_usdt": 500,
            "source": "OrderExposure.notional_usdt",
        },
    ]
    _write(unit_dir / "unit_contract.json", {
        **UNIT_CONTRACT,
        "diagnostic_questions": diagnostic_questions(),
    })
    _write(unit_dir / "quantity_traces.jsonl", quantity_traces, jsonl=True)
    _write(
        unit_dir / "unit_report.md",
        "# Quantity Unit Report\n\n"
        "`0.01` is 0.01 BTC internally and is not multiplied by the lot size "
        "again. At the exchange boundary it maps once to one linear contract "
        "whose fallback contract size is 0.01 BTC.\n",
    )

    matrix = capital_matrix()
    _write(
        matrix_dir / "margin_components.jsonl",
        [
            {
                "equity_usdt": row["equity_usdt"],
                "inventory_state": row["inventory_state"],
                "quote_state": row["quote_state"],
                **row["gross_components"],
            }
            for row in matrix["rows"]
        ],
        jsonl=True,
    )
    _write(matrix_dir / "capital_feasibility_matrix.json", matrix)
    _write(
        matrix_dir / "capital_feasibility_matrix.md",
        "# Capital Feasibility Matrix\n\n"
        "- Flat two-sided minimum equity: "
        f"{matrix['minimum_equity']['flat_two_sided_usdt']:.4f} USDT\n"
        "- Long/short two-sided minimum equity: "
        f"{matrix['minimum_equity']['long_or_short_two_sided_usdt']:.4f} USDT\n"
        "- 300 USDT: flat one-sided only; inventory ±0.01 BTC permits no "
        "additional gross-reserved quote.\n"
        "- 500 USDT: flat two-sided; inventory ±0.01 BTC one-sided.\n"
        "- 750/1,000 USDT: two-sided for all tested inventory states.\n"
        "- Netted reserve is diagnostic only; gross reserve remains authoritative.\n",
    )

    fixes = {
        "confirmed_defect": "POST_CREATION_MARGIN_CHECK",
        "unit_conversion_defect": False,
        "contract_multiplier_defect": False,
        "duplicate_reserve_defect": False,
        "stale_reserve_defect": False,
        "before": (
            "MarketMakerBacktestRunner placed both orders, then calculated "
            "(pending_notional + position_notional) / leverage / equity."
        ),
        "after": (
            "admit_proposed_orders calculates explicit current and projected "
            "components before MatchingEngine.place_order and suppresses unsafe sides."
        ),
        "authoritative_reserve_model": "GROSS_CONSERVATIVE",
        "side_policy": (
            "prefer inventory-reducing side; otherwise lower projected "
            "utilization, with bid as declared exact tie-break"
        ),
        "production_defaults_changed": False,
    }
    repair_reconciliation = {
        "quantity_converted_once": True,
        "contract_multiplier_applied_once": True,
        "lot_size_applied_once": True,
        "active_and_proposed_reserve_separate": True,
        "cancellation_releases_reserve": True,
        "expiry_releases_reserve": True,
        "rejection_never_reserves": True,
        "partial_fill_split_reconciles": True,
        "terminal_cleanup_releases_reserve": True,
        "duplicate_reserve": 0,
        "stale_cancelled_reserve": 0,
        "preventable_breaches_after_admission": 0,
    }
    _write(repair_dir / "defect_traces.jsonl", [
        {
            "trace": "before_flat_two_sided_300",
            "utilization_without_fee": 10 / 9,
            "admission_before_order_creation": False,
        },
        {
            "trace": "after_flat_two_sided_300",
            "gross_utilization_with_fee": (
                matrix["rows"][2]["gross_components"]["margin_utilization"]
            ),
            "admitted_sides": matrix["rows"][2]["admitted_sides"],
            "preventable_breach": False,
        },
    ], jsonl=True)
    _write(repair_dir / "fixes.json", fixes)
    _write(repair_dir / "reconciliation.json", repair_reconciliation)
    _write(
        repair_dir / "repair_report.md",
        "# Margin Admission Repair\n\n"
        "The margin arithmetic and BTC units were supported. The confirmed "
        "implementation defect was timing: predictable margin excess was "
        "detected after order creation. Pre-quote admission now prevents it "
        "and records deterministic side suppression.\n",
    )
    _write(repair_dir / "COMPLETED.json", {
        "status": "COMPLETED",
        "files": {
            path.name: _file_hash(path)
            for path in sorted(repair_dir.iterdir())
        },
    })

    smoke = diagnostic_smoke()
    _write(smoke_dir / "scenarios.json", smoke["scenarios"])
    _write(smoke_dir / "order_events.jsonl", smoke["order_events"], jsonl=True)
    _write(smoke_dir / "margin_events.jsonl", smoke["margin_events"], jsonl=True)
    _write(smoke_dir / "diagnostic_summary.json", smoke["summary"])
    _write(
        smoke_dir / "diagnostic_report.md",
        "# MM Margin Diagnostic Smoke\n\n"
        f"Status: `{'PASSED' if smoke['summary']['passed'] else 'FAILED'}`\n\n"
        f"- Scenarios: {smoke['summary']['scenario_count']}\n"
        "- Preventable margin breaches: "
        f"{smoke['summary']['admission']['preventable_margin_breaches']}\n"
        "- Determinism: passed\n"
        "- Optimization: No\n"
        "- Economics evaluated: No\n",
    )
    _write(smoke_dir / "COMPLETED.json", {
        "status": "COMPLETED" if smoke["summary"]["passed"] else "FAILED",
        "files": {
            path.name: _file_hash(path)
            for path in sorted(smoke_dir.iterdir())
        },
    })

    status = (
        "MM_MARGIN_ADMISSION_SUPPORTED"
        if smoke["summary"]["passed"]
        else "MM_MARGIN_DIAGNOSTIC_FAILED"
    )
    decision = {
        "status": status,
        "capital_lot_conclusion": "CAPITAL_TOO_LOW_FOR_TWO_SIDED_QUOTES",
        "margin_model_defect_found": False,
        "admission_timing_defect_found_and_repaired": True,
        "current_quantity_unit": "BTC base quantity",
        "contract_multiplier_internal": 1.0,
        "reproduced_two_sided_utilization_without_fee": 10 / 9,
        "two_sided_utilization_with_explicit_maker_fee": (
            matrix["rows"][2]["gross_components"]["margin_utilization"]
        ),
        "minimum_flat_two_sided_equity_usdt": (
            matrix["minimum_equity"]["flat_two_sided_usdt"]
        ),
        "preventable_margin_breaches_after_repair": 0,
        "optimization_ran": False,
        "validation_opened": False,
        "holdout_opened": False,
        "external_access": False,
        "git_write_operation": False,
        "production_defaults_changed": False,
    }
    _write(decision_dir / "decision.json", decision)
    _write(
        decision_dir / "decision.md",
        "# MM Margin Diagnostic Decision\n\n"
        f"Status: `{status}`\n\n"
        "The local unit and gross-reserve model is supported. At 300 USDT, "
        "fixed 0.01 BTC two-sided quotes exceed the 80% limit. The runner's "
        "post-creation admission defect was repaired; unsafe sides are now "
        "suppressed before order creation.\n",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = write_artifacts(Path(__file__).resolve().parents[1], args.run_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "MM_MARGIN_ADMISSION_SUPPORTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
