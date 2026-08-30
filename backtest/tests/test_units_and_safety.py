from decimal import Decimal

import optuna
import pytest

from backtest.robust_optimize import _build_trial_cfg, check_capital_feasibility
from backtest.runner import BacktestRunner, default_market_spec
from backtest.matching_engine import MatchingEngine
from backtest.synthetic_data import generate_regime_switching_gbm
from config import Config
from fill_tracker import Fill, FillTracker
from market_spec import OrderValidationError, validate_order
from order_manager import OrderManager


def test_contract_and_base_quantity_round_trip() -> None:
    spec = default_market_spec()
    assert spec.contracts_to_base(Decimal("1")) == Decimal("0.01")
    assert spec.base_to_contracts(Decimal("0.01"), exact=True) == Decimal("1")
    assert spec.compute_notional(Decimal("1"), Decimal("50000")) == Decimal("500.00")


def test_min_contract_and_min_notional_rejection() -> None:
    spec = default_market_spec()
    with pytest.raises(OrderValidationError, match="minimum"):
        validate_order(
            spec=spec, side="buy", price=Decimal("100"), amount=Decimal("0"),
            best_bid=Decimal("99"), best_ask=Decimal("101"),
            current_inventory=Decimal("0"), max_inventory=Decimal("0.01"),
            available_equity=Decimal("1000"), leverage=Decimal("1"),
            maker_fee_rate=Decimal("0.0002"), taker_fee_rate=Decimal("0.0005"),
        )


def test_runner_skips_infeasible_capital_orders_locally() -> None:
    cfg = Config(initial_capital=300.0)
    ticks = generate_regime_switching_gbm(n_days=1, ticks_per_day=48, seed=42)
    result = BacktestRunner(cfg, fill_mode="conservative").run(ticks)
    assert result.local_risk_rejection_count > 0
    assert result.invalid_order_count == 0
    assert result.bid_fills == result.ask_fills == 0
    assert check_capital_feasibility(300, 1, 0.01) is not None


def test_optuna_cannot_override_fixed_safety_invariants() -> None:
    params = {
        "gamma": 0.1, "k": 2.0, "tau": 1.0,
        "trend_spread_multiplier": 2.0, "trend_size_multiplier": 0.5,
        "range_spread_multiplier": 1.0, "imbalance_skew_factor": 0.2,
        "inventory_skew_factor": 1.0, "ema_span": 20,
    }
    cfg = Config(initial_capital=1000.0, max_drawdown_pct=0.031)
    trial_cfg = _build_trial_cfg(optuna.trial.FixedTrial(params), cfg)
    assert trial_cfg.fixed_lot_size == 0.01
    assert trial_cfg.max_inventory_lots == 1
    assert trial_cfg.max_drawdown_pct == 0.031
    assert trial_cfg.leverage == cfg.leverage


class _ExchangeStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_order(self, *args):
        self.calls.append(args)
        return {"id": "flatten-1"}

    def create_limit_order(self, *args):
        self.calls.append(args)
        return {"id": "limit-1"}


def test_production_flatten_converts_base_to_reduce_only_contracts() -> None:
    exchange = _ExchangeStub()
    manager = OrderManager(Config(initial_capital=1000.0))
    manager.flatten_position(
        exchange, "BTC/USDT:USDT", 0.01,
        {"market_spec": default_market_spec()},
    )
    symbol, order_type, side, contracts, price, params = exchange.calls[0]
    assert (symbol, order_type, side, contracts, price) == (
        "BTC/USDT:USDT", "market", "sell", 1.0, None
    )
    assert params == {"reduceOnly": True, "timeInForce": "IOC"}


def test_production_order_path_enforces_margin_cap() -> None:
    exchange = _ExchangeStub()
    manager = OrderManager(Config(initial_capital=300.0))
    placed = manager._place_order(
        exchange, "BTC/USDT:USDT", "buy", 50_000.0, 0.01,
        {"market_spec": default_market_spec()}, 0.0, 300.0,
        49_999.0, 50_001.0,
    )
    assert placed is None
    assert exchange.calls == []


def test_probabilistic_partial_fill_leaves_residual_order() -> None:
    engine = MatchingEngine(fill_mode="probabilistic", fill_seed=0)
    tracker = FillTracker(Config(initial_capital=1000.0))
    engine.place_order("buy", 49_999.0, 0.01)
    tick = {"bids": [[49_999.5, 1]], "asks": [[50_000.0, 1]], "timestamp": 1}
    fills = engine.check_fills(tick, tracker)
    assert len(fills) == 1
    assert 0 < fills[0].size < 0.01
    assert engine.pending_count == 1


def test_backtest_flatten_uses_taker_fee_and_adverse_slippage() -> None:
    cfg = Config(initial_capital=1000.0)
    tracker = FillTracker(cfg)
    tracker.process_fill(Fill("open", "order", "buy", 50_000.0, 0.01, 0.0))
    engine = MatchingEngine(taker_fee_rate=0.0005)
    fill = engine.execute_market_flatten(
        {"bids": [[49_990.0, 1]], "asks": [[50_010.0, 1]], "timestamp": 1},
        tracker, slippage_bps=10.0, reason="kill",
    )
    assert fill is not None
    assert fill.liquidity == "taker"
    assert fill.price < 49_990.0
    assert fill.fee == pytest.approx(fill.price * 0.01 * 0.0005)
    assert tracker.position == pytest.approx(0.0)


def test_exchange_trade_contract_amount_and_base_fee_are_converted() -> None:
    tracker = FillTracker(Config(initial_capital=1000.0))
    fill = tracker._fill_from_exchange_trade({
        "id": "trade-1", "order": "order-1", "side": "buy",
        "price": 50_000.0, "amount": 1, "timestamp": 1000,
        "fee": {"cost": 0.000001, "currency": "BTC"},
        "takerOrMaker": "maker",
    }, default_market_spec())
    assert fill.size == pytest.approx(0.01)
    assert fill.fee == pytest.approx(0.05)
    assert fill.fee_currency == "USDT"
