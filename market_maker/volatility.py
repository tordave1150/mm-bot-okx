"""Causal EWMA log-return volatility estimator."""

from __future__ import annotations

import math


class CausalEWMAVolatility:
    def __init__(
        self,
        *,
        decay: float,
        minimum_samples: int,
        floor: float,
        cap: float,
    ) -> None:
        if not 0 < decay < 1:
            raise ValueError("decay must be in (0, 1)")
        if minimum_samples < 2:
            raise ValueError("minimum_samples must be at least two")
        if not 0 < floor <= cap:
            raise ValueError("invalid volatility floor/cap")
        self.decay = decay
        self.minimum_samples = minimum_samples
        self.floor = floor
        self.cap = cap
        self._previous_price: float | None = None
        self._variance: float | None = None
        self._return_count = 0

    @property
    def ready(self) -> bool:
        return self._return_count >= self.minimum_samples

    @property
    def variance(self) -> float | None:
        if not self.ready or self._variance is None:
            return None
        volatility = min(self.cap, max(self.floor, math.sqrt(self._variance)))
        return volatility * volatility

    @property
    def volatility(self) -> float | None:
        variance = self.variance
        return math.sqrt(variance) if variance is not None else None

    def update(self, mid_price: float) -> float | None:
        if not math.isfinite(mid_price) or mid_price <= 0:
            raise ValueError("mid price must be finite and positive")
        if self._previous_price is not None:
            log_return = math.log(mid_price / self._previous_price)
            squared = log_return * log_return
            self._variance = (
                squared if self._variance is None
                else self.decay * self._variance + (1 - self.decay) * squared
            )
            self._return_count += 1
        self._previous_price = mid_price
        return self.volatility
