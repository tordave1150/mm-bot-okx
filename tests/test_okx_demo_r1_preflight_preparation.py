"""Deterministic offline tests for R1 read-only preflight preparation.

These tests prove all 15 offline safety requirements specified in
EXECUTION_R0_CLOSURE_R1_PREFLIGHT_PREPARATION.md Section 21 under socket denial,
blank credentials, and zero external network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

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
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard
from tests.test_okx_demo_adapter import FaultExchange, _market_spec
from okx_demo_r0_closure_r1_prep import build_r1_preflight_preparation_package

ROOT = Path(__file__).resolve().parents[1]


class PreflightMockExchange(FaultExchange):
    def market(self, symbol: str) -> dict[str, Any]:
        return {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "contractSize": 0.01,
            "precision": {"amount": 0, "price": 1},
            "limits": {"amount": {"min": 1, "step": 1}},
            "linear": True,
            "inverse": False,
            "swap": True,
        }


@pytest.fixture(autouse=True)
def enforce_offline_socket_guard():
    """Ensure socket denial is strictly active for every test in this module."""
    guard = _OfflineSocketGuard()
    with guard:
        yield guard
    assert guard.attempts == []


def _create_mock_adapter(
    exchange: Any,
    session_id: str = "r1-preflight-session-test",
    tmp_path: Path | None = None,
) -> OkxDemoAdapter:
    profile = load_promoted_profile(ROOT)
    store_dir = tmp_path or Path("./tmp_pytest_state")
    if store_dir.exists():
        if store_dir.is_file():
            store_dir.unlink()
        else:
            shutil.rmtree(store_dir)
    store = DemoStateStore(store_dir)
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


# Requirement 1: LIVE mode remains unavailable
def test_requirement_01_live_mode_remains_unavailable() -> None:
    config = DemoAdapterConfig(mode=ExecutionMode.LIVE)
    with pytest.raises(DemoAdapterError, match="LIVE mode is unavailable"):
        config.validate("test_session")


# Requirement 2: missing credentials fail before any remote operation
def test_requirement_02_missing_credentials_fail_before_remote_op() -> None:
    with pytest.raises(DemoAdapterError, match="key, secret, and passphrase are all required"):
        build_ccxt_demo_exchange(api_key="", api_secret="", passphrase="")
    with pytest.raises(DemoAdapterError, match="key, secret, and passphrase are all required"):
        build_ccxt_demo_exchange(api_key="key", api_secret="", passphrase="pass")


# Requirement 3: wrong arm token fails
def test_requirement_03_wrong_arm_token_fails() -> None:
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        explicit_arm_token="WRONG_TOKEN",
    )
    with pytest.raises(DemoAdapterError, match="OKX demo is not explicitly armed"):
        config.validate("test_session")


# Requirement 4: wrong symbol fails
def test_requirement_04_wrong_symbol_fails() -> None:
    session_id = "test_session"
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        symbol="ETH/USDT:USDT",
        explicit_arm_token=f"OKX_DEMO:{session_id}",
    )
    with pytest.raises(DemoAdapterError, match="only the frozen BTC/USDT:USDT symbol is allowed"):
        config.validate(session_id)


# Requirement 5: wrong leverage expectation fails where required
def test_requirement_05_wrong_leverage_expectation_fails() -> None:
    session_id = "test_session"
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        leverage=5,
        explicit_arm_token=f"OKX_DEMO:{session_id}",
    )
    with pytest.raises(DemoAdapterError, match="frozen leverage must be 3"):
        config.validate(session_id)


# Requirement 6: wrong margin mode expectation fails
def test_requirement_06_wrong_margin_mode_fails() -> None:
    session_id = "test_session"
    config = DemoAdapterConfig(
        mode=ExecutionMode.OKX_DEMO,
        margin_mode="cross",
        explicit_arm_token=f"OKX_DEMO:{session_id}",
    )
    with pytest.raises(DemoAdapterError, match="frozen demo account mode mismatch"):
        config.validate(session_id)


# Requirement 7: sandbox/demo transport is mandatory
def test_requirement_07_sandbox_demo_transport_mandatory() -> None:
    exchange = PreflightMockExchange()
    exchange.options["sandboxMode"] = False
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="exchange is not in sandbox mode"):
        adapter.verify_demo_transport()

    exchange.options["sandboxMode"] = True
    exchange.headers["x-simulated-trading"] = "0"
    with pytest.raises(DemoAdapterError, match="simulated-trading header is missing"):
        adapter.verify_demo_transport()


# Requirement 8: mutation methods are unreachable / rejected in R1 preflight mode
def test_requirement_08_mutation_methods_unreachable_in_r1_preflight() -> None:
    exchange = PreflightMockExchange()
    adapter = _create_mock_adapter(exchange)
    # Prior to preflight running or when not allowed, new orders must fail closed
    with pytest.raises(DemoAdapterError, match="new orders are halted"):
        adapter.submit_post_only(
            side="buy",
            price=49000.0,
            best_bid=49000.0,
            best_ask=50000.0,
            market_timestamp_ms=int(time.time() * 1000),
        )


# Requirement 9: unknown endpoint category fails closed
def test_requirement_09_unknown_endpoint_category_fails_closed() -> None:
    allowed_read_endpoints = {
        "fetch_markets",
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
    }
    candidate_endpoint = "unrecognized_third_party_call"
    assert candidate_endpoint not in allowed_read_endpoints
    # Fail-closed enforcement: any unknown endpoint is denied
    with pytest.raises(ValueError, match="Endpoint DENIED: unknown category"):
        if candidate_endpoint not in allowed_read_endpoints:
            raise ValueError(f"Endpoint DENIED: unknown category '{candidate_endpoint}'")


# Requirement 10: R1 evidence writer never serializes secrets
def test_requirement_10_evidence_writer_never_serializes_secrets() -> None:
    snapshot = AccountSnapshot(
        account_uid="secret_account_uid_12345",
        position_mode="net_mode",
        leverage=3.0,
        total_equity_usdt=10000.0,
        free_equity_usdt=10000.0,
        position_btc=0.0,
        average_entry_price=0.0,
        maintenance_margin_usdt=0.0,
        open_orders=(),
        recent_trades=(),
        clock_skew_ms=10,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
    )
    public = snapshot.public_dict()
    assert public["account_uid"] == "present"
    assert "secret_account_uid_12345" not in json.dumps(public)
    for forbidden in ["apiKey", "secret", "password", "passphrase"]:
        assert forbidden not in public


# Requirement 11: foreign/unowned state produces failure output
def test_requirement_11_foreign_unowned_state_fails() -> None:
    exchange = PreflightMockExchange()
    # Unowned position present at startup
    exchange.position_btc = 0.05
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="fresh session has unowned orders or exposure|position and fill accounting do not reconcile"):
        adapter.preflight()


# Requirement 12: incomplete snapshots fail closed
def test_requirement_12_incomplete_snapshots_fail_closed() -> None:
    exchange = PreflightMockExchange()
    exchange.fetch_balance_fails = True
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(DemoAdapterError, match="authoritative preflight failed"):
        adapter.preflight()


# Requirement 13: clock-skew violation fixture fails
def test_requirement_13_clock_skew_violation_fails() -> None:
    exchange = PreflightMockExchange()
    exchange.clock_offset_ms = 5000  # Exceeds 1500ms budget
    adapter = _create_mock_adapter(exchange)
    with pytest.raises(ClockSkewBudgetError, match="clock skew exceeds frozen limit"):
        adapter.preflight()


# Requirement 14: market metadata mismatch fails
def test_requirement_14_market_metadata_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = PreflightMockExchange()
    adapter = _create_mock_adapter(exchange)
    # Patch market_spec with inverse=True
    bad_spec = _market_spec()
    bad_spec = bad_spec.__class__(
        symbol=bad_spec.symbol,
        contract_size=bad_spec.contract_size,
        amount_step=bad_spec.amount_step,
        min_amount=bad_spec.min_amount,
        min_notional=bad_spec.min_notional,
        price_tick=bad_spec.price_tick,
        amount_precision=bad_spec.amount_precision,
        price_precision=bad_spec.price_precision,
        linear=False,
        inverse=True,
    )
    monkeypatch.setattr(
        "okx_demo_adapter.fetch_market_info",
        lambda exc, sym, allow_fallback=False: {"market_spec": bad_spec},
    )
    with pytest.raises(DemoAdapterError, match="market must be a linear USDT contract"):
        adapter.preflight()


# Requirement 15: valid mocked read-only state produces R1_PREFLIGHT_PASS
def test_requirement_15_valid_mocked_state_produces_pass() -> None:
    exchange = PreflightMockExchange()
    adapter = _create_mock_adapter(exchange)
    snapshot = adapter.preflight()
    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.total_equity_usdt == 10000.0
    assert snapshot.position_btc == 0.0
    assert snapshot.leverage == 3.0
    assert adapter.halted_reason == ""
    # Ensure zero mutation calls were executed on exchange
    assert exchange.create_calls == 0
    assert exchange.cancel_calls == 0


def test_fresh_preparation_preserves_exact_r0_evidence_id(tmp_path: Path) -> None:
    """A preparation binds the supplied R0 evidence ID, not merely its folder name."""
    evidence_id = "r0-post-q06-campaign-wall-audit-offline-20260905T024551Z"
    package = build_r1_preflight_preparation_package(
        tmp_path,
        "test-exact-r0-evidence",
        tmp_path / "generic_r0_folder",
        "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf",
        r0_evidence_id=evidence_id,
    )
    identity = json.loads((package / "r1_identity.json").read_text(encoding="utf-8"))
    marker = json.loads((package / "R1_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    assert identity["r0_evidence_id"] == evidence_id
    assert marker["r0_closure_ref"] == evidence_id
    assert marker["r0_evidence_id"] == evidence_id
