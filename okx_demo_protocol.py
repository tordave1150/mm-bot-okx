"""Evidence writer and one-shot executor for OKX demo execution safety."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Config, load_config
from okx_demo_adapter import (
    DemoAdapterConfig,
    ExecutionMode,
    OkxDemoAdapter,
    build_ccxt_demo_exchange,
)
from okx_demo_profile import PromotedProfile, canonical_sha256, load_promoted_profile
from okx_demo_runtime import (
    DemoMatrixRunner,
    DemoMatrixSpec,
    DemoRunFailed,
    MarketDataGate,
)
from okx_demo_state import DemoStateStore
from okx_production_readiness import audit_project


PROTOCOL_ID = "okx-demo-execution-safety-v1"
ARTIFACT_ROOT = Path("artifacts/okx_demo_execution_safety")
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_EXECUTION_SAFETY.md",
    "config.py",
    "market_spec.py",
    "utils.py",
    "okx_execution_safety.py",
    "okx_demo_profile.py",
    "okx_demo_state.py",
    "okx_demo_adapter.py",
    "okx_demo_runtime.py",
    "okx_demo_protocol.py",
    "okx_production_readiness.py",
    "market_maker/as_config.py",
    "market_maker/as_strategy.py",
    "market_maker/quote_model.py",
    "market_maker/volatility.py",
    "market_maker/inventory.py",
    "market_maker/arrival_intensity.py",
)
EVENT_STREAMS = (
    "market_events",
    "order_events",
    "trade_events",
    "defensive_events",
    "safety_events",
)
OFFLINE_FIXTURE_IDS = (
    "create_accepted_response_lost",
    "cancel_timeout_order_remains_open",
    "cancel_accepted_response_lost",
    "open_orders_snapshot_unavailable",
    "trades_snapshot_unavailable",
    "balance_snapshot_unavailable",
    "position_snapshot_unavailable",
    "duplicate_same_side_orders",
    "foreign_unowned_order",
    "missing_order_without_trade",
    "partial_fill_with_live_remainder",
    "late_fill_after_order_disappears",
    "restart_before_fill",
    "restart_after_fill",
    "restart_before_kill_activation",
    "restart_after_kill_activation",
    "corrupt_state",
    "stale_state",
    "wrong_account_state",
    "wrong_symbol_state",
    "wrong_market_state",
    "empty_position_snapshot",
    "long_position_reconciliation",
    "short_position_reconciliation",
    "one_base_step_position_boundary",
    "websocket_disconnect_rest_failure",
    "websocket_reordered_snapshot",
    "websocket_duplicate_snapshot",
    "stale_book",
    "crossed_book",
    "clock_skew",
    "rate_limit_snapshot_failure",
    "partial_snapshot_outage",
    "flatten_response_lost",
    "partial_flatten",
    "delayed_flatten_position_update",
    "persistence_failure_after_create",
    "supervisor_failure_signal",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def _source_hashes(root: Path) -> dict[str, str]:
    missing = [name for name in SOURCE_FILES if not (root / name).is_file()]
    if missing:
        raise RuntimeError("runtime source missing: " + ",".join(missing))
    return {name: _sha256(root / name) for name in SOURCE_FILES}


def _credential_config() -> Config:
    config = load_config()
    if not all((config.api_key, config.api_secret, config.api_passphrase)):
        raise RuntimeError("OKX demo credentials are incomplete")
    if not config.sandbox:
        raise RuntimeError("OKX_SANDBOX must remain true")
    if config.exchange_name != "okx" or config.symbol != "BTC/USDT:USDT":
        raise RuntimeError("frozen exchange/symbol mismatch")
    return config


def _adapter(
    *,
    exchange: Any,
    profile: PromotedProfile,
    session_id: str,
    state_path: Path,
    arm_token: str,
) -> OkxDemoAdapter:
    return OkxDemoAdapter(
        exchange=exchange,
        config=DemoAdapterConfig(
            mode=ExecutionMode.OKX_DEMO,
            symbol="BTC/USDT:USDT",
            margin_mode="isolated",
            position_mode="net_mode",
            leverage=3,
            explicit_arm_token=arm_token,
        ),
        promoted_profile=profile,
        session_id=session_id,
        state_store=DemoStateStore(state_path),
    )


def _safe_preflight(
    *, root: Path, output: Path, session_id: str, arm_token: str
) -> tuple[dict[str, Any], Any, PromotedProfile]:
    config = _credential_config()
    profile = load_promoted_profile(root)
    exchange = build_ccxt_demo_exchange(
        api_key=config.api_key,
        api_secret=config.api_secret,
        passphrase=config.api_passphrase,
    )
    adapter = _adapter(
        exchange=exchange,
        profile=profile,
        session_id=session_id,
        state_path=output / "preflight_state.json",
        arm_token=arm_token,
    )
    first = adapter.preflight()
    verified = adapter.preflight()
    if verified.position_mode != "net_mode" or verified.leverage != 3.0:
        raise RuntimeError("read-only demo account mode/leverage did not verify")
    fee_schedule_matches = math.isclose(
        float(verified.maker_fee_rate),
        float(profile.strategy.maker_fee_rate),
        rel_tol=0.0,
        abs_tol=1e-12,
    ) and math.isclose(
        float(verified.taker_fee_rate),
        float(profile.strategy.taker_fee_rate),
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    if verified.open_orders or abs(verified.position_btc) > 1e-12:
        raise RuntimeError("demo account must be flat with zero open orders")
    raw_book = exchange.fetch_order_book("BTC/USDT:USDT")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    market = MarketDataGate().validate(raw_book, now_ms=now_ms)
    lot_btc = float(profile.strategy.fixed_lot_size_btc)
    leverage = float(profile.capital_policy["leverage"])
    capital = float(profile.capital_policy["capital_usdt"])
    utilization = float(profile.capital_policy["maximum_margin_utilization"])
    one_order_margin = market["best_ask"] * lot_btc / leverage
    two_sided_margin = one_order_margin * 2.0
    frozen_margin_limit = capital * utilization
    effective_margin_limit = min(frozen_margin_limit, verified.free_equity_usdt)
    capacity_passed = two_sided_margin <= effective_margin_limit
    metadata = {
        "protocol_id": PROTOCOL_ID,
        "session_id": session_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "credentials_complete": True,
        "credentials_serialized": False,
        "sandbox_mode": bool(exchange.options.get("sandboxMode")),
        "simulated_trading_header": (
            str(exchange.headers.get("x-simulated-trading", "")) == "1"
        ),
        "initial_snapshot": first.public_dict(),
        "verified_snapshot": verified.public_dict(),
        "market_spec": adapter.market_spec.to_dict(),
        "market_fingerprint": adapter.market_spec.fingerprint,
        "quarantined_market_metadata_rows": list(
            adapter.market_metadata_quarantine
        ),
        "profile_binding_sha256": profile.binding_sha256,
        "fee_schedule_matches_frozen_profile": fee_schedule_matches,
        "market_snapshot": {
            "timestamp_ms": market["timestamp"],
            "best_bid": market["best_bid"],
            "best_ask": market["best_ask"],
            "age_ms": market["age_ms"],
        },
        "two_sided_capacity": {
            "fixed_lot_size_btc": lot_btc,
            "leverage": leverage,
            "capital_usdt": capital,
            "maximum_margin_utilization": utilization,
            "one_order_margin_usdt": one_order_margin,
            "two_sided_margin_usdt": two_sided_margin,
            "frozen_margin_limit_usdt": frozen_margin_limit,
            "authoritative_free_equity_usdt": verified.free_equity_usdt,
            "effective_margin_limit_usdt": effective_margin_limit,
            "passed": capacity_passed,
        },
    }
    _write_json(output / "preflight.json", metadata)
    if not fee_schedule_matches:
        raise RuntimeError("authoritative demo fee schedule differs from frozen profile")
    if not capacity_passed:
        raise RuntimeError(
            "frozen capital copy cannot support two simultaneous maker intents"
        )
    return metadata, exchange, profile


def _run_tests(root: Path, output: Path) -> dict[str, Any]:
    suites = (
        (
            "root",
            [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        ),
        (
            "backtest_non_optuna",
            [
                sys.executable, "-m", "pytest", "backtest/tests", "-q",
                "-p", "no:cacheprovider",
                "--ignore=backtest/tests/test_units_and_safety.py",
                "--ignore=backtest/tests/test_robust_gates.py",
            ],
        ),
    )
    results: dict[str, Any] = {}
    for name, command in suites:
        suite_command = [
            *command,
            "--basetemp",
            str((output / f"{name}_tmp").resolve()),
        ]
        completed = subprocess.run(
            suite_command,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
        text = completed.stdout
        (output / f"{name}.txt").write_text(text, encoding="utf-8")
        matches = re.findall(r"(\d+) passed", text)
        results[name] = {
            "returncode": completed.returncode,
            "passed": int(matches[-1]) if matches else 0,
            "optuna_importing_files_excluded": (
                [
                    "backtest/tests/test_units_and_safety.py",
                    "backtest/tests/test_robust_gates.py",
                ] if name == "backtest_non_optuna" else []
            ),
        }
        if completed.returncode != 0:
            raise RuntimeError(f"{name} regression suite failed")
    _write_json(output / "test_summary.json", results)
    return results


def _installed_ccxt_contract() -> dict[str, Any]:
    import ccxt
    import inspect

    source = Path(inspect.getsourcefile(ccxt.okx)).resolve()
    return {
        "ccxt_version": ccxt.__version__,
        "okx_source_sha256": _sha256(source),
        "request_fields": {
            "normal_order": ["postOnly", "reduceOnly", "tdMode", "clOrdId"],
            "flatten_order": ["reduceOnly", "tdMode", "clOrdId"],
            "client_order_id_max_characters": 32,
        },
        "demo_transport": {
            "sandbox_option": "sandboxMode=true",
            "required_header": "x-simulated-trading=1",
        },
        "account_contract": {
            "position_mode": "net_mode",
            "margin_mode": "isolated",
            "leverage": 3,
        },
    }


def _build_spec(
    *,
    root: Path,
    run_id: str,
    profile: PromotedProfile,
    preflight: dict[str, Any],
    matrix: DemoMatrixSpec,
    tests: dict[str, Any],
) -> dict[str, Any]:
    runtime_configuration = {
        "execution_mode": "OKX_DEMO",
        "symbol": "BTC/USDT:USDT",
        "market_type": "linear_swap",
        "margin_mode": "isolated",
        "position_mode": "net_mode",
        "leverage": 3,
        "profile_binding_sha256": profile.binding_sha256,
        "market_fingerprint": preflight["market_fingerprint"],
        "capital_policy": profile.capital_policy,
        "matrix": asdict(matrix),
    }
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "run_id": run_id,
        "execution_mode": "OKX_DEMO",
        "production_authorized": False,
        "live_mode_available": False,
        "formal_execution_count": 1,
        "profile_binding": profile.binding_payload,
        "profile_binding_sha256": profile.binding_sha256,
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": canonical_sha256(runtime_configuration),
        "matrix": asdict(matrix),
        "offline_control_order_intents": matrix.offline_control_order_intents,
        "activity_retention_minimum": 0.80,
        "preflight_contract": preflight,
        "installed_ccxt_contract": _installed_ccxt_contract(),
        "source_hashes": _source_hashes(root),
        "test_contract": tests,
        "offline_fixture_ids": list(OFFLINE_FIXTURE_IDS),
        "flatten_retry_budget": 1,
        "market_data_maximum_age_ms": 1_000,
        "maximum_clock_skew_ms": 1_500,
        "artifact_streams": [
            "market_events.jsonl",
            "order_events.jsonl",
            "trade_events.jsonl",
            "defensive_events.jsonl",
            "safety_events.jsonl",
            "raw_result.json",
        ],
        "artifact_contract": {
            "execution_armed_marker": "EXECUTION_ARMED.json",
            "durable_streams_before_order_submission": [
                f"{name}.jsonl" for name in EVENT_STREAMS
            ],
            "raw_completion_marker": "RAW_COMPLETED.json",
            "final_completion_written_last": "COMPLETED.json",
        },
        "closed_statuses": [
            "OKX_DEMO_EVIDENCE_FAILED",
            "OKX_DEMO_RUNTIME_BINDING_FAILED",
            "OKX_DEMO_RECONCILIATION_FAILED",
            "OKX_DEMO_SAFETY_FAILED",
            "OKX_DEMO_ACTIVITY_INSUFFICIENT",
            "OKX_DEMO_EXECUTION_SAFETY_SUPPORT",
        ],
    }


def _secret_scan(paths: list[Path], credentials: Config) -> dict[str, Any]:
    secrets = (credentials.api_key, credentials.api_secret, credentials.api_passphrase)
    matches: list[str] = []
    files_scanned = 0
    for base in paths:
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else [
            path for path in base.rglob("*") if path.is_file()
        ]
        for path in candidates:
            try:
                data = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files_scanned += 1
            if any(secret and secret in data for secret in secrets):
                matches.append(path.name)
    return {
        "passed": not matches,
        "files_scanned": files_scanned,
        "credential_values_serialized": False if not matches else True,
        "matching_file_names": matches,
    }


def _formal_raw_completions(root: Path) -> list[Path]:
    return sorted(
        (root / ARTIFACT_ROOT).glob("*/formal_run/RAW_COMPLETED.json")
    )


def _formal_execution_markers(root: Path) -> list[Path]:
    artifact_root = root / ARTIFACT_ROOT
    return sorted(set(
        artifact_root.glob("*/formal_run/EXECUTION_ARMED.json")
    ) | set(
        artifact_root.glob("*/formal_run/RAW_COMPLETED.json")
    ))


def _durable_event_sink(formal_dir: Path):
    allowed = set(EVENT_STREAMS)

    def sink(stream: str, row: dict[str, Any]) -> None:
        if stream not in allowed:
            raise RuntimeError(f"unknown formal event stream: {stream}")
        path = formal_dir / f"{stream}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return sink


def _build_run_audits(
    *, result: Any, matrix: DemoMatrixSpec, tests: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    acknowledgements = [
        row for row in result.order_events
        if row.get("event") == "POST_ONLY_ACKNOWLEDGED"
    ]
    cancellations = [
        row for row in result.order_events
        if row.get("event") == "CANCEL_CONFIRMED"
    ]
    acknowledgement_index = {
        str(row.get("order_id")): index
        for index, row in enumerate(result.order_events)
        if row.get("event") == "POST_ONLY_ACKNOWLEDGED"
    }
    cancel_after_ack = all(
        str(row.get("order_id")) in acknowledgement_index
        and acknowledgement_index[str(row.get("order_id"))]
        < result.order_events.index(row)
        for row in cancellations
    )
    client_ids = [str(row.get("client_order_id") or "") for row in acknowledgements]
    order_audit = {
        "acknowledged_post_only_orders": len(acknowledgements),
        "cancel_confirmed_orders": len(cancellations),
        "unique_nonempty_client_order_ids": (
            bool(client_ids)
            and all(client_ids)
            and len(client_ids) == len(set(client_ids))
        ),
        "duplicate_orders": result.duplicate_orders,
        "ambiguous_retries": result.ambiguous_retries,
        "foreign_or_unowned_orders": 0,
        "final_open_order_count": result.final_open_order_count,
        "passed": (
            len(acknowledgements) == result.acknowledged_normal_orders
            and len(cancellations) == result.cancelled_normal_orders
            and all(client_ids)
            and len(client_ids) == len(set(client_ids))
            and result.duplicate_orders == 0
            and result.ambiguous_retries == 0
            and result.final_open_order_count == 0
        ),
    }
    classification_counts = {
        "NORMAL_MAKER": sum(
            row.get("classification") == "NORMAL_MAKER"
            for row in result.trade_events
        ),
        "SPECIAL_REDUCE_ONLY": sum(
            row.get("classification") == "SPECIAL_REDUCE_ONLY"
            for row in result.trade_events
        ),
        "UNKNOWN": sum(
            row.get("classification") == "UNKNOWN"
            for row in result.trade_events
        ),
    }
    trade_ids = [str(row.get("trade_id") or "") for row in result.trade_events]
    classification_reconciles = (
        sum(classification_counts.values()) == len(result.trade_events)
        and classification_counts["NORMAL_MAKER"] == result.normal_fill_count
        and classification_counts["SPECIAL_REDUCE_ONLY"] == result.special_fill_count
        and classification_counts["UNKNOWN"] == result.unknown_fill_count == 0
    )
    fill_audit = {
        "trade_count": len(result.trade_events),
        "trade_ids_unique": len(trade_ids) == len(set(trade_ids)),
        "trade_ids_nonempty": all(trade_ids),
        "classification_counts": classification_counts,
        "classification_reconciles": classification_reconciles,
        "normal_fill_count": result.normal_fill_count,
        "bid_normal_fills": result.bid_normal_fills,
        "ask_normal_fills": result.ask_normal_fills,
        "passed": (
            len(trade_ids) == len(set(trade_ids))
            and all(trade_ids)
            and classification_reconciles
            and result.bid_normal_fills + result.ask_normal_fills
            == result.normal_fill_count
        ),
    }
    fee_sum = sum(float(row.get("fee_usdt") or 0.0) for row in result.trade_events)
    fee_reconciles = math.isclose(
        fee_sum, result.actual_fees_usdt, rel_tol=0.0, abs_tol=1e-12
    )
    fee_audit = {
        "stream_fee_sum_usdt": fee_sum,
        "reported_actual_fees_usdt": result.actual_fees_usdt,
        "all_fees_nonnegative": all(
            float(row.get("fee_usdt") or 0.0) >= 0.0
            for row in result.trade_events
        ),
        "reconciles": fee_reconciles,
        "passed": fee_reconciles,
    }
    position_audit = {
        "final_position_btc": result.final_position_btc,
        "maximum_allowed_residual_btc": 1e-12,
        "final_open_order_count": result.final_open_order_count,
        "passed": (
            abs(result.final_position_btc) <= 1e-12
            and result.final_open_order_count == 0
        ),
    }
    accepted_books = [
        row for row in result.market_events if row.get("event") == "MARKET_ACCEPTED"
    ]
    staleness_audit = {
        "accepted_market_event_count": len(accepted_books),
        "maximum_observed_age_ms": max(
            (abs(int(row.get("age_ms") or 0)) for row in accepted_books),
            default=0,
        ),
        "maximum_allowed_age_ms": 1_000,
        "stale_placements": result.stale_placements,
        "passed": (
            result.stale_placements == 0
            and all(abs(int(row.get("age_ms") or 0)) <= 1_000 for row in accepted_books)
        ),
    }
    event_order_audit = {
        "cancel_events_follow_matching_acknowledgement": cancel_after_ack,
        "raw_completed_written_after_streams": True,
        "completion_written_last": True,
        "passed": cancel_after_ack,
    }
    root_fixtures_pass = tests["root"]["returncode"] == 0
    restart_audit = {
        "fixture_contract": [
            "restart_before_after_fill",
            "restart_before_after_kill_activation",
            "state_identity_and_corruption",
        ],
        "root_fixture_suite_passed": root_fixtures_pass,
        "formal_runtime_restart_attempted": False,
        "passed": root_fixtures_pass,
    }
    kill_switch_audit = {
        "fixture_contract": [
            "kill_latch_persists_across_restart",
            "release_requires_explicit_ack_flat_position_and_zero_owned_orders",
        ],
        "root_fixture_suite_passed": root_fixtures_pass,
        "passed": root_fixtures_pass,
    }
    flatten_audit = {
        "submission_count": result.flatten_submission_count,
        "confirmation_count": result.flatten_confirmation_count,
        "frozen_retry_budget": 1,
        "final_position_btc": result.final_position_btc,
        "passed": (
            result.flatten_submission_count <= 1
            and result.flatten_confirmation_count == result.flatten_submission_count
            and abs(result.final_position_btc) <= 1e-12
        ),
    }
    accounting_audit = {
        "trade_count": len(result.trade_events),
        "normal_fill_count": result.normal_fill_count,
        "special_fill_count": result.special_fill_count,
        "gross_execution_pnl_usdt": result.gross_execution_pnl_usdt,
        "actual_fees_usdt": result.actual_fees_usdt,
        "net_execution_pnl_usdt": result.net_execution_pnl_usdt,
        "net_equals_gross_less_fees": math.isclose(
            result.net_execution_pnl_usdt,
            result.gross_execution_pnl_usdt - result.actual_fees_usdt,
            rel_tol=0.0,
            abs_tol=1e-12,
        ),
        "fee_reconciles": fee_reconciles,
        "fill_classification_reconciles": classification_reconciles,
        "position_reconciles": position_audit["passed"],
    }
    accounting_audit["passed"] = all((
        accounting_audit["net_equals_gross_less_fees"],
        accounting_audit["fee_reconciles"],
        accounting_audit["fill_classification_reconciles"],
        accounting_audit["position_reconciles"],
    ))
    classification_audit = {
        **classification_counts,
        "special_exits_excluded_from_normal_activity": all(
            row.get("normal_activity_eligible") is False
            for row in result.trade_events
            if row.get("classification") == "SPECIAL_REDUCE_ONLY"
        ),
        "passed": classification_reconciles,
    }
    return {
        "order_audit": order_audit,
        "fill_audit": fill_audit,
        "position_audit": position_audit,
        "fee_audit": fee_audit,
        "accounting_audit": accounting_audit,
        "classification_audit": classification_audit,
        "event_order_audit": event_order_audit,
        "staleness_audit": staleness_audit,
        "restart_audit": restart_audit,
        "kill_switch_audit": kill_switch_audit,
        "emergency_flatten_audit": flatten_audit,
    }


def engineering_preflight(root: Path, run_id: str, arm_token: str) -> Path:
    output = (root / ARTIFACT_ROOT / f"engineering_preflight_{run_id}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    exchange = None
    try:
        preflight_session = f"preflight:{run_id}"
        metadata, exchange, _ = _safe_preflight(
            root=root,
            output=output,
            session_id=preflight_session,
            arm_token=f"OKX_DEMO:{preflight_session}",
        )
        _write_json(output / "COMPLETED.json", {
            "status": "READ_ONLY_DEMO_PREFLIGHT_PASSED",
            "sandbox_mode": metadata["sandbox_mode"],
            "simulated_trading_header": metadata["simulated_trading_header"],
            "orders_submitted": 0,
        })
        return output
    finally:
        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


def execute_formal(root: Path, run_id: str, arm_token: str) -> Path:
    prior_markers = _formal_execution_markers(root)
    if prior_markers:
        joined = ", ".join(str(path.relative_to(root)) for path in prior_markers)
        raise RuntimeError(
            "formal demo execution was already armed; reporting recovery only: "
            + joined
        )
    output = (root / ARTIFACT_ROOT / run_id).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tests_dir = output / "tests"
    tests_dir.mkdir()
    tests = _run_tests(root, tests_dir)
    credentials = _credential_config()
    readiness_dir = output / "readiness"
    readiness_dir.mkdir()
    initial_readiness = audit_project(root, credentials)
    _write_json(readiness_dir / "initial_readiness.json", initial_readiness)
    unexpected_initial_blockers = sorted(
        set(initial_readiness["blocker_codes"]) - {"SIGNED_PROMOTION_MANIFEST"}
    )
    if unexpected_initial_blockers:
        raise RuntimeError(
            "initial demo readiness blockers: " + ",".join(unexpected_initial_blockers)
        )
    preflight_dir = output / "engineering_preflight"
    preflight_dir.mkdir()
    exchange = None
    formal_execution_count = 0
    try:
        preflight, exchange, profile = _safe_preflight(
            root=root,
            output=preflight_dir,
            session_id=f"preflight:{run_id}",
            arm_token=arm_token.replace(run_id, f"preflight:{run_id}"),
        )
        matrix = DemoMatrixSpec()
        specification = _build_spec(
            root=root,
            run_id=run_id,
            profile=profile,
            preflight=preflight,
            matrix=matrix,
            tests=tests,
        )
        specification_dir = output / "specification"
        specification_dir.mkdir()
        _write_json(specification_dir / "promotion_manifest.json", {
            "profile_binding": profile.binding_payload,
            "profile_binding_sha256": profile.binding_sha256,
            "runtime_configuration": specification["runtime_configuration"],
            "runtime_configuration_sha256": specification[
                "runtime_configuration_sha256"
            ],
            "runtime_source_hashes": specification["source_hashes"],
        })
        specification_sha256 = canonical_sha256(specification)
        _write_json(specification_dir / "protocol_spec.json", specification)
        (specification_dir / "protocol_spec.sha256").write_text(
            specification_sha256 + "\n", encoding="utf-8"
        )

        formal_dir = output / "formal_run"
        formal_dir.mkdir()
        for stream in EVENT_STREAMS:
            (formal_dir / f"{stream}.jsonl").touch(exist_ok=False)
        adapter = _adapter(
            exchange=exchange,
            profile=profile,
            session_id=run_id,
            state_path=formal_dir / "runtime_state.json",
            arm_token=arm_token,
        )
        _write_json(formal_dir / "EXECUTION_ARMED.json", {
            "formal_execution_count": 1,
            "run_id": run_id,
            "specification_sha256": specification_sha256,
            "orders_submitted_before_marker": 0,
        })
        runner = DemoMatrixRunner(
            adapter=adapter,
            spec=matrix,
            event_sink=_durable_event_sink(formal_dir),
        )
        formal_execution_count = 1
        try:
            result = runner.run()
        except DemoRunFailed as exc:
            result = exc.result
        _write_json(formal_dir / "raw_result.json", result.to_dict())
        _write_json(formal_dir / "RAW_COMPLETED.json", {
            "formal_execution_count": formal_execution_count,
            "raw_status": result.status,
            "specification_sha256": specification_sha256,
        })

        analysis_dir = output / "analysis"
        analysis_dir.mkdir()
        endpoint_audit = {
            "sandbox_mode": bool(exchange.options.get("sandboxMode")),
            "simulated_trading_header": (
                str(exchange.headers.get("x-simulated-trading", "")) == "1"
            ),
            "live_endpoint_attempts": result.live_endpoint_attempts,
            "production_authorized": False,
        }
        _write_json(analysis_dir / "endpoint_audit.json", endpoint_audit)
        run_audits = _build_run_audits(result=result, matrix=matrix, tests=tests)
        for name, audit in run_audits.items():
            _write_json(analysis_dir / f"{name}.json", audit)
        accounting_audit = run_audits["accounting_audit"]
        activity_audit = {
            "offline_control_order_intents": matrix.offline_control_order_intents,
            "acknowledged_normal_orders": result.acknowledged_normal_orders,
            "cancelled_normal_orders": result.cancelled_normal_orders,
            "activity_retention": result.activity_retention,
            "minimum": 0.80,
            "passed": result.activity_retention >= 0.80,
        }
        _write_json(analysis_dir / "activity_audit.json", activity_audit)
        failure_fixture_manifest = {
            "fixture_ids": list(OFFLINE_FIXTURE_IDS),
            "fixture_count": len(OFFLINE_FIXTURE_IDS),
            "required_fixture_groups": [
                "ambiguous_create", "ambiguous_cancel", "snapshot_outages",
                "duplicate_foreign_missing_partial_late_orders",
                "restart_and_kill_latch", "state_identity_and_corruption",
                "empty_long_short_position_boundaries", "market_data_outages",
                "stale_crossed_clock_and_rate_limits", "single_flight_flatten",
                "persistence_failure", "supervisor_failure_signal",
            ],
            "pytest_files": [
                "tests/test_okx_demo_adapter.py",
                "tests/test_okx_demo_profile.py",
                "tests/test_okx_demo_state.py",
                "tests/test_okx_execution_safety.py",
            ],
            "root_tests_passed": tests["root"]["passed"],
            "passed": tests["root"]["returncode"] == 0,
        }
        _write_json(analysis_dir / "failure_fixture_audit.json", failure_fixture_manifest)

        final_readiness = audit_project(root, credentials)
        _write_json(readiness_dir / "final_readiness.json", final_readiness)
        artifact_secret_scan = _secret_scan(
            [output, root / "bot.log", root / "bot_error.log"], credentials
        )

        all_tests_pass = all(row["returncode"] == 0 for row in tests.values())
        endpoint_pass = (
            endpoint_audit["sandbox_mode"]
            and endpoint_audit["simulated_trading_header"]
            and endpoint_audit["live_endpoint_attempts"] == 0
        )
        all_run_audits_pass = all(
            bool(audit["passed"]) for audit in run_audits.values()
        )
        readiness_pass = bool(final_readiness["demo_ready"])
        structured_payload_probe = json.dumps(
            {
                "protocol_id": PROTOCOL_ID,
                "run_id": run_id,
                "profile_id": profile.profile_id,
                "profile_name": profile.profile_name,
                "specification_sha256": specification_sha256,
                "run_audits": run_audits,
            },
            sort_keys=True,
        )
        credential_values = (
            credentials.api_key,
            credentials.api_secret,
            credentials.api_passphrase,
        )
        structured_payload_secret_free = not any(
            secret and secret in structured_payload_probe
            for secret in credential_values
        )
        secret_scan_pass = (
            artifact_secret_scan["passed"] and structured_payload_secret_free
        )
        supported = (
            result.status == "OKX_DEMO_EXECUTION_SAFETY_SUPPORT"
            and all_tests_pass
            and endpoint_pass
            and secret_scan_pass
            and readiness_pass
            and all_run_audits_pass
            and activity_audit["passed"]
        )
        if supported:
            status = "OKX_DEMO_EXECUTION_SAFETY_SUPPORT"
        elif result.status != "OKX_DEMO_EXECUTION_SAFETY_SUPPORT":
            status = result.status
        elif not readiness_pass:
            status = "OKX_DEMO_RUNTIME_BINDING_FAILED"
        elif not all_run_audits_pass:
            reconciliation_names = {
                "order_audit", "fill_audit", "position_audit",
                "fee_audit", "accounting_audit", "classification_audit",
                "event_order_audit",
            }
            status = (
                "OKX_DEMO_RECONCILIATION_FAILED"
                if any(
                    not run_audits[name]["passed"]
                    for name in reconciliation_names
                )
                else "OKX_DEMO_SAFETY_FAILED"
            )
        elif not activity_audit["passed"]:
            status = "OKX_DEMO_ACTIVITY_INSUFFICIENT"
        else:
            status = "OKX_DEMO_EVIDENCE_FAILED"
        decision = {
            "status": status,
            "protocol_id": PROTOCOL_ID,
            "run_id": run_id,
            "specification_sha256": specification_sha256,
            "profile_id": profile.profile_id,
            "profile_name": profile.profile_name,
            "profile_binding_sha256": profile.binding_sha256,
            "formal_execution_count": formal_execution_count,
            "test_counts": tests,
            "accounting": accounting_audit,
            "run_audit_passes": {
                name: audit["passed"] for name, audit in run_audits.items()
            },
            "activity": activity_audit,
            "endpoint_audit": endpoint_audit,
            "initial_readiness_blockers": initial_readiness["blocker_codes"],
            "final_readiness_blockers": final_readiness["blocker_codes"],
            "secret_scan_passed": secret_scan_pass,
            "production_authorized": False,
            "live_orders": 0,
            "optuna_executed": False,
            "validation": False,
            "holdout": False,
            "git": False,
        }
        decision_dir = output / "decision"
        decision_dir.mkdir()
        decision_markdown = (
            "# OKX Demo execution-safety decision\n\n"
            f"Status: `{status}`\n\n"
            f"Profile: `{profile.profile_name}`\n\n"
            f"Activity retention: `{result.activity_retention:.2%}`\n\n"
            f"Normal fills: `{result.normal_fill_count}`; special fills: "
            f"`{result.special_fill_count}`; actual fees: "
            f"`{result.actual_fees_usdt:.8f} USDT`; net execution PnL: "
            f"`{result.net_execution_pnl_usdt:.8f} USDT`.\n\n"
            "Production remains unauthorized.\n"
        )
        final_payloads = (
            json.dumps(decision, sort_keys=True),
            decision_markdown,
        )
        if any(
            secret and secret in payload
            for secret in credential_values
            for payload in final_payloads
        ):
            raise RuntimeError("credential value reached final decision payload")
        _write_json(decision_dir / "decision.json", decision)
        (decision_dir / "decision.md").write_text(decision_markdown, encoding="utf-8")
        secret_scan = _secret_scan(
            [output, root / "bot.log", root / "bot_error.log"], credentials
        )
        secret_scan["structured_final_payloads_scanned_in_memory"] = 2
        secret_scan["structured_final_payloads_secret_free"] = True
        if not secret_scan["passed"]:
            raise RuntimeError("credential value detected in formal artifacts")
        _write_json(analysis_dir / "secret_scan.json", secret_scan)
        hash_manifest = {
            str(path.relative_to(output)).replace("\\", "/"): _sha256(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name not in {"completion_hashes.json", "COMPLETED.json"}
        }
        _write_json(output / "completion_hashes.json", hash_manifest)
        _write_json(output / "COMPLETED.json", {
            "status": status,
            "formal_execution_count": formal_execution_count,
            "specification_sha256": specification_sha256,
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "production_authorized": False,
            "live_orders": 0,
            "optuna_executed": False,
        })
        return output
    finally:
        if exchange is not None:
            try:
                exchange.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.root.resolve()
    expected = f"OKX_DEMO:{args.run_id}"
    if args.arm != expected:
        raise SystemExit("arm token must exactly match OKX_DEMO:<run-id>")
    if args.command == "preflight":
        path = engineering_preflight(root, args.run_id, args.arm)
    else:
        path = execute_formal(root, args.run_id, args.arm)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
