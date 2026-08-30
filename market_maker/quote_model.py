"""Unit-normalized Avellaneda–Stoikov quote calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from market_maker.as_config import MarketMakerV1Config
from market_maker.inventory import inventory_control


@dataclass(frozen=True)
class QuoteDecision:
    timestamp_ms: int
    quote_allowed: bool
    quote_reason: str
    suppression_reason: str
    reservation_price: float
    raw_half_spread_bps: float
    bounded_half_spread_bps: float
    raw_bid: float
    raw_ask: float
    rounded_bid: float
    rounded_ask: float
    bid_size_btc: float
    ask_size_btc: float
    bid_suppressed: bool
    ask_suppressed: bool
    inventory_utilization: float
    feature_fingerprint: str


def reservation_price(
    *,
    mid_price: float,
    inventory_lots: float,
    gamma: float,
    return_variance: float,
    time_horizon_ticks: int,
) -> float:
    values = (mid_price, inventory_lots, gamma, return_variance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("reservation-price inputs must be finite")
    if mid_price <= 0 or gamma <= 0 or return_variance < 0:
        raise ValueError("invalid reservation-price inputs")
    shift_fraction = (
        inventory_lots * gamma * return_variance * time_horizon_ticks
    )
    return mid_price * (1.0 - shift_fraction)


def avellaneda_stoikov_half_spread_bps(
    *,
    gamma: float,
    arrival_decay: float,
    return_variance: float,
    time_horizon_ticks: int,
) -> float:
    values = (gamma, arrival_decay, return_variance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("half-spread inputs must be finite")
    if gamma <= 0 or arrival_decay <= 0 or return_variance < 0:
        raise ValueError("invalid half-spread inputs")
    fraction = (
        math.log1p(gamma / arrival_decay) / gamma
        + 0.5 * gamma * return_variance * time_horizon_ticks
    )
    return fraction * 10_000.0


def _round_down(value: float, tick: float) -> float:
    return float(
        (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(
            rounding=ROUND_FLOOR
        ) * Decimal(str(tick))
    )


def _round_up(value: float, tick: float) -> float:
    return float(
        (Decimal(str(value)) / Decimal(str(tick))).to_integral_value(
            rounding=ROUND_CEILING
        ) * Decimal(str(tick))
    )


def build_quotes(
    *,
    config: MarketMakerV1Config,
    timestamp_ms: int,
    best_bid: float,
    best_ask: float,
    mid_price: float,
    imbalance: float,
    return_variance: float,
    inventory_btc: float,
    feature_fingerprint: str,
) -> QuoteDecision:
    config.validate()
    values = (
        best_bid, best_ask, mid_price, imbalance,
        return_variance, inventory_btc,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("quote inputs must be finite")
    if best_bid <= 0 or best_ask <= best_bid or mid_price <= 0:
        raise ValueError("invalid two-sided market")
    inventory_lots = inventory_btc / config.fixed_lot_size_btc
    reservation = reservation_price(
        mid_price=mid_price,
        inventory_lots=inventory_lots,
        gamma=config.risk_aversion_gamma,
        return_variance=return_variance,
        time_horizon_ticks=config.time_horizon_ticks,
    )
    reservation -= (
        mid_price
        * max(-1.0, min(1.0, imbalance))
        * config.imbalance_skew_strength
        * return_variance
    )
    raw_half_bps = avellaneda_stoikov_half_spread_bps(
        gamma=config.risk_aversion_gamma,
        arrival_decay=config.arrival_decay_k_or_proxy,
        return_variance=return_variance,
        time_horizon_ticks=config.time_horizon_ticks,
    )
    bounded_bps = min(
        config.maximum_half_spread_bps,
        max(config.minimum_half_spread_bps, raw_half_bps),
    )
    half_usdt = mid_price * bounded_bps / 10_000.0
    raw_bid = reservation - half_usdt
    raw_ask = reservation + half_usdt
    rounded_bid = min(_round_down(raw_bid, config.tick_size_usdt), best_bid)
    rounded_ask = max(_round_up(raw_ask, config.tick_size_usdt), best_ask)
    controls = inventory_control(
        inventory_btc=inventory_btc,
        fixed_lot_size_btc=config.fixed_lot_size_btc,
        maximum_inventory_btc=config.maximum_inventory_btc,
    )
    valid = (
        rounded_bid > 0
        and rounded_ask > rounded_bid
        and rounded_bid <= best_bid
        and rounded_ask >= best_ask
    )
    suppression = ""
    if not valid:
        suppression = "INVALID_POST_ONLY_QUOTES"
    elif controls.bid_suppressed and controls.ask_suppressed:
        suppression = "INVENTORY_BOTH_SIDES_SUPPRESSED"
    return QuoteDecision(
        timestamp_ms=timestamp_ms,
        quote_allowed=valid and not (
            controls.bid_suppressed and controls.ask_suppressed
        ),
        quote_reason="AVELLANEDA_STOIKOV" if valid else "NO_QUOTE",
        suppression_reason=suppression,
        reservation_price=reservation,
        raw_half_spread_bps=raw_half_bps,
        bounded_half_spread_bps=bounded_bps,
        raw_bid=raw_bid,
        raw_ask=raw_ask,
        rounded_bid=rounded_bid,
        rounded_ask=rounded_ask,
        bid_size_btc=controls.bid_size_btc,
        ask_size_btc=controls.ask_size_btc,
        bid_suppressed=controls.bid_suppressed,
        ask_suppressed=controls.ask_suppressed,
        inventory_utilization=controls.utilization,
        feature_fingerprint=feature_fingerprint,
    )
