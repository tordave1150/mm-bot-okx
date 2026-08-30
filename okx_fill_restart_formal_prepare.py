"""Freeze one future formal OKX Demo orchestration package entirely offline.

This module only reads already-written evidence, inspects installed local
sources, runs socket-denied fixtures, and writes a non-overwriting package.
It never constructs an authenticated exchange, reads credentials, performs a
preflight, creates an execution marker, or submits/cancels an order.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from okx_demo_profile import load_promoted_profile
from okx_fill_restart_formal import (
    FORMAL_PROTOCOL_ID,
    FormalRunSpecification,
    canonical_sha256,
)
from okx_fill_restart_offline import (
    ARTIFACT_ROOT,
    PRIOR_RUN_ID,
    _completion_hashes,
    _json,
    _sha256,
    _write_json,
    _write_text,
    installed_ccxt_contract,
    verify_predecessors,
)
from okx_fill_restart_preflight import (
    OFFLINE_READY_STATUS,
    preflight_source_hashes,
    verify_offline_evidence,
)


FORMAL_PACKAGE_STATUS = "FORMAL_PACKAGE_FROZEN_OFFLINE"
PREFLIGHT_READY_STATUS = "READ_ONLY_PREFLIGHT_PASSED"
DEFAULT_OFFLINE_RUN_ID = "offline-20260803T160436Z"
DEFAULT_PREFLIGHT_RUN_ID = "preflight-20260803T161744Z"
FORMAL_PACKAGE_SOURCE_FILES = (
    "okx_fill_restart_formal.py",
    "okx_fill_restart_gateway.py",
    "okx_fill_restart_executor.py",
    "okx_fill_restart_formal_prepare.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_formal_prepare.py",
)
FORMAL_FIXTURES = {
    "formal_not_armed_and_run_reuse": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_prepare_is_offline_not_armed_and_run_reuse_is_refused"
    ),
    "formal_spec_arm_and_transport_drift": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_spec_and_arm_fail_closed_on_drift_or_bad_snapshot"
    ),
    "write_ahead_identity_and_ambiguous_create": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_write_ahead_identity_rate_cap_and_ambiguous_resolution"
    ),
    "missing_incomplete_foreign_snapshot": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_missing_order_incomplete_or_foreign_snapshot_halts_without_create"
    ),
    "formal_r1_r2_exact_accounting": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_happy_path_requires_r1_r2_and_exact_accounting"
    ),
    "both_sides_before_r1_and_accounting_mismatch": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_both_sides_before_r1_and_bad_accounting_fail_closed"
    ),
    "deadline_single_flight_flatten": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_deadline_flatten_is_single_flight_and_halts"
    ),
    "formal_state_truncation_and_hash_break": (
        "tests/test_okx_fill_restart_formal.py::"
        "test_state_store_detects_truncation_and_hash_break"
    ),
    "demo_gateway_transport_mutation_and_flatten_scope": (
        "tests/test_okx_fill_restart_gateway.py"
    ),
    "formal_executor_package_arm_stream_and_fill_binding": (
        "tests/test_okx_fill_restart_executor.py"
    ),
    "fill_history_recent_tail_union_and_conflict": (
        "tests/test_okx_fill_restart_gateway.py::"
        "test_fill_union_recovers_fresh_tail_omission_and_deduplicates"
    ),
    "single_flatten_many_partial_fills": (
        "tests/test_okx_fill_restart_validation.py::"
        "test_one_flatten_order_accepts_many_partial_fills_without_resubmission"
    ),
}


class FormalPackageError(RuntimeError):
    pass


class _OfflineSocketGuard:
    """Deny and count parent-process socket dispatch during package freeze."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._create_connection = socket.create_connection

    def __enter__(self) -> "_OfflineSocketGuard":
        guard = self

        def blocked_connect(instance: socket.socket, address: object) -> None:
            guard.attempts.append(type(address).__name__)
            raise FormalPackageError(
                "network access is prohibited during formal package freeze"
            )

        def blocked_connect_ex(instance: socket.socket, address: object) -> int:
            guard.attempts.append(type(address).__name__)
            raise FormalPackageError(
                "network access is prohibited during formal package freeze"
            )

        def blocked_create_connection(
            address: object, *args: object, **kwargs: object
        ) -> None:
            guard.attempts.append(type(address).__name__)
            raise FormalPackageError(
                "network access is prohibited during formal package freeze"
            )

        socket.socket.connect = blocked_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = blocked_connect_ex  # type: ignore[method-assign]
        socket.create_connection = blocked_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, *args: object) -> None:
        socket.socket.connect = self._connect  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
        socket.create_connection = self._create_connection


def verify_preflight_evidence(root: Path, run_id: str) -> dict[str, object]:
    output = root / ARTIFACT_ROOT / run_id
    terminal_path = output / "PREFLIGHT_PHASE_COMPLETED.json"
    decision_path = output / "decision" / "preflight_decision.json"
    result_path = output / "preflight" / "preflight_result.json"
    completion_path = output / "completion_hashes.json"
    source_path = output / "specification" / "source_hashes.json"
    required = (
        terminal_path, decision_path, result_path, completion_path, source_path,
    )
    if not all(path.is_file() for path in required):
        raise FormalPackageError("read-only preflight evidence is incomplete")
    terminal = _json(terminal_path)
    decision = _json(decision_path)
    result = _json(result_path)
    if terminal.get("phase_status") != PREFLIGHT_READY_STATUS:
        raise FormalPackageError("read-only preflight terminal status is not passed")
    if (
        decision.get("phase_status") != PREFLIGHT_READY_STATUS
        or decision.get("read_only_preflight_passed") is not True
        or result.get("status") != PREFLIGHT_READY_STATUS
        or result.get("passed") is not True
    ):
        raise FormalPackageError("read-only preflight decision is not passed")
    zero_mutation_values = (
        terminal.get("orders_submitted"), terminal.get("live_endpoint_attempts"),
        terminal.get("live_orders"), result.get("orders_submitted"),
        result.get("orders_amended"), result.get("orders_cancelled"),
        result.get("mutation_attempts"), result.get("live_endpoint_attempts"),
    )
    if any(value != 0 for value in zero_mutation_values):
        raise FormalPackageError("preflight contains a mutation, order, or Live attempt")
    if any((
        terminal.get("formal_execution_armed") is not False,
        decision.get("formal_demo_execution_authorized") is not False,
        result.get("snapshot_count") != 2,
        result.get("snapshots_consistent") is not True,
        result.get("state_resolved") is not True,
        result.get("market_timestamps_strictly_monotonic") is not True,
        result.get("fee_schedule_matches_frozen_profile") is not True,
    )):
        raise FormalPackageError("preflight boundary or snapshot gate is invalid")
    for name in ("initial_snapshot", "verified_snapshot"):
        snapshot = result.get(name)
        if not isinstance(snapshot, dict):
            raise FormalPackageError("preflight account snapshot is missing")
        if snapshot.get("position_btc") != 0.0 or snapshot.get("open_orders") != 0:
            raise FormalPackageError("preflight account was not flat and empty")
        if snapshot.get("position_mode") != "net_mode" or snapshot.get("leverage") != 3.0:
            raise FormalPackageError("preflight account mode or leverage drifted")
    transport = result.get("transport_audit")
    if not isinstance(transport, dict) or any((
        transport.get("sandbox_mode") is not True,
        transport.get("simulated_trading_header") is not True,
        transport.get("non_withdrawal_permissions") is not True,
        transport.get("read_permission_present") is not True,
        transport.get("trade_permission_present") is not True,
        transport.get("mutation_attempts") != 0,
        transport.get("live_endpoint_attempts") != 0,
    )):
        raise FormalPackageError("preflight Demo transport or permission proof failed")
    completion = _json(completion_path)
    failures = [
        relative for relative, expected in completion.items()
        if not (output / relative).is_file()
        or _sha256(output / relative) != expected
    ]
    if failures or _sha256(completion_path) != terminal.get("completion_hashes_sha256"):
        raise FormalPackageError("preflight completion hash verification failed")
    frozen_sources = _json(source_path)
    source_failures = [
        relative for relative, expected in frozen_sources.items()
        if not (root / relative).is_file() or _sha256(root / relative) != expected
    ]
    if source_failures:
        raise FormalPackageError("preflight-bound source changed")
    if result.get("profile_binding_sha256") != load_promoted_profile(root).binding_sha256:
        raise FormalPackageError("preflight profile binding drifted")
    return {
        "passed": True,
        "run_id": run_id,
        "status": PREFLIGHT_READY_STATUS,
        "completion_files_checked": len(completion),
        "source_files_checked": len(frozen_sources),
        "completion_hashes_sha256": _sha256(completion_path),
        "terminal_sha256": _sha256(terminal_path),
        "decision_sha256": _sha256(decision_path),
        "result_sha256": _sha256(result_path),
        "account_binding_sha256": result["account_binding_sha256"],
        "market_fingerprint": result["market_fingerprint"],
        "market_spec": result["market_spec"],
        "read_only_network_requests_in_prior_preflight": result["network_attempts"],
        "current_task_network_requests": 0,
        "orders_submitted": 0,
    }


def formal_source_hashes(root: Path) -> dict[str, str]:
    hashes = preflight_source_hashes(root)
    for relative in FORMAL_PACKAGE_SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise FormalPackageError(f"formal package source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _installed_formal_ccxt_contract() -> dict[str, object]:
    contract = installed_ccxt_contract()
    contract.update({
        "inspection_only_during_package_freeze": True,
        "network_used_during_package_freeze": False,
        "rest_host": "www.okx.com",
        "rest_url_template": "https://{hostname}",
        "private_paths": {
            "create": "POST /api/v5/trade/order",
            "cancel": "POST /api/v5/trade/cancel-order",
            "open_orders": "GET /api/v5/trade/orders-pending",
            "fills": "GET /api/v5/trade/fills",
            "positions": "GET /api/v5/account/positions",
            "balance": "GET /api/v5/account/balance",
            "account_config": "GET /api/v5/account/config",
            "leverage": "GET /api/v5/account/leverage-info",
            "fee": "GET /api/v5/account/trade-fee",
        },
        "public_paths": {
            "book": "GET /api/v5/market/books",
            "time": "GET /api/v5/public/time",
            "instruments": "GET /api/v5/public/instruments",
        },
        "client_order_id_max_characters": 32,
        "base_quantity_btc": "0.01",
        "contract_size_btc": "0.01",
        "exchange_amount_contracts": "1",
    })
    return contract


def build_formal_package_spec(
    *,
    root: Path,
    package_id: str,
    formal_run_id: str,
    predecessor_audit: dict[str, Any],
    offline_audit: dict[str, object],
    preflight_audit: dict[str, object],
    hashes: dict[str, str],
) -> tuple[dict[str, object], FormalRunSpecification]:
    if not package_id.startswith("formal-package-") or any(ch.isspace() for ch in package_id):
        raise FormalPackageError("formal package ID is invalid")
    if not formal_run_id.startswith("formal-") or any(ch.isspace() for ch in formal_run_id):
        raise FormalPackageError("formal run ID is invalid")
    if not predecessor_audit.get("passed"):
        raise FormalPackageError("immutable predecessor audit failed")
    if not offline_audit.get("passed") or not preflight_audit.get("passed"):
        raise FormalPackageError("offline or preflight evidence is not passed")
    profile = load_promoted_profile(root)
    prior_promotion = _json(
        root / "artifacts" / "okx_demo_execution_safety" / PRIOR_RUN_ID
        / "specification" / "promotion_manifest.json"
    )
    market_fingerprint = str(preflight_audit["market_fingerprint"])
    if market_fingerprint != prior_promotion["runtime_configuration"]["market_fingerprint"]:
        raise FormalPackageError("preflight market fingerprint differs from promotion")
    source_manifest_sha256 = canonical_sha256(hashes)
    ccxt_contract = _installed_formal_ccxt_contract()
    risk_budget = {
        "capital_usdt": "750",
        "leverage": 3,
        "maximum_inventory_btc": "0.01",
        "soft_guard_usdt": "22.50",
        "hard_kill_usdt": "37.50",
        "maximum_wall_minutes": 120,
        "maximum_normal_creates": 120,
        "maximum_owned_bid": 1,
        "maximum_owned_ask": 1,
        "maximum_unresolved_flatten": 1,
    }
    runtime_configuration = {
        "protocol_id": FORMAL_PROTOCOL_ID,
        "formal_run_id": formal_run_id,
        "package_id": package_id,
        "execution_mode_after_separate_arm": "OKX_DEMO",
        "package_freeze_mode": "OFFLINE_FIXTURE",
        "network_allowed_during_package_freeze": False,
        "orders_allowed_during_package_freeze": False,
        "formal_execution_armed": False,
        "live_mode_available": False,
        "symbol": "BTC/USDT:USDT",
        "market_type": "linear_swap",
        "margin_mode": "isolated",
        "position_mode": "net_mode",
        "leverage": 3,
        "market_fingerprint": market_fingerprint,
        "profile_binding_sha256": profile.binding_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "orchestrator": "okx_fill_restart_formal.FormalOrchestrator",
        "execution_driver": "okx_fill_restart_executor.FormalExecutionDriver",
        "demo_gateway": "okx_fill_restart_gateway.FormalDemoGateway",
        "quote_engine_sources": {
            name: hashes[name] for name in (
                "okx_demo_profile.py", "okx_demo_runtime.py", "market_spec.py",
                "market_maker/as_strategy.py", "market_maker/quote_model.py",
                "market_maker/inventory.py", "market_maker/volatility.py",
                "market_maker/arrival_intensity.py",
            )
        },
        "observation_interval_ms": 350,
        "minimum_create_interval_ms": 2_000,
        "maximum_market_age_ms": 1_000,
        "maximum_clock_skew_ms": 1_500,
        "minimum_order_lifetime_ticks": (
            profile.strategy.minimum_order_lifetime_ticks
        ),
        "maximum_order_age_ticks": profile.strategy.maximum_order_age_ticks,
        "requote_threshold_ticks": profile.strategy.requote_threshold_ticks,
        "fill_pagination": {
            "ccxt_paginate": True,
            "maximum_calls_per_snapshot": 3,
            "page_size": 100,
            "recent_tail_query": True,
            "recent_tail_limit": 100,
            "query_order": ["paginated_history", "recent_tail"],
            "union_identity": "trade_id",
            "conflicting_duplicate_policy": "fail_closed",
            "filter_before_union": "timestamp_ms >= formal_trade_since_ms",
            "same_timestamp_deduplication": True,
        },
        "risk_budget": risk_budget,
    }
    runtime_configuration_sha256 = canonical_sha256(runtime_configuration)
    restart_contract = {
        "process_generations": [0, 1, 2],
        "R1": {
            "trigger": "first owned normal maker fill with observable nonzero position",
            "required_stage": "RESTART_REQUIRED_R1",
            "exit_signal": "RESTART_REQUIRED_AFTER_FILL",
            "resume_token_binding": [
                "formal_run_id", "package_specification_sha256",
                "source_manifest_sha256", "checkpoint_id", "process_generation",
            ],
            "resume_requirements": [
                "metadata_loaded_before_state", "two_exact_authoritative_snapshots",
                "zero_owned_orders", "same_nonzero_position", "exact_fill_cursor",
                "exact_inventory_entry_pnl_fees_account_binding",
            ],
        },
        "R2": {
            "trigger": "normal maker FIFO round trip while flat and empty",
            "required_stage": "RESTART_REQUIRED_R2",
            "exit_signal": "RESTART_REQUIRED_AFTER_KILL_LATCH",
            "resume_requirements": [
                "persistent_validation_kill", "submission_block_proof",
                "two_exact_flat_empty_snapshots", "matching_activation_acknowledgement",
            ],
        },
        "new_run_id_on_resume": False,
        "second_execution_marker_on_resume": False,
    }
    endpoint_contract = {
        "environment": "OKX_DEMO",
        "hostname": ccxt_contract["rest_host"],
        "sandbox_mode_required_immediately_before_every_request": True,
        "x_simulated_trading_header_required_immediately_before_every_request": "1",
        "fallback_to_live": False,
        "live_endpoint_attempt_budget": 0,
        "request_paths": {
            "private": ccxt_contract["private_paths"],
            "public": ccxt_contract["public_paths"],
        },
        "normal_create_non_secret_fields": {
            "type": "limit",
            "postOnly": True,
            "reduceOnly": False,
            "tdMode": "isolated",
            "clOrdId": "deterministic_session_scoped_max_30_chars",
            "amount_contracts": "1",
        },
        "emergency_flatten_non_secret_fields": {
            "type": "market",
            "reduceOnly": True,
            "tdMode": "isolated",
            "clOrdId": "persisted_single_flight_identity",
            "maximum_submissions": 1,
        },
        "secrets_or_headers_logged": False,
    }
    artifact_contract = {
        "root": "artifacts/okx_demo_fill_restart_validation",
        "package_directory": package_id,
        "formal_run_id": formal_run_id,
        "non_overwriting": True,
        "formal_execution_marker": "formal_run/FORMAL_EXECUTION_ARMED.json",
        "formal_execution_marker_created_by_package_freeze": False,
        "maximum_formal_execution_markers": 1,
        "state_snapshot": "formal_run/state/formal_state.json",
        "state_journal": "formal_run/state/formal_state_journal.jsonl",
        "append_only_streams": [
            "market", "order", "trade", "fill", "position", "balance", "fee",
            "accounting", "defensive", "safety", "supervisor",
        ],
        "checkpoint_outputs": [
            "R1_handoff", "R1_resume", "R1_generation", "R1_reconciliation",
            "R2_handoff", "R2_resume", "R2_generation", "R2_reconciliation",
        ],
        "raw_completion_before_reporting": "formal_run/RAW_COMPLETED.json",
        "completion_hashes": "completion_hashes.json",
        "completed_json_written_last_only_after_formal_close": "COMPLETED.json",
        "report_recovery_never_creates_another_marker": True,
    }
    fixture_manifest = {
        **{
            key: {"test": value, "layer": "formal_orchestration"}
            for key, value in FORMAL_FIXTURES.items()
        },
        "predecessor_offline_fixture_manifest_sha256": _sha256(
            root / ARTIFACT_ROOT / str(offline_audit["offline_run_id"])
            / "fixtures" / "fixture_manifest.json"
        ),
    }
    base = {
        "schema_version": 1,
        "protocol_id": FORMAL_PROTOCOL_ID,
        "phase": "FORMAL_PACKAGE_FREEZE_OFFLINE",
        "package_id": package_id,
        "formal_run_id": formal_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predecessor_hash_audit_sha256": canonical_sha256(predecessor_audit),
        "offline_evidence_audit": offline_audit,
        "preflight_evidence_audit": preflight_audit,
        "profile_binding": profile.binding_payload,
        "profile_binding_sha256": profile.binding_sha256,
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": runtime_configuration_sha256,
        "source_hashes": hashes,
        "source_manifest_sha256": source_manifest_sha256,
        "market_spec": preflight_audit["market_spec"],
        "ccxt_contract": ccxt_contract,
        "endpoint_contract": endpoint_contract,
        "risk_budget": risk_budget,
        "restart_contract": restart_contract,
        "fixture_manifest": fixture_manifest,
        "artifact_contract": artifact_contract,
        "conduct_contract": {
            "organic_fills_only": True,
            "artificial_fill_seeking_prices": False,
            "self_trade_or_second_account": False,
            "profile_change_to_obtain_fill": False,
            "profitability_is_gate": False,
            "no_fill_outcome": "OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT",
        },
        "package_boundary": {
            "network_attempts": 0,
            "preflight_executed_in_this_task": False,
            "orders_submitted": 0,
            "orders_cancelled": 0,
            "formal_execution_armed": False,
            "formal_arm_token_issued": False,
            "execution_marker_created": False,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        },
    }
    package_specification_sha256 = canonical_sha256(base)
    formal = FormalRunSpecification(
        formal_run_id=formal_run_id,
        package_id=package_id,
        package_specification_sha256=package_specification_sha256,
        source_manifest_sha256=source_manifest_sha256,
        runtime_configuration_sha256=runtime_configuration_sha256,
        profile_binding_sha256=profile.binding_sha256,
        market_fingerprint=market_fingerprint,
        offline_completion_sha256=str(offline_audit["completion_hashes_sha256"]),
        preflight_completion_sha256=str(preflight_audit["completion_hashes_sha256"]),
        preflight_decision_sha256=str(preflight_audit["decision_sha256"]),
        ccxt_source_sha256=str(ccxt_contract["okx_source_sha256"]),
    )
    formal.validate()
    spec = dict(base)
    spec["package_specification_sha256"] = package_specification_sha256
    spec["package_hash_contract"] = (
        "SHA-256 of canonical package payload before adding "
        "package_specification_sha256 and package_hash_contract"
    )
    spec["formal_controller_specification"] = formal.to_dict()
    return spec, formal


def _run_suite(
    *, root: Path, output: Path, name: str, arguments: Iterable[str]
) -> dict[str, object]:
    tests = output / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    audit_path = tests / f"{name}_network_audit.json"
    base_temp = root / ".okx_fill_restart_tmp" / output.name / name
    base_temp.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "pytest", *arguments, "-q",
        "-p", "no:cacheprovider", "-p", "okx_offline_network_guard",
        "--basetemp", str(base_temp.resolve()),
    ]
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    ):
        environment[variable] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        completed = subprocess.run(
            command, cwd=root, env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=300, check=False,
        )
    finally:
        shutil.rmtree(base_temp, ignore_errors=True)
    _write_text(tests / f"{name}.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1, "live_endpoint_attempts": -1,
        "optuna_imported": True,
    }
    result = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": int(audit.get("network_attempts", -1)),
        "live_endpoint_attempts": int(audit.get("live_endpoint_attempts", -1)),
        "optuna_imported": bool(audit.get("optuna_imported", True)),
        "command_scope": list(arguments),
    }
    result["passed_gate"] = all((
        result["returncode"] == 0, result["passed"] > 0,
        result["network_attempts"] == 0,
        result["live_endpoint_attempts"] == 0,
        result["optuna_imported"] is False,
    ))
    return result


def run_formal_offline_suites(root: Path, output: Path) -> dict[str, object]:
    suites = {
        "formal_targeted": _run_suite(
            root=root, output=output, name="formal_targeted",
            arguments=(
                "tests/test_okx_fill_restart_formal.py",
                "tests/test_okx_fill_restart_gateway.py",
                "tests/test_okx_fill_restart_executor.py",
                "tests/test_okx_fill_restart_formal_prepare.py",
            ),
        ),
        "root_non_optuna": _run_suite(
            root=root, output=output, name="root_non_optuna", arguments=("tests",),
        ),
        "backtest_non_optuna": _run_suite(
            root=root, output=output, name="backtest_non_optuna",
            arguments=(
                "backtest/tests",
                "--ignore=backtest/tests/test_units_and_safety.py",
                "--ignore=backtest/tests/test_robust_gates.py",
            ),
        ),
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise FormalPackageError("one or more socket-denied formal regression suites failed")
    return suites


def _artifact_secret_scan(output: Path) -> dict[str, object]:
    patterns = (
        re.compile(rb"(?i)OKX_(?:API_KEY|SECRET|PASSPHRASE)\s*[=:]\s*[^\s\"']+"),
        re.compile(rb"(?i)\b(?:apiKey|secret|password)\s*[=:]\s*[\"'][^\"']{8,}"),
    )
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix == ".tmp":
            continue
        scanned += 1
        raw = path.read_bytes()
        if any(pattern.search(raw) for pattern in patterns):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_pattern_matches": sorted(set(matches)),
        "credential_environment_accessed": False,
        "credentials_serialized": False,
    }


def _formal_completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "FORMAL_PACKAGE_COMPLETED.json", "COMPLETED.json"}
    result: dict[str, str] = {}
    for path in output.rglob("*"):
        if (
            not path.is_file() or path.is_symlink()
            or path.name in ignored or path.suffix == ".tmp"
        ):
            continue
        result[path.relative_to(output).as_posix()] = _sha256(path)
    return dict(sorted(result.items()))


def _freeze_formal_package(
    *,
    root: Path,
    package_id: str,
    formal_run_id: str,
    offline_run_id: str = DEFAULT_OFFLINE_RUN_ID,
    preflight_run_id: str = DEFAULT_PREFLIGHT_RUN_ID,
) -> Path:
    root = root.resolve()
    output = root / ARTIFACT_ROOT / package_id
    if output.exists():
        raise FormalPackageError("formal package ID reuse refused")
    for manifest_path in (root / ARTIFACT_ROOT).glob(
        "formal-package-*/run/formal_run_manifest.json"
    ):
        if _json(manifest_path).get("formal_run_id") == formal_run_id:
            raise FormalPackageError("formal run ID reuse refused")
    existing_markers = list((root / ARTIFACT_ROOT).glob(
        "formal-package-*/formal_run/FORMAL_EXECUTION_ARMED.json"
    ))
    if existing_markers:
        raise FormalPackageError("a successor formal execution marker already exists")
    output.mkdir(parents=True)

    predecessor = verify_predecessors(root)
    offline = verify_offline_evidence(root, offline_run_id)
    if offline.get("passed") is not True:
        raise FormalPackageError("offline evidence is not passed")
    preflight = verify_preflight_evidence(root, preflight_run_id)
    hashes = formal_source_hashes(root)
    spec, controller_spec = build_formal_package_spec(
        root=root,
        package_id=package_id,
        formal_run_id=formal_run_id,
        predecessor_audit=predecessor,
        offline_audit=offline,
        preflight_audit=preflight,
        hashes=hashes,
    )
    _write_json(output / "predecessor" / "hash_audit.json", predecessor)
    _write_json(output / "predecessor" / "offline_evidence_audit.json", offline)
    _write_json(output / "predecessor" / "preflight_evidence_audit.json", preflight)
    _write_json(output / "specification" / "formal_package_spec.json", spec)
    _write_json(
        output / "specification" / "formal_controller_spec.json",
        controller_spec.to_dict(),
    )
    _write_json(output / "specification" / "source_hashes.json", hashes)
    _write_json(
        output / "specification" / "promotion_manifest.json",
        {
            "profile_binding": spec["profile_binding"],
            "profile_binding_sha256": spec["profile_binding_sha256"],
            "v16_specification_sha256": controller_spec.v16_specification_sha256,
            "prior_demo_specification_sha256": (
                controller_spec.prior_demo_specification_sha256
            ),
            "runtime_configuration": spec["runtime_configuration"],
            "runtime_configuration_sha256": spec["runtime_configuration_sha256"],
            "runtime_source_hashes": hashes,
            "source_manifest_sha256": spec["source_manifest_sha256"],
            "package_specification_sha256": spec["package_specification_sha256"],
        },
    )
    for name in (
        "runtime_configuration", "ccxt_contract", "endpoint_contract",
        "risk_budget", "restart_contract", "fixture_manifest", "artifact_contract",
    ):
        _write_json(output / "specification" / f"{name}.json", spec[name])
    _write_json(output / "run" / "formal_run_manifest.json", {
        "formal_run_id": formal_run_id,
        "package_id": package_id,
        "package_specification_sha256": spec["package_specification_sha256"],
        "execution_state": "NOT_ARMED",
        "formal_execution_authorized": False,
        "formal_arm_token_issued": False,
        "formal_execution_marker_count": 0,
        "network_attempts": 0,
        "orders_submitted": 0,
        "orders_cancelled": 0,
        "process_generation": 0,
        "R1_completed": False,
        "R2_completed": False,
        "reserved_closed_status": None,
    })
    _write_json(output / "orchestration" / "controller_contract.json", {
        "controller": "okx_fill_restart_formal.FormalOrchestrator",
        "transport_free": True,
        "initial_stage": "NOT_ARMED",
        "stage_values": [item.value for item in __import__(
            "okx_fill_restart_formal", fromlist=["FormalStage"]
        ).FormalStage],
        "write_ahead_before_create": True,
        "ambiguous_create_retry_allowed": False,
        "authoritative_cancel_before_replace_or_restart": True,
        "normal_orders_post_only": True,
        "emergency_flatten_reduce_only_single_flight": True,
        "unknown_state_new_submissions": 0,
        "external_actions_executed_during_freeze": 0,
    })
    _write_json(output / "readiness" / "initial_readiness.json", {
        "predecessor_hashes_passed": predecessor["passed"],
        "offline_evidence_passed": offline["passed"],
        "read_only_preflight_evidence_passed": preflight["passed"],
        "formal_package_sources_present": True,
        "formal_execution_armed": False,
        "network_allowed": False,
        "orders_allowed": False,
    })

    tests = run_formal_offline_suites(root, output)
    post_test_hashes = formal_source_hashes(root)
    if post_test_hashes != hashes:
        raise FormalPackageError("formal source hashes changed during package freeze")
    total_tests = sum(int(value["passed"]) for value in tests.values())
    _write_json(output / "audits" / "endpoint_audit.json", {
        "package_freeze_mode": "OFFLINE_FIXTURE",
        "network_attempts": 0,
        "preflight_attempts": 0,
        "order_create_attempts": 0,
        "order_cancel_attempts": 0,
        "account_setter_attempts": 0,
        "live_endpoint_attempts": 0,
        "installed_endpoint_contract_inspected_locally": True,
    })
    _write_json(output / "audits" / "source_hash_audit.json", {
        "passed": True,
        "files_checked": len(hashes),
        "source_manifest_sha256": canonical_sha256(hashes),
        "post_test_source_manifest_sha256": canonical_sha256(post_test_hashes),
    })
    _write_json(output / "audits" / "test_count_audit.json", {
        "passed": True,
        "suite_count": len(tests),
        "total_passed_count_including_overlapping_scopes": total_tests,
        "all_network_attempts": 0,
        "all_live_endpoint_attempts": 0,
        "optuna_imported": False,
    })
    secret_scan = _artifact_secret_scan(output)
    _write_json(output / "audits" / "secret_scan.json", secret_scan)
    if not secret_scan["passed"]:
        raise FormalPackageError("secret pattern found in formal package")
    readiness = {
        "phase_status": FORMAL_PACKAGE_STATUS,
        "formal_package_frozen": True,
        "formal_execution_authorized": False,
        "formal_execution_armed": False,
        "execution_marker_created": False,
        "offline_tests_passed": True,
        "network_attempts": 0,
        "orders_submitted": 0,
        "preflight_executed_in_this_task": False,
        "prior_read_only_preflight_referenced": preflight_run_id,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": (
            "separate explicit formal OKX Demo execution instruction and exact "
            "session arm token; package freeze alone authorizes no network or order"
        ),
    }
    _write_json(output / "readiness" / "final_readiness.json", readiness)
    _write_json(output / "decision" / "formal_package_decision.json", readiness)
    _write_text(
        output / "decision" / "formal_package_decision.md",
        "# Formal Demo package freeze\n\n"
        f"- Status: `{FORMAL_PACKAGE_STATUS}`\n"
        f"- Package: `{package_id}`\n"
        f"- Reserved formal run: `{formal_run_id}`\n"
        "- Network attempts in this task: `0`\n"
        "- Orders submitted: `0`\n"
        "- Formal execution armed: `false`\n"
        "- Live mode available: `false`\n\n"
        "A separate explicit formal Demo instruction and exact session arm token "
        "are required before any future external action.\n",
    )
    completion = _formal_completion_hashes(output)
    _write_json(output / "completion_hashes.json", completion)
    for relative, expected in completion.items():
        if _sha256(output / relative) != expected:
            raise FormalPackageError("formal package completion verification failed")
    _write_json(output / "FORMAL_PACKAGE_COMPLETED.json", {
        **readiness,
        "package_id": package_id,
        "formal_run_id": formal_run_id,
        "package_specification_sha256": spec["package_specification_sha256"],
        "source_manifest_sha256": spec["source_manifest_sha256"],
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_json_reserved_for_formal_close": True,
    })
    return output


def freeze_formal_package(
    *,
    root: Path,
    package_id: str,
    formal_run_id: str,
    offline_run_id: str = DEFAULT_OFFLINE_RUN_ID,
    preflight_run_id: str = DEFAULT_PREFLIGHT_RUN_ID,
) -> Path:
    with _OfflineSocketGuard() as guard:
        output = _freeze_formal_package(
            root=root,
            package_id=package_id,
            formal_run_id=formal_run_id,
            offline_run_id=offline_run_id,
            preflight_run_id=preflight_run_id,
        )
    if guard.attempts:
        raise FormalPackageError("network attempt was blocked during formal package freeze")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--offline-run-id", default=DEFAULT_OFFLINE_RUN_ID)
    parser.add_argument("--preflight-run-id", default=DEFAULT_PREFLIGHT_RUN_ID)
    args = parser.parse_args()
    try:
        output = freeze_formal_package(
            root=Path(__file__).resolve().parent,
            package_id=args.package_id,
            formal_run_id=args.formal_run_id,
            offline_run_id=args.offline_run_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"formal package freeze failed closed: {type(exc).__name__}: {exc}")
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
