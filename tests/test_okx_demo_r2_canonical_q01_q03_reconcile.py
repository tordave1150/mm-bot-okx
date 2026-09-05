"""Deterministic offline tests for R2 Canonical (Q01-Q03) Evidence & Accounting Reconciliation.

Tests:
1. Immutable verification of original run evidence (r2-canonical-qualification-run-20260904T154500Z).
2. Tamper detection and fail-closed integrity enforcement.
3. Authoritative Q02 fill ledger validation for both legs (maker entry + special flatten).
4. Recomputation of FIFO maker round trips to strictly 0 (taker flatten excluded).
5. Exact fee reconciliation (zero variance against actual OKX fees and modeled rates).
6. Normal vs special economic attribution separation.
7. Reporting semantics fix (session_net_pnl vs campaign_cumulative_net_pnl).
8. Candidate fingerprint zero-drift invariant.
9. Full end-to-end reconciliation execution under strict socket denial.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path
import pytest

from okx_demo_r2_canonical_q01_q03_reconcile import (
    EXPECTED_CANDIDATE_FINGERPRINT,
    ORIGINAL_COMPLETION_HASHES,
    ORIGINAL_RUN_ID,
    RAW_Q02_TRADES,
    build_corrected_stage_c_diagnostics,
    build_economic_attribution_recomputed,
    build_fee_reconciliation_audit,
    build_q02_fill_ledger,
    build_reporting_semantics_audit,
    run_canonical_q01_q03_reconciliation,
    verify_original_run_integrity,
)
from okx_demo_staged_validation import compute_candidate_fingerprint
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Ensure socket denial is strictly active for every test in this module."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def test_candidate_fingerprint_integrity() -> None:
    fp = compute_candidate_fingerprint(ROOT)["candidate_fingerprint"]
    assert fp == EXPECTED_CANDIDATE_FINGERPRINT


def test_original_run_immutable_verification_passes() -> None:
    original_run_dir = ROOT / "artifacts" / "r2_canonical_qualification_runs" / ORIGINAL_RUN_ID
    assert original_run_dir.exists()
    record = verify_original_run_integrity(original_run_dir)
    assert record["files_verified_count"] == len(ORIGINAL_COMPLETION_HASHES)
    assert record["preservation_status"] == "ORIGINAL_RUN_IMMUTABLY_PRESERVED"


def test_original_run_tamper_fails_closed(tmp_path: Path) -> None:
    # Create a corrupted mock copy of the original run directory
    mock_run = tmp_path / "mock_corrupted_run"
    shutil.copytree(ROOT / "artifacts" / "r2_canonical_qualification_runs" / ORIGINAL_RUN_ID, mock_run)

    # Tamper with q01_session_audit.json
    tampered_file = mock_run / "q01_session_audit.json"
    tampered_file.write_text("{\"tampered\": true}", encoding="utf-8")

    with pytest.raises(ValueError, match="Integrity violation"):
        verify_original_run_integrity(mock_run)


def test_q02_fill_ledger_schema_and_contents() -> None:
    ledger = build_q02_fill_ledger()
    assert ledger["authoritative_fill_count"] == 2
    assert ledger["slot"] == "Q02"

    fills = ledger["fills"]
    leg1, leg2 = fills[0], fills[1]

    # Leg 1: Maker Entry
    assert leg1["trade_id"] == "4394970947"
    assert leg1["exchange_order_id"] == "3893620855457976320"
    assert leg1["client_order_id"] == "bt8db015b7df380196b21e1f52b8"
    assert leg1["side"] == "buy"
    assert leg1["quantity_btc"] == "0.01"
    assert leg1["price_usdt"] == "79702.1"
    assert leg1["maker_taker"] == "maker"
    assert leg1["post_only"] is True
    assert leg1["reduce_only"] is False
    assert leg1["fee_amount"] == "0.1594042"
    assert leg1["inventory_before_btc"] == 0.0
    assert leg1["inventory_after_btc"] == 0.01
    assert leg1["leg_classification"] == "NORMAL_MAKER_ENTRY"

    # Leg 2: Special Terminal Reduce-Only Flatten
    assert leg2["trade_id"] == "4394972150"
    assert leg2["exchange_order_id"] == "3893621498931318784"
    assert leg2["client_order_id"] == "btd6fbd5179332d7a9bb79b31eed"
    assert leg2["side"] == "sell"
    assert leg2["quantity_btc"] == "0.01"
    assert leg2["price_usdt"] == "79627.8"
    assert leg2["maker_taker"] == "taker"
    assert leg2["post_only"] is False
    assert leg2["reduce_only"] is True
    assert leg2["fee_amount"] == "0.398139"
    assert leg2["inventory_before_btc"] == 0.01
    assert leg2["inventory_after_btc"] == 0.0
    assert leg2["leg_classification"] == "SPECIAL_TERMINAL_REDUCE_ONLY_FLATTEN"


def test_fifo_maker_round_trip_reconciliation_zero() -> None:
    econ = build_economic_attribution_recomputed()
    fifo_rec = econ["fifo_round_trip_reconciliation"]
    assert fifo_rec["fifo_maker_round_trips_authoritative"] == 0
    assert fifo_rec["fifo_maker_round_trips_previously_reported"] == 1
    assert fifo_rec["owned_maker_bid_fills"] == 1
    assert fifo_rec["owned_maker_ask_fills"] == 0
    assert fifo_rec["routine_terminal_cleanup"] == 1
    assert fifo_rec["total_maker_fills"] == 1


def test_fee_reconciliation_exact_zero_variance() -> None:
    audit = build_fee_reconciliation_audit()
    assert audit["fee_reconciliation_passed"] is True

    leg1 = audit["legs"]["leg_1_maker_entry"]
    assert leg1["exchange_actual_fee"] == "0.1594042"
    assert leg1["modeled_fee"] == "0.1594042"
    assert leg1["fee_variance"] == "0.0"

    leg2 = audit["legs"]["leg_2_special_flatten"]
    assert Decimal(leg2["exchange_actual_fee"]) == Decimal("0.3981390")
    assert Decimal(leg2["modeled_fee"]) == Decimal("0.3981390")
    assert leg2["fee_variance"] == "0.0"

    totals = audit["partitioned_totals"]
    assert totals["normal_maker_fees_usdt"] == "0.1594042"
    assert totals["special_taker_fees_usdt"] == "0.398139"
    assert totals["total_fees_usdt"] == "0.5575432"
    assert totals["total_variance_usdt"] == "0.0"


def test_economic_attribution_separation() -> None:
    econ = build_economic_attribution_recomputed()
    normal = econ["normal_maker_economics"]
    special = econ["special_flatten_economics"]
    agg = econ["aggregate_economics"]

    assert normal["normal_gross_pnl_usdt"] == "0.0000"
    assert normal["normal_maker_fees_usdt"] == "0.1594042"
    assert normal["normal_net_pnl_usdt"] == "-0.1594042"

    assert special["special_flatten_gross_impact_usdt"] == "-0.7430"
    assert special["special_taker_fees_usdt"] == "0.398139"
    assert special["special_net_pnl_usdt"] == "-1.141139"

    assert agg["aggregate_gross_pnl_usdt"] == "-0.7430"
    assert agg["aggregate_fees_usdt"] == "0.5575432"
    assert agg["aggregate_net_pnl_usdt"] == "-1.3005432"


def test_reporting_semantics_audit() -> None:
    semantics = build_reporting_semantics_audit()
    assert semantics["semantics_enforced"] is True
    sessions = semantics["sessions"]

    assert sessions["Q01"]["session_net_pnl_usdt"] == "0.0000000"
    assert sessions["Q01"]["campaign_cumulative_net_pnl_usdt"] == "0.0000000"

    assert sessions["Q02"]["session_net_pnl_usdt"] == "-1.3005432"
    assert sessions["Q02"]["campaign_cumulative_net_pnl_usdt"] == "-1.3005432"

    assert sessions["Q03"]["session_net_pnl_usdt"] == "0.0000000"
    assert sessions["Q03"]["campaign_cumulative_net_pnl_usdt"] == "-1.3005432"


def test_corrected_stage_c_diagnostics_structure() -> None:
    diag = build_corrected_stage_c_diagnostics()
    assert set(diag.keys()) == {"Q01", "Q02", "Q03", "AGGREGATE_Q01_Q03"}

    agg = diag["AGGREGATE_Q01_Q03"]
    assert agg["maker_bid_fills"] == 1
    assert agg["maker_ask_fills"] == 0
    assert agg["total_maker_fills"] == 1
    assert agg["fifo_maker_round_trips"] == 0
    assert agg["routine_terminal_cleanup"] == 1
    assert agg["emergency_flatten"] == 0
    assert agg["maker_fees_usdt"] == "0.1594042"
    assert agg["special_fees_usdt"] == "0.3981390"
    assert agg["total_fees_usdt"] == "0.5575432"
    assert agg["normal_net_pnl_usdt"] == "-0.1594042"
    assert agg["special_net_pnl_usdt"] == "-1.1411390"
    assert agg["aggregate_net_pnl_usdt"] == "-1.3005432"
    assert agg["terminal_position_btc"] == 0.0
    assert agg["terminal_owned_orders"] == 0


def test_full_canonical_reconciliation_execution_under_socket_guard(tmp_path: Path) -> None:
    test_output_dir = tmp_path / "test-reconciliation-run"
    original_run_dir = ROOT / "artifacts" / "r2_canonical_qualification_runs" / ORIGINAL_RUN_ID

    out_dir = run_canonical_q01_q03_reconciliation(
        root=ROOT,
        original_run_dir=original_run_dir,
        output_dir=test_output_dir,
    )
    assert out_dir.exists()

    expected_files = [
        "immutable_run_verification.json",
        "q02_authoritative_fill_ledger.json",
        "fee_reconciliation_audit.json",
        "economic_attribution_recomputed.json",
        "reporting_semantics_audit.json",
        "corrected_stage_c_diagnostics.json",
        "stage_c_reconciliation_manifest.json",
        "completion_hashes.json",
        "R2_Q01_Q03_ACCOUNTING_RECONCILED_PASS.json",
    ]
    for fname in expected_files:
        p = out_dir / fname
        assert p.exists(), f"Missing file: {fname}"

    marker = json.loads((out_dir / "R2_Q01_Q03_ACCOUNTING_RECONCILED_PASS.json").read_text(encoding="utf-8"))
    assert marker["status"] == "R2_Q01_Q03_ACCOUNTING_RECONCILED_PASS"
    assert marker["decision"] == "R2_Q01_Q03_ACCOUNTING_RECONCILED_PASS"
    assert marker["economic_sessions_credited"] == 3
    assert marker["recomputed_fifo_maker_round_trips"] == 0
    assert marker["recomputed_routine_terminal_cleanup"] == 1
    assert marker["informational_next_status"] == "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION"
    assert marker["post_reconciliation_action"] == "HARD_STOP_ENFORCED"
    assert marker["q04_started"] is False
    assert marker["production_authorized"] is False
    assert marker["git_write_operation"] is False
