"""
fill_tracker.py — Fill detection, VWAP computation, and P&L attribution.

Primary detection path: compare exchange closed/recent trades against known
fills each iteration.  Stretch: WebSocket ``watch_my_trades()`` for instant
detection (wired in strategy.py).
"""

from __future__ import annotations

import logging
from decimal import Decimal
import time
from dataclasses import dataclass, field
from typing import Literal

from config import Config
from market_spec import MarketSpec
from fill_classification import CanonicalFillClassification

logger = logging.getLogger(__name__)


@dataclass
class Fill:
    """A single fill event."""

    fill_id: str
    order_id: str
    side: str           # "buy" or "sell"
    price: float
    size: float
    timestamp: float    # Unix seconds
    fee: float = 0.0
    fee_currency: str = ""
    liquidity: Literal["maker", "taker"] = "maker"
    reason: str = "strategy"
    classification: CanonicalFillClassification | None = None


class FillTracker:
    """Detects fills, computes VWAP, and attributes P&L.

    The tracker maintains a bounded fill history and separate VWAP
    accumulators for bid and ask fills.

    Usage:
        1. ``detect_fills(exchange, symbol)`` each iteration → list of new fills.
        2. ``compute_pnl(inventory, mid_price)`` → (realized_delta, unrealized).
    """

    def __init__(self, config: Config, max_fills: int = 1000):
        self.cfg = config
        self._max_fills = max_fills

        self.fills: list[Fill] = []
        self._known_fill_ids: set[str] = set()

        # ── VWAP accumulators ───────────────────────────────────────────
        self._bid_vwap_numer: float = 0.0   # sum(price * size) for buys
        self._bid_vwap_denom: float = 0.0   # sum(size) for buys
        self._ask_vwap_numer: float = 0.0
        self._ask_vwap_denom: float = 0.0

        # ── P&L tracking ────────────────────────────────────────────────
        self.realized_pnl: float = 0.0
        self.gross_realized_pnl: float = 0.0
        self.total_fees: float = 0.0

        # ── Position tracking (for P&L attribution) ─────────────────────
        self._position: float = 0.0         # Running net position
        self._avg_entry_price: float = 0.0  # Weighted average entry price

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def known_fill_ids(self) -> set[str]:
        """Return the set of known fill IDs (for state persistence)."""
        return self._known_fill_ids

    @property
    def bid_vwap(self) -> float:
        if self._bid_vwap_denom == 0:
            return 0.0
        return self._bid_vwap_numer / self._bid_vwap_denom

    @property
    def ask_vwap(self) -> float:
        if self._ask_vwap_denom == 0:
            return 0.0
        return self._ask_vwap_numer / self._ask_vwap_denom

    @property
    def position(self) -> float:
        return self._position

    @property
    def avg_entry_price(self) -> float:
        return self._avg_entry_price

    def detect_fills(
        self, exchange, symbol: str, market_spec: MarketSpec
    ) -> list[Fill]:
        """Check exchange for new fills not yet tracked.

        Uses ``fetch_my_trades`` with a limit to get recent trades and
        filters out already-known fill IDs.
        """
        new_fills: list[Fill] = []

        try:
            # Fetch recent trades (last 100)
            trades = exchange.fetch_my_trades(symbol, limit=100)

            # If this is the very first fetch on a fresh start (no state loaded),
            # mark existing historical trades as known but don't process them.
            if not getattr(self, "_initialized_fills", False):
                self._initialized_fills = True
                if not self._known_fill_ids and trades:
                    logger.info("Fresh start: ignoring %d historical trades.", len(trades))
                    for trade in trades:
                        if "id" in trade:
                            self._known_fill_ids.add(trade["id"])
                    return []

            for trade in trades:
                fill_id = trade.get("id", "")
                if not fill_id or fill_id in self._known_fill_ids:
                    continue

                fill = self._fill_from_exchange_trade(trade, market_spec)

                new_fills.append(fill)
                self._known_fill_ids.add(fill_id)
                self._process_fill(fill)

                logger.info(
                    "Fill detected: %s %s %.6f @ %.2f (fee=%.6f %s) id=%s",
                    fill.side,
                    symbol,
                    fill.size,
                    fill.price,
                    fill.fee,
                    fill.fee_currency,
                    fill.fill_id,
                )

        except Exception:
            logger.exception("Failed to fetch trades for %s", symbol)

        return new_fills

    def process_ws_fill(
        self, trade: dict, market_spec: MarketSpec
    ) -> Fill | None:
        """Process a fill received via WebSocket (watch_my_trades).

        Same structure as a CCXT trade dict.
        """
        fill_id = trade.get("id", "")
        if not fill_id or fill_id in self._known_fill_ids:
            return None

        fill = self._fill_from_exchange_trade(trade, market_spec)

        self._known_fill_ids.add(fill_id)
        self._process_fill(fill)
        return fill

    def _fill_from_exchange_trade(
        self, trade: dict, market_spec: MarketSpec
    ) -> Fill:
        """Convert exchange contracts and fees to canonical base/quote units."""
        if not market_spec.linear or market_spec.inverse:
            raise ValueError("Only linear contract fills are supported")
        price = float(trade.get("price", 0))
        contracts = Decimal(str(trade.get("amount", 0)))
        base_size = float(market_spec.contracts_to_base(contracts))
        fee_data = trade.get("fee") or {}
        fee_cost = float(fee_data.get("cost", 0) or 0)
        fee_currency = str(fee_data.get("currency", "") or "").upper()
        pair = market_spec.symbol.split(":", 1)[0]
        base_currency, quote_currency = pair.split("/", 1)
        if fee_cost and fee_currency == base_currency.upper():
            fee_quote = fee_cost * price
        elif not fee_cost or fee_currency in {"", quote_currency.upper(), "USDT", "USDC"}:
            fee_quote = fee_cost
        else:
            raise ValueError(f"Unsupported fee currency {fee_currency!r}")
        return Fill(
            fill_id=str(trade.get("id", "")),
            order_id=str(trade.get("order", "")),
            side=str(trade.get("side", "unknown")),
            price=price,
            size=base_size,
            timestamp=float(trade.get("timestamp", 0)) / 1000.0,
            fee=fee_quote,
            fee_currency=quote_currency,
            liquidity=("taker" if trade.get("takerOrMaker") == "taker" else "maker"),
        )

    def process_fill(self, fill: Fill) -> None:
        """Process a fill — public API for backtest matching engine."""
        self._process_fill(fill)

    def restore_state(
        self,
        known_fill_ids: set[str],
        realized_pnl: float,
        position: float,
        avg_entry_price: float,
        gross_realized_pnl: float | None = None,
        total_fees: float | None = None,
    ) -> None:
        """Restore tracker state from persisted data.

        Called on startup after loading state from disk.
        """
        self._known_fill_ids = known_fill_ids
        self.realized_pnl = realized_pnl
        self.gross_realized_pnl = (
            realized_pnl if gross_realized_pnl is None else gross_realized_pnl
        )
        if total_fees is not None:
            self.total_fees = total_fees
        self._position = position
        self._avg_entry_price = avg_entry_price

    def compute_unrealized_pnl(self, mid_price: float) -> float:
        """Mark-to-market unrealized P&L on current position."""
        if self._position == 0 or self._avg_entry_price == 0:
            return 0.0
        return self._position * (mid_price - self._avg_entry_price)

    def get_recent_fills(self, n: int = 10) -> list[Fill]:
        """Return the last *n* fills."""
        return self.fills[-n:]

    # ── Private ─────────────────────────────────────────────────────────

    def _process_fill(self, fill: Fill) -> None:
        """Update VWAP, position, and realized P&L from a new fill."""
        # ── VWAP ────────────────────────────────────────────────────────
        if fill.side == "buy":
            self._bid_vwap_numer += fill.price * fill.size
            self._bid_vwap_denom += fill.size
        else:
            self._ask_vwap_numer += fill.price * fill.size
            self._ask_vwap_denom += fill.size

        # ── Fees ────────────────────────────────────────────────────────
        self.total_fees += fill.fee

        # ── Position + realized P&L ─────────────────────────────────────
        signed_size = fill.size if fill.side == "buy" else -fill.size
        old_pos = self._position
        new_pos = old_pos + signed_size

        if old_pos == 0:
            # Opening new position
            self._avg_entry_price = fill.price
            # Deduct fee from realized P&L on opening (AGENTS.md §11.2)
            self.realized_pnl -= fill.fee
        elif (old_pos > 0 and fill.side == "buy") or (
            old_pos < 0 and fill.side == "sell"
        ):
            # Adding to existing position — update weighted avg entry
            total_size = abs(old_pos) + fill.size
            self._avg_entry_price = (
                abs(old_pos) * self._avg_entry_price + fill.size * fill.price
            ) / total_size
            # Deduct fee from realized P&L on adding
            self.realized_pnl -= fill.fee
        else:
            # Reducing or flipping position — realize P&L
            reduce_size = min(fill.size, abs(old_pos))
            if fill.side == "sell":
                realized = reduce_size * (fill.price - self._avg_entry_price)
            else:
                realized = reduce_size * (self._avg_entry_price - fill.price)

            self.gross_realized_pnl += realized
            self.realized_pnl += realized - fill.fee

            # If position flipped, set new avg entry to fill price
            if abs(new_pos) > 0 and (
                (old_pos > 0 and new_pos < 0)
                or (old_pos < 0 and new_pos > 0)
            ):
                self._avg_entry_price = fill.price

            if new_pos == 0:
                self._avg_entry_price = 0.0

        self._position = new_pos

        # ── Store fill ──────────────────────────────────────────────────
        self.fills.append(fill)
        if len(self.fills) > self._max_fills:
            self.fills = self.fills[-self._max_fills:]
