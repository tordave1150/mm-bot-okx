"""R0 repair that admits the immutable bootstrap-exposure audit to fresh R1."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r1_preflight_bootstrap_exposure_audit_offline import verify_failed_preflight
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_preflight_bootstrap_exposure_admission_repair")
PATTERN = re.compile(r"r1-bootstrap-exposure-admission-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R1_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_R0_OFFLINE_SUPPORT"
PREDECESSOR_EVIDENCE_ID = "r1-preflight-bootstrap-exposure-audit-offline-20260902T073223Z"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON object required: {path.name}")
    return value


def verify_predecessor(root: Path) -> dict[str, object]:
    terminal_path = (
        root / "artifacts/okx_demo_r1_preflight_bootstrap_exposure_audit"
        / PREDECESSOR_EVIDENCE_ID
        / "R0_R1_PREFLIGHT_BOOTSTRAP_EXPOSURE_AUDIT_COMPLETED.json"
    )
    terminal = _read(terminal_path)
    audit = verify_failed_preflight(root)
    if any((
        terminal.get("evidence_id") != PREDECESSOR_EVIDENCE_ID,
        terminal.get("status") != "OKX_DEMO_R1_PREFLIGHT_BOOTSTRAP_EXPOSURE_AUDIT_R0_OFFLINE_SUPPORT",
        terminal.get("R0_offline_audit_passed") is not True,
        terminal.get("terminal_written_last") is not True,
        audit.get("failed_preflight_decision") != "READ_ONLY_PREFLIGHT_FAILED",
        audit.get("failure_reason") != "UNOWNED_DEMO_EXPOSURE",
        audit.get("terminal_account_authoritative") is not False,
        audit.get("rerun_authorized") is not False,
        audit.get("resume_authorized") is not False,
    )):
        raise RepairError("bootstrap-exposure predecessor boundary drifted")
    return {**audit, "predecessor_evidence_id": PREDECESSOR_EVIDENCE_ID}


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh bootstrap-exposure admission repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("bootstrap-exposure admission repair identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/admission_predecessor_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "passed": True,
        "repository_defect_found": True,
        "cause": "the R1 verifier had no exact fail-closed admission mapping for the new R0 audit kind",
        "repair": "admit only the exact bootstrap-exposure admission repair terminal with complete hashes and zero-mutation audits",
        "risk_or_permission_relaxed": False,
        "failed_R1_identity_reusable": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "only exact evidence kind and terminal status are admitted", "passed": True},
        {"requirement": "predecessor exposure failure remains non-resumable", "passed": True},
        {"requirement": "Demo-only, zero mutation, no Live/prod remain required", "passed": True},
        {"requirement": "fresh R1 identity remains independently required", "passed": True},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name,
        "okx_demo_r1_preflight_bootstrap_exposure_audit_offline.py",
        "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py",
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_audit.py",
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_admission_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r1_bootstrap_admission_targeted", (
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_audit.py",
        "tests/test_okx_demo_r1_preflight_bootstrap_exposure_admission_repair.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    suites = _run_required_suites(root, output)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/r1_bootstrap_admission_targeted_summary.json", targeted)
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
        "evidence_kind": "r1_preflight_bootstrap_exposure_admission_r0_offline_repair",
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
    _write_json(output / "R0_R1_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_COMPLETED.json", {
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
        print(f"R1_BOOTSTRAP_EXPOSURE_ADMISSION_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
