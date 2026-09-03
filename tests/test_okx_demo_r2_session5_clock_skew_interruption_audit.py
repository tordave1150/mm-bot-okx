from pathlib import Path

from okx_demo_r2_session5_clock_skew_interruption_audit_offline import verify_predecessor
from okx_fill_restart_preflight import _preflight_predecessor_audit


ROOT = Path(__file__).resolve().parents[1]


def test_session5_clock_skew_evidence_is_immutable_and_pre_mutation() -> None:
    audit = verify_predecessor(ROOT)
    assert audit["failed_campaign_decision"] == "NOT_READY"
    assert audit["clock_skew_gate_failed_before_mutation"] is True
    assert audit["create_amend_cancel_flatten"] == [0, 0, 0, 0]
    assert audit["terminal_position_open_orders"] == ["0", 0]
    assert audit["controller_engine_gateway_reconciled"] is False
    assert audit["resume_authorized"] is False
    assert audit["accept_authorized"] is False


def test_preflight_admission_accepts_only_the_immutable_session5_audit() -> None:
    allowed = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_session5_clock_skew_interruption_audit_r0_offline",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False,
        "accept_authorized": False,
        "failed_session_slot": 5,
        "failed_package_id": "economic-package-20260901T141747Z",
        "failed_campaign_run_id": "economic-campaign-run-20260901T141747Z",
    })
    denied = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_session5_clock_skew_interruption_audit_r0_offline",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False,
        "accept_authorized": True,
        "failed_session_slot": 5,
    })
    assert allowed["passed"] is True
    assert allowed["resume_authorized"] is False
    assert denied["passed"] is False
