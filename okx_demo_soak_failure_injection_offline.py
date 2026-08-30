"""Create socket-denied readiness evidence for a future bounded Demo soak."""

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

from okx_demo_soak_failure_injection import scenario_matrix
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_validation import canonical_sha256


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_soak_failure_injection"
PREDECESSOR_PACKAGE_ID = "formal-package-20260810T123953Z"
PREDECESSOR_RUN_ID = "formal-20260810T123953Z"
READY_STATUS = "OKX_DEMO_SOAK_FAILURE_INJECTION_OFFLINE_SUPPORT"
PREDECESSOR_HASHES = {
    "formal_run/FORMAL_EXECUTION_ARMED.json": (
        "2f28453da64775809c8b5485c38e40df1ae324e2f1f15c6917ed66bc652c3d10"
    ),
    "COMPLETED.json": (
        "f62a17a606b7c4826f862ee6ae0cab7ef35d224c9f08bfc13234aa188f12b11e"
    ),
    "formal_run/state/formal_state.json": (
        "9e11edb821fc396277eaf541011987123ae374fc8833fdcfb746692037baa1ab"
    ),
    "formal_run/state/validation_state.json": (
        "a9dd54551e08f1b7b69783debe954b55a983b4bcf7685beeb5f5a773816b74bb"
    ),
    "formal_run/streams/gateway_audit.jsonl": (
        "5de023e3e3a215d4d3d39ca309f02fb3ff1d403764681382130d4a5c86ad60c5"
    ),
    "formal_run/formal_completion_hashes.json": (
        "3277fe669bbe22cbb725d64fd6bc02b3aa2eb5198e7f1487186b5d060381ad37"
    ),
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_BOUNDED_SOAK_EXECUTION.md",
    "okx_demo_soak_failure_injection.py",
    "okx_demo_soak_failure_injection_offline.py",
    "okx_demo_soak_executor.py",
    "okx_demo_soak_prepare.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_formal.py",
    "okx_fill_restart_validation.py",
    "okx_fill_restart_preflight.py",
    "okx_fill_restart_preflight_prepare.py",
    "okx_demo_runtime.py",
    "okx_offline_network_guard.py",
    "tests/test_okx_demo_soak_failure_injection.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_future_book_timestamp_repair.py",
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
)
BACKTEST_DESELECT = (
    "backtest/tests/test_mm_v1_6_economics.py::test_root_agents_controls_v16_process",
)


class SoakOfflineError(RuntimeError):
    pass


class SocketGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create = socket.create_connection

    def __enter__(self) -> "SocketGuard":
        guard = self

        def blocked(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise SoakOfflineError("network prohibited during soak offline fixtures")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            blocked(instance, address)
            return 1

        def blocked_create(address: object, *args: object, **kwargs: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise SoakOfflineError("network prohibited during soak offline fixtures")

        socket.socket.connect = blocked  # type: ignore[method-assign]
        socket.socket.connect_ex = blocked_ex  # type: ignore[method-assign]
        socket.create_connection = blocked_create  # type: ignore[assignment]
        return self

    def __exit__(self, *args: object) -> None:
        socket.socket.connect = self._connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._create


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SoakOfflineError(f"expected JSON object: {path}")
    return value


def _payload(path: Path) -> dict[str, object]:
    value = _json(path).get("payload")
    if not isinstance(value, dict):
        raise SoakOfflineError(f"expected durable payload: {path}")
    return value


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
    terminal = _json(package / "COMPLETED.json")
    formal = _payload(package / "formal_run/state/formal_state.json")
    validation = _payload(package / "formal_run/state/validation_state.json")
    endpoint = _json(package / "audits/formal/endpoint_audit.json")
    manifest = _json(package / "formal_run/formal_completion_hashes.json")
    manifest_failures = [
        relative for relative, expected in manifest.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    failures.extend(manifest_failures)
    marker_count = sum(
        path.name == "FORMAL_EXECUTION_ARMED.json"
        for path in package.rglob("FORMAL_EXECUTION_ARMED.json")
    )
    open_engine = [
        item for item in dict(validation.get("owned_orders") or {}).values()
        if isinstance(item, dict)
        and item.get("status") in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}
    ]
    ledger = dict(validation.get("ledger") or {})
    if any((
        terminal.get("status") != "OKX_DEMO_FILL_RESTART_SUPPORT",
        terminal.get("formal_run_id") != PREDECESSOR_RUN_ID,
        terminal.get("formal_execution_marker_count") != 1,
        terminal.get("R1_completed") is not True,
        terminal.get("R2_completed") is not True,
        terminal.get("normal_bid_fills") != 1,
        terminal.get("normal_ask_fills") != 1,
        terminal.get("normal_fifo_round_trips") != 1,
        terminal.get("special_fill_count") != 0,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_order_count") != 0,
        terminal.get("gross_realized_pnl_usdt") != "0.4960",
        terminal.get("actual_fees_usdt") != "0.2598552",
        terminal.get("net_realized_pnl_usdt") != "0.2361448",
        terminal.get("live_endpoint_attempts") != 0,
        terminal.get("live_orders") != 0,
        formal.get("stage") != "COMPLETE",
        formal.get("process_generation") != 2,
        formal.get("position_btc") != "0",
        formal.get("owned_orders") != {},
        formal.get("pending_intents") != {},
        formal.get("r1_completed") is not True,
        formal.get("r2_completed") is not True,
        validation.get("phase") != "RUNNING_AFTER_R2",
        validation.get("r1_completed") is not True,
        validation.get("r2_completed") is not True,
        validation.get("pending_position_reconciliation") is not False,
        validation.get("kill_latch", {}).get("active") is not False,
        ledger.get("inventory_btc") != "0.000",
        ledger.get("normal_bid_fills") != 1,
        ledger.get("normal_ask_fills") != 1,
        len(ledger.get("normal_round_trips") or []) != 1,
        bool(open_engine),
        endpoint.get("mutation_method_counts", {}).get("create_order") != 44,
        endpoint.get("mutation_method_counts", {}).get("cancel_order") != 42,
        endpoint.get("flatten_dispatches") != 0,
        endpoint.get("process_generations") != [0, 1, 2],
        endpoint.get("live_endpoint_attempts") != 0,
        endpoint.get("live_orders") != 0,
        marker_count != 1,
        (package / "formal_run/UNRESOLVED_FAILURE.json").exists(),
    )):
        failures.append("successful_formal_contract")
    if failures:
        raise SoakOfflineError(
            "successful predecessor changed: " + ",".join(failures[:10])
        )
    return {
        "passed": True,
        "immutable": True,
        "package_id": PREDECESSOR_PACKAGE_ID,
        "formal_run_id": PREDECESSOR_RUN_ID,
        "status": terminal["status"],
        "fixed_hashes": PREDECESSOR_HASHES,
        "formal_completion_files_checked": len(manifest),
        "formal_execution_marker_count": marker_count,
        "R1_completed": True,
        "R2_completed": True,
        "process_generations": [0, 1, 2],
        "normal_create_count": 44,
        "normal_cancel_count": 42,
        "normal_bid_fills": 1,
        "normal_ask_fills": 1,
        "normal_fifo_round_trips": 1,
        "flatten_dispatches": 0,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "gross_realized_pnl_usdt": "0.4960",
        "actual_fees_usdt": "0.2598552",
        "net_realized_pnl_usdt": "0.2361448",
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "rerun_allowed": False,
    }


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise SoakOfflineError(f"source missing: {relative}")
        hashes[relative] = _sha256(path)
    if hashes["AGENTS.md"] != hashes[
        "AGENTS_OKX_DEMO_BOUNDED_SOAK_EXECUTION.md"
    ]:
        raise SoakOfflineError("root AGENTS is not byte-identical to successor")
    return dict(sorted(hashes.items()))


def _run_suite(
    root: Path, output: Path, name: str, arguments: tuple[str, ...]
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    temp = Path(tempfile.mkdtemp(prefix=f"okx-soak-failure-{name}-"))
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
        "--basetemp", str(temp),
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
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_soak_failure_injection.py",
        "tests/test_okx_r1_terminal_reconciliation_repair.py",
        "tests/test_okx_signed_age_prearm_terminal_repair.py",
        "tests/test_okx_r2_warmup_audit_counter_repair.py",
        "tests/test_okx_fill_restart_executor.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_formal.py",
        "tests/test_okx_fill_restart_validation.py",
        "tests/test_okx_future_book_timestamp_repair.py",
        *(f"--deselect={node}" for node in ROOT_DESELECT),
    )
    root_args = (
        "tests",
        *(f"--ignore={item}" for item in ROOT_EXCLUSIONS),
        *(f"--deselect={node}" for node in ROOT_DESELECT),
    )
    suites = {
        "soak_failure_targeted": _run_suite(
            root, output, "soak_failure_targeted", targeted
        ),
        "root_non_optuna": _run_suite(root, output, "root_non_optuna", root_args),
        "backtest_non_optuna": _run_suite(root, output, "backtest_non_optuna", (
            "backtest/tests",
            "--ignore=backtest/tests/test_units_and_safety.py",
            "--ignore=backtest/tests/test_robust_gates.py",
            *(f"--deselect={node}" for node in BACKTEST_DESELECT),
        )),
    }
    _write_json(output / "tests/test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise SoakOfflineError("one or more offline suites failed")
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
    ignored = {"completion_hashes.json", "OFFLINE_SOAK_FAILURE_COMPLETED.json"}
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def run_offline(root: Path, repair_id: str) -> Path:
    root = root.resolve()
    if not repair_id.startswith("soak-failure-offline-"):
        raise SoakOfflineError("offline evidence ID is invalid")
    output = root / ARTIFACT_ROOT / repair_id
    if output.exists():
        raise SoakOfflineError("offline evidence ID reuse refused")
    guard = SocketGuard()
    with guard:
        predecessor = verify_predecessor(root)
        hashes = source_hashes(root)
        output.mkdir(parents=True)
        _write_json(output / "predecessor/successful_formal_audit.json", predecessor)
        _write_json(output / "specification/source_hashes.json", hashes)
        _write_json(output / "specification/failure_scenario_matrix.json", {
            "schema_version": 1,
            "scenarios": list(scenario_matrix()),
            "network_allowed": False,
            "preflight_authorized": False,
            "formal_authorized": False,
            "soak_authorized": False,
            "production_authorized": False,
        })
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise SoakOfflineError("source changed during offline suites")
        _write_json(output / "audits/endpoint_mutation_audit.json", {
            "mode": "OFFLINE_FIXTURE",
            "parent_socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
            "preflight_preparations": 0,
            "preflight_attempts": 0,
            "formal_attempts": 0,
            "soak_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        _write_json(output / "audits/source_hash_audit.json", {
            "passed": True,
            "source_files_checked": len(hashes),
            "source_manifest_sha256": canonical_sha256(hashes),
            "post_test_source_manifest_sha256": canonical_sha256(post_hashes),
        })
        _write_json(output / "audits/test_count_audit.json", {
            name: value["passed"] for name, value in suites.items()
        })
        secret = _secret_scan(output)
        _write_json(output / "audits/secret_scan.json", secret)
        if not secret["passed"]:
            raise SoakOfflineError("secret pattern found in offline evidence")
        decision = {
            "status": READY_STATUS,
            "repair_id": repair_id,
            "offline_passed": True,
            "successful_predecessor_immutable": True,
            "successor_protocol_active": True,
            "fresh_preflight_required": True,
            "preflight_prepared": False,
            "preflight_executed": False,
            "formal_executed": False,
            "soak_executed": False,
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
            "next_boundary": "prepare fresh read-only preflight identifiers offline",
        }
        _write_json(output / "decision/offline_decision.json", decision)
        _write_text(
            output / "decision/offline_decision.md",
            "# OKX Demo bounded-soak failure-injection readiness\n\n"
            f"- Status: `{READY_STATUS}`\n"
            f"- Evidence ID: `{repair_id}`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n"
            "- Preflight/formal/soak executed: `false / false / false`\n",
        )
        completion = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        _write_json(output / "OFFLINE_SOAK_FAILURE_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
        })
    if guard.attempts:
        raise SoakOfflineError("network attempt blocked during offline evidence")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id")
    args = parser.parse_args()
    repair_id = args.repair_id or (
        "soak-failure-offline-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline(args.root, repair_id)
    except Exception as exc:
        print(f"SOAK_FAILURE_OFFLINE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"repair_id": repair_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
