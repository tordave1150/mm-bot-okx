"""Create socket-denied evidence for signed-age/pre-arm/terminal repair."""

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
    Path("artifacts") / "okx_demo_signed_age_prearm_terminal_repair"
)
PREDECESSOR_PACKAGE_ID = "formal-package-20260807T151814Z"
PREDECESSOR_RUN_ID = "formal-20260807T151814Z"
REPAIR_STATUS = "OKX_DEMO_SIGNED_AGE_PREARM_TERMINAL_REPAIR_OFFLINE_SUPPORT"
PREDECESSOR_HASHES = {
    "formal_run/FORMAL_EXECUTION_ARMED.json": (
        "187dc225d96eb2c7ebd346a93e69a06818a4bd966ad27fa725cd4b8644f5d97b"
    ),
    "formal_run/UNRESOLVED_FAILURE.json": (
        "7ba7fe37218fcc22e90334f5db7c519cf4e082fab7e748741b3ae83a3aec4ba4"
    ),
    "formal_run/state/formal_state.json": (
        "e74c802d987eae927732127c9ba9d8a60337e18c7212f00fe4d13402b8eb8676"
    ),
    "formal_run/state/validation_state.json": (
        "3a8c0cc7c2e4443ea0393469c7cce75ce1ee894a8f1d0bb999cdcbba22855fbe"
    ),
    "formal_run/streams/safety.jsonl": (
        "5bbf05e8585f6162fccf0642deb38be00a937630f6cd2210df7be766636c4703"
    ),
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_SIGNED_AGE_PREARM_TERMINAL_REPAIR.md",
    "okx_signed_age_prearm_terminal_repair_offline.py",
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


class SignedAgeRepairError(RuntimeError):
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
            raise SignedAgeRepairError("network prohibited during offline repair")

        def blocked_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise SignedAgeRepairError("network prohibited during offline repair")

        def blocked_create(
            address: object, *args: object, **kwargs: object
        ) -> None:
            guard.attempts.append(type(address).__name__)
            raise SignedAgeRepairError("network prohibited during offline repair")

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
        raise SignedAgeRepairError(f"expected JSON object: {path}")
    return value


def _payload(path: Path) -> dict[str, object]:
    wrapper = _json(path)
    payload = wrapper.get("payload")
    if not isinstance(payload, dict):
        raise SignedAgeRepairError(f"expected durable payload: {path}")
    return payload


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
    safety_lines = (
        package / "formal_run" / "streams" / "safety.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    safety_payloads = [json.loads(line).get("payload") or {} for line in safety_lines]
    forbidden = (
        "COMPLETED.json",
        "formal_run/RAW_COMPLETED.json",
        "formal_run/streams/order.jsonl",
        "formal_run/streams/gateway_audit.jsonl",
        "formal_run/restart/R1_handoff.json",
        "formal_run/restart/R2_handoff.json",
        "formal_run/restart/R1_resume.json",
        "formal_run/restart/R2_resume.json",
    )
    failures.extend(relative for relative in forbidden if (package / relative).exists())
    if any((
        unresolved.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        unresolved.get("reason") != "FormalSafetyError:MARKET_DATA_STALE",
        unresolved.get("formal_execution_marker_count") != 1,
        unresolved.get("report_recovery_required") is not True,
        unresolved.get("second_order_run_allowed") is not False,
        unresolved.get("live_endpoint_attempts") != 0,
        unresolved.get("live_orders") != 0,
        formal.get("formal_run_id") != PREDECESSOR_RUN_ID,
        formal.get("stage") != "HALTED",
        formal.get("r1_completed") is not False,
        formal.get("r2_completed") is not False,
        formal.get("normal_create_count") != 0,
        formal.get("flatten_submission_count") != 0,
        formal.get("position_btc") != "0",
        formal.get("owned_orders") != {},
        formal.get("pending_intents") != {},
        validation.get("external_order_submissions") != 0,
        validation.get("last_reconciled_position_btc") != "0",
        validation.get("owned_orders") != {},
        validation.get("r1_completed") is not False,
        validation.get("r2_completed") is not False,
        validation.get("ledger", {}).get("inventory_btc") != "0",
        len(safety_payloads) != 1,
        safety_payloads[0].get("event") != "FATAL_EXECUTOR_ERROR",
        safety_payloads[0].get("new_submissions_after_error") != 0,
    )):
        failures.append("formal_predecessor_contract")
    marker_count = sum(
        path.name == "FORMAL_EXECUTION_ARMED.json"
        for path in package.rglob("FORMAL_EXECUTION_ARMED.json")
    )
    if marker_count != 1:
        failures.append("formal_execution_marker_count")
    if failures:
        raise SignedAgeRepairError(
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
        "normal_create_count": 0,
        "flatten_dispatches": 0,
        "external_order_submissions": 0,
        "terminal_position_btc": "0",
        "terminal_owned_orders": 0,
        "terminal_pending_intents": 0,
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
            raise SignedAgeRepairError(f"repair source missing: {relative}")
        hashes[relative] = _sha256(path)
    if hashes["AGENTS.md"] != hashes[
        "AGENTS_OKX_DEMO_SIGNED_AGE_PREARM_TERMINAL_REPAIR.md"
    ]:
        raise SignedAgeRepairError("successor root AGENTS.md is not byte-identical")
    return dict(sorted(hashes.items()))


def _run_suite(
    root: Path, output: Path, name: str, arguments: tuple[str, ...]
) -> dict[str, object]:
    audit_path = output / "tests" / f"{name}_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix=f"okx-signed-age-{name}-"))
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
    root_args = [
        "tests",
        *(f"--ignore={item}" for item in ROOT_EXCLUSIONS),
        *(f"--deselect={node}" for node in ROOT_DESELECT),
    ]
    targeted = (
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
    suites = {
        "signed_age_repair_targeted": _run_suite(
            root, output, "signed_age_repair_targeted", targeted
        ),
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
        raise SignedAgeRepairError("one or more signed-age repair suites failed")
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
        "OFFLINE_SIGNED_AGE_REPAIR_COMPLETED.json",
    }
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def run_offline_repair(root: Path, repair_id: str) -> Path:
    root = root.resolve()
    if not repair_id.startswith("signed-age-repair-offline-"):
        raise SignedAgeRepairError("signed-age repair ID is invalid")
    output = root / ARTIFACT_ROOT / repair_id
    if output.exists():
        raise SignedAgeRepairError("signed-age repair ID reuse refused")
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
            "signed_age_minimum_inclusive": "-maximum_clock_skew_ms",
            "signed_age_maximum_inclusive": "maximum_market_age_ms",
            "positive_stale_prearm_retry_bounded": True,
            "future_beyond_clock_skew_retryable": False,
            "retry_after_controller_arm": False,
            "terminal_account_only_snapshot_count": 2,
            "terminal_account_only_requires_flat_empty": True,
            "market_freshness_required_for_terminal_reporting": False,
            "r2_warmup_and_cumulative_audit_preserved": True,
            "preflight_authorized": False,
            "formal_execution_authorized": False,
            "network_allowed": False,
            "orders_allowed": False,
            "production_authorized": False,
        })
        suites = run_suites(root, output)
        post_hashes = source_hashes(root)
        if post_hashes != hashes:
            raise SignedAgeRepairError("repair source changed during tests")
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
            raise SignedAgeRepairError("secret pattern found in repair evidence")
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
            "# OKX Demo signed-age/pre-arm/terminal reporting repair\n\n"
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
                raise SignedAgeRepairError("repair completion verification failed")
        _write_json(output / "OFFLINE_SIGNED_AGE_REPAIR_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": canonical_sha256(hashes),
        })
    if guard.attempts:
        raise SignedAgeRepairError("network attempt blocked during offline repair")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id")
    args = parser.parse_args()
    repair_id = args.repair_id or (
        "signed-age-repair-offline-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    try:
        output = run_offline_repair(args.root, repair_id)
    except Exception as exc:
        print(f"SIGNED_AGE_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"repair_id": repair_id, "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
