"""
backtest/tests/test_matching_engine.py — Unit tests for MatchingEngine v1.

Tests the four key acceptance criteria:
1. Bid fills when next-tick ask drops to/below our bid price
2. Ask fills when next-tick bid rises to/above our ask price
3. No fill when price stays between bid and ask
4. cancel_all() clears all pending orders — no fills after cancellation

Run with:
    python -m pytest backtest/tests/test_matching_engine.py -v
"""

from __future__ import annotations

import sys
import os

# Ensure project root is importable
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
from config import Config
from fill_tracker import FillTracker
from backtest.matching_engine import MatchingEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tick(best_bid: float, best_ask: float, ts_ms: int = 1_700_000_000_000) -> dict:
    """Build a minimal CCXT-format order book tick."""
    return {
        "bids": [[best_bid, 1.0], [best_bid - 10, 2.0]],
        "asks": [[best_ask, 1.0], [best_ask + 10, 2.0]],
        "timestamp": ts_ms,
    }


def _make_fill_tracker() -> FillTracker:
    cfg = Config()
    return FillTracker(cfg)


# ── Test Cases ───────────────────────────────────────────────────────────────

class TestMatchingEngineV1:

    def test_bid_fills_when_ask_drops_to_bid_price(self):
        """Market ask touches our bid price → full fill at our limit price."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        our_bid_price = 50_000.0
        our_bid_size = 0.01

        order_id = engine.place_order("buy", our_bid_price, our_bid_size)
        assert order_id != "", "place_order should return a non-empty ID"

        # Market ask drops exactly to our bid
        tick = _make_tick(best_bid=49_990.0, best_ask=50_000.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 1, "Expected exactly one fill"
        assert fills[0].side == "buy"
        assert fills[0].price == our_bid_price
        assert fills[0].size == our_bid_size
        assert fills[0].order_id == order_id

        # FillTracker should have updated position
        assert ft.position == pytest.approx(our_bid_size, rel=1e-6)

        # Order is consumed — no more pending
        assert engine.pending_count == 0
        assert engine.total_bid_fills == 1

    def test_bid_fills_when_ask_drops_below_bid_price(self):
        """Market ask falls BELOW our bid → still fills at our limit price (v1 optimistic)."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        our_bid_price = 50_000.0
        engine.place_order("buy", our_bid_price, 0.01)

        # Market ask overshoots below our bid
        tick = _make_tick(best_bid=49_800.0, best_ask=49_850.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 1
        assert fills[0].price == our_bid_price  # filled at limit, not market price

    def test_ask_fills_when_bid_rises_to_ask_price(self):
        """Market bid touches our ask price → full fill at our limit price."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        our_ask_price = 51_000.0
        our_ask_size = 0.01

        order_id = engine.place_order("sell", our_ask_price, our_ask_size)

        # Market bid rises exactly to our ask
        tick = _make_tick(best_bid=51_000.0, best_ask=51_010.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 1
        assert fills[0].side == "sell"
        assert fills[0].price == our_ask_price
        assert fills[0].size == our_ask_size
        assert fills[0].order_id == order_id
        assert engine.total_ask_fills == 1

    def test_ask_fills_when_bid_rises_above_ask_price(self):
        """Market bid rises ABOVE our ask → fills at our limit price (v1 optimistic)."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        our_ask_price = 51_000.0
        engine.place_order("sell", our_ask_price, 0.01)

        tick = _make_tick(best_bid=51_200.0, best_ask=51_210.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 1
        assert fills[0].price == our_ask_price

    def test_no_fill_when_price_between_bid_and_ask(self):
        """Market price stays between our quotes → no fill."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        engine.place_order("buy", 49_900.0, 0.01)   # our bid is below market
        engine.place_order("sell", 51_000.0, 0.01)  # our ask is above market

        # Market is trading 50,000 / 50,010 — neither order should fill
        tick = _make_tick(best_bid=50_000.0, best_ask=50_010.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 0, "No fills expected when spread is inside our quotes"
        assert engine.pending_count == 2, "Both orders should still be pending"
        assert ft.position == pytest.approx(0.0)

    def test_no_fill_bid_slightly_above_market_ask(self):
        """Market ask is 1 tick ABOVE our bid → no fill (strict comparison)."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        engine.place_order("buy", 50_000.0, 0.01)

        # Market ask is 50,000.1 — just above our bid, should NOT fill
        tick = _make_tick(best_bid=49_990.0, best_ask=50_000.1)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 0

    def test_cancel_all_clears_orders(self):
        """After cancel_all(), subsequent ticks produce no fills."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        engine.place_order("buy", 50_000.0, 0.01)
        engine.place_order("sell", 51_000.0, 0.01)

        engine.cancel_all()
        assert engine.pending_count == 0

        # Even a highly favourable tick produces no fills
        tick = _make_tick(best_bid=51_000.0, best_ask=49_900.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 0
        assert ft.position == pytest.approx(0.0)

    def test_cancel_single_order(self):
        """cancel_order() removes only the specified order."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        bid_id = engine.place_order("buy", 50_000.0, 0.01)
        ask_id = engine.place_order("sell", 51_000.0, 0.01)  # noqa: F841

        engine.cancel_order(bid_id)
        assert engine.pending_count == 1

        # Market bid crosses ask but bid was cancelled — only ask fills
        tick = _make_tick(best_bid=51_000.0, best_ask=50_100.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 1
        assert fills[0].side == "sell"

    def test_both_sides_fill_simultaneously(self):
        """If market crosses both our bid and ask in one tick, both fill."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        engine.place_order("buy", 50_000.0, 0.01)
        engine.place_order("sell", 50_050.0, 0.01)

        # Simulating a very wide tick that touches both
        tick = _make_tick(best_bid=50_050.0, best_ask=50_000.0)
        fills = engine.check_fills(tick, ft)

        assert len(fills) == 2
        sides = {f.side for f in fills}
        assert sides == {"buy", "sell"}

    def test_zero_size_order_not_placed(self):
        """place_order with size=0 returns empty string and adds no order."""
        engine = MatchingEngine()
        oid = engine.place_order("buy", 50_000.0, 0.0)
        assert oid == ""
        assert engine.pending_count == 0

    def test_fill_id_uniqueness(self):
        """Each fill gets a unique deterministic ID."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        engine.place_order("buy", 50_000.0, 0.01)
        fills1 = engine.check_fills(_make_tick(49_990.0, 50_000.0), ft)

        engine.place_order("buy", 50_000.0, 0.01)
        fills2 = engine.check_fills(_make_tick(49_990.0, 50_000.0), ft)

        assert fills1[0].fill_id != fills2[0].fill_id

    def test_fill_tracker_pnl_after_round_trip(self):
        """Buy then sell at higher price → positive realized P&L in FillTracker."""
        engine = MatchingEngine()
        ft = _make_fill_tracker()

        # Buy 0.01 BTC at 50,000
        engine.place_order("buy", 50_000.0, 0.01)
        engine.check_fills(_make_tick(49_990.0, 50_000.0, ts_ms=1_000_000), ft)

        # Sell 0.01 BTC at 51,000
        engine.place_order("sell", 51_000.0, 0.01)
        engine.check_fills(_make_tick(51_000.0, 51_010.0, ts_ms=2_000_000), ft)

        # Realized P&L = (51000 - 50000) * 0.01 = $10
        assert ft.realized_pnl == pytest.approx(10.0, rel=1e-6)
        assert ft.position == pytest.approx(0.0, abs=1e-9)
