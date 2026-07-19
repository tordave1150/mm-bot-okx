"""
tests/test_fill_tracker.py — Unit tests for FillTracker accounting.

Verifies:
  - Fee attribution on opening, adding, and reducing fills
  - Round-trip P&L with fees
  - Position flip accounting
  - State restore via public API
"""

from __future__ import annotations

import pytest
from config import Config
from fill_tracker import Fill, FillTracker


def _make_tracker(
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
) -> FillTracker:
    cfg = Config(maker_fee_rate=maker_fee, taker_fee_rate=taker_fee)
    return FillTracker(cfg)


def _make_fill(
    side: str = "buy",
    price: float = 50_000.0,
    size: float = 0.01,
    fee: float = 0.0,
    fill_id: str = "test-fill-1",
    order_id: str = "test-order-1",
    timestamp: float = 1.0,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        price=price,
        size=size,
        timestamp=timestamp,
        fee=fee,
        fee_currency="USDT",
    )


class TestFillTrackerAccounting:

    def test_opening_buy_deducts_fee(self):
        """Opening a new position should deduct the fee from realized P&L."""
        ft = _make_tracker()
        fill = _make_fill(side="buy", price=50_000, size=0.01, fee=0.10)
        ft.process_fill(fill)

        assert ft.position == pytest.approx(0.01)
        assert ft.realized_pnl == pytest.approx(-0.10)  # fee deducted
        assert ft.avg_entry_price == pytest.approx(50_000.0)

    def test_adding_to_position_deducts_fee(self):
        """Adding to existing position should deduct the fee."""
        ft = _make_tracker()
        ft.process_fill(_make_fill(side="buy", price=50_000, size=0.01, fee=0.10, fill_id="f1"))
        ft.process_fill(_make_fill(side="buy", price=51_000, size=0.01, fee=0.10, fill_id="f2"))

        assert ft.position == pytest.approx(0.02)
        assert ft.realized_pnl == pytest.approx(-0.20)  # two opening fees
        assert ft.avg_entry_price == pytest.approx(50_500.0)

    def test_reducing_position_realizes_pnl_minus_fee(self):
        """Reducing position realizes P&L and deducts closing fee."""
        ft = _make_tracker()
        ft.process_fill(_make_fill(side="buy", price=50_000, size=0.01, fee=0.10, fill_id="f1"))
        ft.process_fill(_make_fill(side="sell", price=51_000, size=0.01, fee=0.10, fill_id="f2"))

        # Realized = (51000 - 50000) * 0.01 - 0.10 (close fee) - 0.10 (open fee)
        # = 10.0 - 0.10 - 0.10 = 9.80
        assert ft.position == pytest.approx(0.0)
        assert ft.realized_pnl == pytest.approx(9.80)

    def test_round_trip_with_zero_fees(self):
        """Round trip without fees should give clean P&L."""
        ft = _make_tracker()
        ft.process_fill(_make_fill(side="buy", price=50_000, size=0.01, fee=0.0, fill_id="f1"))
        ft.process_fill(_make_fill(side="sell", price=51_000, size=0.01, fee=0.0, fill_id="f2"))

        assert ft.realized_pnl == pytest.approx(10.0)
        assert ft.position == pytest.approx(0.0)

    def test_position_flip(self):
        """Flipping from long to short should realize P&L and set new avg entry."""
        ft = _make_tracker()
        ft.process_fill(_make_fill(side="buy", price=50_000, size=0.01, fee=0.0, fill_id="f1"))
        # Sell 0.02 → close 0.01 long, open 0.01 short
        ft.process_fill(_make_fill(side="sell", price=51_000, size=0.02, fee=0.0, fill_id="f2"))

        assert ft.position == pytest.approx(-0.01)
        # Realized on closing: (51000 - 50000) * 0.01 = 10.0
        assert ft.realized_pnl == pytest.approx(10.0)
        assert ft.avg_entry_price == pytest.approx(51_000.0)


class TestFillTrackerPublicAPI:

    def test_process_fill_public_method(self):
        """process_fill() should be the public API for backtest matching engine."""
        ft = _make_tracker()
        fill = _make_fill()
        ft.process_fill(fill)
        assert ft.position == pytest.approx(0.01)

    def test_restore_state(self):
        """restore_state() should set all internal state."""
        ft = _make_tracker()
        ft.restore_state(
            known_fill_ids={"fill-1", "fill-2"},
            realized_pnl=5.0,
            position=0.02,
            avg_entry_price=49_000.0,
        )

        assert ft.position == pytest.approx(0.02)
        assert ft.realized_pnl == pytest.approx(5.0)
        assert ft.avg_entry_price == pytest.approx(49_000.0)
        assert ft.known_fill_ids == {"fill-1", "fill-2"}

    def test_known_fill_ids_property(self):
        """known_fill_ids should be a public property."""
        ft = _make_tracker()
        assert isinstance(ft.known_fill_ids, set)
