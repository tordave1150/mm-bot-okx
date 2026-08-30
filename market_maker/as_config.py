"""Frozen Market Maker v1 research profile and unit contract."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass


STRATEGY_NAME = "market_maker_v1"
STRATEGY_VERSION = "market-maker-v1"
PROFILE_SCHEMA_VERSION = "market-maker-v1-profile-v1"


@dataclass(frozen=True)
class MarketMakerV1Config:
    strategy_name: str = STRATEGY_NAME
    schema_version: str = PROFILE_SCHEMA_VERSION

    # Exactly ten tunable strategy controls.
    risk_aversion_gamma: float = 0.08
    arrival_decay_k_or_proxy: float = 15_000.0
    volatility_ewma_decay: float = 0.92
    minimum_half_spread_bps: float = 5.0
    maximum_half_spread_bps: float = 30.0
    inventory_skew_strength: float = 1.0
    imbalance_skew_strength: float = 0.25
    minimum_order_lifetime_ticks: int = 2
    maximum_order_age_ticks: int = 8
    requote_threshold_ticks: int = 2

    # Frozen model and safety inputs; never optimizer variables.
    volatility_min_samples: int = 12
    volatility_floor: float = 0.00002
    volatility_cap: float = 0.004
    time_horizon_ticks: int = 12
    minimum_size_change_ratio: float = 0.25
    fixed_lot_size_btc: float = 0.01
    maximum_inventory_lots: int = 1
    maximum_margin_utilization: float = 0.80
    soft_session_loss_pct: float = 0.03
    hard_kill_drawdown_pct: float = 0.05
    tick_size_usdt: float = 0.1
    maker_fee_rate: float = 0.0002
    taker_fee_rate: float = 0.0005
    terminal_slippage_bps: float = 5.0

    @property
    def maximum_inventory_btc(self) -> float:
        return self.fixed_lot_size_btc * self.maximum_inventory_lots

    @property
    def tunable_parameters(self) -> dict[str, float | int]:
        payload = asdict(self)
        names = (
            "risk_aversion_gamma",
            "arrival_decay_k_or_proxy",
            "volatility_ewma_decay",
            "minimum_half_spread_bps",
            "maximum_half_spread_bps",
            "inventory_skew_strength",
            "imbalance_skew_strength",
            "minimum_order_lifetime_ticks",
            "maximum_order_age_ticks",
            "requote_threshold_ticks",
        )
        return {name: payload[name] for name in names}

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def validate(self) -> None:
        if self.strategy_name != STRATEGY_NAME:
            raise ValueError("profile is not Market Maker v1")
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            raise ValueError("unknown Market Maker v1 profile schema")
        numeric = [
            value for value in asdict(self).values()
            if isinstance(value, (int, float))
        ]
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("all profile values must be finite")
        if self.risk_aversion_gamma <= 0:
            raise ValueError("risk_aversion_gamma must be positive")
        if self.arrival_decay_k_or_proxy <= 0:
            raise ValueError("arrival_decay_k_or_proxy must be positive")
        if not 0 < self.volatility_ewma_decay < 1:
            raise ValueError("volatility_ewma_decay must be in (0, 1)")
        if not 0 < self.volatility_floor <= self.volatility_cap:
            raise ValueError("invalid volatility floor/cap")
        if self.minimum_half_spread_bps <= 0:
            raise ValueError("minimum half-spread must be positive")
        if self.maximum_half_spread_bps < self.minimum_half_spread_bps:
            raise ValueError("maximum half-spread must be >= minimum")
        if self.minimum_order_lifetime_ticks < 1:
            raise ValueError("minimum order lifetime must be positive")
        if self.maximum_order_age_ticks < self.minimum_order_lifetime_ticks:
            raise ValueError("maximum order age must cover minimum lifetime")
        if self.requote_threshold_ticks < 1:
            raise ValueError("requote threshold must be positive")
        if self.fixed_lot_size_btc != 0.01:
            raise ValueError("fixed lot size must remain 0.01 BTC")
        if self.maximum_inventory_lots != 1:
            raise ValueError("maximum inventory must remain one lot")
        if self.maximum_margin_utilization != 0.80:
            raise ValueError("maximum margin utilization must remain 0.80")
        if self.soft_session_loss_pct != 0.03:
            raise ValueError("soft session loss must remain 3%")
        if self.hard_kill_drawdown_pct != 0.05:
            raise ValueError("hard kill drawdown must remain 5%")
        if self.tick_size_usdt <= 0:
            raise ValueError("tick size must be positive")


UNIT_CONTRACT = {
    "mid_price": "USDT per BTC",
    "inventory": "BTC converted to signed inventory lots before formula use",
    "risk_aversion_gamma": "dimensionless normalized inventory-risk coefficient",
    "volatility": "per-tick log-return standard deviation",
    "volatility_variance": "per-tick squared log-return",
    "time_horizon": "ticks",
    "arrival_decay": "dimensionless inverse fractional-price-distance proxy",
    "reservation_price": "USDT per BTC",
    "half_spread": "basis points converted once to USDT per BTC",
    "tick_rounding": "bid down and ask up to tick_size_usdt",
    "normalization": (
        "reservation shift fraction = inventory_lots * gamma * "
        "return_variance * horizon; half-spread formula is evaluated as a "
        "fraction of mid price and then converted to bps"
    ),
}
