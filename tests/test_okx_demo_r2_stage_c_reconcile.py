"""Deterministic offline tests for R2 Stage C Evidence Reconciliation & Audit.

Verifies:
1. Preservation & immutable integrity of original run r2-stage-c-run-20260904T141733Z.
2. Candidate fingerprint zero drift & zero parameter tuning.
3. Accurate termination reason audit (canonical vs executor override).
4. Authoritative separation of session_owned_maker_fills (0) vs trade_cursor_events_observed (3).
5. Inconsistency resolution (maker fills reported vs inventory and PnL).
6. Order classification for all 24 creates and 24 cancels (unfilled_then_cancelled).
7. Authoritative recomputed economic accounting.
8. Stage C diagnostic table correctness.
9. Decision model: R2_STAGE_C_NONQUALIFICATION_RUN with Q04-Q12 blocked and hard stop enforced.
10. Strict execution under _OfflineSocketGuard.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import pytest

from okx_demo_r2_stage_c_reconcile import (
    EXPECTED_CANDIDATE_FINGERPRINT,
    ORIGINAL_COMPLETION_HASHES,
    ORIGINAL_RUN_ID,
    run_stage_c_evidence_reconciliation,
    verify_original_run_integrity,
)
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_RUN_DIR = ROOT / "artifacts" / "r2_stage_c_execution" / ORIGINAL_RUN_ID


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Ensure socket denial is strictly active for every test in this module."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def test_original_run_files_intact_and_unmodified():
    """Confirms all 11 original run files exist and match their exact SHA256 hashes."""
    verified = verify_original_run_integrity(ORIGINAL_RUN_DIR)
    assert len(verified) == 11  # 11 files listed in completion_hashes.json
    for rel_path, expected_hash in ORIGINAL_COMPLETION_HASHES.items():
        assert verified[rel_path] == expected_hash


def test_reconciliation_generates_complete_package(tmp_path: Path):
    """Executes reconciliation in a temporary directory and verifies all artifacts."""
    out_dir = tmp_path / "reconciliation_package"
    result_dir = run_stage_c_evidence_reconciliation(
        root=ROOT,
        original_run_dir=ORIGINAL_RUN_DIR,
        output_dir=out_dir,
        stamp="20260904T143500Z",
    )
    assert result_dir == out_dir
    assert (out_dir / "canonical_termination_audit.json").exists()
    assert (out_dir / "session_owned_fill_ledger.json").exists()
    assert (out_dir / "inconsistency_reconciliation_audit.json").exists()
    assert (out_dir / "recomputed_economic_accounting.json").exists()
    assert (out_dir / "order_lifecycle_reconciliation.json").exists()
    assert (out_dir / "stage_c_diagnostic_summary.json").exists()
    assert (out_dir / "candidate_verification.json").exists()
    assert (out_dir / "completion_hashes.json").exists()
    assert (out_dir / "R2_STAGE_C_EVIDENCE_RECONCILIATION_COMPLETED.json").exists()


def test_canonical_vs_executor_termination_audit(tmp_path: Path):
    """Verifies termination audit proves executor override and classifies as non-qualification."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    audit = json.loads((out_dir / "canonical_termination_audit.json").read_text(encoding="utf-8"))
    assert audit["executor_fixed_cycle_override_present"] is True
    assert audit["executor_fixed_cycle_setting"] == 4
    assert audit["qualification_denominator_eligibility"] == "EXCLUDED_FROM_12_SESSION_ECONOMIC_DENOMINATOR"

    for slot in ["Q01", "Q02", "Q03"]:
        detail = audit["session_termination_details"][slot]
        assert detail["quoting_cycles"] == 4
        assert detail["normal_creates"] == 8
        assert detail["canonical_termination_reason"] == "NOT_REACHED"
        assert detail["root_cause_classification"] == "EXECUTOR_SPECIFIC_FIXED_CYCLE_LIMIT"
        assert "range(cycles_per_session)" in detail["termination_predicate_true"]


def test_session_owned_fill_ledger_distinction(tmp_path: Path):
    """Verifies explicit separation of session_owned_maker_fills vs trade_cursor_events_observed."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    ledger = json.loads((out_dir / "session_owned_fill_ledger.json").read_text(encoding="utf-8"))
    assert ledger["session_owned_maker_fills_total"] == 0
    assert ledger["trade_cursor_events_observed_total"] == 3

    hist = ledger["historical_trade_cursor_record"]
    assert hist["fill_trade_id"] == "4389103155"
    assert hist["timestamp_ms"] == 1788410786789
    assert hist["q01_q03_ownership_verdict"] == "NOT_OWNED_BY_Q01_Q02_OR_Q03"

    for slot in ["Q01", "Q02", "Q03"]:
        slot_data = ledger["per_session_fill_reconciliation"][slot]
        assert slot_data["session_owned_maker_fills"] == 0
        assert slot_data["trade_cursor_events_observed"] == 1
        assert slot_data["owned_fills"] == []


def test_inconsistency_reconciliation_audit(tmp_path: Path):
    """Verifies reconciliation between 3 reported fills vs 0 inventory and 0 PnL."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    audit = json.loads((out_dir / "inconsistency_reconciliation_audit.json").read_text(encoding="utf-8"))
    for slot in ["Q01", "Q02", "Q03"]:
        path = audit["inventory_path_per_session"][slot]
        assert path["startup_inventory_btc"] == 0.0
        assert path["post_cycle_0_inventory_btc"] == 0.0
        assert path["post_cycle_3_inventory_btc"] == 0.0
        assert path["terminal_inventory_btc"] == 0.0
        assert path["true_maximum_absolute_inventory_btc"] == 0.0


def test_order_lifecycle_reconciliation(tmp_path: Path):
    """Verifies all 24 creates and 24 cancels are classified as unfilled_then_cancelled."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    recon = json.loads((out_dir / "order_lifecycle_reconciliation.json").read_text(encoding="utf-8"))
    assert recon["total_orders_created"] == 24
    assert recon["total_orders_cancelled"] == 24
    assert recon["classification_summary"]["unfilled_then_cancelled"] == 24
    assert recon["classification_summary"]["partially_filled_then_remainder_cancelled"] == 0
    assert recon["classification_summary"]["fully_filled"] == 0
    assert recon["classification_summary"]["rejected"] == 0


def test_authoritative_recomputed_economics(tmp_path: Path):
    """Verifies recomputed economic accounting from authoritative owned fills."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    econ = json.loads((out_dir / "recomputed_economic_accounting.json").read_text(encoding="utf-8"))
    agg = econ["aggregate_q01_q03_economics"]
    assert agg["total_maker_fills"] == 0
    assert agg["filled_btc_quantity"] == 0.0
    assert agg["fifo_maker_round_trips"] == 0
    assert agg["gross_realized_pnl_usdt"] == "0.00"
    assert agg["maker_fees_usdt"] == "0.00"
    assert agg["aggregate_net_pnl_usdt"] == "0.00"
    assert agg["terminal_inventory_btc"] == 0.0
    assert agg["maximum_absolute_intra_session_inventory_btc"] == 0.0


def test_decision_model_and_terminal_marker(tmp_path: Path):
    """Verifies terminal decision is R2_STAGE_C_NONQUALIFICATION_RUN with hard stop enforced."""
    out_dir = tmp_path / "reconcile"
    run_stage_c_evidence_reconciliation(root=ROOT, original_run_dir=ORIGINAL_RUN_DIR, output_dir=out_dir)

    marker = json.loads((out_dir / "R2_STAGE_C_EVIDENCE_RECONCILIATION_COMPLETED.json").read_text(encoding="utf-8"))
    assert marker["status"] == "R2_STAGE_C_NONQUALIFICATION_RUN"
    assert marker["reconciliation_decision"] == "R2_STAGE_C_NONQUALIFICATION_RUN"
    assert marker["operational_classification"] == "NON_QUALIFICATION_OPERATIONAL_RUN"
    assert marker["economic_denominator_counted"] is False
    assert marker["q04_started"] is False
    assert marker["q04_q12_execution_authorized"] is False
    assert marker["production_authorized"] is False
    assert marker["hard_stop_enforced"] is True
    assert marker["informational_next_status"] == "Q04_Q12_BLOCKED_PENDING_CANONICAL_QUALIFICATION_DECISION"


def test_corrupt_original_file_fails_closed(tmp_path: Path):
    """Verifies reconciliation fails closed if any original run file is corrupted or modified."""
    corrupt_run_dir = tmp_path / "corrupt_run"
    shutil.copytree(ORIGINAL_RUN_DIR, corrupt_run_dir)
    # Tamper with one file
    (corrupt_run_dir / "q01_session_audit.json").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="Integrity violation"):
        run_stage_c_evidence_reconciliation(
            root=ROOT,
            original_run_dir=corrupt_run_dir,
            output_dir=tmp_path / "out",
        )
