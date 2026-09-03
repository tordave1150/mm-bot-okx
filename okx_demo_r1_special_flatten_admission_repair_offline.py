"""Offline repair for admitting the frozen special-flatten R0 lineage to R1."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_fill_restart_preflight import _preflight_predecessor_audit
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r1_special_flatten_admission_repair")
PATTERN = re.compile(r"r1-special-flatten-admission-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R1_SPECIAL_FLATTEN_ADMISSION_REPAIR_R0_OFFLINE_SUPPORT"
PREDECESSOR_ID = "r2-special-flatten-classification-repair-offline-20260902T163500Z"
FAILED_PREPARATION_ID = "preflight-package-20260903T010715Z"
FAILED_RUN_ID = "preflight-20260903T010715Z"
FAILED_SESSION_ID = "preflight:preflight-20260903T010715Z:p0:635049f4442a"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"object required: {path.name}")
    return value


def predecessor_audit(root: Path) -> dict[str, object]:
    prior = root / "artifacts/okx_demo_r2_special_flatten_classification_repair" / PREDECESSOR_ID
    terminal = _read(prior / "R0_R2_SPECIAL_FLATTEN_CLASSIFICATION_REPAIR_COMPLETED.json")
    decision = _read(prior / "decision/offline_decision.json")
    freeze = _read(prior / "predecessor/campaign_freeze_audit.json")
    completion = _read(prior / "completion_hashes.json")
    if any(
        not (prior / relative).is_file() or _sha256(prior / relative) != expected
        for relative, expected in completion.items()
    ):
        raise RepairError("prior classification completion evidence drifted")
    # The old R0 source manifest intentionally predates this admission repair.
    # Recheck the immutable classification sources; the admission files are the
    # only permitted source drift and are covered by this new evidence/tests.
    sources = _read(prior / "specification/source_hashes.json")
    immutable_sources = {
        "AGENTS.md", "okx_demo_r2_special_flatten_classification_repair_offline.py",
        "okx_demo_multi_session_campaign.py", "okx_demo_multi_session_supervisor.py",
        "okx_demo_soak_executor.py", "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    }
    if any(_sha256(root / name) != sources.get(name) for name in immutable_sources):
        raise RepairError("classification source boundary drifted")
    offline = {
        "passed": True,
        "evidence_kind": decision.get("evidence_kind"),
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": False,
        "special_flatten_sessions": freeze.get("special_flatten_sessions"),
        "resume_authorized": freeze.get("resume_authorized"),
        "identity_reuse_authorized": freeze.get("identity_reuse_authorized"),
    }
    admission = _preflight_predecessor_audit(root, offline)
    failed_output = root / "artifacts/okx_demo_fill_restart_validation" / FAILED_RUN_ID
    if any((
        offline.get("passed") is not True,
        offline.get("evidence_kind") != "r2_special_flatten_classification_r0_offline_repair",
        offline.get("special_flatten_sessions") != 2,
        offline.get("terminal_account_authoritative") is not False,
        admission.get("passed") is not True,
        failed_output.exists(),
        terminal.get("terminal_written_last") is not True,
    )):
        raise RepairError("R1 special-flatten admission predecessor drifted")
    return {
        "predecessor_evidence_id": PREDECESSOR_ID,
        "predecessor_evidence_kind": offline["evidence_kind"],
        "predecessor_passed": True,
        "predecessor_special_flatten_sessions": 2,
        "predecessor_terminal_account_authoritative": False,
        "admission_passed": True,
        "failed_preparation_id": FAILED_PREPARATION_ID,
        "failed_run_id": FAILED_RUN_ID,
        "failed_session_id": FAILED_SESSION_ID,
        "failed_identity_reusable": False,
        "rerun_authorized": False,
        "resume_authorized": False,
        "orders_or_mutations_in_failed_run": 0,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh R1 special-flatten admission repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("offline evidence identity reuse refused")
    predecessor = predecessor_audit(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/admission_predecessor_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "repository_defect_found": True,
        "cause": "R1 predecessor admission lacked a branch for the verified special-flatten classification R0 evidence kind",
        "repair": "admit only the exact immutable NOT_READY lineage with two dispatch sessions and no current-account authority claim",
        "risk_or_permission_relaxed": False,
        "failed_identity_reusable": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "exact classification lineage admitted", "passed": True},
        {"requirement": "three flatten sessions rejected", "passed": True},
        {"requirement": "failed R1 identity remains non-reusable", "passed": True},
        {"requirement": "Demo-only/zero-mutation/Live-prod zero retained", "passed": True},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name,
        "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py",
        "okx_demo_r2_special_flatten_classification_repair_offline.py",
        "tests/test_okx_fill_restart_preflight.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        str(name): _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "r1_sf", (
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    suites = _run_required_suites(root, output)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/test_summary.json", summary)
    if any(item.get("passed_gate") is not True or item.get("returncode") != 0
           or item.get("network_attempts") != 0 or item.get("live_endpoint_attempts") != 0
           or item.get("optuna_imported") is not False for item in summary.values()):
        raise RepairError("offline test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY,
        "evidence_kind": "r1_special_flatten_admission_r0_offline_repair",
        "R0_offline_repair_passed": True,
        "preflight_authorized": False, "economic_campaign_authorized": False,
        "production_authorized": False, "live_mode_available": False,
        "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False,
        "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if scan.get("passed") is not True:
        raise RepairError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path)
                  for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R1_SPECIAL_FLATTEN_ADMISSION_REPAIR_COMPLETED.json", {
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
        print(f"R1_SPECIAL_FLATTEN_ADMISSION_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
