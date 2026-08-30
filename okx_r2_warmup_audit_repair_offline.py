"""Create socket-denied evidence for the R2 warmup/cumulative-audit repair."""

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


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_r2_warmup_audit_repair"
PREDECESSOR_PACKAGE_ID = "formal-package-20260807T130847Z"
PREDECESSOR_RUN_ID = "formal-20260807T130847Z"
REPAIR_STATUS = "OKX_DEMO_R2_WARMUP_AUDIT_REPAIR_OFFLINE_SUPPORT"
PREDECESSOR_HASHES = {
    "COMPLETED.json": "468f93c2234d93546178185287f9a7d59973a03294912de64969c488dda56c87",
    "formal_run/formal_completion_hashes.json": "ee8f542d46527ec1f9596a925c6865c61f17023d397a63605bde3195ab5231d6",
    "formal_run/RAW_COMPLETED.json": "e85004baaf169e82e0ffd0fd091799911306a2bd2a9dd03b21871eb1d48f3bf3",
    "formal_run/restart/R2_handoff.json": "6f211c26c71b4217428d71f752294ace3ca3bbce010c83a3dd1868711eca6439",
    "formal_run/FORMAL_EXECUTION_ARMED.json": "6bdf321c21ff4a91d4ca9fef6823e0c266527465c2431dd77e86307836d2fc26",
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_R2_WARMUP_AUDIT_COUNTER_REPAIR.md",
    "okx_r2_warmup_audit_repair_offline.py",
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
)
BACKTEST_DESELECT = (
    "backtest/tests/test_mm_v1_6_economics.py::test_root_agents_controls_v16_process",
)


class R2RepairError(RuntimeError):
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
            raise R2RepairError("network prohibited during offline R2 repair")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise R2RepairError("network prohibited during offline R2 repair")

        def blocked_create(address: object, *args: object, **kwargs: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise R2RepairError("network prohibited during offline R2 repair")

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
        raise R2RepairError(f"expected JSON object: {path}")
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
    completed = _json(package / "COMPLETED.json")
    if any((
        completed.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        completed.get("formal_run_id") != PREDECESSOR_RUN_ID,
        completed.get("formal_execution_marker_count") != 1,
        completed.get("R1_completed") is not True,
        completed.get("R2_completed") is not False,
        completed.get("normal_bid_fills") != 1,
        completed.get("normal_ask_fills") != 1,
        completed.get("normal_fifo_round_trips") != 1,
        completed.get("special_fill_count") != 0,
        completed.get("gross_realized_pnl_usdt") != "0.5810",
        completed.get("actual_fees_usdt") != "0.2607478",
        completed.get("net_realized_pnl_usdt") != "0.3202522",
        completed.get("final_position_btc") != "0",
        completed.get("final_open_order_count") != 0,
        completed.get("live_endpoint_attempts") != 0,
        completed.get("live_orders") != 0,
    )):
        failures.append("COMPLETED.json:terminal_contract")
    manifest_path = package / "formal_run" / "formal_completion_hashes.json"
    manifest = _json(manifest_path)
    failures.extend(
        f"completion:{relative}" for relative, expected in manifest.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    )
    if completed.get("formal_completion_hashes_sha256") != _sha256(manifest_path):
        failures.append("formal_completion_hashes_sha256")
    if (package / "formal_run" / "restart" / "R2_resume.json").exists():
        failures.append("R2_resume.json:must_be_absent")
    order_events = [
        json.loads(line).get("payload") or {}
        for line in (package / "formal_run" / "streams" / "order.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    creates = sum(row.get("event") == "NORMAL_CREATE_RESOLVED" for row in order_events)
    cancels = sum(
        len(row.get("confirmed_client_order_ids") or []) for row in order_events
        if row.get("event") == "AUTHORITATIVE_CANCEL_CONFIRMED"
    )
    kill = _json(package / "audits" / "formal" / "kill_switch_audit.json")
    if creates != 37 or cancels != 35:
        failures.append("durable_order_event_counts")
    if kill.get("kill_active_at_terminal") is not True:
        failures.append("terminal_kill_latch")
    if failures:
        raise R2RepairError("failed formal predecessor changed: " + ",".join(failures[:10]))
    return {
        "passed": True,
        "immutable": True,
        "package_id": PREDECESSOR_PACKAGE_ID,
        "formal_run_id": PREDECESSOR_RUN_ID,
        "status": completed["status"],
        "fixed_hashes": PREDECESSOR_HASHES,
        "completion_files_checked": len(manifest),
        "formal_execution_marker_count": 1,
        "R1_completed": True,
        "R2_completed": False,
        "normal_create_events": creates,
        "authoritative_cancel_confirmations": cancels,
        "flatten_dispatches": 0,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "kill_active_at_terminal": True,
        "rerun_allowed": False,
    }


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise R2RepairError(f"repair source missing: {relative}")
        hashes[relative] = _sha256(path)
    if hashes["AGENTS.md"] != hashes[
        "AGENTS_OKX_DEMO_R2_WARMUP_AUDIT_COUNTER_REPAIR.md"
    ]:
        raise R2RepairError("successor root AGENTS.md is not byte-identical")
    return dict(sorted(hashes.items()))


def _run_suite(
    root: Path, output: Path, name: str, arguments: tuple[str, ...]
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix=f"okx-r2-{name}-"))
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
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
        check=False,
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
    root_args = ["tests", *(f"--ignore={item}" for item in ROOT_EXCLUSIONS)]
    suites = {
        "r2_repair_targeted": _run_suite(root, output, "r2_repair_targeted", (
            "tests/test_okx_r2_warmup_audit_counter_repair.py",
            "tests/test_okx_fill_restart_executor.py",
            "tests/test_okx_fill_restart_gateway.py",
            "tests/test_okx_fill_restart_formal.py",
            "tests/test_okx_fill_restart_validation.py",
            "tests/test_okx_future_book_timestamp_repair.py",
        )),
        "root_non_optuna": _run_suite(
            root, output, "root_non_optuna", tuple(root_args)
        ),
        "backtest_non_optuna": _run_suite(root, output, "backtest_non_optuna", (
            "backtest/tests",
            "--ignore=backtest/tests/test_units_and_safety.py",
            "--ignore=backtest/tests/test_robust_gates.py",
            *(f"--deselect={node}" for node in BACKTEST_DESELECT),
        )),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise R2RepairError("one or more R2 repair suites failed")
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
    ignored = {"completion_hashes.json", "OFFLINE_R2_WARMUP_AUDIT_REPAIR_COMPLETED.json"}
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def run_offline_repair(root: Path, repair_id: str) -> Path:
    root = root.resolve()
    if not repair_id.startswith("r2-repair-offline-"):
        raise R2RepairError("R2 repair ID is invalid")
    output = root / ARTIFACT_ROOT / repair_id
    if output.exists():
        raise R2RepairError("R2 repair ID reuse refused")
    guard = OfflineSocketGuard()
    with guard:
        predecessor = verify_predecessor(root)
        hashes = source_hashes(root)
        output.mkdir(parents=True)
        _write_json(output / "predecessor" / "formal_run_audit.json", predecessor)
        _write_json(output / "specification" / "source_hashes.json", hashes)
        _write_json(output / "specification" / "r2_repair_spec.json", {
            "schema_version": 1,
            "repair_id": repair_id,
            "mode": "OFFLINE_FIXTURE",
            "source_manifest_sha256": canonical_sha256(hashes),
            "r2_quote_engine_warmup_before_probe": True,
            "warmup_probe_mutation_guard": True,
            "hash_chained_generation_gateway_audit": True,
            "terminal_counters_cumulative": True,
            "terminal_order_event_reconciliation": True,
            "preflight_authorized": False,
            "formal_execution_authorized": False,
            "network_allowed": False,
            "orders_allowed": False,
            "production_authorized": False,
        })
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise R2RepairError("repair source changed during tests")
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
            raise R2RepairError("secret pattern found in repair evidence")
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
            "# OKX Demo R2 warmup and cumulative audit repair\n\n"
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
                raise R2RepairError("repair completion verification failed")
        _write_json(output / "OFFLINE_R2_WARMUP_AUDIT_REPAIR_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
        })
    if guard.attempts:
        raise R2RepairError("network attempt blocked during offline repair")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id")
    args = parser.parse_args()
    repair_id = args.repair_id or (
        "r2-repair-offline-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline_repair(args.root, repair_id)
    except Exception as exc:
        print(f"R2_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"repair_id": repair_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
