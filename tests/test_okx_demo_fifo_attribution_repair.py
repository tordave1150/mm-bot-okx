from pathlib import Path

import okx_demo_fifo_attribution_repair_offline as repair


ROOT = Path(__file__).resolve().parents[1]


def test_failed_fifo_campaign_is_immutable_and_not_reusable() -> None:
    audit = repair.verify_predecessor(ROOT)
    assert audit["package_id"] == repair.PACKAGE_ID
    assert audit["session_package_id"] == repair.SESSION_ID
    assert audit["normal_fill_count"] == 20
    assert audit["normal_fifo_round_trips"] == 11
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["slot_3_through_12_started"] is False


def test_failed_session_replays_with_extended_fifo_identity_audit() -> None:
    result = repair.replay_failed_session(ROOT)
    audit = result["extended_fifo_audit"]
    assert result["passed"] is True
    assert result["normal_fill_count"] == 20
    assert result["normal_fifo_round_trips"] == 11
    assert result["legacy_pair_bound"] == 10
    assert audit["normal_fifo_unique_completed_entry_fills"] == 11
    assert audit["normal_fifo_evidence_trade_ids"] <= 20
    assert audit["normal_fifo_exit_fill_reuse_count"] >= 1
    assert result["source_artifacts_modified"] is False
