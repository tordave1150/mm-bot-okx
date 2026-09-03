from pathlib import Path

import okx_demo_r1_preflight_bootstrap_exposure_admission_repair_offline as repair
from okx_fill_restart_preflight import _preflight_predecessor_audit


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_exposure_predecessor_is_exact_and_non_resumable() -> None:
    result = repair.verify_predecessor(ROOT)

    assert result["failed_preflight_decision"] == "READ_ONLY_PREFLIGHT_FAILED"
    assert result["failure_reason"] == "UNOWNED_DEMO_EXPOSURE"
    assert result["rerun_authorized"] is False
    assert result["resume_authorized"] is False


def test_admission_mapping_rejects_missing_non_resumable_flags() -> None:
    accepted = {
        "evidence_kind": "r1_preflight_bootstrap_exposure_admission_r0_offline_repair",
        "failed_preflight_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "failure_reason": "UNOWNED_DEMO_EXPOSURE",
        "terminal_account_authoritative": False,
        "rerun_authorized": False,
        "resume_authorized": False,
    }
    assert _preflight_predecessor_audit(ROOT, accepted)["passed"] is True

    rejected = {**accepted, "resume_authorized": True}
    assert _preflight_predecessor_audit(ROOT, rejected)["passed"] is False
