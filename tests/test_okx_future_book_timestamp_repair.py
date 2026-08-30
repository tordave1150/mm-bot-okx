import ast
from decimal import Decimal
from pathlib import Path

import pytest

import okx_fill_restart_gateway
from market_spec import MarketSpec
from okx_demo_runtime import MarketDataGate, MarketDataSafetyError, signed_book_age_ms
from okx_fill_restart_gateway import FormalDemoGateway, FormalGatewayError


NOW_MS = 2_000_000
MAXIMUM_AGE_MS = 1_000
MAXIMUM_CLOCK_SKEW_MS = 1_500
ROOT = Path(__file__).resolve().parents[1]


class BookExchange:
    def __init__(self) -> None:
        self.options = {"sandboxMode": True}
        self.headers = {"x-simulated-trading": "1"}
        self.hostname = "www.okx.com"
        self.timestamp = NOW_MS
        self.bids = [[49_999.0, 2.0]]
        self.asks = [[50_001.0, 2.0]]
        self.create_calls = 0

    def fetch_order_book(self, _symbol):
        return {
            "timestamp": self.timestamp,
            "bids": self.bids,
            "asks": self.asks,
        }

    def create_order(self, *_args, **_kwargs):
        self.create_calls += 1
        return {"id": "unexpected"}


def _market_spec() -> MarketSpec:
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


def _gateway(monkeypatch, *, age_ms: int = 0) -> tuple[FormalDemoGateway, BookExchange]:
    exchange = BookExchange()
    exchange.timestamp = NOW_MS - age_ms
    monkeypatch.setattr(okx_fill_restart_gateway.time, "time", lambda: NOW_MS / 1000)
    gateway = FormalDemoGateway(exchange)
    return gateway, exchange


def _fetch(gateway: FormalDemoGateway):
    return gateway.fetch_book(
        maximum_age_ms=MAXIMUM_AGE_MS,
        maximum_clock_skew_ms=MAXIMUM_CLOCK_SKEW_MS,
    )


@pytest.mark.parametrize("age_ms", [0, 1, MAXIMUM_AGE_MS, -1, -49, -MAXIMUM_CLOCK_SKEW_MS])
def test_signed_book_age_accepts_both_exact_boundaries(monkeypatch, age_ms: int) -> None:
    gateway, exchange = _gateway(monkeypatch, age_ms=age_ms)
    book = _fetch(gateway)
    assert book.age_ms == age_ms
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


@pytest.mark.parametrize(
    "age_ms",
    [MAXIMUM_AGE_MS + 1, -(MAXIMUM_CLOCK_SKEW_MS + 1)],
)
def test_signed_book_age_fails_closed_one_unit_beyond_each_budget(
    monkeypatch, age_ms: int
) -> None:
    gateway, exchange = _gateway(monkeypatch, age_ms=age_ms)
    with pytest.raises(FormalGatewayError, match="stale or invalid"):
        _fetch(gateway)
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


@pytest.mark.parametrize("budget", [0, -1, True, 1.0, "1500"])
def test_invalid_clock_skew_budget_fails_closed(monkeypatch, budget: object) -> None:
    gateway, exchange = _gateway(monkeypatch, age_ms=-1)
    with pytest.raises(FormalGatewayError, match="stale or invalid"):
        gateway.fetch_book(
            maximum_age_ms=MAXIMUM_AGE_MS,
            maximum_clock_skew_ms=budget,
        )
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


@pytest.mark.parametrize("timestamp", [None, 0, -1, True, 2_000_000.0, "2000000"])
def test_missing_nonpositive_or_noninteger_timestamp_fails_closed(
    monkeypatch, timestamp: object
) -> None:
    gateway, exchange = _gateway(monkeypatch)
    exchange.timestamp = timestamp
    with pytest.raises(FormalGatewayError, match="timestamp is invalid"):
        _fetch(gateway)
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


@pytest.mark.parametrize(
    ("bids", "asks", "message"),
    [
        ([], [[50_001.0, 1.0]], "empty"),
        ([[49_999.0, 1.0]], [], "empty"),
        ([[50_001.0, 1.0]], [[50_000.0, 1.0]], "stale or invalid"),
        ([[0.0, 1.0]], [[50_001.0, 1.0]], "stale or invalid"),
        ([["bad", 1.0]], [[50_001.0, 1.0]], "invalid"),
    ],
)
def test_invalid_book_shape_or_spread_fails_closed(
    monkeypatch, bids: list, asks: list, message: str
) -> None:
    gateway, exchange = _gateway(monkeypatch)
    exchange.bids = bids
    exchange.asks = asks
    with pytest.raises(FormalGatewayError, match=message):
        _fetch(gateway)
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


def test_future_budget_never_hides_positive_staleness(monkeypatch) -> None:
    gateway, _exchange = _gateway(monkeypatch, age_ms=MAXIMUM_AGE_MS + 1)
    with pytest.raises(FormalGatewayError, match="stale or invalid"):
        gateway.fetch_book(
            maximum_age_ms=MAXIMUM_AGE_MS,
            maximum_clock_skew_ms=99_999,
        )


def test_rejected_future_book_blocks_post_only_mutation(monkeypatch) -> None:
    gateway, exchange = _gateway(
        monkeypatch, age_ms=-(MAXIMUM_CLOCK_SKEW_MS + 1)
    )
    gateway.market_spec = _market_spec()
    with pytest.raises(FormalGatewayError, match="stale or invalid"):
        gateway.submit_post_only(
            client_order_id="fr" + "a" * 28,
            side="buy",
            price=Decimal("49998"),
            quantity_btc=Decimal("0.01"),
            maximum_age_ms=MAXIMUM_AGE_MS,
            maximum_clock_skew_ms=MAXIMUM_CLOCK_SKEW_MS,
        )
    assert gateway.mutation_calls == []
    assert exchange.create_calls == 0


def test_preflight_gate_and_formal_gateway_share_asymmetric_age_semantics() -> None:
    gate = MarketDataGate(
        maximum_age_ms=MAXIMUM_AGE_MS,
        maximum_clock_skew_ms=MAXIMUM_CLOCK_SKEW_MS,
    )
    future = {
        "timestamp": NOW_MS + MAXIMUM_CLOCK_SKEW_MS,
        "bids": [[49_999.0, 1.0]],
        "asks": [[50_001.0, 1.0]],
    }
    assert gate.validate(future, now_ms=NOW_MS)["age_ms"] == -1_500
    too_stale = {**future, "timestamp": NOW_MS - MAXIMUM_AGE_MS - 1}
    with pytest.raises(MarketDataSafetyError, match="stale or future-dated"):
        MarketDataGate().validate(too_stale, now_ms=NOW_MS)
    assert signed_book_age_ms(
        observed_at_ms=NOW_MS,
        exchange_timestamp_ms=NOW_MS + MAXIMUM_CLOCK_SKEW_MS,
        maximum_age_ms=MAXIMUM_AGE_MS,
        maximum_clock_skew_ms=MAXIMUM_CLOCK_SKEW_MS,
    ) == -1_500


def test_market_gate_preserves_duplicate_and_regression_fail_closed() -> None:
    gate = MarketDataGate()
    book = {
        "timestamp": NOW_MS,
        "bids": [[49_999.0, 1.0]],
        "asks": [[50_001.0, 1.0]],
    }
    gate.validate(book, now_ms=NOW_MS)
    with pytest.raises(MarketDataSafetyError, match="duplicate"):
        gate.validate(book, now_ms=NOW_MS)
    with pytest.raises(MarketDataSafetyError, match="non-monotonic"):
        gate.validate({**book, "timestamp": NOW_MS - 1}, now_ms=NOW_MS)


def test_every_formal_book_decision_passes_both_frozen_budgets_explicitly() -> None:
    calls = []
    for relative in ("okx_fill_restart_executor.py", "okx_fill_restart_gateway.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"fetch_book", "submit_post_only"}:
                continue
            calls.append((relative, node.func.attr, {row.arg for row in node.keywords}))
    assert calls
    for relative, method, keywords in calls:
        assert "maximum_age_ms" in keywords, (relative, method)
        assert "maximum_clock_skew_ms" in keywords, (relative, method)
