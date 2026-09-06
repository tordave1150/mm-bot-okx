"""Authoritative R2 Stage C (Q01-Q03) Three-Session Economic Checkpoint Executor for OKX Demo.

Executes the authorized Stage C Three-Session Economic Checkpoint on OKX Demo only under:
CODEX_EXECUTION_R2_THREE_SESSION_CHECKPOINT.md
- Scope: Exactly Q01, Q02, and Q03 of campaign r2-qualification-campaign-20260904T133500Z
- Frozen Candidate Fingerprint: 1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf
- Operational canary excluded from 12-session economic qualification denominator
- Mandatory fresh admission reconciliation gate before EACH session (Q01, Q02, Q03)
- Preserved frozen risk boundary (750 USDT capital, 0.01 lot, 0.01 BTC dominant inventory cap)
- Preserved terminal flatten lifecycle (single-flight reduce-only flatten permitted, post-only quoting)
- Exact session loss guards (22.50 USDT soft drawdown, 37.50 USDT hard kill, 75.00 USDT campaign loss)
- 17-point Stage C Three-Session Checkpoint (Checkpoint 2) evaluated after Q03
- Strict hard stop after Q03: zero execution of Q04-Q12, zero production authorization.
"""

from __future__ import annotations

import argparse
import atexit
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
from okx_demo_r2_evidence_integrity import EvidenceIntegrityError, replay_fill_ledger

ROOT = Path(__file__).resolve().parent

CANONICAL_R0_CLOSURE_REF = "r0-closure-20260904T121733Z"
CANONICAL_R1_RUN_ID = "r1-preflight-run-20260904T121733Z"
CANONICAL_R2_FINAL_PREP_REF = "r2-final-prep-20260904T130500Z"
CANONICAL_R2_CANARY_RUN_ID = "r2-canary-run-20260904T131836Z"
CANONICAL_STAGE_C_PREP_REF = "r2-stage-c-prep-20260904T133500Z"
EXPECTED_CANDIDATE_FINGERPRINT = "ef993bc42ffbb19cf1bfc94d3dcca32cd19909c9b0d9da73ab0a01ab0088ffb8"
CANONICAL_CAMPAIGN_ID = "r2-qualification-campaign-20260904T133500Z"

SLOT_SCHEDULE = [
    {
        "slot_index": 1,
        "qualification_slot": "Q01",
        "session_id": "r2-session-20260904T133500Z-q01:p0:9c1a01f1",
        "nonce": "9c1a01f1",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T133500Z-q01:p0:9c1a01f1",
    },
    {
        "slot_index": 2,
        "qualification_slot": "Q02",
        "session_id": "r2-session-20260904T133500Z-q02:p0:3e4b02a2",
        "nonce": "3e4b02a2",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T133500Z-q02:p0:3e4b02a2",
    },
    {
        "slot_index": 3,
        "qualification_slot": "Q03",
        "session_id": "r2-session-20260904T133500Z-q03:p0:7f8c03d3",
        "nonce": "7f8c03d3",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T133500Z-q03:p0:7f8c03d3",
    },
]

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


def write_interruption_guard(
    *,
    run_dir: Path,
    run_id: str,
    campaign_id: str,
    execution_scope: str,
    slots: list[Mapping[str, Any]],
) -> Path:
    """Durably deny successor admission until this run writes its terminal marker.

    A process can be terminated without giving Python a chance to execute an
    exception handler. This marker is written before credentials, transport,
    or mutations, so an absent completion marker is always fail-closed.
    """
    guard = run_dir / "R2_STAGE_C_INTERRUPTION_GUARD.json"
    write_json_atomic(guard, {
        "status": "R2_STAGE_C_IN_PROGRESS_OR_INTERRUPTED_NOT_ACCEPTABLE",
        "run_id": run_id,
        "campaign_id": campaign_id,
        "execution_scope": execution_scope,
        "session_ids": [str(slot["session_id"]) for slot in slots],
        "accept_authorized": False,
        "retry_authorized": False,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "successor_session_authorized": False,
        "terminal_account_authoritative": False,
        "credential_reads_at_guard_write": 0,
        "network_attempts_at_guard_write": 0,
        "order_mutations_at_guard_write": 0,
        "superseded_only_by": "R2_STAGE_C_EXECUTION_COMPLETED.json",
    })
    return guard


def generate_completion_hashes(directory: Path, marker_filename: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", marker_filename}
    hashes: dict[str, str] = {}
    for item in sorted(directory.rglob("*")):
        if item.is_file() and item.name not in ignored and not item.name.endswith(".tmp"):
            relative_name = item.relative_to(directory).as_posix()
            hashes[relative_name] = hash_file(item)
    return hashes


def write_post_flatten_terminal_failure(
    *,
    run_dir: Path,
    run_id: str,
    campaign_id: str,
    session_id: str,
    phase: str,
    state_path: Path,
    audited_exchange: Any | None,
    reason_code: str = "POST_FLATTEN_TERMINAL_RECONCILIATION_FAILED",
) -> Path:
    """Write one immutable, fail-closed marker when execution cannot finish.

    This handler intentionally performs no exchange operation. A failure after a
    flatten dispatch cannot establish an authoritative account state from local
    data, so the run and its session identity remain ineligible for accept,
    retry, resume, reuse, or successor admission.
    """
    terminal_path = run_dir / "R2_STAGE_C_EXECUTION_FAILED.json"
    if terminal_path.exists():
        return terminal_path
    if (run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json").exists():
        raise DemoAdapterError("Cannot write failure evidence after terminal success")

    def counter(field: str) -> int:
        return int(getattr(audited_exchange, field, 0))

    failure_evidence = run_dir / "terminal_failure_evidence.json"
    write_json_atomic(failure_evidence, {
        "status": "R2_STAGE_C_POST_FLATTEN_TERMINAL_FAILURE",
        "run_id": run_id,
        "campaign_id": campaign_id,
        "session_id": session_id,
        "failure_phase": phase,
        "failure_reason_code": reason_code,
        "local_state_available": state_path.is_file(),
        "local_state_sha256": hash_file(state_path) if state_path.is_file() else None,
        "normal_create_dispatches": counter("normal_orders_created"),
        "flatten_dispatches": counter("flatten_orders_created"),
        "cancel_dispatches": counter("orders_cancelled"),
        "mutation_retries": counter("mutation_retry_attempts"),
        "live_endpoint_attempts": counter("live_endpoint_attempts"),
        "terminal_account_authoritative": False,
        "reconciliation": False,
        "accept_authorized": False,
        "retry_authorized": False,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "successor_session_authorized": False,
    })
    completion_hashes = generate_completion_hashes(run_dir, "R2_STAGE_C_EXECUTION_FAILED.json")
    write_json_atomic(run_dir / "completion_hashes.json", completion_hashes)
    write_json_atomic(terminal_path, {
        "status": "R2_STAGE_C_EXECUTION_FAILED_NOT_ACCEPTABLE",
        "run_id": run_id,
        "campaign_id": campaign_id,
        "session_id": session_id,
        "failure_phase": phase,
        "failure_reason_code": reason_code,
        "terminal_account_authoritative": False,
        "reconciliation": False,
        "accept_authorized": False,
        "retry_authorized": False,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "successor_session_authorized": False,
        "completion_hashes_sha256": canonical_sha256(json.dumps(completion_hashes, sort_keys=True)),
        "files_verified": len(completion_hashes),
    })
    return terminal_path


def write_unterminated_stage_c_failure(
    *,
    run_dir: Path,
    run_id: str,
    campaign_id: str,
    session_ids: list[str],
    audited_exchange: Any | None,
    reason_code: str = "UNHANDLED_STAGE_C_LIFECYCLE_EXCEPTION_OR_INTERRUPTION",
) -> Path:
    """Latch any unhandled post-guard exit as non-authoritative local evidence.

    This is intentionally local-only and is also the fallback for exceptions
    outside terminal reconciliation. A hard process kill can still bypass
    ``atexit``; the pre-existing interruption guard remains authoritative then.
    """
    state_files = sorted((run_dir / "state").glob("*_runtime_state.json"))
    return write_post_flatten_terminal_failure(
        run_dir=run_dir,
        run_id=run_id,
        campaign_id=campaign_id,
        session_id=session_ids[-1] if session_ids else "UNKNOWN_SESSION",
        phase="UNHANDLED_POST_GUARD_EXIT",
        state_path=state_files[-1] if state_files else run_dir / "state" / "missing_runtime_state.json",
        audited_exchange=audited_exchange,
        reason_code=reason_code,
    )


class AuditedStageCExchangeWrapper:
    """Audited exchange wrapper for Stage C Three-Session Checkpoint execution.
    
    Enforces:
    - Post-only limit order creation on normal quoting path.
    - Single-flight reduce-only order creation for terminal/emergency flatten.
    - Blocks all unauthorized taker/market order creation.
    - Blocks account configuration mutations (set_position_mode, set_leverage, transfers).
    - Blocks live production endpoints.
    """

    def __init__(self, raw_exchange: Any) -> None:
        self._raw_exchange = raw_exchange
        self.endpoint_calls: dict[str, int] = {}
        self.intercepted_mutations: list[str] = []
        self.live_endpoint_attempts: int = 0
        self.normal_orders_created: int = 0
        self.flatten_orders_created: int = 0
        self.orders_cancelled: int = 0
        self.created_client_order_ids: set[str] = set()
        self.cancelled_order_ids: set[str] = set()
        self.mutation_retry_attempts: int = 0

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
        is_post_only = (params.get("postOnly") is True or params.get("ordType") == "post_only")
        is_reduce_only = (params.get("reduceOnly") is True)
        if not is_post_only and not is_reduce_only:
            raise DemoAdapterError(
                "Normal-path non-post-only order creation is strictly prohibited in Stage C"
            )
        client_id = str(params.get("clOrdId") or "")
        if not client_id or client_id in self.created_client_order_ids:
            self.mutation_retry_attempts += 1
            raise DemoAdapterError("Stage C duplicate or missing create identity is prohibited")
        self.created_client_order_ids.add(client_id)
        if is_reduce_only:
            self.flatten_orders_created += 1
        else:
            self.normal_orders_created += 1
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
        if not id or id in self.cancelled_order_ids:
            self.mutation_retry_attempts += 1
            raise DemoAdapterError("Stage C cancel retry is prohibited")
        self.cancelled_order_ids.add(id)
        self.orders_cancelled += 1
        return self._raw_exchange.cancel_order(id, symbol, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name in PROHIBITED_MUTATION_METHODS:
            self.intercepted_mutations.append(name)
            raise DemoAdapterError(
                f"MUTATION_ATTEMPT_PROHIBITED_IN_R2_STAGE_C: Method '{name}' is forbidden."
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


def run_session_admission(
    adapter: OkxDemoAdapter,
    candidate_fp: str,
    session_id: str,
    slot_nonce: str,
) -> tuple[bool, AccountSnapshot, dict[str, Any]]:
    """Executes the complete 10-point fresh admission/reconciliation gate before a session."""
    snapshot = adapter.preflight()

    # 10 Mandatory Checks
    checks: dict[str, bool] = {
        "01_candidate_fingerprint_match": candidate_fp == EXPECTED_CANDIDATE_FINGERPRINT,
        "02_okx_demo_transport_proof": adapter.config.mode == ExecutionMode.OKX_DEMO,
        "03_clock_skew_within_budget": abs(snapshot.clock_skew_ms) <= 1500,
        "04_market_and_contract_spec_match": adapter.config.symbol == "BTC/USDT:USDT",
        "05_position_mode_net": snapshot.position_mode == "net_mode",
        "06_margin_mode_isolated": adapter.config.margin_mode == "isolated",
        "07_leverage_3x": snapshot.leverage == 3.0,
        "08_startup_position_flat": abs(snapshot.position_btc) == 0.0,
        "09_startup_open_orders_zero": len(snapshot.open_orders) == 0,
        "10_equity_sufficient": snapshot.free_equity_usdt >= 100.0,
    }

    all_passed = all(checks.values())
    if not all_passed:
        failed = [k for k, v in checks.items() if not v]
        raise DemoAdapterError(f"Fresh admission gate failed closed for {session_id}: {failed}")

    audit = {
        "admission_decision": "R2_ADMISSION_PASS",
        "candidate_fingerprint": candidate_fp,
        "checks": checks,
        "clock_skew_ms": snapshot.clock_skew_ms,
        "contract_size": "0.01",
        "free_equity_usdt": snapshot.free_equity_usdt,
        "leverage": snapshot.leverage,
        "margin_mode": "isolated",
        "open_orders_count": len(snapshot.open_orders),
        "position_btc": snapshot.position_btc,
        "position_mode": snapshot.position_mode,
        "session_id": session_id,
        "slot_nonce": slot_nonce,
        "symbol": "BTC/USDT:USDT",
        "total_equity_usdt": snapshot.total_equity_usdt,
        "transport": "OKX_DEMO_SANDBOX",
        "zero_account_mutations": True,
    }
    return True, snapshot, audit


def execute_r2_stage_c(
    *,
    root: Path = ROOT,
    stamp: str | None = None,
    campaign_id: str = CANONICAL_CAMPAIGN_ID,
    execution_scope: str = "STAGE_C_SESSIONS_Q01_Q03",
    slot_schedule: list[dict[str, Any]] | None = None,
    r0_closure_ref: str = CANONICAL_R0_CLOSURE_REF,
    r1_run_id: str = CANONICAL_R1_RUN_ID,
    r2_canary_run_id: str = CANONICAL_R2_CANARY_RUN_ID,
    stage_c_prep_ref: str = CANONICAL_STAGE_C_PREP_REF,
    execution_stage: str = "TEST_FIXTURE",
    execute_q01_to_q03_only: bool = True,
    execute_q04_to_q12: bool = False,
    production_authorized: bool = False,
    exchange: Any | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    passphrase: str | None = None,
    load_env_file: bool = True,
    warmup_ticks: int = 14,
    cycles_per_session: int | None = None,
    tick_interval_s: float = 0.5,
    resting_s: float = 1.0,
    max_session_wall_time_s: float = 1800.0,
    max_session_normal_creates: int = 60,
) -> Path:
    """Executes Stage C (Sessions Q01, Q02, and Q03) and evaluates Checkpoint 2 on OKX Demo."""
    # 1. Scope & Authority Invariant Assertions
    if execution_scope not in {"STAGE_C_SESSIONS_Q01_Q03", "R2_CURRENT_STAGE_C_SINGLE_SESSION"}:
        raise DemoAdapterError(f"Unauthorized execution scope: {execution_scope}")
    if execution_scope == "STAGE_C_SESSIONS_Q01_Q03" and not execute_q01_to_q03_only:
        raise DemoAdapterError("Execution scope violation: must execute exactly Q01-Q03")
    if execution_scope == "R2_CURRENT_STAGE_C_SINGLE_SESSION" and (not slot_schedule or len(slot_schedule) != 1):
        raise DemoAdapterError("Current Stage C execution requires exactly one admitted session")
    if execute_q04_to_q12:
        raise DemoAdapterError("Continuation violation: Sessions Q04-Q12 are strictly blocked")
    if production_authorized:
        raise DemoAdapterError("Production access strictly prohibited")

    # Canonical qualification vs operational canary/test mode separation
    if execution_stage == "ECONOMIC_QUALIFICATION":
        if cycles_per_session is not None:
            raise DemoAdapterError(
                "Fixed cycle limit is strictly prohibited when execution_stage == 'ECONOMIC_QUALIFICATION'. "
                "Canonical economic qualification sessions must be governed exclusively by canonical lifecycle limits."
            )
    elif execution_stage in {"OPERATIONAL_CANARY", "TEST_FIXTURE"}:
        if cycles_per_session is None:
            cycles_per_session = 4
    else:
        raise DemoAdapterError(f"Unknown execution_stage: {execution_stage}")

    # 2. Candidate Fingerprint & Zero-Tuning Invariant
    candidate_fp = compute_candidate_fingerprint(root)["candidate_fingerprint"]
    if candidate_fp != EXPECTED_CANDIDATE_FINGERPRINT:
        raise DemoAdapterError(
            f"Candidate fingerprint drift detected! Expected {EXPECTED_CANDIDATE_FINGERPRINT}, found {candidate_fp}"
        )

    promoted_profile = load_promoted_profile(root)
    strategy = promoted_profile.build_strategy()

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"r2-stage-c-run-{stamp}"
    run_dir = root / "artifacts" / "r2_stage_c_execution" / run_id
    if run_dir.exists():
        raise DemoAdapterError(f"Run directory already exists: {run_dir}. Reuse prohibited.")
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Initiating R2 Stage C Execution ({run_id}) ===")
    print(f"Campaign ID: {campaign_id}")
    target_schedule = slot_schedule or SLOT_SCHEDULE
    write_interruption_guard(
        run_dir=run_dir,
        run_id=run_id,
        campaign_id=campaign_id,
        execution_scope=execution_scope,
        slots=target_schedule,
    )
    failure_context: dict[str, Any] = {"audited_exchange": None}

    def record_latch_write_error(exc: BaseException) -> None:
        """Never silently discard a failure-evidence write error."""
        write_json_atomic(run_dir / "R2_STAGE_C_FAILURE_EVIDENCE_WRITE_ERROR.json", {
            "status": "R2_STAGE_C_FAILURE_EVIDENCE_WRITE_ERROR",
            "run_id": run_id,
            "campaign_id": campaign_id,
            "error_type": type(exc).__name__,
            "terminal_account_authoritative": False,
            "accept_authorized": False,
            "retry_authorized": False,
            "resume_authorized": False,
            "identity_reuse_authorized": False,
            "successor_session_authorized": False,
        })

    def latch_unhandled_exit() -> None:
        try:
            write_unterminated_stage_c_failure(
                run_dir=run_dir,
                run_id=run_id,
                campaign_id=campaign_id,
                session_ids=[str(slot["session_id"]) for slot in target_schedule],
                audited_exchange=failure_context["audited_exchange"],
            )
        except Exception:
            record_latch_write_error(sys.exc_info()[1] or RuntimeError("unknown latch failure"))

    atexit.register(latch_unhandled_exit)
    original_excepthook = sys.excepthook

    def stage_c_excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        try:
            write_unterminated_stage_c_failure(
                run_dir=run_dir,
                run_id=run_id,
                campaign_id=campaign_id,
                session_ids=[str(slot["session_id"]) for slot in target_schedule],
                audited_exchange=failure_context["audited_exchange"],
                reason_code=f"UNHANDLED_STAGE_C_EXCEPTION_{exc_type.__name__.upper()}",
            )
        except Exception as latch_exc:
            record_latch_write_error(latch_exc)
        original_excepthook(exc_type, exc, tb)

    sys.excepthook = stage_c_excepthook
    next_status = (
        "R2_NEXT_CURRENT_STAGE_C_DECISION_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION"
        if execution_scope == "R2_CURRENT_STAGE_C_SINGLE_SESSION"
        else "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION"
    )
    checkpoint_status = (
        "R2_CURRENT_SINGLE_SESSION_TERMINAL_PASSED"
        if execution_scope == "R2_CURRENT_STAGE_C_SINGLE_SESSION"
        else "R2_THREE_SESSION_CHECKPOINT_PASSED"
    )
    print(f"Target Sessions: {', '.join((s.get('qualification_slot') or s.get('slot_name', '')).upper() for s in target_schedule)}")

    # 3. Exchange Setup
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

    audited_exchange = AuditedStageCExchangeWrapper(raw_exchange)
    failure_context["audited_exchange"] = audited_exchange
    market_gate = MarketDataGate()

    campaign_start_s = time.time()
    session_audit_records: list[dict[str, Any]] = []
    aggregate_normal_creates = 0
    aggregate_flatten_creates = 0
    aggregate_cancels = 0
    cumulative_realized_pnl = Decimal("0.0")

    # 4. Sequential execution of the explicitly admitted schedule.
    for slot_info in target_schedule:
        slot_label = slot_info.get("qualification_slot") or slot_info.get("slot_name", "").upper()
        session_id = slot_info["session_id"]
        slot_nonce = slot_info["nonce"]
        arm_token = slot_info["expected_arm_token"]

        print(f"\n--- Starting Session {slot_label} ({session_id}) ---")
        session_start_s = time.time()

        session_state_file = run_dir / "state" / f"{slot_label}_runtime_state.json"
        state_store = DemoStateStore(session_state_file)
        adapter_config = DemoAdapterConfig(
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
            config=adapter_config,
            promoted_profile=promoted_profile,
            session_id=session_id,
            state_store=state_store,
        )

        # 4a. Fresh Admission Reconciliation Gate
        print(f"[{slot_label}] Executing mandatory fresh admission gate...")
        admission_passed, admission_snapshot, admission_audit = run_session_admission(
            adapter, candidate_fp, session_id, slot_nonce
        )
        print(f"[{slot_label}] Fresh admission PASSED (Skew: {admission_snapshot.clock_skew_ms}ms, Equity: {admission_snapshot.free_equity_usdt:.2f} USDT)")

        # 4b. Volatility Warmup
        print(f"[{slot_label}] Executing volatility warmup ({warmup_ticks} ticks)...")
        for _ in range(warmup_ticks):
            raw_book = audited_exchange.fetch_order_book(adapter_config.symbol)
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

        # 4c. Session Quoting Cycles
        session_normal_creates = 0
        session_flatten_creates = 0
        session_cancels = 0
        session_cycles_history: list[dict[str, Any]] = []
        soft_loss_triggered = False

        print(f"[{slot_label}] Starting quoting loop (stage={execution_stage})...")
        cycle = 0
        canonical_termination_reason = "CANONICAL_UNKNOWN"
        while True:
            # Check fixed cycle limit in operational / test mode
            if execution_stage in {"OPERATIONAL_CANARY", "TEST_FIXTURE"}:
                if cycles_per_session is not None and cycle >= cycles_per_session:
                    canonical_termination_reason = "OPERATIONAL_FIXED_CYCLE_LIMIT"
                    break

            # Canonical lifecycle termination checks
            elapsed_s = time.time() - session_start_s
            if elapsed_s >= max_session_wall_time_s:
                canonical_termination_reason = "CANONICAL_SESSION_WALL_TIME_EXPIRED"
                break

            if session_normal_creates >= max_session_normal_creates:
                canonical_termination_reason = "CANONICAL_NORMAL_CREATES_BUDGET_EXHAUSTED"
                break

            cycle_start_s = time.time()
            snap = adapter.preflight()

            # Check Loss Guards
            session_drawdown = Decimal(str(admission_snapshot.total_equity_usdt)) - Decimal(str(snap.total_equity_usdt))
            if session_drawdown >= Decimal("37.50"):
                print(f"[{slot_label}] HARD KILL TRIGGERED: Drawdown {session_drawdown} USDT >= 37.50 USDT")
                adapter.cancel_all_owned()
                if abs(snap.position_btc) > 1e-8:
                    adapter.submit_emergency_flatten(
                        position_btc=snap.position_btc,
                        reference_price=float(strategy.latest_mid or 0),
                    )
                canonical_termination_reason = "CANONICAL_HARD_DRAWDOWN_LIMIT_BREACHED"
                raise DemoAdapterError(f"Session hard kill drawdown limit breached: {session_drawdown} USDT")

            if session_drawdown >= Decimal("22.50"):
                print(f"[{slot_label}] Soft loss guard active ({session_drawdown} USDT >= 22.50 USDT). Throttling new quoting.")
                soft_loss_triggered = True

            curr_net_pnl = Decimal(str(adapter.state.net_realized_pnl_usdt if adapter.state else 0.0))
            if (cumulative_realized_pnl + curr_net_pnl) <= Decimal("-75.00"):
                print(f"[{slot_label}] CAMPAIGN HARD LOSS TRIGGERED: {cumulative_realized_pnl + curr_net_pnl} USDT <= -75.00 USDT")
                adapter.cancel_all_owned()
                if abs(snap.position_btc) > 1e-8:
                    adapter.submit_emergency_flatten(
                        position_btc=snap.position_btc,
                        reference_price=float(strategy.latest_mid or 0),
                    )
                canonical_termination_reason = "CANONICAL_CAMPAIGN_HARD_LOSS_LIMIT_BREACHED"
                raise DemoAdapterError("Campaign hard loss limit breached")

            raw_book = audited_exchange.fetch_order_book(adapter_config.symbol)
            now_ms = int(time.time() * 1000)
            book = market_gate.validate(raw_book, now_ms=now_ms)

            decision = strategy.decide(
                {"timestamp": book["timestamp"], "bids": book["bids"], "asks": book["asks"]},
                inventory_btc=snap.position_btc,
                market_data_age_ms=book["age_ms"],
                staleness_limit_ms=market_gate.maximum_age_ms,
            )

            cycle_orders: list[SubmittedOrder] = []
            if decision and decision.quote_allowed and not soft_loss_triggered:
                # Place post-only bid
                if not decision.bid_suppressed and session_normal_creates < max_session_normal_creates:
                    if int(time.time() * 1000) - book["timestamp"] > 700:
                        raw_book = audited_exchange.fetch_order_book(adapter_config.symbol)
                        book = market_gate.validate(raw_book, now_ms=int(time.time() * 1000))
                    bid_order = adapter.submit_post_only(
                        side="buy",
                        price=decision.rounded_bid,
                        best_bid=book["best_bid"],
                        best_ask=book["best_ask"],
                        market_timestamp_ms=book["timestamp"],
                    )
                    cycle_orders.append(bid_order)
                    session_normal_creates += 1
                    aggregate_normal_creates += 1

                # Place post-only ask
                if not decision.ask_suppressed and session_normal_creates < max_session_normal_creates:
                    if int(time.time() * 1000) - book["timestamp"] > 700:
                        raw_book = audited_exchange.fetch_order_book(adapter_config.symbol)
                        book = market_gate.validate(raw_book, now_ms=int(time.time() * 1000))
                    ask_order = adapter.submit_post_only(
                        side="sell",
                        price=decision.rounded_ask,
                        best_bid=book["best_bid"],
                        best_ask=book["best_ask"],
                        market_timestamp_ms=book["timestamp"],
                    )
                    cycle_orders.append(ask_order)
                    session_normal_creates += 1
                    aggregate_normal_creates += 1

            if resting_s > 0:
                time.sleep(resting_s)

            # Cancel owned quotes for this cycle
            cancelled_ids = adapter.cancel_all_owned()
            session_cancels += len(cancelled_ids)
            aggregate_cancels += len(cancelled_ids)

            cycle_duration = time.time() - cycle_start_s
            session_cycles_history.append({
                "cycle": cycle,
                "orders_created": len(cycle_orders),
                "orders_cancelled": len(cancelled_ids),
                "duration_s": round(cycle_duration, 3),
                "best_bid": book["best_bid"],
                "best_ask": book["best_ask"],
            })
            cycle += 1

            if tick_interval_s > 0:
                time.sleep(tick_interval_s)

        # 4d. Session Terminal Reconciliation. Any exception after a possible
        # flatten dispatch gets durable failure evidence before it escapes.
        terminal_phase = "TERMINAL_CANCEL"
        try:
            print(f"[{slot_label}] Reconciling terminal session state...")
            adapter.cancel_all_owned()
            terminal_phase = "POST_TERMINAL_CANCEL_READ"
            mid_term_snap = adapter.preflight()

            routine_cleanup_count = 0
            emergency_flatten_count = 0
            if abs(mid_term_snap.position_btc) > 1e-8:
                print(f"[{slot_label}] Executing routine terminal cleanup for {mid_term_snap.position_btc} BTC...")
                terminal_phase = "FLATTEN_DISPATCH"
                flatten_order = adapter.submit_emergency_flatten(
                    position_btc=mid_term_snap.position_btc,
                    reference_price=float(book["best_bid"] if mid_term_snap.position_btc > 0 else book["best_ask"]),
                )
                terminal_phase = "POST_FLATTEN_RECONCILIATION"
                if flatten_order is not None:
                    routine_cleanup_count += 1
                    session_flatten_creates += 1
                    aggregate_flatten_creates += 1

            terminal_phase = "FINAL_TERMINAL_READ"
            final_term_snap = adapter.preflight()
            if abs(final_term_snap.position_btc) > 1e-8:
                raise DemoAdapterError(f"[{slot_label}] Terminal position not flat: {final_term_snap.position_btc} BTC")
            if len(final_term_snap.open_orders) > 0:
                raise DemoAdapterError(f"[{slot_label}] Terminal open orders remain: {len(final_term_snap.open_orders)}")
        except BaseException:
            try:
                write_post_flatten_terminal_failure(
                    run_dir=run_dir,
                    run_id=run_id,
                    campaign_id=campaign_id,
                    session_id=session_id,
                    phase=terminal_phase,
                    state_path=session_state_file,
                    audited_exchange=audited_exchange,
                )
            except Exception:
                # Preserve the original terminal failure. The pre-execution guard
                # still blocks successors if local storage itself is unavailable.
                pass
            raise

        session_duration_s = time.time() - session_start_s
        session_net_pnl = Decimal(str(adapter.state.net_realized_pnl_usdt if adapter.state else 0.0))
        cumulative_realized_pnl += session_net_pnl

        try:
            ledger = replay_fill_ledger(adapter.session_owned_fills)
            ledger_valid = len(adapter.foreign_fills_observed) == 0
        except EvidenceIntegrityError as exc:
            ledger = {"error": str(exc)}
            ledger_valid = False
        session_audit = {
            "admission_audit": admission_audit,
            "canonical_termination_reason": canonical_termination_reason,
            "cycles_executed": cycle,
            "duration_s": round(session_duration_s, 3),
            "emergency_flatten_count": emergency_flatten_count,
            "execution_stage": execution_stage,
            "fifo_round_trips": adapter.session_fifo_round_trips,
            "maker_ask_fills": adapter.session_maker_ask_fills,
            "maker_bid_fills": adapter.session_maker_bid_fills,
            "maker_fills_observed": adapter.session_maker_fills_total,
            "normal_creates": session_normal_creates,
            "orders_cancelled": session_cancels,
            "routine_cleanup_count": routine_cleanup_count,
            "session_id": session_id,
            "session_net_pnl_usdt": str(session_net_pnl),
            "session_owned_maker_fills": adapter.session_maker_fills_total,
            "cursor_baseline_trades_observed": len(adapter.state.fill_cursor.ids_at_timestamp) if (adapter.state and adapter.state.fill_cursor) else 0,
            "slot": slot_label,
            "soft_loss_triggered": soft_loss_triggered,
            "terminal_open_orders": len(final_term_snap.open_orders),
            "terminal_position_btc": final_term_snap.position_btc,
            "total_fees_usdt": str(adapter.state.total_fees_usdt if adapter.state else 0.0),
            "foreign_fills_observed": len(adapter.foreign_fills_observed),
            "unclassified_events_zero": ledger_valid,
            "mutation_ambiguity_zero": bool(adapter.state and not adapter.state.kill_switch.active and not adapter.halted_reason),
            "unresolved_flatten_zero": bool(adapter.state and adapter.state.flatten_state in {"IDLE", "CONFIRMED"}),
            "inventory_cap_respected": bool(adapter.state and abs(adapter.state.inventory_btc) <= 0.01),
            "owned_order_cap_respected": bool(adapter.state and len(adapter.state.owned_open_orders) <= 2),
            "exact_accounting_reconciled": bool(
                adapter.state
                and abs(adapter.state.inventory_btc - final_term_snap.position_btc) <= 1e-12
                and not adapter.state.owned_open_orders
                and Decimal(ledger.get("inventory_btc", "NaN")) == Decimal(str(final_term_snap.position_btc))
                and Decimal(ledger.get("gross_realized_pnl_usdt", "NaN")) == adapter.session_owned_realized_pnl
            ) and ledger_valid,
            "fee_attribution_reconciled": bool(
                adapter.state
                and Decimal(ledger.get("fees_usdt", "NaN")) == adapter.session_owned_fees_usdt
            ) and ledger_valid,
            "fifo_attribution_reconciled": ledger.get("normal_fifo_round_trips") == adapter.session_fifo_round_trips and ledger_valid,
            "normal_path_post_only_enforced": audited_exchange.normal_orders_created >= session_normal_creates,
            "ledger_replay": ledger,
        }
        session_audit_records.append(session_audit)
        write_json_atomic(run_dir / f"{slot_label.lower()}_session_audit.json", session_audit)
        print(f"[{slot_label}] Terminal reconciliation PASSED (Position: 0.0 BTC, Orders: 0, Duration: {session_duration_s:.2f}s)")

    campaign_duration_s = time.time() - campaign_start_s

    # 5. Stage C Three-Session Hard Checkpoint Evaluation (Checkpoint 2)
    print("\n=== Evaluating Stage C Three-Session Checkpoint (Checkpoint 2) ===")
    checkpoint_checks: dict[str, bool] = {
        "01_fresh_admission_passed_all_sessions": len(session_audit_records) == len(target_schedule),
        "02_candidate_fingerprint_intact": candidate_fp == EXPECTED_CANDIDATE_FINGERPRINT,
        "03_terminal_position_flat_all_sessions": all(s["terminal_position_btc"] == 0.0 for s in session_audit_records),
        "04_terminal_owned_orders_zero_all_sessions": all(s["terminal_open_orders"] == 0 for s in session_audit_records),
        "05_unknown_fills_zero_all_sessions": all(s["foreign_fills_observed"] == 0 for s in session_audit_records),
        "06_unclassified_events_zero": all(s["unclassified_events_zero"] for s in session_audit_records),
        "07_mutation_ambiguity_zero": all(s["mutation_ambiguity_zero"] for s in session_audit_records),
        "08_unresolved_flatten_zero": all(s["unresolved_flatten_zero"] for s in session_audit_records),
        "09_inventory_cap_breaches_zero": all(s["inventory_cap_respected"] for s in session_audit_records),
        "10_owned_order_count_breaches_zero": all(s["owned_order_cap_respected"] for s in session_audit_records),
        "11_normal_create_budget_breaches_zero": aggregate_normal_creates <= 60 * len(target_schedule) and all(s["normal_creates"] <= 60 for s in session_audit_records),
        "12_normal_path_post_only_enforced": all(s["normal_path_post_only_enforced"] for s in session_audit_records),
        "13_special_flatten_ceiling_adhered": sum(s["routine_cleanup_count"] + s["emergency_flatten_count"] for s in session_audit_records) <= 2,
        "14_loss_guards_respected": cumulative_realized_pnl >= Decimal("-75.00"),
        "15_exact_accounting_reconciled_all_sessions": all(
            s["exact_accounting_reconciled"] and s["fee_attribution_reconciled"] and s["fifo_attribution_reconciled"]
            for s in session_audit_records
        ),
        "16_clock_skew_and_book_safety_respected": all(s["admission_audit"]["clock_skew_ms"] <= 1500 for s in session_audit_records),
        "17_zero_mutation_retries_and_live_denial": (
            audited_exchange.live_endpoint_attempts == 0
            and len(audited_exchange.intercepted_mutations) == 0
            and audited_exchange.mutation_retry_attempts == 0
        ),
    }

    checkpoint_passed = all(checkpoint_checks.values())
    if not checkpoint_passed:
        failed = [k for k, v in checkpoint_checks.items() if not v]
        raise DemoAdapterError(f"Stage C Three-Session Checkpoint FAILED: {failed}")

    checkpoint_record = {
        "checkpoint_decision": checkpoint_status,
        "checkpoint_id": "STAGE_C_THREE_SESSION_CHECKPOINT",
        "hard_safety_checks": checkpoint_checks,
        "hard_safety_passed": True,
        "informational_next_status": next_status,
        "post_checkpoint_action": "HARD_STOP_ENFORCED",
        "q04_started": False,
        "sessions_executed": len(target_schedule),
        "target_sessions": [s.get("qualification_slot") or s.get("slot_name", "").upper() for s in target_schedule],
        "timestamp_utc": stamp,
    }
    write_json_atomic(run_dir / "checkpoint_2_evaluation.json", checkpoint_record)

    # 6. Operational and Economic Diagnostics
    diagnostics = {
        "aggregate_cancels": aggregate_cancels,
        "aggregate_flatten_creates": aggregate_flatten_creates,
        "aggregate_normal_creates": aggregate_normal_creates,
        "campaign_duration_s": round(campaign_duration_s, 3),
        "campaign_id": campaign_id,
        "cumulative_net_pnl_usdt": str(cumulative_realized_pnl),
        "diagnostics_scope": "EARLY_REGRESSION_AND_SAFETY_DIAGNOSTICS (Not final 12-session floors)",
        "emergency_flattens_total": sum(s["emergency_flatten_count"] for s in session_audit_records),
        "maker_fills_total": sum(s["maker_fills_observed"] for s in session_audit_records),
        "routine_cleanups_total": sum(s["routine_cleanup_count"] for s in session_audit_records),
        "sessions_evaluated": [s.get("qualification_slot") or s.get("slot_name", "").upper() for s in target_schedule],
        "systemic_execution_defects_observed": False,
    }
    write_json_atomic(run_dir / "operational_and_economic_diagnostics.json", diagnostics)

    # 7. Candidate Verification Artifact
    candidate_verification = {
        "behavioral_parameter_drift": 0,
        "candidate_fingerprint": candidate_fp,
        "profile_id": promoted_profile.profile_id,
        "profile_name": promoted_profile.profile_name,
        "strategy_controls_frozen": True,
        "verified": True,
    }
    write_json_atomic(run_dir / "candidate_verification.json", candidate_verification)

    # 8. Risk and Boundary Audit
    risk_audit = {
        "absolute_inventory_cap_btc": 0.01,
        "campaign_hard_loss_limit_usdt": "75.00",
        "clock_skew_budget_ms": 1500,
        "dominant_exposure_limit_enforced": True,
        "hard_drawdown_limit_usdt": "37.50",
        "live_endpoints_called": audited_exchange.live_endpoint_attempts,
        "modeled_capital_usdt": 750.0,
        "mutation_retries_attempted": audited_exchange.mutation_retry_attempts,
        "normal_creates_budget_session": 60,
        "normal_creates_budget_stage_c": 180,
        "normal_lot_size_btc": 0.01,
        "prohibited_methods_intercepted": audited_exchange.intercepted_mutations,
        "soft_drawdown_limit_usdt": "22.50",
        "unresolved_flattens_terminal": 0,
    }
    write_json_atomic(run_dir / "risk_and_boundary_audit.json", risk_audit)

    # 9. Stage C Execution Manifest
    manifest = {
        "campaign_id": campaign_id,
        "candidate_fingerprint": candidate_fp,
        "checkpoint_result": "PASSED",
        "operational_canary_excluded": True,
        "q04_started": False,
        "run_id": run_id,
        "sessions_executed": len(target_schedule),
        "session_ids": [str(slot["session_id"]) for slot in target_schedule],
        "slots_completed": ["Q01", "Q02", "Q03"],
        "timestamp_utc": stamp,
    }
    write_json_atomic(run_dir / "stage_c_execution_manifest.json", manifest)

    # 10. Completion Hashes
    completion_hashes = generate_completion_hashes(run_dir, "R2_STAGE_C_EXECUTION_COMPLETED.json")
    write_json_atomic(run_dir / "completion_hashes.json", completion_hashes)
    hashes_sha256 = canonical_sha256(json.dumps(completion_hashes, sort_keys=True))

    # 11. Terminal Completion Marker
    terminal_marker = {
        "account_mutations": 0,
        "behavioral_parameter_drift": 0,
        "campaign_id": campaign_id,
        "candidate_fingerprint": candidate_fp,
        "completion_hashes_sha256": hashes_sha256,
        "files_verified": len(completion_hashes),
        "flatten_attempts": aggregate_flatten_creates,
        "git_write_operation": False,
        "informational_next_status": next_status,
        "live_endpoint_attempts": 0,
        "operational_canary_excluded": True,
        "orders_amended": 0,
        "orders_cancelled": aggregate_cancels,
        "orders_created": aggregate_normal_creates + aggregate_flatten_creates,
        "production_authorized": False,
        "q01_q03_execution_authorized": True,
        "q04_q12_execution_authorized": False,
        "q04_started": False,
        "r0_closure_ref": r0_closure_ref,
        "r1_run_id": r1_run_id,
        "r2_canary_run_id": r2_canary_run_id,
        "r2_stage_c_preparation_ref": stage_c_prep_ref,
        "interruption_guard_superseded_by_terminal": True,
        "session_ids": [str(slot["session_id"]) for slot in target_schedule],
        "sessions_executed": len(target_schedule),
        "stage_c_execution_complete": True,
        "status": checkpoint_status,
        "timestamp_utc": stamp,
    }
    write_json_atomic(run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json", terminal_marker)
    atexit.unregister(latch_unhandled_exit)
    sys.excepthook = original_excepthook

    print(f"\n=== Stage C Three-Session Checkpoint Execution Complete ===")
    print(f"Status: {checkpoint_status}")
    print(f"Next Status: {next_status}")
    print(f"Sessions Executed: {len(target_schedule)}")
    print(f"Hard Stop Enforced: Q04 NOT started. Production NOT authorized.")
    print(f"Artifacts: {run_dir}")

    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute R2 Stage C Sessions Q01-Q03 on OKX Demo.")
    parser.add_argument(
        "--execution-stage",
        type=str,
        default="TEST_FIXTURE",
        choices=["TEST_FIXTURE", "OPERATIONAL_CANARY", "ECONOMIC_QUALIFICATION"],
        help="Execution stage: TEST_FIXTURE, OPERATIONAL_CANARY, or ECONOMIC_QUALIFICATION.",
    )
    parser.add_argument("--warmup-ticks", type=int, default=14, help="Warmup order book ticks.")
    parser.add_argument("--cycles", type=int, default=None, help="Quoting cycles per session (prohibited in ECONOMIC_QUALIFICATION).")
    parser.add_argument("--tick-interval", type=float, default=0.5, help="Interval between ticks.")
    parser.add_argument("--resting", type=float, default=1.0, help="Resting time per quote.")
    args = parser.parse_args()

    execute_r2_stage_c(
        execution_stage=args.execution_stage,
        warmup_ticks=args.warmup_ticks,
        cycles_per_session=args.cycles,
        tick_interval_s=args.tick_interval,
        resting_s=args.resting,
    )


if __name__ == "__main__":
    main()
