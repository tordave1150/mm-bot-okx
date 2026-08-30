from pathlib import Path

import pytest

import okx_demo_multi_session_supervisor as legacy
import okx_demo_workoff_timestamp_campaign_prepare as prepare
import okx_demo_workoff_timestamp_campaign_supervisor as supervisor


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "workoff-timestamp-repair-offline-20260822T035814Z"


def test_failed_workoff_predecessor_is_immutable_and_nonresumable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["all_slots_attempted"] is True
    assert audit["completed_slots"] == list(range(1, 12))
    assert audit["failed_slots"] == [12]


def test_workoff_supervisor_uses_stable_campaign_decision_serialization() -> None:
    assert supervisor.base._decision_payload(legacy.CampaignDecision.NOT_READY) == {
        "decision": "NOT_READY"
    }


def test_failed_package_is_not_a_fresh_workoff_successor_package() -> None:
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(ROOT, supervisor.FAILED_PACKAGE_ID)
