import json
from pathlib import Path

import pytest

import okx_demo_markout_special_closure_campaign_prepare as prepare
import okx_demo_markout_special_closure_campaign_supervisor as supervisor
import okx_demo_multi_session_supervisor as legacy


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "markout-special-repair-offline-20260826T124504Z"


def test_failed_markout_predecessor_is_immutable_and_nonresumable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["failed_slots"] == [1]
    assert audit["slots_2_through_12_started"] is False
    assert audit["normal_fill_count"] == 3
    assert audit["causal_markout_count"] == 2
    assert audit["terminal_special_closed_markout_count"] == 1


def test_failed_package_is_not_a_fresh_markout_successor() -> None:
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(ROOT, supervisor.FAILED_PACKAGE_ID)


def test_markout_successor_requires_all_campaign_bindings(tmp_path: Path) -> None:
    source = ROOT / "artifacts/okx_demo_multi_session_economic_soak/packages"
    package_id = "economic-package-20990101T000000Z"
    output = tmp_path / "artifacts/okx_demo_multi_session_economic_soak/packages" / package_id
    output.mkdir(parents=True)
    # A partial package must fail closed before execution; no network is involved.
    (output / "A2_PACKAGE_COMPLETED.json").write_text(
        json.dumps({"package_id": package_id}), encoding="utf-8"
    )
    assert source.is_dir()
    with pytest.raises(legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(tmp_path, package_id)
