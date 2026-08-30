"""
tests/test_quote_engine.py — Unit tests for QuoteEngine.

Verifies:
  - A-S formula produces valid bid < ask
  - Inventory skew shifts reservation price correctly
  - Fixed lot size is used (not pct-based sizing)
  - Regime multipliers affect spread
"""

from __future__ import annotations

import math

import pytest
from config import Config
from quote_engine import QuoteEngine
from market_state import MarketState


def _make_market_state(
    mid: float = 50_000.0,
    spread: float = 10.0,
    volatility: float = 0.02,
) -> MarketState:
    """Create a MarketState with pre-set mid price and volatility."""
    ms = MarketState()
    ms.configure(ema_span=20, price_history_length=100,
                 staleness_threshold_s=999, latency_warning_ms=1e12)

    # Synthesise a minimal order book
    half_spread = spread / 2
    # Feed deterministic alternating prices to build a non-zero estimate.
    for index in range(25):
        shifted_mid = mid * (1.0 + (volatility / 100.0) * (1 if index % 2 else -1))
        tick = {
            "bids": [[shifted_mid - half_spread, 1.0]],
            "asks": [[shifted_mid + half_spread, 1.0]],
            "timestamp": 1_700_000_000_000 + index * 1_000,
        }
        ms.update_from_orderbook(tick)

    return ms


_MARKET_INFO = {
    "tick_size": 0.1,
    "lot_size": 0.001,
    "min_notional": 5.0,
    "contract_size": 0.001,
}


class TestQuoteEngineBasic:

    def test_avellaneda_uses_return_variance_without_extra_price_factor(self):
        cfg = Config(gamma=0.04, k=2.0, tau=1.0, fixed_lot_size=0.01)
        engine = QuoteEngine(cfg)
        reservation, half = engine._avellaneda(50_000.0, 0.01, 0.01)

        variance_distance = 50_000.0 * 0.01**2
        adverse_distance = 0.04 * 50_000.0 * 0.01 * math.sqrt(2.0)
        assert reservation == pytest.approx(50_000.0 - adverse_distance)
        assert half == pytest.approx(
            math.log1p(0.04 / 2.0) / 0.04
            + adverse_distance
            + 0.04 * variance_distance / 2.0
        )
        assert 28.0 < half < 29.0

    def test_queue_improvement_does_not_erase_defensive_model_distance(self):
        engine = QuoteEngine(Config(gamma=0.5, k=2.0, tau=1.0))
        market = _make_market_state(mid=50_000.0, spread=10.0, volatility=1.0)
        quotes = engine.generate(market, 0.0, _MARKET_INFO)

        assert quotes.raw_bid_price < market.best_bid
        assert quotes.raw_ask_price > market.best_ask
        assert quotes.bid_price < market.best_bid
        assert quotes.ask_price > market.best_ask

    def test_drawdown_budget_suppresses_opening_but_not_reduce_only_exit(self):
        engine = QuoteEngine(Config())
        market = _make_market_state()

        flat = engine.generate(
            market, 0.0, _MARKET_INFO,
            remaining_drawdown_budget_usdt=0.0,
        )
        assert not flat.bid_valid
        assert not flat.ask_valid
        assert flat.bid_skip_reason == "LOCAL_RISK_REJECT:DRAWDOWN_BUDGET"
        assert flat.ask_skip_reason == "LOCAL_RISK_REJECT:DRAWDOWN_BUDGET"

        long = engine.generate(
            market, 0.01, _MARKET_INFO,
            remaining_drawdown_budget_usdt=0.0,
        )
        assert not long.bid_valid
        assert long.ask_valid
        assert long.ask_reduce_only

    def test_bid_below_ask(self):
        """Generated bid price must be strictly below ask price."""
        cfg = Config()
        qe = QuoteEngine(cfg)
        ms = _make_market_state()

        quotes = qe.generate(
            ms=ms,
            inventory=0.0,
            market_info=_MARKET_INFO,
            spread_multiplier=1.0,
            size_multiplier=1.0,
        )

        if quotes.bid_valid and quotes.ask_valid:
            assert quotes.bid_price < quotes.ask_price

    def test_quotes_near_mid(self):
        """Bid and ask should be roughly symmetric around mid."""
        cfg = Config()
        qe = QuoteEngine(cfg)
        ms = _make_market_state(mid=50_000.0)

        quotes = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )

        if quotes.bid_valid and quotes.ask_valid:
            mid = ms.mid_price
            bid_distance = mid - quotes.bid_price
            ask_distance = quotes.ask_price - mid
            assert bid_distance > 0
            assert ask_distance > 0

    def test_fixed_lot_size_used(self):
        """Order size should be derived from fixed_lot_size, not percentage."""
        cfg = Config(fixed_lot_size=0.01)
        qe = QuoteEngine(cfg)
        ms = _make_market_state()

        quotes = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )

        # Size should be exactly the fixed lot (before regime adjustment)
        if quotes.bid_valid:
            assert quotes.bid_size == pytest.approx(0.01, rel=0.5)


class TestInventorySkew:

    def test_long_inventory_skews_lower(self):
        """With long inventory, reservation price should shift down (sell bias)."""
        cfg = Config(inventory_skew_factor=1.0)
        qe = QuoteEngine(cfg)
        ms = _make_market_state(mid=50_000.0)

        quotes_neutral = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )
        quotes_long = qe.generate(
            ms=ms, inventory=0.005, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )

        if quotes_neutral.bid_valid and quotes_long.bid_valid:
            # Long inventory → bid should be lower (less eager to buy more)
            assert quotes_long.bid_price <= quotes_neutral.bid_price

    def test_short_inventory_skews_higher(self):
        """With short inventory, reservation price should shift up (buy bias)."""
        cfg = Config(inventory_skew_factor=1.0)
        qe = QuoteEngine(cfg)
        ms = _make_market_state(mid=50_000.0)

        quotes_neutral = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )
        quotes_short = qe.generate(
            ms=ms, inventory=-0.005, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )

        if quotes_neutral.ask_valid and quotes_short.ask_valid:
            # Short inventory → ask should be higher (less eager to sell more)
            assert quotes_short.ask_price >= quotes_neutral.ask_price

    def test_long_limit_suppresses_bid_and_marks_ask_reduce_only(self):
        cfg = Config()
        quotes = QuoteEngine(cfg).generate(
            _make_market_state(), cfg.max_inventory, _MARKET_INFO
        )
        assert not quotes.bid_valid
        assert quotes.ask_valid
        assert quotes.ask_reduce_only
        assert quotes.ask_price > _make_market_state().best_bid

    def test_short_limit_suppresses_ask_and_marks_bid_reduce_only(self):
        cfg = Config()
        quotes = QuoteEngine(cfg).generate(
            _make_market_state(), -cfg.max_inventory, _MARKET_INFO
        )
        assert not quotes.ask_valid
        assert quotes.bid_valid
        assert quotes.bid_reduce_only


class TestPostOnlyClamping:

    def test_extreme_inventory_skew_is_clamped_after_rounding(self):
        cfg = Config(inventory_skew_factor=2.5, imbalance_skew_factor=0.8)
        ms = _make_market_state(spread=0.2, volatility=0.5)
        quotes = QuoteEngine(cfg).generate(ms, cfg.max_inventory, _MARKET_INFO)
        if quotes.bid_valid:
            assert quotes.bid_price < ms.best_ask
        if quotes.ask_valid:
            assert quotes.ask_price > ms.best_bid

    def test_locked_market_skips_both_sides(self):
        ms = _make_market_state()
        ms.best_ask = ms.best_bid
        quotes = QuoteEngine(Config()).generate(ms, 0.0, _MARKET_INFO)
        assert not quotes.bid_valid
        assert not quotes.ask_valid


class TestRegimeMultipliers:

    def test_wider_spread_multiplier(self):
        """A larger spread_multiplier should widen the bid-ask spread."""
        cfg = Config()
        qe = QuoteEngine(cfg)
        ms = _make_market_state(mid=50_000.0)

        quotes_normal = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=1.0, size_multiplier=1.0,
        )
        quotes_wide = qe.generate(
            ms=ms, inventory=0.0, market_info=_MARKET_INFO,
            spread_multiplier=2.0, size_multiplier=1.0,
        )

        if quotes_normal.bid_valid and quotes_wide.bid_valid:
            spread_normal = quotes_normal.ask_price - quotes_normal.bid_price
            spread_wide = quotes_wide.ask_price - quotes_wide.bid_price
            assert spread_wide > spread_normal
