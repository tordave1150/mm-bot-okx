from pathlib import Path
import okx_demo_r2_session1_interruption_audit_offline as audit

ROOT=Path(__file__).resolve().parents[1]
def test_expired_interrupted_session_is_non_resumable_and_account_unknown() -> None:
    result=audit.verify_predecessor(ROOT)
    assert result["campaign_wall_expired"] is True
    assert result["session_marker_reuse_refused"] is True
    assert result["authoritative_account_unknown"] is True
    assert result["resume_authorized"] is False
    assert result["retry_authorized"] is False
    assert result["session_2_started"] is False
