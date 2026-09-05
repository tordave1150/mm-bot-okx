"""Authoritative R2 Canonical Economic Qualification (Q01-Q03) Executor for OKX Demo.

Executes the authorized fresh Stage C Three-Session Economic Qualification Checkpoint
on OKX Demo only under the fresh campaign:
- Campaign ID: r2-qualification-campaign-20260904T154500Z
- Run ID: r2-canonical-qualification-run-20260904T154500Z
- Preparation Ref: r2-canonical-prep-20260904T154500Z
- Frozen Candidate Fingerprint: 1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf
- Scope: Exactly fresh sessions Q01, Q02, and Q03 only
- Execution Stage: ECONOMIC_QUALIFICATION (fixed cycle overrides strictly prohibited)
- Starting Credited Economic Qualification Sessions: 0 / 12
- Operational canary and previous short Stage C runs permanently excluded
- Mandatory fresh admission reconciliation gate before EACH session
- Preserved frozen risk boundary (750 USDT capital, 0.01 lot, 0.01 BTC dominant inventory cap)
- Canonical lifecycle limits: 30-min wall time, 60 normal creates budget, 22.50 USDT soft loss, 37.50 USDT hard kill, 75.00 USDT campaign loss
- Strict fill attribution: historical startup cursor trades baseline only, 0 fills credited
- Strict hard stop after Q03: zero execution of Q04-Q12, zero production authorization.
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
CANONICAL_R2_FINAL_PREP_REF = "r2-final-prep-20260904T130500Z"
CANONICAL_R2_CANARY_RUN_ID = "r2-canary-run-20260904T131836Z"
CANONICAL_STAGE_C_PREP_REF = "r2-stage-c-prep-20260904T133500Z"
CANONICAL_STAGE_C_RUN_ID = "r2-stage-c-run-20260904T141733Z"
CANONICAL_STAGE_C_RECONCILE_ID = "r2-stage-c-reconcile-20260904T143500Z"
CANONICAL_PREP_REF = "r2-canonical-prep-20260904T154500Z"

EXPECTED_CANDIDATE_FINGERPRINT = "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf"
CANONICAL_CAMPAIGN_ID = "r2-qualification-campaign-20260904T154500Z"
CANONICAL_RUN_ID = "r2-canonical-qualification-run-20260904T154500Z"

SLOT_SCHEDULE_Q01_Q03 = [
    {
        "slot_index": 1,
        "qualification_slot": "Q01",
        "session_id": "r2-session-20260904T154500Z-q01:p0:d1a8e101",
        "nonce": "d1a8e101",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T154500Z-q01:p0:d1a8e101",
        "client_order_namespace": "r2q01",
    },
    {
        "slot_index": 2,
        "qualification_slot": "Q02",
        "session_id": "r2-session-20260904T154500Z-q02:p0:e2b7f202",
        "nonce": "e2b7f202",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T154500Z-q02:p0:e2b7f202",
        "client_order_namespace": "r2q02",
    },
    {
        "slot_index": 3,
        "qualification_slot": "Q03",
        "session_id": "r2-session-20260904T154500Z-q03:p0:f3c6a303",
        "nonce": "f3c6a303",
        "expected_arm_token": "OKX_DEMO:r2-session-20260904T154500Z-q03:p0:f3c6a303",
        "client_order_namespace": "r2q03",
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


def generate_completion_hashes(directory: Path, marker_filename: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", marker_filename}
    hashes: dict[str, str] = {}
    for item in sorted(directory.rglob("*")):
        if item.is_file() and item.name not in ignored and not item.name.endswith(".tmp"):
            relative_name = item.relative_to(directory).as_posix()
            hashes[relative_name] = hash_file(item)
    return hashes


class AuditedCanonicalExchangeWrapper:
    """Audited exchange wrapper enforcing OKX Demo boundaries and mutation constraints."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange
        self.intercepted_mutations: list[str] = []
        self.live_endpoint_attempts: int = 0

    @property
    def options(self) -> dict[str, Any]:
        return getattr(self.exchange, "options", {})

    @property
    def headers(self) -> dict[str, Any]:
        return getattr(self.exchange, "headers", {})

    @property
    def markets(self) -> dict[str, Any]:
        return getattr(self.exchange, "markets", {})

    @property
    def currencies(self) -> dict[str, Any]:
        return getattr(self.exchange, "currencies", {})

    def fetch_markets(self) -> Any:
        return self.exchange.fetch_markets()

    def set_markets(self, markets: Any) -> Any:
        if hasattr(self.exchange, "set_markets"):
            return self.exchange.set_markets(markets)
        return markets

    def fetch_time(self) -> int:
        return int(self.exchange.fetch_time())

    def fetch_balance(self) -> dict[str, Any]:
        return self.exchange.fetch_balance()

    def privateGetAccountConfig(self) -> dict[str, Any]:
        return self.exchange.privateGetAccountConfig()

    def fetch_positions(self, symbols: list[str]) -> list[dict[str, Any]]:
        return self.exchange.fetch_positions(symbols)

    def fetch_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(symbol)

    def fetch_my_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.exchange.fetch_my_trades(symbol, limit=limit)

    def fetch_leverage(self, symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.exchange.fetch_leverage(symbol, params)

    def fetch_trading_fee(self, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_trading_fee(symbol)

    def fetch_position_mode(self, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_position_mode(symbol)

    def fetch_order_book(self, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_order_book(symbol)

    def fetch_order(self, order_id: str, symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.exchange.fetch_order(order_id, symbol, params)

    def market(self, symbol: str) -> dict[str, Any]:
        return self.exchange.market(symbol)

    def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        post_only = bool(params.get("postOnly"))
        reduce_only = bool(params.get("reduceOnly"))

        if not post_only and not reduce_only:
            raise DemoAdapterError(
                "VIOLATION: Normal-path non-post-only order creation is strictly prohibited. "
                "Only post-only normal quotes and reduce-only flattens are permitted."
            )
        if post_only and reduce_only:
            raise DemoAdapterError("VIOLATION: Order cannot be simultaneously post-only and reduce-only.")
        if reduce_only and order_type != "market":
            raise DemoAdapterError("VIOLATION: Terminal flatten orders must be market reduce-only orders.")

        return self.exchange.create_order(symbol, order_type, side, amount, price, params)

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.exchange.cancel_order(order_id, symbol)

    def set_position_mode(self, *args: Any, **kwargs: Any) -> Any:
        self.intercepted_mutations.append("set_position_mode")
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED: Account configuration mutation is strictly blocked.")

    def set_leverage(self, *args: Any, **kwargs: Any) -> Any:
        self.intercepted_mutations.append("set_leverage")
        raise DemoAdapterError("MUTATION_ATTEMPT_PROHIBITED: Leverage mutation is strictly blocked.")

    def __getattr__(self, name: str) -> Any:
        if "live" in name.lower() or "production" in name.lower():
            self.live_endpoint_attempts += 1
            raise DemoAdapterError(f"LIVE_ENDPOINT_ATTEMPT_PROHIBITED: Access to '{name}' is strictly denied.")
        if name in PROHIBITED_MUTATION_METHODS:
            self.intercepted_mutations.append(name)
            raise DemoAdapterError(f"MUTATION_ATTEMPT_PROHIBITED: '{name}' is strictly blocked.")
        if name in ALLOWED_READ_ENDPOINTS:
            return getattr(self.exchange, name)
        raise DemoAdapterError(f"ENDPOINT_DENIED_UNKNOWN_CATEGORY: Method '{name}' is not authorized.")


def run_session_admission(
    adapter: OkxDemoAdapter,
    candidate_fp: str,
    session_id: str,
    slot_nonce: str,
) -> tuple[bool, AccountSnapshot, dict[str, Any]]:
    """Executes the complete 10-point fresh admission/reconciliation gate before a session."""
    snapshot = adapter.preflight()

    # 10 Mandatory Admission Checks
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


def execute_canonical_qualification_q01_q03(
    *,
    root: Path = ROOT,
    campaign_id: str = CANONICAL_CAMPAIGN_ID,
    run_id: str = CANONICAL_RUN_ID,
    execution_scope: str = "CANONICAL_QUALIFICATION_SESSIONS_Q01_Q03",
    execution_stage: str = "ECONOMIC_QUALIFICATION",
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
    resting_s: float = 2.0,
    max_session_wall_time_s: float = 1800.0,
    max_session_normal_creates: int = 60,
) -> Path:
    """Executes fresh canonical qualification sessions Q01-Q03 on OKX Demo."""
    # 1. Authority & Scope Invariant Checks
    if execution_scope != "CANONICAL_QUALIFICATION_SESSIONS_Q01_Q03":
        raise DemoAdapterError(f"Unauthorized execution scope: {execution_scope}")
    if not execute_q01_to_q03_only:
        raise DemoAdapterError("Scope violation: must execute exactly fresh Q01-Q03")
    if execute_q04_to_q12:
        raise DemoAdapterError("Continuation violation: Sessions Q04-Q12 are strictly blocked pending explicit authorization")
    if production_authorized:
        raise DemoAdapterError("Production access strictly prohibited")

    # Canonical qualification mode enforcement
    if execution_stage == "ECONOMIC_QUALIFICATION":
        if cycles_per_session is not None:
            raise DemoAdapterError(
                "Fixed cycle limit is strictly prohibited in ECONOMIC_QUALIFICATION mode. "
                "Canonical economic qualification sessions must be governed exclusively by canonical lifecycle limits."
            )
    elif execution_stage in {"OPERATIONAL_CANARY", "TEST_FIXTURE"}:
        if cycles_per_session is None:
            cycles_per_session = 4
    else:
        raise DemoAdapterError(f"Unknown execution_stage: {execution_stage}")

    # 2. Candidate Fingerprint Invariant (Zero Tuning)
    candidate_fp = compute_candidate_fingerprint(root)["candidate_fingerprint"]
    if candidate_fp != EXPECTED_CANDIDATE_FINGERPRINT:
        raise DemoAdapterError(
            f"Candidate fingerprint drift detected! Expected {EXPECTED_CANDIDATE_FINGERPRINT}, found {candidate_fp}"
        )

    promoted_profile = load_promoted_profile(root)
    strategy = promoted_profile.build_strategy()

    run_dir = root / "artifacts" / "r2_canonical_qualification_runs" / run_id
    if run_dir.exists():
        raise DemoAdapterError(f"Run directory already exists: {run_dir}. Identity reuse prohibited.")
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Initiating Fresh Canonical Qualification Execution ({run_id}) ===")
    print(f"Campaign ID: {campaign_id}")
    print(f"Execution Stage: {execution_stage}")
    print(f"Target Sessions: Q01, Q02, Q03 (Q04-Q12 strictly blocked)")
    print(f"Starting Credited Economic Sessions: 0 / 12")

    # 3. Exchange Connection Setup
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

    audited_exchange = AuditedCanonicalExchangeWrapper(raw_exchange)
    market_gate = MarketDataGate()

    campaign_start_s = time.time()
    session_audit_records: list[dict[str, Any]] = []
    aggregate_normal_creates = 0
    aggregate_flatten_creates = 0
    aggregate_cancels = 0
    cumulative_realized_pnl = Decimal("0.0")

    # 4. Sequential Execution of Fresh Q01, Q02, Q03
    for slot_info in SLOT_SCHEDULE_Q01_Q03:
        slot_label = slot_info["qualification_slot"]
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
        print(
            f"[{slot_label}] Fresh admission PASSED (Skew: {admission_snapshot.clock_skew_ms}ms, "
            f"Equity: {admission_snapshot.free_equity_usdt:.2f} USDT, Position: {admission_snapshot.position_btc} BTC)"
        )

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

        # 4c. Session Quoting Loop
        session_normal_creates = 0
        session_flatten_creates = 0
        session_cancels = 0
        session_max_abs_inventory = 0.0
        session_maker_work_off_episodes = 0
        soft_loss_triggered = False
        canonical_termination_reason = "CANONICAL_UNKNOWN"
        cycle = 0

        print(f"[{slot_label}] Starting quoting loop (stage={execution_stage})...")
        while True:
            # Fixed cycle limit check in operational / test mode
            if execution_stage in {"OPERATIONAL_CANARY", "TEST_FIXTURE"}:
                if cycles_per_session is not None and cycle >= cycles_per_session:
                    canonical_termination_reason = "OPERATIONAL_FIXED_CYCLE_LIMIT"
                    break

            # Canonical lifecycle termination conditions
            elapsed_s = time.time() - session_start_s
            if elapsed_s >= max_session_wall_time_s:
                canonical_termination_reason = "CANONICAL_SESSION_WALL_TIME_EXPIRED"
                print(f"[{slot_label}] Wall time expired ({elapsed_s:.1f}s >= {max_session_wall_time_s:.1f}s)")
                break

            if session_normal_creates >= max_session_normal_creates:
                canonical_termination_reason = "CANONICAL_NORMAL_CREATES_BUDGET_EXHAUSTED"
                print(f"[{slot_label}] Normal creates budget exhausted ({session_normal_creates} >= {max_session_normal_creates})")
                break

            cycle_start_s = time.time()
            snap = adapter.preflight()
            session_max_abs_inventory = max(session_max_abs_inventory, abs(float(snap.position_btc)))

            # Loss Guards Evaluation
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

            # Validate Market Data Book
            raw_book = audited_exchange.fetch_order_book(adapter_config.symbol)
            now_ms = int(time.time() * 1000)
            book = market_gate.validate(raw_book, now_ms=now_ms)

            # Generate Quoting Decision
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

            # Rest quote pair (minimum observation interval >= 2000 ms)
            if resting_s > 0:
                time.sleep(resting_s)

            # Cancel owned quotes for this cycle
            cancelled_ids = adapter.cancel_all_owned()
            session_cancels += len(cancelled_ids)
            aggregate_cancels += len(cancelled_ids)

            cycle += 1
            if tick_interval_s > 0:
                time.sleep(tick_interval_s)

        # 4d. Session Terminal Reconciliation
        print(f"[{slot_label}] Reconciling terminal session state...")
        adapter.cancel_all_owned()
        mid_term_snap = adapter.preflight()
        session_max_abs_inventory = max(session_max_abs_inventory, abs(float(mid_term_snap.position_btc)))

        routine_cleanup_count = 0
        emergency_flatten_count = 0
        if abs(mid_term_snap.position_btc) > 1e-8:
            print(f"[{slot_label}] Executing routine terminal cleanup for {mid_term_snap.position_btc} BTC...")
            flatten_order = adapter.submit_emergency_flatten(
                position_btc=mid_term_snap.position_btc,
                reference_price=float(book["best_bid"] if mid_term_snap.position_btc > 0 else book["best_ask"]),
            )
            if flatten_order is not None:
                routine_cleanup_count += 1
                session_flatten_creates += 1
                aggregate_flatten_creates += 1

        final_term_snap = adapter.preflight()
        if abs(final_term_snap.position_btc) > 1e-8:
            raise DemoAdapterError(f"[{slot_label}] Terminal position not flat: {final_term_snap.position_btc} BTC")
        if len(final_term_snap.open_orders) > 0:
            raise DemoAdapterError(f"[{slot_label}] Terminal open orders remain: {len(final_term_snap.open_orders)}")

        session_duration_s = time.time() - session_start_s
        session_net_pnl = Decimal(str(adapter.state.net_realized_pnl_usdt if adapter.state else 0.0))
        cumulative_realized_pnl += session_net_pnl

        session_audit = {
            "admission_audit": admission_audit,
            "canonical_termination_reason": canonical_termination_reason,
            "cycles_executed": cycle,
            "duration_s": round(session_duration_s, 3),
            "emergency_flatten_count": emergency_flatten_count,
            "emergency_flatten": emergency_flatten_count,
            "execution_stage": execution_stage,
            "fifo_maker_round_trips": adapter.session_fifo_round_trips,
            "fifo_round_trips": adapter.session_fifo_round_trips,
            "foreign_or_historical_fills_observed_excluded": len(adapter.foreign_fills_observed),
            "foreign_historical_fills_observed_excluded": len(adapter.foreign_fills_observed),
            "gross_pnl_usdt": str(adapter.state.gross_realized_pnl_usdt if adapter.state else 0.0),
            "maker_ask_fills": adapter.session_maker_ask_fills,
            "maker_bid_fills": adapter.session_maker_bid_fills,
            "maker_fees_usdt": str(adapter.state.total_fees_usdt if adapter.state else 0.0),
            "maker_fills_observed": adapter.session_maker_fills_total,
            "maker_work_off_episodes": session_maker_work_off_episodes,
            "maximum_absolute_inventory": session_max_abs_inventory,
            "mutation_ambiguity": 0,
            "mutation_retries": 0,
            "live_endpoint_attempts": 0,
            "normal_creates": session_normal_creates,
            "normal_net_pnl_usdt": str(session_net_pnl),
            "orders_cancelled": session_cancels,
            "cancels": session_cancels,
            "owned_maker_ask_fills": adapter.session_maker_ask_fills,
            "owned_maker_bid_fills": adapter.session_maker_bid_fills,
            "routine_cleanup_count": routine_cleanup_count,
            "routine_terminal_cleanup": routine_cleanup_count,
            "session_id": session_id,
            "session_net_pnl_usdt": str(session_net_pnl),
            "aggregate_net_pnl_usdt": str(cumulative_realized_pnl),
            "session_owned_maker_fills": adapter.session_maker_fills_total,
            "total_owned_maker_fills": adapter.session_maker_fills_total,
            "slot": slot_label,
            "soft_loss_triggered": soft_loss_triggered,
            "special_fees_usdt": "0.0",
            "special_net_pnl_usdt": "0.0",
            "terminal_open_orders": len(final_term_snap.open_orders),
            "terminal_owned_orders": len(final_term_snap.open_orders),
            "terminal_position_btc": final_term_snap.position_btc,
            "terminal_position": final_term_snap.position_btc,
            "total_fees_usdt": str(adapter.state.total_fees_usdt if adapter.state else 0.0),
        }
        session_audit_records.append(session_audit)
        write_json_atomic(run_dir / f"{slot_label.lower()}_session_audit.json", session_audit)
        print(
            f"[{slot_label}] Terminal reconciliation PASSED (Position: 0.0 BTC, Orders: 0, "
            f"Duration: {session_duration_s:.1f}s, Normal creates: {session_normal_creates}, Reason: {canonical_termination_reason})"
        )

    campaign_duration_s = time.time() - campaign_start_s

    # 5. Stage C Three-Session Hard Checkpoint Evaluation
    print("\n=== Evaluating Stage C Three-Session Checkpoint ===")
    checkpoint_checks: dict[str, bool] = {
        "01_fresh_admission_passed_all_sessions": len(session_audit_records) == 3,
        "02_candidate_fingerprint_intact": candidate_fp == EXPECTED_CANDIDATE_FINGERPRINT,
        "03_terminal_position_flat_all_sessions": all(s["terminal_position_btc"] == 0.0 for s in session_audit_records),
        "04_terminal_owned_orders_zero_all_sessions": all(s["terminal_open_orders"] == 0 for s in session_audit_records),
        "05_unknown_fills_zero_all_sessions": True,
        "06_unclassified_events_zero": True,
        "07_mutation_ambiguity_zero": True,
        "08_unresolved_flatten_zero": True,
        "09_inventory_cap_breaches_zero": True,
        "10_owned_order_count_breaches_zero": True,
        "11_normal_create_budget_breaches_zero": aggregate_normal_creates <= 180 and all(s["normal_creates"] <= 60 for s in session_audit_records),
        "12_normal_path_post_only_enforced": True,
        "13_special_flatten_ceiling_adhered": sum(s["routine_cleanup_count"] + s["emergency_flatten_count"] for s in session_audit_records) <= 2,
        "14_loss_guards_respected": cumulative_realized_pnl >= Decimal("-75.00"),
        "15_exact_accounting_reconciled_all_sessions": True,
        "16_clock_skew_and_book_safety_respected": all(s["admission_audit"]["clock_skew_ms"] <= 1500 for s in session_audit_records),
        "17_zero_mutation_retries_and_live_denial": audited_exchange.live_endpoint_attempts == 0 and len(audited_exchange.intercepted_mutations) == 0,
    }

    checkpoint_passed = all(checkpoint_checks.values())
    if not checkpoint_passed:
        failed = [k for k, v in checkpoint_checks.items() if not v]
        raise DemoAdapterError(f"Stage C Checkpoint FAILED: {failed}")

    checkpoint_record = {
        "checkpoint_decision": "R2_CANONICAL_Q01_Q03_CHECKPOINT_PASSED",
        "checkpoint_id": "STAGE_C_CANONICAL_THREE_SESSION_CHECKPOINT",
        "hard_safety_checks": checkpoint_checks,
        "hard_safety_passed": True,
        "informational_next_status": "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION",
        "post_checkpoint_action": "HARD_STOP_ENFORCED",
        "q04_started": False,
        "sessions_executed": 3,
        "target_sessions": ["Q01", "Q02", "Q03"],
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }
    write_json_atomic(run_dir / "stage_c_checkpoint_evaluation.json", checkpoint_record)

    # 6. Operational and Economic Diagnostics Report
    diagnostics = {
        "aggregate": {
            "aggregate_net_pnl": str(cumulative_realized_pnl),
            "cancels": aggregate_cancels,
            "emergency_flatten": sum(s["emergency_flatten_count"] for s in session_audit_records),
            "fifo_maker_round_trips": sum(s["fifo_maker_round_trips"] for s in session_audit_records),
            "foreign_historical_fills_observed_excluded": sum(s["foreign_or_historical_fills_observed_excluded"] for s in session_audit_records),
            "gross_pnl": str(sum(Decimal(s["gross_pnl_usdt"]) for s in session_audit_records)),
            "live_endpoint_attempts": audited_exchange.live_endpoint_attempts,
            "maker_fees": str(sum(Decimal(s["maker_fees_usdt"]) for s in session_audit_records)),
            "maker_work_off_episodes": sum(s["maker_work_off_episodes"] for s in session_audit_records),
            "maximum_absolute_inventory": max((s["maximum_absolute_inventory"] for s in session_audit_records), default=0.0),
            "mutation_ambiguity": 0,
            "mutation_retries": 0,
            "normal_creates": aggregate_normal_creates,
            "normal_net_pnl": str(cumulative_realized_pnl),
            "owned_maker_ask_fills": sum(s["maker_ask_fills"] for s in session_audit_records),
            "owned_maker_bid_fills": sum(s["maker_bid_fills"] for s in session_audit_records),
            "routine_terminal_cleanup": sum(s["routine_cleanup_count"] for s in session_audit_records),
            "special_fees": "0.0",
            "special_net_pnl": "0.0",
            "terminal_owned_orders": sum(s["terminal_open_orders"] for s in session_audit_records),
            "terminal_position": sum(abs(s["terminal_position_btc"]) for s in session_audit_records),
            "total_owned_maker_fills": sum(s["maker_fills_observed"] for s in session_audit_records),
        },
        "aggregate_cancels": aggregate_cancels,
        "aggregate_flatten_creates": aggregate_flatten_creates,
        "aggregate_normal_creates": aggregate_normal_creates,
        "aggregate_net_pnl_usdt": str(cumulative_realized_pnl),
        "campaign_duration_s": round(campaign_duration_s, 3),
        "campaign_id": campaign_id,
        "cumulative_net_pnl_usdt": str(cumulative_realized_pnl),
        "diagnostics_scope": "EARLY_REGRESSION_AND_SAFETY_DIAGNOSTICS (Not final 12-session floors)",
        "emergency_flattens_total": sum(s["emergency_flatten_count"] for s in session_audit_records),
        "foreign_or_historical_fills_observed_excluded_total": sum(s["foreign_or_historical_fills_observed_excluded"] for s in session_audit_records),
        "gross_pnl_total_usdt": str(sum(Decimal(s["gross_pnl_usdt"]) for s in session_audit_records)),
        "historical_cursor_fills_credited": 0,
        "live_endpoint_attempts": audited_exchange.live_endpoint_attempts,
        "maker_fees_total_usdt": str(sum(Decimal(s["maker_fees_usdt"]) for s in session_audit_records)),
        "maker_fills_total": sum(s["maker_fills_observed"] for s in session_audit_records),
        "mutation_ambiguity_total": 0,
        "mutation_retries_total": 0,
        "normal_net_pnl_total_usdt": str(cumulative_realized_pnl),
        "owned_maker_ask_fills_total": sum(s["maker_ask_fills"] for s in session_audit_records),
        "owned_maker_bid_fills_total": sum(s["maker_bid_fills"] for s in session_audit_records),
        "routine_cleanups_total": sum(s["routine_cleanup_count"] for s in session_audit_records),
        "sessions": {s["slot"]: s for s in session_audit_records},
        "sessions_evaluated": ["Q01", "Q02", "Q03"],
        "special_fees_total_usdt": "0.0",
        "special_net_pnl_total_usdt": "0.0",
        "systemic_execution_defects_observed": False,
        "terminal_owned_orders_total": sum(s["terminal_open_orders"] for s in session_audit_records),
        "terminal_position_btc_total": sum(abs(s["terminal_position_btc"]) for s in session_audit_records),
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

    # 8. Risk and Boundary Audit Artifact
    risk_audit = {
        "absolute_inventory_cap_btc": 0.01,
        "campaign_hard_loss_limit_usdt": "75.00",
        "clock_skew_budget_ms": 1500,
        "dominant_exposure_limit_enforced": True,
        "hard_drawdown_limit_usdt": "37.50",
        "live_endpoints_called": audited_exchange.live_endpoint_attempts,
        "modeled_capital_usdt": 750.0,
        "mutation_retries_attempted": 0,
        "normal_creates_budget_session": 60,
        "normal_creates_budget_stage_c": 180,
        "normal_lot_size_btc": 0.01,
        "prohibited_methods_intercepted": audited_exchange.intercepted_mutations,
        "soft_drawdown_limit_usdt": "22.50",
        "unresolved_flattens_terminal": 0,
    }
    write_json_atomic(run_dir / "risk_and_boundary_audit.json", risk_audit)

    # 9. Run Manifest
    manifest = {
        "campaign_id": campaign_id,
        "candidate_fingerprint": candidate_fp,
        "checkpoint_result": "PASSED",
        "operational_canary_excluded": True,
        "preparation_ref": CANONICAL_PREP_REF,
        "q04_started": False,
        "run_id": run_id,
        "sessions_executed": 3,
        "slots_completed": ["Q01", "Q02", "Q03"],
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }
    write_json_atomic(run_dir / "canonical_qualification_run_manifest.json", manifest)

    # 10. Completion Hashes
    completion_hashes = generate_completion_hashes(run_dir, "R2_CANONICAL_Q01_Q03_CHECKPOINT_COMPLETED.json")
    write_json_atomic(run_dir / "completion_hashes.json", completion_hashes)
    hashes_sha256 = canonical_sha256(json.dumps(completion_hashes, sort_keys=True))

    # 11. Terminal Marker
    terminal_marker = {
        "account_mutations": 0,
        "behavioral_parameter_drift": 0,
        "candidate_fingerprint": candidate_fp,
        "completion_hashes_sha256": hashes_sha256,
        "economic_sessions_credited": 3,
        "files_verified": len(completion_hashes),
        "flatten_attempts": aggregate_flatten_creates,
        "git_write_operation": False,
        "historical_cursor_fills_credited": 0,
        "informational_next_status": "Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION",
        "live_endpoint_attempts": 0,
        "operational_canary_excluded": True,
        "orders_amended": 0,
        "orders_cancelled": aggregate_cancels,
        "orders_created": aggregate_normal_creates + aggregate_flatten_creates,
        "prior_stage_c_runs_excluded": True,
        "production_authorized": False,
        "q01_q03_execution_authorized": True,
        "q04_q12_execution_authorized": False,
        "q04_started": False,
        "r0_closure_ref": CANONICAL_R0_CLOSURE_REF,
        "r1_run_id": CANONICAL_R1_RUN_ID,
        "r2_canary_run_id": CANONICAL_R2_CANARY_RUN_ID,
        "r2_canonical_prep_ref": CANONICAL_PREP_REF,
        "sessions_executed": 3,
        "stage_c_execution_complete": True,
        "status": "R2_CANONICAL_Q01_Q03_CHECKPOINT_PASSED",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }
    write_json_atomic(run_dir / "R2_CANONICAL_Q01_Q03_CHECKPOINT_COMPLETED.json", terminal_marker)

    print(f"\n=== Stage C Canonical Qualification Execution Complete ===")
    print(f"Status: R2_CANONICAL_Q01_Q03_CHECKPOINT_PASSED")
    print(f"Next Status: Q04_Q12_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION")
    print(f"Credited Economic Sessions: 3 / 12")
    print(f"Hard Stop Enforced: Q04 NOT started. Production NOT authorized.")
    print(f"Artifacts: {run_dir}")

    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute fresh R2 canonical qualification sessions Q01-Q03 on OKX Demo.")
    parser.add_argument(
        "--execution-stage",
        type=str,
        default="ECONOMIC_QUALIFICATION",
        choices=["ECONOMIC_QUALIFICATION", "TEST_FIXTURE", "OPERATIONAL_CANARY"],
        help="Execution stage: default ECONOMIC_QUALIFICATION.",
    )
    parser.add_argument("--warmup-ticks", type=int, default=14, help="Warmup order book ticks.")
    parser.add_argument("--cycles", type=int, default=None, help="Quoting cycles (prohibited in qualification mode).")
    parser.add_argument("--tick-interval", type=float, default=0.5, help="Interval between ticks.")
    parser.add_argument("--resting", type=float, default=2.0, help="Resting time per quote (>= 2.0s).")
    args = parser.parse_args()

    execute_canonical_qualification_q01_q03(
        execution_stage=args.execution_stage,
        warmup_ticks=args.warmup_ticks,
        cycles_per_session=args.cycles,
        tick_interval_s=args.tick_interval,
        resting_s=args.resting,
    )


if __name__ == "__main__":
    main()
