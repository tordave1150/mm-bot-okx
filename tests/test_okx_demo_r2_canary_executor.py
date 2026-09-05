"""Deterministic offline tests for R2 Session 1 Canary Execution.

Tests all authorization boundaries, Stage A fresh admission gates,
Stage B canary lifecycle, AuditedCanaryExchangeWrapper mutation guards,
and Stage B Hard Checkpoint 1 under strict socket denial.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_canary_executor import (
    CANONICAL_ARM_TOKEN,
    CANONICAL_SESSION_ID,
    EXPECTED_CANDIDATE_FINGERPRINT,
    AuditedCanaryExchangeWrapper,
    execute_r2_canary,
    verify_r2_final_admission_package,
)
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard
from tests.test_okx_demo_r1_preflight_preparation import PreflightMockExchange

ROOT = Path(__file__).resolve().parents[1]

FRESH_R0_EVIDENCE_ID = "r0-post-q06-campaign-wall-audit-offline-20260905T024551Z"
FRESH_R1_RUN_ID = "r1-preflight-run-20260905T025038Z"
FRESH_R2_PREP_REF = "r2-prep-20260905T025807Z"
FRESH_FINAL_PREP_REF = "r2-final-prep-20260905T043900Z"
FRESH_CAMPAIGN_ID = "r2-campaign-20260905T025807Z"
FRESH_SESSION_ID = "r2-session-20260905T025807Z-s01:p0:d6e26491"
FRESH_ARM_TOKEN = f"OKX_DEMO:{FRESH_SESSION_ID}"


def _fresh_canary_kwargs() -> dict[str, str]:
    return {
        "r0_evidence_id": FRESH_R0_EVIDENCE_ID,
        "r1_run_id": FRESH_R1_RUN_ID,
        "r2_prep_ref": FRESH_R2_PREP_REF,
        "final_prep_ref": FRESH_FINAL_PREP_REF,
        "campaign_id": FRESH_CAMPAIGN_ID,
        "session_id": FRESH_SESSION_ID,
        "arm_token": FRESH_ARM_TOKEN,
    }


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Ensure socket denial is strictly active for every test in this module."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


# 1. Authorization and Scope Boundaries
def test_wrong_execution_scope_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Unauthorized execution scope"):
        execute_r2_canary(
            exchange=mock_exchange,
            execution_scope="UNAUTHORIZED_FULL_CAMPAIGN",
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_requesting_more_than_session_1_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="must execute no more than Session 1"):
        execute_r2_canary(
            exchange=mock_exchange,
            execute_no_more_than_session_1=False,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_continuation_to_sessions_2_12_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Sessions 2-12 cannot be authorized in Canary"):
        execute_r2_canary(
            exchange=mock_exchange,
            continue_to_sessions_2_12=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_authorizing_production_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Production access strictly prohibited"):
        execute_r2_canary(
            exchange=mock_exchange,
            production_authorized=True,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_arm_token_mismatch_fails() -> None:
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Arm token mismatch"):
        execute_r2_canary(
            exchange=mock_exchange,
            arm_token="OKX_DEMO:wrong_token_here",
            tick_interval_s=0.0,
            resting_s=0.0,
        )


# 2. Candidate Fingerprint Drift
def test_candidate_fingerprint_drift_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_exchange = PreflightMockExchange()
    monkeypatch.setattr(
        "okx_demo_r2_canary_executor.compute_candidate_fingerprint",
        lambda root: {"candidate_fingerprint": "corrupted_fingerprint_123"},
    )
    with pytest.raises(DemoAdapterError, match="Candidate fingerprint drift detected"):
        execute_r2_canary(
            exchange=mock_exchange,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


# 3. Audited Exchange Wrapper Guards
def test_wrapper_blocks_set_position_mode() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedCanaryExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R2_CANARY"):
        wrapper.set_position_mode()


def test_wrapper_blocks_set_leverage() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedCanaryExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R2_CANARY"):
        wrapper.set_leverage()


def test_wrapper_blocks_transfer() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedCanaryExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R2_CANARY"):
        wrapper.transfer()


def test_wrapper_blocks_unknown_endpoint() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedCanaryExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="ENDPOINT_DENIED_UNKNOWN_CATEGORY"):
        _ = wrapper.some_unknown_exchange_method


def test_wrapper_blocks_non_post_only_order() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedCanaryExchangeWrapper(mock_exchange)
    with pytest.raises(DemoAdapterError, match="Non-post-only order creation is strictly prohibited"):
        wrapper.create_order(
            symbol="BTC/USDT:USDT",
            order_type="limit",
            side="buy",
            amount=1.0,
            price=49000.0,
            params={"postOnly": False},
        )


# 4. Stage A Admission Gate Failures
def test_stage_a_fails_on_nonzero_startup_position() -> None:
    test_stamp = "test_stage_a_nonzero_pos"
    target_dir = ROOT / "artifacts" / "r2_canary_runs" / f"r2-canary-run-{test_stamp}"
    try:
        mock_exchange = PreflightMockExchange()
        mock_exchange.position_btc = 0.05
        with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|position and fill accounting do not reconcile"):
            execute_r2_canary(
                stamp=test_stamp,
                exchange=mock_exchange,
                tick_interval_s=0.0,
                resting_s=0.0,
                **_fresh_canary_kwargs(),
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_stage_a_fails_on_foreign_open_orders() -> None:
    test_stamp = "test_stage_a_foreign_orders"
    target_dir = ROOT / "artifacts" / "r2_canary_runs" / f"r2-canary-run-{test_stamp}"
    try:
        mock_exchange = PreflightMockExchange()
        mock_exchange.orders["foreign_order_1"] = {
            "id": "foreign_order_1",
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "status": "open",
            "amount": 1,
            "price": 40000.0,
        }
        with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|prior session has unresolved state"):
            execute_r2_canary(
                stamp=test_stamp,
                exchange=mock_exchange,
                tick_interval_s=0.0,
                resting_s=0.0,
                **_fresh_canary_kwargs(),
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_stage_a_fails_on_clock_skew_violation() -> None:
    test_stamp = "test_stage_a_clock_skew"
    target_dir = ROOT / "artifacts" / "r2_canary_runs" / f"r2-canary-run-{test_stamp}"
    try:
        mock_exchange = PreflightMockExchange()
        mock_exchange.clock_offset_ms = 3500  # > 1500 ms limit
        with pytest.raises(DemoAdapterError, match="clock skew exceeds frozen limit"):
            execute_r2_canary(
                stamp=test_stamp,
                exchange=mock_exchange,
                tick_interval_s=0.0,
                resting_s=0.0,
                **_fresh_canary_kwargs(),
            )
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# 5. End-to-End Canary Execution & Checkpoint 1
def test_canary_execution_passes_cleanly() -> None:
    test_stamp = "test_canary_e2e_pass"
    target_dir = ROOT / "artifacts" / "r2_canary_runs" / f"r2-canary-run-{test_stamp}"
    try:
        mock_exchange = PreflightMockExchange()
        run_dir = execute_r2_canary(
            root=ROOT,
            stamp=test_stamp,
            exchange=mock_exchange,
            lifecycle_cycles=2,
            tick_interval_s=0.0,
            resting_s=0.0,
            **_fresh_canary_kwargs(),
        )
        assert run_dir.is_dir()

        # Verify Stage A admission audit
        stage_a = json.loads((run_dir / "stage_a_admission_audit.json").read_text(encoding="utf-8"))
        assert stage_a["admission_decision"] == "R2_ADMISSION_PASS"
        assert stage_a["position_btc"] == 0.0
        assert stage_a["open_orders_count"] == 0
        assert stage_a["zero_account_mutations"] is True

        # Verify Stage B Canary evidence
        canary = json.loads((run_dir / "stage_b_session1_canary_evidence.json").read_text(encoding="utf-8"))
        assert canary["cycles_executed"] == 2
        assert canary["terminal_position_btc"] == 0.0
        assert canary["terminal_open_orders"] == 0

        # Verify Hard Checkpoint 1
        chk1 = json.loads((run_dir / "hard_checkpoint_1_audit.json").read_text(encoding="utf-8"))
        assert chk1["checkpoint_decision"] == "PASS"
        assert chk1["passed_count"] == 17
        assert len(chk1["failed_checks"]) == 0

        # Verify endpoint audit: zero account mutations
        endpoints = json.loads((run_dir / "endpoint_audit.json").read_text(encoding="utf-8"))
        assert endpoints["zero_account_mutations_proven"] is True
        assert endpoints["zero_live_endpoints_proven"] is True

        # Verify terminal completion marker
        terminal = json.loads((run_dir / "R2_CANARY_EXECUTION_COMPLETED.json").read_text(encoding="utf-8"))
        assert terminal["status"] == "R2_CANARY_PASSED"
        assert terminal["sessions_executed"] == 1
        assert terminal["session_2_started"] is False
        assert terminal["continue_to_sessions_2_12"] is False
        assert terminal["production_authorized"] is False
        assert terminal["informational_next_status"] == "R2_STAGE_C_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"

        # Verify secrecy hygiene: zero credentials in any artifact
        for json_file in run_dir.rglob("*.json"):
            content = json_file.read_text(encoding="utf-8")
            for forbidden in ["apiKey", "secret", "password", "passphrase"]:
                assert f'"{forbidden}": "' not in content

    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_final_admission_rejects_mismatched_session_before_exchange_access() -> None:
    with pytest.raises(DemoAdapterError, match="Session 1 identity mismatch"):
        verify_r2_final_admission_package(
            root=ROOT,
            final_prep_ref=FRESH_FINAL_PREP_REF,
            r0_evidence_id=FRESH_R0_EVIDENCE_ID,
            r1_run_id=FRESH_R1_RUN_ID,
            r2_prep_ref=FRESH_R2_PREP_REF,
            campaign_id=FRESH_CAMPAIGN_ID,
            session_id="r2-session-wrong",
            arm_token="OKX_DEMO:r2-session-wrong",
            candidate_fingerprint=EXPECTED_CANDIDATE_FINGERPRINT,
        )
