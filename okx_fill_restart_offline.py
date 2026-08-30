"""Build non-overwriting offline evidence for fill/restart validation.

The command in this module never constructs an exchange, reads credentials,
performs OKX preflight, or submits an order.  It verifies immutable predecessor
hashes, runs tests under a socket-deny pytest plugin, writes a durable reference
R1/R2 fixture, and closes only the offline implementation phase.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from okx_demo_profile import load_promoted_profile
from okx_fill_restart_validation import (
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    RiskBudget,
    RuntimeBinding,
    canonical_sha256,
    make_snapshot,
)


V16_SPEC_SHA256 = (
    "e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f"
)
PRIOR_CANONICAL_SPEC_SHA256 = (
    "10063903656d0bde6ea6259134bbe1e19409a1babb10c682ebd644080123508e"
)
PRIOR_PROTOCOL_FILE_SHA256 = (
    "cf0985d36983fbf5776f9becb75103c305380a39cce9adcebfaf6a2d9ff28534"
)
PRIOR_COMPLETION_MANIFEST_SHA256 = (
    "ef673741c4bacb9aab7bb49175d435e070975040e628a3146831f893869a14e7"
)
PRIOR_DECISION_SHA256 = (
    "ca7658a21b766300f2bda98d54d45bbae004ba33a7d86fa187617ca83ebbe42b"
)
PRIOR_COMPLETED_SHA256 = (
    "288889525e85c3579f5bbec50581b4f124259e73c813fab494b0f6b9d368afa1"
)
PREDECESSOR_PROTOCOL_SHA256 = (
    "00a562b815353b213386bb9750c6093914befa80eb850c15e05ddd4e81f8b4e5"
)
PRIOR_RUN_ID = "okx-demo-20260802T172200Z"
ARTIFACT_ROOT = Path("artifacts") / "okx_demo_fill_restart_validation"


SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_FILL_RESTART_VALIDATION.md",
    "AGENTS_OKX_DEMO_EXECUTION_SAFETY.md",
    "okx_fill_restart_validation.py",
    "okx_fill_restart_offline.py",
    "okx_offline_network_guard.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_fill_restart_offline.py",
    "tests/test_okx_production_readiness.py",
    "backtest/tests/test_mm_v1_6_economics.py",
    "okx_demo_adapter.py",
    "okx_demo_profile.py",
    "okx_demo_protocol.py",
    "okx_demo_runtime.py",
    "okx_demo_state.py",
    "okx_execution_safety.py",
    "okx_production_readiness.py",
    "market_spec.py",
    "utils.py",
    "market_maker/arrival_intensity.py",
    "market_maker/as_config.py",
    "market_maker/as_strategy.py",
    "market_maker/inventory.py",
    "market_maker/quote_model.py",
    "market_maker/volatility.py",
)


PREDECESSOR_FIXTURES = {
    "create_accepted_response_lost": "tests/test_okx_demo_adapter.py::test_create_accepted_response_lost_resolves_once_without_retry",
    "create_rejected_ambiguous": "tests/test_okx_demo_adapter.py::test_create_rejected_is_ambiguous_and_never_retried",
    "cancel_timed_out_order_open": "tests/test_okx_demo_adapter.py::test_cancel_timeout_and_order_still_open_halts_without_replacement",
    "cancel_accepted_response_lost": "tests/test_okx_demo_adapter.py::test_cancel_timeout_but_accepted_is_confirmed_by_snapshot",
    "cancel_confirmation_unavailable": "tests/test_okx_demo_adapter.py::test_cancel_confirmation_outage_halts_new_orders",
    "foreign_order": "tests/test_okx_demo_adapter.py::test_fresh_session_rejects_foreign_order",
    "duplicate_same_side": "tests/test_okx_demo_adapter.py::test_duplicate_same_side_intent_is_rejected_before_create",
    "missing_order": "tests/test_okx_demo_adapter.py::test_missing_expected_order_without_trade_fails_closed",
    "partial_fill": "tests/test_okx_demo_adapter.py::test_partial_fill_reconciles_live_remainder_and_persistent_accounting",
    "late_fill": "tests/test_okx_demo_adapter.py::test_late_fill_reconciles_disappeared_order_and_position",
    "restart_before_fill": "tests/test_okx_demo_adapter.py::test_restart_before_fill_restores_owned_order_generation",
    "restart_after_fill": "tests/test_okx_demo_adapter.py::test_restart_after_fill_restores_fill_cursor_position_and_fees",
    "wrong_state_identity": "tests/test_okx_demo_state.py::test_wrong_state_identity_fails_closed",
    "corrupt_state": "tests/test_okx_demo_state.py::test_corrupt_state_fails_closed_without_overwrite",
    "stale_state": "tests/test_okx_demo_state.py::test_stale_state_fails_closed_without_overwrite",
    "future_state": "tests/test_okx_demo_state.py::test_future_dated_state_fails_closed",
    "empty_position": "tests/test_okx_demo_adapter.py::test_empty_position_snapshot_explicitly_reconciles_zero",
    "long_short_restore": "tests/test_okx_demo_adapter.py::test_long_and_short_position_restore_reconcile_exactly",
    "one_step_boundary": "tests/test_okx_execution_safety.py::test_position_reconciliation_one_base_step_boundary_is_closed",
    "websocket_disconnect_rest_failure": "tests/test_okx_demo_adapter.py::test_websocket_disconnect_plus_rest_failure_halts_without_create",
    "websocket_duplicate_reorder": "tests/test_okx_demo_adapter.py::test_websocket_duplicate_and_reordered_snapshots_are_not_made_fresh",
    "stale_crossed_empty_delayed_book": "tests/test_okx_demo_adapter.py::test_unsafe_market_data_proves_zero_new_submissions",
    "authoritative_snapshot_outages": "tests/test_okx_demo_adapter.py::test_authoritative_snapshot_outage_proves_zero_new_submissions",
    "clock_skew": "tests/test_okx_demo_adapter.py::test_clock_skew_preflight_proves_zero_new_submissions",
    "flatten_response_lost": "tests/test_okx_demo_adapter.py::test_flatten_response_lost_resolves_by_client_identity_without_retry",
    "partial_flatten": "tests/test_okx_demo_adapter.py::test_partial_flatten_reconciles_without_resubmission",
    "delayed_flatten_position": "tests/test_okx_demo_adapter.py::test_delayed_flatten_position_update_does_not_resubmit",
    "persistence_failure": "tests/test_okx_demo_adapter.py::test_persistence_failure_after_create_cancels_and_blocks_restart",
    "kill_switch_restart": "tests/test_okx_demo_adapter.py::test_kill_switch_survives_restart_and_blocks_new_orders",
    "ambiguous_cancel_restart": "tests/test_okx_demo_adapter.py::test_ambiguous_cancel_latch_survives_supervisor_restart",
    "special_fill_exclusion": "tests/test_okx_demo_adapter.py::test_special_reduce_only_fill_is_excluded_from_normal_activity",
    "unknown_fill": "tests/test_okx_demo_adapter.py::test_unknown_new_trade_fails_closed",
    "offline_lifecycle": "tests/test_okx_demo_adapter.py::test_offline_demo_matrix_completes_full_order_lifecycle",
    "formal_marker_crash": "tests/test_okx_demo_protocol.py::test_execution_armed_marker_blocks_rerun_after_midstream_crash",
    "formal_report_recovery": "tests/test_okx_demo_protocol.py::test_formal_raw_completion_disables_any_second_formal_execution",
}


FILL_RESTART_FIXTURES = {
    "live_unavailable_and_risk_caps": "tests/test_okx_fill_restart_validation.py::test_live_mode_and_wider_risk_budget_are_structurally_rejected",
    "binding_drift": "tests/test_okx_fill_restart_validation.py::test_binding_rejects_profile_spec_symbol_and_source_drift",
    "paginated_same_timestamp_reordered": "tests/test_okx_fill_restart_validation.py::test_paginated_same_timestamp_reordered_duplicate_and_late_fills",
    "pagination_chain_and_conflict": "tests/test_okx_fill_restart_validation.py::test_fill_cursor_rejects_broken_page_chain_and_conflicting_duplicate",
    "r1_long_short": "tests/test_okx_fill_restart_validation.py::test_r1_restart_restores_real_fill_position_fees_and_generation",
    "r1_r2_round_trip": "tests/test_okx_fill_restart_validation.py::test_full_r1_r2_kill_restart_round_trip_and_safe_release",
    "partial_late_after_cancel": "tests/test_okx_fill_restart_validation.py::test_partial_then_late_fill_after_confirmed_cancel_reconciles",
    "trade_before_position": "tests/test_okx_fill_restart_validation.py::test_trade_before_position_lag_blocks_then_reconciles",
    "position_before_trade": "tests/test_okx_fill_restart_validation.py::test_position_before_trade_blocks_then_reconciles",
    "maker_taker_fee_order_mismatch": "tests/test_okx_fill_restart_validation.py::test_maker_fee_and_order_mismatch_fail_closed_with_zero_submissions",
    "incomplete_snapshots": "tests/test_okx_fill_restart_validation.py::test_every_incomplete_snapshot_halts_with_zero_submissions",
    "foreign_duplicate_missing": "tests/test_okx_fill_restart_validation.py::test_foreign_duplicate_and_missing_orders_fail_closed",
    "restart_metadata_pair_token": "tests/test_okx_fill_restart_validation.py::test_restart_requires_metadata_exact_pair_and_correct_token",
    "single_use_token_and_binding": "tests/test_okx_fill_restart_validation.py::test_resume_token_is_single_use_and_binding_drift_fails",
    "checkpoint_live_order": "tests/test_okx_fill_restart_validation.py::test_checkpoint_refuses_live_owned_orders_or_flat_fill",
    "special_not_fifo": "tests/test_okx_fill_restart_validation.py::test_special_reduce_only_fill_is_accounted_but_not_normal_fifo",
    "journal_recovery": "tests/test_okx_fill_restart_validation.py::test_hash_chain_round_trip_and_journal_only_recovery",
    "corrupt_truncate_hash_break": "tests/test_okx_fill_restart_validation.py::test_corrupt_truncated_and_hash_broken_state_fail_closed",
    "atomic_replace_failure": "tests/test_okx_fill_restart_validation.py::test_snapshot_replace_failure_is_durable_but_runtime_halts",
    "run_id_reuse": "tests/test_okx_fill_restart_validation.py::test_run_id_reuse_is_refused",
    "artifact_contract": "tests/test_okx_fill_restart_offline.py::test_offline_spec_is_demo_only_non_armed_and_exactly_bound",
    "reference_fixture_no_external_activity": "tests/test_okx_fill_restart_offline.py::test_reference_fixture_completes_r1_r2_without_external_activity",
    "predecessor_audit": "tests/test_okx_fill_restart_offline.py::test_predecessor_audit_passes_read_only",
}


class OfflineEvidenceError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        if not value.endswith("\n"):
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OfflineEvidenceError(f"expected JSON object: {path}")
    return value


def verify_predecessors(root: Path) -> dict[str, Any]:
    root = root.resolve()
    v16_spec_path = (
        root / "artifacts" / "mm_v1_6_economic_viability"
        / "specification_20260801T070658Z" / "protocol_spec.json"
    )
    prior_root = (
        root / "artifacts" / "okx_demo_execution_safety" / PRIOR_RUN_ID
    )
    v16_spec = _json(v16_spec_path)
    v16_failures: list[str] = []
    v16_checked = 0
    for relative, expected in dict(v16_spec["source_hashes"]).items():
        if relative == "AGENTS.md":
            continue
        v16_checked += 1
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            v16_failures.append(relative)

    promotion = _json(prior_root / "specification" / "promotion_manifest.json")
    runtime_failures: list[str] = []
    runtime_checked = 0
    for relative, expected in dict(promotion["runtime_source_hashes"]).items():
        if relative == "AGENTS.md":
            continue
        runtime_checked += 1
        path = root / relative
        if not path.is_file() or _sha256(path) != expected:
            runtime_failures.append(relative)

    completion_path = prior_root / "completion_hashes.json"
    completion = _json(completion_path)
    artifact_failures: list[str] = []
    artifact_checked = 0
    for relative, expected in completion.items():
        artifact_checked += 1
        path = prior_root / relative
        if not path.is_file() or _sha256(path) != expected:
            artifact_failures.append(relative)

    root_protocol = root / "AGENTS.md"
    protocol_copy = root / "AGENTS_OKX_DEMO_FILL_RESTART_VALIDATION.md"
    fixed_hashes = {
        "root_protocol_copy_exact": (
            root_protocol.read_bytes() == protocol_copy.read_bytes()
        ),
        "v16_specification": _sha256(v16_spec_path) == V16_SPEC_SHA256,
        "prior_protocol_file": (
            _sha256(prior_root / "specification" / "protocol_spec.json")
            == PRIOR_PROTOCOL_FILE_SHA256
        ),
        "prior_completion_manifest": (
            _sha256(completion_path) == PRIOR_COMPLETION_MANIFEST_SHA256
        ),
        "prior_decision": (
            _sha256(prior_root / "decision" / "decision.json")
            == PRIOR_DECISION_SHA256
        ),
        "prior_completed": (
            _sha256(prior_root / "COMPLETED.json") == PRIOR_COMPLETED_SHA256
        ),
        "predecessor_protocol": (
            _sha256(root / "AGENTS_OKX_DEMO_EXECUTION_SAFETY.md")
            == PREDECESSOR_PROTOCOL_SHA256
        ),
        "v16_status": (
            _json(
                root / "artifacts" / "mm_v1_6_economic_viability"
                / "decision_20260801T070658Z" / "decision.json"
            ).get("status") == "MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT"
        ),
        "prior_demo_status": (
            _json(prior_root / "decision" / "decision.json").get("status")
            == "OKX_DEMO_EXECUTION_SAFETY_SUPPORT"
        ),
        "prior_canonical_specification": (
            (prior_root / "specification" / "protocol_spec.sha256")
            .read_text(encoding="utf-8").strip()
            == PRIOR_CANONICAL_SPEC_SHA256
        ),
    }
    passed = (
        not v16_failures
        and not runtime_failures
        and not artifact_failures
        and all(fixed_hashes.values())
    )
    return {
        "passed": passed,
        "authority_transition_only": True,
        "v16_non_authority_checked": v16_checked,
        "v16_non_authority_failures": v16_failures,
        "prior_runtime_non_authority_checked": runtime_checked,
        "prior_runtime_non_authority_failures": runtime_failures,
        "prior_artifacts_checked": artifact_checked,
        "prior_artifact_failures": artifact_failures,
        "fixed_hashes": fixed_hashes,
        "v16_specification_sha256": V16_SPEC_SHA256,
        "prior_demo_run_id": PRIOR_RUN_ID,
        "prior_demo_canonical_specification_sha256": PRIOR_CANONICAL_SPEC_SHA256,
    }


def source_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise OfflineEvidenceError(f"runtime source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return hashes


def installed_ccxt_contract() -> dict[str, Any]:
    import ccxt

    source = Path(inspect.getsourcefile(ccxt.okx) or "").resolve()
    if not source.is_file():
        raise OfflineEvidenceError("installed CCXT OKX source is unavailable")
    return {
        "inspection_only": True,
        "network_used": False,
        "ccxt_version": ccxt.__version__,
        "okx_source_sha256": _sha256(source),
        "normal_order_fields": ["postOnly", "reduceOnly", "tdMode", "clOrdId"],
        "flatten_order_fields": ["reduceOnly", "tdMode", "clOrdId"],
        "demo_transport_required": {
            "sandboxMode": True,
            "x-simulated-trading": "1",
        },
        "live_mode_available": False,
    }


def build_offline_spec(
    *,
    root: Path,
    run_id: str,
    predecessor_audit: dict[str, Any],
    hashes: dict[str, str],
) -> tuple[dict[str, Any], RuntimeBinding]:
    if not predecessor_audit.get("passed"):
        raise OfflineEvidenceError("immutable predecessor audit failed")
    profile = load_promoted_profile(root)
    promotion = _json(
        root / "artifacts" / "okx_demo_execution_safety" / PRIOR_RUN_ID
        / "specification" / "promotion_manifest.json"
    )
    source_manifest_sha256 = canonical_sha256(hashes)
    risk = RiskBudget()
    risk.validate()
    runtime_configuration = {
        "protocol_id": "okx-demo-fill-restart-validation-v1",
        "run_id": run_id,
        "execution_mode": "OFFLINE_FIXTURE",
        "network_allowed": False,
        "preflight_allowed": False,
        "orders_allowed": False,
        "formal_execution_armed": False,
        "live_mode_available": False,
        "symbol": "BTC/USDT:USDT",
        "market_type": "linear_swap",
        "margin_mode": "isolated",
        "position_mode": "net_mode",
        "leverage": 3,
        "market_fingerprint": promotion["runtime_configuration"][
            "market_fingerprint"
        ],
        "profile_binding_sha256": profile.binding_sha256,
        "risk_budget": risk.to_dict(),
        "restart_checkpoints": [
            "RESTART_REQUIRED_AFTER_FILL",
            "RESTART_REQUIRED_AFTER_KILL_LATCH",
        ],
        "source_manifest_sha256": source_manifest_sha256,
    }
    runtime_hash = canonical_sha256(runtime_configuration)
    binding = RuntimeBinding(
        run_id=run_id,
        execution_mode="OFFLINE_FIXTURE",
        account_binding="offline-fixture-no-account-credentials",
        market_fingerprint=str(runtime_configuration["market_fingerprint"]),
        profile_binding_sha256=profile.binding_sha256,
        runtime_configuration_sha256=runtime_hash,
        source_manifest_sha256=source_manifest_sha256,
    )
    binding.validate()
    fixture_manifest = {
        **{key: {"test": value, "predecessor": True}
           for key, value in PREDECESSOR_FIXTURES.items()},
        **{key: {"test": value, "predecessor": False}
           for key, value in FILL_RESTART_FIXTURES.items()},
    }
    spec = {
        "protocol_id": "okx-demo-fill-restart-validation-v1",
        "phase": "OFFLINE_IMPLEMENTATION_AND_FIXTURES",
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predecessor_hash_audit_sha256": canonical_sha256(predecessor_audit),
        "profile_binding": profile.binding_payload,
        "profile_binding_sha256": profile.binding_sha256,
        "runtime_binding": binding.to_dict(),
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": runtime_hash,
        "source_hashes": hashes,
        "source_manifest_sha256": source_manifest_sha256,
        "risk_budget": risk.to_dict(),
        "ccxt_contract": installed_ccxt_contract(),
        "fixture_manifest": fixture_manifest,
        "artifact_contract": {
            "root": "artifacts/okx_demo_fill_restart_validation",
            "non_overwriting": True,
            "append_only_reference_state_journal": True,
            "formal_execution_marker_created": False,
            "completed_json_reserved_for_formal_close": True,
            "offline_phase_terminal": "OFFLINE_PHASE_COMPLETED.json",
        },
        "prohibitions": {
            "network": True,
            "okx_preflight": True,
            "orders": True,
            "live": True,
            "git_writes": True,
            "optuna": True,
            "v16_evidence_mutation": True,
            "prior_demo_evidence_mutation": True,
        },
    }
    spec["offline_specification_sha256"] = canonical_sha256(spec)
    return spec, binding


def _fill(order: OwnedOrder, trade_id: str, timestamp_ms: int, price: str) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "timestamp_ms": timestamp_ms,
        "side": order.side,
        "price": price,
        "quantity_btc": "0.01",
        "fee_cost": "0.10",
        "fee_currency": "USDT",
        "liquidity": "maker",
        "reduce_only": False,
    }


def _single_page(fill: dict[str, object]) -> list[dict[str, object]]:
    return [{
        "page_index": 0,
        "cursor": "",
        "next_cursor": "",
        "trades": [fill],
    }]


def run_reference_fixture(output: Path, binding: RuntimeBinding) -> dict[str, Any]:
    state_dir = output / "reference_fixture"
    engine = FillRestartEngine.create(
        store=HashChainStateStore(state_dir / "runtime_state.json"),
        binding=binding,
    )
    bid = OwnedOrder(
        "fixture-bid-client", "fixture-bid-order", "buy",
        Decimal("0.01"), Decimal("0.01"), False, True,
    )
    engine.record_owned_order_ack(bid)
    engine.ingest_fill_pages(_single_page(_fill(bid, "fixture-bid-fill", 1000, "50000")))
    engine.record_authoritative_snapshot(make_snapshot(
        binding,
        sequence=1,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    ))
    r1 = engine.checkpoint_after_actual_fill()
    r1_pair = (
        make_snapshot(binding, sequence=2, position_btc="0.01", average_entry_price="50000", run_fees_usdt="0.10"),
        make_snapshot(binding, sequence=3, position_btc="0.01", average_entry_price="50000", run_fees_usdt="0.10"),
    )
    engine = FillRestartEngine.resume(
        store=engine.store,
        expected_binding=binding,
        resume_token=r1.resume_token,
        market_metadata_loaded=True,
        snapshots=r1_pair,
    )
    ask = OwnedOrder(
        "fixture-ask-client", "fixture-ask-order", "sell",
        Decimal("0.01"), Decimal("0.01"), False, True,
    )
    engine.record_owned_order_ack(ask)
    engine.ingest_fill_pages(_single_page(_fill(ask, "fixture-ask-fill", 2000, "50010")))
    engine.record_authoritative_snapshot(make_snapshot(
        binding,
        sequence=4,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.20",
    ))
    r2 = engine.checkpoint_kill_latch()
    activation_id = engine.state.kill_latch.activation_id
    r2_pair = (
        make_snapshot(binding, sequence=5, position_btc="0", average_entry_price="0", run_fees_usdt="0.20"),
        make_snapshot(binding, sequence=6, position_btc="0", average_entry_price="0", run_fees_usdt="0.20"),
    )
    engine = FillRestartEngine.resume(
        store=engine.store,
        expected_binding=binding,
        resume_token=r2.resume_token,
        market_metadata_loaded=True,
        snapshots=r2_pair,
    )
    blocked_before_release = not engine.state.can_submit
    engine.release_kill_latch(
        acknowledgement_id=activation_id,
        snapshots=r2_pair,
    )
    result = {
        "passed": (
            engine.state.r1_completed
            and engine.state.r2_completed
            and engine.state.ledger.inventory_btc == 0
            and not engine.state.open_orders
            and blocked_before_release
            and engine.state.external_network_attempts == 0
            and engine.state.external_preflight_attempts == 0
            and engine.state.external_order_submissions == 0
        ),
        "execution_mode": binding.execution_mode,
        "r1_completed": engine.state.r1_completed,
        "r2_completed": engine.state.r2_completed,
        "r1_checkpoint_id": r1.checkpoint_id,
        "r2_checkpoint_id": r2.checkpoint_id,
        "process_generation": engine.state.process_generation,
        "normal_bid_fills": engine.state.ledger.normal_bid_fills,
        "normal_ask_fills": engine.state.ledger.normal_ask_fills,
        "normal_fifo_round_trips": len(engine.state.ledger.normal_round_trips),
        "gross_realized_pnl_usdt": str(
            engine.state.ledger.gross_realized_pnl_usdt
        ),
        "actual_fees_usdt": str(engine.state.ledger.total_fees_usdt),
        "net_realized_pnl_usdt": str(engine.state.ledger.net_realized_pnl_usdt),
        "final_position_btc": str(engine.state.ledger.inventory_btc),
        "final_owned_open_orders": len(engine.state.open_orders),
        "kill_latch_blocked_before_release": blocked_before_release,
        "resume_token_plaintext_persisted": False,
        "external_network_attempts": engine.state.external_network_attempts,
        "external_preflight_attempts": engine.state.external_preflight_attempts,
        "external_order_submissions": engine.state.external_order_submissions,
    }
    _write_json(state_dir / "reference_result.json", result)
    return result


def _run_suite(
    *,
    root: Path,
    output: Path,
    name: str,
    arguments: Iterable[str],
) -> dict[str, Any]:
    tests_dir = output / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    audit_path = tests_dir / f"{name}_network_audit.json"
    # Keep high-churn fixture files out of immutable evidence and use a short
    # project-local path so Windows atomic replaces remain reliable.
    base_temp = root / ".okx_fill_restart_tmp" / output.name / name
    base_temp.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *arguments,
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "okx_offline_network_guard",
        "--basetemp",
        str(base_temp.resolve()),
    ]
    environment = dict(os.environ)
    for name_to_clear in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    ):
        environment[name_to_clear] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
    finally:
        shutil.rmtree(base_temp, ignore_errors=True)
    _write_text(tests_dir / f"{name}.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1,
        "optuna_imported": True,
        "live_endpoint_attempts": -1,
    }
    result = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": int(audit.get("network_attempts", -1)),
        "live_endpoint_attempts": int(audit.get("live_endpoint_attempts", -1)),
        "optuna_imported": bool(audit.get("optuna_imported", True)),
        "command_scope": list(arguments),
    }
    result["passed_gate"] = (
        result["returncode"] == 0
        and result["passed"] > 0
        and result["network_attempts"] == 0
        and result["live_endpoint_attempts"] == 0
        and result["optuna_imported"] is False
    )
    return result


def run_test_suites(root: Path, output: Path) -> dict[str, Any]:
    suites = {
        "fill_restart_targeted": _run_suite(
            root=root,
            output=output,
            name="fill_restart_targeted",
            arguments=(
                "tests/test_okx_fill_restart_validation.py",
                "tests/test_okx_fill_restart_offline.py",
            ),
        ),
        "root_non_optuna": _run_suite(
            root=root,
            output=output,
            name="root_non_optuna",
            arguments=("tests",),
        ),
        "backtest_non_optuna": _run_suite(
            root=root,
            output=output,
            name="backtest_non_optuna",
            arguments=(
                "backtest/tests",
                "--ignore=backtest/tests/test_units_and_safety.py",
                "--ignore=backtest/tests/test_robust_gates.py",
            ),
        ),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(value["passed_gate"] for value in suites.values()):
        raise OfflineEvidenceError("one or more offline regression suites failed")
    return suites


def _secret_scan(output: Path) -> dict[str, Any]:
    secret_values = [
        os.environ.get(name, "")
        for name in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE")
    ]
    secret_values = [value for value in secret_values if len(value) >= 8]
    scanned = 0
    matches: list[str] = []
    for path in output.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
            continue
        scanned += 1
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for value in secret_values:
            if value.encode("utf-8") in raw:
                matches.append(path.relative_to(output).as_posix())
                break
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_matches": sorted(set(matches)),
        "credentials_read_from_dotenv": False,
        "credential_values_reported": False,
    }


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "OFFLINE_PHASE_COMPLETED.json"}
    result: dict[str, str] = {}
    for path in output.rglob("*"):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.name in ignored
            or path.suffix == ".tmp"
        ):
            continue
        result[path.relative_to(output).as_posix()] = _sha256(path)
    return dict(sorted(result.items()))


def run_offline_validation(root: Path, run_id: str) -> Path:
    root = root.resolve()
    if not run_id.startswith("offline-") or any(ch.isspace() for ch in run_id):
        raise OfflineEvidenceError("offline run_id must start with 'offline-' and contain no whitespace")
    output = root / ARTIFACT_ROOT / run_id
    if output.exists():
        raise OfflineEvidenceError("offline run-ID reuse refused")
    output.mkdir(parents=True)

    predecessor = verify_predecessors(root)
    _write_json(output / "predecessor" / "hash_audit.json", predecessor)
    if not predecessor["passed"]:
        raise OfflineEvidenceError("predecessor evidence changed")
    hashes = source_hashes(root)
    spec, binding = build_offline_spec(
        root=root,
        run_id=run_id,
        predecessor_audit=predecessor,
        hashes=hashes,
    )
    _write_json(output / "specification" / "offline_spec.json", spec)
    _write_json(output / "specification" / "source_hashes.json", hashes)
    _write_json(output / "specification" / "runtime_binding.json", binding.to_dict())
    _write_json(output / "specification" / "risk_budget.json", RiskBudget().to_dict())
    _write_json(output / "fixtures" / "fixture_manifest.json", spec["fixture_manifest"])
    _write_json(output / "readiness" / "initial_readiness.json", {
        "passed": True,
        "execution_mode": "OFFLINE_FIXTURE",
        "predecessor_hashes_passed": True,
        "network_allowed": False,
        "preflight_allowed": False,
        "orders_allowed": False,
        "formal_execution_armed": False,
    })

    reference = run_reference_fixture(output, binding)
    suites = run_test_suites(root, output)
    endpoint_audit = {
        "passed": all(row["network_attempts"] == 0 for row in suites.values()),
        "execution_mode": "OFFLINE_FIXTURE",
        "network_attempts": sum(row["network_attempts"] for row in suites.values()),
        "live_endpoint_attempts": sum(
            row["live_endpoint_attempts"] for row in suites.values()
        ),
        "attempted_hosts": [],
        "okx_preflight_executed": False,
        "external_orders_submitted": 0,
        "fixture_exchange_calls_are_local_test_doubles": True,
    }
    _write_json(output / "audits" / "endpoint_audit.json", endpoint_audit)
    _write_json(output / "audits" / "restart_audit.json", {
        "passed": reference["passed"],
        "r1_completed": reference["r1_completed"],
        "r2_completed": reference["r2_completed"],
        "process_generation": reference["process_generation"],
        "kill_latch_blocked_before_release": reference[
            "kill_latch_blocked_before_release"
        ],
        "resume_token_plaintext_persisted": False,
    })
    _write_json(output / "audits" / "accounting_audit.json", {
        "passed": reference["passed"],
        "normal_bid_fills": reference["normal_bid_fills"],
        "normal_ask_fills": reference["normal_ask_fills"],
        "normal_fifo_round_trips": reference["normal_fifo_round_trips"],
        "gross_realized_pnl_usdt": reference["gross_realized_pnl_usdt"],
        "actual_fees_usdt": reference["actual_fees_usdt"],
        "net_realized_pnl_usdt": reference["net_realized_pnl_usdt"],
        "special_fills_excluded": True,
    })
    secret_scan = _secret_scan(output)
    _write_json(output / "audits" / "secret_scan.json", secret_scan)
    final_passed = (
        reference["passed"]
        and endpoint_audit["passed"]
        and secret_scan["passed"]
        and all(row["passed_gate"] for row in suites.values())
    )
    readiness = {
        "passed": final_passed,
        "offline_implementation_ready": final_passed,
        "formal_demo_ready": False,
        "read_only_preflight_required_next": True,
        "preflight_executed": False,
        "formal_execution_armed": False,
        "network_attempts": endpoint_audit["network_attempts"],
        "orders_submitted": 0,
        "live_endpoint_attempts": endpoint_audit["live_endpoint_attempts"],
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
    }
    _write_json(output / "readiness" / "final_readiness.json", readiness)
    decision = {
        "phase_status": (
            "OFFLINE_IMPLEMENTATION_READY" if final_passed
            else "OFFLINE_IMPLEMENTATION_FAILED"
        ),
        "final_protocol_status": "NOT_EVALUATED",
        "formal_demo_execution_authorized": False,
        "read_only_preflight_authorized": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": endpoint_audit["live_endpoint_attempts"],
        "live_orders": 0,
        "network_attempts": endpoint_audit["network_attempts"],
        "preflight_executed": False,
        "orders_submitted": 0,
        "optuna_executed": False,
        "v16_evidence_mutated": False,
        "prior_demo_evidence_mutated": False,
        "next_boundary": (
            "separately authorized read-only OKX Demo preflight with a session arm token"
        ),
    }
    _write_json(output / "decision" / "offline_decision.json", decision)
    _write_text(output / "decision" / "offline_decision.md", (
        "# OKX Demo Fill & Restart Validation - Offline Phase\n\n"
        f"- Phase status: `{decision['phase_status']}`\n"
        "- Network attempts: `0`\n"
        "- OKX preflight: `not executed`\n"
        "- Orders submitted: `0`\n"
        "- Live mode: `unavailable`\n"
        "- Formal protocol status: `not evaluated`\n\n"
        "The next boundary is a separately authorized, read-only OKX Demo preflight.\n"
    ))
    hashes_manifest = _completion_hashes(output)
    _write_json(output / "completion_hashes.json", hashes_manifest)
    terminal = {
        "phase_status": decision["phase_status"],
        "run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "offline_only": True,
        "network_attempts": 0,
        "preflight_executed": False,
        "orders_submitted": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "formal_execution_armed": False,
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "completed_json_reserved_for_formal_close": True,
    }
    _write_json(output / "OFFLINE_PHASE_COMPLETED.json", terminal)
    if not final_passed:
        raise OfflineEvidenceError("offline implementation gates failed")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        output = run_offline_validation(args.root, args.run_id)
    except Exception as exc:
        print(f"OFFLINE_IMPLEMENTATION_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
