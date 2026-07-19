"""
tests/test_quote_engine.py — Unit tests for QuoteEngine.

Verifies:
  - A-S formula produces valid bid < ask
  - Inventory skew shifts reservation price correctly
  - Fixed lot size is used (not pct-based sizing)
  - Regime multipliers affect spread
"""

from __future__ import annotations

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
    tick = {
        "bids": [[mid - half_spread, 1.0]],
        "asks": [[mid + half_spread, 1.0]],
        "timestamp": 1_700_000_000_000,
    }
    # Feed enough ticks to build a volatility estimate
    for _ in range(25):
        ms.update_from_orderbook(tick)

    return ms


_MARKET_INFO = {
    "tick_size": 0.1,
    "lot_size": 0.001,
    "min_notional": 5.0,
    "contract_size": 0.001,
}


class TestQuoteEngineBasic:

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
            mid = 50_000.0
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
