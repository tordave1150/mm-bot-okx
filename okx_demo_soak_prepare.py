"""Freeze a fresh bounded OKX Demo soak package entirely offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_soak_failure_injection import scenario_matrix
from okx_demo_soak_failure_injection_offline import _run_suite
from okx_fill_restart_formal_prepare import (
    _OfflineSocketGuard,
    _artifact_secret_scan,
    verify_preflight_evidence,
)
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_preflight import preflight_source_hashes, verify_offline_evidence
from okx_fill_restart_validation import canonical_sha256


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_soak_validation"
READY_STATUS = "BOUNDED_DEMO_SOAK_PACKAGE_FROZEN_OFFLINE"
DEFAULT_REPAIR_ID = "soak-failure-offline-20260810T130849Z"
DEFAULT_PREFLIGHT_RUN_ID = "preflight-20260810T131800Z"
SOURCE_FILES = (
    "okx_demo_soak_prepare.py",
    "okx_demo_soak_executor.py",
    "okx_demo_soak_failure_injection.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_soak_failure_injection.py",
)
TARGETED_TESTS = (
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_soak_failure_injection.py",
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_validation.py",
)


class SoakPackageError(RuntimeError):
    pass


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = preflight_source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise SoakPackageError(f"soak package source missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _identifiers(
    *, source_manifest_sha256: str, preflight_sha256: str
) -> dict[str, str]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_id = f"soak-package-{stamp}"
    run_id = f"soak-{stamp}"
    nonce = hashlib.sha256(
        f"{package_id}|{run_id}|{source_manifest_sha256}|{preflight_sha256}".encode()
    ).hexdigest()[:12]
    session_id = f"soak:{run_id}:p0:{nonce}"
    return {
        "package_id": package_id,
        "run_id": run_id,
        "session_id": session_id,
        "arm_token": f"OKX_DEMO:{session_id}",
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "SOAK_PACKAGE_COMPLETED.json"}
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and path.name not in ignored and path.suffix != ".tmp"
    ))


def prepare(
    root: Path,
    *,
    repair_id: str,
    preflight_run_id: str,
) -> tuple[Path, dict[str, str]]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        repair = verify_offline_evidence(root, repair_id)
        preflight = verify_preflight_evidence(root, preflight_run_id)
        if any((
            repair.get("evidence_kind") != "soak_failure_injection_readiness",
            repair.get("passed") is not True,
            repair.get("formal_predecessor_package_id")
            != "formal-package-20260810T123953Z",
            preflight.get("passed") is not True,
            preflight.get("run_id") != preflight_run_id,
        )):
            raise SoakPackageError("repair or preflight evidence is invalid")
        preflight_repair_path = (
            root / "artifacts/okx_demo_fill_restart_validation"
            / preflight_run_id / "predecessor/offline_evidence_audit.json"
        )
        preflight_repair = json.loads(
            preflight_repair_path.read_text(encoding="utf-8")
        )
        if (
            preflight_repair.get("repair_id") != repair_id
            or preflight_repair.get("completion_hashes_sha256")
            != repair.get("completion_hashes_sha256")
        ):
            raise SoakPackageError("preflight is not bound to selected soak evidence")
        hashes = _source_hashes(root)
        source_manifest_sha256 = canonical_sha256(hashes)
        identifiers = _identifiers(
            source_manifest_sha256=source_manifest_sha256,
            preflight_sha256=str(preflight["completion_hashes_sha256"]),
        )
        output = root / ARTIFACT_ROOT / identifiers["package_id"]
        run_output = root / ARTIFACT_ROOT / identifiers["run_id"]
        if output.exists() or run_output.exists():
            raise SoakPackageError("fresh soak identity collision")
        output.mkdir(parents=True)
        token_hash = hashlib.sha256(
            identifiers["arm_token"].encode("utf-8")
        ).hexdigest()
        budget = {
            "capital_usdt": "750",
            "leverage": 3,
            "maximum_inventory_btc": "0.01",
            "maximum_owned_bid": 1,
            "maximum_owned_ask": 1,
            "maximum_unresolved_flatten": 1,
            "soft_guard_usdt": "22.50",
            "hard_kill_usdt": "37.50",
            "session_wall_minutes": 30,
            "session_normal_create_cap": 60,
            "observation_interval_ms": 2_000,
            "maximum_market_age_ms": 1_000,
            "maximum_clock_skew_ms": 1_500,
            "read_retry_attempts": 3,
            "ambiguous_mutation_retry_attempts": 0,
            "terminal_account_snapshots": 2,
        }
        spec = {
            "schema_version": 1,
            "protocol_id": "okx-demo-bounded-soak-failure-injection-v1",
            "execution_source": "okx_demo_soak_executor.py",
            "execution_source_bound": True,
            **{key: value for key, value in identifiers.items() if key != "arm_token"},
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "repair_id": repair_id,
            "preflight_run_id": preflight_run_id,
            "successful_formal_predecessor": "formal-package-20260810T123953Z",
            "source_manifest_sha256": source_manifest_sha256,
            "repair_completion_sha256": repair["completion_hashes_sha256"],
            "preflight_completion_sha256": preflight["completion_hashes_sha256"],
            "preflight_decision_sha256": preflight["decision_sha256"],
            "market_fingerprint": preflight["market_fingerprint"],
            "market_spec": preflight["market_spec"],
            "risk_budget": budget,
            "failure_scenario_matrix": list(scenario_matrix()),
            "normal_orders": "POST_ONLY_ONLY",
            "shutdown_flatten": "SINGLE_FLIGHT_REDUCE_ONLY",
            "foreign_orders": "FAIL_CLOSED",
            "ambiguous_mutation": "BLOCK_UNTIL_AUTHORITATIVE_RECONCILIATION",
            "required_terminal_state": "TWO_FLAT_EMPTY_ACCOUNT_SNAPSHOTS",
            "soak_authorized": False,
            "soak_executed": False,
            "execution_marker_created": False,
            "network_authorized_during_freeze": False,
            "orders_authorized_during_freeze": False,
            "production_authorized": False,
        }
        spec["specification_sha256"] = canonical_sha256(spec)
        _write_json(output / "predecessor/offline_evidence_audit.json", repair)
        _write_json(output / "predecessor/preflight_evidence_audit.json", preflight)
        _write_json(output / "specification/source_hashes.json", hashes)
        _write_json(output / "specification/soak_package_spec.json", spec)
        _write_json(output / "run/soak_run_manifest.json", {
            "package_id": identifiers["package_id"],
            "run_id": identifiers["run_id"],
            "session_id": identifiers["session_id"],
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
            "soak_authorized": False,
            "soak_executed": False,
            "execution_marker_created": False,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
        })
        targeted = _run_suite(
            root, output, "soak_package_targeted", TARGETED_TESTS
        )
        _write_json(output / "tests/test_summary.json", {
            "soak_package_targeted": targeted,
        })
        if not targeted["passed_gate"]:
            raise SoakPackageError("soak package targeted regressions failed")
        if _source_hashes(root) != hashes:
            raise SoakPackageError("soak package source changed during freeze")
        _write_json(output / "audits/network_mutation_audit.json", {
            "socket_denied": True,
            "network_attempts": len(guard.attempts),
            "okx_requests": 0,
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
        secret = _artifact_secret_scan(output)
        _write_json(output / "audits/secret_scan.json", secret)
        if not secret["passed"]:
            raise SoakPackageError("secret pattern found in soak package")
        decision = {
            "status": READY_STATUS,
            "package_id": identifiers["package_id"],
            "run_id": identifiers["run_id"],
            "session_id": identifiers["session_id"],
            "soak_authorized": False,
            "soak_executed": False,
            "execution_marker_created": False,
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
            "next_boundary": "separate exact bounded Demo soak authorization",
        }
        _write_json(output / "decision/soak_package_decision.json", decision)
        _write_text(
            output / "decision/soak_package_decision.md",
            "# Bounded OKX Demo soak package\n\n"
            f"- Package: `{identifiers['package_id']}`\n"
            f"- Run: `{identifiers['run_id']}`\n"
            f"- Session: `{identifiers['session_id']}`\n"
            "- Soak authorized/executed: `false / false`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n",
        )
        completion = _completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        _write_json(output / "SOAK_PACKAGE_COMPLETED.json", {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": source_manifest_sha256,
            "specification_sha256": spec["specification_sha256"],
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
        })
    if guard.attempts:
        raise SoakPackageError("network attempt blocked during package freeze")
    return output, identifiers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id", default=DEFAULT_REPAIR_ID)
    parser.add_argument("--preflight-run-id", default=DEFAULT_PREFLIGHT_RUN_ID)
    args = parser.parse_args()
    try:
        output, identifiers = prepare(
            args.root,
            repair_id=args.repair_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"SOAK_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
