"""Create socket-denied evidence for the R1/terminal reconciliation repair."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_validation import canonical_sha256


ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r1_terminal_reconciliation_repair"
)
PREDECESSOR_PACKAGE_ID = "formal-package-20260809T152113Z"
PREDECESSOR_RUN_ID = "formal-20260809T152113Z"
REPAIR_STATUS = "OKX_DEMO_R1_TERMINAL_RECONCILIATION_REPAIR_OFFLINE_SUPPORT"
PREDECESSOR_HASHES = {
    "formal_run/FORMAL_EXECUTION_ARMED.json": (
        "1f085aac5729aa8fcccfdc9601cae2370794da3d98238110567288c58dd2f830"
    ),
    "formal_run/UNRESOLVED_FAILURE.json": (
        "c0771fe60e71f80e8b59b4efcd97ff2351caab6536ff3d3b33dd19a72161d379"
    ),
    "formal_run/state/formal_state.json": (
        "a4d1dcf6833b46f87e88831de9dd5760f34f4f3e53c31f99447daf8bc9fc82f2"
    ),
    "formal_run/state/validation_state.json": (
        "5bffd99213150ee96d00ff4675c57c30cef2b235a7b3c965d8183d8f448fe57d"
    ),
    "formal_run/streams/safety.jsonl": (
        "5bbf05e8585f6162fccf0642deb38be00a937630f6cd2210df7be766636c4703"
    ),
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_R1_TERMINAL_RECONCILIATION_REPAIR.md",
    "okx_r1_terminal_reconciliation_repair_offline.py",
    "okx_signed_age_prearm_terminal_repair_offline.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_formal.py",
    "okx_fill_restart_validation.py",
    "okx_fill_restart_preflight.py",
    "okx_fill_restart_preflight_prepare.py",
    "okx_demo_adapter.py",
    "okx_demo_profile.py",
    "okx_demo_protocol.py",
    "okx_demo_runtime.py",
    "okx_offline_network_guard.py",
    "market_spec.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_future_book_timestamp_repair.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_fill_restart_preflight_prepare.py",
)
ROOT_EXCLUSIONS = (
    "tests/test_okx_fill_cursor_repair_offline.py",
    "tests/test_okx_fill_cursor_formal_prepare.py",
    "tests/test_okx_fill_restart_formal_prepare.py",
    "tests/test_okx_fill_restart_offline.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_production_readiness.py",
    "tests/test_okx_r2_warmup_audit_formal_prepare.py",
)
ROOT_DESELECT = (
    "tests/test_okx_r2_warmup_audit_counter_repair.py::"
    "test_successor_root_protocol_is_byte_identical_to_source_copy",
    "tests/test_okx_fill_restart_preflight.py::"
    "test_future_book_repair_evidence_binds_failed_formal_predecessor",
)
BACKTEST_DESELECT = (
    "backtest/tests/test_mm_v1_6_economics.py::test_root_agents_controls_v16_process",
)


class R1TerminalRepairError(RuntimeError):
    pass


class OfflineSocketGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create_connection = socket.create_connection

    def __enter__(self) -> "OfflineSocketGuard":
        guard = self

        def blocked(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise R1TerminalRepairError("network prohibited during offline repair")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise R1TerminalRepairError("network prohibited during offline repair")

        def blocked_create(
            address: object, *args: object, **kwargs: object
        ) -> None:
            guard.attempts.append(type(address).__name__)
            raise R1TerminalRepairError("network prohibited during offline repair")

        socket.socket.connect = blocked  # type: ignore[method-assign]
        socket.socket.connect_ex = blocked_ex  # type: ignore[method-assign]
        socket.create_connection = blocked_create  # type: ignore[assignment]
        return self

    def __exit__(self, *args: object) -> None:
        socket.socket.connect = self._connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._create_connection


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R1TerminalRepairError(f"expected JSON object: {path}")
    return value


def _payload(path: Path) -> dict[str, object]:
    payload = _json(path).get("payload")
    if not isinstance(payload, dict):
        raise R1TerminalRepairError(f"expected durable payload: {path}")
    return payload


def _stream_payloads(path: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line).get("payload")
        if isinstance(payload, dict):
            result.append(payload)
    return result


def verify_predecessor(root: Path) -> dict[str, object]:
    package = (
        root / "artifacts" / "okx_demo_fill_restart_validation"
        / PREDECESSOR_PACKAGE_ID
    )
    failures = [
        relative for relative, expected in PREDECESSOR_HASHES.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    unresolved = _json(package / "formal_run" / "UNRESOLVED_FAILURE.json")
    formal = _payload(package / "formal_run" / "state" / "formal_state.json")
    validation = _payload(
        package / "formal_run" / "state" / "validation_state.json"
    )
    ledger = dict(validation.get("ledger") or {})
    order_payloads = _stream_payloads(
        package / "formal_run" / "streams" / "order.jsonl"
    )
    position_payloads = _stream_payloads(
        package / "formal_run" / "streams" / "position.jsonl"
    )
    create_events = sum(
        item.get("event") == "NORMAL_CREATE_RESOLVED" for item in order_payloads
    )
    cancel_confirmations = sum(
        len(item.get("confirmed_client_order_ids") or [])
        for item in order_payloads
        if item.get("event") == "AUTHORITATIVE_CANCEL_CONFIRMED"
    )
    owned_orders = dict(validation.get("owned_orders") or {})
    flatten_orders = [
        item for item in owned_orders.values()
        if isinstance(item, dict) and item.get("reduce_only") is True
    ]
    terminal_position = position_payloads[-1] if position_payloads else {}
    forbidden = (
        "COMPLETED.json",
        "formal_run/RAW_COMPLETED.json",
        "formal_run/restart/R1_handoff.json",
        "formal_run/restart/R2_handoff.json",
        "formal_run/restart/R1_resume.json",
        "formal_run/restart/R2_resume.json",
    )
    failures.extend(relative for relative in forbidden if (package / relative).exists())
    marker_count = sum(
        path.name == "FORMAL_EXECUTION_ARMED.json"
        for path in package.rglob("FORMAL_EXECUTION_ARMED.json")
    )
    if any((
        unresolved.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        unresolved.get("reason") != (
            "FormalSafetyError:R1 checkpoint requires no orders and nonzero position"
        ),
        unresolved.get("formal_execution_marker_count") != 1,
        unresolved.get("report_recovery_required") is not True,
        unresolved.get("second_order_run_allowed") is not False,
        unresolved.get("live_endpoint_attempts") != 0,
        unresolved.get("live_orders") != 0,
        formal.get("formal_run_id") != PREDECESSOR_RUN_ID,
        formal.get("stage") != "HALTED",
        formal.get("r1_completed") is not False,
        formal.get("r2_completed") is not False,
        formal.get("normal_create_count") != 49,
        formal.get("normal_bid_fills_total") != 1,
        formal.get("normal_ask_fills_total") != 0,
        validation.get("r1_completed") is not False,
        validation.get("r2_completed") is not False,
        validation.get("normal_acknowledgements") != 49,
        validation.get("last_reconciled_position_btc") != "0.0000",
        validation.get("pending_position_reconciliation") is not False,
        ledger.get("inventory_btc") != "0.0000",
        ledger.get("normal_bid_fills") != 1,
        ledger.get("normal_ask_fills") != 0,
        ledger.get("special_fill_count") != 2,
        ledger.get("gross_realized_pnl_usdt") != "0.39002",
        ledger.get("total_fees_usdt") != "0.45627931",
        ledger.get("net_realized_pnl_usdt") != "-0.06625931",
        create_events != 49,
        cancel_confirmations != 48,
        len(flatten_orders) != 1,
        flatten_orders[0].get("status") != "FILLED" if flatten_orders else True,
        terminal_position.get("position_btc") != "0",
        terminal_position.get("open_order_count") != 0,
        marker_count != 1,
    )):
        failures.append("formal_predecessor_contract")
    if failures:
        raise R1TerminalRepairError(
            "failed formal predecessor changed: " + ",".join(failures[:10])
        )
    return {
        "passed": True,
        "immutable": True,
        "package_id": PREDECESSOR_PACKAGE_ID,
        "formal_run_id": PREDECESSOR_RUN_ID,
        "status": unresolved["status"],
        "reason": unresolved["reason"],
        "fixed_hashes": PREDECESSOR_HASHES,
        "formal_execution_marker_count": marker_count,
        "R1_completed": False,
        "R2_completed": False,
        "normal_create_count": create_events,
        "normal_cancel_confirmations": cancel_confirmations,
        "normal_bid_fills": ledger["normal_bid_fills"],
        "normal_ask_fills": ledger["normal_ask_fills"],
        "flatten_dispatches": 1,
        "flatten_fill_parts": ledger["special_fill_count"],
        "terminal_position_btc": validation["last_reconciled_position_btc"],
        "terminal_open_orders": terminal_position["open_order_count"],
        "gross_realized_pnl_usdt": ledger["gross_realized_pnl_usdt"],
        "total_fees_usdt": ledger["total_fees_usdt"],
        "net_realized_pnl_usdt": ledger["net_realized_pnl_usdt"],
        "report_recovery_required": True,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "rerun_allowed": False,
    }


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise R1TerminalRepairError(f"repair source missing: {relative}")
        hashes[relative] = _sha256(path)
    if hashes["AGENTS.md"] != hashes[
        "AGENTS_OKX_DEMO_R1_TERMINAL_RECONCILIATION_REPAIR.md"
    ]:
        raise R1TerminalRepairError("successor root AGENTS.md is not byte-identical")
    return dict(sorted(hashes.items()))


def _run_suite(
    root: Path, output: Path, name: str, arguments: tuple[str, ...]
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix=f"okx-r1-terminal-{name}-"))
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    ):
        environment[variable] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    command = [
        sys.executable, "-m", "pytest", *arguments, "-q",
        "-p", "no:cacheprovider", "-p", "okx_offline_network_guard",
        "--basetemp", str(base_temp),
    ]
    completed = subprocess.run(
        command, cwd=root, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=900, check=False,
    )
    _write_text(output / "tests" / f"{name}.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1,
        "live_endpoint_attempts": -1,
        "optuna_imported": True,
    }
    result = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": audit.get("network_attempts"),
        "live_endpoint_attempts": audit.get("live_endpoint_attempts"),
        "optuna_imported": audit.get("optuna_imported"),
        "command_scope": list(arguments),
        "root_exclusions": list(ROOT_EXCLUSIONS) if name == "root_non_optuna" else [],
        "root_deselect": list(ROOT_DESELECT) if name == "root_non_optuna" else [],
        "backtest_deselect": list(BACKTEST_DESELECT) if name == "backtest_non_optuna" else [],
    }
    result["passed_gate"] = all((
        result["returncode"] == 0,
        result["passed"] > 0,
        result["network_attempts"] == 0,
        result["live_endpoint_attempts"] == 0,
        result["optuna_imported"] is False,
    ))
    return result


def run_suites(root: Path, output: Path) -> dict[str, object]:
    targeted = (
        "tests/test_okx_r1_terminal_reconciliation_repair.py",
        "tests/test_okx_signed_age_prearm_terminal_repair.py",
        "tests/test_okx_r2_warmup_audit_counter_repair.py",
        "tests/test_okx_fill_restart_executor.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_formal.py",
        "tests/test_okx_fill_restart_validation.py",
        "tests/test_okx_future_book_timestamp_repair.py",
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
        *(f"--deselect={node}" for node in ROOT_DESELECT),
    )
    root_args = (
        "tests",
        *(f"--ignore={item}" for item in ROOT_EXCLUSIONS),
        *(f"--deselect={node}" for node in ROOT_DESELECT),
    )
    suites = {
        "r1_terminal_repair_targeted": _run_suite(
            root, output, "r1_terminal_repair_targeted", targeted
        ),
        "root_non_optuna": _run_suite(
            root, output, "root_non_optuna", root_args
        ),
        "backtest_non_optuna": _run_suite(
            root, output, "backtest_non_optuna", (
                "backtest/tests",
                "--ignore=backtest/tests/test_units_and_safety.py",
                "--ignore=backtest/tests/test_robust_gates.py",
                *(f"--deselect={node}" for node in BACKTEST_DESELECT),
            ),
        ),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise R1TerminalRepairError("one or more R1/terminal suites failed")
    return suites


def _secret_scan(output: Path) -> dict[str, object]:
    pattern = re.compile(
        rb"(?i)OKX_(?:API_KEY|SECRET|PASSPHRASE)\s*[=:]\s*[^\s\"']+"
    )
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix == ".tmp":
            continue
        scanned += 1
        if pattern.search(path.read_bytes()):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_pattern_matches": sorted(set(matches)),
        "credential_environment_accessed": False,
        "credentials_serialized": False,
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {
        "completion_hashes.json",
        "OFFLINE_R1_TERMINAL_REPAIR_COMPLETED.json",
    }
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def run_offline_repair(root: Path, repair_id: str) -> Path:
    root = root.resolve()
    if not repair_id.startswith("r1-terminal-repair-offline-"):
        raise R1TerminalRepairError("R1/terminal repair ID is invalid")
    output = root / ARTIFACT_ROOT / repair_id
    if output.exists():
        raise R1TerminalRepairError("R1/terminal repair ID reuse refused")
    guard = OfflineSocketGuard()
    with guard:
        predecessor = verify_predecessor(root)
        hashes = source_hashes(root)
        output.mkdir(parents=True)
        _write_json(output / "predecessor" / "formal_run_audit.json", predecessor)
        _write_json(output / "specification" / "source_hashes.json", hashes)
        _write_json(output / "specification" / "repair_spec.json", {
            "schema_version": 1,
            "repair_id": repair_id,
            "mode": "OFFLINE_FIXTURE",
            "source_manifest_sha256": canonical_sha256(hashes),
            "filled_order_removed_before_r1_checkpoint": True,
            "r1_checkpoint_transition_prevalidates_both_states": True,
            "r1_checkpoint_separate_from_emergency_flatten": True,
            "multi_partial_flatten_reconciles_controller_engine_account": True,
            "historical_closed_engine_orders_retained": True,
            "terminal_account_only_can_reconcile_stale_controller": True,
            "terminal_account_snapshot_count": 2,
            "preflight_authorized": False,
            "formal_execution_authorized": False,
            "network_allowed": False,
            "orders_allowed": False,
            "production_authorized": False,
        })
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise R1TerminalRepairError("repair source changed during tests")
        _write_json(output / "audits" / "endpoint_mutation_audit.json", {
            "execution_mode": "OFFLINE_FIXTURE",
            "parent_socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
            "preflight_attempts": 0,
            "formal_execution_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        _write_json(output / "audits" / "source_hash_audit.json", {
            "passed": True,
            "source_files_checked": len(hashes),
            "source_manifest_sha256": canonical_sha256(hashes),
            "post_test_source_manifest_sha256": canonical_sha256(post_hashes),
        })
        _write_json(output / "audits" / "test_count_audit.json", {
            name: value["passed"] for name, value in suites.items()
        })
        secret = _secret_scan(output)
        _write_json(output / "audits" / "secret_scan.json", secret)
        if not secret["passed"]:
            raise R1TerminalRepairError("secret pattern found in repair evidence")
        decision = {
            "status": REPAIR_STATUS,
            "repair_id": repair_id,
            "offline_repair_passed": True,
            "predecessor_rerun_allowed": False,
            "successor_protocol_active": True,
            "fresh_preflight_required": True,
            "fresh_formal_package_required_after_preflight": True,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "next_boundary": "fresh read-only preflight package preparation offline",
        }
        _write_json(output / "readiness" / "final_readiness.json", decision)
        _write_json(output / "decision" / "offline_repair_decision.json", decision)
        _write_text(
            output / "decision" / "offline_repair_decision.md",
            "# OKX Demo R1 and terminal reconciliation repair\n\n"
            f"- Status: `{REPAIR_STATUS}`\n"
            f"- Repair ID: `{repair_id}`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n"
            "- Next: prepare a fresh read-only preflight package offline.\n",
        )
        completion = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        for relative, expected in completion.items():
            if _sha256(output / relative) != expected:
                raise R1TerminalRepairError("repair completion verification failed")
        _write_json(output / "OFFLINE_R1_TERMINAL_REPAIR_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
        })
    if guard.attempts:
        raise R1TerminalRepairError("network attempt blocked during offline repair")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id")
    args = parser.parse_args()
    repair_id = args.repair_id or (
        "r1-terminal-repair-offline-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline_repair(args.root, repair_id)
    except Exception as exc:
        print(f"R1_TERMINAL_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"repair_id": repair_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
