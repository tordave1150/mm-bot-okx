"""Deterministic offline tests for R2 Canonical Economic Qualification (Q04-Q06) Executor.

Tests all execution boundaries, fresh admission gates, canonical qualification mode,
special flatten ceiling enforcement (<= 2 / 12), and Six-Session Checkpoint evaluation
under strict socket denial.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_canonical_q04_q06_executor import (
    CANONICAL_CAMPAIGN_ID,
    EXPECTED_CANDIDATE_FINGERPRINT,
    AuditedCanonicalExchangeWrapper,
    execute_canonical_qualification_q04_q06,
)
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard
from tests.test_okx_demo_r1_preflight_preparation import PreflightMockExchange

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Ensure socket denial is strictly active for every test in this module."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def test_unauthorized_scope_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Unauthorized execution scope"):
        execute_canonical_qualification_q04_q06(
            exchange=mock_exchange,
            execution_scope="UNAUTHORIZED_SCOPE",
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_fixed_cycles_prohibited_in_qualification_mode() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Fixed cycle limit is strictly prohibited in ECONOMIC_QUALIFICATION mode"):
        execute_canonical_qualification_q04_q06(
            exchange=mock_exchange,
            execution_stage="ECONOMIC_QUALIFICATION",
            cycles_per_session=4,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_q07_to_q12_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Continuation violation: Sessions Q07-Q12 are strictly blocked"):
        execute_canonical_qualification_q04_q06(
            exchange=mock_exchange,
            execute_q07_to_q12=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_production_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Production access strictly prohibited"):
        execute_canonical_qualification_q04_q06(
            exchange=mock_exchange,
            production_authorized=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_audited_wrapper_blocks_mutations() -> None:
    mock_exchange = PreflightMockExchange()
    audited = AuditedCanonicalExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED"):
        audited.set_leverage(3)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED"):
        audited.set_position_mode("net_mode")


def test_full_mock_q04_q06_qualification_execution_passes() -> None:
    test_run_id = "test-canonical-q04-q06-mock-run"
    target_dir = ROOT / "artifacts" / "r2_canonical_qualification_runs" / test_run_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_canonical_qualification_q04_q06(
            exchange=mock_exchange,
            run_id=test_run_id,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=2,
            warmup_ticks=2,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        assert run_dir.exists()

        expected_files = [
            "q04_session_audit.json",
            "q05_session_audit.json",
            "q06_session_audit.json",
            "six_session_checkpoint_evaluation.json",
            "operational_and_economic_diagnostics.json",
            "final_12_session_diagnostic_projection.json",
            "candidate_verification.json",
            "risk_and_boundary_audit.json",
            "canonical_qualification_q04_q06_manifest.json",
            "completion_hashes.json",
            "R2_CANONICAL_Q04_Q06_CHECKPOINT_COMPLETED.json",
        ]
        for fname in expected_files:
            assert (run_dir / fname).exists(), f"Missing expected artifact: {fname}"

        marker = json.loads((run_dir / "R2_CANONICAL_Q04_Q06_CHECKPOINT_COMPLETED.json").read_text(encoding="utf-8"))
        assert marker["status"] == "R2_CANONICAL_Q04_Q06_CHECKPOINT_COMPLETED"
        assert marker["decision"] == "R2_CANONICAL_Q04_Q06_CHECKPOINT_COMPLETED"
        assert marker["economic_sessions_credited"] == 6
        assert marker["q07_started"] is False
        assert marker["production_authorized"] is False
        assert marker["git_write_operation"] is False

        checkpoint = json.loads((run_dir / "six_session_checkpoint_evaluation.json").read_text(encoding="utf-8"))
        assert checkpoint["checkpoint_decision"] == "R2_CANONICAL_Q04_Q06_CHECKPOINT_COMPLETED"
        assert checkpoint["hard_safety_passed"] is True
        assert checkpoint["sessions_credited_total"] == 6

        proj = json.loads((run_dir / "final_12_session_diagnostic_projection.json").read_text(encoding="utf-8"))
        assert proj["qualification_sessions_credited"] == 6
        assert proj["qualification_sessions_remaining"] == 6
        assert "final_12_session_floor_projections" in proj

    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
