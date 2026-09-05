"""Deterministic offline tests for R2 Stage C (Q01-Q03) Three-Session Checkpoint Execution.

Tests all execution boundaries, fresh admission gates, Stage C lifecycle,
AuditedStageCExchangeWrapper mutation guards, and Stage C Hard Checkpoint 2
under strict socket denial.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_stage_c_executor import (
    CANONICAL_CAMPAIGN_ID,
    EXPECTED_CANDIDATE_FINGERPRINT,
    AuditedStageCExchangeWrapper,
    execute_r2_stage_c,
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


# 1. Authorization & Scope Invariant Tests
def test_unauthorized_scope_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Unauthorized execution scope"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            execution_scope="UNAUTHORIZED_SCOPE",
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_requesting_more_than_q03_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="must execute exactly Q01-Q03"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            execute_q01_to_q03_only=False,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_q04_to_q12_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Sessions Q04-Q12 are strictly blocked"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            execute_q04_to_q12=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_production_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Production access strictly prohibited"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            production_authorized=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


# 2. Candidate Fingerprint Invariant Tests
def test_candidate_fingerprint_drift_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_exchange = PreflightMockExchange()
    monkeypatch.setattr(
        "okx_demo_r2_stage_c_executor.compute_candidate_fingerprint",
        lambda root: {"candidate_fingerprint": "corrupted_candidate_fingerprint_999"},
    )
    with pytest.raises(DemoAdapterError, match="Candidate fingerprint drift detected"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


# 3. Audited Exchange Wrapper Guards
def test_audited_wrapper_blocks_account_mutations() -> None:
    mock_exchange = PreflightMockExchange()
    audited = AuditedStageCExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R2_STAGE_C"):
        audited.set_leverage(3)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R2_STAGE_C"):
        audited.set_position_mode("net_mode")


def test_audited_wrapper_blocks_live_endpoints() -> None:
    mock_exchange = PreflightMockExchange()
    audited = AuditedStageCExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="LIVE_ENDPOINT_ATTEMPT_PROHIBITED"):
        _ = audited.live_trading_endpoint


def test_audited_wrapper_enforces_post_only_on_normal_quotes() -> None:
    mock_exchange = PreflightMockExchange()
    audited = AuditedStageCExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="Normal-path non-post-only order creation is strictly prohibited"):
        audited.create_order(
            "BTC/USDT:USDT",
            "market",
            "buy",
            0.01,
            params={"postOnly": False},
        )


# 4. Fresh Admission Gate Invariant Tests
def test_admission_fails_on_clock_skew_exceeding_1500ms() -> None:
    test_stamp = "test_admission_skew"
    target_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    mock_exchange = PreflightMockExchange()
    mock_exchange.clock_offset_ms = 2000  # 2000 ms skew > 1500 ms limit
    try:
        with pytest.raises(DemoAdapterError, match="clock skew exceeds frozen limit|Fresh admission gate failed closed"):
            execute_r2_stage_c(
                exchange=mock_exchange,
                stamp=test_stamp,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_admission_fails_on_nonzero_position() -> None:
    test_stamp = "test_admission_pos"
    target_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    mock_exchange = PreflightMockExchange()
    mock_exchange.position_btc = 0.01
    try:
        with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|Fresh admission gate failed closed"):
            execute_r2_stage_c(
                exchange=mock_exchange,
                stamp=test_stamp,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_admission_fails_on_open_orders() -> None:
    test_stamp = "test_admission_orders"
    target_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    mock_exchange = PreflightMockExchange()
    mock_exchange.orders = {"test_order": {"id": "test_order", "symbol": "BTC/USDT:USDT"}}
    try:
        with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|Fresh admission gate failed closed"):
            execute_r2_stage_c(
                exchange=mock_exchange,
                stamp=test_stamp,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# 5. Full Stage C (Q01-Q03) Mock Execution & Checkpoint 2 Verification
def test_full_stage_c_mock_execution_passes() -> None:
    test_stamp = "test_stage_c_mock_pass"
    target_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_r2_stage_c(
            exchange=mock_exchange,
            stamp=test_stamp,
            warmup_ticks=2,
            cycles_per_session=2,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        assert run_dir.is_dir()

        # Check terminal marker
        terminal = json.loads((run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json").read_text(encoding="utf-8"))
        assert terminal["status"] == "R2_THREE_SESSION_CHECKPOINT_PASSED"
        assert terminal["informational_next_status"] == "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION"
        assert terminal["sessions_executed"] == 3
        assert terminal["q04_started"] is False
        assert terminal["q01_q03_execution_authorized"] is True
        assert terminal["q04_q12_execution_authorized"] is False
        assert terminal["production_authorized"] is False
        assert terminal["operational_canary_excluded"] is True
        assert terminal["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT
        assert terminal["behavioral_parameter_drift"] == 0

        # Check Checkpoint 2 evaluation
        chk = json.loads((run_dir / "checkpoint_2_evaluation.json").read_text(encoding="utf-8"))
        assert chk["checkpoint_decision"] == "R2_THREE_SESSION_CHECKPOINT_PASSED"
        assert chk["hard_safety_passed"] is True
        assert chk["sessions_executed"] == 3
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

        # Verify zero secrets serialized
        for item in run_dir.rglob("*.json"):
            content = item.read_text(encoding="utf-8")
            for forbidden in ["apiKey", "secret", "password", "passphrase"]:
                assert f'"{forbidden}": "' not in content
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
