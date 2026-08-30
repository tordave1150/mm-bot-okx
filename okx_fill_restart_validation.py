"""Offline safety kernel for OKX Demo fill and restart validation.

This module has no exchange or network dependency.  It models the durable
state and fail-closed transitions that a separately armed OKX Demo process
must use.  Offline fixtures exercise the same accounting, reconciliation,
checkpoint, resume-token, and kill-latch contracts before any network access
is permitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
PROTOCOL_ID = "okx-demo-fill-restart-validation-v1"
V16_SPECIFICATION_SHA256 = (
    "e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f"
)
PRIOR_DEMO_SPECIFICATION_SHA256 = (
    "10063903656d0bde6ea6259134bbe1e19409a1babb10c682ebd644080123508e"
)
PRIOR_DEMO_COMPLETED_SHA256 = (
    "288889525e85c3579f5bbec50581b4f124259e73c813fab494b0f6b9d368afa1"
)
PROFILE_ID = "mm-v1-6-profile-02"
PROFILE_NAME = "FEE_AWARE_SPREAD_6"
SYMBOL = "BTC/USDT:USDT"
BASE_STEP_BTC = Decimal("0.01")
MAXIMUM_INVENTORY_BTC = Decimal("0.01")


class ValidationSafetyError(RuntimeError):
    """A safety invariant is unknown, incomplete, or violated."""


class PersistenceFailure(ValidationSafetyError):
    """Durable state could not be written or verified."""


class RunPhase(str, Enum):
    RUNNING = "RUNNING"
    RESTART_REQUIRED_AFTER_FILL = "RESTART_REQUIRED_AFTER_FILL"
    RUNNING_AFTER_R1 = "RUNNING_AFTER_R1"
    RESTART_REQUIRED_AFTER_KILL_LATCH = "RESTART_REQUIRED_AFTER_KILL_LATCH"
    KILL_LATCH_BLOCKED = "KILL_LATCH_BLOCKED"
    RUNNING_AFTER_R2 = "RUNNING_AFTER_R2"
    HALTED = "HALTED"


class CheckpointKind(str, Enum):
    NONE = "NONE"
    R1_AFTER_FILL = "R1_AFTER_FILL"
    R2_KILL_LATCH = "R2_KILL_LATCH"


def canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationSafetyError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise ValidationSafetyError(f"{name} is not finite")
    return result


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValidationSafetyError(f"{name} is not a lowercase SHA-256")


@dataclass(frozen=True)
class RiskBudget:
    maximum_wall_minutes: int = 120
    maximum_normal_creates: int = 120
    maximum_owned_bid: int = 1
    maximum_owned_ask: int = 1
    maximum_inventory_btc: Decimal = MAXIMUM_INVENTORY_BTC
    capital_usdt: Decimal = Decimal("750")
    leverage: int = 3
    soft_guard_usdt: Decimal = Decimal("22.50")
    hard_kill_usdt: Decimal = Decimal("37.50")
    maximum_unresolved_flatten: int = 1

    def validate(self) -> None:
        if self.maximum_wall_minutes > 120 or self.maximum_wall_minutes <= 0:
            raise ValidationSafetyError("wall-time budget is invalid")
        if self.maximum_normal_creates > 120 or self.maximum_normal_creates <= 0:
            raise ValidationSafetyError("normal-create budget is invalid")
        if self.maximum_owned_bid != 1 or self.maximum_owned_ask != 1:
            raise ValidationSafetyError("owned-side limit drift")
        if self.maximum_inventory_btc > MAXIMUM_INVENTORY_BTC:
            raise ValidationSafetyError("inventory budget is wider than protocol")
        if self.capital_usdt != Decimal("750") or self.leverage != 3:
            raise ValidationSafetyError("capital or leverage drift")
        if self.soft_guard_usdt > Decimal("22.50"):
            raise ValidationSafetyError("soft guard is wider than protocol")
        if self.hard_kill_usdt > Decimal("37.50"):
            raise ValidationSafetyError("hard kill is wider than protocol")
        if self.maximum_unresolved_flatten != 1:
            raise ValidationSafetyError("flatten single-flight policy drift")

    def to_dict(self) -> dict[str, object]:
        return {
            "maximum_wall_minutes": self.maximum_wall_minutes,
            "maximum_normal_creates": self.maximum_normal_creates,
            "maximum_owned_bid": self.maximum_owned_bid,
            "maximum_owned_ask": self.maximum_owned_ask,
            "maximum_inventory_btc": str(self.maximum_inventory_btc),
            "capital_usdt": str(self.capital_usdt),
            "leverage": self.leverage,
            "soft_guard_usdt": str(self.soft_guard_usdt),
            "hard_kill_usdt": str(self.hard_kill_usdt),
            "maximum_unresolved_flatten": self.maximum_unresolved_flatten,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RiskBudget":
        result = cls(
            maximum_wall_minutes=int(value["maximum_wall_minutes"]),
            maximum_normal_creates=int(value["maximum_normal_creates"]),
            maximum_owned_bid=int(value["maximum_owned_bid"]),
            maximum_owned_ask=int(value["maximum_owned_ask"]),
            maximum_inventory_btc=_decimal(
                value["maximum_inventory_btc"], "maximum_inventory_btc"
            ),
            capital_usdt=_decimal(value["capital_usdt"], "capital_usdt"),
            leverage=int(value["leverage"]),
            soft_guard_usdt=_decimal(value["soft_guard_usdt"], "soft_guard_usdt"),
            hard_kill_usdt=_decimal(value["hard_kill_usdt"], "hard_kill_usdt"),
            maximum_unresolved_flatten=int(value["maximum_unresolved_flatten"]),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class RuntimeBinding:
    run_id: str
    execution_mode: str
    account_binding: str
    market_fingerprint: str
    profile_binding_sha256: str
    runtime_configuration_sha256: str
    source_manifest_sha256: str
    symbol: str = SYMBOL
    market_type: str = "linear_swap"
    margin_mode: str = "isolated"
    position_mode: str = "net_mode"
    leverage: int = 3
    profile_id: str = PROFILE_ID
    profile_name: str = PROFILE_NAME
    v16_specification_sha256: str = V16_SPECIFICATION_SHA256
    prior_demo_specification_sha256: str = PRIOR_DEMO_SPECIFICATION_SHA256
    prior_demo_completed_sha256: str = PRIOR_DEMO_COMPLETED_SHA256

    def validate(self) -> None:
        if not self.run_id or any(ch.isspace() for ch in self.run_id):
            raise ValidationSafetyError("run_id is missing or contains whitespace")
        if self.execution_mode not in {"OFFLINE_FIXTURE", "OKX_DEMO"}:
            raise ValidationSafetyError("LIVE or unknown execution mode is unavailable")
        if not self.account_binding or not self.market_fingerprint:
            raise ValidationSafetyError("account/market identity binding is incomplete")
        if self.symbol != SYMBOL or self.market_type != "linear_swap":
            raise ValidationSafetyError("symbol or market type drift")
        if self.margin_mode != "isolated" or self.position_mode != "net_mode":
            raise ValidationSafetyError("account mode drift")
        if self.leverage != 3:
            raise ValidationSafetyError("leverage drift")
        if self.profile_id != PROFILE_ID or self.profile_name != PROFILE_NAME:
            raise ValidationSafetyError("profile identity drift")
        if self.v16_specification_sha256 != V16_SPECIFICATION_SHA256:
            raise ValidationSafetyError("v1.6 specification drift")
        if self.prior_demo_specification_sha256 != PRIOR_DEMO_SPECIFICATION_SHA256:
            raise ValidationSafetyError("predecessor Demo specification drift")
        if self.prior_demo_completed_sha256 != PRIOR_DEMO_COMPLETED_SHA256:
            raise ValidationSafetyError("predecessor Demo completion drift")
        for name in (
            "profile_binding_sha256",
            "runtime_configuration_sha256",
            "source_manifest_sha256",
        ):
            _require_sha256(str(getattr(self, name)), name)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "execution_mode": self.execution_mode,
            "account_binding": self.account_binding,
            "market_fingerprint": self.market_fingerprint,
            "profile_binding_sha256": self.profile_binding_sha256,
            "runtime_configuration_sha256": self.runtime_configuration_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "margin_mode": self.margin_mode,
            "position_mode": self.position_mode,
            "leverage": self.leverage,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "v16_specification_sha256": self.v16_specification_sha256,
            "prior_demo_specification_sha256": self.prior_demo_specification_sha256,
            "prior_demo_completed_sha256": self.prior_demo_completed_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RuntimeBinding":
        result = cls(**value)
        result.validate()
        return result


@dataclass
class OwnedOrder:
    client_order_id: str
    order_id: str
    side: str
    quantity_btc: Decimal
    remaining_btc: Decimal
    reduce_only: bool
    post_only_acknowledged: bool
    status: str = "ACKNOWLEDGED"

    def validate(self) -> None:
        if not self.client_order_id or not self.order_id:
            raise ValidationSafetyError("owned order identity is incomplete")
        if self.side not in {"buy", "sell"}:
            raise ValidationSafetyError("owned order side is invalid")
        if self.quantity_btc <= 0 or self.quantity_btc > MAXIMUM_INVENTORY_BTC:
            raise ValidationSafetyError("owned order quantity violates cap")
        if self.remaining_btc < 0 or self.remaining_btc > self.quantity_btc:
            raise ValidationSafetyError("owned order remainder is invalid")
        if self.status not in {"ACKNOWLEDGED", "CANCEL_CONFIRMED", "FILLED"}:
            raise ValidationSafetyError("owned order status is invalid")
        if not self.reduce_only and not self.post_only_acknowledged:
            raise ValidationSafetyError("normal order lacks post-only acknowledgement")

    @property
    def is_open(self) -> bool:
        return self.status == "ACKNOWLEDGED" and self.remaining_btc > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "side": self.side,
            "quantity_btc": str(self.quantity_btc),
            "remaining_btc": str(self.remaining_btc),
            "reduce_only": self.reduce_only,
            "post_only_acknowledged": self.post_only_acknowledged,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "OwnedOrder":
        result = cls(
            client_order_id=str(value["client_order_id"]),
            order_id=str(value["order_id"]),
            side=str(value["side"]),
            quantity_btc=_decimal(value["quantity_btc"], "quantity_btc"),
            remaining_btc=_decimal(value["remaining_btc"], "remaining_btc"),
            reduce_only=bool(value["reduce_only"]),
            post_only_acknowledged=bool(value["post_only_acknowledged"]),
            status=str(value["status"]),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class FillEvent:
    trade_id: str
    order_id: str
    client_order_id: str
    timestamp_ms: int
    side: str
    price: Decimal
    quantity_btc: Decimal
    fee_cost: Decimal
    fee_currency: str
    liquidity: str
    reduce_only: bool

    def validate(self) -> None:
        if not self.trade_id or not self.order_id or not self.client_order_id:
            raise ValidationSafetyError("fill identity is incomplete")
        if self.timestamp_ms <= 0:
            raise ValidationSafetyError("fill timestamp is missing")
        if self.side not in {"buy", "sell"}:
            raise ValidationSafetyError("fill side is invalid")
        if self.price <= 0 or self.quantity_btc <= 0:
            raise ValidationSafetyError("fill price/quantity is invalid")
        if self.fee_currency not in {"USDT", "BTC"}:
            raise ValidationSafetyError("fill fee currency is unsupported")
        if self.liquidity not in {"maker", "taker"}:
            raise ValidationSafetyError("fill liquidity classification is unknown")

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "timestamp_ms": self.timestamp_ms,
            "side": self.side,
            "price": str(self.price),
            "quantity_btc": str(self.quantity_btc),
            "fee_cost": str(self.fee_cost),
            "fee_currency": self.fee_currency,
            "liquidity": self.liquidity,
            "reduce_only": self.reduce_only,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FillEvent":
        required = {
            "trade_id", "order_id", "client_order_id", "timestamp_ms",
            "side", "price", "quantity_btc", "fee_cost", "fee_currency",
            "liquidity", "reduce_only",
        }
        if set(value) != required:
            missing = sorted(required - set(value))
            extra = sorted(set(value) - required)
            raise ValidationSafetyError(
                f"fill fields mismatch missing={missing} extra={extra}"
            )
        result = cls(
            trade_id=str(value["trade_id"]),
            order_id=str(value["order_id"]),
            client_order_id=str(value["client_order_id"]),
            timestamp_ms=int(value["timestamp_ms"]),
            side=str(value["side"]),
            price=_decimal(value["price"], "fill price"),
            quantity_btc=_decimal(value["quantity_btc"], "fill quantity"),
            fee_cost=_decimal(value["fee_cost"], "fill fee"),
            fee_currency=str(value["fee_currency"]).upper(),
            liquidity=str(value["liquidity"]).lower(),
            reduce_only=bool(value["reduce_only"]),
        )
        result.validate()
        return result


@dataclass
class FillDedupCursor:
    watermark_ms: int = 0
    ids_at_watermark: list[str] = field(default_factory=list)
    seen_fingerprints: dict[str, str] = field(default_factory=dict)
    late_fill_count: int = 0

    def select_new_pages(self, pages: Sequence[dict[str, object]]) -> list[FillEvent]:
        if not pages:
            raise ValidationSafetyError("fill pagination snapshot is empty")
        expected_cursor = ""
        observed_cursors: set[str] = set()
        fetched: dict[str, FillEvent] = {}
        for expected_index, page in enumerate(pages):
            if int(page.get("page_index", -1)) != expected_index:
                raise ValidationSafetyError("fill page order is non-monotonic")
            cursor = str(page.get("cursor", ""))
            if cursor != expected_cursor or cursor in observed_cursors:
                raise ValidationSafetyError("fill pagination cursor chain is invalid")
            observed_cursors.add(cursor)
            trades = page.get("trades")
            if not isinstance(trades, list):
                raise ValidationSafetyError("fill page trades are unavailable")
            for value in trades:
                if not isinstance(value, dict):
                    raise ValidationSafetyError("fill page contains malformed trade")
                fill = FillEvent.from_dict(value)
                prior = fetched.get(fill.trade_id)
                if prior is not None and prior.fingerprint != fill.fingerprint:
                    raise ValidationSafetyError("conflicting duplicate fill in pagination")
                fetched[fill.trade_id] = fill
            expected_cursor = str(page.get("next_cursor", ""))
            if expected_cursor == "" and expected_index != len(pages) - 1:
                raise ValidationSafetyError("fill pagination terminated early")
        if expected_cursor:
            raise ValidationSafetyError("fill pagination snapshot is incomplete")

        selected: list[FillEvent] = []
        for fill in sorted(fetched.values(), key=lambda row: (row.timestamp_ms, row.trade_id)):
            prior_fingerprint = self.seen_fingerprints.get(fill.trade_id)
            if prior_fingerprint:
                if prior_fingerprint != fill.fingerprint:
                    raise ValidationSafetyError("persisted fill identity changed")
                continue
            if fill.timestamp_ms < self.watermark_ms:
                self.late_fill_count += 1
            self.seen_fingerprints[fill.trade_id] = fill.fingerprint
            selected.append(fill)

        if selected:
            maximum = max(self.watermark_ms, max(row.timestamp_ms for row in selected))
            ids = set(self.ids_at_watermark if maximum == self.watermark_ms else [])
            ids.update(row.trade_id for row in selected if row.timestamp_ms == maximum)
            self.watermark_ms = maximum
            self.ids_at_watermark = sorted(ids)
        return selected

    def to_dict(self) -> dict[str, object]:
        return {
            "watermark_ms": self.watermark_ms,
            "ids_at_watermark": list(self.ids_at_watermark),
            "seen_fingerprints": dict(sorted(self.seen_fingerprints.items())),
            "late_fill_count": self.late_fill_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FillDedupCursor":
        return cls(
            watermark_ms=int(value.get("watermark_ms", 0)),
            ids_at_watermark=[str(row) for row in value.get("ids_at_watermark", [])],
            seen_fingerprints={
                str(key): str(item)
                for key, item in dict(value.get("seen_fingerprints", {})).items()
            },
            late_fill_count=int(value.get("late_fill_count", 0)),
        )


@dataclass
class FillLedger:
    inventory_btc: Decimal = Decimal("0")
    average_entry_price: Decimal = Decimal("0")
    # The aggregate fields reconcile the authoritative account across every
    # owned fill, including an emergency reduce-only flatten.  Normal and
    # special attribution is retained separately so a safety flatten can
    # never improve or contaminate promoted maker economics.
    gross_realized_pnl_usdt: Decimal = Decimal("0")
    total_fees_usdt: Decimal = Decimal("0")
    net_realized_pnl_usdt: Decimal = Decimal("0")
    normal_gross_realized_pnl_usdt: Decimal = Decimal("0")
    normal_fees_usdt: Decimal = Decimal("0")
    normal_net_realized_pnl_usdt: Decimal = Decimal("0")
    special_gross_realized_pnl_usdt: Decimal = Decimal("0")
    special_fees_usdt: Decimal = Decimal("0")
    special_net_realized_pnl_usdt: Decimal = Decimal("0")
    fill_fingerprints: dict[str, str] = field(default_factory=dict)
    normal_bid_fills: int = 0
    normal_ask_fills: int = 0
    special_fill_count: int = 0
    normal_fifo_lots: list[dict[str, object]] = field(default_factory=list)
    # Match fragments are accounting detail. A round trip is emitted only
    # when one FIFO entry lot is fully closed.
    normal_fifo_match_fragments: list[dict[str, str]] = field(default_factory=list)
    normal_round_trips: list[dict[str, object]] = field(default_factory=list)
    normal_inventory_cycles: list[dict[str, str]] = field(default_factory=list)

    @property
    def normal_fill_count(self) -> int:
        return self.normal_bid_fills + self.normal_ask_fills

    def _apply_fifo(self, fill: FillEvent, *, eligible: bool) -> None:
        signed = fill.quantity_btc if fill.side == "buy" else -fill.quantity_btc
        remaining = signed
        while self.normal_fifo_lots and remaining != 0:
            lot = self.normal_fifo_lots[0]
            lot_quantity = _decimal(lot["quantity_signed_btc"], "FIFO lot quantity")
            if lot_quantity * remaining > 0:
                break
            closed = min(abs(lot_quantity), abs(remaining))
            lot_evidence_eligible = eligible and bool(
                lot.get("round_trip_eligible", True)
            )
            if lot_evidence_eligible:
                entry_price = _decimal(lot["price"], "FIFO entry price")
                direction = Decimal("1") if lot_quantity > 0 else Decimal("-1")
                fragment_gross = closed * (fill.price - entry_price) * direction
                self.normal_fifo_match_fragments.append({
                    "entry_fill_id": lot["fill_id"],
                    "exit_fill_id": fill.trade_id,
                    "quantity_btc": str(closed),
                    "gross_pnl_usdt": str(fragment_gross),
                })
                matched_before = _decimal(
                    lot.get("matched_quantity_btc", "0"),
                    "FIFO matched quantity",
                )
                gross_before = _decimal(
                    lot.get("matched_gross_pnl_usdt", "0"),
                    "FIFO matched gross PnL",
                )
                exit_ids = [str(item) for item in lot.get("exit_fill_ids", [])]
                exit_ids.append(fill.trade_id)
                lot["matched_quantity_btc"] = str(matched_before + closed)
                lot["matched_gross_pnl_usdt"] = str(gross_before + fragment_gross)
                lot["exit_fill_ids"] = exit_ids
            else:
                special_before = _decimal(
                    lot.get("special_matched_quantity_btc", "0"),
                    "FIFO special matched quantity",
                )
                lot["special_matched_quantity_btc"] = str(
                    special_before + closed
                )
                lot["round_trip_eligible"] = False
            lot_remainder = abs(lot_quantity) - closed
            remaining_remainder = abs(remaining) - closed
            if lot_remainder == 0:
                if lot_evidence_eligible:
                    original = _decimal(
                        lot.get("original_quantity_btc", abs(lot_quantity)),
                        "FIFO original quantity",
                    )
                    matched = _decimal(
                        lot.get("matched_quantity_btc", "0"),
                        "FIFO matched quantity",
                    )
                    if matched != original:
                        raise ValidationSafetyError(
                            "completed FIFO lot quantity does not reconcile"
                        )
                    self.normal_round_trips.append({
                        "entry_fill_id": str(lot["fill_id"]),
                        "exit_fill_id": fill.trade_id,
                        "exit_fill_ids": list(lot.get("exit_fill_ids", [])),
                        "quantity_btc": str(original),
                        "gross_pnl_usdt": str(_decimal(
                            lot.get("matched_gross_pnl_usdt", "0"),
                            "FIFO completed gross PnL",
                        )),
                        "status": "FULLY_CLOSED_FIFO_LOT",
                    })
                self.normal_fifo_lots.pop(0)
            else:
                lot["quantity_signed_btc"] = str(
                    lot_remainder if lot_quantity > 0 else -lot_remainder
                )
            remaining = (
                Decimal("0") if remaining_remainder == 0
                else (remaining_remainder if remaining > 0 else -remaining_remainder)
            )
        if eligible and remaining != 0:
            self.normal_fifo_lots.append({
                "fill_id": fill.trade_id,
                "quantity_signed_btc": str(remaining),
                "original_quantity_btc": str(abs(remaining)),
                "price": str(fill.price),
                "matched_quantity_btc": "0",
                "matched_gross_pnl_usdt": "0",
                "exit_fill_ids": [],
                "special_matched_quantity_btc": "0",
                "round_trip_eligible": True,
            })

    def apply(self, fill: FillEvent, order: OwnedOrder) -> bool:
        prior = self.fill_fingerprints.get(fill.trade_id)
        if prior:
            if prior != fill.fingerprint:
                raise ValidationSafetyError("conflicting duplicate fill in ledger")
            return False
        if fill.client_order_id != order.client_order_id or fill.order_id != order.order_id:
            raise ValidationSafetyError("fill does not map exactly to owned order")
        if fill.side != order.side or fill.reduce_only != order.reduce_only:
            raise ValidationSafetyError("fill/order side or reduce-only mismatch")
        if fill.quantity_btc > order.remaining_btc:
            raise ValidationSafetyError("fill exceeds owned order remainder")
        normal = not order.reduce_only
        if normal and (not order.post_only_acknowledged or fill.liquidity != "maker"):
            raise ValidationSafetyError("normal fill lacks maker/post-only proof")

        signed = fill.quantity_btc if fill.side == "buy" else -fill.quantity_btc
        current = self.inventory_btc
        average = self.average_entry_price
        resulting = current + signed
        if abs(resulting) > MAXIMUM_INVENTORY_BTC:
            raise ValidationSafetyError("fill would exceed maximum inventory")
        if order.reduce_only and (
            current == 0 or current * signed >= 0 or abs(signed) > abs(current)
        ):
            raise ValidationSafetyError("special fill is not strictly reduce-only")

        realized = Decimal("0")
        if current == 0 or current * signed > 0:
            total = abs(current) + abs(signed)
            new_average = (
                (abs(current) * average + abs(signed) * fill.price) / total
            )
        else:
            closed = min(abs(current), abs(signed))
            direction = Decimal("1") if current > 0 else Decimal("-1")
            realized = closed * (fill.price - average) * direction
            if resulting == 0:
                new_average = Decimal("0")
            elif current * resulting > 0:
                new_average = average
            else:
                new_average = fill.price

        fee_usdt = (
            fill.fee_cost
            if fill.fee_currency == "USDT"
            else fill.fee_cost * fill.price
        )
        notional = fill.quantity_btc * fill.price
        if abs(fee_usdt) > notional * Decimal("0.01"):
            raise ValidationSafetyError("fill fee is outside bounded reconciliation range")

        self._apply_fifo(fill, eligible=normal)
        if normal and current != 0 and resulting == 0:
            self.normal_inventory_cycles.append({
                "exit_fill_id": fill.trade_id,
                "closed_inventory_btc": str(abs(current)),
                "resulting_inventory_btc": "0",
            })
        self.inventory_btc = resulting
        self.average_entry_price = new_average
        self.gross_realized_pnl_usdt += realized
        self.total_fees_usdt += fee_usdt
        self.net_realized_pnl_usdt = (
            self.gross_realized_pnl_usdt - self.total_fees_usdt
        )
        if normal:
            self.normal_gross_realized_pnl_usdt += realized
            self.normal_fees_usdt += fee_usdt
            self.normal_net_realized_pnl_usdt = (
                self.normal_gross_realized_pnl_usdt - self.normal_fees_usdt
            )
        else:
            self.special_gross_realized_pnl_usdt += realized
            self.special_fees_usdt += fee_usdt
            self.special_net_realized_pnl_usdt = (
                self.special_gross_realized_pnl_usdt - self.special_fees_usdt
            )
        self.fill_fingerprints[fill.trade_id] = fill.fingerprint
        if normal and fill.side == "buy":
            self.normal_bid_fills += 1
        elif normal:
            self.normal_ask_fills += 1
        else:
            self.special_fill_count += 1
        order.remaining_btc -= fill.quantity_btc
        if order.remaining_btc == 0:
            order.status = "FILLED"
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "inventory_btc": str(self.inventory_btc),
            "average_entry_price": str(self.average_entry_price),
            "gross_realized_pnl_usdt": str(self.gross_realized_pnl_usdt),
            "total_fees_usdt": str(self.total_fees_usdt),
            "net_realized_pnl_usdt": str(self.net_realized_pnl_usdt),
            "normal_gross_realized_pnl_usdt": str(
                self.normal_gross_realized_pnl_usdt
            ),
            "normal_fees_usdt": str(self.normal_fees_usdt),
            "normal_net_realized_pnl_usdt": str(
                self.normal_net_realized_pnl_usdt
            ),
            "special_gross_realized_pnl_usdt": str(
                self.special_gross_realized_pnl_usdt
            ),
            "special_fees_usdt": str(self.special_fees_usdt),
            "special_net_realized_pnl_usdt": str(
                self.special_net_realized_pnl_usdt
            ),
            "fill_fingerprints": dict(sorted(self.fill_fingerprints.items())),
            "normal_bid_fills": self.normal_bid_fills,
            "normal_ask_fills": self.normal_ask_fills,
            "special_fill_count": self.special_fill_count,
            "normal_fifo_lots": list(self.normal_fifo_lots),
            "normal_fifo_match_fragments": list(
                self.normal_fifo_match_fragments
            ),
            "normal_round_trips": list(self.normal_round_trips),
            "normal_inventory_cycles": list(self.normal_inventory_cycles),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FillLedger":
        special_fill_count = int(value.get("special_fill_count", 0))
        split_fields = (
            "normal_gross_realized_pnl_usdt",
            "normal_fees_usdt",
            "normal_net_realized_pnl_usdt",
            "special_gross_realized_pnl_usdt",
            "special_fees_usdt",
            "special_net_realized_pnl_usdt",
        )
        if special_fill_count and not all(field in value for field in split_fields):
            raise ValidationSafetyError(
                "restored ledger lacks special-fill economics attribution"
            )
        gross = _decimal(value["gross_realized_pnl_usdt"], "ledger gross PnL")
        fees = _decimal(value["total_fees_usdt"], "ledger fees")
        net = _decimal(value["net_realized_pnl_usdt"], "ledger net PnL")
        result = cls(
            inventory_btc=_decimal(value["inventory_btc"], "ledger inventory"),
            average_entry_price=_decimal(
                value["average_entry_price"], "ledger average entry"
            ),
            gross_realized_pnl_usdt=gross,
            total_fees_usdt=fees,
            net_realized_pnl_usdt=net,
            normal_gross_realized_pnl_usdt=_decimal(
                value.get("normal_gross_realized_pnl_usdt", gross),
                "normal ledger gross PnL",
            ),
            normal_fees_usdt=_decimal(
                value.get("normal_fees_usdt", fees), "normal ledger fees"
            ),
            normal_net_realized_pnl_usdt=_decimal(
                value.get("normal_net_realized_pnl_usdt", net),
                "normal ledger net PnL",
            ),
            special_gross_realized_pnl_usdt=_decimal(
                value.get("special_gross_realized_pnl_usdt", "0"),
                "special ledger gross PnL",
            ),
            special_fees_usdt=_decimal(
                value.get("special_fees_usdt", "0"), "special ledger fees"
            ),
            special_net_realized_pnl_usdt=_decimal(
                value.get("special_net_realized_pnl_usdt", "0"),
                "special ledger net PnL",
            ),
            fill_fingerprints={
                str(key): str(item)
                for key, item in dict(value.get("fill_fingerprints", {})).items()
            },
            normal_bid_fills=int(value.get("normal_bid_fills", 0)),
            normal_ask_fills=int(value.get("normal_ask_fills", 0)),
            special_fill_count=special_fill_count,
            normal_fifo_lots=[dict(row) for row in value.get("normal_fifo_lots", [])],
            normal_fifo_match_fragments=[
                dict(row)
                for row in value.get("normal_fifo_match_fragments", [])
            ],
            normal_round_trips=[
                dict(row) for row in value.get("normal_round_trips", [])
            ],
            normal_inventory_cycles=[
                dict(row) for row in value.get("normal_inventory_cycles", [])
            ],
        )
        if abs(result.inventory_btc) > MAXIMUM_INVENTORY_BTC:
            raise ValidationSafetyError("restored ledger exceeds inventory cap")
        if result.net_realized_pnl_usdt != (
            result.gross_realized_pnl_usdt - result.total_fees_usdt
        ):
            raise ValidationSafetyError("restored ledger PnL does not reconcile")
        if result.normal_net_realized_pnl_usdt != (
            result.normal_gross_realized_pnl_usdt - result.normal_fees_usdt
        ):
            raise ValidationSafetyError("restored normal economics do not reconcile")
        if result.special_net_realized_pnl_usdt != (
            result.special_gross_realized_pnl_usdt - result.special_fees_usdt
        ):
            raise ValidationSafetyError("restored special economics do not reconcile")
        if (
            result.gross_realized_pnl_usdt
            != result.normal_gross_realized_pnl_usdt
            + result.special_gross_realized_pnl_usdt
            or result.total_fees_usdt
            != result.normal_fees_usdt + result.special_fees_usdt
            or result.net_realized_pnl_usdt
            != result.normal_net_realized_pnl_usdt
            + result.special_net_realized_pnl_usdt
        ):
            raise ValidationSafetyError(
                "restored aggregate and attributed economics do not reconcile"
            )
        for lot in result.normal_fifo_lots:
            remaining = abs(_decimal(
                lot["quantity_signed_btc"], "FIFO lot quantity"
            ))
            original = _decimal(
                lot.get("original_quantity_btc", remaining),
                "FIFO original quantity",
            )
            matched = _decimal(
                lot.get("matched_quantity_btc", "0"),
                "FIFO matched quantity",
            )
            special_matched = _decimal(
                lot.get("special_matched_quantity_btc", "0"),
                "FIFO special matched quantity",
            )
            if remaining + matched + special_matched != original:
                raise ValidationSafetyError("restored FIFO lot quantity mismatch")
        return result


@dataclass(frozen=True)
class AuthoritativeSnapshot:
    sequence: int
    account_binding: str
    symbol: str
    market_fingerprint: str
    position_btc: Decimal
    average_entry_price: Decimal
    run_fees_usdt: Decimal
    open_orders: tuple[dict[str, str], ...] = ()
    orders_complete: bool = True
    trades_complete: bool = True
    position_complete: bool = True
    balance_complete: bool = True
    fee_complete: bool = True

    def validate(self, binding: RuntimeBinding) -> None:
        if self.sequence < 0:
            raise ValidationSafetyError("snapshot sequence is invalid")
        if not all((
            self.orders_complete, self.trades_complete, self.position_complete,
            self.balance_complete, self.fee_complete,
        )):
            raise ValidationSafetyError("authoritative snapshot is incomplete")
        if self.account_binding != binding.account_binding:
            raise ValidationSafetyError("snapshot account binding mismatch")
        if self.symbol != binding.symbol:
            raise ValidationSafetyError("snapshot symbol mismatch")
        if self.market_fingerprint != binding.market_fingerprint:
            raise ValidationSafetyError("snapshot market fingerprint mismatch")
        if len({row.get("client_order_id", "") for row in self.open_orders}) != len(
            self.open_orders
        ):
            raise ValidationSafetyError("snapshot contains duplicate client order identity")
        sides = [row.get("side", "") for row in self.open_orders]
        if any(side not in {"buy", "sell"} for side in sides):
            raise ValidationSafetyError("snapshot contains unknown order side")
        if sides.count("buy") > 1 or sides.count("sell") > 1:
            raise ValidationSafetyError("snapshot contains duplicate same-side orders")

    @property
    def exact_key(self) -> str:
        return canonical_sha256({
            "account_binding": self.account_binding,
            "symbol": self.symbol,
            "market_fingerprint": self.market_fingerprint,
            "position_btc": str(self.position_btc),
            "average_entry_price": str(self.average_entry_price),
            "run_fees_usdt": str(self.run_fees_usdt),
            "open_orders": list(self.open_orders),
            "completeness": [
                self.orders_complete, self.trades_complete,
                self.position_complete, self.balance_complete,
                self.fee_complete,
            ],
        })


@dataclass
class ValidationKillLatch:
    active: bool = False
    reason: str = ""
    activation_id: str = ""

    def activate(self, run_id: str, process_generation: int) -> str:
        if self.active:
            return self.activation_id
        self.reason = "CONTROLLED_R2_VALIDATION"
        self.activation_id = canonical_sha256({
            "run_id": run_id,
            "process_generation": process_generation,
            "reason": self.reason,
        })[:24]
        self.active = True
        return self.activation_id

    def release(self, acknowledgement_id: str) -> None:
        if not self.active or acknowledgement_id != self.activation_id:
            raise ValidationSafetyError("kill-latch acknowledgement mismatch")
        self.active = False
        self.reason = ""
        self.activation_id = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "reason": self.reason,
            "activation_id": self.activation_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ValidationKillLatch":
        result = cls(
            active=bool(value.get("active", False)),
            reason=str(value.get("reason", "")),
            activation_id=str(value.get("activation_id", "")),
        )
        if result.active and not (result.reason and result.activation_id):
            raise ValidationSafetyError("restored kill latch is incomplete")
        return result


@dataclass
class ValidationRunState:
    binding: RuntimeBinding
    risk_budget: RiskBudget
    phase: RunPhase = RunPhase.RUNNING
    process_generation: int = 0
    owned_orders: dict[str, OwnedOrder] = field(default_factory=dict)
    fill_cursor: FillDedupCursor = field(default_factory=FillDedupCursor)
    ledger: FillLedger = field(default_factory=FillLedger)
    last_reconciled_position_btc: Decimal = Decimal("0")
    pending_position_reconciliation: bool = False
    placement_halted_for_fill: bool = False
    kill_latch: ValidationKillLatch = field(default_factory=ValidationKillLatch)
    checkpoint_kind: CheckpointKind = CheckpointKind.NONE
    checkpoint_id: str = ""
    resume_token_sha256: str = ""
    used_resume_token_sha256: list[str] = field(default_factory=list)
    r1_completed: bool = False
    r2_completed: bool = False
    normal_acknowledgements: int = 0
    external_network_attempts: int = 0
    external_preflight_attempts: int = 0
    external_order_submissions: int = 0
    halt_reason: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def open_orders(self) -> dict[str, OwnedOrder]:
        return {key: row for key, row in self.owned_orders.items() if row.is_open}

    @property
    def can_submit(self) -> bool:
        return (
            self.phase in {
                RunPhase.RUNNING,
                RunPhase.RUNNING_AFTER_R1,
                RunPhase.RUNNING_AFTER_R2,
            }
            and not self.kill_latch.active
            and not self.pending_position_reconciliation
            and not self.placement_halted_for_fill
            and not self.halt_reason
        )

    def validate(self) -> None:
        self.binding.validate()
        self.risk_budget.validate()
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationSafetyError("state schema version mismatch")
        if self.process_generation < 0:
            raise ValidationSafetyError("negative process generation")
        if self.normal_acknowledgements > self.risk_budget.maximum_normal_creates:
            raise ValidationSafetyError("normal-create budget exceeded")
        if any(value != 0 for value in (
            self.external_network_attempts,
            self.external_preflight_attempts,
            self.external_order_submissions,
        )) and self.binding.execution_mode == "OFFLINE_FIXTURE":
            raise ValidationSafetyError("offline state records external activity")
        for order in self.owned_orders.values():
            order.validate()
        sides = [row.side for row in self.open_orders.values()]
        if sides.count("buy") > 1 or sides.count("sell") > 1:
            raise ValidationSafetyError("restored state has duplicate same-side orders")
        if abs(self.ledger.inventory_btc) > self.risk_budget.maximum_inventory_btc:
            raise ValidationSafetyError("restored state exceeds inventory budget")
        if self.phase is RunPhase.HALTED and not self.halt_reason:
            raise ValidationSafetyError("halted state lacks a reason")
        if self.resume_token_sha256:
            _require_sha256(self.resume_token_sha256, "resume_token_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "binding": self.binding.to_dict(),
            "risk_budget": self.risk_budget.to_dict(),
            "phase": self.phase.value,
            "process_generation": self.process_generation,
            "owned_orders": {
                key: row.to_dict() for key, row in sorted(self.owned_orders.items())
            },
            "fill_cursor": self.fill_cursor.to_dict(),
            "ledger": self.ledger.to_dict(),
            "last_reconciled_position_btc": str(
                self.last_reconciled_position_btc
            ),
            "pending_position_reconciliation": self.pending_position_reconciliation,
            "placement_halted_for_fill": self.placement_halted_for_fill,
            "kill_latch": self.kill_latch.to_dict(),
            "checkpoint_kind": self.checkpoint_kind.value,
            "checkpoint_id": self.checkpoint_id,
            "resume_token_sha256": self.resume_token_sha256,
            "used_resume_token_sha256": list(self.used_resume_token_sha256),
            "r1_completed": self.r1_completed,
            "r2_completed": self.r2_completed,
            "normal_acknowledgements": self.normal_acknowledgements,
            "external_network_attempts": self.external_network_attempts,
            "external_preflight_attempts": self.external_preflight_attempts,
            "external_order_submissions": self.external_order_submissions,
            "halt_reason": self.halt_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ValidationRunState":
        try:
            result = cls(
                schema_version=int(value["schema_version"]),
                binding=RuntimeBinding.from_dict(dict(value["binding"])),
                risk_budget=RiskBudget.from_dict(dict(value["risk_budget"])),
                phase=RunPhase(str(value["phase"])),
                process_generation=int(value["process_generation"]),
                owned_orders={
                    str(key): OwnedOrder.from_dict(dict(item))
                    for key, item in dict(value["owned_orders"]).items()
                },
                fill_cursor=FillDedupCursor.from_dict(dict(value["fill_cursor"])),
                ledger=FillLedger.from_dict(dict(value["ledger"])),
                last_reconciled_position_btc=_decimal(
                    value["last_reconciled_position_btc"],
                    "last reconciled position",
                ),
                pending_position_reconciliation=bool(
                    value["pending_position_reconciliation"]
                ),
                placement_halted_for_fill=bool(value["placement_halted_for_fill"]),
                kill_latch=ValidationKillLatch.from_dict(dict(value["kill_latch"])),
                checkpoint_kind=CheckpointKind(str(value["checkpoint_kind"])),
                checkpoint_id=str(value["checkpoint_id"]),
                resume_token_sha256=str(value["resume_token_sha256"]),
                used_resume_token_sha256=[
                    str(row) for row in value["used_resume_token_sha256"]
                ],
                r1_completed=bool(value["r1_completed"]),
                r2_completed=bool(value["r2_completed"]),
                normal_acknowledgements=int(value["normal_acknowledgements"]),
                external_network_attempts=int(value["external_network_attempts"]),
                external_preflight_attempts=int(value["external_preflight_attempts"]),
                external_order_submissions=int(value["external_order_submissions"]),
                halt_reason=str(value["halt_reason"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationSafetyError("runtime state is malformed") from exc
        result.validate()
        return result


class HashChainStateStore:
    """Write-ahead hash-chain plus atomic current-state snapshot."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.journal_path = state_path.with_name(state_path.stem + "_journal.jsonl")

    @staticmethod
    def _record_base(
        *, sequence: int, previous_hash: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "previous_hash": previous_hash,
            "payload_sha256": canonical_sha256(payload),
            "payload": payload,
        }

    def _records(self) -> list[dict[str, object]]:
        if not self.journal_path.exists():
            if self.state_path.exists():
                raise PersistenceFailure("state snapshot exists without journal")
            return []
        try:
            raw = self.journal_path.read_text(encoding="utf-8")
            if not raw or not raw.endswith("\n"):
                raise PersistenceFailure("state journal is empty or truncated")
            records = [json.loads(line) for line in raw.splitlines()]
        except PersistenceFailure:
            raise
        except Exception as exc:
            raise PersistenceFailure("state journal cannot be decoded") from exc
        previous = ""
        for index, record in enumerate(records, start=1):
            if int(record.get("sequence", 0)) != index:
                raise PersistenceFailure("state journal sequence is non-monotonic")
            if str(record.get("previous_hash", "")) != previous:
                raise PersistenceFailure("state journal hash chain is broken")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise PersistenceFailure("state journal payload is malformed")
            if str(record.get("payload_sha256", "")) != canonical_sha256(payload):
                raise PersistenceFailure("state journal payload hash mismatch")
            base = self._record_base(
                sequence=index, previous_hash=previous, payload=payload
            )
            expected = canonical_sha256(base)
            if str(record.get("record_hash", "")) != expected:
                raise PersistenceFailure("state journal record hash mismatch")
            previous = expected
        return records

    def commit(self, state: ValidationRunState) -> str:
        state.validate()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        records = self._records()
        previous = str(records[-1]["record_hash"]) if records else ""
        base = self._record_base(
            sequence=len(records) + 1,
            previous_hash=previous,
            payload=state.to_dict(),
        )
        record = dict(base)
        record["record_hash"] = canonical_sha256(base)
        try:
            with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_snapshot(record)
        except Exception as exc:
            if isinstance(exc, PersistenceFailure):
                raise
            raise PersistenceFailure("state commit failed after write-ahead attempt") from exc
        return str(record["record_hash"])

    def _write_snapshot(self, record: dict[str, object]) -> None:
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def load(
        self,
        *,
        expected_binding: RuntimeBinding | None = None,
        allow_journal_recovery: bool = False,
    ) -> ValidationRunState | None:
        records = self._records()
        if not records:
            return None
        last = records[-1]
        if self.state_path.exists():
            try:
                snapshot = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise PersistenceFailure("state snapshot cannot be decoded") from exc
            if snapshot != last and not allow_journal_recovery:
                raise PersistenceFailure("state snapshot does not match journal head")
        elif not allow_journal_recovery:
            raise PersistenceFailure("state snapshot is missing")
        state = ValidationRunState.from_dict(dict(last["payload"]))
        if expected_binding is not None and state.binding.to_dict() != expected_binding.to_dict():
            raise ValidationSafetyError("restored runtime binding drift")
        return state

    def recover_snapshot_from_journal(self) -> None:
        records = self._records()
        if not records:
            raise PersistenceFailure("no journal record is available for recovery")
        self._write_snapshot(records[-1])


@dataclass(frozen=True)
class RestartDirective:
    exit_signal: str
    checkpoint_id: str
    resume_token: str


class FillRestartEngine:
    """Fail-closed state machine used by offline fixtures and future Demo glue."""

    def __init__(self, store: HashChainStateStore, state: ValidationRunState):
        self.store = store
        self.state = state

    @classmethod
    def create(
        cls,
        *,
        store: HashChainStateStore,
        binding: RuntimeBinding,
        risk_budget: RiskBudget | None = None,
    ) -> "FillRestartEngine":
        if store.state_path.exists() or store.journal_path.exists():
            raise PersistenceFailure("run state already exists; run-ID reuse refused")
        state = ValidationRunState(
            binding=binding,
            risk_budget=risk_budget or RiskBudget(),
        )
        store.commit(state)
        return cls(store, state)

    def _commit_or_halt(self) -> None:
        try:
            self.store.commit(self.state)
        except PersistenceFailure:
            self.state.phase = RunPhase.HALTED
            self.state.halt_reason = "STATE_PERSISTENCE_FAILED"
            try:
                # The journal may still be writable when atomic snapshot replace
                # fails.  Persist the halt as a second write-ahead record so a
                # recovering supervisor never resumes quoting from the accepted
                # but incompletely snapshotted transition.
                self.store.commit(self.state)
            except PersistenceFailure:
                pass
            raise

    def _halt(self, reason: str, exc: Exception | None = None) -> None:
        self.state.phase = RunPhase.HALTED
        self.state.halt_reason = reason
        try:
            self.store.commit(self.state)
        except PersistenceFailure:
            pass
        if exc is None:
            raise ValidationSafetyError(reason)
        raise ValidationSafetyError(reason) from exc

    def record_owned_order_ack(self, order: OwnedOrder) -> None:
        """Record an acknowledgement; this method itself submits no order."""
        try:
            order.validate()
            if not self.state.can_submit:
                raise ValidationSafetyError("new order is blocked by runtime state")
            if order.client_order_id in self.state.owned_orders:
                raise ValidationSafetyError("duplicate client order identity")
            if any(row.order_id == order.order_id for row in self.state.owned_orders.values()):
                raise ValidationSafetyError("duplicate exchange order identity")
            if any(row.side == order.side for row in self.state.open_orders.values()):
                raise ValidationSafetyError("duplicate same-side owned order")
            if not order.reduce_only:
                self.state.normal_acknowledgements += 1
                if self.state.normal_acknowledgements > (
                    self.state.risk_budget.maximum_normal_creates
                ):
                    raise ValidationSafetyError("normal-create budget exhausted")
            self.state.owned_orders[order.client_order_id] = order
            self._commit_or_halt()
        except (ValidationSafetyError, PersistenceFailure) as exc:
            if isinstance(exc, PersistenceFailure):
                raise
            self._halt("ORDER_ACK_REJECTED", exc)

    def record_special_order_ack(self, order: OwnedOrder) -> None:
        """Persist one emergency reduce-only order while normal placement is halted."""
        try:
            order.validate()
            if not order.reduce_only or order.post_only_acknowledged:
                raise ValidationSafetyError("special order semantics are invalid")
            if self.state.ledger.inventory_btc == 0:
                raise ValidationSafetyError("special order requires nonzero inventory")
            if order.quantity_btc > abs(self.state.ledger.inventory_btc):
                raise ValidationSafetyError("special order exceeds open inventory")
            if order.client_order_id in self.state.owned_orders:
                raise ValidationSafetyError("duplicate special client identity")
            if any(row.order_id == order.order_id for row in self.state.owned_orders.values()):
                raise ValidationSafetyError("duplicate special exchange identity")
            if any(row.is_open for row in self.state.owned_orders.values()):
                raise ValidationSafetyError("special order requires zero other open orders")
            if any(row.reduce_only for row in self.state.owned_orders.values()):
                raise ValidationSafetyError("special order single-flight already consumed")
            self.state.owned_orders[order.client_order_id] = order
            self._commit_or_halt()
        except (ValidationSafetyError, PersistenceFailure) as exc:
            if isinstance(exc, PersistenceFailure):
                raise
            self._halt("SPECIAL_ORDER_ACK_REJECTED", exc)

    def ingest_fill_pages(self, pages: Sequence[dict[str, object]]) -> int:
        working = ValidationRunState.from_dict(self.state.to_dict())
        try:
            fills = working.fill_cursor.select_new_pages(pages)
            applied = 0
            for fill in fills:
                order = working.owned_orders.get(fill.client_order_id)
                if order is None:
                    raise ValidationSafetyError("unknown or unowned fill")
                if working.ledger.apply(fill, order):
                    applied += 1
                    if not order.reduce_only:
                        working.placement_halted_for_fill = True
            if working.ledger.inventory_btc != working.last_reconciled_position_btc:
                working.pending_position_reconciliation = True
            self.store.commit(working)
            self.state = working
            return applied
        except Exception as exc:
            self._halt("FILL_RECONCILIATION_FAILED", exc)
        return 0

    def _validate_snapshot_orders(
        self, state: ValidationRunState, snapshot: AuthoritativeSnapshot
    ) -> None:
        snapshot.validate(state.binding)
        by_client = {
            str(row.get("client_order_id", "")): row for row in snapshot.open_orders
        }
        foreign = set(by_client) - set(state.owned_orders)
        if foreign:
            raise ValidationSafetyError("foreign or unowned order in snapshot")
        expected = set(state.open_orders)
        if set(by_client) != expected:
            raise ValidationSafetyError("owned open-order snapshot does not reconcile")
        for client_id, row in by_client.items():
            owned = state.owned_orders[client_id]
            if str(row.get("order_id", "")) != owned.order_id:
                raise ValidationSafetyError("owned order identity changed")
            if str(row.get("side", "")) != owned.side:
                raise ValidationSafetyError("owned order side changed")

    def record_authoritative_snapshot(
        self,
        snapshot: AuthoritativeSnapshot,
        *,
        allow_position_lag: bool = False,
    ) -> bool:
        working = ValidationRunState.from_dict(self.state.to_dict())
        try:
            self._validate_snapshot_orders(working, snapshot)
            if snapshot.run_fees_usdt != working.ledger.total_fees_usdt:
                raise ValidationSafetyError("authoritative fee total mismatch")
            expected_position = working.ledger.inventory_btc
            if snapshot.position_btc == expected_position:
                if expected_position == 0:
                    if snapshot.average_entry_price != 0:
                        raise ValidationSafetyError("flat snapshot has nonzero entry price")
                    working.ledger.average_entry_price = Decimal("0")
                elif snapshot.average_entry_price != working.ledger.average_entry_price:
                    raise ValidationSafetyError("authoritative average entry mismatch")
                working.last_reconciled_position_btc = expected_position
                working.pending_position_reconciliation = False
                self.store.commit(working)
                self.state = working
                return True
            if (
                allow_position_lag
                and working.pending_position_reconciliation
                and snapshot.position_btc == working.last_reconciled_position_btc
            ):
                self.store.commit(working)
                self.state = working
                return False
            raise ValidationSafetyError("authoritative position does not reconcile")
        except Exception as exc:
            self._halt("AUTHORITATIVE_SNAPSHOT_FAILED", exc)
        return False

    def observe_position_before_trade(
        self, snapshot: AuthoritativeSnapshot
    ) -> None:
        """Persist a bounded position-leading-trade observation and block quotes.

        OKX position and trade feeds can arrive in either order.  A position lead
        is not treated as reconciled or fresh evidence: it is accepted only when
        one owned order can explain the entire bounded delta, the trade snapshot
        is explicitly marked incomplete, and every other snapshot component is
        authoritative.  Quoting remains blocked until the fill and a complete
        snapshot catch up.
        """
        working = ValidationRunState.from_dict(self.state.to_dict())
        try:
            if snapshot.trades_complete:
                raise ValidationSafetyError("position-lead observation must mark trades incomplete")
            if not all((
                snapshot.orders_complete, snapshot.position_complete,
                snapshot.balance_complete, snapshot.fee_complete,
            )):
                raise ValidationSafetyError("position-lead snapshot is otherwise incomplete")
            if (
                snapshot.account_binding != working.binding.account_binding
                or snapshot.symbol != working.binding.symbol
                or snapshot.market_fingerprint != working.binding.market_fingerprint
            ):
                raise ValidationSafetyError("position-lead identity mismatch")
            if snapshot.run_fees_usdt != working.ledger.total_fees_usdt:
                raise ValidationSafetyError("position-lead fee snapshot mismatch")
            live_ids = {
                str(row.get("client_order_id", "")) for row in snapshot.open_orders
            }
            if live_ids - set(working.owned_orders):
                raise ValidationSafetyError("position-lead snapshot contains foreign order")
            delta = snapshot.position_btc - working.ledger.inventory_btc
            candidates = [
                row for row in working.open_orders.values()
                if (delta > 0 and row.side == "buy")
                or (delta < 0 and row.side == "sell")
            ]
            if (
                delta == 0
                or abs(snapshot.position_btc) > working.risk_budget.maximum_inventory_btc
                or len(candidates) != 1
                or abs(delta) > candidates[0].remaining_btc
            ):
                raise ValidationSafetyError("position lead is not explained by one owned order")
            working.pending_position_reconciliation = True
            working.placement_halted_for_fill = True
            self.store.commit(working)
            self.state = working
        except Exception as exc:
            self._halt("POSITION_LEAD_OBSERVATION_FAILED", exc)

    def confirm_cancellations(
        self,
        *,
        confirmed_client_ids: Iterable[str],
        snapshot: AuthoritativeSnapshot,
    ) -> None:
        working = ValidationRunState.from_dict(self.state.to_dict())
        try:
            snapshot.validate(working.binding)
            live = {
                str(row.get("client_order_id", "")) for row in snapshot.open_orders
            }
            confirmed = set(confirmed_client_ids)
            if confirmed & live:
                raise ValidationSafetyError("cancelled order remains authoritative-open")
            for client_id in confirmed:
                order = working.owned_orders.get(client_id)
                if order is None:
                    raise ValidationSafetyError("cancel confirmation is unowned")
                if order.status == "ACKNOWLEDGED":
                    order.status = "CANCEL_CONFIRMED"
            self._validate_snapshot_orders(working, snapshot)
            if snapshot.position_btc != working.ledger.inventory_btc:
                raise ValidationSafetyError("cancel snapshot position mismatch")
            if snapshot.run_fees_usdt != working.ledger.total_fees_usdt:
                raise ValidationSafetyError("cancel snapshot fee mismatch")
            expected_average = (
                working.ledger.average_entry_price
                if working.ledger.inventory_btc != 0 else Decimal("0")
            )
            if snapshot.average_entry_price != expected_average:
                raise ValidationSafetyError("cancel snapshot average entry mismatch")
            working.last_reconciled_position_btc = snapshot.position_btc
            working.pending_position_reconciliation = False
            self.store.commit(working)
            self.state = working
        except Exception as exc:
            self._halt("CANCEL_RECONCILIATION_FAILED", exc)

    def _new_checkpoint(self, kind: CheckpointKind) -> RestartDirective:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        checkpoint_id = canonical_sha256({
            "run_id": self.state.binding.run_id,
            "kind": kind.value,
            "process_generation": self.state.process_generation,
            "state": self.state.to_dict(),
        })[:24]
        self.state.checkpoint_kind = kind
        self.state.checkpoint_id = checkpoint_id
        self.state.resume_token_sha256 = token_hash
        self.state.phase = (
            RunPhase.RESTART_REQUIRED_AFTER_FILL
            if kind is CheckpointKind.R1_AFTER_FILL
            else RunPhase.RESTART_REQUIRED_AFTER_KILL_LATCH
        )
        self._commit_or_halt()
        return RestartDirective(self.state.phase.value, checkpoint_id, token)

    def validate_checkpoint_after_actual_fill(self) -> None:
        if self.state.r1_completed:
            raise ValidationSafetyError("R1 checkpoint already completed")
        if self.state.ledger.normal_fill_count < 1:
            raise ValidationSafetyError("R1 requires an actual normal maker fill")
        if self.state.ledger.inventory_btc == 0:
            raise ValidationSafetyError("R1 requires a nonzero position")
        if self.state.open_orders:
            raise ValidationSafetyError("R1 requires zero owned open orders")
        if self.state.pending_position_reconciliation:
            raise ValidationSafetyError("R1 position is not reconciled")
        if self.state.last_reconciled_position_btc != self.state.ledger.inventory_btc:
            raise ValidationSafetyError("R1 authoritative position mismatch")

    def checkpoint_after_actual_fill(self) -> RestartDirective:
        self.validate_checkpoint_after_actual_fill()
        return self._new_checkpoint(CheckpointKind.R1_AFTER_FILL)

    @staticmethod
    def _validate_restart_snapshots(
        state: ValidationRunState,
        snapshots: Sequence[AuthoritativeSnapshot],
        expected_position: Decimal,
    ) -> None:
        if len(snapshots) != 2:
            raise ValidationSafetyError("restart requires exactly two snapshots")
        for snapshot in snapshots:
            snapshot.validate(state.binding)
            if snapshot.open_orders:
                raise ValidationSafetyError("restart snapshot contains open orders")
            if snapshot.position_btc != expected_position:
                raise ValidationSafetyError("restart snapshot position mismatch")
            if snapshot.run_fees_usdt != state.ledger.total_fees_usdt:
                raise ValidationSafetyError("restart snapshot fee mismatch")
            expected_average = (
                state.ledger.average_entry_price if expected_position != 0 else Decimal("0")
            )
            if snapshot.average_entry_price != expected_average:
                raise ValidationSafetyError("restart snapshot average entry mismatch")
        if snapshots[0].exact_key != snapshots[1].exact_key:
            raise ValidationSafetyError("restart snapshots are not exact and consistent")

    @classmethod
    def resume(
        cls,
        *,
        store: HashChainStateStore,
        expected_binding: RuntimeBinding,
        resume_token: str,
        market_metadata_loaded: bool,
        snapshots: Sequence[AuthoritativeSnapshot],
    ) -> "FillRestartEngine":
        if not market_metadata_loaded:
            raise ValidationSafetyError("market metadata must load before restored state")
        state = store.load(expected_binding=expected_binding)
        if state is None:
            raise ValidationSafetyError("restart state is missing")
        token_hash = hashlib.sha256(resume_token.encode("utf-8")).hexdigest()
        if token_hash in state.used_resume_token_sha256:
            raise ValidationSafetyError("resume token was already used")
        if not state.resume_token_sha256 or token_hash != state.resume_token_sha256:
            raise ValidationSafetyError("resume token does not match checkpoint")
        if state.phase is RunPhase.RESTART_REQUIRED_AFTER_FILL:
            cls._validate_restart_snapshots(
                state, snapshots, state.ledger.inventory_btc
            )
            state.phase = RunPhase.RUNNING_AFTER_R1
            state.r1_completed = True
            state.placement_halted_for_fill = False
        elif state.phase is RunPhase.RESTART_REQUIRED_AFTER_KILL_LATCH:
            cls._validate_restart_snapshots(state, snapshots, Decimal("0"))
            if not state.kill_latch.active:
                raise ValidationSafetyError("R2 kill latch was not persisted")
            state.phase = RunPhase.KILL_LATCH_BLOCKED
        else:
            raise ValidationSafetyError("state is not at a restart checkpoint")
        state.process_generation += 1
        state.used_resume_token_sha256.append(token_hash)
        state.resume_token_sha256 = ""
        store.commit(state)
        return cls(store, state)

    def checkpoint_kill_latch(self) -> RestartDirective:
        if not self.state.r1_completed:
            raise ValidationSafetyError("R2 requires completed R1")
        if self.state.r2_completed:
            raise ValidationSafetyError("R2 checkpoint already completed")
        if len(self.state.ledger.normal_round_trips) < 1:
            raise ValidationSafetyError("R2 requires a normal FIFO maker round trip")
        if self.state.ledger.inventory_btc != 0 or self.state.open_orders:
            raise ValidationSafetyError("R2 requires flat position and zero orders")
        if self.state.pending_position_reconciliation:
            raise ValidationSafetyError("R2 position is not reconciled")
        self.state.kill_latch.activate(
            self.state.binding.run_id, self.state.process_generation
        )
        return self._new_checkpoint(CheckpointKind.R2_KILL_LATCH)

    def release_kill_latch(
        self,
        *,
        acknowledgement_id: str,
        snapshots: Sequence[AuthoritativeSnapshot],
    ) -> None:
        if self.state.phase is not RunPhase.KILL_LATCH_BLOCKED:
            raise ValidationSafetyError("R2 release is not at blocked checkpoint")
        self._validate_restart_snapshots(self.state, snapshots, Decimal("0"))
        self.state.kill_latch.release(acknowledgement_id)
        self.state.r2_completed = True
        self.state.placement_halted_for_fill = False
        self.state.phase = RunPhase.RUNNING_AFTER_R2
        self.state.checkpoint_kind = CheckpointKind.NONE
        self.state.checkpoint_id = ""
        self._commit_or_halt()


def make_fixture_binding(run_id: str = "offline-fixture") -> RuntimeBinding:
    """Return an exact, non-secret binding for deterministic offline fixtures."""
    filler = "a" * 64
    binding = RuntimeBinding(
        run_id=run_id,
        execution_mode="OFFLINE_FIXTURE",
        account_binding="offline-account-binding",
        market_fingerprint="offline-market-fingerprint",
        profile_binding_sha256=filler,
        runtime_configuration_sha256="b" * 64,
        source_manifest_sha256="c" * 64,
    )
    binding.validate()
    return binding


def make_snapshot(
    binding: RuntimeBinding,
    *,
    sequence: int,
    position_btc: object,
    average_entry_price: object = "0",
    run_fees_usdt: object = "0",
    open_orders: Iterable[dict[str, str]] = (),
    **completeness: bool,
) -> AuthoritativeSnapshot:
    return AuthoritativeSnapshot(
        sequence=sequence,
        account_binding=binding.account_binding,
        symbol=binding.symbol,
        market_fingerprint=binding.market_fingerprint,
        position_btc=_decimal(position_btc, "snapshot position"),
        average_entry_price=_decimal(
            average_entry_price, "snapshot average entry"
        ),
        run_fees_usdt=_decimal(run_fees_usdt, "snapshot fees"),
        open_orders=tuple(dict(row) for row in open_orders),
        orders_complete=completeness.get("orders_complete", True),
        trades_complete=completeness.get("trades_complete", True),
        position_complete=completeness.get("position_complete", True),
        balance_complete=completeness.get("balance_complete", True),
        fee_complete=completeness.get("fee_complete", True),
    )
