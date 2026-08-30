from pathlib import Path

import pytest

import okx_demo_multi_session_supervisor as legacy
import okx_demo_terminal_special_closure_campaign_prepare as prepare
import okx_demo_terminal_special_closure_campaign_supervisor as supervisor


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "terminal-special-repair-offline-20260822T083020Z"


def test_failed_terminal_special_predecessor_is_immutable_and_nonresumable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["completed_slots"] == list(range(1, 11))
    assert audit["failed_slots"] == [11]
    assert audit["slot_12_started"] is False
    assert audit["special_flatten_sessions"] == 4


def test_failed_package_is_not_a_fresh_terminal_special_successor() -> None:
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(ROOT, supervisor.FAILED_PACKAGE_ID)
