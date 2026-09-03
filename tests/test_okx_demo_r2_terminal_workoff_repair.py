from pathlib import Path

import okx_demo_r2_terminal_workoff_repair_offline as repair
from okx_fill_restart_preflight import _preflight_predecessor_audit


ROOT = Path(__file__).resolve().parents[1]


def test_not_ready_campaign_is_immutable_and_non_resumable() -> None:
    audit = repair.verify_predecessor(ROOT)
    assert audit["terminal_decision"] == "NOT_READY"
    assert audit["terminal_position_open_orders"] == "0/0"
    assert audit["special_flatten_sessions"] == 5
    assert audit["normal_fill_count"] == 112
    assert audit["causal_reentry_records"] == 107
    assert audit["resume_authorized"] is False
    assert audit["identity_reuse_authorized"] is False


def test_r1_admission_accepts_only_the_terminal_workoff_r0_contract() -> None:
    accepted = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_terminal_workoff_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False,
        "failed_package_id": repair.PACKAGE_ID,
        "failed_campaign_run_id": repair.RUN_ID,
    })
    assert accepted["passed"] is True
    rejected = _preflight_predecessor_audit(ROOT, {
        "evidence_kind": "r2_terminal_workoff_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": False,
        "resume_authorized": False,
    })
    assert rejected["passed"] is False
