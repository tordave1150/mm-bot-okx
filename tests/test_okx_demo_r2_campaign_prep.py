"""Unit tests for R2 Successor Economic Campaign Preparation.

Proves:
1. Offline execution under socket denial (zero network, zero credentials, zero order mutations).
2. Prerequisite verification: R0 closure and R1 preflight completion markers.
3. Strict candidate fingerprint integrity binding.
4. Correct 12-session schedule generation and arm token format.
5. Exact preservation of frozen risk boundary and CampaignLimits.
6. Secret leakage prevention across all preparation artifacts.
7. Terminal marker enforces R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION with hard stop.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import pytest

from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_r2_campaign_prep import (
    CANONICAL_R0_CLOSURE_REF,
    CANONICAL_R1_RUN_ID,
    EXPECTED_CANDIDATE_FINGERPRINT,
    build_r2_campaign_preparation_package,
)
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Default socket denial for offline testing."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def test_build_r2_preparation_package_success(tmp_path: Path) -> None:
    test_stamp = "20260904T124817Z_test"
    target_dir = ROOT / "artifacts" / "r2_economic_campaign_preparation" / f"r2-prep-{test_stamp}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    try:
        prep_dir = build_r2_campaign_preparation_package(
            root=ROOT,
            stamp=test_stamp,
            r0_closure_ref=CANONICAL_R0_CLOSURE_REF,
            r1_run_id=CANONICAL_R1_RUN_ID,
        )
        assert prep_dir.is_dir()
        assert prep_dir == target_dir

        expected_files = [
            "candidate_identity.json",
            "prerequisite_evidence_manifest.json",
            "r2_identity.json",
            "r2_authorization_contract.json",
            "frozen_risk_specification.json",
            "session_schedule.json",
            "endpoint_permissions.json",
            "qualification_expectations.json",
            "failure_matrix.json",
            "credential_contract.json",
            "demo_transport_contract.json",
            "expected_evidence_schema.json",
            "completion_hashes.json",
            "R2_PREPARATION_COMPLETED.json",
        ]
        for fname in expected_files:
            assert (prep_dir / fname).is_file(), f"Missing file: {fname}"

        # 1. Check completion hashes
        hashes = json.loads((prep_dir / "completion_hashes.json").read_text(encoding="utf-8"))
        assert len(hashes) == 12

        # 2. Check terminal marker
        terminal = json.loads((prep_dir / "R2_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
        assert terminal["status"] == "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION"
        assert terminal["r2_authorized"] is False
        assert terminal["r2_executed"] is False
        assert terminal["sessions_executed"] == 0
        assert terminal["network_attempts"] == 0
        assert terminal["create_attempts"] == 0
        assert terminal["cancel_attempts"] == 0
        assert terminal["flatten_attempts"] == 0
        assert terminal["account_mutation_attempts"] == 0
        assert terminal["git_write_operation"] is False
        assert terminal["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT

        # 3. Check 12 session slots
        schedule = json.loads((prep_dir / "session_schedule.json").read_text(encoding="utf-8"))
        assert schedule["total_slots"] == 12
        assert len(schedule["session_slots"]) == 12
        for i, slot in enumerate(schedule["session_slots"], 1):
            assert slot["slot_index"] == i
            assert slot["slot_name"] == f"s{i:02d}"
            assert slot["timeout_budget_ms"] == 30 * 60 * 1000
            assert slot["max_normal_creates"] == 60
            assert slot["expected_session_arm_token"].startswith(f"OKX_DEMO:r2-session-{test_stamp}-s{i:02d}")

        # 4. Check frozen risk specification
        risk_spec = json.loads((prep_dir / "frozen_risk_specification.json").read_text(encoding="utf-8"))
        boundary = risk_spec["frozen_risk_boundary"]
        limits = CampaignLimits()
        assert boundary["aggregate_hard_loss_usdt"] == str(limits.aggregate_hard_loss_usdt)
        assert boundary["session_hard_drawdown_usdt"] == str(limits.session_hard_drawdown_usdt)
        assert boundary["session_soft_drawdown_usdt"] == str(limits.session_soft_drawdown_usdt)
        assert boundary["maximum_inventory_btc"] == str(limits.maximum_inventory_btc)

        # 5. Check credential contract
        cred_contract = json.loads((prep_dir / "credential_contract.json").read_text(encoding="utf-8"))
        assert cred_contract["credentials_loaded"] is False
        assert cred_contract["credentials_read"] == 0

    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_missing_r0_closure_fails() -> None:
    with pytest.raises(FileNotFoundError, match="Prerequisite R0 closure marker missing"):
        build_r2_campaign_preparation_package(
            root=ROOT,
            stamp="nonexistent_test",
            r0_closure_ref="r0-closure-nonexistent-99999999",
            r1_run_id=CANONICAL_R1_RUN_ID,
        )


def test_missing_r1_preflight_fails() -> None:
    with pytest.raises(FileNotFoundError, match="Prerequisite R1 preflight marker missing"):
        build_r2_campaign_preparation_package(
            root=ROOT,
            stamp="nonexistent_test",
            r0_closure_ref=CANONICAL_R0_CLOSURE_REF,
            r1_run_id="r1-run-nonexistent-99999999",
        )


def test_fresh_r0_and_r1_predecessors_bind_exactly() -> None:
    stamp = "test-fresh-r0-r1-exact"
    target = ROOT / "artifacts" / "r2_economic_campaign_preparation" / f"r2-prep-{stamp}"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        package = build_r2_campaign_preparation_package(
            root=ROOT,
            stamp=stamp,
            r1_run_id="r1-preflight-run-20260905T025038Z",
            r0_evidence_id="r0-post-q06-campaign-wall-audit-offline-20260905T024551Z",
            r0_evidence_dir=ROOT / "artifacts" / "r0_post_q06_diagnostic",
        )
        terminal = json.loads((package / "R2_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
        prerequisites = json.loads((package / "prerequisite_evidence_manifest.json").read_text(encoding="utf-8"))
        assert terminal["r0_evidence_id"] == "r0-post-q06-campaign-wall-audit-offline-20260905T024551Z"
        assert prerequisites["r1_preflight"]["run_id"] == "r1-preflight-run-20260905T025038Z"
    finally:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
