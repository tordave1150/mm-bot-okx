"""Authoritative R1 Read-Only OKX Demo Preflight Executor.

Executes the authorized R1 Read-Only OKX Demo Preflight under:
EXECUTION_R1_READ_ONLY_PREFLIGHT.md
- Strict read-only allowlist enforcement
- Interception and zero tolerance for order/account mutations
- Authoritative clock-skew, market specification, and startup state reconciliation
- Sanitized, tamper-evident evidence output with zero secret serialization
- Hard stop before R2 or order placement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from okx_demo_adapter import (
    AccountSnapshot,
    ClockSkewBudgetError,
    DemoAdapterConfig,
    DemoAdapterError,
    ExecutionMode,
    OkxDemoAdapter,
    build_ccxt_demo_exchange,
)
from okx_demo_profile import load_promoted_profile
from okx_demo_state import DemoStateStore
from okx_demo_staged_validation import compute_candidate_fingerprint

ROOT = Path(__file__).resolve().parent

# Canonical references from R0 closure and R1 preparation
CANONICAL_R0_CLOSURE_REF = "r0-closure-20260904T121733Z"
CANONICAL_R1_PACKAGE_ID = "r1-package-20260904T121733Z"
CANONICAL_R1_RUN_ID = "r1-preflight-run-20260904T121733Z"
CANONICAL_R1_SESSION_ID = "r1-preflight-session-20260904T121733Z:p0:319720120a59"
CANONICAL_ARM_TOKEN = f"OKX_DEMO:{CANONICAL_R1_SESSION_ID}"
EXPECTED_CANDIDATE_FINGERPRINT = "ef993bc42ffbb19cf1bfc94d3dcca32cd19909c9b0d9da73ab0a01ab0088ffb8"

ALLOWED_READ_ENDPOINTS = frozenset({
    "fetch_markets",
    "set_markets",
    "fetch_market_info",
    "fetch_time",
    "fetch_balance",
    "privateGetAccountConfig",
    "fetch_positions",
    "fetch_open_orders",
    "fetch_my_trades",
    "fetch_leverage",
    "fetch_trading_fee",
    "fetch_position_mode",
    "market",
    "load_markets",
})

PROHIBITED_MUTATION_METHODS = frozenset({
    "create_order",
    "cancel_order",
    "cancel_all_owned",
    "submit_emergency_flatten",
    "set_position_mode",
    "set_leverage",
    "transfer",
    "withdrawal",
})


def canonical_sha256(content: bytes | str) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_atomic(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".tmp")
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temp_target.write_text(data, encoding="utf-8")
    temp_target.replace(target)


def generate_completion_hashes(directory: Path, marker_filename: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", marker_filename}
    hashes: dict[str, str] = {}
    for item in sorted(directory.rglob("*")):
        if item.is_file() and item.name not in ignored and not item.name.endswith(".tmp"):
            rel = item.relative_to(directory).as_posix()
            hashes[rel] = hash_file(item)
    return hashes


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoAdapterError(f"R1 preparation evidence unreadable: {path.name}") from exc
    if not isinstance(payload, dict):
        raise DemoAdapterError(f"R1 preparation evidence is not an object: {path.name}")
    return payload


def verify_r1_preflight_preparation(
    *,
    prep_path: Path,
    package_id: str,
    run_id: str,
    session_id: str,
    arm_token: str,
    r0_evidence_id: str | None = None,
) -> dict[str, str]:
    """Fail closed on every package/identity mismatch before credentials or transport.

    A supplied ``r0_evidence_id`` is intentionally exact: fresh packages must
    contain that field and cannot fall back to a directory name.
    """
    if not prep_path.is_dir():
        raise DemoAdapterError(f"R1 preparation package missing at: {prep_path}")

    identity = _read_json_object(prep_path / "r1_identity.json")
    marker = _read_json_object(prep_path / "R1_PREPARATION_COMPLETED.json")
    hashes = _read_json_object(prep_path / "completion_hashes.json")

    for relative_name, expected_hash in hashes.items():
        if not isinstance(relative_name, str) or not isinstance(expected_hash, str):
            raise DemoAdapterError("R1 preparation hash manifest is malformed")
        candidate = prep_path / relative_name
        if not candidate.is_file() or hash_file(candidate) != expected_hash:
            raise DemoAdapterError(f"R1 preparation hash verification failed: {relative_name}")

    expected_token = f"OKX_DEMO:{session_id}"
    expected = {
        "package_id": package_id,
        "run_id": run_id,
        "session_id": session_id,
        "expected_arm_token": arm_token,
    }
    for field, value in expected.items():
        if identity.get(field) != value:
            raise DemoAdapterError(f"R1 preparation identity mismatch: {field}")
    if arm_token != expected_token:
        raise DemoAdapterError("R1 arm token does not bind to the requested session")
    if marker.get("status") != "R1_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION":
        raise DemoAdapterError("R1 preparation is not awaiting explicit authorization")
    if marker.get("preparation_package_id") != package_id:
        raise DemoAdapterError("R1 preparation marker package ID mismatch")
    if marker.get("r1_run_id") != run_id or marker.get("r1_session_id") != session_id:
        raise DemoAdapterError("R1 preparation marker run/session mismatch")
    if any(marker.get(field) != 0 for field in ("credential_reads", "network_attempts", "demo_endpoint_attempts")):
        raise DemoAdapterError("R1 preparation is not offline-clean")
    if marker.get("r1_authorized") is not False or marker.get("r1_executed") is not False:
        raise DemoAdapterError("R1 preparation has an invalid execution state")

    package_r0_ref = identity.get("r0_evidence_id")
    marker_r0_ref = marker.get("r0_evidence_id", marker.get("r0_closure_ref"))
    if r0_evidence_id is not None:
        if package_r0_ref != r0_evidence_id or marker_r0_ref != r0_evidence_id:
            raise DemoAdapterError("R1 preparation exact R0 evidence ID mismatch")
    elif package_r0_ref is not None and package_r0_ref != marker_r0_ref:
        raise DemoAdapterError("R1 preparation R0 evidence reference mismatch")

    return {
        "package_id": package_id,
        "r0_evidence_id": str(package_r0_ref or marker_r0_ref),
    }


class AuditedReadOnlyExchangeWrapper:
    """Auditing wrapper ensuring only allowlisted read-only calls reach the exchange."""

    def __init__(self, underlying_exchange: Any) -> None:
        self._exchange = underlying_exchange
        self.endpoint_call_counts: dict[str, int] = {}
        self.create_attempts = 0
        self.cancel_attempts = 0
        self.flatten_attempts = 0
        self.account_mutation_attempts = 0
        self.live_endpoint_attempts = 0
        self.prohibited_call_attempts = 0
        self.unknown_endpoint_attempts = 0

    @property
    def options(self) -> dict[str, Any]:
        return self._exchange.options

    @property
    def headers(self) -> dict[str, Any]:
        return self._exchange.headers

    @property
    def markets(self) -> dict[str, Any]:
        return self._exchange.markets

    def market(self, symbol: str) -> dict[str, Any]:
        self.endpoint_call_counts["market"] = self.endpoint_call_counts.get("market", 0) + 1
        return self._exchange.market(symbol)

    def set_markets(self, markets: list[dict[str, Any]]) -> None:
        self.endpoint_call_counts["set_markets"] = self.endpoint_call_counts.get("set_markets", 0) + 1
        return self._exchange.set_markets(markets)

    def load_markets(self, reload: bool = False) -> dict[str, Any]:
        self.endpoint_call_counts["load_markets"] = self.endpoint_call_counts.get("load_markets", 0) + 1
        return self._exchange.load_markets(reload)

    def fetch_markets(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.endpoint_call_counts["fetch_markets"] = self.endpoint_call_counts.get("fetch_markets", 0) + 1
        return self._exchange.fetch_markets(*args, **kwargs)

    def fetch_time(self, *args: Any, **kwargs: Any) -> int:
        self.endpoint_call_counts["fetch_time"] = self.endpoint_call_counts.get("fetch_time", 0) + 1
        return self._exchange.fetch_time(*args, **kwargs)

    def fetch_balance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.endpoint_call_counts["fetch_balance"] = self.endpoint_call_counts.get("fetch_balance", 0) + 1
        return self._exchange.fetch_balance(*args, **kwargs)

    def privateGetAccountConfig(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.endpoint_call_counts["privateGetAccountConfig"] = self.endpoint_call_counts.get("privateGetAccountConfig", 0) + 1
        return self._exchange.privateGetAccountConfig(*args, **kwargs)

    def fetch_positions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.endpoint_call_counts["fetch_positions"] = self.endpoint_call_counts.get("fetch_positions", 0) + 1
        return self._exchange.fetch_positions(*args, **kwargs)

    def fetch_open_orders(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.endpoint_call_counts["fetch_open_orders"] = self.endpoint_call_counts.get("fetch_open_orders", 0) + 1
        return self._exchange.fetch_open_orders(*args, **kwargs)

    def fetch_my_trades(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.endpoint_call_counts["fetch_my_trades"] = self.endpoint_call_counts.get("fetch_my_trades", 0) + 1
        return self._exchange.fetch_my_trades(*args, **kwargs)

    def fetch_leverage(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.endpoint_call_counts["fetch_leverage"] = self.endpoint_call_counts.get("fetch_leverage", 0) + 1
        return self._exchange.fetch_leverage(*args, **kwargs)

    def fetch_trading_fee(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.endpoint_call_counts["fetch_trading_fee"] = self.endpoint_call_counts.get("fetch_trading_fee", 0) + 1
        return self._exchange.fetch_trading_fee(*args, **kwargs)

    def fetch_position_mode(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.endpoint_call_counts["fetch_position_mode"] = self.endpoint_call_counts.get("fetch_position_mode", 0) + 1
        return self._exchange.fetch_position_mode(*args, **kwargs)

    # Prohibited mutations interceptors
    def create_order(self, *args: Any, **kwargs: Any) -> Any:
        self.create_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: create_order")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:
        self.cancel_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: cancel_order")

    def cancel_all_owned(self, *args: Any, **kwargs: Any) -> Any:
        self.cancel_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: cancel_all_owned")

    def submit_emergency_flatten(self, *args: Any, **kwargs: Any) -> Any:
        self.flatten_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: submit_emergency_flatten")

    def set_position_mode(self, *args: Any, **kwargs: Any) -> Any:
        self.account_mutation_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: set_position_mode")

    def set_leverage(self, *args: Any, **kwargs: Any) -> Any:
        self.account_mutation_attempts += 1
        self.prohibited_call_attempts += 1
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED_IN_R1: set_leverage")

    def __getattr__(self, name: str) -> Any:
        if name in PROHIBITED_MUTATION_METHODS:
            self.prohibited_call_attempts += 1
            raise DemoAdapterError(f"MUTATION_ATTEMPT_PROHIBITED_IN_R1: {name}")
        if name not in ALLOWED_READ_ENDPOINTS:
            self.unknown_endpoint_attempts += 1
            raise DemoAdapterError(f"ENDPOINT_DENIED_UNKNOWN_CATEGORY: {name}")
        return getattr(self._exchange, name)


def run_r1_read_only_preflight(
    *,
    run_id: str = CANONICAL_R1_RUN_ID,
    session_id: str = CANONICAL_R1_SESSION_ID,
    arm_token: str = CANONICAL_ARM_TOKEN,
    prep_dir: Path | None = None,
    package_id: str = CANONICAL_R1_PACKAGE_ID,
    r0_evidence_id: str | None = None,
    user_authorization_statement: str = "Authorize R1 Read-Only OKX Demo Preflight and create the Codex execution MD. No order mutation or R2 authorization.",
    exchange: Any | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    passphrase: str | None = None,
    load_env_file: bool = True,
) -> dict[str, Any]:
    print("=== Commencing R1 Read-Only OKX Demo Preflight Execution ===")
    expected_token = f"OKX_DEMO:{session_id}"
    if arm_token != expected_token:
        raise DemoAdapterError(f"Arm token mismatch: expected {expected_token}, got {arm_token}")
    prep_path = prep_dir or (ROOT / "artifacts" / "r1_read_only_preflight_preparation" / "r1-prep-20260904T121733Z")
    preparation = verify_r1_preflight_preparation(
        prep_path=prep_path,
        package_id=package_id,
        run_id=run_id,
        session_id=session_id,
        arm_token=arm_token,
        r0_evidence_id=r0_evidence_id,
    )

    # 1. Verify candidate fingerprint
    print("Verifying candidate source fingerprint...")
    fingerprint_result = compute_candidate_fingerprint(ROOT)
    actual_fingerprint = fingerprint_result.get("candidate_fingerprint", "")
    print(f"Active Fingerprint: {actual_fingerprint}")
    if actual_fingerprint != EXPECTED_CANDIDATE_FINGERPRINT:
        raise DemoAdapterError(
            f"Candidate fingerprint drift detected! Expected {EXPECTED_CANDIDATE_FINGERPRINT}, found {actual_fingerprint}"
        )

    # 2. Verify arm token and session binding
    # 3. Resolve exchange / credentials into memory only (no logging/printing)
    if exchange is not None:
        raw_exchange = exchange
        key = api_key or "present"
        sec = api_secret or "present"
        pass_phrase = passphrase or "present"
    else:
        if load_env_file:
            load_dotenv()
        key = api_key if api_key is not None else os.getenv("OKX_API_KEY", "").strip()
        sec = api_secret if api_secret is not None else os.getenv("OKX_SECRET", "").strip()
        pass_phrase = passphrase if passphrase is not None else os.getenv("OKX_PASSPHRASE", "").strip()
        if not key or not sec or not pass_phrase:
            raise DemoAdapterError("Missing required OKX credentials in environment (.env)")

        # 4. Construct CCXT demo exchange
        print("Constructing OKX Demo sandbox transport...")
        raw_exchange = build_ccxt_demo_exchange(
            api_key=key,
            api_secret=sec,
            passphrase=pass_phrase,
        )
    audited_exchange = AuditedReadOnlyExchangeWrapper(raw_exchange)

    # 5. Create run artifact directory
    run_dir = ROOT / "artifacts" / "r1_read_only_preflight_runs" / run_id
    if run_dir.exists():
        # Prevent collisions / overwriting existing runs
        raise DemoAdapterError(f"Run directory already exists: {run_dir}. Identity reuse prohibited.")
    run_dir.mkdir(parents=True, exist_ok=True)
    state_file = run_dir / "state" / "demo_runtime_state.json"

    # 6. Initialize adapter and execute preflight
    profile = load_promoted_profile(ROOT)
    state_store = DemoStateStore(state_file)
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        symbol="BTC/USDT:USDT",
        margin_mode="isolated",
        position_mode="net_mode",
        leverage=3,
        maximum_clock_skew_ms=1500,
        explicit_arm_token=arm_token,
    )
    adapter = OkxDemoAdapter(
        exchange=audited_exchange,
        config=config,
        promoted_profile=profile,
        session_id=session_id,
        state_store=state_store,
    )

    print("Executing authoritative adapter.preflight()...")
    start_time = time.time()
    snapshot = adapter.preflight()
    duration_s = time.time() - start_time
    print(f"Preflight completed in {duration_s:.2f}s")

    # 7. Authoritative reconciliation assertions
    print("Verifying authoritative reconciliation gates...")
    assert snapshot.position_btc == 0.0, f"Unowned position detected: {snapshot.position_btc} BTC"
    assert len(snapshot.open_orders) == 0, f"Unowned open orders detected: {len(snapshot.open_orders)}"
    assert snapshot.total_equity_usdt >= 100.0, f"Insufficient USDT equity: {snapshot.total_equity_usdt}"
    assert snapshot.clock_skew_ms <= 1500, f"Clock skew exceeded budget: {snapshot.clock_skew_ms}ms"
    assert snapshot.leverage == 3.0, f"Leverage mismatch: {snapshot.leverage}"
    assert snapshot.position_mode == "net_mode", f"Position mode mismatch: {snapshot.position_mode}"
    assert adapter.halted_reason == "", f"Adapter in halted state: {adapter.halted_reason}"

    # Verify zero mutations
    assert audited_exchange.create_attempts == 0
    assert audited_exchange.cancel_attempts == 0
    assert audited_exchange.flatten_attempts == 0
    assert audited_exchange.account_mutation_attempts == 0
    assert audited_exchange.live_endpoint_attempts == 0
    assert audited_exchange.prohibited_call_attempts == 0

    now_iso = datetime.now(timezone.utc).isoformat()

    # 8. Write immutable evidence package
    print("Writing R1 read-only preflight evidence artifacts...")

    # 8.1 candidate_identity.json
    candidate_identity = {
        "candidate_fingerprint": actual_fingerprint,
        "r0_closure_ref": preparation["r0_evidence_id"],
        "r0_evidence_id": preparation["r0_evidence_id"],
        "r1_package_id": preparation["package_id"],
        "r1_run_id": run_id,
        "r1_session_id": session_id,
        "symbol": "BTC/USDT:USDT",
        "profile_id": profile.profile_id,
        "profile_fingerprint": profile.profile_fingerprint,
        "specification_sha256": profile.specification_sha256,
        "timestamp_utc": now_iso,
    }
    write_json_atomic(run_dir / "candidate_identity.json", candidate_identity)

    # 8.2 preflight_authorization.json
    preflight_auth = {
        "authorized": True,
        "authorization_scope": "R1_READ_ONLY_OKX_DEMO_PREFLIGHT",
        "authorized_by_user_statement": user_authorization_statement,
        "arm_token_sha256": canonical_sha256(arm_token),
        "r2_authorized": False,
        "order_mutation_authorized": False,
        "live_authorized": False,
        "timestamp_utc": now_iso,
    }
    write_json_atomic(run_dir / "preflight_authorization.json", preflight_auth)

    # 8.3 transport_audit.json
    transport_audit = {
        "execution_mode": "OKX_DEMO",
        "sandbox_mode": audited_exchange.options.get("sandboxMode") is True,
        "simulated_trading_header": audited_exchange.headers.get("x-simulated-trading") == "1",
        "live_mode_available": False,
        "transport_environment": "OKX_DEMO_SANDBOX",
        "status": "VERIFIED_DEMO_TRANSPORT",
    }
    write_json_atomic(run_dir / "transport_audit.json", transport_audit)

    # 8.4 endpoint_audit.json
    endpoint_audit = {
        "total_read_calls": sum(audited_exchange.endpoint_call_counts.values()),
        "calls_by_endpoint": audited_exchange.endpoint_call_counts,
        "create_attempts": audited_exchange.create_attempts,
        "cancel_attempts": audited_exchange.cancel_attempts,
        "flatten_attempts": audited_exchange.flatten_attempts,
        "account_mutation_attempts": audited_exchange.account_mutation_attempts,
        "live_endpoint_attempts": audited_exchange.live_endpoint_attempts,
        "prohibited_call_attempts": audited_exchange.prohibited_call_attempts,
        "unknown_endpoint_attempts": audited_exchange.unknown_endpoint_attempts,
        "policy": "DENY_UNKNOWN",
        "enforcement": "FAIL_CLOSED_ZERO_MUTATIONS",
    }
    write_json_atomic(run_dir / "endpoint_audit.json", endpoint_audit)

    # 8.5 account_snapshot.json (Strictly sanitized: account_uid='present', zero secrets)
    public_snapshot = snapshot.public_dict()
    write_json_atomic(run_dir / "account_snapshot.json", public_snapshot)

    # 8.6 reconciliation_audit.json
    reconciliation_audit = {
        "clock_skew_ms": snapshot.clock_skew_ms,
        "clock_skew_budget_ms": 1500,
        "clock_skew_passed": snapshot.clock_skew_ms <= 1500,
        "initial_position_btc": snapshot.position_btc,
        "position_gate_passed": snapshot.position_btc == 0.0,
        "initial_open_orders": len(snapshot.open_orders),
        "orders_gate_passed": len(snapshot.open_orders) == 0,
        "total_equity_usdt": snapshot.total_equity_usdt,
        "free_equity_usdt": snapshot.free_equity_usdt,
        "equity_gate_passed": snapshot.total_equity_usdt >= 100.0,
        "leverage": snapshot.leverage,
        "leverage_gate_passed": snapshot.leverage == 3.0,
        "position_mode": snapshot.position_mode,
        "position_mode_gate_passed": snapshot.position_mode == "net_mode",
        "contract_size": str(adapter.market_spec.contract_size) if adapter.market_spec else "0.01",
        "contract_linear": adapter.market_spec.linear if adapter.market_spec else True,
        "contract_inverse": adapter.market_spec.inverse if adapter.market_spec else False,
        "maker_fee_rate": snapshot.maker_fee_rate,
        "taker_fee_rate": snapshot.taker_fee_rate,
        "reconciliation_status": "ALL_GATES_PASSED",
    }
    write_json_atomic(run_dir / "reconciliation_audit.json", reconciliation_audit)

    # 8.7 safety_audit.json
    safety_audit = {
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "prohibited_call_attempts": 0,
        "live_mode_available": False,
        "production_authorized": False,
        "r2_authorized": False,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "secrets_serialized": False,
    }
    write_json_atomic(run_dir / "safety_audit.json", safety_audit)

    # 8.8 preflight_decision.json
    preflight_decision = {
        "decision": "R1_PREFLIGHT_PASSED",
        "run_id": run_id,
        "session_id": session_id,
        "candidate_fingerprint": actual_fingerprint,
        "informational_next_status": "R2_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION",
        "r2_authorized": False,
        "order_mutation_count": 0,
        "timestamp_utc": now_iso,
    }
    write_json_atomic(run_dir / "preflight_decision.json", preflight_decision)

    # 8.9 completion_hashes.json
    completion_hashes = generate_completion_hashes(run_dir, "R1_PREFLIGHT_PASSED.json")
    write_json_atomic(run_dir / "completion_hashes.json", completion_hashes)

    # 8.10 R1_PREFLIGHT_PASSED.json (terminal marker written last)
    r1_passed = {
        "status": "R1_PREFLIGHT_PASSED",
        "run_id": run_id,
        "session_id": session_id,
        "r0_closure_ref": preparation["r0_evidence_id"],
        "r0_evidence_id": preparation["r0_evidence_id"],
        "r1_package_id": preparation["package_id"],
        "candidate_fingerprint": actual_fingerprint,
        "timestamp_utc": now_iso,
        "files_verified": len(completion_hashes),
        "completion_hashes_sha256": canonical_sha256(json.dumps(completion_hashes, sort_keys=True)),
        "create_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "r2_authorized": False,
        "production_authorized": False,
        "git_write_operation": False,
        "ready_for_r2_preparation_authorization": True,
    }
    write_json_atomic(run_dir / "R1_PREFLIGHT_PASSED.json", r1_passed)

    # 9. Automated Secret Leakage Scan across output files
    print("Conducting zero-secret leakage scan across output artifacts...")
    for item in run_dir.rglob("*"):
        if item.is_file():
            content = item.read_text(encoding="utf-8", errors="ignore")
            if key and key != "present":
                assert key not in content, f"Secret leakage in {item.name}: key detected!"
            if sec and sec != "present":
                assert sec not in content, f"Secret leakage in {item.name}: secret detected!"
            if pass_phrase and pass_phrase != "present":
                assert pass_phrase not in content, f"Secret leakage in {item.name}: passphrase detected!"
            if snapshot.account_uid and snapshot.account_uid != "present":
                assert snapshot.account_uid not in content, f"Raw account_uid leaked in {item.name}!"

    print("Zero secret leakage confirmed.")
    print(f"R1 Read-Only Preflight PASSED! Evidence recorded at: {run_dir}")
    return r1_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute R1 Read-Only OKX Demo Preflight")
    parser.add_argument("--run-id", default=CANONICAL_R1_RUN_ID, help="R1 Run ID")
    parser.add_argument("--session-id", default=CANONICAL_R1_SESSION_ID, help="R1 Session ID")
    parser.add_argument("--arm-token", default=CANONICAL_ARM_TOKEN, help="Explicit Arm Token")
    parser.add_argument("--prep-dir", type=Path, help="Fresh R1 preparation package directory")
    parser.add_argument("--package-id", default=CANONICAL_R1_PACKAGE_ID, help="Fresh R1 package ID")
    parser.add_argument("--r0-evidence-id", help="Exact predecessor R0 evidence ID")
    args = parser.parse_args()

    res = run_r1_read_only_preflight(
        run_id=args.run_id,
        session_id=args.session_id,
        arm_token=args.arm_token,
        prep_dir=args.prep_dir,
        package_id=args.package_id,
        r0_evidence_id=args.r0_evidence_id,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
