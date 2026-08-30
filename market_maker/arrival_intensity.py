"""Declared smoke-only arrival-decay proxy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArrivalDecayProxy:
    value: float
    mode: str = "FIXED_DECLARED_PROXY"
    empirical_calibration: bool = False
    exchange_ready: bool = False

    def validate(self) -> None:
        if self.mode != "FIXED_DECLARED_PROXY":
            raise ValueError("unsupported arrival-decay mode")
        if self.value <= 0:
            raise ValueError("arrival-decay proxy must be positive")
        if self.empirical_calibration or self.exchange_ready:
            raise ValueError("smoke proxy cannot claim calibration or readiness")
