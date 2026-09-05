"""Tests for the staged validation pipeline and regression protection per Codex brief."""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    FlattenReason,
    QuoteMode,
    SampleEfficiencyQuotePolicy,
    SessionPhase,
    WorkOffStage,
)
from okx_demo_multi_session_campaign import (
    CampaignDecision,
    CampaignError,
    CampaignLimits,
    CampaignManifest,
    CampaignRegistry,
    EconomicHealth,
    EvidenceSufficiency,
    OverallOfflineState,
    SafetyDecision,
    SessionEvidence,
    seal_session_evidence,
)
from okx_demo_soak_executor import BoundedSoakExecutor, SoakPackage
from okx_demo_staged_validation import (
    OfflineHarnessGateway,
    _create_mock_session,
    compute_candidate_fingerprint,
    run_tier_0,
    run_tier_1,
    run_tier_2,
    run_tier_3,
    run_tier_4_qualification,
)


def _manifest() -> CampaignManifest:
    return CampaignManifest(
        campaign_id="test-staged-manifest",
        source_sha256="0" * 64,
        created_at_ms=1,
        formal_predecessor="formal-predecessor",
        soak_predecessor="soak-predecessor",
        formal_completed_sha256="0" * 64,
        soak_completed_sha256="0" * 64,
    )


def test_tier_0_unit_checks_pass() -> None:
    result = run_tier_0()
    assert result["status"] == "PASS"
    assert result["checks_passed"] >= 6


def test_tier_1_scenarios_pass() -> None:
    result = run_tier_1()
    assert result["status"] == "PASS"
    assert result["scenarios_passed"] == 10


def test_tier_2_canary_session_passes() -> None:
    result = run_tier_2()
    assert result["status"] == "PASS"
    assert result["safety_pass"] is True
    assert result["terminal_position_btc"] == "0"


def test_tier_3_economic_batch_passes() -> None:
    result = run_tier_3()
    assert result["status"] == "PASS"
    assert result["session_count"] == 3
    assert result["safety_decision"] == "SAFETY_PASS"
    assert result["overall_state"] == "ECONOMICALLY_PROMISING"


def test_candidate_fingerprint_is_deterministic_and_binds_sources() -> None:
    fp1 = compute_candidate_fingerprint()
    fp2 = compute_candidate_fingerprint()
    assert fp1["candidate_fingerprint"] == fp2["candidate_fingerprint"]
    assert len(str(fp1["candidate_fingerprint"])) == 64
    assert fp1["policy_fingerprint"] == fp2["policy_fingerprint"]
    assert fp1["limits_fingerprint"] == fp2["limits_fingerprint"]


def test_safety_failure_can_never_be_downgraded_to_warning_or_insufficient_evidence() -> None:
    registry = CampaignRegistry(root=Path("."), manifest=_manifest())
    # Session with safety violation
    unsafe_session = _create_mock_session(1, is_safe=False)
    staged = registry.evaluate_staged([unsafe_session])

    assert staged["safety_decision"] == SafetyDecision.SAFETY_FAIL.value
    # Crucial regression test: MUST be strictly UNSAFE, never ECONOMICALLY_PROMISING, SAFE_OFFLINE, or MORE_EVIDENCE_REQUIRED
    assert staged["overall_state"] == OverallOfflineState.UNSAFE.value
    assert "UNSAFE_SESSION" in staged["safety_reasons"]


def test_workoff_mode_never_increases_absolute_inventory() -> None:
    controller = EconomicSessionController("test-workoff", "0" * 64)
    # Open buy fill -> inventory = +0.01
    controller.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=10,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    # In work-off mode, tick classification only allows ONE_SIDED_SELL_DEFENSE
    mode = controller.classify_tick(timestamp_ms=11, inventory_btc=Decimal("0.01"))
    assert mode is QuoteMode.ONE_SIDED_SELL_DEFENSE
    assert controller.current_workoff_stage in (WorkOffStage.PASSIVE_WORK_OFF, WorkOffStage.AGGRESSIVE_MAKER_WORK_OFF)


def test_aggressive_workoff_remains_maker_post_only() -> None:
    controller = EconomicSessionController("test-aggressive", "0" * 64)
    controller.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=10,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    controller.observe_inventory_defense(trade_id="b1", timestamp_ms=11)
    controller.enter_draining(timestamp_ms=20, normal_creates=48)
    controller.classify_tick(timestamp_ms=21, inventory_btc=Decimal("0.01"))
    assert controller.current_workoff_stage is WorkOffStage.AGGRESSIVE_MAKER_WORK_OFF

    # WorkOffStage must remain strictly in maker stages before emergency flatten
    assert controller.current_workoff_stage is not WorkOffStage.EMERGENCY_FLATTEN


def test_cleanup_reason_classification_is_preserved_and_observable() -> None:
    controller = EconomicSessionController("test-reasons", "0" * 64)
    controller.observe_fill(
        trade_id="b1", side="buy", timestamp_ms=10,
        inventory_before_btc=Decimal("0"), inventory_after_btc=Decimal("0.01"),
        fill_order_id="o1", fill_quantity_btc=Decimal("0.01"), fill_price_usdt=Decimal("65000"),
    )
    controller.observe_inventory_defense(trade_id="b1", timestamp_ms=11)
    controller.authorize_taker_flatten(reason="ROUTINE_TERMINAL_CLEANUP", timestamp_ms=100)
    controller.close_with_special_flatten(
        timestamp_ms=101, inventory_before_btc=Decimal("0.01"), flatten_quantity_btc=Decimal("0.01")
    )
    assert controller.flatten_reason == FlattenReason.ROUTINE_TERMINAL_CLEANUP.value
    ev = controller.evidence(require_complete=False)
    assert ev["flatten_reason"] == FlattenReason.ROUTINE_TERMINAL_CLEANUP.value
    assert ev["terminal_inventory_before_cleanup_btc"] == "0.01"


def test_tier_4_qualification_with_frozen_candidate() -> None:
    fp = compute_candidate_fingerprint()
    result = run_tier_4_qualification(str(fp["candidate_fingerprint"]))
    assert result["status"] == "PASS"
    assert result["session_count"] == 12
    assert result["safety_decision"] == "SAFETY_PASS"
    assert result["economic_health"] == "HEALTHY"
    assert result["evidence_sufficiency"] == "SUFFICIENT_EVIDENCE"
    assert result["overall_state"] == "OFFLINE_QUALIFICATION_PASSED"
    assert result["informational_status"] == "R1_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"
    assert result["candidate_fingerprint"] == str(fp["candidate_fingerprint"])
