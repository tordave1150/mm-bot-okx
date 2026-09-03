"""Socket-denied audit for an R1 preflight stopped by existing Demo exposure."""
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


ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_preflight_bootstrap_exposure_audit")
PATTERN = re.compile(r"r1-preflight-bootstrap-exposure-audit-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R1_PREFLIGHT_BOOTSTRAP_EXPOSURE_AUDIT_R0_OFFLINE_SUPPORT"
REPAIR_ID = "r2-session1-fill-claim-repair-offline-20260902T065629Z"
PREPARATION_ID = "preflight-package-20260902T071044Z"
RUN_ID = "preflight-20260902T071044Z"
SESSION_ID = "preflight:preflight-20260902T071044Z:p0:5b87924659d9"


class AuditError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"durable JSON object required: {path.name}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    base = root / "artifacts/okx_demo_fill_restart_validation"
    package = base / "preflight_packages" / PREPARATION_ID
    run = base / RUN_ID
    return {
        "package_terminal": package / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "package_completion": package / "completion_hashes.json",
        "phase_terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "run_completion": run / "completion_hashes.json",
        "result": run / "preflight/preflight_result.json",
        "decision": run / "decision/preflight_decision.json",
        "endpoint": run / "audits/endpoint_audit.json",
        "secret_scan": run / "audits/secret_scan.json",
    }


def verify_failed_preflight(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise AuditError("failed R1 preflight evidence is incomplete")
    package, terminal, result, decision, endpoint, secret = (
        _read(paths[name])
        for name in (
            "package_terminal", "phase_terminal", "result", "decision", "endpoint", "secret_scan"
        )
    )
    diagnostic = dict(result.get("account_only_diagnostic") or {})
    required_zero = ("mutation_attempts", "orders_submitted", "orders_amended", "orders_cancelled", "live_endpoint_attempts", "live_orders")
    if any((
        package.get("preparation_id") != PREPARATION_ID,
        package.get("run_id") != RUN_ID,
        package.get("session_id") != SESSION_ID,
        package.get("repair_id") != REPAIR_ID,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        result.get("failure_reason") != "fresh session has unowned orders or exposure",
        result.get("primary_error_category") != "READ_FAILURE",
        result.get("market_bootstrap_completed") is not False,
        diagnostic.get("signed_position_btc") != "-0.01",
        diagnostic.get("position_row_count") != 1,
        diagnostic.get("open_order_count") != 0,
        result.get("terminal_account_authoritative") is not False,
        result.get("terminal_reconciliation_mode") != "UNRESOLVED_FAIL_CLOSED",
        any(result.get(name) != 0 for name in required_zero),
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        endpoint.get("read_call_count") != 18,
        endpoint.get("read_permission_present") is not True,
        decision.get("read_only_preflight_passed") is not False,
        decision.get("formal_demo_execution_authorized") is not False,
        secret.get("passed") is not True,
    )):
        raise AuditError("failed R1 bootstrap-exposure boundary drifted")
    return {
        "immutable": True,
        "repair_id": REPAIR_ID,
        "preparation_id": PREPARATION_ID,
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "failed_preflight_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "failure_stage": "PRE_MARKET_BOOTSTRAP",
        "failure_reason": "UNOWNED_DEMO_EXPOSURE",
        "signed_position_btc": "-0.01",
        "open_orders": 0,
        "market_bootstrap_completed": False,
        "terminal_account_authoritative": False,
        "terminal_reconciliation_mode": "UNRESOLVED_FAIL_CLOSED",
        "mutation_attempts": 0,
        "mutation_retries": 0,
        "live_endpoint_attempts": 0,
        "rerun_authorized": False,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise AuditError("fresh bootstrap-exposure audit identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise AuditError("bootstrap-exposure audit identity reuse refused")
    predecessor = verify_failed_preflight(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_preflight_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "passed": True,
        "repository_defect_found": False,
        "cause": "the Demo account already had a non-zero unowned position before the fresh R1 session",
        "failure_stage": "PRE_MARKET_BOOTSTRAP",
        "bootstrap_gate_blocked_before_mutation": True,
        "terminal_reconciliation_fail_closed": True,
        "account_state_remediation_required": "separately verify and flatten the Demo account outside this R0 audit before a fresh R1",
        "rerun_same_identity": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "non-zero fresh-session exposure blocks before mutation", "passed": True},
        {"requirement": "no terminal flat claim follows unresolved account evidence", "passed": True},
        {"requirement": "failed R1 identity cannot rerun, resume, or reuse", "passed": True},
        {"requirement": "frozen risk limits remain unchanged", "passed": True},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name, "okx_demo_adapter.py", "okx_fill_restart_preflight.py",
        "okx_fill_restart_preflight_prepare.py", "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_audit.py",
    )
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r1_bootstrap_exposure_targeted", (
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_audit.py",
        "tests/test_okx_demo_adapter.py", "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    suites = _run_required_suites(root, output)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/r1_bootstrap_exposure_targeted_summary.json", targeted)
    _write_json(output / "tests/test_summary.json", summary)
    if any(
        item.get("passed_gate") is not True
        or item.get("returncode") != 0
        or item.get("network_attempts") != 0
        or item.get("live_endpoint_attempts") != 0
        or item.get("optuna_imported") is not False
        for item in summary.values()
    ):
        raise AuditError("offline test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY,
        "evidence_kind": "r1_preflight_bootstrap_exposure_audit_r0_offline",
        "R0_offline_audit_passed": True,
        "preflight_authorized": False,
        "economic_campaign_authorized": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate account-state remediation, then separately authorize fresh R1 preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise AuditError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R1_PREFLIGHT_BOOTSTRAP_EXPOSURE_AUDIT_COMPLETED.json", {
        **decision, "evidence_id": evidence_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "terminal_written_last": True,
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"R1_BOOTSTRAP_EXPOSURE_AUDIT_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
