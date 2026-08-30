"""Frozen MM v1.6 profile promotion for the opt-in OKX demo runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from market_maker.as_config import MarketMakerV1Config
from market_maker.as_strategy import MarketMakerV1Strategy


SPECIFICATION_SHA256 = (
    "e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f"
)
PROFILE_ID = "mm-v1-6-profile-02"
PROFILE_NAME = "FEE_AWARE_SPREAD_6"
PROFILE_FINGERPRINT = (
    "d43c9d67183647dc624f49df8e14e0922c329b99874dc8443273f2a23caaf806"
)


class ProfilePromotionError(ValueError):
    """Raised when frozen research evidence does not map exactly to runtime."""


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PromotedProfile:
    profile_id: str
    profile_name: str
    profile_fingerprint: str
    strategy_fingerprint: str
    specification_sha256: str
    strategy: MarketMakerV1Config
    defensive_overlay: dict[str, Any]
    capital_policy: dict[str, Any]
    source_path: str

    @property
    def binding_payload(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "profile_fingerprint": self.profile_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            "specification_sha256": self.specification_sha256,
            "strategy_parameters": asdict(self.strategy),
            "defensive_overlay": self.defensive_overlay,
            "capital_policy": self.capital_policy,
            "units": {
                "price": "USDT_per_BTC",
                "quantity": "BTC_base",
                "exchange_amount": "contracts_converted_once_by_MarketSpec",
                "fee_rate": "quote_notional_fraction",
                "spread": "basis_points",
            },
        }

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self.binding_payload)

    def build_strategy(self) -> MarketMakerV1Strategy:
        return MarketMakerV1Strategy(self.strategy)


def _spec_path(root: Path) -> Path:
    path = (
        root
        / "artifacts"
        / "mm_v1_6_economic_viability"
        / "specification_20260801T070658Z"
        / "protocol_spec.json"
    )
    if not path.is_file():
        raise ProfilePromotionError(f"frozen specification missing: {path}")
    return path


def load_promoted_profile(root: Path) -> PromotedProfile:
    root = root.resolve()
    path = _spec_path(root)
    spec = json.loads(path.read_text(encoding="utf-8"))
    profiles = [
        row for row in spec.get("profiles", [])
        if row.get("profile_id") == PROFILE_ID
    ]
    if len(profiles) != 1:
        raise ProfilePromotionError("expected exactly one frozen profile")
    row = profiles[0]
    if row.get("profile_name") != PROFILE_NAME:
        raise ProfilePromotionError("frozen profile name mismatch")
    if row.get("profile_fingerprint") != PROFILE_FINGERPRINT:
        raise ProfilePromotionError("frozen profile fingerprint mismatch")
    if row.get("adaptive") is not False or row.get("v1_5_overlay_exact") is not True:
        raise ProfilePromotionError("profile adaptivity/overlay contract drift")

    strategy = MarketMakerV1Config(**row["parameters"])
    strategy.validate()
    calculated_profile_fingerprint = canonical_sha256({
        "protocol_id": spec.get("protocol_id"),
        "profile_name": row["profile_name"],
        "parameters": row["parameters"],
        "defensive_overlay": row["defensive_overlay"],
    })
    if calculated_profile_fingerprint != PROFILE_FINGERPRINT:
        raise ProfilePromotionError("runtime profile fingerprint mismatch")

    capital = dict(spec.get("capital_policy", {}))
    expected_capital = {
        "capital_usdt": 750.0,
        "hard_kill_drawdown": 0.05,
        "leverage": 3,
        "lot_size_btc": 0.01,
        "maximum_inventory_btc": 0.01,
        "maximum_margin_utilization": 0.8,
        "soft_session_loss": 0.03,
    }
    if capital != expected_capital:
        raise ProfilePromotionError("capital policy drift")

    overlay = dict(row.get("defensive_overlay", {}))
    required_overlay = {
        "drawdown_guard_pct": 0.03,
        "drawdown_guard_slippage_bps": 10.0,
        "inventory_aware_reentry": True,
        "one_sided_cooldown_ticks": 2,
        "one_sided_defensive": True,
        "one_sided_hold_until_reentry_confirmation": True,
        "one_sided_max_ticks": 3,
        "one_sided_reentry_confirmation_ticks": 2,
        "one_sided_reentry_threshold_bps": 4.2,
        "shock_threshold_bps": 4.5,
    }
    if overlay != required_overlay:
        raise ProfilePromotionError("defensive overlay drift")

    return PromotedProfile(
        profile_id=PROFILE_ID,
        profile_name=PROFILE_NAME,
        profile_fingerprint=PROFILE_FINGERPRINT,
        strategy_fingerprint=strategy.fingerprint,
        specification_sha256=SPECIFICATION_SHA256,
        strategy=strategy,
        defensive_overlay=overlay,
        capital_policy=capital,
        source_path=str(path.relative_to(root)).replace("\\", "/"),
    )


@dataclass(frozen=True)
class OverlayDecision:
    allow_quoting: bool
    suppress_side: str | None
    drawdown_guard_latched: bool
    one_sided_active: bool
    completed_reentry: bool
    reason: str
    events: tuple[dict[str, Any], ...] = ()


@dataclass
class DefensiveOverlayController:
    """Causal runtime state machine matching the frozen v1.5 overlay."""

    overlay: dict[str, Any]
    prior_mid: float = 0.0
    one_sided_remaining: int = 0
    cooldown_remaining: int = 0
    episode_active: bool = False
    confirmation_streak: int = 0
    entry_direction: int = 0
    suppressed_side: str | None = None
    inventory_aware_reentry: bool = False
    drawdown_guard_latched: bool = False
    sequence: int = 0

    def evaluate(
        self,
        *,
        mid_price: float,
        inventory_btc: float,
        drawdown: float,
        normal_fill_observed: bool,
    ) -> OverlayDecision:
        if mid_price <= 0:
            return OverlayDecision(
                False, None, self.drawdown_guard_latched, self.episode_active,
                False, "INVALID_MID",
            )
        self.sequence += 1
        events: list[dict[str, Any]] = []
        if self.drawdown_guard_latched or drawdown >= float(
            self.overlay["drawdown_guard_pct"]
        ):
            if not self.drawdown_guard_latched:
                events.append({"event": "DRAWDOWN_GUARD_ENTRY", "sequence": self.sequence})
            self.drawdown_guard_latched = True
            self.prior_mid = mid_price
            return OverlayDecision(
                False, None, True, self.episode_active, False,
                "DRAWDOWN_GUARD_LATCHED", tuple(events),
            )

        signed_move_bps = (
            (mid_price / self.prior_mid - 1.0) * 10_000.0
            if self.prior_mid > 0 else 0.0
        )
        causal_move_bps = abs(signed_move_bps)
        shock = causal_move_bps >= float(self.overlay["shock_threshold_bps"])
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
        if (
            not self.episode_active
            and self.cooldown_remaining == 0
            and (shock or normal_fill_observed)
        ):
            self.episode_active = True
            self.one_sided_remaining = max(1, int(self.overlay["one_sided_max_ticks"]))
            self.confirmation_streak = 0
            self.entry_direction = 1 if signed_move_bps >= 0 else -1
            self.suppressed_side = "sell" if mid_price >= self.prior_mid else "buy"
            events.append({
                "event": "ONE_SIDED_DEFENSIVE_ENTRY",
                "sequence": self.sequence,
                "suppressed_side": self.suppressed_side,
            })

        completed_reentry = False
        if self.episode_active:
            self.one_sided_remaining = max(0, self.one_sided_remaining - 1)
            confirmation = (
                causal_move_bps <= float(self.overlay["one_sided_reentry_threshold_bps"])
                or (
                    self.entry_direction != 0
                    and signed_move_bps * self.entry_direction < 0
                )
            )
            if self.one_sided_remaining == 0:
                self.confirmation_streak = self.confirmation_streak + 1 if confirmation else 0
            required = max(1, int(self.overlay["one_sided_reentry_confirmation_ticks"]))
            if self.one_sided_remaining == 0 and self.confirmation_streak >= required:
                events.append({
                    "event": "DEFENSIVE_REENTRY_READY",
                    "sequence": self.sequence + 1,
                })
                self.episode_active = False
                self.cooldown_remaining = max(
                    1, int(self.overlay["one_sided_cooldown_ticks"])
                )
                self.inventory_aware_reentry = (
                    bool(self.overlay["inventory_aware_reentry"])
                    and abs(inventory_btc) > 1e-12
                )
                self.suppressed_side = None
                self.confirmation_streak = 0
                self.entry_direction = 0
                completed_reentry = True

        active_side = self.suppressed_side if self.episode_active else None
        if self.inventory_aware_reentry and not self.episode_active:
            if abs(inventory_btc) <= 1e-12:
                self.inventory_aware_reentry = False
                events.append({
                    "event": "INVENTORY_AWARE_REENTRY_COMPLETE",
                    "sequence": self.sequence,
                })
            else:
                active_side = "buy" if inventory_btc > 0 else "sell"
        self.prior_mid = mid_price
        return OverlayDecision(
            True,
            active_side,
            False,
            self.episode_active,
            completed_reentry,
            "ONE_SIDED_DEFENSIVE" if active_side else "NORMAL",
            tuple(events),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["overlay"] = dict(self.overlay)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any], overlay: dict[str, Any]) -> "DefensiveOverlayController":
        if payload.get("overlay") != overlay:
            raise ProfilePromotionError("restored defensive overlay mismatch")
        fields = {
            name: payload[name]
            for name in (
                "prior_mid", "one_sided_remaining", "cooldown_remaining",
                "episode_active", "confirmation_streak", "entry_direction",
                "suppressed_side", "inventory_aware_reentry",
                "drawdown_guard_latched", "sequence",
            )
        }
        return cls(overlay=dict(overlay), **fields)
