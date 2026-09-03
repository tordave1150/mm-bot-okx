from pathlib import Path

from okx_demo_r1_network_transport_audit_offline import verify_failed_preflight
from okx_fill_restart_preflight import _preflight_predecessor_audit

ROOT = Path(__file__).resolve().parents[1]


def test_failed_network_preflight_is_immutable_and_unresolved() -> None:
    audit = verify_failed_preflight(ROOT)
    assert audit["primary_error_category"] == "NETWORK"
    assert audit["read_call_count"] == 6
    assert audit["terminal_account_authoritative"] is False
    assert audit["mutation_attempts"] == 0
    assert audit["rerun_authorized"] is False


def test_network_audit_admission_rejects_a_rerunnable_identity() -> None:
    accepted = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r1_network_transport_audit_r0_offline",
        "failed_preflight_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "terminal_account_authoritative": False,
        "rerun_authorized": False,
        "failed_preparation_id": "preflight-package-20260902T021850Z",
        "failed_run_id": "preflight-20260902T021850Z",
        "failed_session_id": "preflight:preflight-20260902T021850Z:p0:3c614c2aebc6",
    })
    rejected = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r1_network_transport_audit_r0_offline",
        "failed_preflight_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "terminal_account_authoritative": False,
        "rerun_authorized": True,
    })
    assert accepted["passed"] is True
    assert rejected["passed"] is False
