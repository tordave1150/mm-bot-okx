"""Infrastructure-only order-lifecycle reconciliation smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from backtest.matching_engine import (
    CancellationReason,
    MatchingEngine,
    OrderState,
)
from config import Config
from fill_tracker import FillTracker


SCHEMA_VERSION = "funnel-repair-smoke-v1"
TS = 1_800_000_000_000


def _tracker() -> FillTracker:
    return FillTracker(Config())


def _tick(bid: float, ask: float, offset: int = 0) -> dict[str, Any]:
    return {
        "bids": [[bid, 1.0]],
        "asks": [[ask, 1.0]],
        "timestamp": TS + offset,
    }


def _context(reason: CancellationReason, case: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "source_component": "funnel_repair_smoke",
        "source_event": case,
        "timestamp_ms": TS,
        "strategy_version": "infrastructure-repair-v1",
        "profile_fingerprint": f"infra-{case}",
        "protocol_id": "FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
    }


class _OnePartialFillEngine(MatchingEngine):
    def __init__(self) -> None:
        super().__init__()
        self._partial_done = False

    def _check_single_fill(self, order, market_bid, market_ask, mid_price):
        if not self._partial_done:
            self._partial_done = True
            return order.price, order.size / 2.0, True
        return None


def _cancel_case(case: str, reason: CancellationReason) -> dict[str, Any]:
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    engine.cancel_all(**_context(reason, case))
    return _case_record(case, engine)


def _case_record(case: str, engine: MatchingEngine) -> dict[str, Any]:
    reconciliation = engine.reconciliation()
    return {
        "case": case,
        "reconciliation": reconciliation,
        "events": list(engine.lifecycle_events),
        "passed": reconciliation["funnel_accounting_reconciliation"],
    }


def run_smoke() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cases = [
        _cancel_case("ordinary_age", CancellationReason.CANCEL_MAXIMUM_ORDER_AGE),
        _cancel_case(
            "requote_price", CancellationReason.CANCEL_REQUOTE_PRICE_CHANGE
        ),
        _cancel_case("requote_size", CancellationReason.CANCEL_REQUOTE_SIZE_CHANGE),
        _cancel_case("strategy_replace", CancellationReason.CANCEL_STRATEGY_REPLACE),
        _cancel_case("inventory_rebalance", CancellationReason.CANCEL_INVENTORY_REBALANCE),
        _cancel_case("soft_stop", CancellationReason.CANCEL_RISK_SOFT_STOP),
        _cancel_case("hard_kill", CancellationReason.CANCEL_RISK_HARD_KILL),
        _cancel_case("terminal_cleanup", CancellationReason.CANCEL_TERMINAL_CLEANUP),
        _cancel_case(
            "emergency_cleanup", CancellationReason.CANCEL_EMERGENCY_CLEANUP
        ),
        _cancel_case("shutdown_cleanup", CancellationReason.CANCEL_SHUTDOWN),
    ]

    race = MatchingEngine(cancel_latency_ticks=2)
    race_id = race.place_order("buy", 100.0, 0.01)
    race.cancel_order(
        race_id,
        **_context(CancellationReason.CANCEL_REQUOTE_PRICE_CHANGE, "cancel_fill_race"),
    )
    race.check_fills(_tick(98.0, 99.0), _tracker())
    cases.append(_case_record("cancel_request_then_fill", race))

    filled = MatchingEngine()
    filled_id = filled.place_order("buy", 100.0, 0.01)
    filled.check_fills(_tick(98.0, 99.0), _tracker())
    filled.cancel_order(
        filled_id,
        **_context(CancellationReason.CANCEL_TERMINAL_CLEANUP, "fill_then_cancel"),
    )
    cases.append(_case_record("fill_then_later_cancel", filled))

    duplicate = MatchingEngine()
    duplicate.place_order("buy", 100.0, 0.01)
    kwargs = _context(CancellationReason.CANCEL_TERMINAL_CLEANUP, "duplicate_bulk")
    duplicate.cancel_all(**kwargs)
    duplicate.cancel_all(**kwargs)
    cases.append(_case_record("duplicate_cancel_all", duplicate))

    included = MatchingEngine()
    included.place_order("buy", 100.0, 0.01)
    included.check_fills(_tick(98.0, 99.0), _tracker())
    included.cancel_all(
        **_context(CancellationReason.CANCEL_TERMINAL_CLEANUP, "filled_in_bulk")
    )
    cases.append(_case_record("already_filled_in_cancel_all", included))

    partial_cancel = _OnePartialFillEngine()
    partial_cancel_id = partial_cancel.place_order("buy", 100.0, 0.02)
    partial_cancel.check_fills(_tick(99.0, 101.0), _tracker())
    partial_cancel.cancel_order(
        partial_cancel_id,
        **_context(
            CancellationReason.CANCEL_REQUOTE_SIZE_CHANGE,
            "partial_residual_cancel",
        ),
    )
    cases.append(_case_record("partial_fill_residual_cancel", partial_cancel))

    partial_open = _OnePartialFillEngine()
    partial_open.place_order("sell", 102.0, 0.02)
    partial_open.check_fills(_tick(99.0, 101.0), _tracker())
    partial_open.finalize_open_orders(
        timestamp_ms=TS,
        source_component="funnel_repair_smoke",
        source_event="partial_residual_open",
        strategy_version="infrastructure-repair-v1",
        profile_fingerprint="infrastructure-repair-profile",
        protocol_id="FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
    )
    cases.append(_case_record("partial_fill_residual_open", partial_open))

    rejected = MatchingEngine()
    rejected.record_rejected_before_activation(
        side="buy",
        price=100.0,
        size=0.01,
        timestamp_ms=TS,
        rejection_reason="POST_ONLY_WOULD_CROSS",
    )
    cases.append(_case_record("rejected_before_activation", rejected))

    expired = MatchingEngine()
    expired_id = expired.place_order("buy", 100.0, 0.01)
    expired.expire_order(
        expired_id,
        timestamp_ms=TS,
        source_component="funnel_repair_smoke",
        source_event="expiry",
    )
    cases.append(_case_record("expired_order", expired))

    terminal_open = MatchingEngine()
    terminal_open.place_order("buy", 100.0, 0.01)
    terminal_open.finalize_open_orders(
        timestamp_ms=TS,
        source_component="funnel_repair_smoke",
        source_event="terminal_open",
        strategy_version="infrastructure-repair-v1",
        profile_fingerprint="infrastructure-repair-profile",
        protocol_id="FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
    )
    cases.append(_case_record("open_at_terminal", terminal_open))

    contract_checks = {}
    missing = MatchingEngine()
    try:
        missing.cancel_all(
            source_component="funnel_repair_smoke",
            source_event="missing_reason",
            timestamp_ms=TS,
            strategy_version="infrastructure-repair-v1",
            profile_fingerprint="infrastructure-repair-profile",
            protocol_id="FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
        )
    except TypeError:
        contract_checks["missing_reason_raises"] = True
    unknown = MatchingEngine()
    try:
        unknown.cancel_all(
            reason="CANCEL_OTHER",
            source_component="funnel_repair_smoke",
            source_event="unknown_reason",
            timestamp_ms=TS,
            strategy_version="infrastructure-repair-v1",
            profile_fingerprint="infrastructure-repair-profile",
            protocol_id="FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
        )
    except ValueError:
        contract_checks["unknown_reason_raises"] = True

    totals = {
        key: sum(case["reconciliation"][key] for case in cases)
        for key in (
            "passive_orders_created",
            "fully_filled_orders",
            "partially_filled_orders",
            "fully_cancelled_orders",
            "rejected_before_activation",
            "expired_orders",
            "open_orders_at_terminal",
            "unclassified_order_removals",
            "duplicate_terminal_transitions",
            "multiple_terminal_reason_orders",
            "zero_terminal_reason_orders",
            "created_quantity",
            "filled_quantity",
            "cancelled_quantity",
            "rejected_quantity",
            "expired_quantity",
            "open_quantity_at_terminal",
        )
    }
    totals["order_count_reconciliation"] = (
        totals["passive_orders_created"]
        == totals["fully_filled_orders"]
        + totals["fully_cancelled_orders"]
        + totals["rejected_before_activation"]
        + totals["expired_orders"]
        + totals["open_orders_at_terminal"]
    )
    totals["order_quantity_reconciliation"] = math.isclose(
        totals["created_quantity"],
        totals["filled_quantity"]
        + totals["cancelled_quantity"]
        + totals["rejected_quantity"]
        + totals["expired_quantity"]
        + totals["open_quantity_at_terminal"],
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    totals["funnel_accounting_reconciliation"] = (
        totals["order_count_reconciliation"]
        and totals["order_quantity_reconciliation"]
        and totals["unclassified_order_removals"] == 0
        and totals["duplicate_terminal_transitions"] == 0
        and totals["multiple_terminal_reason_orders"] == 0
        and totals["zero_terminal_reason_orders"] == 0
        and all(contract_checks.values())
        and len(contract_checks) == 2
        and all(case["passed"] for case in cases)
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": "FUNNEL_REPAIR_INFRASTRUCTURE_ONLY_V1",
        "research_evaluation": False,
        "optimization": False,
        "validation_opened": False,
        "holdout_opened": False,
        "case_count": len(cases) + len(contract_checks),
        "contract_checks": contract_checks,
        "totals": totals,
        "passed": totals["funnel_accounting_reconciliation"],
    }
    return result, cases


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_exclusive(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def write_artifacts(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing artifact overwrite: {output_dir}")
    output_dir.mkdir(parents=True)
    result, cases = run_smoke()
    lifecycle = {
        "schema_version": "canonical-order-lifecycle-v1",
        "states": [state.value for state in OrderState],
        "initial_active_path": ["CREATED", "ACTIVE"],
        "legal_transitions": {
            "CREATED": ["PENDING_ACTIVATION", "ACTIVE", "REJECTED"],
            "PENDING_ACTIVATION": ["ACTIVE", "REJECTED", "EXPIRED"],
            "ACTIVE": [
                "PARTIALLY_FILLED", "FILLED", "CANCEL_REQUESTED",
                "CANCELLED", "EXPIRED", "OPEN_AT_TERMINAL",
            ],
            "PARTIALLY_FILLED": [
                "PARTIALLY_FILLED", "FILLED", "CANCEL_REQUESTED",
                "CANCELLED", "EXPIRED", "OPEN_AT_TERMINAL",
            ],
            "CANCEL_REQUESTED": [
                "PARTIALLY_FILLED", "FILLED", "CANCELLED", "OPEN_AT_TERMINAL"
            ],
            "FILLED": [],
            "CANCELLED": [],
            "REJECTED": [],
            "EXPIRED": [],
            "OPEN_AT_TERMINAL": [],
        },
        "terminal_states": [
            "FILLED", "CANCELLED", "REJECTED", "EXPIRED", "OPEN_AT_TERMINAL"
        ],
        "count_identity": (
            "created = filled + cancelled + rejected + expired + open_at_terminal"
        ),
        "quantity_identity": (
            "created_quantity = filled_quantity + cancelled_quantity + "
            "rejected_quantity + expired_quantity + open_quantity_at_terminal"
        ),
    }
    reasons = {
        "schema_version": "cancellation-reason-taxonomy-v1",
        "exclusive_primary_reasons": [reason.value for reason in CancellationReason],
        "invalid_success_bucket": "CANCEL_OTHER",
        "fail_closed_reason": "CANCEL_UNKNOWN_ERROR",
    }
    _write_exclusive(
        output_dir / "order_lifecycle.json",
        json.dumps(lifecycle, indent=2, sort_keys=True) + "\n",
    )
    _write_exclusive(
        output_dir / "cancellation_reasons.json",
        json.dumps(reasons, indent=2, sort_keys=True) + "\n",
    )
    _write_exclusive(
        output_dir / "reconciliation_results.json",
        json.dumps(result, indent=2, sort_keys=True) + "\n",
    )
    _write_exclusive(
        output_dir / "representative_traces.jsonl",
        "".join(json.dumps(case, sort_keys=True) + "\n" for case in cases),
    )
    report = (
        "# Funnel Accounting Repair\n\n"
        f"Status: `{'FUNNEL_REPAIR_SUPPORTED' if result['passed'] else 'FUNNEL_REPAIR_FAILED'}`\n\n"
        f"- Representative and contract cases: {result['case_count']}\n"
        f"- Passive orders: {result['totals']['passive_orders_created']}\n"
        f"- Unclassified removals: {result['totals']['unclassified_order_removals']}\n"
        f"- Order-count reconciliation: {result['totals']['order_count_reconciliation']}\n"
        f"- Order-quantity reconciliation: {result['totals']['order_quantity_reconciliation']}\n"
        "- Optimization performed: false\n"
        "- Validation opened: false\n"
        "- Holdout opened: false\n"
    )
    _write_exclusive(output_dir / "repair_report.md", report)
    payload_files = sorted(
        path for path in output_dir.iterdir() if path.name != "COMPLETED.json"
    )
    completed = {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETED" if result["passed"] else "FAILED",
        "repair_status": (
            "FUNNEL_REPAIR_SUPPORTED" if result["passed"] else "FUNNEL_REPAIR_FAILED"
        ),
        "files": {path.name: _sha256(path) for path in payload_files},
        "legacy_research_artifacts_required": False,
        "external_endpoint_contacted": False,
        "validation_opened": False,
        "holdout_opened": False,
    }
    _write_exclusive(
        output_dir / "COMPLETED.json",
        json.dumps(completed, indent=2, sort_keys=True) + "\n",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = write_artifacts(args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
