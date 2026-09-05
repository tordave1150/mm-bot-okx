"""Deterministic offline tests for R2 Canonical Economic Qualification (Q01-Q03) Executor.

Tests all execution boundaries, fresh admission gates, canonical qualification mode,
AuditedCanonicalExchangeWrapper mutation guards, and Stage C Checkpoint evaluation
under strict socket denial.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_canonical_qualification_executor import (
    CANONICAL_CAMPAIGN_ID,
    EXPECTED_CANDIDATE_FINGERPRINT,
    AuditedCanonicalExchangeWrapper,
    execute_canonical_qualification_q01_q03,
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
        execute_canonical_qualification_q01_q03(
            exchange=mock_exchange,
            execution_scope="UNAUTHORIZED_SCOPE",
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_fixed_cycles_prohibited_in_qualification_mode() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Fixed cycle limit is strictly prohibited in ECONOMIC_QUALIFICATION mode"):
        execute_canonical_qualification_q01_q03(
            exchange=mock_exchange,
            execution_stage="ECONOMIC_QUALIFICATION",
            cycles_per_session=4,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_q04_to_q12_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Continuation violation: Sessions Q04-Q12 are strictly blocked"):
        execute_canonical_qualification_q01_q03(
            exchange=mock_exchange,
            execute_q04_to_q12=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_production_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Production access strictly prohibited"):
        execute_canonical_qualification_q01_q03(
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


def test_full_mock_qualification_execution_passes() -> None:
    test_run_id = "test-canonical-qualification-mock-run"
    target_dir = ROOT / "artifacts" / "r2_canonical_qualification_runs" / test_run_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_canonical_qualification_q01_q03(
            exchange=mock_exchange,
            run_id=test_run_id,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=2,
            warmup_ticks=2,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        assert run_dir.is_dir()

        # Check terminal marker
        marker_file = run_dir / "R2_CANONICAL_Q01_Q03_CHECKPOINT_COMPLETED.json"
        assert marker_file.is_file()
        marker = json.loads(marker_file.read_text(encoding="utf-8"))
        assert marker["status"] == "R2_CANONICAL_Q01_Q03_CHECKPOINT_PASSED"
        assert marker["informational_next_status"] == "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION"
        assert marker["economic_sessions_credited"] == 3
        assert marker["q04_started"] is False
        assert marker["prior_stage_c_runs_excluded"] is True
        assert marker["operational_canary_excluded"] is True
        assert marker["historical_cursor_fills_credited"] == 0
        assert marker["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT
        assert marker["behavioral_parameter_drift"] == 0

        # Check Checkpoint evaluation
        chk = json.loads((run_dir / "stage_c_checkpoint_evaluation.json").read_text(encoding="utf-8"))
        assert chk["checkpoint_decision"] == "R2_CANONICAL_Q01_Q03_CHECKPOINT_PASSED"
        assert chk["hard_safety_passed"] is True
        assert len(chk["hard_safety_checks"]) == 17
        assert all(chk["hard_safety_checks"].values())

        # Check per-session audits
        for slot in ["q01", "q02", "q03"]:
            audit_file = run_dir / f"{slot}_session_audit.json"
            assert audit_file.is_file()
            audit = json.loads(audit_file.read_text(encoding="utf-8"))
            assert audit["slot"].lower() == slot
            assert audit["terminal_position_btc"] == 0.0
            assert audit["terminal_open_orders"] == 0
            assert audit["admission_audit"]["admission_decision"] == "R2_ADMISSION_PASS"

        # Check operational diagnostics
        diag = json.loads((run_dir / "operational_and_economic_diagnostics.json").read_text(encoding="utf-8"))
        assert diag["sessions_evaluated"] == ["Q01", "Q02", "Q03"]
        assert diag["systemic_execution_defects_observed"] is False
        assert diag["historical_cursor_fills_credited"] == 0

    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
