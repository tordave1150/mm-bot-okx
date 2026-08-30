import json
from pathlib import Path

import pytest

import okx_demo_owned_cancel_reconciliation_campaign_prepare as prepare
import okx_demo_owned_cancel_reconciliation_campaign_supervisor as supervisor
import okx_demo_multi_session_supervisor as legacy


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "owned-cancel-repair-offline-20260826T155324Z"


def test_failed_owned_cancel_predecessor_is_immutable_and_nonresumable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["completed_slots"] == [1, 2, 3, 4]
    assert audit["failed_slots"] == [5]
    assert audit["slots_6_through_12_started"] is False
    assert audit["attempted_normal_fills"] == 60
    assert audit["attempted_fifo_round_trips"] == 29
    assert audit["mutation_retries"] == 0


def test_failed_package_is_not_a_fresh_owned_cancel_successor() -> None:
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(ROOT, supervisor.FAILED_PACKAGE_ID)


def test_owned_cancel_successor_requires_complete_package(tmp_path: Path) -> None:
    package_id = "economic-package-20990101T000000Z"
    output = (
        tmp_path / "artifacts/okx_demo_multi_session_economic_soak/packages"
        / package_id
    )
    output.mkdir(parents=True)
    (output / "A2_PACKAGE_COMPLETED.json").write_text(
        json.dumps({"package_id": package_id}), encoding="utf-8"
    )
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(tmp_path, package_id)
