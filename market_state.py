"""
market_state.py — Real-time market data tracking and derived metrics.

Contains the EMA utility class and the MarketState class that aggregates
order book data, computes mid/microprice, EWMA volatility, and order book
imbalance.  Data arrives from either WebSocket (primary) or REST (fallback).
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class EMA:
    """Exponential Moving Average with configurable span.

    Stateful:  call ``update(value)`` with each new observation and read
    ``.value`` for the current smoothed estimate.

    The smoothing factor ``alpha = 2 / (span + 1)`` follows the pandas
    convention for EWMA.
    """

    def __init__(self, span: int = 20):
        if span < 1:
            raise ValueError("span must be >= 1")
        self.span = span
        self.alpha: float = 2.0 / (span + 1)
        self._value: float | None = None
        self._count: int = 0

    @property
    def value(self) -> float | None:
        return self._value

    @property
    def ready(self) -> bool:
        """True once we have at least *span* observations (warm-up done)."""
        return self._count >= self.span

    def update(self, new_value: float) -> float:
        """Incorporate *new_value* and return the updated EMA."""
        self._count += 1
        if self._value is None:
            self._value = new_value
        else:
            self._value = self.alpha * new_value + (1 - self.alpha) * self._value
        return self._value

    def reset(self) -> None:
        self._value = None
        self._count = 0


@dataclass
class MarketState:
    """Aggregated snapshot of current market conditions.

    Updated each iteration from order book data (WS or REST).
    All derived metrics (volatility, imbalance, microprice) are recomputed
    on every ``update_from_orderbook`` / ``update_from_rest`` call.
    """

    # ── Current book top ────────────────────────────────────────────────
    best_bid: float = 0.0
    best_bid_size: float = 0.0
    best_ask: float = 0.0
    best_ask_size: float = 0.0

    # ── Derived ─────────────────────────────────────────────────────────
    mid_price: float = 0.0
    microprice: float = 0.0       # Size-weighted mid
    spread: float = 0.0
    volatility: float = 0.0       # EWMA of absolute log-returns
    order_book_imbalance: float = 0.0  # (-1, 1) — positive = bid-heavy

    # ── Regime (set externally by RegimeDetector) ───────────────────────
    regime: str = "range"

    # ── Timestamps ──────────────────────────────────────────────────────
    last_update_ts: float = 0.0          # local monotonic time of last update
    last_exchange_ts: float = 0.0        # exchange-reported timestamp (ms → s)
    latency_ms: float = 0.0             # exchange_ts → local_ts delta

    # ── Internal state (not part of public snapshot) ────────────────────
    _vol_ema: EMA = field(default_factory=lambda: EMA(span=20), repr=False)
    _price_history: deque = field(
        default_factory=lambda: deque(maxlen=500), repr=False
    )
    _prev_mid: float = field(default=0.0, repr=False)

    # ── Configuration (set once from Config) ────────────────────────────
    _staleness_threshold_s: float = field(default=3.0, repr=False)
    _latency_warning_ms: float = field(default=500.0, repr=False)

    def configure(
        self,
        ema_span: int = 20,
        price_history_length: int = 500,
        staleness_threshold_s: float = 3.0,
        latency_warning_ms: float = 500.0,
    ) -> None:
        """One-time setup from Config values.  Call in ``Strategy.initialize``."""
        self._vol_ema = EMA(span=ema_span)
        self._price_history = deque(maxlen=price_history_length)
        self._staleness_threshold_s = staleness_threshold_s
        self._latency_warning_ms = latency_warning_ms

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def is_stale(self) -> bool:
        """True if no update has arrived within the staleness window."""
        if self.last_update_ts == 0.0:
            return True
        return (time.monotonic() - self.last_update_ts) > self._staleness_threshold_s

    @property
    def price_history(self) -> list[tuple[float, float]]:
        """Return a copy of the price history as [(timestamp, mid_price), …]."""
        return list(self._price_history)

    @property
    def price_history_prices(self) -> list[float]:
        """Just the price values from the history buffer."""
        return [p for _, p in self._price_history]

    def update_from_orderbook(self, ob: dict[str, Any]) -> None:
        """Parse a CCXT-format order book dict and recompute all derived fields.

        Expected keys:
            bids: list[[price, size], …]   (descending by price)
            asks: list[[price, size], …]   (ascending by price)
            timestamp: int (ms) or None
        """
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if not bids or not asks:
            logger.warning("Empty order book received — skipping update")
            return

        self.best_bid = float(bids[0][0])
        self.best_bid_size = float(bids[0][1])
        self.best_ask = float(asks[0][0])
        self.best_ask_size = float(asks[0][1])

        self._recompute(ob.get("timestamp"))

    def update_from_rest(self, ticker: dict[str, Any]) -> None:
        """Fallback update from a REST ticker or fetch_order_book result.

        Accepts the same format as ``update_from_orderbook`` (CCXT orderbook)
        or a minimal dict with ``bid``, ``ask``, ``bidVolume``, ``askVolume``.
        """
        if "bids" in ticker:
            # It's actually a full order book
            self.update_from_orderbook(ticker)
            return

        self.best_bid = float(ticker.get("bid", 0) or 0)
        self.best_bid_size = float(ticker.get("bidVolume", 0) or 0)
        self.best_ask = float(ticker.get("ask", 0) or 0)
        self.best_ask_size = float(ticker.get("askVolume", 0) or 0)

        self._recompute(ticker.get("timestamp"))

    # ── Order book depth helpers ────────────────────────────────────────

    def compute_book_imbalance(
        self, bids: list[list[float]], asks: list[list[float]], depth: int = 5
    ) -> float:
        """Compute order book imbalance from top-N levels.

        Returns a value in (-1, 1):
          +1 = all bid volume, no ask volume (buy pressure)
          -1 = all ask volume, no bid volume (sell pressure)
           0 = balanced
        """
        bid_vol = sum(level[1] for level in bids[:depth])
        ask_vol = sum(level[1] for level in asks[:depth])
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    # ── Private ─────────────────────────────────────────────────────────

    def _recompute(self, exchange_ts_ms: int | None) -> None:
        """Recalculate all derived metrics after a data update."""
        now = time.monotonic()
        self.last_update_ts = now

        # Latency tracking
        if exchange_ts_ms is not None:
            self.last_exchange_ts = float(exchange_ts_ms) / 1000.0
            # Use wall-clock for latency (monotonic has different epoch)
            self.latency_ms = (time.time() - self.last_exchange_ts) * 1000.0
            if self.latency_ms > self._latency_warning_ms:
                logger.warning(
                    "High data latency: %.1f ms (threshold %.1f ms)",
                    self.latency_ms,
                    self._latency_warning_ms,
                )

        if self.best_bid <= 0 or self.best_ask <= 0:
            return

        # Mid price
        self.mid_price = (self.best_bid + self.best_ask) / 2.0

        # Microprice (size-weighted mid)
        total_top_size = self.best_bid_size + self.best_ask_size
        if total_top_size > 0:
            self.microprice = (
                self.best_bid * self.best_ask_size
                + self.best_ask * self.best_bid_size
            ) / total_top_size
        else:
            self.microprice = self.mid_price

        # Spread
        self.spread = self.best_ask - self.best_bid

        # Order book imbalance (top-of-book only in this path)
        total = self.best_bid_size + self.best_ask_size
        if total > 0:
            self.order_book_imbalance = (
                self.best_bid_size - self.best_ask_size
            ) / total
        else:
            self.order_book_imbalance = 0.0

        # Price history
        self._price_history.append((time.time(), self.mid_price))

        # Volatility (EWMA of absolute log-returns)
        if self._prev_mid > 0 and self.mid_price > 0:
            log_return = math.log(self.mid_price / self._prev_mid)
            self._vol_ema.update(abs(log_return))
            if self._vol_ema.value is not None:
                self.volatility = self._vol_ema.value

        self._prev_mid = self.mid_price
