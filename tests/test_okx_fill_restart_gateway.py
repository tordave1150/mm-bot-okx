import time
from decimal import Decimal

import pytest

import okx_fill_restart_gateway
from market_spec import MarketSpec
from okx_fill_restart_gateway import (
    FormalDemoGateway,
    FormalGatewayError,
    PostOnlyCreateRejected,
    PostOnlyWouldCross,
)


def _spec() -> MarketSpec:
    return MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.01"),
        amount_step=Decimal("1"),
        min_amount=Decimal("1"),
        min_notional=None,
        price_tick=Decimal("0.1"),
        amount_precision=0,
        price_precision=1,
        linear=True,
        inverse=False,
    )


class GatewayExchange:
    def __init__(self) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.hostname = "www.okx.com"
        self.orders = {}
        self.create_payloads = []
        self.cancel_payloads = []
        self.history_trades = []
        self.recent_trades = []
        self.trade_calls = []

    def fetch_markets(self):
        return [{"id": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT"}]

    def set_markets(self, markets):
        self.markets = {row["symbol"]: row for row in markets}

    def fetch_time(self):
        return int(time.time() * 1000)

    def privateGetAccountConfig(self):
        return {"data": [{
            "uid": "demo-account",
            "posMode": "net_mode",
            "perm": "read_only,trade",
        }]}

    def fetch_balance(self):
        return {"USDT": {"total": 1000, "free": 1000}}

    def fetch_positions(self, symbols):
        return []

    def privateGetAccountPositions(self, params):
        assert params == {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
        return {"data": []}

    def privateGetTradeOrdersPending(self, params):
        assert params == {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
        return {"data": []}

    def fetch_open_orders(self, symbol):
        return [row.copy() for row in self.orders.values()]

    def fetch_leverage(self, symbol, params):
        return {"longLeverage": 3, "shortLeverage": 3}

    def fetch_trading_fee(self, symbol):
        return {"maker": 0.0002, "taker": 0.0005}

    def fetch_order_book(self, symbol):
        return {
            "timestamp": int(time.time() * 1000),
            "bids": [[49_999.0, 2.0]],
            "asks": [[50_001.0, 2.0]],
        }

    def fetch_my_trades(self, symbol, since, limit, params):
        self.trade_calls.append((symbol, since, limit, dict(params)))
        if params.get("paginate") is True:
            return [row.copy() for row in self.history_trades]
        assert since is None
        assert limit == 100
        assert params == {}
        return [row.copy() for row in self.recent_trades]

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_payloads.append((symbol, order_type, side, amount, price, params))
        order = {
            "id": f"order-{len(self.create_payloads)}",
            "clientOrderId": params["clOrdId"],
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "postOnly": bool(params.get("postOnly")),
            "info": {
                "clOrdId": params["clOrdId"],
                "ordType": "post_only" if params.get("postOnly") else "market",
            },
        }
        self.orders[params["clOrdId"]] = order
        return order.copy()

    def fetch_order(self, order_id, symbol, params):
        return self.orders[params["clientOrderId"]].copy()

    def cancel_order(self, order_id, symbol):
        self.cancel_payloads.append((order_id, symbol))
        key = next(key for key, row in self.orders.items() if row["id"] == order_id)
        del self.orders[key]
        return {"id": order_id, "status": "canceled"}


@pytest.fixture
def gateway(monkeypatch):
    exchange = GatewayExchange()
    monkeypatch.setattr(
        okx_fill_restart_gateway,
        "fetch_market_info",
        lambda *args, **kwargs: {"market_spec": _spec()},
    )
    result = FormalDemoGateway(exchange)
    result.load_market()
    return result


def test_gateway_rejects_any_unproven_demo_transport(monkeypatch) -> None:
    exchange = GatewayExchange()
    exchange.headers = {}
    gateway = FormalDemoGateway(exchange)
    with pytest.raises(FormalGatewayError, match="transport"):
        gateway.fetch_book(maximum_age_ms=1_000, maximum_clock_skew_ms=1_500)
    assert gateway.live_endpoint_attempts == 1
    assert exchange.create_payloads == []


def test_account_snapshot_is_hashed_complete_and_non_withdrawal(gateway) -> None:
    snapshot = gateway.fetch_account()
    assert snapshot.account_binding != "demo-account"
    assert len(snapshot.account_binding) == 64
    assert snapshot.permissions == ("read_only", "trade")
    assert snapshot.position_mode == "net_mode"
    assert snapshot.leverage == Decimal("3")
    assert snapshot.position_btc == 0


def test_terminal_account_snapshot_does_not_require_market_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = GatewayExchange()
    gateway = FormalDemoGateway(exchange)
    monkeypatch.setattr(
        exchange,
        "fetch_markets",
        lambda: (_ for _ in ()).throw(TimeoutError("metadata unavailable")),
    )

    snapshot = gateway.fetch_terminal_account_only()

    assert gateway.market_spec is None
    assert snapshot.account_binding != "demo-account"
    assert snapshot.position_btc == 0
    assert snapshot.open_orders == ()
    assert gateway.mutation_calls == []
    assert "fetch_markets" not in gateway.read_calls
    assert "fetch_positions" not in gateway.read_calls
    assert "fetch_open_orders" not in gateway.read_calls


def test_terminal_account_snapshot_converts_signed_net_partial_position() -> None:
    exchange = GatewayExchange()
    exchange.privateGetAccountPositions = lambda params: {
        "data": [{"instId": "BTC-USDT-SWAP", "pos": "-2", "posSide": "net"}]
    }
    gateway = FormalDemoGateway(exchange)

    snapshot = gateway.fetch_terminal_account_only()

    assert snapshot.position_btc == Decimal("-0.02")


def test_terminal_account_time_failure_keeps_only_sanitized_partial_diagnostic() -> None:
    exchange = GatewayExchange()
    exchange.privateGetAccountPositions = lambda params: {
        "data": [{"instId": "BTC-USDT-SWAP", "pos": "-1", "posSide": "net"}]
    }
    exchange.fetch_time = lambda: (_ for _ in ()).throw(TimeoutError("unavailable"))
    gateway = FormalDemoGateway(exchange)

    with pytest.raises(TimeoutError):
        gateway.fetch_terminal_account_only()

    partial = gateway.last_terminal_partial_diagnostic
    assert partial is not None
    assert partial["position_btc"] == "-0.01"
    assert partial["open_orders"] == 0
    assert partial["clock_verified"] is False
    assert len(str(partial["account_binding"])) == 64
    assert "fetch_time" == gateway.read_calls[-1]


def test_normal_create_and_cancel_are_exactly_scoped(gateway) -> None:
    client_id = "fr" + "a" * 28
    response = gateway.submit_post_only(
        client_order_id=client_id,
        side="buy",
        price=Decimal("49998"),
        quantity_btc=Decimal("0.01"),
        maximum_age_ms=1_000,
        maximum_clock_skew_ms=1_500,
    )
    assert response["clientOrderId"] == client_id
    params = gateway.exchange.create_payloads[0][-1]
    assert params == {
        "postOnly": True,
        "reduceOnly": False,
        "tdMode": "isolated",
        "clOrdId": client_id,
    }
    assert gateway.cancel_all_owned([client_id]) == (client_id,)
    assert gateway.exchange.orders == {}


def test_crossed_post_only_quote_is_typed_and_blocked_before_dispatch(
    gateway,
) -> None:
    client_id = "fr" + "c" * 28
    callbacks: list[str] = []
    with pytest.raises(PostOnlyWouldCross) as caught:
        gateway.submit_post_only(
            client_order_id=client_id,
            side="buy",
            price=Decimal("50001"),
            quantity_btc=Decimal("0.01"),
            maximum_age_ms=1_000,
            maximum_clock_skew_ms=1_500,
            before_dispatch=lambda: callbacks.append("dispatched"),
        )
    assert callbacks == []
    assert gateway.exchange.create_payloads == []
    assert gateway.public_audit()["normal_create_dispatches"] == 0
    assert caught.value.public_dict() == {
        "classification": "PRE_DISPATCH_POST_ONLY_WOULD_CROSS",
        "client_order_id": client_id,
        "side": "buy",
        "price": "50001",
        "best_bid": "49999.0",
        "best_ask": "50001.0",
        "mutation_dispatched": False,
        "mutation_retry": False,
    }


def test_terminal_post_only_rejection_is_classified_after_absence_reconciliation(
    gateway,
) -> None:
    client_id = "fr" + "d" * 28
    callbacks: list[str] = []

    def rejected_create(symbol, order_type, side, amount, price, params):
        gateway.exchange.create_payloads.append(
            (symbol, order_type, side, amount, price, params)
        )
        return {
            "id": "rejected-order",
            "clientOrderId": params["clOrdId"],
            "side": side,
            "status": "rejected",
            "filled": 0,
            "postOnly": True,
            "info": {
                "clOrdId": params["clOrdId"],
                "ordType": "post_only",
                "sCode": "51008",
                "state": "rejected",
            },
        }

    gateway.exchange.create_order = rejected_create
    gateway.exchange.fetch_order = lambda *args, **kwargs: (_ for _ in ()).throw(
        KeyError("absent")
    )
    with pytest.raises(PostOnlyCreateRejected) as caught:
        gateway.submit_post_only(
            client_order_id=client_id,
            side="buy",
            price=Decimal("49998"),
            quantity_btc=Decimal("0.01"),
            maximum_age_ms=1_000,
            maximum_clock_skew_ms=1_500,
            before_dispatch=lambda: callbacks.append("durable"),
        )
    assert callbacks == ["durable"]
    assert caught.value.public_dict() == {
        "classification": "REJECTED_TERMINAL_ABSENT",
        "client_order_id": client_id,
        "status": "rejected",
        "exchange_code": "51008",
        "mutation_retry": False,
        "authoritative_absence": True,
    }
    assert gateway.public_audit()["normal_create_dispatches"] == 1
    assert gateway.exchange.orders == {}


def test_foreign_order_blocks_cancel_without_mutation(gateway) -> None:
    gateway.exchange.orders["foreign"] = {
        "id": "foreign-order",
        "clientOrderId": "foreign",
        "side": "buy",
    }
    with pytest.raises(FormalGatewayError, match="foreign"):
        gateway.cancel_all_owned([])
    assert gateway.exchange.cancel_payloads == []


def test_flatten_is_reduce_only_and_single_flight(gateway) -> None:
    client_id = "fr" + "b" * 28
    gateway.submit_reduce_only_flatten(
        client_order_id=client_id,
        position_btc=Decimal("0.01"),
    )
    params = gateway.exchange.create_payloads[0][-1]
    assert params == {
        "reduceOnly": True,
        "tdMode": "isolated",
        "clOrdId": client_id,
    }
    with pytest.raises(FormalGatewayError, match="single-flight"):
        gateway.submit_reduce_only_flatten(
            client_order_id="fr" + "c" * 28,
            position_btc=Decimal("0.01"),
        )


def _trade(
    trade_id: str,
    *,
    timestamp: int,
    client_id: str = "fr-owned",
    price: float = 50_000.0,
    amount: float = 1.0,
) -> dict:
    return {
        "id": trade_id,
        "order": f"order-{trade_id}",
        "timestamp": timestamp,
        "side": "buy",
        "price": price,
        "amount": amount,
        "fee": {"cost": 0.10, "currency": "USDT"},
        "takerOrMaker": "maker",
        "info": {"clOrdId": client_id},
    }


def test_fill_union_recovers_fresh_tail_omission_and_deduplicates(gateway) -> None:
    overlap = _trade("trade-a", timestamp=1_100)
    fresh = _trade("trade-b", timestamp=1_200)
    gateway.exchange.history_trades = [overlap]
    gateway.exchange.recent_trades = [
        _trade("old", timestamp=999),
        dict(overlap),
        fresh,
    ]

    rows = gateway.fetch_trades(since_ms=1_000)

    assert [row["id"] for row in rows] == ["trade-a", "trade-b"]
    assert len(gateway.exchange.trade_calls) == 2
    assert gateway.exchange.trade_calls[0][3] == {
        "paginate": True,
        "paginationCalls": 3,
    }
    assert gateway.exchange.trade_calls[1][1:] == (None, 100, {})
    assert gateway.last_fill_union_audit == {
        "history_rows": 1,
        "recent_tail_rows": 3,
        "eligible_history_rows": 1,
        "eligible_recent_tail_rows": 2,
        "deduplicated_overlap_rows": 1,
        "union_rows": 2,
    }
    assert gateway.public_audit()["fill_union_duplicates"] == 1


def test_fill_union_rejects_conflicting_duplicate_identity(gateway) -> None:
    gateway.exchange.history_trades = [_trade("trade-a", timestamp=1_100)]
    gateway.exchange.recent_trades = [
        _trade("trade-a", timestamp=1_100, price=50_001.0)
    ]

    with pytest.raises(FormalGatewayError, match="identity conflict"):
        gateway.fetch_trades(since_ms=1_000)

    assert gateway.fill_union_conflicts == 1
