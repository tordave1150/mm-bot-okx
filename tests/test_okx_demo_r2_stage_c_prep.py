"""Deterministic offline tests for R2 Three-Session Economic Checkpoint Preparation.

Tests all 20 requirements specified in CODEX_EXECUTION_R2_THREE_SESSION_CHECKPOINT.md
under strict socket denial, zero network, and blank credentials.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r1_preflight_executor import AuditedReadOnlyExchangeWrapper
from okx_demo_r2_stage_c_prep import (
    CANONICAL_R0_CLOSURE_REF,
    CANONICAL_R1_RUN_ID,
    CANONICAL_R2_CANARY_RUN_ID,
    CANONICAL_R2_FINAL_PREP_REF,
    EXPECTED_CANDIDATE_FINGERPRINT,
    build_stage_c_preparation_package,
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


# Req-01: Prerequisite evidence chain verified
def test_requirement_01_prerequisite_evidence_intact() -> None:
    test_stamp = "test_req01_prereqs"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        assert prep_dir.is_dir()
        manifest = json.loads((prep_dir / "prerequisite_manifest.json").read_text(encoding="utf-8"))
        assert manifest["prerequisites_satisfied"] is True
        assert manifest["r0_closure"]["status"] == "R0_OFFLINE_QUALIFICATION_PASSED"
        assert manifest["r1_preflight"]["status"] == "R1_PREFLIGHT_PASSED"
        assert manifest["r2_final_preparation"]["status"] == "R2_FINAL_PREPARATION_PASSED"
        assert manifest["r2_operational_canary"]["status"] == "R2_CANARY_PASSED"
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-02: Candidate fingerprint matches expected
def test_requirement_02_candidate_fingerprint_matches() -> None:
    test_stamp = "test_req02_fp_match"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        identity = json.loads((prep_dir / "candidate_identity.json").read_text(encoding="utf-8"))
        assert identity["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT
        assert identity["behavioral_parameter_drift"] == 0
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-03: Candidate drift detection triggers on corrupted fingerprint
def test_requirement_03_candidate_fingerprint_drift_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "okx_demo_r2_stage_c_prep.compute_candidate_fingerprint",
        lambda root: {"candidate_fingerprint": "corrupted_candidate_fp_deadbeef"},
    )
    with pytest.raises(ValueError, match="Candidate fingerprint drift detected"):
        build_stage_c_preparation_package(root=ROOT, stamp="test_req03_corrupt")


# Req-04: Operational canary excluded from qualification denominator
def test_requirement_04_operational_canary_excluded_from_denominator() -> None:
    test_stamp = "test_req04_canary_exclusion"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        record = json.loads((prep_dir / "operational_canary_exclusion_record.json").read_text(encoding="utf-8"))
        assert record["denominator_policy"] == "EXCLUDED_FROM_12_SESSION_ECONOMIC_QUALIFICATION_DENOMINATOR"
        assert record["qualification_denominator_start_slot"] == "Q01"
        assert record["qualification_denominator_total_sessions"] == 12
        assert record["classification"] == "OPERATIONAL_TRANSPORT_AND_MUTATION_VALIDATION_ONLY"
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-05: Qualification schedule allocates exactly 12 fresh slots
def test_requirement_05_qualification_schedule_twelve_slots() -> None:
    test_stamp = "test_req05_twelve_slots"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        schedule = json.loads((prep_dir / "qualification_schedule_manifest.json").read_text(encoding="utf-8"))
        assert schedule["total_slots"] == 12
        assert len(schedule["schedule"]) == 12
        slots = [s["qualification_slot"] for s in schedule["schedule"]]
        assert slots == [f"Q{i:02d}" for i in range(1, 13)]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-06: Session nonces for Q01-Q12 are cryptographically unique with 0 collisions
def test_requirement_06_session_nonces_unique_zero_collisions() -> None:
    test_stamp = "test_req06_unique_nonces"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        schedule = json.loads((prep_dir / "qualification_schedule_manifest.json").read_text(encoding="utf-8"))
        nonces = [s["nonce"] for s in schedule["schedule"]]
        assert len(nonces) == 12
        assert len(set(nonces)) == 12
        assert schedule["collision_count"] == 0
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-07: Scope bounded strictly to Q01-Q03 and lifecycle preserved
def test_requirement_07_prepared_scope_bounded_to_q01_q03() -> None:
    test_stamp = "test_req07_scope_bounds"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        schedule = json.loads((prep_dir / "qualification_schedule_manifest.json").read_text(encoding="utf-8"))
        assert schedule["prepared_slots_count"] == 3
        assert schedule["prepared_slots"] == ["Q01", "Q02", "Q03"]
        contract = json.loads((prep_dir / "q01_q03_execution_contract.json").read_text(encoding="utf-8"))
        assert contract["target_slots"] == ["Q01", "Q02", "Q03"]
        assert contract["normal_quoting_execution"] == "POST_ONLY_MAKER_ONLY"
        assert contract["normal_path_taker_fills_allowed"] is False
        assert contract["single_flight_reduce_only_flatten_permitted"] is True
        assert contract["permitted_special_flatten_reasons"] == ["ROUTINE_TERMINAL_CLEANUP", "RISK_EMERGENCY_FLATTEN"]
        assert contract["absolute_inventory_cap_btc"] == 0.01
        assert contract["max_unresolved_single_flight_flatten"] == 1
        assert contract["campaign_special_flatten_ceiling"] == "2 / 12"
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-08: Sessions Q04-Q12 marked strictly unauthorized
def test_requirement_08_q04_q12_strictly_unauthorized() -> None:
    test_stamp = "test_req08_q04_unauth"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        schedule = json.loads((prep_dir / "qualification_schedule_manifest.json").read_text(encoding="utf-8"))
        assert schedule["unauthorized_slots_count"] == 9
        unauth = [s for s in schedule["schedule"] if s["status"] == "UNAUTHORIZED_STAGE_D_BLOCKED"]
        assert len(unauth) == 9
        assert [s["qualification_slot"] for s in unauth] == [f"Q{i:02d}" for i in range(4, 13)]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-09: Per-session admission contract mandates fresh preflight with 10 checks & clock skew <= 1500 ms
def test_requirement_09_per_session_admission_mandate() -> None:
    test_stamp = "test_req09_per_session_adm"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        contract = json.loads((prep_dir / "per_session_admission_contract.json").read_text(encoding="utf-8"))
        assert "APPLIES_PRIOR_TO_FIRST_ORDER_OF_EACH_SESSION" in contract["scope"]
        assert contract["clock_skew_limit_ms"] == 1500
        checks = contract["mandatory_checks_per_session"]
        assert len(checks) >= 10
        joined = " ".join(checks)
        assert "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf" in joined
        assert "sandboxMode" in joined or "simulated-trading" in joined
        assert "1500 ms" in joined
        assert "net_mode" in joined
        assert "isolated" in joined
        assert "3.0x" in joined
        assert "position_btc == 0.0" in joined
        assert "open_orders count == 0" in joined
        assert "cursor" in joined
        assert "kill-switch" in joined
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-10: Per-session admission gate prohibits automated account mutations
def test_requirement_10_admission_prohibits_account_mutations() -> None:
    test_stamp = "test_req10_no_auto_mutations"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        contract = json.loads((prep_dir / "per_session_admission_contract.json").read_text(encoding="utf-8"))
        assert contract["account_state_mutation_allowed"] is False
        assert "FAIL_CLOSED" in contract["remediation_policy"]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-11: Frozen risk boundary preserved with exact canonical limits
def test_requirement_11_frozen_risk_boundary_preserved() -> None:
    test_stamp = "test_req11_risk_boundary"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        risk = json.loads((prep_dir / "frozen_risk_specification.json").read_text(encoding="utf-8"))
        assert risk["modeled_capital_usdt"] == 750.0
        assert risk["normal_lot_size_btc"] == 0.01
        assert risk["maximum_inventory_btc"] == 0.01
        assert risk["absolute_inventory_cap_btc"] == 0.01
        assert risk["leverage"] == 3.0
        assert risk["position_mode"] == "net_mode"
        assert risk["margin_mode"] == "isolated"
        assert risk["session_soft_drawdown_usdt"] == "22.50"
        assert risk["session_hard_drawdown_usdt"] == "37.50"
        assert risk["aggregate_campaign_hard_loss_usdt"] == "75.00"
        assert "25.00" not in str(risk.values())
        assert risk["maximum_session_normal_creates"] == 60
        assert risk["maximum_stage_c_normal_creates"] == 180
        assert risk["maximum_campaign_normal_creates"] == 720
        assert risk["maximum_session_wall_ms"] == 1800000
        assert risk["maximum_campaign_wall_ms"] == 21600000
        assert risk["maximum_campaign_sessions"] == 12
        assert risk["clock_skew_budget_ms"] == 1500
        assert risk["book_age_budget_ms"] == 1000
        assert risk["observation_interval_ms"] == 2000
        assert risk["read_retries"] == 3
        assert risk["mutation_retries"] == 0
        assert risk["maximum_owned_bid"] == 1
        assert risk["maximum_owned_ask"] == 1
        assert risk["maximum_unresolved_flatten"] == 1
        assert risk["campaign_special_flatten_ceiling"] == "2 / 12"
        assert risk["terminal_flatten_lifecycle_preserved"] is True
        assert "INFORMATIONAL_ONLY" in risk["notional_cap_status"]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-12: No-tuning attestation verified
def test_requirement_12_no_tuning_attestation_verified() -> None:
    test_stamp = "test_req12_no_tuning"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        att = json.loads((prep_dir / "no_tuning_attestation.json").read_text(encoding="utf-8"))
        assert att["attestation"] == "NO_STRATEGY_TUNING_PERMITTED"
        assert att["behavioral_parameter_drift"] == 0
        assert att["minimum_half_spread_bps"] == 6.0
        assert att["gamma"] == 0.08
        assert att["max_order_age_ticks"] == 8
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-13: Three-Session Checkpoint contract enforces all 17 hard safety checks as early checkpoint
def test_requirement_13_checkpoint_enforces_17_hard_checks() -> None:
    test_stamp = "test_req13_checkpoint_checks"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        chk = json.loads((prep_dir / "three_session_checkpoint_contract.json").read_text(encoding="utf-8"))
        assert len(chk["hard_safety_checks"]) == 17
        assert chk["full_campaign_floors_enforced_at_stage_c"] is False
        assert len(chk["operational_and_economic_evaluation_diagnostics"]) >= 6
        assert "EARLY_REGRESSION_AND_SAFETY_CHECKPOINT" in chk["scope"]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-14: Checkpoint failure blocks Q04 continuation
def test_requirement_14_checkpoint_failure_blocks_continuation() -> None:
    test_stamp = "test_req14_failure_blocks"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        chk = json.loads((prep_dir / "three_session_checkpoint_contract.json").read_text(encoding="utf-8"))
        assert "HARD_STOP" in chk["post_checkpoint_action"]
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-15: Account mutation methods unreachable
def test_requirement_15_account_mutations_unreachable() -> None:
    exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(exchange)
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1"):
        wrapper.set_leverage()
    with pytest.raises(DemoAdapterError, match="MUTATION_ATTEMPT_PROHIBITED_IN_R1"):
        wrapper.set_position_mode()


# Req-16: Live endpoints unreachable and denied
def test_requirement_16_unknown_endpoints_fail_closed() -> None:
    exchange = PreflightMockExchange()
    wrapper = AuditedReadOnlyExchangeWrapper(exchange)
    with pytest.raises(DemoAdapterError, match="ENDPOINT_DENIED_UNKNOWN_CATEGORY"):
        _ = wrapper.transfer_assets


# Req-17: Zero secrets serialized in any artifact
def test_requirement_17_zero_secrets_serialized() -> None:
    test_stamp = "test_req17_secrets_clean"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        for item in prep_dir.rglob("*.json"):
            content = item.read_text(encoding="utf-8")
            for forbidden in ["apiKey", "secret", "password", "passphrase"]:
                assert f'"{forbidden}": "' not in content
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-18: Preparation mode performs zero order mutations
def test_requirement_18_zero_order_mutations_during_preparation() -> None:
    test_stamp = "test_req18_zero_mutations"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        terminal = json.loads(
            (prep_dir / "R2_THREE_SESSION_CHECKPOINT_PREPARATION_COMPLETED.json").read_text(encoding="utf-8")
        )
        assert terminal["orders_created"] == 0
        assert terminal["orders_amended"] == 0
        assert terminal["orders_cancelled"] == 0
        assert terminal["flatten_attempts"] == 0
        assert terminal["account_mutations"] == 0
        assert terminal["live_endpoint_attempts"] == 0
        assert terminal["sessions_executed"] == 0
        assert terminal["q01_q03_execution_authorized"] is False
        assert terminal["q04_q12_execution_authorized"] is False
        assert terminal["production_authorized"] is False
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)


# Req-19: Strict socket denial enforced during tests (tested via autouse fixture)
def test_requirement_19_socket_denial_enforced(enforce_offline_socket_guard: _OfflineSocketGuard) -> None:
    assert enforce_offline_socket_guard.attempts == []


# Req-20: Terminal completion marker valid with complete SHA-256 manifest
def test_requirement_20_terminal_completion_marker_valid() -> None:
    test_stamp = "test_req20_terminal_marker"
    target_dir = ROOT / "artifacts" / "r2_three_session_checkpoint_preparation" / f"r2-stage-c-prep-{test_stamp}"
    try:
        prep_dir = build_stage_c_preparation_package(root=ROOT, stamp=test_stamp)
        terminal = json.loads(
            (prep_dir / "R2_THREE_SESSION_CHECKPOINT_PREPARATION_COMPLETED.json").read_text(encoding="utf-8")
        )
        hashes = json.loads((prep_dir / "completion_hashes.json").read_text(encoding="utf-8"))
        assert terminal["status"] == "R2_THREE_SESSION_PREPARATION_PASSED"
        assert terminal["files_verified"] == len(hashes)
        assert terminal["r2_stage_c_preparation_complete"] is True
        assert terminal["operational_canary_excluded"] is True
    finally:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
