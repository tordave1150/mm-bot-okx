from pathlib import Path

import okx_demo_r2_session1_fill_claim_reconciliation_repair_offline as repair
from okx_fill_restart_preflight import (
    MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
    preflight_protocol_id_for_evidence,
    verify_offline_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "r2-session1-fill-claim-repair-offline-20260902T065629Z"


def test_failed_session_is_not_ready_and_non_resumable() -> None:
    result = repair.verify_predecessor(ROOT)

    assert result["failed_campaign_decision"] == "NOT_READY"
    assert result["session_1_accepted"] is False
    assert result["resume_authorized"] is False
    assert result["terminal_account_authoritative"] is False


def test_fill_claim_repair_evidence_admits_only_multi_session_preflight() -> None:
    verified = verify_offline_evidence(ROOT, EVIDENCE_ID)

    assert verified["passed"] is True
    assert (
        verified["evidence_kind"]
        == "r2_session1_fill_claim_reconciliation_r0_offline_repair"
    )
    assert (
        preflight_protocol_id_for_evidence(verified["evidence_kind"])
        == MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID
    )
