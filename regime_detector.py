"""
regime_detector.py — Rule-based market regime detection.

Uses EMA crossover, RSI, and price slope to classify the current market
as "trend" or "range".  The classification feeds into the quote engine
to adjust spread widths and order sizes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from config import Config
from market_state import EMA

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Classifies market regime as 'trend' or 'range'.

    Inputs:  a list of recent prices (from MarketState.price_history_prices).
    Outputs: regime label + multipliers for spread and size.

    Trend regime → widen spreads + reduce size (avoid adverse selection).
    Range regime → tighten spreads + normal size (capture flow).
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._fast_ema = EMA(span=config.regime_fast_ema_span)
        self._slow_ema = EMA(span=config.regime_slow_ema_span)
        self._regime: str = "range"

    @property
    def regime(self) -> str:
        return self._regime

    def detect(self, prices: list[float]) -> str:
        """Analyse recent prices and return 'trend' or 'range'.

        Also updates internal EMA state.  Should be called once per
        iteration with the full price_history_prices from MarketState.
        """
        if len(prices) < max(self.cfg.regime_slow_ema_span, self.cfg.regime_rsi_period, self.cfg.regime_slope_window):
            self._regime = "range"
            return self._regime

        # Update EMAs with latest price
        latest = prices[-1]
        self._fast_ema.update(latest)
        self._slow_ema.update(latest)

        is_trend = False

        # ── 1. EMA divergence ───────────────────────────────────────────
        if self._fast_ema.value is not None and self._slow_ema.value is not None:
            divergence = abs(self._fast_ema.value - self._slow_ema.value)
            if latest > 0:
                relative_divergence = divergence / latest
                if relative_divergence > self.cfg.regime_ema_divergence_threshold:
                    is_trend = True

        # ── 2. RSI ──────────────────────────────────────────────────────
        rsi = self._compute_rsi(prices, self.cfg.regime_rsi_period)
        if rsi is not None:
            if rsi > self.cfg.regime_rsi_overbought or rsi < self.cfg.regime_rsi_oversold:
                is_trend = True

        # ── 3. Price slope ──────────────────────────────────────────────
        slope = self._compute_slope(prices, self.cfg.regime_slope_window)
        if slope is not None and abs(slope) > self.cfg.regime_slope_threshold:
            is_trend = True

        self._regime = "trend" if is_trend else "range"
        return self._regime

    def get_spread_multiplier(self) -> float:
        """Return spread multiplier for current regime."""
        if self._regime == "trend":
            return self.cfg.trend_spread_multiplier
        return self.cfg.range_spread_multiplier

    def get_size_multiplier(self) -> float:
        """Return size multiplier for current regime."""
        if self._regime == "trend":
            return self.cfg.trend_size_multiplier
        return self.cfg.range_size_multiplier

    # ── Indicators ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_rsi(prices: list[float], period: int) -> float | None:
        """Compute RSI over the last *period* prices.

        Returns a value in [0, 100] or None if insufficient data.
        """
        if len(prices) < period + 1:
            return None

        changes = [
            prices[i] - prices[i - 1] for i in range(len(prices) - period, len(prices))
        ]

        gains = [c for c in changes if c > 0]
        losses = [-c for c in changes if c < 0]

        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _compute_slope(prices: list[float], window: int) -> float | None:
        """Compute normalised price slope via simple linear regression.

        Returns slope / mean_price so that the magnitude is comparable
        across different price levels.  Returns None if insufficient data.
        """
        if len(prices) < window:
            return None

        recent = prices[-window:]
        n = len(recent)
        mean_price = sum(recent) / n
        if mean_price == 0:
            return None

        # Simple least-squares slope
        x_mean = (n - 1) / 2.0
        numerator = 0.0
        denominator = 0.0
        for i, p in enumerate(recent):
            dx = i - x_mean
            numerator += dx * p
            denominator += dx * dx

        if denominator == 0:
            return None

        raw_slope = numerator / denominator
        return raw_slope / mean_price  # Normalised
