"""Direct contract tests for Market Maker v1 reorientation and smoke."""

from __future__ import annotations

import ast
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from backtest.mm_optimize import evaluate_candidate, sample_candidates
from backtest.mm_protocol import (
    MAX_EVALUATED_CANDIDATES,
    PROPOSAL_CEILING,
    VALID_CANDIDATE_TARGET,
    payload_hash,
    scenario_ticks,
)
from backtest.mm_runner import MarketMakerBacktestRunner
from backtest.mm_smoke import build_protocol, declare
from config import Config
from market_maker.as_config import MarketMakerV1Config, UNIT_CONTRACT
from market_maker.as_strategy import MarketMakerV1Strategy
from market_maker.diagnostics import (
    conservative_objective,
    favorable_markout,
    top_profit_removal,
)
from market_maker.inventory import inventory_control
from market_maker.quote_model import (
    avellaneda_stoikov_half_spread_bps,
    build_quotes,
    reservation_price,
)
from market_maker.registry import (
    ACTIVE_RESEARCH_STRATEGIES,
    MissingStrategyError,
    RemovedStrategyError,
    UnknownStrategyError,
    select_research_strategy,
)
from market_maker.volatility import CausalEWMAVolatility


def _profile(**overrides) -> MarketMakerV1Config:
    return MarketMakerV1Config(**overrides)


def _tick(mid: float, timestamp: int = 1) -> dict:
    return {
        "bids": [[mid - 5.0, 1.0]],
        "asks": [[mid + 5.0, 1.0]],
        "timestamp": timestamp,
    }


def _engineering_path(seed: int = 99_101) -> dict:
    ticks = scenario_ticks("normal_range", seed, count=120)
    return {
        "market_path_id": f"mm-engineering-fixture-{seed}",
        "market_path_hash": payload_hash(ticks),
        "scenario": "normal_range",
        "scenario_parameters": {"tick_count": len(ticks)},
        "market_seed": seed,
        "fill_seed": seed + 1,
        "source_block": "mm-engineering-fixture",
        "synthetic_generator_version": "mm-v1-synthetic-v1",
        "index_range": [0, len(ticks) - 1],
        "cancel_latency_ticks": 0,
        "split": "ENGINEERING_FIXTURE_ONLY",
    }


def test_exactly_one_active_research_strategy():
    assert list(ACTIVE_RESEARCH_STRATEGIES) == ["market_maker_v1"]
    assert select_research_strategy("market_maker_v1") is MarketMakerV1Strategy


@pytest.mark.parametrize("name", [None, "", "   "])
def test_missing_strategy_fails_closed(name):
    with pytest.raises(MissingStrategyError):
        select_research_strategy(name)


@pytest.mark.parametrize("name", ["unknown", "volatility", "market-maker"])
def test_unknown_strategy_fails_closed(name):
    with pytest.raises(UnknownStrategyError):
        select_research_strategy(name)


@pytest.mark.parametrize("name", ["alpha", "alpha_v1", "alpha_v2", "alpha_v3"])
def test_removed_strategy_names_fail_closed(name):
    with pytest.raises(RemovedStrategyError):
        select_research_strategy(name)


def test_shared_modules_do_not_import_strategy_implementation():
    root = Path.cwd()
    for relative in (
        "market_spec.py",
        "market_state.py",
        "fill_tracker.py",
        "order_manager.py",
        "risk_manager.py",
        "backtest/matching_engine.py",
        "backtest/metrics.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        assert not any(name.startswith("market_maker") for name in imports)


def test_production_defaults_unchanged():
    before = asdict(Config())
    _profile().validate()
    sample_candidates()
    assert asdict(Config()) == before


def test_profile_has_exactly_ten_tunables_and_fixed_safety():
    profile = _profile()
    assert len(profile.tunable_parameters) == 10
    assert profile.fixed_lot_size_btc == 0.01
    assert profile.maximum_inventory_lots == 1
    assert profile.maximum_margin_utilization == 0.80
    assert profile.soft_session_loss_pct == 0.03
    assert profile.hard_kill_drawdown_pct == 0.05


def test_unit_contract_is_explicit():
    assert set(UNIT_CONTRACT) == {
        "mid_price",
        "inventory",
        "risk_aversion_gamma",
        "volatility",
        "volatility_variance",
        "time_horizon",
        "arrival_decay",
        "reservation_price",
        "half_spread",
        "tick_rounding",
        "normalization",
    }


def test_reservation_price_calculation_and_inventory_skew():
    neutral = reservation_price(
        mid_price=50_000.0,
        inventory_lots=0.0,
        gamma=0.1,
        return_variance=0.0001,
        time_horizon_ticks=10,
    )
    long = reservation_price(
        mid_price=50_000.0,
        inventory_lots=1.0,
        gamma=0.1,
        return_variance=0.0001,
        time_horizon_ticks=10,
    )
    short = reservation_price(
        mid_price=50_000.0,
        inventory_lots=-1.0,
        gamma=0.1,
        return_variance=0.0001,
        time_horizon_ticks=10,
    )
    assert neutral == pytest.approx(50_000.0)
    assert long < neutral < short


def test_half_spread_formula():
    actual = avellaneda_stoikov_half_spread_bps(
        gamma=0.1,
        arrival_decay=10_000.0,
        return_variance=0.0001,
        time_horizon_ticks=10,
    )
    assert actual > 0
    assert actual == pytest.approx(
        (
            __import__("math").log1p(0.1 / 10_000.0) / 0.1
            + 0.5 * 0.1 * 0.0001 * 10
        ) * 10_000.0
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"risk_aversion_gamma": 0.0},
        {"arrival_decay_k_or_proxy": 0.0},
        {"volatility_ewma_decay": 1.0},
        {"minimum_half_spread_bps": 10.0, "maximum_half_spread_bps": 5.0},
        {"minimum_order_lifetime_ticks": 5, "maximum_order_age_ticks": 4},
    ],
)
def test_invalid_profile_values_fail_closed(kwargs):
    with pytest.raises(ValueError):
        _profile(**kwargs).validate()


def test_causal_volatility_floor_cap_and_prefix_invariance():
    first = CausalEWMAVolatility(
        decay=0.9, minimum_samples=3, floor=0.001, cap=0.002
    )
    second = CausalEWMAVolatility(
        decay=0.9, minimum_samples=3, floor=0.001, cap=0.002
    )
    prefix = [100.0, 100.0, 100.0, 100.0]
    outputs_a = [first.update(value) for value in prefix]
    outputs_b = [second.update(value) for value in prefix]
    assert outputs_a == outputs_b
    assert first.volatility == pytest.approx(0.001)
    second.update(1_000.0)
    assert first.volatility == pytest.approx(0.001)
    assert second.volatility == pytest.approx(0.002)


def test_inventory_limit_suppresses_risk_increasing_side():
    long = inventory_control(
        inventory_btc=0.01,
        fixed_lot_size_btc=0.01,
        maximum_inventory_btc=0.01,
    )
    short = inventory_control(
        inventory_btc=-0.01,
        fixed_lot_size_btc=0.01,
        maximum_inventory_btc=0.01,
    )
    assert long.bid_suppressed and long.bid_size_btc == 0
    assert short.ask_suppressed and short.ask_size_btc == 0


def test_quotes_are_finite_tick_aligned_post_only_and_bid_below_ask():
    decision = build_quotes(
        config=_profile(),
        timestamp_ms=1,
        best_bid=49_995.0,
        best_ask=50_005.0,
        mid_price=50_000.0,
        imbalance=0.0,
        return_variance=0.000001,
        inventory_btc=0.0,
        feature_fingerprint="fixture",
    )
    assert decision.quote_allowed
    assert decision.rounded_bid <= 49_995.0
    assert decision.rounded_ask >= 50_005.0
    assert decision.rounded_bid < decision.rounded_ask
    assert decision.rounded_bid * 10 == pytest.approx(
        round(decision.rounded_bid * 10)
    )


def test_missing_and_stale_market_data_prevent_quotes():
    strategy = MarketMakerV1Strategy(_profile(volatility_min_samples=2))
    assert strategy.decide({}, inventory_btc=0.0) is None
    assert strategy.decide(
        _tick(50_000.0), inventory_btc=0.0, market_data_age_ms=2_000
    ) is None


def test_strategy_is_causal_and_quotes_after_warmup():
    first = MarketMakerV1Strategy(_profile(volatility_min_samples=2))
    second = MarketMakerV1Strategy(_profile(volatility_min_samples=2))
    prefix = [_tick(50_000.0 + index * 10, index) for index in range(4)]
    outputs_a = [first.decide(tick, inventory_btc=0.0) for tick in prefix]
    outputs_b = [second.decide(tick, inventory_btc=0.0) for tick in prefix]
    assert outputs_a == outputs_b
    second.decide(_tick(60_000.0, 99), inventory_btc=0.0)
    assert outputs_a[-1] == outputs_b[-1]
    assert outputs_a[-1] is not None


def test_runner_requotes_only_after_declared_minimum_lifetime():
    profile = _profile(
        volatility_min_samples=2,
        minimum_order_lifetime_ticks=3,
        maximum_order_age_ticks=8,
        requote_threshold_ticks=1,
    )
    ticks = scenario_ticks("normal_range", 99_102, count=20)
    result = MarketMakerBacktestRunner(
        profile,
        protocol_id="ENGINEERING_FIXTURE",
        scenario="normal_range",
        source_block="engineering",
        fill_seed=99_103,
    ).run(ticks)
    cancellations = [
        event for event in result.order_events
        if event.get("event") == "order_terminal"
        and event.get("terminal_state") == "CANCELLED"
        and event.get("reason") == "CANCEL_REQUOTE_PRICE_CHANGE"
    ]
    assert all(event["tick"] >= 3 for event in cancellations)


def test_markout_sign_convention():
    assert favorable_markout(side="buy", fill_price=100.0, future_mid=101.0) == 1.0
    assert favorable_markout(side="sell", fill_price=101.0, future_mid=100.0) == 1.0


def test_top_ten_percent_removal():
    assert top_profit_removal([10.0, 2.0, -1.0]) == pytest.approx(1.0)


def test_objective_is_deterministic_and_not_raw_pnl_only():
    metrics = {
        "net_pnl_usdt": 5.0,
        "average_spread_capture_usdt": 0.1,
        "profit_factor_clipped": 1.5,
        "after_top_10pct_removal_usdt": 2.0,
        "quote_uptime": 0.8,
        "max_drawdown": 0.01,
        "inventory_variance": 0.00002,
        "adverse_markout_loss_usdt": 0.5,
        "terminal_liquidation_cost_usdt": 0.2,
        "quote_churn": 0.3,
        "cancel_to_fill_ratio": 1.0,
    }
    assert conservative_objective(metrics) == conservative_objective(metrics)
    changed = dict(metrics, quote_churn=0.9)
    assert conservative_objective(changed) < conservative_objective(metrics)


def test_sampler_is_deterministic_valid_and_bounded():
    proposals_a, candidates_a = sample_candidates()
    proposals_b, candidates_b = sample_candidates()
    assert proposals_a == proposals_b
    assert candidates_a == candidates_b
    assert len(proposals_a) <= PROPOSAL_CEILING
    assert len(candidates_a) == VALID_CANDIDATE_TARGET
    assert len(candidates_a) <= MAX_EVALUATED_CANDIDATES
    assert all(item["status"] in {"VALID", "DUPLICATE", "INVALID_CONSTRAINT"} for item in proposals_a)
    assert len({item["profile_fingerprint"] for item in candidates_a}) == len(candidates_a)


def test_engineering_fixture_funnel_and_accounting_reconcile():
    _, candidates = sample_candidates()
    result = evaluate_candidate(
        candidates[0],
        [_engineering_path()],
        protocol_id="MM_ENGINEERING_FIXTURE_ONLY",
    )
    assert result["integrity"]["passed"]
    assert result["integrity"]["unclassified_order_removals"] == 0


def test_protocol_is_conservative_bounded_and_sealed(tmp_path):
    del tmp_path
    protocol = build_protocol(Path.cwd())
    assert protocol["fill_mode"] == "conservative"
    assert protocol["probabilistic_positive_score_weight"] == 0.0
    assert protocol["touch_only_positive_score_weight"] == 0.0
    assert protocol["sampler"]["optional_extension"] is None
    assert protocol["validation_opened"] is False
    assert protocol["holdout_opened"] is False


def test_artifact_overwrite_refused(tmp_path):
    hypothesis = tmp_path / "hypothesis"
    hypothesis.mkdir()
    with pytest.raises(FileExistsError):
        declare(Path.cwd(), hypothesis, tmp_path / "protocol")
