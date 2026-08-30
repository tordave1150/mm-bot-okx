"""Close a failed formal run with read-only staleness reconciliation evidence.

This is reporting recovery only.  It refuses to create an execution marker,
call a gateway mutation, change account configuration, or recover/resume the
trading loop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from okx_fill_restart_executor import (
    _build_gateway,
    _write_new_json,
    load_frozen_package,
)
from okx_fill_restart_offline import ARTIFACT_ROOT, _sha256


EXPECTED_PACKAGE_ID = "formal-package-20260806T152904Z"
EXPECTED_RUN_ID = "formal-20260806T152904Z"
FAILED_STATUS = "OKX_DEMO_FILL_RESTART_SAFETY_FAILED"


class StalenessReportRecoveryError(RuntimeError):
    pass


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StalenessReportRecoveryError(f"JSON object required: {path.name}")
    return value


def _recovery_snapshot(gateway: object) -> dict[str, object]:
    account = gateway.fetch_account()
    raw = gateway._read("fetch_order_book", "BTC/USDT:USDT")
    bids = raw.get("bids") or []
    asks = raw.get("asks") or []
    timestamp_ms = int(raw.get("timestamp") or 0)
    observed_at_ms = int(time.time() * 1000)
    return {
        "account": {
            "position_btc": str(account.position_btc),
            "open_order_count": len(account.open_orders),
            "clock_skew_ms": int(account.clock_skew_ms),
            "position_mode": str(account.position_mode),
            "leverage": str(account.leverage),
        },
        "book": {
            "timestamp_present": timestamp_ms > 0,
            "age_ms": observed_at_ms - timestamp_ms,
            "has_bids": bool(bids),
            "has_asks": bool(asks),
            "spread_valid": bool(
                bids and asks and float(asks[0][0]) > float(bids[0][0])
            ),
        },
    }


def recover(root: Path, package_id: str) -> Path:
    root = root.resolve()
    if package_id != EXPECTED_PACKAGE_ID:
        raise StalenessReportRecoveryError("unexpected formal package ID")
    package = load_frozen_package(root, package_id)
    if package.spec.formal_run_id != EXPECTED_RUN_ID:
        raise StalenessReportRecoveryError("unexpected formal run ID")
    output = root / ARTIFACT_ROOT / package_id
    marker_path = output / "formal_run" / "FORMAL_EXECUTION_ARMED.json"
    unresolved_path = output / "formal_run" / "UNRESOLVED_FAILURE.json"
    terminal_path = output / "COMPLETED.json"
    if terminal_path.exists():
        raise StalenessReportRecoveryError("formal terminal already exists")
    if not marker_path.is_file() or not unresolved_path.is_file():
        raise StalenessReportRecoveryError("marker or unresolved failure is missing")
    marker = _json(marker_path)
    unresolved = _json(unresolved_path)
    if (
        marker.get("formal_execution_marker_count") != 1
        or marker.get("formal_run_id") != EXPECTED_RUN_ID
        or unresolved.get("status") != FAILED_STATUS
        or unresolved.get("report_recovery_required") is not True
        or unresolved.get("second_order_run_allowed") is not False
        or unresolved.get("live_endpoint_attempts") != 0
        or unresolved.get("live_orders") != 0
        or unresolved.get("shutdown_errors") != ["reporting:FormalGatewayError"]
        or "authoritative order book is stale or invalid"
        not in str(unresolved.get("reason"))
    ):
        raise StalenessReportRecoveryError("unresolved failure contract drift")
    if (output / "formal_run" / "streams" / "order.jsonl").exists():
        raise StalenessReportRecoveryError("unexpected order stream exists")
    marker_count = len(list(output.glob("formal_run/FORMAL_EXECUTION_ARMED.json")))
    if marker_count != 1:
        raise StalenessReportRecoveryError("formal marker count is not exactly one")

    gateway, secrets = _build_gateway()
    try:
        gateway.load_market()
        snapshots = [_recovery_snapshot(gateway)]
        time.sleep(package.spec.observation_interval_ms / 1000)
        snapshots.append(_recovery_snapshot(gateway))
        audit = gateway.public_audit()
    finally:
        try:
            gateway.exchange.close()
        except Exception:
            pass

    accounts = [row["account"] for row in snapshots]
    books = [row["book"] for row in snapshots]
    if not all(
        row["position_btc"] == "0"
        and row["open_order_count"] == 0
        and row["position_mode"] == "net_mode"
        and row["leverage"] in {"3", "3.0"}
        for row in accounts
    ):
        raise StalenessReportRecoveryError("recovery account is not flat and empty")
    if not all(
        row["timestamp_present"]
        and row["has_bids"]
        and row["has_asks"]
        and row["spread_valid"]
        and abs(int(row["age_ms"])) <= package.spec.maximum_clock_skew_ms
        for row in books
    ) or not any(int(row["age_ms"]) < 0 for row in books):
        raise StalenessReportRecoveryError("negative-age staleness was not reproduced")
    if (
        audit.get("mutation_call_count") != 0
        or audit.get("mutation_methods") != []
        or audit.get("flatten_dispatches") != 0
        or audit.get("live_endpoint_attempts") != 0
        or audit.get("live_orders") != 0
        or audit.get("sandbox_mode") is not True
        or audit.get("simulated_trading_header") is not True
    ):
        raise StalenessReportRecoveryError("recovery transport boundary failed")

    recovery_dir = output / "formal_run" / "recovery"
    recovery = {
        "status": "READ_ONLY_REPORT_RECOVERY_PASSED",
        "formal_run_id": EXPECTED_RUN_ID,
        "formal_execution_marker_count": 1,
        "second_formal_execution": False,
        "snapshots": snapshots,
        "flat_empty_consistent": True,
        "root_cause": (
            "order-book timestamps were slightly ahead of local receive time; "
            "the formal gateway rejected every negative age even though account "
            "clock skew remained within the frozen 1500 ms bound"
        ),
        "runtime_fix_applied": False,
        "rerun_attempted": False,
        "orders_submitted": 0,
        "orders_amended": 0,
        "orders_cancelled": 0,
        "flatten_dispatches": 0,
        "account_configuration_mutations": 0,
        "report_recovery_network_reads": audit["read_call_count"],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "recovery_source": Path(__file__).name,
        "recovery_source_sha256": _sha256(Path(__file__).resolve()),
    }
    _write_new_json(recovery_dir / "staleness_reconciliation.json", recovery)
    audits = output / "audits" / "formal"
    _write_new_json(audits / "endpoint_audit.json", audit)
    _write_new_json(audits / "staleness_audit.json", {
        "passed": True,
        "maximum_clock_skew_ms": package.spec.maximum_clock_skew_ms,
        "clock_skew_ms": [row["clock_skew_ms"] for row in accounts],
        "order_book_age_ms": [row["age_ms"] for row in books],
        "negative_age_rejected_by_frozen_gateway": True,
        "source_change_or_retry_in_this_run": False,
    })
    _write_new_json(audits / "position_audit.json", {
        "passed": True,
        "snapshot_count": 2,
        "final_position_btc": "0",
    })
    _write_new_json(audits / "order_audit.json", {
        "passed": True,
        "normal_create_dispatches": 0,
        "normal_cancel_dispatches": 0,
        "flatten_dispatches": 0,
        "final_open_order_count": 0,
    })
    _write_new_json(audits / "restart_audit.json", {
        "R1_completed": False,
        "R2_completed": False,
        "process_generation": 0,
        "restart_attempted": False,
    })
    _write_new_json(audits / "accounting_audit.json", {
        "passed": True,
        "normal_fill_count": 0,
        "special_fill_count": 0,
        "actual_fees_usdt": "0",
        "gross_realized_pnl_usdt": "0",
        "net_realized_pnl_usdt": "0",
        "final_inventory_btc": "0",
    })

    secret_matches: list[str] = []
    scanned = 0
    encoded = tuple(value.encode("utf-8") for value in secrets if value)
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix == ".tmp":
            continue
        scanned += 1
        raw = path.read_bytes()
        if any(secret in raw for secret in encoded):
            secret_matches.append(path.relative_to(output).as_posix())
    if secret_matches:
        raise StalenessReportRecoveryError("credential value found in artifacts")
    _write_new_json(audits / "secret_scan.json", {
        "passed": True,
        "files_scanned": scanned,
        "credential_values_reported": False,
        "secret_matches": [],
    })

    result = {
        "status": FAILED_STATUS,
        "reason": (
            "formal start failed closed before placement because the frozen "
            "gateway rejected a slightly future-dated OKX order-book timestamp; "
            "read-only recovery proved terminal flat and empty"
        ),
        "formal_run_id": EXPECTED_RUN_ID,
        "formal_execution_marker_count": 1,
        "normal_bid_fills": 0,
        "normal_ask_fills": 0,
        "normal_fifo_round_trips": 0,
        "special_fill_count": 0,
        "actual_fees_usdt": "0",
        "gross_realized_pnl_usdt": "0",
        "net_realized_pnl_usdt": "0",
        "final_position_btc": "0",
        "final_open_order_count": 0,
        "R1_completed": False,
        "R2_completed": False,
        "report_recovery_completed": True,
        "second_order_run_allowed": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
    }
    _write_new_json(output / "formal_run" / "raw_result.json", result)
    _write_new_json(output / "formal_run" / "RAW_COMPLETED.json", {
        "status": FAILED_STATUS,
        "formal_run_id": EXPECTED_RUN_ID,
        "formal_execution_marker_count": 1,
        "final_position_btc": "0",
        "final_open_order_count": 0,
        "report_recovery_completed": True,
    })
    _write_new_json(output / "decision" / "formal" / "decision.json", result)
    decision_md = output / "decision" / "formal" / "decision.md"
    decision_md.parent.mkdir(parents=True, exist_ok=True)
    if decision_md.exists():
        raise StalenessReportRecoveryError("formal decision already exists")
    decision_md.write_text(
        "# Formal OKX Demo decision\n\n"
        f"- Status: `{FAILED_STATUS}`\n"
        "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n"
        "- Final position: `0 BTC`\n"
        "- Final open orders: `0`\n"
        "- Live endpoint attempts/orders: `0 / 0`\n"
        "- Retry in this run: `false`\n",
        encoding="utf-8",
        newline="\n",
    )

    manifest = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and path.name not in {"COMPLETED.json", "formal_completion_hashes.json"}
    }
    _write_new_json(output / "formal_run" / "formal_completion_hashes.json", manifest)
    terminal = {
        **result,
        "formal_completion_hashes_sha256": _sha256(
            output / "formal_run" / "formal_completion_hashes.json"
        ),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _write_new_json(terminal_path, terminal)
    return terminal_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--package-id", required=True)
    args = parser.parse_args()
    try:
        terminal = recover(args.root, args.package_id)
    except Exception as exc:
        print(json.dumps({
            "status": "REPORT_RECOVERY_FAILED_CLOSED",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, sort_keys=True))
        return 1
    print(json.dumps({
        "status": "READ_ONLY_REPORT_RECOVERY_PASSED",
        "terminal": str(terminal),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
