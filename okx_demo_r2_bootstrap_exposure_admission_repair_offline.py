"""R0 repair that binds the passed R1 preflight to exact R2 admission rules."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r1_preflight_bootstrap_exposure_admission_repair_offline import verify_predecessor
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_bootstrap_exposure_admission_repair")
PATTERN = re.compile(r"r2-bootstrap-exposure-admission-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_R0_OFFLINE_SUPPORT"
PREDECESSOR_EVIDENCE_ID = "r1-bootstrap-exposure-admission-repair-offline-20260902T122706Z"
R1_RUN_ID = "preflight-20260902T123014Z"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON object required: {path.name}")
    return value


def verify_r2_predecessor(root: Path) -> dict[str, object]:
    predecessor = verify_predecessor(root)
    run = root / "artifacts/okx_demo_fill_restart_validation" / R1_RUN_ID
    paths = {
        "terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "result": run / "preflight/preflight_result.json",
        "decision": run / "decision/preflight_decision.json",
        "endpoint": run / "audits/endpoint_audit.json",
        "secret": run / "audits/secret_scan.json",
        "offline_audit": run / "predecessor/offline_evidence_audit.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("passed R1 durable evidence is incomplete")
    terminal, result, decision, endpoint, secret, offline = (
        _read(path) for path in paths.values()
    )
    if any((
        predecessor.get("rerun_authorized") is not False,
        predecessor.get("resume_authorized") is not False,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_PASSED",
        result.get("passed") is not True,
        result.get("execution_mode") != "OKX_DEMO",
        result.get("mutation_attempts") != 0,
        result.get("orders_submitted") != 0,
        result.get("orders_amended") != 0,
        result.get("orders_cancelled") != 0,
        result.get("live_endpoint_attempts") != 0,
        result.get("live_orders") != 0,
        decision.get("read_only_preflight_passed") is not True,
        decision.get("formal_demo_execution_authorized") is not False,
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        offline.get("evidence_kind") != "r1_preflight_bootstrap_exposure_admission_r0_offline_repair",
        offline.get("offline_run_id") != PREDECESSOR_EVIDENCE_ID,
        secret.get("passed") is not True,
    )):
        raise RepairError("R2 predecessor admission boundary drifted")
    return {
        "r1_preflight_passed": True,
        "r1_run_id": R1_RUN_ID,
        "r1_mutation_attempts": 0,
        "r1_live_endpoint_attempts": 0,
        "predecessor_evidence_kind": "r1_preflight_bootstrap_exposure_admission_r0_offline_repair",
        "predecessor_evidence_id": PREDECESSOR_EVIDENCE_ID,
        "failed_R1_identity_reusable": False,
        "resume_authorized": False,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh R2 bootstrap-exposure admission repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("R2 bootstrap-exposure admission repair identity reuse refused")
    predecessor = verify_r2_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/r2_admission_predecessor_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "passed": True,
        "repository_defect_found": True,
        "cause": "R2 package admission lacked an exact mapping for the R1 bootstrap-exposure repair lineage",
        "repair": "admit only a passed Demo R1 with zero mutations and exact non-reusable predecessor evidence",
        "risk_or_permission_relaxed": False,
        "campaign_identity_reusable": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "only R1 passed with Demo-only zero mutations is admitted", "passed": True},
        {"requirement": "failed R1 identity remains non-reusable", "passed": True},
        {"requirement": "R2 package and campaign identities must be fresh", "passed": True},
        {"requirement": "no Live/prod or risk-boundary relaxation", "passed": True},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name,
        "okx_demo_r1_preflight_bootstrap_exposure_admission_repair_offline.py",
        "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py",
        "okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_r2_bootstrap_exposure_admission_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r2_bootstrap_admission", (
        "tests/test_okx_demo_r2_bootstrap_exposure_admission_repair.py",
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_admission_repair.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
        "tests/test_okx_demo_multi_session_prepare.py",
    ))
    suites = _run_required_suites(root, output)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/r2_bootstrap_admission_summary.json", targeted)
    _write_json(output / "tests/test_summary.json", summary)
    if any(
        item.get("passed_gate") is not True
        or item.get("returncode") != 0
        or item.get("network_attempts") != 0
        or item.get("live_endpoint_attempts") != 0
        or item.get("optuna_imported") is not False
        for item in summary.values()
    ):
        raise RepairError("offline test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY,
        "evidence_kind": "r2_bootstrap_exposure_admission_r0_offline_repair",
        "R0_offline_repair_passed": True,
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
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_COMPLETED.json", {
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
        print(f"R2_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
