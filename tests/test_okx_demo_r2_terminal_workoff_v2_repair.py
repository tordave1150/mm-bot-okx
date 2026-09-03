from pathlib import Path

import okx_demo_r2_terminal_workoff_v2_repair_offline as repair
from okx_fill_restart_preflight import _preflight_predecessor_audit


ROOT = Path(__file__).resolve().parents[1]


def test_current_campaign_is_frozen_not_ready_without_reuse() -> None:
    audit = repair.verify_predecessor(ROOT)
    assert audit["terminal_decision"] == "NOT_READY"
    assert audit["special_flatten_sessions"] == 3
    assert audit["maximum_special_flatten_sessions"] == 2
    assert audit["completed_sessions"] == 10
    assert audit["unaccepted_session"] == 11
    assert audit["accept_session_11_authorized"] is False
    assert audit["start_session_12_authorized"] is False
    assert audit["resume_authorized"] is False
    assert audit["identity_reuse_authorized"] is False


def test_special_sessions_are_counted_per_session_not_per_fill() -> None:
    audit = repair.verify_predecessor(ROOT)
    rows = audit["special_session_audits"]
    assert [row["slot"] for row in rows] == [4, 8, 11]
    assert rows[0]["special_fill_count"] == 3
    assert all(row["special_flatten"] for row in rows)


def test_v2_terminal_workoff_evidence_is_admitted_and_fails_closed() -> None:
    accepted = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_terminal_workoff_v2_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False,
        "failed_package_id": repair.PACKAGE_ID,
        "failed_campaign_run_id": repair.RUN_ID,
    })
    assert accepted["passed"] is True
    rejected = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_terminal_workoff_v2_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": False,
        "resume_authorized": False,
    })
    assert rejected["passed"] is False
