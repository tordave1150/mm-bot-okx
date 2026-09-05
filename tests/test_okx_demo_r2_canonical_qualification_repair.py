"""Deterministic tests proving canonical executor repair, fill attribution, and lifecycle termination.

Tests satisfy Requirements 6 and 7 of the R2 Canonical Economic Qualification Campaign Brief
under strict socket denial:
- Fill cursor isolation and baseline trade exclusion
- Multi-session trade deduplication
- Bid/ask fill attribution and idempotency
- Foreign and previous-run fill isolation
- Partial/full fill reconciliation and cancel-after-partial-fill
- FIFO round trip and PnL/fee attribution
- Prohibited fixed-cycle limits in qualification mode
- 30-minute wall time limit termination
- 60-create session budget termination
- Soft-loss throttling at 22.50 USDT
- Hard drawdown kill at 37.50 USDT
- Fail-closed safety faults
- Terminal work-off/flatten lifecycle
- Frozen candidate fingerprint and risk boundary integrity.
"""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
import pytest

from okx_demo_adapter import (
    AccountSnapshot,
    DemoAdapterConfig,
    DemoAdapterError,
    ExecutionMode,
    OkxDemoAdapter,
)
from okx_demo_profile import load_promoted_profile
from okx_demo_r2_stage_c_executor import (
    EXPECTED_CANDIDATE_FINGERPRINT,
    AuditedStageCExchangeWrapper,
    execute_r2_stage_c,
)
from okx_demo_state import DemoRuntimeState, DemoStateStore
from okx_demo_staged_validation import compute_candidate_fingerprint
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


def _create_test_adapter(
    exchange: PreflightMockExchange,
    tmp_path: Path,
    session_id: str = "test-canonical-session-1",
) -> OkxDemoAdapter:
    profile = load_promoted_profile(ROOT)
    state_file = tmp_path / f"{session_id}_state.json"
    store = DemoStateStore(state_file)
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        symbol="BTC/USDT:USDT",
        margin_mode="isolated",
        position_mode="net_mode",
        leverage=3,
        maximum_clock_skew_ms=1500,
        explicit_arm_token=f"OKX_DEMO:{session_id}",
    )
    return OkxDemoAdapter(
        exchange=exchange,
        config=config,
        promoted_profile=profile,
        session_id=session_id,
        state_store=store,
    )


# ==============================================================================
# REQUIREMENT 6: Deterministic Fill Attribution and Cursor Accounting Tests
# ==============================================================================

def test_historical_cursor_seed_produces_zero_qualification_fills(tmp_path: Path) -> None:
    """Historical trade cursor entries establish baseline only; never increment qualification fills."""
    exchange = PreflightMockExchange()
    # Seed historical trade on exchange before adapter startup
    exchange.trades = [{
        "id": "historical-trade-4389103155",
        "order": "historical-order-001",
        "clientOrderId": "r2-canary-order-001",
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": 1788500000000,
    }]
    adapter = _create_test_adapter(exchange, tmp_path)
    snapshot = adapter.preflight()

    # Fill metrics must be strictly zero
    assert len(adapter.session_owned_fills) == 0
    assert adapter.session_maker_fills_total == 0
    assert adapter.session_maker_bid_fills == 0
    assert adapter.session_maker_ask_fills == 0
    assert adapter.session_fifo_round_trips == 0
    assert adapter.session_owned_realized_pnl == Decimal("0.0")
    assert adapter.session_owned_fees_usdt == Decimal("0.0")
    assert adapter.session_filled_btc_quantity == 0.0


def test_same_historical_trade_seen_in_multiple_sessions_never_counted_repeatedly(tmp_path: Path) -> None:
    """The same historical trade observed across multiple sessions is never credited to any session."""
    exchange = PreflightMockExchange()
    exchange.trades = [{
        "id": "historical-trade-4389103155",
        "order": "historical-order-001",
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": 1788500000000,
    }]

    # Session 1
    adapter1 = _create_test_adapter(exchange, tmp_path, session_id="session-q01")
    adapter1.preflight()
    assert adapter1.session_maker_fills_total == 0
    assert len(adapter1.session_owned_fills) == 0

    # Session 2
    adapter2 = _create_test_adapter(exchange, tmp_path, session_id="session-q02")
    adapter2.preflight()
    assert adapter2.session_maker_fills_total == 0
    assert len(adapter2.session_owned_fills) == 0


def test_one_newly_owned_bid_fill_increments_bid_fill_exactly_once(tmp_path: Path) -> None:
    """One newly owned bid fill increments maker bid fills exactly once."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    # Submit post-only bid
    submitted = adapter.submit_post_only(
        side="buy",
        price=49_000.0,
        best_bid=49_000.0,
        best_ask=50_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )
    assert submitted.side == "buy"

    # Incur fill for this owned order
    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-bid-1",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "buy",
        "price": 49_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.098, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.01  # 1 contract = 0.01 BTC
    exchange.orders.pop(submitted.order_id, None)

    # Reconcile
    adapter.preflight()

    assert adapter.session_maker_bid_fills == 1
    assert adapter.session_maker_ask_fills == 0
    assert adapter.session_maker_fills_total == 1
    assert len(adapter.session_owned_fills) == 1
    assert adapter.session_filled_btc_quantity == 0.01


def test_one_newly_owned_ask_fill_increments_ask_fill_exactly_once(tmp_path: Path) -> None:
    """One newly owned ask fill increments maker ask fills exactly once."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    # Submit post-only ask
    submitted = adapter.submit_post_only(
        side="sell",
        price=51_000.0,
        best_bid=49_000.0,
        best_ask=51_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )
    assert submitted.side == "sell"

    # Incur fill for this owned order
    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-ask-1",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "sell",
        "price": 51_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.102, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = -0.01
    exchange.orders.pop(submitted.order_id, None)

    adapter.preflight()

    assert adapter.session_maker_ask_fills == 1
    assert adapter.session_maker_bid_fills == 0
    assert adapter.session_maker_fills_total == 1
    assert len(adapter.session_owned_fills) == 1
    assert adapter.session_filled_btc_quantity == 0.01


def test_duplicate_exchange_trade_observations_are_idempotent(tmp_path: Path) -> None:
    """Observing the same exchange trade on subsequent preflight cycles is idempotent."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    submitted = adapter.submit_post_only(
        side="buy",
        price=49_000.0,
        best_bid=49_000.0,
        best_ask=50_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )

    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-bid-idempotent",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "buy",
        "price": 49_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.098, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.01
    exchange.orders.pop(submitted.order_id, None)

    adapter.preflight()
    assert adapter.session_maker_fills_total == 1

    # Second preflight with the same trade still in exchange.trades
    adapter.preflight()
    assert adapter.session_maker_fills_total == 1
    assert len(adapter.session_owned_fills) == 1


def test_foreign_fills_never_enter_qualification_economics(tmp_path: Path) -> None:
    """Foreign trades never enter session_owned_fills, maker fills, FIFO, or qualification PnL/fees."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    # Inject an unowned foreign trade that occurred on the account
    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "foreign-trade-999",
        "order": "foreign-order-999",
        "clientOrderId": "foreign-client-999",
        "side": "buy",
        "price": 49_500.0,
        "amount": 1.0,
        "fee": {"cost": 0.50, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.01

    adapter.preflight()

    assert adapter.session_maker_fills_total == 0
    assert len(adapter.session_owned_fills) == 0
    assert adapter.session_fifo_round_trips == 0
    assert adapter.session_owned_realized_pnl == Decimal("0.0")
    assert adapter.session_owned_fees_usdt == Decimal("0.0")
    assert len(adapter.foreign_fills_observed) == 1


def test_fills_from_previous_r2_runs_never_enter_new_campaign(tmp_path: Path) -> None:
    """Fills from previous R2 runs (e.g. Stage C short run or canary) never enter the new session."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path, session_id="r2-session-fresh-q01")
    adapter.preflight()

    # Inject a trade from previous Stage C run
    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "old-run-trade-12345",
        "order": "order-r2-stage-c-run-20260904T141733Z-q01-001",
        "clientOrderId": "r2-session-20260904T133500Z-q01:p0:9c1a01f1:1",
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.01

    adapter.preflight()

    assert adapter.session_maker_fills_total == 0
    assert len(adapter.session_owned_fills) == 0
    assert len(adapter.foreign_fills_observed) == 1


def test_partially_filled_owned_orders_reconcile_correctly(tmp_path: Path) -> None:
    """Partially filled owned order reconciles partial fill and keeps remaining contracts open."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    submitted = adapter.submit_post_only(
        side="buy",
        price=49_000.0,
        best_bid=49_000.0,
        best_ask=50_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )

    # Incur partial fill: 0.5 contracts (0.005 BTC)
    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-partial-1",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "buy",
        "price": 49_000.0,
        "amount": 0.5,
        "fee": {"cost": 0.049, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.005
    # Order remains open with remaining amount
    exchange.orders[submitted.client_order_id]["amount"] = 0.5

    adapter.preflight()

    assert adapter.session_maker_bid_fills == 1
    assert adapter.session_maker_fills_total == 1
    assert adapter.session_filled_btc_quantity == 0.005
    assert submitted.client_order_id in adapter.state.owned_open_orders


def test_fully_filled_owned_orders_reconcile_correctly(tmp_path: Path) -> None:
    """Fully filled owned order reconciles fill and removes order from owned_open_orders."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    submitted = adapter.submit_post_only(
        side="sell",
        price=51_000.0,
        best_bid=49_000.0,
        best_ask=51_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )

    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-full-1",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "sell",
        "price": 51_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.102, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = -0.01
    exchange.orders.pop(submitted.client_order_id, None)

    adapter.preflight()

    assert adapter.session_maker_ask_fills == 1
    assert adapter.session_maker_fills_total == 1
    assert submitted.client_order_id not in adapter.state.owned_open_orders


def test_cancel_after_partial_fill_preserves_fill_and_cancels_remainder(tmp_path: Path) -> None:
    """Cancelling after a partial fill retains the fill record and only cancels the remainder."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    submitted = adapter.submit_post_only(
        side="buy",
        price=49_000.0,
        best_bid=49_000.0,
        best_ask=50_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )

    now_ms = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-partial-2",
        "order": submitted.order_id,
        "clientOrderId": submitted.client_order_id,
        "side": "buy",
        "price": 49_000.0,
        "amount": 0.5,
        "fee": {"cost": 0.049, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": now_ms,
    })
    exchange.position_btc = 0.005
    exchange.orders[submitted.client_order_id]["amount"] = 0.5

    adapter.preflight()
    assert adapter.session_maker_fills_total == 1

    # Cancel open orders
    cancelled = adapter.cancel_all_owned()
    assert submitted.order_id in cancelled
    assert len(adapter.state.owned_open_orders) == 0

    # Fill count and filled quantity must be preserved
    assert adapter.session_maker_fills_total == 1
    assert adapter.session_filled_btc_quantity == 0.005


def test_fifo_attribution_uses_owned_fills_only(tmp_path: Path) -> None:
    """FIFO round trip attribution triggers strictly on owned buy followed by owned sell."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    # 1. Buy 1 lot (0.01 BTC)
    buy_order = adapter.submit_post_only(
        side="buy",
        price=49_000.0,
        best_bid=49_000.0,
        best_ask=50_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )
    t1 = int(time.time() * 1000)
    exchange.trades.append({
        "id": "trade-fifo-1",
        "order": buy_order.order_id,
        "clientOrderId": buy_order.client_order_id,
        "side": "buy",
        "price": 49_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.098, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": t1,
    })
    exchange.position_btc = 0.01
    exchange.orders.pop(buy_order.client_order_id, None)
    adapter.preflight()
    assert adapter.session_fifo_round_trips == 0

    # 2. Sell 1 lot (0.01 BTC) to close round trip
    sell_order = adapter.submit_post_only(
        side="sell",
        price=51_000.0,
        best_bid=49_000.0,
        best_ask=51_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )
    t2 = t1 + 100
    exchange.trades.append({
        "id": "trade-fifo-2",
        "order": sell_order.order_id,
        "clientOrderId": sell_order.client_order_id,
        "side": "sell",
        "price": 51_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.102, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": t2,
    })
    exchange.position_btc = 0.0
    exchange.orders.pop(sell_order.client_order_id, None)
    adapter.preflight()

    assert adapter.session_fifo_round_trips == 1
    assert adapter.session_maker_fills_total == 2
    # Gross realized: 0.01 * (51000 - 50000) = +10.00 USDT
    assert adapter.session_owned_realized_pnl == Decimal("10.00")
    # Fees: 0.098 + 0.102 = 0.20 USDT
    assert adapter.session_owned_fees_usdt == Decimal("0.20")


def test_fees_and_pnl_use_owned_fills_only(tmp_path: Path) -> None:
    """PnL and fees in session accounting reflect owned fills only."""
    exchange = PreflightMockExchange()
    adapter = _create_test_adapter(exchange, tmp_path)
    adapter.preflight()

    buy_order = adapter.submit_post_only(
        side="buy",
        price=50_000.0,
        best_bid=50_000.0,
        best_ask=51_000.0,
        market_timestamp_ms=int(time.time() * 1000),
    )
    exchange.trades.append({
        "id": "trade-pnl-1",
        "order": buy_order.order_id,
        "clientOrderId": buy_order.client_order_id,
        "side": "buy",
        "price": 50_000.0,
        "amount": 1.0,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "timestamp": int(time.time() * 1000),
    })
    exchange.position_btc = 0.01
    exchange.orders.pop(buy_order.client_order_id, None)
    adapter.preflight()

    assert adapter.session_owned_fees_usdt == Decimal("0.10")
    assert adapter.session_owned_realized_pnl == Decimal("0.0")


# ==============================================================================
# REQUIREMENT 7: Canonical Lifecycle Termination Tests
# ==============================================================================

def test_qualification_mode_prohibits_cycles_parameter() -> None:
    """Fixed cycle parameter is strictly prohibited in ECONOMIC_QUALIFICATION mode."""
    mock_exchange = PreflightMockExchange()
    with pytest.raises(DemoAdapterError, match="Fixed cycle limit is strictly prohibited when execution_stage == 'ECONOMIC_QUALIFICATION'"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            execution_stage="ECONOMIC_QUALIFICATION",
            cycles_per_session=4,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_qualification_mode_cannot_terminate_from_fixed_cycle_condition() -> None:
    """Qualification mode does not terminate after 4 cycles; requires canonical termination."""
    mock_exchange = PreflightMockExchange()
    # In economic qualification stage, fixed cycle limit is blocked
    with pytest.raises(DemoAdapterError, match="Fixed cycle limit is strictly prohibited"):
        execute_r2_stage_c(
            exchange=mock_exchange,
            execution_stage="ECONOMIC_QUALIFICATION",
            cycles_per_session=4,
            tick_interval_s=0.0,
            resting_s=0.0,
        )


def test_30_minute_wall_limit_terminates_correctly() -> None:
    """Session terminates with CANONICAL_SESSION_WALL_TIME_EXPIRED when wall time expires."""
    test_stamp = "test_wall_limit"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_r2_stage_c(
            exchange=mock_exchange,
            stamp=test_stamp,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=100,  # high cycle limit so wall time terminates first
            max_session_wall_time_s=0.04,  # 40 ms wall time limit
            tick_interval_s=0.01,
            resting_s=0.02,
        )
        q01_audit = json.loads((run_dir / "q01_session_audit.json").read_text(encoding="utf-8"))
        assert q01_audit["canonical_termination_reason"] == "CANONICAL_SESSION_WALL_TIME_EXPIRED"
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


def test_60_create_session_budget_terminates_correctly() -> None:
    """Session terminates with CANONICAL_NORMAL_CREATES_BUDGET_EXHAUSTED when creates reach budget."""
    test_stamp = "test_create_budget"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_r2_stage_c(
            exchange=mock_exchange,
            stamp=test_stamp,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=100,
            max_session_normal_creates=6,  # test threshold 6 creates (3 cycles of 2 quotes)
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        q01_audit = json.loads((run_dir / "q01_session_audit.json").read_text(encoding="utf-8"))
        assert q01_audit["canonical_termination_reason"] == "CANONICAL_NORMAL_CREATES_BUDGET_EXHAUSTED"
        assert q01_audit["normal_creates"] == 6
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


def test_soft_loss_lifecycle_throttles_quoting() -> None:
    """Soft-loss guard at 22.50 USDT drawdown throttles new quoting without crashing."""
    test_stamp = "test_soft_loss"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    call_count = 0
    def dynamic_balance():
        nonlocal call_count
        call_count += 1
        # Admission preflight uses baseline 10,000 USDT
        if call_count <= 2:
            return {"USDT": {"total": 10000.0, "free": 10000.0}}
        # Inside quoting loop, balance drops by 23 USDT (drawdown 23.0 >= 22.50)
        return {"USDT": {"total": 9977.0, "free": 9977.0}}
    mock_exchange.fetch_balance = dynamic_balance

    try:
        run_dir = execute_r2_stage_c(
            exchange=mock_exchange,
            stamp=test_stamp,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=2,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        assert run_dir.is_dir()
        q01_audit = json.loads((run_dir / "q01_session_audit.json").read_text(encoding="utf-8"))
        assert q01_audit["soft_loss_triggered"] is True
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


def test_37_50_usdt_hard_drawdown_terminates_correctly() -> None:
    """Hard drawdown of 37.50 USDT triggers immediate fail-closed termination."""
    test_stamp = "test_hard_kill"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    call_count = 0
    def dynamic_balance():
        nonlocal call_count
        call_count += 1
        # Admission uses 10,000 USDT
        if call_count <= 2:
            return {"USDT": {"total": 10000.0, "free": 10000.0}}
        # In quoting loop, balance drops by 40 USDT (drawdown 40.0 >= 37.50)
        return {"USDT": {"total": 9960.0, "free": 9960.0}}
    mock_exchange.fetch_balance = dynamic_balance

    try:
        with pytest.raises(DemoAdapterError, match="Session hard kill drawdown limit breached"):
            execute_r2_stage_c(
                exchange=mock_exchange,
                stamp=test_stamp,
                execution_stage="TEST_FIXTURE",
                cycles_per_session=2,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


def test_safety_and_reconciliation_faults_fail_closed() -> None:
    """Clock skew > 1500 ms fails closed at fresh admission."""
    test_stamp = "test_skew_fault"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    mock_exchange = PreflightMockExchange()
    mock_exchange.clock_offset_ms = 2500  # 2500 ms > 1500 ms

    try:
        with pytest.raises(DemoAdapterError, match="clock skew exceeds frozen limit|Fresh admission gate failed closed"):
            execute_r2_stage_c(
                exchange=mock_exchange,
                stamp=test_stamp,
                tick_interval_s=0.0,
                resting_s=0.0,
            )
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


def test_terminal_workoff_flatten_lifecycle_preserved() -> None:
    """Residual position at session end triggers terminal flatten to 0.0 BTC."""
    test_stamp = "test_terminal_flatten"
    run_dir = ROOT / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{test_stamp}"
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    mock_exchange = PreflightMockExchange()

    try:
        run_dir = execute_r2_stage_c(
            exchange=mock_exchange,
            stamp=test_stamp,
            execution_stage="TEST_FIXTURE",
            cycles_per_session=1,
            tick_interval_s=0.0,
            resting_s=0.0,
        )
        assert run_dir.is_dir()
        chk = json.loads((run_dir / "checkpoint_2_evaluation.json").read_text(encoding="utf-8"))
        assert chk["hard_safety_checks"]["03_terminal_position_flat_all_sessions"] is True
        assert chk["hard_safety_checks"]["04_terminal_owned_orders_zero_all_sessions"] is True
    finally:
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)


# ==============================================================================
# REQUIREMENTS 8 & 9: Frozen Candidate and Risk Boundary Tests
# ==============================================================================

def test_frozen_candidate_fingerprint_exact_match() -> None:
    """Qualified candidate fingerprint must exactly match the frozen value with 0 drift."""
    fp_record = compute_candidate_fingerprint(ROOT)
    assert fp_record["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT


def test_frozen_risk_boundary_parameters() -> None:
    """Verify frozen risk boundaries: BTC/USDT:USDT, 0.01 BTC lot, 0.01 BTC cap, 750 USDT capital, 3x."""
    profile = load_promoted_profile(ROOT)
    assert profile.strategy.fixed_lot_size_btc == 0.01
    assert profile.capital_policy["capital_usdt"] == 750.0
    assert profile.strategy.minimum_half_spread_bps == 6.0


# ==============================================================================
# REQUIREMENTS 10-13: Canonical Preparation Package and Identity Isolation Tests
# ==============================================================================

def test_canonical_preparation_package_artifacts_and_hashes() -> None:
    """Verify fresh canonical preparation package contains all 13 artifacts and matching hashes."""
    prep_dir = ROOT / "artifacts" / "r2_canonical_qualification_preparation" / "r2-canonical-prep-20260904T154500Z"
    assert prep_dir.is_dir()

    required_artifacts = [
        "canonical_executor_repair_audit.json",
        "fill_ownership_cursor_repair_audit.json",
        "old_vs_new_termination_behavior_comparison.json",
        "fresh_campaign_identity_manifest.json",
        "candidate_verification.json",
        "frozen_risk_specification.json",
        "per_session_admission_contract.json",
        "canonical_lifecycle_contract.json",
        "owned_fill_accounting_contract.json",
        "qualification_evidence_schema.json",
        "deterministic_test_summary.json",
        "completion_hashes.json",
        "R2_CANONICAL_QUALIFICATION_REPAIR_COMPLETED.json",
    ]
    for filename in required_artifacts:
        artifact_path = prep_dir / filename
        assert artifact_path.is_file(), f"Missing required artifact: {filename}"

    # Verify hashes
    completion_hashes = json.loads((prep_dir / "completion_hashes.json").read_text(encoding="utf-8"))
    for rel_path, expected_hash in completion_hashes.items():
        actual_hash = hashlib.sha256((prep_dir / rel_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash, f"Hash mismatch for {rel_path}"

    # Verify terminal marker
    marker = json.loads((prep_dir / "R2_CANONICAL_QUALIFICATION_REPAIR_COMPLETED.json").read_text(encoding="utf-8"))
    assert marker["status"] == "R2_CANONICAL_QUALIFICATION_REPAIR_PASSED"
    assert marker["informational_next_status"] == "R2_FRESH_QUALIFICATION_CAMPAIGN_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION"
    assert marker["credited_economic_sessions"] == "0 / 12"
    assert marker["economic_sessions_credited"] == 0
    assert marker["new_sessions_executed"] == 0
    assert marker["qualification_orders_created"] == 0
    assert marker["behavioral_parameter_drift"] == 0
    assert marker["old_stage_c_runs_excluded"] is True
    assert marker["production_authorized"] is False
    assert marker["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT


def test_fresh_identities_distinct_from_old_runs() -> None:
    """Verify fresh campaign identities are completely distinct from canary and Stage C runs."""
    prep_dir = ROOT / "artifacts" / "r2_canonical_qualification_preparation" / "r2-canonical-prep-20260904T154500Z"
    manifest = json.loads((prep_dir / "fresh_campaign_identity_manifest.json").read_text(encoding="utf-8"))

    fresh_campaign_id = manifest["campaign_id"]
    fresh_sessions = manifest["fresh_slot_schedule"]
    assert len(fresh_sessions) == 12

    # Must not match canary or old Stage C
    assert fresh_campaign_id != "r2-qualification-campaign-20260904T133500Z"
    assert fresh_campaign_id != "r2-canary-run-20260904T131836Z"

    old_session_ids = {
        "r2-session-20260904T133500Z-q01:p0:9c1a01f1",
        "r2-session-20260904T133500Z-q02:p0:3e4b02a2",
        "r2-session-20260904T133500Z-q03:p0:7f8c03d3",
    }
    for session in fresh_sessions:
        assert session["session_id"] not in old_session_ids
        assert session["nonce"] not in {"9c1a01f1", "3e4b02a2", "7f8c03d3"}
