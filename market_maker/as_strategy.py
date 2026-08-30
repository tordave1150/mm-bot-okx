"""Causal Market Maker v1 strategy façade."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from market_maker.arrival_intensity import ArrivalDecayProxy
from market_maker.as_config import MarketMakerV1Config
from market_maker.quote_model import QuoteDecision, build_quotes
from market_maker.volatility import CausalEWMAVolatility


class MarketMakerV1Strategy:
    def __init__(self, config: MarketMakerV1Config) -> None:
        config.validate()
        ArrivalDecayProxy(config.arrival_decay_k_or_proxy).validate()
        self.config = config
        self.volatility = CausalEWMAVolatility(
            decay=config.volatility_ewma_decay,
            minimum_samples=config.volatility_min_samples,
            floor=config.volatility_floor,
            cap=config.volatility_cap,
        )
        self._prefix: list[dict[str, Any]] = []

    def decide(
        self,
        tick: dict[str, Any],
        *,
        inventory_btc: float,
        market_data_age_ms: int = 0,
        staleness_limit_ms: int = 1_000,
    ) -> QuoteDecision | None:
        bids = tick.get("bids") or []
        asks = tick.get("asks") or []
        if not bids or not asks:
            return None
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        if (
            market_data_age_ms > staleness_limit_ms
            or best_bid <= 0
            or best_ask <= best_bid
        ):
            return None
        mid = (best_bid + best_ask) / 2.0
        bid_size = float(bids[0][1])
        ask_size = float(asks[0][1])
        total_size = bid_size + ask_size
        imbalance = (
            (bid_size - ask_size) / total_size if total_size > 0 else 0.0
        )
        volatility = self.volatility.update(mid)
        observation = {
            "timestamp_ms": int(tick.get("timestamp", 0)),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid,
            "imbalance": imbalance,
        }
        self._prefix.append(observation)
        if volatility is None:
            return None
        feature_payload = {
            "feature_version": "market-maker-v1-features-v1",
            "profile_fingerprint": self.config.fingerprint,
            "observed_prefix": self._prefix,
            "volatility": volatility,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                feature_payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        decision = build_quotes(
            config=self.config,
            timestamp_ms=observation["timestamp_ms"],
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            imbalance=imbalance,
            return_variance=volatility * volatility,
            inventory_btc=inventory_btc,
            feature_fingerprint=fingerprint,
        )
        if not all(
            math.isfinite(value)
            for value in (
                decision.reservation_price,
                decision.raw_half_spread_bps,
                decision.bounded_half_spread_bps,
                decision.rounded_bid,
                decision.rounded_ask,
            )
        ):
            raise ValueError("non-finite quote decision")
        return decision
