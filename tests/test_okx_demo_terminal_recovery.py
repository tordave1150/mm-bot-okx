from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from okx_demo_soak_executor import SoakPackage
from okx_demo_terminal_recovery_executor import (
    TerminalRecoveryExecutor,
    TerminalRecoveryGateway,
)


class ExchangeFixture:
    options = {"sandboxMode": True}
    headers = {"x-simulated-trading": "1"}
    hostname = "www.okx.com"

    def __init__(self, *, trade: bool) -> None:
        self.trade = trade
        self.create_calls = 0

    def create_order(self, *args, **kwargs):
        self.create_calls += 1
        raise TimeoutError("ambiguous dispatch")

    def fetch_open_orders(self, *args, **kwargs):
        return []

    def fetch_closed_orders(self, *args, **kwargs):
        return []

    def fetch_orders(self, *args, **kwargs):
        return []

    def fetch_my_trades(self, *args, **kwargs):
        if not self.trade:
            return []
        return [{
            "id": "fill-1",
            "order": "flatten-order",
            "info": {"clOrdId": "flatten-client"},
        }]


def test_ambiguous_flatten_resolves_from_recent_tail_without_retry() -> None:
    exchange = ExchangeFixture(trade=True)
    gateway = TerminalRecoveryGateway(exchange)
    gateway.market_spec = SimpleNamespace(
        base_to_contracts=lambda value, exact: Decimal("1")
    )
    result = gateway.submit_reduce_only_flatten(
        client_order_id="flatten-client", position_btc=Decimal("0.01")
    )
    assert result["id"] == "flatten-order"
    assert result["reconciledFromTrades"] is True
    assert exchange.create_calls == 1
    assert gateway.flatten_dispatches == 1


def test_ambiguous_flatten_absence_fails_without_second_dispatch() -> None:
    exchange = ExchangeFixture(trade=False)
    gateway = TerminalRecoveryGateway(exchange)
    gateway.market_spec = SimpleNamespace(
        base_to_contracts=lambda value, exact: Decimal("1")
    )
    with pytest.raises(Exception, match="no retry allowed"):
        gateway.submit_reduce_only_flatten(
            client_order_id="flatten-client", position_btc=Decimal("0.01")
        )
    assert exchange.create_calls == 1
    assert gateway.flatten_dispatches == 1


class AccountGateway:
    flatten_dispatches = 1

    def __init__(self) -> None:
        self.reads = 0

    def fetch_account(self):
        self.reads += 1
        return SimpleNamespace(
            position_btc=Decimal("0"),
            open_orders=(),
            account_binding="binding",
            clock_skew_ms=0,
        )


def test_shutdown_after_ambiguous_flatten_collects_two_account_only_snapshots(
    tmp_path: Path,
) -> None:
    package = SoakPackage(tmp_path, tmp_path, {
        "run_id": "run",
        "session_id": "session",
        "risk_budget": {"read_retry_attempts": 1, "maximum_clock_skew_ms": 1500},
    })
    gateway = AccountGateway()
    driver = TerminalRecoveryExecutor(
        package, gateway, sleep=lambda _: None, now=lambda: 1
    )
    driver.owned.clear()
    first, second, reconciled = driver._shutdown()
    assert first.position_btc == second.position_btc == 0
    assert gateway.reads >= 3
    assert reconciled is False
    assert gateway.flatten_dispatches == 1
