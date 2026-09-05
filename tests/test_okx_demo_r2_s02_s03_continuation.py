from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_s02_s03_continuation import (
    admit_current_stage_c_execution,
    build_current_stage_c_continuation_package,
    execute_current_stage_c_session,
    verify_current_stage_c_predecessors,
)
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard
from tests.test_okx_demo_r1_preflight_preparation import PreflightMockExchange

ROOT = Path(__file__).resolve().parents[1]
ARGS = {
    "r0_evidence_id": "r0-post-q06-campaign-wall-audit-offline-20260905T024551Z",
    "r1_run_id": "r1-preflight-run-20260905T025038Z",
    "r2_prep_ref": "r2-prep-20260905T025807Z",
    "final_prep_ref": "r2-final-prep-20260905T043900Z",
    "canary_run_id": "r2-canary-run-20260905T045300Z",
    "campaign_id": "r2-campaign-20260905T025807Z",
}


@pytest.fixture(autouse=True)
def socket_denied() -> None:
    guard = _OfflineSocketGuard()
    with guard:
        yield
    assert guard.attempts == []


def test_current_chain_returns_only_s02_and_s03() -> None:
    canary, slots, deadline = verify_current_stage_c_predecessors(root=ROOT, **ARGS)
    assert canary["canary_hard_checkpoint_passed"] is True
    assert [slot["slot_index"] for slot in slots] == [2, 3]
    assert all(slot["expected_session_arm_token"] == f"OKX_DEMO:{slot['session_id']}" for slot in slots)
    assert deadline.isoformat().startswith("2026-09-05T08:58:07")


def test_package_binds_current_sessions_without_creating_a_campaign() -> None:
    stamp = "test-current-stage-c"
    output = ROOT / "artifacts" / "r2_current_stage_c_preparation" / f"r2-current-stage-c-prep-{stamp}"
    try:
        package = build_current_stage_c_continuation_package(root=ROOT, stamp=stamp, **ARGS)
        terminal = json.loads((package / "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json").read_text())
        contract = json.loads((package / "session_contract.json").read_text())
        assert terminal["campaign_id"] == ARGS["campaign_id"]
        assert [slot["slot_index"] for slot in contract["sessions"]] == [2, 3]
        assert terminal["orders_created"] == 0
        assert terminal["network_access"] is False
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_wrong_canary_identity_fails_closed() -> None:
    bad = dict(ARGS)
    bad["canary_run_id"] = "r2-canary-run-wrong"
    with pytest.raises(DemoAdapterError, match="Missing completion hashes"):
        verify_current_stage_c_predecessors(root=ROOT, **bad)


def test_execution_admission_accepts_exact_s02_before_deadline() -> None:
    slot = admit_current_stage_c_execution(
        root=ROOT,
        continuation_prep_ref="r2-current-stage-c-prep-20260905T032200Z",
        campaign_id=ARGS["campaign_id"],
        session_id="r2-session-20260905T025807Z-s02:p0:aff7afc4",
        arm_token="OKX_DEMO:r2-session-20260905T025807Z-s02:p0:aff7afc4",
        now=datetime(2026, 9, 5, 3, 30, tzinfo=timezone.utc),
    )
    assert slot["slot_index"] == 2


def test_execution_admission_blocks_when_full_session_cannot_fit() -> None:
    with pytest.raises(DemoAdapterError, match="wall-time is insufficient"):
        admit_current_stage_c_execution(
            root=ROOT,
            continuation_prep_ref="r2-current-stage-c-prep-20260905T032200Z",
            campaign_id=ARGS["campaign_id"],
            session_id="r2-session-20260905T025807Z-s02:p0:aff7afc4",
            arm_token="OKX_DEMO:r2-session-20260905T025807Z-s02:p0:aff7afc4",
            now=datetime(2026, 9, 5, 8, 29, tzinfo=timezone.utc),
        )


def test_single_session_executor_uses_only_admitted_s02_fixture() -> None:
    stamp = "test-current-s02"
    output = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{stamp}"
    try:
        run_dir = execute_current_stage_c_session(
            root=ROOT,
            continuation_prep_ref="r2-current-stage-c-prep-20260905T032200Z",
            campaign_id=ARGS["campaign_id"],
            session_id="r2-session-20260905T025807Z-s02:p0:aff7afc4",
            arm_token="OKX_DEMO:r2-session-20260905T025807Z-s02:p0:aff7afc4",
            stamp=stamp,
            execution_stage="TEST_FIXTURE",
            exchange=PreflightMockExchange(),
            load_env_file=False,
            warmup_ticks=2,
            cycles_per_session=2,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        terminal = json.loads((run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json").read_text())
        assert terminal["sessions_executed"] == 1
        assert terminal["status"] == "R2_CURRENT_SINGLE_SESSION_TERMINAL_PASSED"
        assert terminal["r2_stage_c_preparation_ref"] == "r2-current-stage-c-prep-20260905T032200Z"
    finally:
        if output.exists():
            shutil.rmtree(output)
