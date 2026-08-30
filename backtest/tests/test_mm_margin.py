"""Focused unit, reserve, release, and admission tests for MM margin."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from backtest.matching_engine import CancellationReason, MatchingEngine
from backtest.mm_protocol import scenario_ticks
from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_margin_diagnostic import diagnostic_gates_pass
from market_maker.as_config import MarketMakerV1Config
from market_maker.margin import (
    UNIT_CONTRACT,
    OrderExposure,
    admit_proposed_orders,
    margin_components,
)
from market_spec import MarketSpec


def _order(side: str, price: float = 50_000.0, size: float = 0.01):
    return OrderExposure(
        side=side, price_usdt_per_btc=price, quantity_btc=size
    )


def _context() -> dict:
    return {
        "reason": CancellationReason.CANCEL_MARGIN_PROTECTION,
        "source_component": "test_mm_margin",
        "source_event": "reserve_release",
        "timestamp_ms": 1,
        "strategy_version": "market-maker-v1",
        "profile_fingerprint": "test-profile",
        "protocol_id": "MM_MARGIN_TEST",
    }


def _components(**overrides):
    values = {
        "inventory_btc": 0.0,
        "mid_price_usdt_per_btc": 50_000.0,
        "current_equity_usdt": 300.0,
        "leverage": 3.0,
    }
    values.update(overrides)
    return margin_components(**values)


def test_unit_contract_is_canonical_btc():
    assert UNIT_CONTRACT["quantity_input_unit"] == "BTC base quantity"
    assert UNIT_CONTRACT["internal_quantity_unit"] == "BTC base quantity"
    assert UNIT_CONTRACT["contract_multiplier_btc"] == 1.0
    assert UNIT_CONTRACT["lot_size_btc"] == 0.01


def test_contract_conversion_is_applied_once():
    spec = MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal("0.01"),
        amount_step=Decimal("1"),
        min_amount=Decimal("1"),
        min_notional=Decimal("5"),
        price_tick=Decimal("0.1"),
        amount_precision=None,
        price_precision=None,
        linear=True,
        inverse=False,
    )
    contracts = spec.base_to_contracts(Decimal("0.01"))
    assert contracts == Decimal("1")
    assert spec.contracts_to_base(contracts) == Decimal("0.01")
    assert spec.compute_notional(Decimal("50000"), contracts) == Decimal("500")


def test_notional_and_leverage_formula():
    order = _order("buy")
    assert order.notional_usdt == 500.0
    components = _components(proposed_orders=[order])
    assert components.proposed_bid_order_reserve_usdt == pytest.approx(500 / 3)


def test_current_equity_is_utilization_denominator():
    low = _components(current_equity_usdt=300, proposed_orders=[_order("buy")])
    high = _components(current_equity_usdt=500, proposed_orders=[_order("buy")])
    assert low.margin_utilization == pytest.approx(5 / 9)
    assert high.margin_utilization == pytest.approx(1 / 3)


def test_active_bid_and_ask_reserves_are_separate_and_gross():
    components = _components(active_orders=[_order("buy"), _order("sell")])
    assert components.active_bid_order_reserve_usdt == pytest.approx(500 / 3)
    assert components.active_ask_order_reserve_usdt == pytest.approx(500 / 3)
    assert components.pending_order_reserve_usdt == pytest.approx(1000 / 3)


def test_proposed_reserve_is_separate_from_active_reserve():
    components = _components(
        active_orders=[_order("buy")], proposed_orders=[_order("sell")]
    )
    assert components.active_bid_order_reserve_usdt > 0
    assert components.proposed_ask_order_reserve_usdt > 0


@pytest.mark.parametrize(
    ("inventory", "side", "expected"),
    [(0.01, "sell", "REDUCING"), (0.01, "buy", "INCREASING"),
     (-0.01, "buy", "REDUCING"), (-0.01, "sell", "INCREASING")],
)
def test_inventory_impact_is_explicit(inventory, side, expected):
    decision = admit_proposed_orders(
        inventory_btc=inventory,
        mid_price_usdt_per_btc=50_000,
        current_equity_usdt=500,
        leverage=3,
        maximum_margin_utilization=0.8,
        active_orders=[],
        proposed_orders=[_order(side)],
    )[0]
    assert decision.inventory_impact == expected


def test_two_sided_gross_reserve_reproduces_111_percent():
    components = _components(
        proposed_orders=[_order("buy"), _order("sell")]
    )
    assert components.margin_utilization == pytest.approx(10 / 9)


def test_netted_model_is_diagnostic_and_uses_maximum_side():
    components = _components(
        proposed_orders=[_order("buy"), _order("sell")],
        reserve_model="NETTED_DIAGNOSTIC",
    )
    assert components.pending_order_reserve_usdt == pytest.approx(500 / 3)
    assert components.reserve_model == "NETTED_DIAGNOSTIC"


def test_flat_300_admits_only_lower_notional_side():
    decisions = admit_proposed_orders(
        inventory_btc=0,
        mid_price_usdt_per_btc=50_000,
        current_equity_usdt=300,
        leverage=3,
        maximum_margin_utilization=0.8,
        active_orders=[],
        proposed_orders=[_order("buy", 49_999), _order("sell", 50_001)],
    )
    assert [item.side for item in decisions if item.allowed] == ["buy"]
    assert next(item for item in decisions if item.side == "sell").reason == (
        "SUPPRESS_PENDING_ORDER_RESERVE"
    )


def test_long_inventory_prefers_reducing_ask_when_only_one_side_fits():
    decisions = admit_proposed_orders(
        inventory_btc=0.01,
        mid_price_usdt_per_btc=50_000,
        current_equity_usdt=500,
        leverage=3,
        maximum_margin_utilization=0.8,
        active_orders=[],
        proposed_orders=[_order("buy"), _order("sell")],
    )
    assert [item.side for item in decisions if item.allowed] == ["sell"]


def test_nonpositive_equity_fails_closed():
    decision = admit_proposed_orders(
        inventory_btc=0,
        mid_price_usdt_per_btc=50_000,
        current_equity_usdt=0,
        leverage=3,
        maximum_margin_utilization=0.8,
        active_orders=[],
        proposed_orders=[_order("buy")],
    )[0]
    assert not decision.allowed
    assert decision.reason == "SUPPRESS_EQUITY_NONPOSITIVE"
    assert math.isinf(decision.projected_utilization)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"inventory_btc": float("nan")},
        {"mid_price_usdt_per_btc": float("inf")},
        {"leverage": 0},
        {"maker_fee_rate": -0.1},
    ],
)
def test_unknown_or_invalid_margin_state_fails(kwargs):
    with pytest.raises(ValueError):
        _components(**kwargs)


def test_fee_reserve_counted_once():
    components = _components(
        active_orders=[_order("buy")],
        proposed_orders=[_order("sell")],
        maker_fee_rate=0.0002,
    )
    assert components.fee_reserve_usdt == pytest.approx(0.2)


def test_cancellation_releases_active_reserve():
    engine = MatchingEngine()
    engine.place_order("buy", 50_000, 0.01)
    before = _components(active_orders=[
        _order(order.side, order.price, order.size)
        for order in engine.pending_orders
    ])
    engine.cancel_all(**_context())
    after = _components(active_orders=[
        _order(order.side, order.price, order.size)
        for order in engine.pending_orders
    ])
    assert before.pending_order_reserve_usdt > 0
    assert after.pending_order_reserve_usdt == 0


def test_expiry_releases_active_reserve():
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 50_000, 0.01)
    engine.expire_order(
        order_id,
        timestamp_ms=1,
        source_component="test_mm_margin",
        source_event="expiry",
    )
    assert engine.pending_count == 0


def test_rejected_order_never_reserves_pending_margin():
    engine = MatchingEngine()
    engine.record_rejected_before_activation(
        side="buy", price=50_000, size=0.01,
        timestamp_ms=1, rejection_reason="MARGIN",
    )
    assert engine.pending_count == 0


def test_partial_fill_splits_position_and_residual_reserve():
    components = _components(
        inventory_btc=0.005,
        active_orders=[_order("buy", size=0.005)],
    )
    assert components.position_margin_usdt == pytest.approx(250 / 3)
    assert components.pending_order_reserve_usdt == pytest.approx(250 / 3)
    assert components.total_modeled_margin_usdt == pytest.approx(500 / 3)


def test_terminal_cleanup_has_no_pending_reserve():
    engine = MatchingEngine()
    engine.place_order("sell", 50_000, 0.01)
    engine.cancel_all(**_context())
    assert engine.pending_count == 0
    assert _components(active_orders=[]).pending_order_reserve_usdt == 0


def test_no_duplicate_or_negative_reserve():
    order = _order("buy")
    components = _components(active_orders=[order])
    assert components.pending_order_reserve_usdt == pytest.approx(500 / 3)
    assert all(
        value >= 0
        for value in components.to_dict().values()
        if isinstance(value, (int, float))
    )


def test_runner_pre_quote_admission_prevents_predictable_breach():
    profile = MarketMakerV1Config()
    ticks = scenario_ticks("normal_range", 99_301, count=80)
    result = MarketMakerBacktestRunner(
        profile,
        protocol_id="MM_MARGIN_ENGINEERING",
        scenario="normal_range",
        source_block="margin-engineering",
        fill_seed=99_302,
        initial_capital_usdt=300,
        leverage=3,
    ).run(ticks)
    assert result.preventable_margin_breaches == 0
    assert result.unknown_margin_states == 0
    assert any(
        event.get("reason") == "SUPPRESS_PENDING_ORDER_RESERVE"
        for event in result.margin_events
    )


def test_zero_unclassified_removals_is_a_passing_integrity_value():
    assert diagnostic_gates_pass(
        {
            "order_counts_reconcile": True,
            "order_quantities_reconcile": True,
            "margin_components_reconcile": True,
            "unclassified_removals": 0,
            "determinism_passed": True,
        },
        {
            "preventable_margin_breaches": 0,
            "unknown_margin_states": 0,
            "negative_reserves": 0,
            "nonfinite_utilization": 0,
        },
    )
