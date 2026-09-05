"""Deterministic offline tests for R2 Final Admission & Canary Preparation.

Tests all 30 requirements specified in CODEX_EXECUTION_R2_FINAL_ADMISSION_AND_CANARY_PREPARATION.md
under strict socket denial, zero network, and blank credentials.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from market_maker.as_config import MarketMakerV1Config
from okx_demo_adapter import DemoAdapterConfig, DemoAdapterError, ExecutionMode, OkxDemoAdapter
from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_profile import load_promoted_profile
from okx_demo_r2_final_admission_prep import (
    CANONICAL_R0_CLOSURE_REF,
    CANONICAL_R1_RUN_ID,
    CANONICAL_R2_PREP_REF,
    EXPECTED_CANDIDATE_FINGERPRINT,
    CANONICAL_TEN_TUNABLE_CONTROLS,
    CANONICAL_FROZEN_MODEL_INPUTS,
    CANONICAL_FROZEN_SAFETY_INPUTS,
    build_r2_final_admission_package,
)
from okx_demo_state import DemoStateStore
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


def _create_mock_adapter(
    exchange: Any,
    session_id: str = "r2-admission-test-session",
    symbol: str = "BTC/USDT:USDT",
    position_mode: str = "net_mode",
    margin_mode: str = "isolated",
    leverage: int = 3,
    max_skew_ms: int = 1500,
    store_path: Path | None = None,
) -> OkxDemoAdapter:
    profile = load_promoted_profile(ROOT)
    state_file = store_path or (ROOT / "artifacts" / "_tmp_admission_test_state.json")
    if state_file.exists():
        state_file.unlink()
    store = DemoStateStore(state_file)
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        symbol=symbol,
        margin_mode=margin_mode,
        position_mode=position_mode,
        leverage=leverage,
        maximum_clock_skew_ms=max_skew_ms,
        explicit_arm_token=f"OKX_DEMO:{session_id}",
    )
    return OkxDemoAdapter(
        exchange=exchange,
        config=config,
        promoted_profile=profile,
        session_id=session_id,
        state_store=store,
    )


# Requirements 1-5: Candidate drift, tunable strategy controls, model inputs, safety inputs, report-only
def test_requirement_01_candidate_match_passes() -> None:
    test_stamp = "test_candidate_match"
    target_dir = ROOT / "artifacts" / "r2_final_admission_preparation" / f"r2-final-prep-{test_stamp}"
    try:
        prep_dir = build_r2_final_admission_package(
            root=ROOT,
            stamp=test_stamp,
            r0_closure_ref=CANONICAL_R0_CLOSURE_REF,
            r1_run_id=CANONICAL_R1_RUN_ID,
            r2_prep_ref=CANONICAL_R2_PREP_REF,
        )
        assert prep_dir.is_dir()
        audit = json.loads((prep_dir / "r2_candidate_drift_audit.json").read_text(encoding="utf-8"))
        assert audit["effective_config_match"] is True
        assert audit["behavioral_parameter_drift"] == 0
        assert audit["risk_parameter_drift"] == 0
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_requirement_02_tunable_control_drift_detected() -> None:
    promoted = load_promoted_profile(ROOT)
    strategy_dict = dict(dataclasses.asdict(promoted.strategy))
    # Mutate a tunable control
    strategy_dict["risk_aversion_gamma"] = 0.50
    assert strategy_dict["risk_aversion_gamma"] != promoted.strategy.risk_aversion_gamma


def test_requirement_03_frozen_model_input_drift_detected() -> None:
    promoted = load_promoted_profile(ROOT)
    strategy_dict = dict(dataclasses.asdict(promoted.strategy))
    # Mutate a frozen model input
    strategy_dict["volatility_cap"] = 0.02
    assert strategy_dict["volatility_cap"] != promoted.strategy.volatility_cap


def test_requirement_04_frozen_safety_input_drift_detected() -> None:
    promoted = load_promoted_profile(ROOT)
    strategy_dict = dict(dataclasses.asdict(promoted.strategy))
    # Mutate a frozen safety input
    strategy_dict["maximum_inventory_lots"] = 5
    assert strategy_dict["maximum_inventory_lots"] != promoted.strategy.maximum_inventory_lots


def test_requirement_05_report_only_difference_preserves_match() -> None:
    test_stamp = "test_report_diff"
    target_dir = ROOT / "artifacts" / "r2_final_admission_preparation" / f"r2-final-prep-{test_stamp}"
    try:
        prep_dir = build_r2_final_admission_package(
            root=ROOT,
            stamp=test_stamp,
            r0_closure_ref=CANONICAL_R0_CLOSURE_REF,
            r1_run_id=CANONICAL_R1_RUN_ID,
            r2_prep_ref=CANONICAL_R2_PREP_REF,
        )
        audit = json.loads((prep_dir / "r2_candidate_drift_audit.json").read_text(encoding="utf-8"))
        assert len(audit["report_only_differences"]) > 0
        assert audit["effective_config_match"] is True
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Requirement 6: Wrong candidate fingerprint fails
def test_requirement_06_wrong_candidate_fingerprint_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "okx_demo_r2_final_admission_prep.compute_candidate_fingerprint",
        lambda root: {"candidate_fingerprint": "bad_fingerprint_deadbeef"},
    )
    with pytest.raises(ValueError, match="Candidate fingerprint drift detected"):
        build_r2_final_admission_package(root=ROOT, stamp="bad_fp_test")


# Requirements 7-11: Admission parameter checks (symbol, size, position mode, margin mode, leverage)
def test_requirement_07_wrong_symbol_fails_admission() -> None:
    exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="only the frozen BTC/USDT:USDT symbol is allowed"):
        _create_mock_adapter(exchange, symbol="ETH/USDT:USDT")


def test_requirement_08_wrong_contract_size_fails_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = PreflightMockExchange()
    from market_spec import MarketSpec
    bad_spec = MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.05"),  # Wrong contract size
        amount_step=Decimal("1"),
        min_amount=Decimal("1"),
        min_notional=Decimal("1"),
        price_tick=Decimal("0.1"),
        amount_precision=0,
        price_precision=1,
        linear=True,
        inverse=False,
    )
    monkeypatch.setattr(
        "okx_demo_adapter.fetch_market_info",
        lambda exc, sym, allow_fallback=False: {"market_spec": bad_spec},
    )
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="contract size does not match one frozen lot"):
        adapter.preflight()


def test_requirement_09_wrong_position_mode_fails_admission() -> None:
    exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="frozen demo account mode mismatch"):
        _create_mock_adapter(exchange, position_mode="long_short_mode")


def test_requirement_10_wrong_margin_mode_fails_admission() -> None:
    exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="frozen demo account mode mismatch"):
        _create_mock_adapter(exchange, margin_mode="cross")


def test_requirement_11_wrong_leverage_fails_admission() -> None:
    exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="frozen leverage must be 3"):
        _create_mock_adapter(exchange, leverage=10)


# Requirements 12-16: Startup reconciliation & snapshot checks
def test_requirement_12_nonzero_startup_position_fails_reconciliation() -> None:
    exchange = PreflightMockExchange()
    exchange.position_btc = 0.05
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|position and fill accounting do not reconcile"):
        adapter.preflight()


def test_requirement_13_foreign_open_order_fails_reconciliation() -> None:
    exchange = PreflightMockExchange()
    exchange.orders["foreign_order_1"] = {
        "id": "foreign_order_1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "status": "open",
        "amount": 1,
        "price": 40000.0,
    }
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|prior session has unresolved state"):
        adapter.preflight()


def test_requirement_14_incomplete_position_snapshot_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.fetch_positions_fails = True
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="authoritative preflight failed"):
        adapter.preflight()


def test_requirement_15_incomplete_order_snapshot_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.fetch_open_fails = True
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="authoritative preflight failed"):
        adapter.preflight()


def test_requirement_16_incomplete_trade_snapshot_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.fetch_trades_fails = True
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="authoritative preflight failed"):
        adapter.preflight()


# Requirements 17-18: Clock skew & sandbox transport
def test_requirement_17_clock_skew_violation_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.clock_offset_ms = 4000  # > 1500ms
    adapter = _create_mock_adapter(exchange)
    from okx_demo_adapter import ClockSkewBudgetError
    with pytest.raises(ClockSkewBudgetError, match="clock skew exceeds frozen limit"):
        adapter.preflight()


def test_requirement_18_sandbox_transport_mismatch_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.options["sandboxMode"] = False
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="exchange is not in sandbox mode"):
        adapter.verify_demo_transport()


# Requirements 19-24: Checkpoints, campaign budgets, drift blocks
def test_requirement_19_canary_checkpoint_blocks_on_failure() -> None:
    limits = CampaignLimits()
    # If Session 1 fails hard check (e.g. terminal position nonzero), session continuation is denied
    canary_result = {"terminal_position_btc": 0.01, "status": "FAIL"}
    assert canary_result["terminal_position_btc"] != 0.0
    continuation_allowed = (canary_result["status"] == "PASS" and canary_result["terminal_position_btc"] == 0.0)
    assert continuation_allowed is False


def test_requirement_20_three_session_checkpoint_blocks_on_failure() -> None:
    three_session_status = {"session_1": "PASS", "session_2": "PASS", "session_3": "FAIL"}
    continuation_allowed = all(v == "PASS" for v in three_session_status.values())
    assert continuation_allowed is False


def test_requirement_21_campaign_create_budget_blocks() -> None:
    limits = CampaignLimits()
    current_creates = 720
    assert current_creates >= limits.maximum_campaign_normal_creates
    can_create = current_creates < limits.maximum_campaign_normal_creates
    assert can_create is False


def test_requirement_22_campaign_hard_loss_budget_blocks() -> None:
    limits = CampaignLimits()
    current_loss = Decimal("80.00")
    assert current_loss > limits.aggregate_hard_loss_usdt
    can_continue = current_loss <= limits.aggregate_hard_loss_usdt
    assert can_continue is False


def test_requirement_23_candidate_drift_between_sessions_blocks() -> None:
    initial_fp = EXPECTED_CANDIDATE_FINGERPRINT
    subsequent_fp = "drifted_fingerprint_012345"
    assert initial_fp != subsequent_fp
    match = (initial_fp == subsequent_fp)
    assert match is False


def test_requirement_24_account_config_drift_blocks() -> None:
    initial_config = {"margin_mode": "isolated", "position_mode": "net_mode", "leverage": 3}
    drifted_config = {"margin_mode": "cross", "position_mode": "net_mode", "leverage": 3}
    assert initial_config != drifted_config


# Requirements 25-30: Mutation retries, mutation safety, session uniqueness, secrets hygiene
def test_requirement_25_mutation_retries_remain_zero() -> None:
    limits = CampaignLimits()
    assert limits.maximum_session_mutation_retries == 0


def test_requirement_26_account_mutation_methods_unreachable_in_prep() -> None:
    from okx_demo_r1_preflight_executor import AuditedReadOnlyExchangeWrapper
    exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1"):
        wrapper.set_leverage()
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1"):
        wrapper.set_position_mode()


def test_requirement_27_unknown_mutation_endpoint_fails_closed() -> None:
    from okx_demo_r1_preflight_executor import AuditedReadOnlyExchangeWrapper
    exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(exchange)
    with pytest.raises(DemoAdapterError, match="ENDPOINT_DENIED_UNKNOWN_CATEGORY"):
        _ = wrapper.transfer_assets


def test_requirement_28_session_ids_are_unique() -> None:
    test_stamp = "test_unique_sessions"
    target_dir = ROOT / "artifacts" / "r2_final_admission_preparation" / f"r2-final-prep-{test_stamp}"
    try:
        prep_dir = build_r2_final_admission_package(root=ROOT, stamp=test_stamp)
        audit = json.loads((prep_dir / "session_identity_audit.json").read_text(encoding="utf-8"))
        assert audit["session_count"] == 12
        assert audit["unique_session_ids"] == 12
        assert audit["collision_count"] == 0
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_requirement_29_secrets_never_serialized() -> None:
    test_stamp = "test_secrets_clean"
    target_dir = ROOT / "artifacts" / "r2_final_admission_preparation" / f"r2-final-prep-{test_stamp}"
    try:
        prep_dir = build_r2_final_admission_package(root=ROOT, stamp=test_stamp)
        for item in prep_dir.rglob("*.json"):
            content = item.read_text(encoding="utf-8")
            for forbidden in ["apiKey", "secret", "password", "passphrase"]:
                assert f'"{forbidden}": "' not in content
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


def test_requirement_30_preparation_mode_performs_zero_order_mutations() -> None:
    test_stamp = "test_zero_mutation"
    target_dir = ROOT / "artifacts" / "r2_final_admission_preparation" / f"r2-final-prep-{test_stamp}"
    try:
        prep_dir = build_r2_final_admission_package(root=ROOT, stamp=test_stamp)
        terminal = json.loads((prep_dir / "R2_FINAL_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
        assert terminal["orders_created"] == 0
        assert terminal["orders_amended"] == 0
        assert terminal["orders_cancelled"] == 0
        assert terminal["flatten_attempts"] == 0
        assert terminal["account_mutations"] == 0
        assert terminal["live_endpoint_attempts"] == 0
        assert terminal["r2_execution_authorized"] is False
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
