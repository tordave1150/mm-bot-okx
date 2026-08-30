from __future__ import annotations

from copy import deepcopy

import pytest

import okx_fill_restart_gateway as gateway_module
from okx_fill_restart_gateway import FormalDemoGateway, FormalGatewayError


CLIENT_ID = "fr" + "a" * 28
ORDER_ID = "owned-order-1"


def _order(*, status: str = "open", filled: str = "0") -> dict:
    return {
        "id": ORDER_ID,
        "clientOrderId": CLIENT_ID,
        "side": "buy",
        "amount": "1",
        "filled": filled,
        "status": status,
        "info": {"clOrdId": CLIENT_ID, "state": status},
    }


def _trade(trade_id: str, *, amount: str = "1") -> dict:
    return {
        "id": trade_id,
        "order": ORDER_ID,
        "timestamp": 1_100,
        "side": "buy",
        "price": "50000",
        "amount": amount,
        "fee": {"cost": "0.10", "currency": "USDT"},
        "takerOrMaker": "maker",
        "info": {"clOrdId": CLIENT_ID},
    }


class CancelRaceExchange:
    def __init__(self) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.hostname = "www.okx.com"
        self.open_snapshots: list[list[dict]] = [[_order()]]
        self.order_result: dict | Exception = _order()
        self.cancel_result: dict | Exception = {"id": ORDER_ID}
        self.history: list[dict] = []
        self.recent: list[dict] = []
        self.cancel_calls = 0
        self.trade_calls: list[tuple] = []

    def fetch_open_orders(self, symbol):
        rows = self.open_snapshots.pop(0) if len(self.open_snapshots) > 1 else self.open_snapshots[0]
        return deepcopy(rows)

    def cancel_order(self, order_id, symbol):
        self.cancel_calls += 1
        if isinstance(self.cancel_result, Exception):
            raise self.cancel_result
        return deepcopy(self.cancel_result)

    def fetch_order(self, order_id, symbol, params):
        if isinstance(self.order_result, Exception):
            raise self.order_result
        return deepcopy(self.order_result)

    def fetch_my_trades(self, symbol, since, limit, params):
        self.trade_calls.append((symbol, since, limit, dict(params)))
        return deepcopy(self.history if params.get("paginate") else self.recent)


def _gateway(monkeypatch, exchange: CancelRaceExchange) -> FormalDemoGateway:
    monkeypatch.setattr(gateway_module.time, "sleep", lambda _: None)
    result = FormalDemoGateway(exchange)
    result.set_cancel_reconciliation_context(since_ms=1_000, read_attempts=3)
    return result


def test_successful_cancel_uses_bounded_stable_absence_without_retry(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[_order()], [_order()], [], []]
    exchange.order_result = _order(status="open")
    gateway = _gateway(monkeypatch, exchange)

    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    audit = gateway.last_cancel_reconciliation_audit
    assert exchange.cancel_calls == 1
    assert audit["cancel_dispatches"] == 1
    assert audit["mutation_retries"] == 0
    assert audit["read_attempts"] == 3
    assert audit["classifications"][CLIENT_ID] == "CANCEL_CONFIRMED"


def test_cancel_fill_race_uses_paginated_plus_recent_tail(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[_order()], []]
    exchange.cancel_result = TimeoutError("response lost after dispatch")
    exchange.order_result = KeyError("order no longer queryable")
    exchange.recent = [_trade("fresh-tail-fill")]
    gateway = _gateway(monkeypatch, exchange)

    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    audit = gateway.last_cancel_reconciliation_audit
    assert exchange.cancel_calls == 1
    assert audit["classifications"][CLIENT_ID] == "FILLED_DURING_CANCEL"
    assert audit["history_recent_tail_union_used"] is True
    assert gateway.last_fill_union_audit["eligible_history_rows"] == 0
    assert gateway.last_fill_union_audit["eligible_recent_tail_rows"] == 1


def test_partial_fill_then_explicit_cancel_is_terminal(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[_order()], []]
    exchange.cancel_result = {"id": ORDER_ID, "status": "canceled"}
    exchange.order_result = _order(status="canceled", filled="0.4")
    exchange.history = [_trade("partial", amount="0.4")]
    gateway = _gateway(monkeypatch, exchange)

    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    assert gateway.last_cancel_reconciliation_audit["classifications"][CLIENT_ID] == (
        "PARTIAL_FILL_THEN_CANCEL_CONFIRMED"
    )
    assert exchange.cancel_calls == 1


def test_pre_cancel_order_disappearance_requires_terminal_read(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[]]
    exchange.order_result = _order(status="filled", filled="1")
    exchange.recent = [_trade("already-filled")]
    gateway = _gateway(monkeypatch, exchange)

    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    assert exchange.cancel_calls == 0
    assert gateway.last_cancel_reconciliation_audit["classifications"][CLIENT_ID] == (
        "FILLED_DURING_CANCEL"
    )


def test_closed_zero_fill_is_cancel_not_a_phantom_fill(monkeypatch) -> None:
    """Reproduce R2 Session 1's generic CCXT ``closed`` cancellation."""
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[]]
    exchange.order_result = _order(status="closed", filled="0")
    gateway = _gateway(monkeypatch, exchange)

    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    audit = gateway.last_cancel_reconciliation_audit
    assert exchange.cancel_calls == 0
    assert audit["classifications"][CLIENT_ID] == "CANCEL_CONFIRMED"
    assert audit["fill_union_audit"]["eligible_history_rows"] == 0
    assert audit["fill_union_audit"]["eligible_recent_tail_rows"] == 0


def test_terminal_fill_claim_without_owned_trade_stays_fail_closed(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[]]
    exchange.order_result = _order(status="closed", filled="1")
    gateway = _gateway(monkeypatch, exchange)

    with pytest.raises(FormalGatewayError, match="bounded reads"):
        gateway.cancel_all_owned([CLIENT_ID])
    audit = gateway.last_cancel_reconciliation_audit
    assert audit["classifications"][CLIENT_ID] == "FILL_CLAIM_UNPROVEN"
    assert audit["resolved"] is False
    assert audit["mutation_retries"] == 0


def test_unresolved_open_order_fails_closed_after_one_cancel(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[_order()], [_order()], [_order()], [_order()]]
    exchange.cancel_result = TimeoutError("ambiguous")
    gateway = _gateway(monkeypatch, exchange)

    with pytest.raises(FormalGatewayError, match="bounded reads"):
        gateway.cancel_all_owned([CLIENT_ID])
    audit = gateway.last_cancel_reconciliation_audit
    assert exchange.cancel_calls == 1
    assert audit["mutation_retries"] == 0
    assert audit["read_attempts"] == 3
    assert audit["resolved"] is False


def test_restart_rebinds_fill_cursor_context_without_mutation(monkeypatch) -> None:
    exchange = CancelRaceExchange()
    exchange.open_snapshots = [[]]
    exchange.order_result = _order(status="filled", filled="1")
    exchange.recent = [_trade("restart-tail")]
    gateway = FormalDemoGateway(exchange)
    monkeypatch.setattr(gateway_module.time, "sleep", lambda _: None)

    gateway.set_cancel_reconciliation_context(since_ms=1_050, read_attempts=3)
    assert gateway.cancel_all_owned([CLIENT_ID]) == (CLIENT_ID,)
    assert exchange.trade_calls[0][1] == 1_050
    assert exchange.cancel_calls == 0


def test_cancel_context_rejects_retry_budget_expansion() -> None:
    gateway = FormalDemoGateway(CancelRaceExchange())
    with pytest.raises(FormalGatewayError, match="context"):
        gateway.set_cancel_reconciliation_context(since_ms=1_000, read_attempts=4)
