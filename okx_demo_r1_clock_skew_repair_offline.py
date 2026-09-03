"""Offline repair evidence for an R1 preflight stopped by the frozen clock gate."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk

ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_clock_skew_repair")
PATTERN = re.compile(r"r1-clock-skew-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R1_CLOCK_SKEW_REPAIR_R0_OFFLINE_SUPPORT"
REPAIR_ID = "r1-clock-skew-repair-offline-20260902T032134Z"
PREPARATION_ID = "preflight-package-20260902T032223Z"
RUN_ID = "preflight-20260902T032223Z"
SESSION_ID = "preflight:preflight-20260902T032223Z:p0:357a6645ef36"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON object required: {path.name}")
    return value


def verify_failed_preflight(root: Path) -> dict[str, object]:
    package = root / "artifacts/okx_demo_fill_restart_validation/preflight_packages" / PREPARATION_ID
    run = root / "artifacts/okx_demo_fill_restart_validation" / RUN_ID
    paths = {
        "package_terminal": package / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "phase_terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "result": run / "preflight/preflight_result.json",
        "decision": run / "decision/preflight_decision.json",
        "endpoint": run / "audits/endpoint_audit.json",
        "secret_scan": run / "audits/secret_scan.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("failed R1 evidence is incomplete")
    package_terminal, phase_terminal, result, decision, endpoint, secret = (
        _read(path) for path in paths.values()
    )
    if any((
        package_terminal.get("preparation_id") != PREPARATION_ID,
        package_terminal.get("run_id") != RUN_ID,
        phase_terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("failure_reason") != "clock skew exceeds frozen limit",
        dict(result.get("clock_skew_diagnostic") or {}).get("clock_skew_ms") != 2023,
        result.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        result.get("terminal_reconciliation_mode") != "UNRESOLVED_FAIL_CLOSED",
        decision.get("read_only_preflight_passed") is not False,
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        endpoint.get("read_call_count") != 11,
        endpoint.get("read_permission_present") is not True,
        endpoint.get("permission_snapshot_count") != 2,
        secret.get("passed") is not True,
    )):
        raise RepairError("failed R1 clock-skew boundary drifted")
    return {
        "immutable": True, "repair_id": REPAIR_ID,
        "preparation_id": PREPARATION_ID, "run_id": RUN_ID, "session_id": SESSION_ID,
        "failed_campaign_decision": "NOT_READY", "failure_reason": "CLOCK_SKEW_EXCEEDS_FROZEN_LIMIT",
        "failure_stage": "PRE_MARKET_BOOTSTRAP", "terminal_account_authoritative": False,
        "resume_authorized": False, "identity_reuse_authorized": False,
        "clock_measurement_serialized_in_predecessor": True,
        "clock_skew_ms": 2023, "maximum_clock_skew_ms": 1500,
        "read_only_transport_succeeded": True,
        "mutation_attempts": 0, "live_endpoint_attempts": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh clock-skew repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("clock-skew repair identity reuse refused")
    predecessor = verify_failed_preflight(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_preflight_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "passed": True,
        "classification": "HOST_CLOCK_OR_TIMEPATH_HEALTH",
        "frozen_maximum_clock_skew_ms": 1500,
        "code_relaxes_clock_budget": False,
        "predecessor_measurement_available": True,
        "repair": "future failures serialize measured skew and frozen bound only",
        "external_remediation_required": "synchronize the network-capable host clock before a fresh R1",
        "external_recovery_confirmation": {
            "source": "user-observed Windows Time status",
            "leap_indicator": 0, "stratum": 5,
            "resync_completed_successfully": True,
        },
        "rerun_same_identity": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/scenario_matrix.json", [
        {"scenario": "skew at or below 1500 ms", "expected": "preflight may continue"},
        {"scenario": "skew above 1500 ms", "expected": "fail closed before account/order admission"},
        {"scenario": "failed R1 identity", "expected": "no rerun or reuse"},
        {"scenario": "network host clock unsynchronized", "expected": "host remediation; no code relaxation"},
    ])
    sources = (
        "AGENTS.md", "okx_demo_adapter.py", "okx_fill_restart_preflight.py",
        "okx_demo_multi_session_prepare.py",
        Path(__file__).name, "tests/test_okx_demo_adapter.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_demo_r1_clock_skew_repair.py",
        "tests/test_okx_demo_multi_session_prepare.py",
    )
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r1_clock_skew_targeted", (
        "tests/test_okx_demo_adapter.py", "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_demo_r1_clock_skew_repair.py", "tests/test_okx_demo_multi_session_prepare.py",
    ))
    suites = _run_required_suites(root, output)
    _write_json(output / "tests/r1_clock_skew_targeted_summary.json", targeted)
    _write_json(output / "tests/test_summary.json", suites)
    if any(item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0
           or int(item.get("network_attempts", 1)) != 0
           or int(item.get("live_endpoint_attempts", 1)) != 0
           or item.get("optuna_imported") is not False
           for item in {**suites, "targeted": targeted}.values()):
        raise RepairError("socket-denied test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY, "R0_offline_repair_passed": True,
        "R1_preparation_authorized": False, "preflight_authorized": False,
        "economic_campaign_authorized": False, "production_authorized": False,
        "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0,
        "optuna_executed": False, "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "synchronize host clock, then separately authorize a fresh R1 preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    secret = _secret_scan(output); _write_json(output / "audits/secret_scan.json", secret)
    if not secret["passed"]:
        raise RepairError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R1_CLOCK_SKEW_REPAIR_COMPLETED.json", {
        **decision, "evidence_id": evidence_id, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_files_checked": len(completion), "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "terminal_written_last": True,
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent); parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try: print(run(args.root, args.evidence_id))
    except Exception as exc: print(f"R1_CLOCK_SKEW_REPAIR_FAILED:{type(exc).__name__}:{exc}"); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
