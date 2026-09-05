"""Unit tests for R1 Read-Only OKX Demo Preflight Executor.

Proves:
1. Allowlist enforcement: only permitted read-only endpoints are called.
2. Denylist enforcement: order/account mutations are strictly blocked and counted.
3. Unknown endpoints fail closed.
4. Arm token validation fails closed on mismatch.
5. Secret leakage scan verifies zero secrets in output artifacts.
6. Execution under socket denial with mock exchange produces valid evidence package.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r1_preflight_executor import (
    AuditedReadOnlyExchangeWrapper,
    run_r1_read_only_preflight,
    CANONICAL_ARM_TOKEN,
    CANONICAL_R1_RUN_ID,
    CANONICAL_R1_SESSION_ID,
    CANONICAL_R1_PACKAGE_ID,
    verify_r1_preflight_preparation,
)
from okx_demo_r0_closure_r1_prep import build_r1_preflight_preparation_package
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard
from tests.test_okx_demo_r1_preflight_preparation import PreflightMockExchange

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Default socket denial for offline testing."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def test_audited_wrapper_allows_read_endpoints() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(mock_exchange)

    # Allowed read calls
    wrapper.fetch_markets()
    wrapper.fetch_time()
    wrapper.fetch_balance()
    wrapper.fetch_positions(["BTC/USDT:USDT"])
    wrapper.fetch_open_orders("BTC/USDT:USDT")
    wrapper.fetch_my_trades("BTC/USDT:USDT")
    wrapper.fetch_leverage("BTC/USDT:USDT", {"mgnMode": "isolated"})
    wrapper.fetch_trading_fee("BTC/USDT:USDT")
    wrapper.fetch_position_mode("BTC/USDT:USDT")

    assert wrapper.endpoint_call_counts["fetch_markets"] == 1
    assert wrapper.endpoint_call_counts["fetch_time"] == 1
    assert wrapper.endpoint_call_counts["fetch_balance"] == 1
    assert wrapper.endpoint_call_counts["fetch_positions"] == 1
    assert wrapper.endpoint_call_counts["fetch_open_orders"] == 1
    assert wrapper.endpoint_call_counts["fetch_my_trades"] == 1
    assert wrapper.endpoint_call_counts["fetch_leverage"] == 1
    assert wrapper.endpoint_call_counts["fetch_trading_fee"] == 1
    assert wrapper.endpoint_call_counts["fetch_position_mode"] == 1
    assert wrapper.create_attempts == 0
    assert wrapper.cancel_attempts == 0
    assert wrapper.flatten_attempts == 0
    assert wrapper.account_mutation_attempts == 0


def test_audited_wrapper_prohibits_mutations() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(mock_exchange)

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: create_order"):
        wrapper.create_order()
    assert wrapper.create_attempts == 1

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: cancel_order"):
        wrapper.cancel_order()
    assert wrapper.cancel_attempts == 1

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: cancel_all_owned"):
        wrapper.cancel_all_owned()
    assert wrapper.cancel_attempts == 2

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: submit_emergency_flatten"):
        wrapper.submit_emergency_flatten()
    assert wrapper.flatten_attempts == 1

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: set_position_mode"):
        wrapper.set_position_mode()
    assert wrapper.account_mutation_attempts == 1

    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1: set_leverage"):
        wrapper.set_leverage()
    assert wrapper.account_mutation_attempts == 2


def test_audited_wrapper_prohibits_unknown_endpoints() -> None:
    mock_exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(mock_exchange)

    with pytest.raises(DemoAdapterError, match="ENDPOINT_DENIED_UNKNOWN_CATEGORY: unknown_third_party_op"):
        _ = wrapper.unknown_third_party_op


def test_run_preflight_wrong_arm_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKX_API_KEY", "dummy_key")
    monkeypatch.setenv("OKX_SECRET", "dummy_secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "dummy_pass")

    with pytest.raises(DemoAdapterError, match="Arm token mismatch"):
        run_r1_read_only_preflight(
            run_id=CANONICAL_R1_RUN_ID,
            session_id=CANONICAL_R1_SESSION_ID,
            arm_token="WRONG_ARM_TOKEN",
        )


def test_run_preflight_missing_credentials_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_SECRET", raising=False)
    monkeypatch.delenv("OKX_PASSPHRASE", raising=False)

    with pytest.raises(DemoAdapterError, match="Missing required OKX credentials"):
        run_r1_read_only_preflight(
            run_id=CANONICAL_R1_RUN_ID,
            session_id=CANONICAL_R1_SESSION_ID,
            arm_token=CANONICAL_ARM_TOKEN,
            load_env_file=False,
            api_key="",
            api_secret="",
            passphrase="",
        )


def test_run_preflight_mock_offline_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prep = build_r1_preflight_preparation_package(
        tmp_path,
        "mock-test-01",
        tmp_path / "r0_evidence",
        "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf",
        r0_evidence_id="r0-mock-test-01",
    )
    identity = json.loads((prep / "r1_identity.json").read_text(encoding="utf-8"))
    test_run_id = identity["run_id"]
    target_dir = ROOT / "artifacts" / "r1_read_only_preflight_runs" / test_run_id
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        res = run_r1_read_only_preflight(
            run_id=test_run_id,
            session_id=identity["session_id"],
            arm_token=identity["expected_arm_token"],
            package_id=identity["package_id"],
            r0_evidence_id="r0-mock-test-01",
            prep_dir=prep,
            exchange=mock_exchange,
            api_key="mock_api_key_for_test",
            api_secret="mock_secret_for_test",
            passphrase="mock_passphrase_for_test",
        )
        assert res["status"] == "R1_PREFLIGHT_PASSED"
        assert res["create_attempts"] == 0
        assert res["cancel_attempts"] == 0
        assert res["flatten_attempts"] == 0
        assert res["account_mutation_attempts"] == 0
        assert res["live_endpoint_attempts"] == 0
        assert res["r2_authorized"] is False

        # Verify all 10 artifact files were created
        expected_files = [
            "candidate_identity.json",
            "preflight_authorization.json",
            "transport_audit.json",
            "endpoint_audit.json",
            "account_snapshot.json",
            "reconciliation_audit.json",
            "safety_audit.json",
            "preflight_decision.json",
            "completion_hashes.json",
            "R1_PREFLIGHT_PASSED.json",
        ]
        for fname in expected_files:
            assert (target_dir / fname).is_file(), f"Missing expected artifact: {fname}"

        # Verify completion hashes integrity
        completion_hashes = json.loads((target_dir / "completion_hashes.json").read_text(encoding="utf-8"))
        assert len(completion_hashes) >= 9
        for rel_path, file_hash in completion_hashes.items():
            assert (target_dir / rel_path).is_file()

        # Verify secret scan passed (no mock secrets in artifacts)
        for item in target_dir.rglob("*.json"):
            content = item.read_text(encoding="utf-8")
            assert "mock_api_key_for_test" not in content
            assert "mock_secret_for_test" not in content
            assert "mock_passphrase_for_test" not in content
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_fresh_package_exact_identity_is_preserved_in_r1_evidence(tmp_path: Path) -> None:
    evidence_id = "r0-post-q06-campaign-wall-audit-offline-20260905T024551Z"
    prep = build_r1_preflight_preparation_package(
        tmp_path,
        "test-r1-exact-predecessor",
        tmp_path / "r0_evidence",
        "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf",
        r0_evidence_id=evidence_id,
    )
    identity = json.loads((prep / "r1_identity.json").read_text(encoding="utf-8"))
    run_id = identity["run_id"]
    target = ROOT / "artifacts" / "r1_read_only_preflight_runs" / run_id
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        result = run_r1_read_only_preflight(
            run_id=run_id,
            session_id=identity["session_id"],
            arm_token=identity["expected_arm_token"],
            package_id=identity["package_id"],
            r0_evidence_id=evidence_id,
            prep_dir=prep,
            exchange=PreflightMockExchange(),
            api_key="mock-key",
            api_secret="mock-secret",
            passphrase="mock-passphrase",
        )
        assert result["r0_evidence_id"] == evidence_id
        written = json.loads((target / "R1_PREFLIGHT_PASSED.json").read_text(encoding="utf-8"))
        assert written["r0_evidence_id"] == evidence_id
        assert written["r1_package_id"] == identity["package_id"]
    finally:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def test_exact_r0_evidence_mismatch_fails_before_transport(tmp_path: Path) -> None:
    prep = build_r1_preflight_preparation_package(
        tmp_path,
        "test-r1-reject-predecessor",
        tmp_path / "r0_evidence",
        "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf",
        r0_evidence_id="r0-expected",
    )
    identity = json.loads((prep / "r1_identity.json").read_text(encoding="utf-8"))
    with pytest.raises(DemoAdapterError, match="exact R0 evidence ID mismatch"):
        verify_r1_preflight_preparation(
            prep_path=prep,
            package_id=identity["package_id"],
            run_id=identity["run_id"],
            session_id=identity["session_id"],
            arm_token=identity["expected_arm_token"],
            r0_evidence_id="r0-wrong",
        )
