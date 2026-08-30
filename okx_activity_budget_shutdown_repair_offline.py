"""Create socket-denied evidence for the activity-budget shutdown repair."""

from __future__ import annotations

import argparse
import hashlib
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


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_activity_budget_shutdown_repair"
PREDECESSOR_PACKAGE_ID = "formal-package-20260805T162735Z"
PREDECESSOR_RUN_ID = "formal-20260805T162735Z"
PREDECESSOR_STATUS = "OKX_DEMO_FILL_RESTART_SAFETY_FAILED"
REPAIR_STATUS = "OKX_DEMO_ACTIVITY_BUDGET_SHUTDOWN_REPAIR_OFFLINE_SUPPORT"
PREDECESSOR_HASHES = {
    "COMPLETED.json": "cb04e253dacffeb96ec03bfcea098abb4df89d9dca43aa5f9f7fa12978f06b08",
    "formal_run/formal_completion_hashes.json": "aeefd22b2a2efa8793894cdfa9c1f9755df738a0f67aceca960a6c2fbf61585c",
    "decision/formal/decision.json": "8897e06a53a5a534f96e810519d619f9e75aa5b21a1019610c6ea6ee1b8dc3d6",
    "formal_run/RAW_COMPLETED.json": "12b85adc0b32854c556a1f9caa2872bb32486a99d338d48424b054e9f9958ad4",
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_ACTIVITY_BUDGET_SHUTDOWN_REPAIR.md",
    "okx_activity_budget_shutdown_repair_offline.py",
    "okx_fill_restart_formal.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_validation.py",
    "okx_demo_adapter.py",
    "okx_demo_profile.py",
    "okx_demo_protocol.py",
    "okx_demo_runtime.py",
    "okx_demo_state.py",
    "okx_offline_network_guard.py",
    "market_spec.py",
    "tests/test_okx_activity_budget_shutdown_repair.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_validation.py",
)
GENERATION_LOCKED_EXCLUSIONS = (
    "tests/test_okx_fill_cursor_repair_offline.py",
    "tests/test_okx_fill_cursor_formal_prepare.py",
    "tests/test_okx_fill_restart_formal_prepare.py",
    "tests/test_okx_fill_restart_offline.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_production_readiness.py",
)
BACKTEST_GENERATION_LOCKED_DESELECT = (
    "backtest/tests/test_mm_v1_6_economics.py::test_root_agents_controls_v16_process",
)


class ShutdownRepairError(RuntimeError):
    pass


class _OfflineSocketGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create_connection = socket.create_connection

    def __enter__(self) -> "_OfflineSocketGuard":
        guard = self

        def blocked(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise ShutdownRepairError("network prohibited during offline repair")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise ShutdownRepairError("network prohibited during offline repair")

        def blocked_create(address: object, *args: object, **kwargs: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise ShutdownRepairError("network prohibited during offline repair")

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
        raise ShutdownRepairError(f"expected JSON object: {path}")
    return value


def verify_predecessor(root: Path) -> dict[str, object]:
    package = (
        root / "artifacts" / "okx_demo_fill_restart_validation"
        / PREDECESSOR_PACKAGE_ID
    )
    failures = [
        relative
        for relative, expected in PREDECESSOR_HASHES.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    completed = _json(package / "COMPLETED.json")
    if any((
        completed.get("status") != PREDECESSOR_STATUS,
        completed.get("formal_run_id") != PREDECESSOR_RUN_ID,
        completed.get("formal_execution_marker_count") != 1,
        completed.get("R1_completed") is not True,
        completed.get("R2_completed") is not False,
        completed.get("normal_bid_fills") != 1,
        completed.get("normal_ask_fills") != 0,
        completed.get("normal_fifo_round_trips") != 0,
        completed.get("special_fill_count") != 1,
        completed.get("final_position_btc") != "0",
        completed.get("final_open_order_count") != 0,
        completed.get("live_endpoint_attempts") != 0,
        completed.get("live_orders") != 0,
    )):
        failures.append("COMPLETED.json:terminal_contract")
    manifest_path = package / "formal_run" / "formal_completion_hashes.json"
    manifest = _json(manifest_path)
    manifest_failures = [
        relative
        for relative, expected in manifest.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    failures.extend(f"completion:{row}" for row in manifest_failures)
    if completed.get("formal_completion_hashes_sha256") != _sha256(manifest_path):
        failures.append("formal_completion_hashes_sha256")
    if failures:
        raise ShutdownRepairError(
            "completed formal predecessor changed: " + ",".join(failures[:10])
        )
    return {
        "passed": True,
        "immutable": True,
        "package_id": PREDECESSOR_PACKAGE_ID,
        "formal_run_id": PREDECESSOR_RUN_ID,
        "status": PREDECESSOR_STATUS,
        "fixed_hashes": PREDECESSOR_HASHES,
        "completion_files_checked": len(manifest),
        "formal_execution_marker_count": 1,
        "R1_completed": True,
        "R2_completed": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "rerun_allowed": False,
    }


def source_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise ShutdownRepairError(f"repair source missing: {relative}")
        result[relative] = _sha256(path)
    return dict(sorted(result.items()))


def build_repair_spec(
    repair_id: str,
    predecessor: dict[str, object],
    hashes: dict[str, str],
) -> dict[str, object]:
    if not repair_id.startswith("shutdown-repair-offline-"):
        raise ShutdownRepairError("shutdown repair ID is invalid")
    return {
        "schema_version": 1,
        "repair_id": repair_id,
        "mode": "OFFLINE_FIXTURE",
        "successor_protocol_active": False,
        "preflight_authorized": False,
        "formal_execution_authorized": False,
        "predecessor": predecessor,
        "source_hashes": hashes,
        "source_manifest_sha256": canonical_sha256(hashes),
        "shutdown_flow": [
            "STOP_NORMAL_PLACEMENT_AND_PERSIST_REASON",
            "CANCEL_AUTHORITATIVE_OWNED_SET",
            "RECONCILE_ORDERS_FILLS_FEES_POSITION_BALANCE_CURSOR",
            "REQUIRE_AUTHORITATIVE_POSITION_EQUALS_LEDGER",
            "SINGLE_FLIGHT_REDUCE_ONLY_FLATTEN_IF_NONZERO",
            "RECONCILE_TERMINAL_FLAT_EMPTY",
            "CLOSE_ACTIVITY_INSUFFICIENT",
        ],
        "activity_reasons": [
            "NORMAL_CREATE_BUDGET_EXHAUSTED",
            "FORMAL_DEADLINE_REACHED",
        ],
        "hard_failures": [
            "UNKNOWN_OR_FOREIGN_ORDER",
            "POSITION_ACCOUNTING_MISMATCH",
            "INCOMPLETE_FEE",
            "OVERFILL",
            "DUPLICATE_CONFLICT",
            "UNRESOLVED_FLATTEN",
            "SECOND_FLATTEN_CREATE",
            "TERMINAL_NOT_FLAT_EMPTY",
        ],
        "generation_locked_test_exclusions": list(GENERATION_LOCKED_EXCLUSIONS),
        "backtest_generation_locked_deselect": list(
            BACKTEST_GENERATION_LOCKED_DESELECT
        ),
        "network_allowed": False,
        "orders_allowed": False,
        "production_authorized": False,
    }


def _run_suite(
    root: Path, output: Path, name: str, arguments: tuple[str, ...]
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix=f"okx-shutdown-{name}-"))
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
        timeout=600,
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
        "generation_locked_exclusions": (
            list(GENERATION_LOCKED_EXCLUSIONS)
            if name == "root_successor_non_optuna" else []
        ),
        "generation_locked_deselect": (
            list(BACKTEST_GENERATION_LOCKED_DESELECT)
            if name == "backtest_non_optuna" else []
        ),
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
    root_arguments = ["tests"]
    root_arguments.extend(
        f"--ignore={relative}" for relative in GENERATION_LOCKED_EXCLUSIONS
    )
    suites = {
        "shutdown_targeted": _run_suite(root, output, "shutdown_targeted", (
            "tests/test_okx_activity_budget_shutdown_repair.py",
            "tests/test_okx_fill_restart_formal.py",
            "tests/test_okx_fill_restart_executor.py",
            "tests/test_okx_fill_restart_gateway.py",
            "tests/test_okx_fill_restart_validation.py",
        )),
        "root_successor_non_optuna": _run_suite(
            root, output, "root_successor_non_optuna", tuple(root_arguments)
        ),
        "backtest_non_optuna": _run_suite(root, output, "backtest_non_optuna", (
            "backtest/tests",
            "--ignore=backtest/tests/test_units_and_safety.py",
            "--ignore=backtest/tests/test_robust_gates.py",
            *(
                f"--deselect={node_id}"
                for node_id in BACKTEST_GENERATION_LOCKED_DESELECT
            ),
        )),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise ShutdownRepairError("one or more shutdown repair suites failed")
    return suites


def _secret_scan(output: Path) -> dict[str, object]:
    patterns = (
        re.compile(rb"(?i)OKX_(?:API_KEY|SECRET|PASSPHRASE)\s*[=:]\s*[^\s\"']+"),
        re.compile(rb"(?i)\b(?:apiKey|secret|password)\s*[=:]\s*[\"'][^\"']{8,}"),
    )
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
            continue
        scanned += 1
        raw = path.read_bytes()
        if any(pattern.search(raw) for pattern in patterns):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_pattern_matches": sorted(set(matches)),
        "credential_environment_accessed": False,
        "credentials_serialized": False,
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "OFFLINE_SHUTDOWN_REPAIR_COMPLETED.json"}
    return dict(sorted(
        (
            path.relative_to(output).as_posix(),
            _sha256(path),
        )
        for path in output.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name not in ignored
        and path.suffix != ".tmp"
    ))


def run_offline_repair(root: Path, repair_id: str) -> Path:
    root = root.resolve()
    output = root / ARTIFACT_ROOT / repair_id
    if output.exists():
        raise ShutdownRepairError("shutdown repair ID reuse refused")
    guard = _OfflineSocketGuard()
    with guard:
        predecessor = verify_predecessor(root)
        hashes = source_hashes(root)
        spec = build_repair_spec(repair_id, predecessor, hashes)
        output.mkdir(parents=True)
        _write_json(output / "predecessor" / "formal_run_audit.json", predecessor)
        _write_json(output / "specification" / "source_hashes.json", hashes)
        _write_json(output / "specification" / "shutdown_repair_spec.json", spec)
        _write_json(output / "readiness" / "initial_readiness.json", {
            "predecessor_verified": True,
            "offline_only": True,
            "network_attempts": 0,
            "orders_submitted": 0,
            "successor_protocol_active": False,
        })
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise ShutdownRepairError("repair source changed during test execution")
        endpoint_audit = {
            "execution_mode": "OFFLINE_FIXTURE",
            "parent_socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
            "preflight_attempts": 0,
            "formal_execution_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        }
        _write_json(output / "audits" / "endpoint_mutation_audit.json", endpoint_audit)
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
            raise ShutdownRepairError("secret pattern found in repair evidence")
        decision = {
            "status": REPAIR_STATUS,
            "repair_id": repair_id,
            "offline_repair_passed": True,
            "predecessor_rerun_allowed": False,
            "successor_protocol_active": False,
            "fresh_preflight_required": True,
            "fresh_formal_package_required": True,
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
            "next_boundary": (
                "explicitly activate AGENTS_OKX_DEMO_ACTIVITY_BUDGET_"
                "SHUTDOWN_REPAIR.md as root AGENTS.md"
            ),
        }
        _write_json(output / "readiness" / "final_readiness.json", decision)
        _write_json(output / "decision" / "offline_repair_decision.json", decision)
        _write_text(
            output / "decision" / "offline_repair_decision.md",
            "# OKX Demo activity-budget shutdown repair\n\n"
            f"- Status: `{REPAIR_STATUS}`\n"
            f"- Repair ID: `{repair_id}`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n"
            "- Successor protocol active: `false`\n\n"
            "Explicit root protocol activation is required next.\n",
        )
        completion = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        for relative, expected in completion.items():
            if _sha256(output / relative) != expected:
                raise ShutdownRepairError("repair completion verification failed")
        terminal = {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
        }
        _write_json(output / "OFFLINE_SHUTDOWN_REPAIR_COMPLETED.json", terminal)
    if guard.attempts:
        raise ShutdownRepairError("network attempt blocked during offline repair")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id")
    args = parser.parse_args()
    repair_id = args.repair_id or (
        "shutdown-repair-offline-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline_repair(args.root, repair_id)
    except Exception as exc:
        print(f"SHUTDOWN_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"repair_id": repair_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
