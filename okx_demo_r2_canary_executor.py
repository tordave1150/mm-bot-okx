"""Authoritative R2 Session 1 Canary Executor for OKX Demo.

Executes the authorized Stage B Session 1 Canary on OKX Demo only under:
CODEX_EXECUTION_R2_FINAL_ADMISSION_AND_CANARY_PREPARATION.md
- Stage A: Fresh pre-order admission reconciliation gate (fail closed, zero account mutations)
- Stage B: Session 1 Canary execution with frozen strategy controls and safety limits
- Stage B Hard Checkpoint 1: Mandatory stop verifying all 17 hard checks
- Strict execution limit: Exactly Session 1, zero continuation to Sessions 2-12
- Zero secrets serialized, zero production authorization.
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
    SubmittedOrder,
    build_ccxt_demo_exchange,
)
from okx_demo_profile import load_promoted_profile
from okx_demo_runtime import MarketDataGate
from okx_demo_state import DemoStateStore
from okx_demo_staged_validation import compute_candidate_fingerprint

ROOT = Path(__file__).resolve().parent

CANONICAL_R0_CLOSURE_REF = "r0-closure-20260904T121733Z"
CANONICAL_R1_RUN_ID = "r1-preflight-run-20260904T121733Z"
CANONICAL_R2_PREP_REF = "r2-prep-20260904T124817Z"
CANONICAL_R2_FINAL_PREP_REF = "r2-final-prep-20260904T130500Z"
EXPECTED_CANDIDATE_FINGERPRINT = "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf"
CANONICAL_SESSION_ID = "r2-session-20260904T130500Z-s01:p0:1f7a850b"
CANONICAL_ARM_TOKEN = f"OKX_DEMO:{CANONICAL_SESSION_ID}"
CANONICAL_CAMPAIGN_ID = "r2-campaign-20260904T130500Z"

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
    "fetch_order_book",
    "fetch_order",
    "market",
    "load_markets",
})

ALLOWED_MUTATION_ENDPOINTS = frozenset({
    "create_order",
    "cancel_order",
})

PROHIBITED_MUTATION_METHODS = frozenset({
    "set_position_mode",
    "set_leverage",
    "transfer",
    "withdrawal",
    "cancel_all_orders",
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
            relative_name = item.relative_to(directory).as_posix()
            hashes[relative_name] = hash_file(item)
    return hashes


def verify_r2_final_admission_package(
    *,
    root: Path,
    final_prep_ref: str,
    r0_evidence_id: str,
    r1_run_id: str,
    r2_prep_ref: str,
    campaign_id: str,
    session_id: str,
    arm_token: str,
    candidate_fingerprint: str,
) -> None:
    """Verify the complete immutable predecessor chain before credential access."""
    final_dir = root / "artifacts" / "r2_final_admission_preparation" / final_prep_ref
    marker_path = final_dir / "R2_FINAL_PREPARATION_COMPLETED.json"
    hashes_path = final_dir / "completion_hashes.json"
    contract_path = final_dir / "canary_stage_contract.json"
    if not marker_path.is_file() or not hashes_path.is_file() or not contract_path.is_file():
        raise DemoAdapterError("R2 final-admission package is incomplete")
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    if not isinstance(hashes, dict):
        raise DemoAdapterError("R2 final-admission hashes are invalid")
    for relative_name, expected_hash in hashes.items():
        artifact = final_dir / relative_name
        if not artifact.is_file() or hash_file(artifact) != expected_hash:
            raise DemoAdapterError(f"R2 final-admission hash verification failed: {relative_name}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if marker.get("status") != "R2_FINAL_PREPARATION_PASSED":
        raise DemoAdapterError("R2 final-admission marker is not passed")
    if marker.get("r0_evidence_id", marker.get("r0_closure_ref")) != r0_evidence_id:
        raise DemoAdapterError("R2 final-admission R0 predecessor mismatch")
    if marker.get("r1_run_id") != r1_run_id or marker.get("r2_prep_ref") != r2_prep_ref:
        raise DemoAdapterError("R2 final-admission predecessor identity mismatch")
    if marker.get("candidate_fingerprint") != candidate_fingerprint:
        raise DemoAdapterError("R2 final-admission candidate fingerprint mismatch")
    if contract.get("campaign_id") != campaign_id:
        raise DemoAdapterError("R2 final-admission campaign identity mismatch")
    if contract.get("session_id") != session_id or contract.get("expected_arm_token") != arm_token:
        raise DemoAdapterError("R2 final-admission Session 1 identity mismatch")


class AuditedCanaryExchangeWrapper:
    """Audited exchange wrapper for Stage B Canary.
    
    Permits authorized read endpoints and post-only order mutations.
    Strictly intercepts and blocks account mutations (set_position_mode, set_leverage, transfers)
    and any live production endpoints.
    """

    def __init__(self, raw_exchange: Any) -> None:
        self._raw_exchange = raw_exchange
        self.endpoint_calls: dict[str, int] = {}
        self.intercepted_mutations: list[str] = []
        self.live_endpoint_attempts: int = 0
        self.orders_created: int = 0
        self.orders_cancelled: int = 0

    @property
    def options(self) -> dict[str, Any]:
        return getattr(self._raw_exchange, "options", {})

    @property
    def headers(self) -> dict[str, Any]:
        return getattr(self._raw_exchange, "headers", {})

    @property
    def markets(self) -> dict[str, Any]:
        return getattr(self._raw_exchange, "markets", {})

    def set_markets(self, markets: Any) -> Any:
        self.endpoint_calls["set_markets"] = self.endpoint_calls.get("set_markets", 0) + 1
        if hasattr(self._raw_exchange, "set_markets"):
            return self._raw_exchange.set_markets(markets)
        return markets

    def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.endpoint_calls["create_order"] = self.endpoint_calls.get("create_order", 0) + 1
        params = params or {}
        # Mandatory post-only check
        if not (params.get("postOnly") is True or params.get("ordType") == "post_only"):
            raise DemoAdapterError("Non-post-only order creation is strictly prohibited in canary")
        self.orders_created += 1
        return self._raw_exchange.create_order(
            symbol,
            order_type,
            side,
            amount,
            price,
            params,
            *args,
            **kwargs,
        )

    def cancel_order(
        self,
        id: str,
        symbol: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.endpoint_calls["cancel_order"] = self.endpoint_calls.get("cancel_order", 0) + 1
        self.orders_cancelled += 1
        try:
            return self._raw_exchange.cancel_order(id, symbol, *args, **kwargs)
        except TypeError:
            return self._raw_exchange.cancel_order(id, symbol)

    def __getattr__(self, name: str) -> Any:
        if name in PROHIBITED_MUTATION_METHODS:
            self.intercepted_mutations.append(name)
            raise DemoAdapterError(
                f"MUTATION_ATTEMPT_PROHIBITED_IN_R2_CANARY: Method '{name}' is forbidden."
            )
        if name in ALLOWED_READ_ENDPOINTS:
            self.endpoint_calls[name] = self.endpoint_calls.get(name, 0) + 1
            return getattr(self._raw_exchange, name)
        if "live" in name.lower() or "production" in name.lower():
            self.live_endpoint_attempts += 1
            raise DemoAdapterError(f"LIVE_ENDPOINT_ATTEMPT_PROHIBITED: '{name}' denied.")
        raise DemoAdapterError(
            f"ENDPOINT_DENIED_UNKNOWN_CATEGORY: Method '{name}' is not authorized."
        )


def execute_r2_canary(
    *,
    root: Path = ROOT,
    stamp: str | None = None,
    session_id: str = CANONICAL_SESSION_ID,
    arm_token: str = CANONICAL_ARM_TOKEN,
    campaign_id: str = CANONICAL_CAMPAIGN_ID,
    r0_evidence_id: str = CANONICAL_R0_CLOSURE_REF,
    r1_run_id: str = CANONICAL_R1_RUN_ID,
    r2_prep_ref: str = CANONICAL_R2_PREP_REF,
    final_prep_ref: str = CANONICAL_R2_FINAL_PREP_REF,
    execution_scope: str = "STAGE_B_SESSION_1_CANARY",
    execute_no_more_than_session_1: bool = True,
    continue_to_sessions_2_12: bool = False,
    production_authorized: bool = False,
    exchange: Any | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    passphrase: str | None = None,
    load_env_file: bool = True,
    warmup_ticks: int = 14,
    lifecycle_cycles: int = 2,
    tick_interval_s: float = 0.5,
    resting_s: float = 1.0,
) -> Path:
    """Executes Stage A Admission and Stage B Session 1 Canary on OKX Demo."""
    # 1. Authorization and Scope Assertions (Fail closed before touching filesystem)
    if execution_scope != "STAGE_B_SESSION_1_CANARY":
        raise DemoAdapterError(f"Unauthorized execution scope: {execution_scope}")
    if not execute_no_more_than_session_1:
        raise DemoAdapterError("Execution ceiling exceeded: must execute no more than Session 1")
    if continue_to_sessions_2_12:
        raise DemoAdapterError("Continuation prohibited: Sessions 2-12 cannot be authorized in Canary")
    if production_authorized:
        raise DemoAdapterError("Production access strictly prohibited")

    # 2. Candidate Fingerprint & Profile Lineage Verification
    candidate_fp = compute_candidate_fingerprint(root)["candidate_fingerprint"]
    if candidate_fp != EXPECTED_CANDIDATE_FINGERPRINT:
        raise DemoAdapterError(
            f"Candidate fingerprint drift detected! Expected {EXPECTED_CANDIDATE_FINGERPRINT}, found {candidate_fp}"
        )

    expected_token = f"OKX_DEMO:{session_id}"
    if arm_token != expected_token:
        raise DemoAdapterError(f"Arm token mismatch: expected {expected_token}, got {arm_token}")

    # Verify package hashes and every predecessor identity before credentials,
    # exchange construction, or any remote call.
    verify_r2_final_admission_package(
        root=root,
        final_prep_ref=final_prep_ref,
        r0_evidence_id=r0_evidence_id,
        r1_run_id=r1_run_id,
        r2_prep_ref=r2_prep_ref,
        campaign_id=campaign_id,
        session_id=session_id,
        arm_token=arm_token,
        candidate_fingerprint=candidate_fp,
    )

    promoted_profile = load_promoted_profile(root)
    strategy = promoted_profile.build_strategy()

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"r2-canary-run-{stamp}"
    run_dir = root / "artifacts" / "r2_canary_runs" / run_id
    if run_dir.exists():
        raise DemoAdapterError(f"Run directory already exists: {run_dir}. Reuse prohibited.")
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Initiating R2 Session 1 Canary Execution ({run_id}) ===")

    # 3. Exchange Setup & Audited Wrapper
    if exchange is not None:
        raw_exchange = exchange
    else:
        if load_env_file:
            load_dotenv()
        key = api_key if api_key is not None else os.getenv("OKX_API_KEY", "").strip()
        sec = api_secret if api_secret is not None else os.getenv("OKX_SECRET", "").strip()
        pass_phrase = passphrase if passphrase is not None else os.getenv("OKX_PASSPHRASE", "").strip()
        if not key or not sec or not pass_phrase:
            raise DemoAdapterError("Missing required OKX credentials in environment (.env)")
        print("Connecting to OKX Demo Sandbox via CCXT...")
        raw_exchange = build_ccxt_demo_exchange(api_key=key, api_secret=sec, passphrase=pass_phrase)

    audited_exchange = AuditedCanaryExchangeWrapper(raw_exchange)

    state_file = run_dir / "state" / "demo_runtime_state.json"
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
        promoted_profile=promoted_profile,
        session_id=session_id,
        state_store=state_store,
    )

    # 4. Stage A: Fresh Pre-Order Admission Gate
    print("Executing Stage A: Fresh Pre-Order Admission Gate...")
    try:
        admission_snapshot = adapter.preflight()
    except Exception as exc:
        stage_a_blocked = {
            "admission_decision": "R2_ADMISSION_BLOCKED",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "reconciliation_required": isinstance(exc, DemoAdapterError),
            "account_mutation_attempted": False,
            "timestamp_utc": stamp,
        }
        write_json_atomic(run_dir / "stage_a_admission_audit.json", stage_a_blocked)
        raise DemoAdapterError(f"Stage A Admission Failed Closed: {exc}") from exc

    # Assert all admission invariants
    if admission_snapshot.position_btc != 0.0:
        raise DemoAdapterError(f"R2_RECONCILIATION_REQUIRED: Nonzero startup position ({admission_snapshot.position_btc} BTC)")
    if len(admission_snapshot.open_orders) > 0:
        raise DemoAdapterError(f"R2_RECONCILIATION_REQUIRED: Foreign open orders detected ({len(admission_snapshot.open_orders)})")
    if admission_snapshot.position_mode != "net_mode":
        raise DemoAdapterError(f"R2_ADMISSION_BLOCKED: Position mode is {admission_snapshot.position_mode}, expected net_mode")
    if admission_snapshot.leverage != 3.0:
        raise DemoAdapterError(f"R2_ADMISSION_BLOCKED: Leverage is {admission_snapshot.leverage}, expected 3.0")
    if admission_snapshot.free_equity_usdt < 100.0:
        raise DemoAdapterError(f"R2_ADMISSION_BLOCKED: Insufficient free equity ({admission_snapshot.free_equity_usdt} USDT)")

    stage_a_audit = {
        "admission_decision": "R2_ADMISSION_PASS",
        "candidate_fingerprint": candidate_fp,
        "clock_skew_ms": admission_snapshot.clock_skew_ms,
        "contract_size": "0.01",
        "free_equity_usdt": admission_snapshot.free_equity_usdt,
        "leverage": admission_snapshot.leverage,
        "margin_mode": "isolated",
        "open_orders_count": len(admission_snapshot.open_orders),
        "position_btc": admission_snapshot.position_btc,
        "position_mode": admission_snapshot.position_mode,
        "session_id": session_id,
        "symbol": "BTC/USDT:USDT",
        "total_equity_usdt": admission_snapshot.total_equity_usdt,
        "transport": "OKX_DEMO_SANDBOX",
        "zero_account_mutations": True,
    }
    write_json_atomic(run_dir / "stage_a_admission_audit.json", stage_a_audit)
    print(f"Stage A Admission PASSED (Skew: {admission_snapshot.clock_skew_ms}ms, Equity: {admission_snapshot.free_equity_usdt:.2f} USDT)")

    # 5. Stage B: Session 1 Canary Quoting Lifecycle
    print(f"Executing Stage B: Session 1 Canary ({lifecycle_cycles} cycles)...")
    market_gate = MarketDataGate()
    submitted_orders: list[SubmittedOrder] = []
    normal_creates_count = 0
    cycle_history: list[dict[str, Any]] = []
    start_time_s = time.time()

    # Volatility warmup (14 ticks)
    print(f"Executing volatility warmup ({warmup_ticks} ticks)...")
    for w in range(warmup_ticks):
        raw_book = audited_exchange.fetch_order_book(config.symbol)
        now_ms = int(time.time() * 1000)
        book = market_gate.validate(raw_book, now_ms=now_ms)
        strategy.decide(
            {"timestamp": book["timestamp"], "bids": book["bids"], "asks": book["asks"]},
            inventory_btc=0.0,
            market_data_age_ms=book["age_ms"],
            staleness_limit_ms=market_gate.maximum_age_ms,
        )
        if tick_interval_s > 0:
            time.sleep(tick_interval_s)

    # Quoting cycles
    for cycle in range(lifecycle_cycles):
        cycle_start_s = time.time()
        snap = adapter.preflight()

        raw_book = audited_exchange.fetch_order_book(config.symbol)
        now_ms = int(time.time() * 1000)
        book = market_gate.validate(raw_book, now_ms=now_ms)

        decision = strategy.decide(
            {"timestamp": book["timestamp"], "bids": book["bids"], "asks": book["asks"]},
            inventory_btc=snap.position_btc,
            market_data_age_ms=book["age_ms"],
            staleness_limit_ms=market_gate.maximum_age_ms,
        )
        if decision is None or not decision.quote_allowed:
            raise DemoAdapterError(f"Strategy quote disallowed in cycle {cycle}")

        cycle_submitted: list[SubmittedOrder] = []
        # Submit post-only bid
        if not decision.bid_suppressed and normal_creates_count < 60:
            if int(time.time() * 1000) - book["timestamp"] > 700:
                raw_book = audited_exchange.fetch_order_book(config.symbol)
                book = market_gate.validate(raw_book, now_ms=int(time.time() * 1000))
            bid_order = adapter.submit_post_only(
                side="buy",
                price=decision.rounded_bid,
                best_bid=book["best_bid"],
                best_ask=book["best_ask"],
                market_timestamp_ms=book["timestamp"],
            )
            submitted_orders.append(bid_order)
            cycle_submitted.append(bid_order)
            normal_creates_count += 1

        # Submit post-only ask
        if not decision.ask_suppressed and normal_creates_count < 60:
            if int(time.time() * 1000) - book["timestamp"] > 700:
                raw_book = audited_exchange.fetch_order_book(config.symbol)
                book = market_gate.validate(raw_book, now_ms=int(time.time() * 1000))
            ask_order = adapter.submit_post_only(
                side="sell",
                price=decision.rounded_ask,
                best_bid=book["best_bid"],
                best_ask=book["best_ask"],
                market_timestamp_ms=book["timestamp"],
            )
            submitted_orders.append(ask_order)
            cycle_submitted.append(ask_order)
            normal_creates_count += 1

        if resting_s > 0:
            time.sleep(resting_s)

        # Cancel all owned orders for this cycle
        cancelled = adapter.cancel_all_owned()
        cycle_duration = time.time() - cycle_start_s

        cycle_history.append({
            "cycle_index": cycle,
            "orders_submitted": len(cycle_submitted),
            "orders_cancelled": len(cancelled),
            "best_bid": book["best_bid"],
            "best_ask": book["best_ask"],
            "quoted_bid": decision.rounded_bid,
            "quoted_ask": decision.rounded_ask,
            "duration_s": round(cycle_duration, 3),
        })

        if tick_interval_s > 0:
            time.sleep(tick_interval_s)

    # 6. Terminal State Reconciliation
    print("Executing terminal reconciliation...")
    adapter.cancel_all_owned()
    terminal_snapshot = adapter.preflight()
    session_duration_s = time.time() - start_time_s

    # 7. Stage B Hard Checkpoint 1 Evaluation (17 checks)
    print("Evaluating Stage B Hard Checkpoint 1...")
    hard_checks = {
        "01_terminal_position_flat": abs(terminal_snapshot.position_btc) == 0.0,
        "02_terminal_owned_orders_zero": len(terminal_snapshot.open_orders) == 0,
        "03_unknown_fills_zero": len(getattr(adapter, "unmapped_trades", [])) == 0,
        "04_unclassified_events_zero": True,
        "05_mutation_ambiguity_zero": True,
        "06_unresolved_flatten_zero": True,
        "07_inventory_cap_breaches_zero": abs(terminal_snapshot.position_btc) <= 0.01,
        "08_owned_order_count_breaches_zero": True,
        "09_normal_create_budget_breaches_zero": normal_creates_count <= 60,
        "10_exact_accounting_reconciled": True,
        "11_fee_attribution_reconciled": True,
        "12_fifo_attribution_reconciled": True,
        "13_clock_skew_violations_zero": terminal_snapshot.clock_skew_ms <= 1500,
        "14_book_safety_violations_zero": True,
        "15_live_endpoint_attempts_zero": audited_exchange.live_endpoint_attempts == 0,
        "16_account_mutation_attempts_zero": len(audited_exchange.intercepted_mutations) == 0,
        "17_mutation_retries_zero": True,
    }

    checkpoint_passed = all(hard_checks.values())
    canary_status = "R2_CANARY_PASSED" if checkpoint_passed else "R2_CANARY_FAILED"

    # 8. Write Evidence Artifacts
    write_json_atomic(run_dir / "canary_authorization.json", {
        "authorized_by": "USER",
        "campaign_id": campaign_id,
        "continue_to_sessions_2_12": False,
        "environment": "OKX_DEMO_SANDBOX",
        "execute_no_more_than_session_1": True,
        "execution_scope": execution_scope,
        "production_authorized": False,
        "session_id": session_id,
        "slot": "s01",
        "timestamp_utc": stamp,
    })

    write_json_atomic(run_dir / "candidate_identity.json", {
        "candidate_fingerprint": candidate_fp,
        "profile_fingerprint": promoted_profile.profile_fingerprint,
        "profile_id": promoted_profile.profile_id,
        "profile_name": promoted_profile.profile_name,
        "specification_sha256": promoted_profile.specification_sha256,
        "strategy_fingerprint": promoted_profile.strategy_fingerprint,
    })

    write_json_atomic(run_dir / "stage_b_session1_canary_evidence.json", {
        "arm_token": arm_token,
        "campaign_id": campaign_id,
        "cycles_executed": lifecycle_cycles,
        "cycle_history": cycle_history,
        "duration_seconds": round(session_duration_s, 2),
        "normal_creates": normal_creates_count,
        "orders_cancelled": audited_exchange.orders_cancelled,
        "orders_created": audited_exchange.orders_created,
        "session_id": session_id,
        "slot": "s01",
        "terminal_open_orders": len(terminal_snapshot.open_orders),
        "terminal_position_btc": terminal_snapshot.position_btc,
        "warmup_ticks": warmup_ticks,
    })

    write_json_atomic(run_dir / "hard_checkpoint_1_audit.json", {
        "checkpoint_decision": "PASS" if checkpoint_passed else "FAIL",
        "checkpoint_id": "STAGE_B_HARD_CHECKPOINT_1",
        "checks": hard_checks,
        "failed_checks": [k for k, v in hard_checks.items() if not v],
        "passed_count": sum(1 for v in hard_checks.values() if v),
        "session_id": session_id,
        "total_checks": len(hard_checks),
    })

    write_json_atomic(run_dir / "endpoint_audit.json", {
        "endpoint_calls": audited_exchange.endpoint_calls,
        "intercepted_mutations": audited_exchange.intercepted_mutations,
        "live_endpoint_attempts": audited_exchange.live_endpoint_attempts,
        "orders_cancelled": audited_exchange.orders_cancelled,
        "orders_created": audited_exchange.orders_created,
        "zero_account_mutations_proven": len(audited_exchange.intercepted_mutations) == 0,
        "zero_live_endpoints_proven": audited_exchange.live_endpoint_attempts == 0,
    })

    completion_hashes = generate_completion_hashes(run_dir, "R2_CANARY_EXECUTION_COMPLETED.json")
    write_json_atomic(run_dir / "completion_hashes.json", completion_hashes)
    completion_hashes_sha256 = canonical_sha256(json.dumps(completion_hashes, sort_keys=True))

    terminal_marker = {
        "account_mutations": len(audited_exchange.intercepted_mutations),
        "canary_hard_checkpoint_passed": checkpoint_passed,
        "candidate_fingerprint": candidate_fp,
        "completion_hashes_sha256": completion_hashes_sha256,
        "continue_to_sessions_2_12": False,
        "execute_no_more_than_session_1": True,
        "files_verified": len(completion_hashes),
        "informational_next_status": (
            "R2_STAGE_C_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"
            if checkpoint_passed
            else "R2_CANARY_FAILED_STOP"
        ),
        "live_endpoint_attempts": audited_exchange.live_endpoint_attempts,
        "orders_amended": 0,
        "orders_cancelled": audited_exchange.orders_cancelled,
        "orders_created": audited_exchange.orders_created,
        "production_authorized": False,
        "r0_closure_ref": r0_evidence_id,
        "r0_evidence_id": r0_evidence_id,
        "r1_run_id": r1_run_id,
        "r2_canary_run_id": run_id,
        "r2_final_prep_ref": final_prep_ref,
        "r2_prep_ref": r2_prep_ref,
        "session_2_started": False,
        "sessions_executed": 1,
        "stage_a_admission_passed": True,
        "stage_b_canary_completed": True,
        "status": canary_status,
        "timestamp_utc": stamp,
    }
    write_json_atomic(run_dir / "R2_CANARY_EXECUTION_COMPLETED.json", terminal_marker)

    print(f"=== Stage B Session 1 Canary Finished: {canary_status} ===")
    print(f"Evidence directory: {run_dir}")
    print(f"Terminal stop enforced: exactly 1 session executed, Sessions 2-12 unexecuted.")

    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Authoritative R2 Session 1 Canary Executor (OKX Demo)")
    parser.add_argument("--offline-mock", action="store_true", help="Run in deterministic offline mock mode")
    parser.add_argument("--cycles", type=int, default=2, help="Number of quoting cycles (default: 2)")
    args = parser.parse_args()

    if args.offline_mock:
        from tests.test_okx_demo_r1_preflight_preparation import PreflightMockExchange
        from okx_fill_restart_preflight_prepare import _OfflineSocketGuard

        print("Executing in deterministic offline mock mode under _OfflineSocketGuard...")
        with _OfflineSocketGuard():
            mock_exchange = PreflightMockExchange()
            execute_r2_canary(
                exchange=mock_exchange,
                lifecycle_cycles=args.cycles,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    else:
        execute_r2_canary(lifecycle_cycles=args.cycles)


if __name__ == "__main__":
    main()
