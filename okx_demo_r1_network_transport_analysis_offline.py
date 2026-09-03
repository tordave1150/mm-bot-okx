"""Seal socket-denied analysis of an immutable R1 transport failure."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json


ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_network_transport_analysis")
PATTERN = re.compile(r"r1-network-transport-analysis-offline-\d{8}T\d{6}Z\Z")
RUN_ID = "preflight-20260830T103541Z"
PREPARATION_ID = "preflight-package-20260830T103541Z"
SESSION_ID = "preflight:preflight-20260830T103541Z:p0:98fade87834b"
READY = "OKX_DEMO_R1_NETWORK_TRANSPORT_R0_OFFLINE_ANALYSIS"


class AnalysisError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AnalysisError(f"invalid JSON evidence: {path.name}")
    return value


def verify_failed_run(root: Path) -> dict[str, object]:
    run = root / "artifacts/okx_demo_fill_restart_validation" / RUN_ID
    package = root / "artifacts/okx_demo_fill_restart_validation/preflight_packages" / PREPARATION_ID
    paths = {
        "package": package / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "result": run / "preflight/preflight_result.json",
        "endpoint": run / "audits/endpoint_audit.json",
        "decision": run / "decision/preflight_decision.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise AnalysisError("R1 transport evidence is incomplete")
    package_json, terminal, result, endpoint, decision = (_read(path) for path in paths.values())
    retry_rows = list(dict(result.get("transport_audit") or {}).get("read_retry_audit") or [])
    expected_methods = ["fetch_markets"] * 3 + ["privateGetAccountConfig"] * 3
    if any((
        package_json.get("run_id") != RUN_ID,
        package_json.get("session_id") != SESSION_ID,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        result.get("primary_error_category") != "NETWORK",
        result.get("terminal_reconciliation_mode") != "UNRESOLVED_FAIL_CLOSED",
        result.get("mutation_attempts") != 0,
        result.get("live_endpoint_attempts") != 0,
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        [row.get("method") for row in retry_rows] != expected_methods,
        any(row.get("category") != "NETWORK" for row in retry_rows),
        decision.get("read_only_preflight_passed") is not False,
    )):
        raise AnalysisError("R1 transport boundary drifted")
    return {
        "immutable": True, "run_id": RUN_ID, "preparation_id": PREPARATION_ID,
        "session_id": SESSION_ID, "resume_authorized": False,
        "identity_reuse_authorized": False, "network_read_attempts": len(retry_rows),
        "read_methods": sorted(set(expected_methods)), "mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnosis() -> dict[str, object]:
    return {
        "passed": True,
        "root_cause": "the current execution environment cannot complete any TCP/HTTP transport to the already-proven Demo host; both public and private read-only routes exhausted their bounded retries before protocol validation",
        "adapter_defect_proven": False,
        "credential_defect_proven": False,
        "environment_network_capable": False,
        "code_remediation": "none; retrying from this environment or reusing the identity would be unsafe and cannot restore external transport",
        "required_external_remediation": "use a genuinely network-capable execution environment with outbound access to www.okx.com, then create fresh R1 identities under separate authorization",
        "risk_expansion": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise AnalysisError("fresh R1 network transport analysis identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise AnalysisError("R1 network transport analysis identity reuse refused")
    predecessor, cause = verify_failed_run(root), diagnosis()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_r1_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", cause)
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "public and private failure routes are independently evidenced", "passed": True},
        {"requirement": "bounded read retries stay at three", "passed": True},
        {"requirement": "no mutation or Live endpoint occurs", "passed": True},
        {"requirement": "identity reuse is refused", "passed": True},
        {"requirement": "R1 remediation requires external network-capable environment", "passed": True},
    ])
    sources = ("AGENTS.md", "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py", Path(__file__).name,
               "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
               "tests/test_okx_demo_r1_network_transport_analysis.py")
    _write_json(output / "specification/source_hashes.json", {name: _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r1_network_transport_targeted", (
        "tests/test_okx_demo_r1_network_transport_analysis.py", "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py"))
    suites = _run_suites(root, output)
    _write_json(output / "tests/r1_network_transport_targeted_summary.json", targeted)
    all_suites = {**suites, "r1_network_transport_targeted": targeted}
    if any(item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0 or int(item.get("network_attempts", 1)) != 0 or int(item.get("live_endpoint_attempts", 1)) != 0 or item.get("optuna_imported") is not False for item in all_suites.values()):
        raise AnalysisError("socket-denied test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {"socket_denied": True, "network_attempts": 0, "credential_reads": 0, "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0, "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0, "account_configuration_attempts": 0, "orders": 0, "mutation_retries": 0})
    decision = {"status": READY, "R0_offline_analysis_passed": True, "R1_preparation_authorized": False, "preflight_authorized": False, "production_authorized": False, "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False, "validation_opened": False, "holdout_opened": False, "git_write_operation": False, "next_boundary": "external network remediation, then separately authorized fresh R1 preparation"}
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise AnalysisError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R1_NETWORK_TRANSPORT_ANALYSIS_COMPLETED.json", {**decision, "evidence_id": evidence_id, "completed_at_utc": datetime.now(timezone.utc).isoformat(), "completion_files_checked": len(completion), "completion_hashes_sha256": _sha256(output / "completion_hashes.json"), "terminal_written_last": True})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"R1_NETWORK_TRANSPORT_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
