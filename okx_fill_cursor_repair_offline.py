"""Create non-overwriting offline evidence for the fill-cursor successor repair.

This command never loads credentials or constructs an exchange.  Every test
process is socket-denied and receives empty OKX credential environment values.
It freezes the failed formal predecessor, repaired sources, regression output,
and the boundary requiring a new root protocol plus a fresh preflight token.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from okx_fill_restart_formal import canonical_sha256
from okx_fill_restart_offline import _sha256, _write_json, _write_text


REPAIR_STATUS = "OKX_DEMO_FILL_CURSOR_REPAIR_OFFLINE_SUPPORT"
ARTIFACT_ROOT = Path("artifacts") / "okx_demo_fill_cursor_repair"
FAILED_PACKAGE_ID = "formal-package-20260805T151737Z"
FAILED_FORMAL_RUN_ID = "formal-20260805T151737Z"
FAILED_STATUS = "OKX_DEMO_FILL_RESTART_RECONCILIATION_FAILED"
ROOT_PROTOCOL_SHA256 = (
    "ee2be03f3c88a007cbe872a8d0f95685fb78c2619e52e10548dead5884aaa1c0"
)
PREDECESSOR_HASHES = {
    "COMPLETED.json": (
        "9f88df950a1771ca484c389542d50b00ecf486d7d75770f6ed8cbca4e5b898d4"
    ),
    "formal_run/formal_completion_hashes.json": (
        "55e28a0584f0bacc6925aa1d3be048e5e663b868ddc867fb2a0e29d810053892"
    ),
    "decision/formal/decision.json": (
        "3bac052811f91b4db5bef181aa9565a2fa5d4a9a29de9a7b983e04375baf64c9"
    ),
    "formal_run/recovery/operation_reconstruction.json": (
        "dd3fac15198763b82982e2c51973dfa8f6e94b9118fdc5bc4bfae8e94727e225"
    ),
}
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md",
    "okx_fill_cursor_repair_offline.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_validation.py",
    "okx_fill_restart_formal.py",
    "okx_fill_restart_formal_prepare.py",
    "okx_fill_restart_offline.py",
    "okx_offline_network_guard.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_fill_cursor_repair_offline.py",
)


class FillCursorRepairError(RuntimeError):
    pass


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FillCursorRepairError(f"expected JSON object: {path}")
    return value


def verify_failed_predecessor(root: Path) -> dict[str, object]:
    root = root.resolve()
    package = (
        root / "artifacts" / "okx_demo_fill_restart_validation"
        / FAILED_PACKAGE_ID
    )
    failures: list[str] = []
    for relative, expected in PREDECESSOR_HASHES.items():
        path = package / relative
        if not path.is_file() or _sha256(path) != expected:
            failures.append(relative)
    completed = _json(package / "COMPLETED.json")
    if any((
        completed.get("status") != FAILED_STATUS,
        completed.get("formal_run_id") != FAILED_FORMAL_RUN_ID,
        completed.get("formal_execution_marker_count") != 1,
        completed.get("R1_completed") is not False,
        completed.get("R2_completed") is not False,
        completed.get("final_position_btc") != "0",
        completed.get("final_open_order_count") != 0,
        completed.get("live_endpoint_attempts") != 0,
        completed.get("live_orders") != 0,
    )):
        failures.append("COMPLETED.json:terminal_contract")
    manifest_path = package / "formal_run" / "formal_completion_hashes.json"
    manifest = _json(manifest_path)
    manifest_failures = [
        relative for relative, expected in manifest.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    failures.extend(f"completion:{row}" for row in manifest_failures)
    if completed.get("formal_completion_hashes_sha256") != _sha256(manifest_path):
        failures.append("formal_completion_hashes_sha256")
    root_protocol = root / "AGENTS.md"
    if not root_protocol.is_file() or _sha256(root_protocol) != ROOT_PROTOCOL_SHA256:
        failures.append("root_protocol_authority")
    if failures:
        raise FillCursorRepairError(
            "failed formal predecessor or active authority changed: "
            + ",".join(failures[:10])
        )
    return {
        "passed": True,
        "package_id": FAILED_PACKAGE_ID,
        "formal_run_id": FAILED_FORMAL_RUN_ID,
        "status": FAILED_STATUS,
        "fixed_hashes": dict(PREDECESSOR_HASHES),
        "completion_files_checked": len(manifest),
        "formal_execution_marker_count": 1,
        "R1_completed": False,
        "R2_completed": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "immutable": True,
    }


def repair_source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise FillCursorRepairError(f"repair source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def build_repair_spec(
    *,
    repair_id: str,
    predecessor: dict[str, object],
    source_hashes: dict[str, str],
) -> dict[str, object]:
    if not repair_id.startswith("repair-offline-") or any(
        character.isspace() for character in repair_id
    ):
        raise FillCursorRepairError("repair ID is invalid")
    return {
        "schema_version": 1,
        "phase": "OFFLINE_FILL_CURSOR_RECOVERY_REPAIR",
        "repair_id": repair_id,
        "execution_mode": "OFFLINE_FIXTURE",
        "network_allowed": False,
        "orders_allowed": False,
        "preflight_authorized": False,
        "formal_execution_authorized": False,
        "successor_protocol_copy": (
            "AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md"
        ),
        "successor_protocol_active": False,
        "predecessor": predecessor,
        "repair_contract": {
            "query_order": ["paginated_history", "recent_tail"],
            "paginated_history_calls": 3,
            "recent_tail_limit": 100,
            "run_boundary_filter": "timestamp_ms >= formal_trade_since_ms",
            "union_key": "trade_id",
            "overlap_fingerprint_fields": [
                "trade_id", "order_id", "client_order_id", "timestamp_ms",
                "side", "price", "amount", "fee_cost", "fee_currency",
                "liquidity",
            ],
            "identical_overlap": "deduplicate_once",
            "conflicting_overlap": "fail_closed",
            "stable_order": ["timestamp_ms", "trade_id"],
            "single_flatten_create_maximum": 1,
            "multi_partial_flatten_supported": True,
            "special_excluded_from_normal_activity_fifo_economics": True,
        },
        "fresh_identifier_contract": {
            "forbidden_preflight_run_id": "preflight-20260805T-arm03",
            "forbidden_package_id": FAILED_PACKAGE_ID,
            "forbidden_formal_run_id": FAILED_FORMAL_RUN_ID,
            "fresh_preflight_token_required": True,
            "fresh_formal_token_required": True,
        },
        "source_hashes": source_hashes,
        "source_manifest_sha256": canonical_sha256(source_hashes),
        "production_authorized": False,
        "live_mode_available": False,
    }


def _run_suite(
    *, root: Path, output: Path, name: str, arguments: Iterable[str]
) -> dict[str, object]:
    tests = output / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    audit_path = tests / f"{name}_network_audit.json"
    base_temp = root / ".okx_fill_cursor_repair_tmp" / output.name / name
    base_temp.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pytest", *arguments, "-q",
        "-p", "no:cacheprovider", "-p", "okx_offline_network_guard",
        "--basetemp", str(base_temp.resolve()),
    ]
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    ):
        environment[variable] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
    finally:
        shutil.rmtree(base_temp, ignore_errors=True)
    _write_text(tests / f"{name}.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1,
        "live_endpoint_attempts": -1,
        "optuna_imported": True,
    }
    result = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": int(audit.get("network_attempts", -1)),
        "live_endpoint_attempts": int(audit.get("live_endpoint_attempts", -1)),
        "optuna_imported": bool(audit.get("optuna_imported", True)),
        "command_scope": list(arguments),
    }
    result["passed_gate"] = all((
        result["returncode"] == 0,
        result["passed"] > 0,
        result["network_attempts"] == 0,
        result["live_endpoint_attempts"] == 0,
        result["optuna_imported"] is False,
    ))
    return result


def run_repair_suites(root: Path, output: Path) -> dict[str, object]:
    suites = {
        "repair_targeted": _run_suite(
            root=root,
            output=output,
            name="repair_targeted",
            arguments=(
                "tests/test_okx_fill_restart_gateway.py",
                "tests/test_okx_fill_restart_executor.py",
                "tests/test_okx_fill_restart_validation.py",
                "tests/test_okx_fill_cursor_repair_offline.py",
            ),
        ),
        "root_non_optuna": _run_suite(
            root=root,
            output=output,
            name="root_non_optuna",
            arguments=("tests",),
        ),
        "backtest_non_optuna": _run_suite(
            root=root,
            output=output,
            name="backtest_non_optuna",
            arguments=(
                "backtest/tests",
                "--ignore=backtest/tests/test_units_and_safety.py",
                "--ignore=backtest/tests/test_robust_gates.py",
            ),
        ),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(row["passed_gate"]) for row in suites.values()):
        raise FillCursorRepairError("one or more repair regression suites failed")
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
        "credentials_loaded": False,
        "credentials_serialized": False,
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "OFFLINE_REPAIR_COMPLETED.json"}
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
        raise FillCursorRepairError("repair ID reuse refused")
    output.mkdir(parents=True)
    predecessor = verify_failed_predecessor(root)
    source_hashes = repair_source_hashes(root)
    specification = build_repair_spec(
        repair_id=repair_id,
        predecessor=predecessor,
        source_hashes=source_hashes,
    )
    _write_json(output / "predecessor" / "failed_formal_audit.json", predecessor)
    _write_json(output / "specification" / "repair_spec.json", specification)
    _write_json(output / "specification" / "source_hashes.json", source_hashes)
    _write_json(output / "readiness" / "initial_readiness.json", {
        "predecessor_immutable": True,
        "execution_mode": "OFFLINE_FIXTURE",
        "network_allowed": False,
        "orders_allowed": False,
        "preflight_authorized": False,
        "formal_execution_authorized": False,
    })
    suites = run_repair_suites(root, output)
    post_test_hashes = repair_source_hashes(root)
    if post_test_hashes != source_hashes:
        raise FillCursorRepairError("repair source changed during regression execution")
    verify_failed_predecessor(root)
    total_passed = sum(int(row["passed"]) for row in suites.values())
    _write_json(output / "audits" / "source_hash_audit.json", {
        "passed": True,
        "files_checked": len(source_hashes),
        "source_manifest_sha256": canonical_sha256(source_hashes),
        "post_test_source_manifest_sha256": canonical_sha256(post_test_hashes),
    })
    _write_json(output / "audits" / "endpoint_audit.json", {
        "passed": True,
        "execution_mode": "OFFLINE_FIXTURE",
        "network_attempts": 0,
        "preflight_attempts": 0,
        "normal_order_attempts": 0,
        "flatten_order_attempts": 0,
        "account_setter_attempts": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    })
    _write_json(output / "audits" / "test_count_audit.json", {
        "passed": True,
        "suite_count": len(suites),
        "total_passed_including_overlapping_scopes": total_passed,
        "network_attempts": 0,
        "live_endpoint_attempts": 0,
        "optuna_imported": False,
    })
    secret_scan = _secret_scan(output)
    _write_json(output / "audits" / "secret_scan.json", secret_scan)
    if not secret_scan["passed"]:
        raise FillCursorRepairError("secret pattern found in repair evidence")
    decision = {
        "status": REPAIR_STATUS,
        "repair_id": repair_id,
        "offline_repair_passed": True,
        "successor_protocol_active": False,
        "fresh_preflight_required": True,
        "fresh_formal_package_required": True,
        "predecessor_rerun_allowed": False,
        "network_attempts": 0,
        "orders_submitted": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "production_authorized": False,
        "live_mode_available": False,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": (
            "explicitly activate AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md "
            "as root AGENTS.md, then separately authorize a fresh read-only "
            "OKX Demo preflight with new identifiers and arm token"
        ),
    }
    _write_json(output / "readiness" / "final_readiness.json", decision)
    _write_json(output / "decision" / "offline_repair_decision.json", decision)
    _write_text(
        output / "decision" / "offline_repair_decision.md",
        "# OKX Demo fill-cursor recovery repair\n\n"
        f"- Status: `{REPAIR_STATUS}`\n"
        f"- Repair ID: `{repair_id}`\n"
        "- Network attempts: `0`\n"
        "- Orders submitted: `0`\n"
        "- Successor protocol active: `false`\n"
        "- Production authorized: `false`\n\n"
        "A fresh root-protocol activation and separately armed read-only "
        "preflight are required next.\n",
    )
    completion = _completion_hashes(output)
    _write_json(output / "completion_hashes.json", completion)
    for relative, expected in completion.items():
        if _sha256(output / relative) != expected:
            raise FillCursorRepairError("repair completion verification failed")
    _write_json(output / "OFFLINE_REPAIR_COMPLETED.json", {
        **decision,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": canonical_sha256(source_hashes),
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id", required=True)
    args = parser.parse_args()
    try:
        output = run_offline_repair(args.root, args.repair_id)
    except Exception as exc:
        print(f"OFFLINE_FILL_CURSOR_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
