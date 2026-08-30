from __future__ import annotations

import json
from pathlib import Path

import pytest

from okx_demo_multi_session_campaign import CampaignDecision, CampaignLimits
from okx_demo_multi_session_supervisor import (
    CampaignSupervisor,
    CampaignSupervisorError,
    verify_campaign_run,
)
from okx_fill_restart_offline import _sha256, _write_json
from tests.test_okx_demo_multi_session_supervisor import _prepared, _session


def _write_completed_child(package: object, slot: int, armed_at_ms: int) -> None:
    identity = package.slots[slot - 1]
    child = package.session_output(identity)
    evidence_path = child / "soak_run/audits/economic_session_evidence.json"
    completion_path = child / "soak_run/completion_hashes.json"
    _write_json(evidence_path, _session(package, slot, armed_at_ms))
    _write_json(
        completion_path,
        {"soak_run/audits/economic_session_evidence.json": _sha256(evidence_path)},
    )
    _write_json(child / "COMPLETED.json", {
        "status": "OKX_DEMO_ECONOMIC_SESSION_SUPPORT",
        "package_id": identity["package_id"],
        "run_id": identity["run_id"],
        "final_position_btc": "0",
        "final_open_orders": 0,
        "two_flat_empty_snapshots": True,
        "controller_engine_gateway_reconciled": True,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "completion_hashes_sha256": _sha256(completion_path),
    })


def test_completed_active_child_is_ingested_after_stale_lease_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(owner="first", now_ms=armed_at + 1, ttl_ms=100)
    supervisor.authorize_next_session(owner="first", now_ms=armed_at + 2)
    _write_completed_child(package, 1, armed_at)

    recovered = CampaignSupervisor.load(package)
    recovered_at = armed_at + 1_060_001
    recovered.acquire_lease(
        owner="recovery", now_ms=recovered_at, ttl_ms=21_900_000
    )
    decision = recovered.accept_active_session_artifact(
        owner="recovery", now_ms=recovered_at + 1
    )

    assert decision is CampaignDecision.IN_PROGRESS
    assert recovered.state()["completed_slots"] == [1]
    assert recovered.state()["active_slot"] is None
    assert recovered.state()["terminal_account_authoritative"] is True
    assert recovered.state()["terminal_final_position_btc"] == "0"
    assert recovered.state()["terminal_final_open_orders"] == 0
    events = recovered.events()
    assert events[-2]["event"] == "LEASE_ACQUIRED"
    assert events[-2]["payload"]["stale_recovery"] is True
    assert events[-1]["event"] == "SESSION_ACCEPTED"


def test_post_recovery_wall_expiry_fails_closed_without_authorizing_next_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 2_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(owner="first", now_ms=armed_at + 1, ttl_ms=100)
    supervisor.authorize_next_session(owner="first", now_ms=armed_at + 2)
    _write_completed_child(package, 1, armed_at)

    recovered_at = armed_at + 1_060_001
    recovered = CampaignSupervisor.load(package)
    recovered.acquire_lease(
        owner="recovery", now_ms=recovered_at, ttl_ms=21_900_000
    )
    assert recovered.accept_active_session_artifact(
        owner="recovery", now_ms=recovered_at + 1
    ) is CampaignDecision.IN_PROGRESS

    wall_expired = armed_at + CampaignLimits().maximum_campaign_wall_ms + 1
    with pytest.raises(CampaignSupervisorError, match="wall budget"):
        recovered.authorize_next_session(owner="recovery", now_ms=wall_expired)

    verified = verify_campaign_run(package)
    assert verified["terminal_decision"] == "NOT_READY"
    assert verified["sessions_completed"] == 1
    assert verified["active_slot"] is None
    terminal = json.loads(
        (package.output / "campaign_run/A2_CAMPAIGN_COMPLETED.json").read_text(
            encoding="utf-8"
        )
    )
    assert terminal["terminal_account_authoritative"] is True
    assert terminal["final_position_btc"] == "0"
    assert terminal["final_open_orders"] == 0
    assert terminal["sessions_completed"] == 1
    assert terminal["sessions_attempted"] == 1
    events = recovered.events()
    assert events[-1]["event"] == "CAMPAIGN_FAILED_CLOSED"
    assert events[-1]["payload"]["reason"] == "CAMPAIGN_WALL_BUDGET"
    assert sum(row["event"] == "SESSION_AUTHORIZED" for row in events) == 1

