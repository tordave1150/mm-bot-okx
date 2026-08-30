"""Deterministic orchestration contract for one future OKX Demo formal run.

The controller is intentionally transport-free: it consumes authoritative
observations and frozen quote-engine output, then emits explicit actions for a
separately armed driver.  Importing or exercising this module cannot perform
network I/O or submit an order.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

from okx_fill_restart_validation import (
    MAXIMUM_INVENTORY_BTC,
    PRIOR_DEMO_COMPLETED_SHA256,
    PRIOR_DEMO_SPECIFICATION_SHA256,
    PROFILE_ID,
    PROFILE_NAME,
    SYMBOL,
    V16_SPECIFICATION_SHA256,
    canonical_json,
    canonical_sha256,
)


FORMAL_SCHEMA_VERSION = 1
FORMAL_PROTOCOL_ID = "okx-demo-fill-restart-formal-v1"


class FormalSafetyError(RuntimeError):
    """A formal orchestration invariant is incomplete or violated."""


class FormalPersistenceError(FormalSafetyError):
    """Formal controller state could not be persisted or verified."""


class FormalStage(str, Enum):
    NOT_ARMED = "NOT_ARMED"
    SEEK_R1_FILL = "SEEK_R1_FILL"
    STOP_AFTER_R1_FILL = "STOP_AFTER_R1_FILL"
    RESTART_REQUIRED_R1 = "RESTART_REQUIRED_R1"
    SEEK_ROUND_TRIP = "SEEK_ROUND_TRIP"
    STOP_AFTER_OFFSET_FILL = "STOP_AFTER_OFFSET_FILL"
    STOP_AFTER_ROUND_TRIP = "STOP_AFTER_ROUND_TRIP"
    RESTART_REQUIRED_R2 = "RESTART_REQUIRED_R2"
    KILL_LATCH_BLOCKED = "KILL_LATCH_BLOCKED"
    FINAL_RECONCILIATION = "FINAL_RECONCILIATION"
    COMPLETE = "COMPLETE"
    HALTED = "HALTED"


class FormalActionType(str, Enum):
    PLACE_POST_ONLY = "PLACE_POST_ONLY"
    RESOLVE_AMBIGUOUS_CREATE = "RESOLVE_AMBIGUOUS_CREATE"
    CANCEL_ALL_OWNED = "CANCEL_ALL_OWNED"
    PERSIST_R1_CHECKPOINT = "PERSIST_R1_CHECKPOINT"
    EXIT_RESTART_R1 = "EXIT_RESTART_R1"
    ACTIVATE_VALIDATION_KILL = "ACTIVATE_VALIDATION_KILL"
    PERSIST_R2_CHECKPOINT = "PERSIST_R2_CHECKPOINT"
    EXIT_RESTART_R2 = "EXIT_RESTART_R2"
    RELEASE_VALIDATION_KILL = "RELEASE_VALIDATION_KILL"
    FLATTEN_REDUCE_ONLY = "FLATTEN_REDUCE_ONLY"
    FINALIZE = "FINALIZE"
    HALT = "HALT"


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FormalSafetyError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise FormalSafetyError(f"{name} is not finite")
    return result


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise FormalSafetyError(f"{name} is not a lowercase SHA-256")


@dataclass(frozen=True)
class FormalRunSpecification:
    formal_run_id: str
    package_id: str
    package_specification_sha256: str
    source_manifest_sha256: str
    runtime_configuration_sha256: str
    profile_binding_sha256: str
    market_fingerprint: str
    offline_completion_sha256: str
    preflight_completion_sha256: str
    preflight_decision_sha256: str
    ccxt_source_sha256: str
    symbol: str = SYMBOL
    profile_id: str = PROFILE_ID
    profile_name: str = PROFILE_NAME
    v16_specification_sha256: str = V16_SPECIFICATION_SHA256
    prior_demo_specification_sha256: str = PRIOR_DEMO_SPECIFICATION_SHA256
    prior_demo_completed_sha256: str = PRIOR_DEMO_COMPLETED_SHA256
    execution_mode: str = "OKX_DEMO"
    live_mode_available: bool = False
    capital_usdt: Decimal = Decimal("750")
    leverage: int = 3
    maximum_inventory_btc: Decimal = Decimal("0.01")
    soft_guard_usdt: Decimal = Decimal("22.50")
    hard_kill_usdt: Decimal = Decimal("37.50")
    maximum_wall_minutes: int = 120
    maximum_normal_creates: int = 120
    maximum_owned_bid: int = 1
    maximum_owned_ask: int = 1
    minimum_create_interval_ms: int = 2_000
    observation_interval_ms: int = 350
    maximum_market_age_ms: int = 1_000
    maximum_clock_skew_ms: int = 1_500
    maximum_flatten_submissions: int = 1

    def validate(self) -> None:
        if not self.formal_run_id.startswith("formal-"):
            raise FormalSafetyError("formal run ID is invalid")
        if not self.package_id.startswith("formal-package-"):
            raise FormalSafetyError("formal package ID is invalid")
        for name in (
            "package_specification_sha256", "source_manifest_sha256",
            "runtime_configuration_sha256", "profile_binding_sha256",
            "offline_completion_sha256", "preflight_completion_sha256",
            "preflight_decision_sha256", "ccxt_source_sha256",
        ):
            _sha(str(getattr(self, name)), name)
        if self.symbol != SYMBOL or self.execution_mode != "OKX_DEMO":
            raise FormalSafetyError("formal exchange identity drift")
        if self.live_mode_available:
            raise FormalSafetyError("LIVE mode must remain unavailable")
        if self.profile_id != PROFILE_ID or self.profile_name != PROFILE_NAME:
            raise FormalSafetyError("formal profile identity drift")
        if self.v16_specification_sha256 != V16_SPECIFICATION_SHA256:
            raise FormalSafetyError("v1.6 specification drift")
        if self.prior_demo_specification_sha256 != PRIOR_DEMO_SPECIFICATION_SHA256:
            raise FormalSafetyError("prior Demo specification drift")
        if self.prior_demo_completed_sha256 != PRIOR_DEMO_COMPLETED_SHA256:
            raise FormalSafetyError("prior Demo completion drift")
        if self.capital_usdt != Decimal("750") or self.leverage != 3:
            raise FormalSafetyError("capital or leverage drift")
        if self.maximum_inventory_btc > MAXIMUM_INVENTORY_BTC:
            raise FormalSafetyError("inventory cap is wider than protocol")
        if self.soft_guard_usdt > Decimal("22.50"):
            raise FormalSafetyError("soft guard is wider than protocol")
        if self.hard_kill_usdt > Decimal("37.50"):
            raise FormalSafetyError("hard kill is wider than protocol")
        if not 1 <= self.maximum_wall_minutes <= 120:
            raise FormalSafetyError("wall-time budget is invalid")
        if not 1 <= self.maximum_normal_creates <= 120:
            raise FormalSafetyError("normal-create budget is invalid")
        if self.maximum_owned_bid != 1 or self.maximum_owned_ask != 1:
            raise FormalSafetyError("owned-side cap drift")
        if self.minimum_create_interval_ms < 2_000:
            raise FormalSafetyError("create-rate ceiling is too loose")
        if self.observation_interval_ms < 350:
            raise FormalSafetyError("observation interval is too aggressive")
        if (
            type(self.maximum_market_age_ms) is not int
            or not 0 < self.maximum_market_age_ms <= 1_000
        ):
            raise FormalSafetyError("market staleness limit is too loose")
        if (
            type(self.maximum_clock_skew_ms) is not int
            or not 0 < self.maximum_clock_skew_ms <= 1_500
        ):
            raise FormalSafetyError("clock-skew limit is too loose")
        if self.maximum_flatten_submissions != 1:
            raise FormalSafetyError("flatten must remain single-flight")

    @property
    def session_id(self) -> str:
        return (
            f"formal:{self.formal_run_id}:p0:"
            f"{self.package_specification_sha256[:12]}"
        )

    @property
    def expected_arm_token(self) -> str:
        return f"OKX_DEMO:{self.session_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "formal_run_id": self.formal_run_id,
            "package_id": self.package_id,
            "package_specification_sha256": self.package_specification_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "runtime_configuration_sha256": self.runtime_configuration_sha256,
            "profile_binding_sha256": self.profile_binding_sha256,
            "market_fingerprint": self.market_fingerprint,
            "offline_completion_sha256": self.offline_completion_sha256,
            "preflight_completion_sha256": self.preflight_completion_sha256,
            "preflight_decision_sha256": self.preflight_decision_sha256,
            "ccxt_source_sha256": self.ccxt_source_sha256,
            "symbol": self.symbol,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "v16_specification_sha256": self.v16_specification_sha256,
            "prior_demo_specification_sha256": self.prior_demo_specification_sha256,
            "prior_demo_completed_sha256": self.prior_demo_completed_sha256,
            "execution_mode": self.execution_mode,
            "live_mode_available": self.live_mode_available,
            "capital_usdt": str(self.capital_usdt),
            "leverage": self.leverage,
            "maximum_inventory_btc": str(self.maximum_inventory_btc),
            "soft_guard_usdt": str(self.soft_guard_usdt),
            "hard_kill_usdt": str(self.hard_kill_usdt),
            "maximum_wall_minutes": self.maximum_wall_minutes,
            "maximum_normal_creates": self.maximum_normal_creates,
            "maximum_owned_bid": self.maximum_owned_bid,
            "maximum_owned_ask": self.maximum_owned_ask,
            "minimum_create_interval_ms": self.minimum_create_interval_ms,
            "observation_interval_ms": self.observation_interval_ms,
            "maximum_market_age_ms": self.maximum_market_age_ms,
            "maximum_clock_skew_ms": self.maximum_clock_skew_ms,
            "maximum_flatten_submissions": self.maximum_flatten_submissions,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FormalRunSpecification":
        data = dict(value)
        for name in (
            "capital_usdt", "maximum_inventory_btc", "soft_guard_usdt",
            "hard_kill_usdt",
        ):
            data[name] = _decimal(data[name], name)
        result = cls(**data)
        result.validate()
        return result


@dataclass(frozen=True)
class FormalObservation:
    sequence: int
    observed_at_ms: int
    environment: str
    sandbox_mode: bool
    simulated_trading_header: bool
    account_binding: str
    market_fingerprint: str
    symbol: str
    position_mode: str
    leverage: Decimal
    position_btc: Decimal
    open_owned_orders: tuple[tuple[str, str], ...]
    foreign_order_count: int
    normal_bid_fills_total: int
    normal_ask_fills_total: int
    normal_fifo_round_trips_total: int
    fill_cursor_timestamp_ms: int
    fill_cursor_trade_id: str
    fill_deduplication_sha256: str
    average_entry_usdt: Decimal
    gross_realized_pnl_usdt: Decimal
    actual_fees_usdt: Decimal
    net_realized_pnl_usdt: Decimal
    equity_usdt: Decimal
    peak_equity_usdt: Decimal
    available_equity_usdt: Decimal
    maintenance_margin_usdt: Decimal
    clock_skew_ms: int
    market_timestamp_ms: int
    market_age_ms: int
    best_bid: Decimal
    best_ask: Decimal
    orders_complete: bool = True
    trades_complete: bool = True
    position_complete: bool = True
    balance_complete: bool = True
    fees_complete: bool = True

    @property
    def open_order_map(self) -> dict[str, str]:
        return dict(self.open_owned_orders)

    @property
    def drawdown_usdt(self) -> Decimal:
        return max(Decimal("0"), self.peak_equity_usdt - self.equity_usdt)

    @property
    def consistency_key(self) -> str:
        return canonical_sha256({
            "environment": self.environment,
            "sandbox_mode": self.sandbox_mode,
            "simulated_trading_header": self.simulated_trading_header,
            "account_binding": self.account_binding,
            "market_fingerprint": self.market_fingerprint,
            "symbol": self.symbol,
            "position_mode": self.position_mode,
            "leverage": str(self.leverage),
            "position_btc": str(self.position_btc),
            "open_owned_orders": list(self.open_owned_orders),
            "foreign_order_count": self.foreign_order_count,
            "normal_bid_fills_total": self.normal_bid_fills_total,
            "normal_ask_fills_total": self.normal_ask_fills_total,
            "normal_fifo_round_trips_total": self.normal_fifo_round_trips_total,
            "fill_cursor_timestamp_ms": self.fill_cursor_timestamp_ms,
            "fill_cursor_trade_id": self.fill_cursor_trade_id,
            "fill_deduplication_sha256": self.fill_deduplication_sha256,
            "average_entry_usdt": str(self.average_entry_usdt),
            "gross_realized_pnl_usdt": str(self.gross_realized_pnl_usdt),
            "actual_fees_usdt": str(self.actual_fees_usdt),
            "net_realized_pnl_usdt": str(self.net_realized_pnl_usdt),
            "completeness": [
                self.orders_complete, self.trades_complete,
                self.position_complete, self.balance_complete,
                self.fees_complete,
            ],
        })

    def safety_failure(self, spec: FormalRunSpecification) -> str:
        if not all((
            self.orders_complete, self.trades_complete, self.position_complete,
            self.balance_complete, self.fees_complete,
        )):
            return "AUTHORITATIVE_SNAPSHOT_INCOMPLETE"
        if self.environment != "OKX_DEMO":
            return "ENVIRONMENT_NOT_DEMO"
        if not self.sandbox_mode or not self.simulated_trading_header:
            return "DEMO_TRANSPORT_UNPROVEN"
        if not self.account_binding or self.market_fingerprint != spec.market_fingerprint:
            return "ACCOUNT_OR_MARKET_BINDING_MISMATCH"
        if self.symbol != spec.symbol:
            return "SYMBOL_MISMATCH"
        if self.position_mode != "net_mode" or self.leverage != Decimal("3"):
            return "ACCOUNT_MODE_OR_LEVERAGE_MISMATCH"
        if self.foreign_order_count:
            return "FOREIGN_OR_UNOWNED_ORDER"
        sides = list(self.open_order_map.values())
        if sides.count("buy") > 1 or sides.count("sell") > 1:
            return "DUPLICATE_SAME_SIDE_ORDER"
        if abs(self.position_btc) > spec.maximum_inventory_btc:
            return "INVENTORY_LIMIT_EXCEEDED"
        accounting_values = (
            self.position_btc, self.average_entry_usdt,
            self.gross_realized_pnl_usdt, self.actual_fees_usdt,
            self.net_realized_pnl_usdt, self.equity_usdt,
            self.peak_equity_usdt, self.available_equity_usdt,
            self.maintenance_margin_usdt, self.best_bid, self.best_ask,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in accounting_values):
            return "NONFINITE_ACCOUNTING_OR_MARKET_VALUE"
        if self.actual_fees_usdt < 0:
            return "FEE_CONVENTION_MISMATCH"
        if self.net_realized_pnl_usdt != self.gross_realized_pnl_usdt - self.actual_fees_usdt:
            return "NET_ACCOUNTING_MISMATCH"
        if self.position_btc == 0 and self.average_entry_usdt != 0:
            return "FLAT_AVERAGE_ENTRY_MISMATCH"
        if self.position_btc != 0 and self.average_entry_usdt <= 0:
            return "OPEN_POSITION_ENTRY_MISSING"
        total_fills = self.normal_bid_fills_total + self.normal_ask_fills_total
        if self.fill_cursor_timestamp_ms < 0:
            return "FILL_CURSOR_INVALID"
        if total_fills and (not self.fill_cursor_trade_id or self.fill_cursor_timestamp_ms <= 0):
            return "FILL_CURSOR_MISSING"
        if not total_fills and (self.fill_cursor_trade_id or self.fill_cursor_timestamp_ms):
            return "FILL_CURSOR_WITHOUT_FILL"
        try:
            _sha(self.fill_deduplication_sha256, "fill deduplication hash")
        except FormalSafetyError:
            return "FILL_DEDUPLICATION_BINDING_INVALID"
        if (
            type(self.clock_skew_ms) is not int
            or self.clock_skew_ms < 0
            or self.clock_skew_ms > spec.maximum_clock_skew_ms
        ):
            return "CLOCK_SKEW_UNSAFE"
        if (
            type(self.market_age_ms) is not int
            or self.market_age_ms < -spec.maximum_clock_skew_ms
            or self.market_age_ms > spec.maximum_market_age_ms
        ):
            return "MARKET_DATA_STALE"
        if self.market_timestamp_ms <= 0 or self.best_bid <= 0 or self.best_ask <= self.best_bid:
            return "MARKET_DATA_INVALID"
        if self.equity_usdt <= self.maintenance_margin_usdt:
            return "MAINTENANCE_MARGIN_UNSAFE"
        if self.available_equity_usdt < 0:
            return "AVAILABLE_EQUITY_UNKNOWN"
        if self.drawdown_usdt >= spec.hard_kill_usdt:
            return "HARD_KILL_DRAWDOWN"
        if self.drawdown_usdt >= spec.soft_guard_usdt:
            return "SOFT_GUARD_DRAWDOWN"
        return ""


@dataclass(frozen=True)
class FrozenQuotePlan:
    profile_binding_sha256: str
    source_manifest_sha256: str
    runtime_configuration_sha256: str
    market_timestamp_ms: int
    best_bid: Decimal
    best_ask: Decimal
    bid_price: Decimal
    ask_price: Decimal
    quantity_btc: Decimal
    quote_allowed: bool
    bid_suppressed: bool = False
    ask_suppressed: bool = False

    def validate(self, spec: FormalRunSpecification, observation: FormalObservation) -> None:
        if self.profile_binding_sha256 != spec.profile_binding_sha256:
            raise FormalSafetyError("quote profile binding drift")
        if self.source_manifest_sha256 != spec.source_manifest_sha256:
            raise FormalSafetyError("quote source binding drift")
        if self.runtime_configuration_sha256 != spec.runtime_configuration_sha256:
            raise FormalSafetyError("quote runtime configuration drift")
        if not self.quote_allowed:
            raise FormalSafetyError("frozen quote engine did not allow quoting")
        if self.market_timestamp_ms != observation.market_timestamp_ms:
            raise FormalSafetyError("quote market timestamp mismatch")
        if self.best_bid != observation.best_bid or self.best_ask != observation.best_ask:
            raise FormalSafetyError("quote market book mismatch")
        if self.quantity_btc != Decimal("0.01"):
            raise FormalSafetyError("quote quantity drift")
        if self.bid_price >= observation.best_ask:
            raise FormalSafetyError("bid would cross the authoritative ask")
        if self.ask_price <= observation.best_bid:
            raise FormalSafetyError("ask would cross the authoritative bid")


@dataclass(frozen=True)
class FormalAction:
    action: FormalActionType
    payload: dict[str, object] = field(default_factory=dict)


@dataclass
class FormalControllerState:
    formal_run_id: str
    package_specification_sha256: str
    source_manifest_sha256: str
    stage: FormalStage = FormalStage.NOT_ARMED
    process_generation: int = 0
    started_at_ms: int = 0
    deadline_at_ms: int = 0
    last_create_at_ms: int = 0
    normal_create_count: int = 0
    client_order_generation: int = 0
    pending_intents: dict[str, dict[str, object]] = field(default_factory=dict)
    owned_orders: dict[str, str] = field(default_factory=dict)
    account_binding: str = ""
    position_btc: Decimal = Decimal("0")
    average_entry_usdt: Decimal = Decimal("0")
    gross_realized_pnl_usdt: Decimal = Decimal("0")
    normal_bid_fills_total: int = 0
    normal_ask_fills_total: int = 0
    normal_fifo_round_trips_total: int = 0
    fill_cursor_timestamp_ms: int = 0
    fill_cursor_trade_id: str = ""
    fill_deduplication_sha256: str = "0" * 64
    actual_fees_usdt: Decimal = Decimal("0")
    net_realized_pnl_usdt: Decimal = Decimal("0")
    equity_usdt: Decimal = Decimal("0")
    peak_equity_usdt: Decimal = Decimal("0")
    last_observation_sequence: int = 0
    last_observed_at_ms: int = 0
    last_market_timestamp_ms: int = 0
    r1_completed: bool = False
    r2_completed: bool = False
    validation_kill_active: bool = False
    validation_kill_id: str = ""
    checkpoint_id: str = ""
    flatten_submission_count: int = 0
    flatten_unknown: bool = False
    halted_reason: str = ""
    schema_version: int = FORMAL_SCHEMA_VERSION

    @property
    def allow_quote_planning(self) -> bool:
        return (
            self.stage in {FormalStage.SEEK_R1_FILL, FormalStage.SEEK_ROUND_TRIP}
            and not self.validation_kill_active
            and not self.flatten_unknown
            and not self.halted_reason
            and not self.pending_intents
        )

    def validate(self, spec: FormalRunSpecification) -> None:
        if self.schema_version != FORMAL_SCHEMA_VERSION:
            raise FormalSafetyError("formal controller schema mismatch")
        if self.formal_run_id != spec.formal_run_id:
            raise FormalSafetyError("formal controller run ID mismatch")
        if self.package_specification_sha256 != spec.package_specification_sha256:
            raise FormalSafetyError("formal controller package mismatch")
        if self.source_manifest_sha256 != spec.source_manifest_sha256:
            raise FormalSafetyError("formal controller source mismatch")
        if self.normal_create_count > spec.maximum_normal_creates:
            raise FormalSafetyError("formal create budget exceeded")
        if abs(self.position_btc) > spec.maximum_inventory_btc:
            raise FormalSafetyError("formal state inventory exceeds cap")
        pending_sides = [str(value.get("side", "")) for value in self.pending_intents.values()]
        sides = list(self.owned_orders.values()) + pending_sides
        if sides.count("buy") > 1 or sides.count("sell") > 1:
            raise FormalSafetyError("formal state has duplicate same-side orders")
        if self.client_order_generation < len(self.pending_intents) + len(self.owned_orders):
            raise FormalSafetyError("formal client-order generation regressed")
        if self.net_realized_pnl_usdt != self.gross_realized_pnl_usdt - self.actual_fees_usdt:
            raise FormalSafetyError("formal state net accounting mismatch")
        _sha(self.fill_deduplication_sha256, "formal fill deduplication hash")
        if self.flatten_submission_count > spec.maximum_flatten_submissions:
            raise FormalSafetyError("formal flatten single-flight policy exceeded")
        if self.stage is FormalStage.HALTED and not self.halted_reason:
            raise FormalSafetyError("halted formal state lacks reason")
        if self.validation_kill_active and not self.validation_kill_id:
            raise FormalSafetyError("active validation kill lacks identity")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "formal_run_id": self.formal_run_id,
            "package_specification_sha256": self.package_specification_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "stage": self.stage.value,
            "process_generation": self.process_generation,
            "started_at_ms": self.started_at_ms,
            "deadline_at_ms": self.deadline_at_ms,
            "last_create_at_ms": self.last_create_at_ms,
            "normal_create_count": self.normal_create_count,
            "client_order_generation": self.client_order_generation,
            "pending_intents": {
                key: dict(sorted(value.items()))
                for key, value in sorted(self.pending_intents.items())
            },
            "owned_orders": dict(sorted(self.owned_orders.items())),
            "account_binding": self.account_binding,
            "position_btc": str(self.position_btc),
            "average_entry_usdt": str(self.average_entry_usdt),
            "gross_realized_pnl_usdt": str(self.gross_realized_pnl_usdt),
            "normal_bid_fills_total": self.normal_bid_fills_total,
            "normal_ask_fills_total": self.normal_ask_fills_total,
            "normal_fifo_round_trips_total": self.normal_fifo_round_trips_total,
            "fill_cursor_timestamp_ms": self.fill_cursor_timestamp_ms,
            "fill_cursor_trade_id": self.fill_cursor_trade_id,
            "fill_deduplication_sha256": self.fill_deduplication_sha256,
            "actual_fees_usdt": str(self.actual_fees_usdt),
            "net_realized_pnl_usdt": str(self.net_realized_pnl_usdt),
            "equity_usdt": str(self.equity_usdt),
            "peak_equity_usdt": str(self.peak_equity_usdt),
            "last_observation_sequence": self.last_observation_sequence,
            "last_observed_at_ms": self.last_observed_at_ms,
            "last_market_timestamp_ms": self.last_market_timestamp_ms,
            "r1_completed": self.r1_completed,
            "r2_completed": self.r2_completed,
            "validation_kill_active": self.validation_kill_active,
            "validation_kill_id": self.validation_kill_id,
            "checkpoint_id": self.checkpoint_id,
            "flatten_submission_count": self.flatten_submission_count,
            "flatten_unknown": self.flatten_unknown,
            "halted_reason": self.halted_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "FormalControllerState":
        try:
            return cls(
                schema_version=int(value["schema_version"]),
                formal_run_id=str(value["formal_run_id"]),
                package_specification_sha256=str(value["package_specification_sha256"]),
                source_manifest_sha256=str(value["source_manifest_sha256"]),
                stage=FormalStage(str(value["stage"])),
                process_generation=int(value["process_generation"]),
                started_at_ms=int(value["started_at_ms"]),
                deadline_at_ms=int(value["deadline_at_ms"]),
                last_create_at_ms=int(value["last_create_at_ms"]),
                normal_create_count=int(value["normal_create_count"]),
                client_order_generation=int(value["client_order_generation"]),
                pending_intents={
                    str(k): dict(v) for k, v in dict(value["pending_intents"]).items()
                },
                owned_orders={str(k): str(v) for k, v in dict(value["owned_orders"]).items()},
                account_binding=str(value["account_binding"]),
                position_btc=_decimal(value["position_btc"], "formal position"),
                average_entry_usdt=_decimal(value["average_entry_usdt"], "formal entry"),
                gross_realized_pnl_usdt=_decimal(
                    value["gross_realized_pnl_usdt"], "formal gross PnL"
                ),
                normal_bid_fills_total=int(value["normal_bid_fills_total"]),
                normal_ask_fills_total=int(value["normal_ask_fills_total"]),
                normal_fifo_round_trips_total=int(value["normal_fifo_round_trips_total"]),
                fill_cursor_timestamp_ms=int(value["fill_cursor_timestamp_ms"]),
                fill_cursor_trade_id=str(value["fill_cursor_trade_id"]),
                fill_deduplication_sha256=str(value["fill_deduplication_sha256"]),
                actual_fees_usdt=_decimal(value["actual_fees_usdt"], "formal fees"),
                net_realized_pnl_usdt=_decimal(value["net_realized_pnl_usdt"], "formal net PnL"),
                equity_usdt=_decimal(value["equity_usdt"], "formal equity"),
                peak_equity_usdt=_decimal(value["peak_equity_usdt"], "formal peak equity"),
                last_observation_sequence=int(value["last_observation_sequence"]),
                last_observed_at_ms=int(value["last_observed_at_ms"]),
                last_market_timestamp_ms=int(value["last_market_timestamp_ms"]),
                r1_completed=bool(value["r1_completed"]),
                r2_completed=bool(value["r2_completed"]),
                validation_kill_active=bool(value["validation_kill_active"]),
                validation_kill_id=str(value["validation_kill_id"]),
                checkpoint_id=str(value["checkpoint_id"]),
                flatten_submission_count=int(value["flatten_submission_count"]),
                flatten_unknown=bool(value["flatten_unknown"]),
                halted_reason=str(value["halted_reason"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalSafetyError("formal controller state is malformed") from exc


class FormalStateStore:
    """Atomic snapshot plus append-only hash chain for formal controller state."""

    def __init__(self, path: Path):
        self.path = path
        self.journal_path = path.with_name(path.stem + "_journal.jsonl")

    def _records(self) -> list[dict[str, object]]:
        if not self.journal_path.exists():
            if self.path.exists():
                raise FormalPersistenceError("formal snapshot exists without journal")
            return []
        raw = self.journal_path.read_text(encoding="utf-8")
        if not raw or not raw.endswith("\n"):
            raise FormalPersistenceError("formal journal is empty or truncated")
        try:
            records = [json.loads(line) for line in raw.splitlines()]
        except Exception as exc:
            raise FormalPersistenceError("formal journal cannot be decoded") from exc
        previous = ""
        for sequence, record in enumerate(records, start=1):
            if int(record.get("sequence", 0)) != sequence:
                raise FormalPersistenceError("formal journal sequence mismatch")
            if str(record.get("previous_hash", "")) != previous:
                raise FormalPersistenceError("formal journal chain mismatch")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise FormalPersistenceError("formal journal payload is malformed")
            base = {
                "schema_version": FORMAL_SCHEMA_VERSION,
                "sequence": sequence,
                "previous_hash": previous,
                "payload_sha256": canonical_sha256(payload),
                "payload": payload,
            }
            expected = canonical_sha256(base)
            if record.get("payload_sha256") != base["payload_sha256"]:
                raise FormalPersistenceError("formal payload hash mismatch")
            if record.get("record_hash") != expected:
                raise FormalPersistenceError("formal record hash mismatch")
            previous = expected
        return records

    def save(self, state: FormalControllerState, spec: FormalRunSpecification) -> str:
        state.validate(spec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = self._records()
        previous = str(records[-1]["record_hash"]) if records else ""
        payload = state.to_dict()
        base = {
            "schema_version": FORMAL_SCHEMA_VERSION,
            "sequence": len(records) + 1,
            "previous_hash": previous,
            "payload_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        record = dict(base)
        record["record_hash"] = canonical_sha256(base)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise FormalPersistenceError("formal state save failed") from exc
        return str(record["record_hash"])

    def load(self, spec: FormalRunSpecification) -> FormalControllerState | None:
        records = self._records()
        if not records:
            return None
        if not self.path.is_file():
            raise FormalPersistenceError("formal state snapshot is missing")
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FormalPersistenceError("formal snapshot cannot be decoded") from exc
        if snapshot != records[-1]:
            raise FormalPersistenceError("formal snapshot does not match journal head")
        state = FormalControllerState.from_dict(dict(snapshot["payload"]))
        state.validate(spec)
        return state


class FormalOrchestrator:
    """Pure formal state machine; every returned action requires driver evidence."""

    def __init__(
        self,
        *,
        spec: FormalRunSpecification,
        store: FormalStateStore,
        state: FormalControllerState,
    ) -> None:
        spec.validate()
        state.validate(spec)
        self.spec = spec
        self.store = store
        self.state = state

    @classmethod
    def prepare(
        cls, spec: FormalRunSpecification, store: FormalStateStore
    ) -> "FormalOrchestrator":
        if store.path.exists() or store.journal_path.exists():
            raise FormalPersistenceError("formal run state already exists")
        state = FormalControllerState(
            formal_run_id=spec.formal_run_id,
            package_specification_sha256=spec.package_specification_sha256,
            source_manifest_sha256=spec.source_manifest_sha256,
        )
        store.save(state, spec)
        return cls(spec=spec, store=store, state=state)

    def _save(self) -> None:
        self.store.save(self.state, self.spec)

    def _halt(self, reason: str) -> tuple[FormalAction, ...]:
        self.state.stage = FormalStage.HALTED
        self.state.halted_reason = reason
        self._save()
        return (FormalAction(FormalActionType.HALT, {"reason": reason}),)

    def arm_and_start(
        self,
        *,
        arm_token: str,
        snapshots: Sequence[FormalObservation],
        now_ms: int,
    ) -> None:
        if self.state.stage is not FormalStage.NOT_ARMED:
            raise FormalSafetyError("formal state is already armed or closed")
        if arm_token != self.spec.expected_arm_token:
            raise FormalSafetyError("formal session arm token mismatch")
        if len(snapshots) != 2:
            raise FormalSafetyError("formal start requires exactly two snapshots")
        for snapshot in snapshots:
            failure = snapshot.safety_failure(self.spec)
            if failure:
                raise FormalSafetyError(failure)
            if snapshot.position_btc != 0 or snapshot.open_owned_orders:
                raise FormalSafetyError("formal start requires flat and zero owned orders")
            if snapshot.normal_bid_fills_total or snapshot.normal_ask_fills_total:
                raise FormalSafetyError("formal run counters must start at zero")
        if snapshots[0].consistency_key != snapshots[1].consistency_key:
            raise FormalSafetyError("formal start snapshots are inconsistent")
        self._require_monotonic_snapshot_pair(snapshots)
        self.state.stage = FormalStage.SEEK_R1_FILL
        self.state.started_at_ms = now_ms
        self.state.deadline_at_ms = now_ms + self.spec.maximum_wall_minutes * 60_000
        self.state.account_binding = snapshots[1].account_binding
        self.state.position_btc = Decimal("0")
        self._apply_observation_accounting(snapshots[1])
        self._apply_observation_clock(snapshots[1])
        self._save()

    def _deadline_or_failure_actions(
        self, observation: FormalObservation, now_ms: int
    ) -> tuple[FormalAction, ...] | None:
        failure = observation.safety_failure(self.spec)
        deadline = self.state.deadline_at_ms and now_ms >= self.state.deadline_at_ms
        if not failure and not deadline:
            return None
        reason = failure or "FORMAL_DEADLINE_REACHED"
        return self._terminal_actions(observation, reason)

    def _terminal_actions(
        self, observation: FormalObservation, reason: str
    ) -> tuple[FormalAction, ...]:
        """Persist cancel/flatten actions before every terminal halt."""
        actions: list[FormalAction] = []
        if observation.open_owned_orders or self.state.owned_orders:
            actions.append(FormalAction(FormalActionType.CANCEL_ALL_OWNED))
        if observation.position_btc != 0:
            if self.state.flatten_submission_count >= self.spec.maximum_flatten_submissions:
                self.state.flatten_unknown = True
                return self._halt("FLATTEN_SINGLE_FLIGHT_EXHAUSTED")
            self.state.flatten_submission_count += 1
            actions.append(FormalAction(
                FormalActionType.FLATTEN_REDUCE_ONLY,
                {
                    "side": "sell" if observation.position_btc > 0 else "buy",
                    "quantity_btc": str(abs(observation.position_btc)),
                    "single_flight_attempt": self.state.flatten_submission_count,
                },
            ))
        self.state.stage = FormalStage.HALTED
        self.state.halted_reason = reason
        self._save()
        actions.append(FormalAction(FormalActionType.HALT, {"reason": reason}))
        return tuple(actions)

    def observe(
        self, observation: FormalObservation, *, now_ms: int
    ) -> tuple[FormalAction, ...]:
        if self.state.stage in {FormalStage.NOT_ARMED, FormalStage.COMPLETE, FormalStage.HALTED}:
            return self._halt("OBSERVATION_NOT_ALLOWED_IN_STAGE")
        emergency = self._deadline_or_failure_actions(observation, now_ms)
        if emergency is not None:
            return emergency
        if self.state.pending_intents:
            return self._halt("UNRESOLVED_CREATE_INTENT")
        if observation.account_binding != self.state.account_binding:
            return self._halt("ACCOUNT_BINDING_DRIFT")
        if (
            observation.sequence <= self.state.last_observation_sequence
            or observation.observed_at_ms <= self.state.last_observed_at_ms
            or observation.market_timestamp_ms <= self.state.last_market_timestamp_ms
        ):
            return self._halt("NON_MONOTONIC_OR_DUPLICATE_OBSERVATION")
        expected_owned = set(self.state.owned_orders)
        observed_owned = set(observation.open_order_map)
        if observed_owned - expected_owned:
            return self._halt("AUTHORITATIVE_OWNED_ORDER_IDENTITY_UNKNOWN")
        fills_before = self.state.normal_bid_fills_total + self.state.normal_ask_fills_total
        fills_after = (
            observation.normal_bid_fills_total + observation.normal_ask_fills_total
        )
        bid_fill_delta = (
            observation.normal_bid_fills_total
            - self.state.normal_bid_fills_total
        )
        ask_fill_delta = (
            observation.normal_ask_fills_total
            - self.state.normal_ask_fills_total
        )
        if (
            observation.normal_bid_fills_total < self.state.normal_bid_fills_total
            or observation.normal_ask_fills_total < self.state.normal_ask_fills_total
            or observation.normal_fifo_round_trips_total
            < self.state.normal_fifo_round_trips_total
        ):
            return self._halt("FORMAL_FILL_COUNTER_REGRESSION")
        new_fill = fills_after > fills_before
        missing_owned = expected_owned - observed_owned
        if missing_owned and not new_fill:
            return self._halt("MISSING_EXPECTED_ORDER_WITHOUT_FILL")
        if any(
            observation.open_order_map[client_id]
            != self.state.owned_orders[client_id]
            for client_id in observed_owned
        ):
            return self._halt("AUTHORITATIVE_OWNED_ORDER_SIDE_MISMATCH")
        if missing_owned:
            missing_bid = sum(
                self.state.owned_orders[client_id] == "buy"
                for client_id in missing_owned
            )
            missing_ask = sum(
                self.state.owned_orders[client_id] == "sell"
                for client_id in missing_owned
            )
            if missing_bid > bid_fill_delta or missing_ask > ask_fill_delta:
                return self._halt("MISSING_OWNED_ORDER_NOT_EXPLAINED_BY_FILL")
            for client_id in missing_owned:
                del self.state.owned_orders[client_id]
        cursor_before = (
            self.state.fill_cursor_timestamp_ms, self.state.fill_cursor_trade_id
        )
        cursor_after = (
            observation.fill_cursor_timestamp_ms, observation.fill_cursor_trade_id
        )
        if cursor_after < cursor_before or (new_fill and cursor_after == cursor_before):
            return self._halt("FILL_CURSOR_NOT_MONOTONIC")
        self.state.normal_bid_fills_total = observation.normal_bid_fills_total
        self.state.normal_ask_fills_total = observation.normal_ask_fills_total
        self.state.normal_fifo_round_trips_total = (
            observation.normal_fifo_round_trips_total
        )
        self._apply_observation_accounting(observation)
        self._apply_observation_clock(observation)

        if new_fill:
            if not self.state.r1_completed:
                if (
                    observation.normal_bid_fills_total > 0
                    and observation.normal_ask_fills_total > 0
                ):
                    return self._halt("BOTH_SIDES_FILLED_BEFORE_R1_CHECKPOINT")
                if observation.position_btc == 0:
                    return self._halt("R1_NONZERO_CHECKPOINT_WAS_NOT_OBSERVABLE")
                self.state.stage = FormalStage.STOP_AFTER_R1_FILL
            elif (
                observation.position_btc == 0
                and observation.normal_fifo_round_trips_total >= 1
            ):
                self.state.stage = FormalStage.STOP_AFTER_ROUND_TRIP
            else:
                self.state.stage = FormalStage.STOP_AFTER_OFFSET_FILL
            self._save()
            if observation.open_owned_orders:
                return (FormalAction(FormalActionType.CANCEL_ALL_OWNED),)

        if self.state.stage is FormalStage.STOP_AFTER_R1_FILL:
            if observation.open_owned_orders:
                return (FormalAction(FormalActionType.CANCEL_ALL_OWNED),)
            return (FormalAction(FormalActionType.PERSIST_R1_CHECKPOINT),)
        if self.state.stage is FormalStage.STOP_AFTER_ROUND_TRIP:
            if observation.open_owned_orders:
                return (FormalAction(FormalActionType.CANCEL_ALL_OWNED),)
            return (FormalAction(FormalActionType.ACTIVATE_VALIDATION_KILL),)
        if self.state.stage is FormalStage.STOP_AFTER_OFFSET_FILL:
            if observation.open_owned_orders:
                return (FormalAction(FormalActionType.CANCEL_ALL_OWNED),)
            self.state.stage = FormalStage.SEEK_ROUND_TRIP
            self.state.owned_orders.clear()
            self._save()
        return ()

    def plan_quotes(
        self,
        *,
        observation: FormalObservation,
        quote: FrozenQuotePlan,
        now_ms: int,
    ) -> tuple[FormalAction, ...]:
        if not self.state.allow_quote_planning:
            raise FormalSafetyError("quote planning is blocked by formal state")
        emergency = self._deadline_or_failure_actions(observation, now_ms)
        if emergency is not None:
            return emergency
        if observation.account_binding != self.state.account_binding:
            raise FormalSafetyError("quote account binding drift")
        if observation.open_order_map != self.state.owned_orders:
            raise FormalSafetyError("quote snapshot does not match authoritative owned set")
        if observation.position_btc != self.state.position_btc:
            raise FormalSafetyError("quote snapshot does not match reconciled position")
        quote.validate(self.spec, observation)
        if self.state.owned_orders or self.state.pending_intents:
            raise FormalSafetyError("replacement requires authoritative cancellation")
        if self.state.normal_create_count >= self.spec.maximum_normal_creates:
            return self._terminal_actions(
                observation, "NORMAL_CREATE_BUDGET_EXHAUSTED"
            )
        if (
            self.state.last_create_at_ms
            and now_ms - self.state.last_create_at_ms < self.spec.minimum_create_interval_ms
        ):
            return ()
        desired: list[tuple[str, Decimal, bool]] = [
            ("buy", quote.bid_price, quote.bid_suppressed),
            ("sell", quote.ask_price, quote.ask_suppressed),
        ]
        if observation.position_btc > 0:
            desired = [row for row in desired if row[0] == "sell"]
        elif observation.position_btc < 0:
            desired = [row for row in desired if row[0] == "buy"]
        actions: list[FormalAction] = []
        remaining_budget = self.spec.maximum_normal_creates - self.state.normal_create_count
        for side, price, suppressed in desired:
            if suppressed or remaining_budget <= 0:
                continue
            self.state.client_order_generation += 1
            generation = self.state.client_order_generation
            client_order_id = "fr" + canonical_sha256({
                "session_id": self.spec.session_id,
                "generation": generation,
                "side": side,
            })[:28]
            intent = {
                "side": side,
                "price": str(price),
                "quantity_btc": str(quote.quantity_btc),
                "generation": generation,
                "dispatch_recorded": False,
                "ambiguous": False,
            }
            self.state.pending_intents[client_order_id] = intent
            actions.append(FormalAction(
                FormalActionType.PLACE_POST_ONLY,
                {
                    "client_order_id": client_order_id,
                    "side": side,
                    "price": str(price),
                    "quantity_btc": str(quote.quantity_btc),
                    "market_timestamp_ms": quote.market_timestamp_ms,
                    "reduce_only": False,
                    "post_only_required": True,
                },
            ))
            remaining_budget -= 1
        if actions:
            self._save()
        return tuple(actions)

    def record_create_dispatch(self, *, client_order_id: str, now_ms: int) -> None:
        """Persist a conservative create attempt immediately before dispatch."""
        intent = self.state.pending_intents.get(client_order_id)
        if intent is None:
            raise FormalSafetyError("create dispatch lacks a write-ahead intent")
        if bool(intent.get("dispatch_recorded")):
            raise FormalSafetyError("create dispatch was already recorded")
        if self.state.normal_create_count >= self.spec.maximum_normal_creates:
            self._halt("NORMAL_CREATE_BUDGET_EXHAUSTED")
            raise FormalSafetyError("normal-create budget exhausted")
        if (
            self.state.last_create_at_ms
            and now_ms - self.state.last_create_at_ms < self.spec.minimum_create_interval_ms
        ):
            raise FormalSafetyError("normal-create rate ceiling violated")
        intent["dispatch_recorded"] = True
        self.state.normal_create_count += 1
        self.state.last_create_at_ms = now_ms
        self._save()

    def abandon_undispatched_intent(
        self, *, client_order_id: str, reason: str
    ) -> None:
        """Remove only an intent that provably never crossed the dispatch boundary."""
        intent = self.state.pending_intents.get(client_order_id)
        if intent is None:
            raise FormalSafetyError("abandoned intent identity is unknown")
        if bool(intent.get("dispatch_recorded")):
            raise FormalSafetyError("a dispatched intent cannot be abandoned")
        if not reason:
            raise FormalSafetyError("abandoned intent reason is missing")
        del self.state.pending_intents[client_order_id]
        self._save()

    def record_post_only_ack(
        self,
        *,
        client_order_id: str,
        side: str,
        post_only_confirmed: bool,
        now_ms: int,
    ) -> None:
        if self.state.stage not in {FormalStage.SEEK_R1_FILL, FormalStage.SEEK_ROUND_TRIP}:
            raise FormalSafetyError("order acknowledgement is not allowed in stage")
        if not post_only_confirmed:
            self._halt("POST_ONLY_ACKNOWLEDGEMENT_MISSING")
            raise FormalSafetyError("post-only acknowledgement is missing")
        intent = self.state.pending_intents.get(client_order_id)
        if not client_order_id or side not in {"buy", "sell"} or intent is None:
            raise FormalSafetyError("order acknowledgement identity is invalid")
        if intent.get("side") != side or not bool(intent.get("dispatch_recorded")):
            raise FormalSafetyError("order acknowledgement does not match dispatched intent")
        if bool(intent.get("ambiguous")):
            raise FormalSafetyError("ambiguous create requires authoritative resolution")
        if client_order_id in self.state.owned_orders:
            raise FormalSafetyError("duplicate client order acknowledgement")
        if side in self.state.owned_orders.values():
            raise FormalSafetyError("duplicate same-side acknowledgement")
        del self.state.pending_intents[client_order_id]
        self.state.owned_orders[client_order_id] = side
        self._save()

    def record_ambiguous_create(self, *, client_order_id: str) -> tuple[FormalAction, ...]:
        intent = self.state.pending_intents.get(client_order_id)
        if intent is None or not bool(intent.get("dispatch_recorded")):
            raise FormalSafetyError("ambiguous create lacks a dispatched intent")
        if bool(intent.get("ambiguous")):
            raise FormalSafetyError("ambiguous create is already awaiting resolution")
        intent["ambiguous"] = True
        self._save()
        return (FormalAction(FormalActionType.RESOLVE_AMBIGUOUS_CREATE, {
            "client_order_id": client_order_id,
            "automatic_retry_allowed": False,
        }),)

    def resolve_ambiguous_create(
        self,
        *,
        client_order_id: str,
        authoritative_status: str,
        post_only_confirmed: bool = False,
    ) -> None:
        intent = self.state.pending_intents.get(client_order_id)
        if intent is None or not bool(intent.get("ambiguous")):
            raise FormalSafetyError("ambiguous resolution identity is unknown")
        if authoritative_status not in {"open", "closed", "absent"}:
            raise FormalSafetyError("ambiguous create remains unresolved")
        side = str(intent["side"])
        if authoritative_status in {"open", "closed"} and not post_only_confirmed:
            self._halt("AMBIGUOUS_CREATE_POST_ONLY_UNPROVEN")
            raise FormalSafetyError("resolved create lacks post-only proof")
        if authoritative_status == "open":
            if side in self.state.owned_orders.values():
                raise FormalSafetyError("ambiguous resolution creates duplicate side")
            self.state.owned_orders[client_order_id] = side
        del self.state.pending_intents[client_order_id]
        self._save()

    def record_authoritative_cancellation(
        self, *, remaining_open_client_ids: Iterable[str]
    ) -> None:
        remaining = set(remaining_open_client_ids)
        if self.state.pending_intents:
            raise FormalSafetyError("cancellation cannot clear unresolved creates")
        if remaining:
            raise FormalSafetyError("authoritative cancellation is incomplete")
        self.state.owned_orders.clear()
        self._save()

    def validate_r1_checkpoint_ready(self) -> None:
        if self.state.stage is not FormalStage.STOP_AFTER_R1_FILL:
            raise FormalSafetyError("R1 checkpoint is not ready")
        if (
            self.state.owned_orders
            or self.state.pending_intents
            or self.state.position_btc == 0
        ):
            raise FormalSafetyError("R1 checkpoint requires no orders and nonzero position")
        if self.state.flatten_submission_count or self.state.flatten_unknown:
            raise FormalSafetyError("R1 checkpoint cannot follow emergency flatten")

    def persist_r1_checkpoint(self, checkpoint_id: str) -> tuple[FormalAction, ...]:
        self.validate_r1_checkpoint_ready()
        if not checkpoint_id:
            raise FormalSafetyError("R1 checkpoint identity is missing")
        self.state.checkpoint_id = checkpoint_id
        self.state.stage = FormalStage.RESTART_REQUIRED_R1
        self._save()
        return (FormalAction(
            FormalActionType.EXIT_RESTART_R1,
            {"checkpoint_id": checkpoint_id},
        ),)

    def reconcile_halted_terminal_flat(
        self,
        *,
        account_binding: str,
        authoritative_position_btc: Decimal,
        authoritative_open_owned_order_ids: Iterable[str],
        closed_owned_order_ids: Iterable[str],
        normal_bid_fills_total: int,
        normal_ask_fills_total: int,
        normal_fifo_round_trips_total: int,
        gross_realized_pnl_usdt: Decimal,
        actual_fees_usdt: Decimal,
        net_realized_pnl_usdt: Decimal,
        flatten_submission_count: int,
    ) -> tuple[str, ...]:
        """Reconcile a stale halted controller from flat authoritative evidence."""
        if self.state.stage is not FormalStage.HALTED:
            raise FormalSafetyError("terminal reconciliation requires halted state")
        if account_binding != self.state.account_binding:
            raise FormalSafetyError("terminal reconciliation account binding mismatch")
        if authoritative_position_btc != 0:
            raise FormalSafetyError("terminal reconciliation requires flat position")
        if tuple(authoritative_open_owned_order_ids):
            raise FormalSafetyError("terminal reconciliation found open owned orders")
        if self.state.pending_intents:
            raise FormalSafetyError("terminal reconciliation found pending intent")
        closed = set(closed_owned_order_ids)
        stale = set(self.state.owned_orders)
        if stale - closed:
            raise FormalSafetyError("terminal reconciliation has unexplained ownership")
        if (
            normal_bid_fills_total < self.state.normal_bid_fills_total
            or normal_ask_fills_total < self.state.normal_ask_fills_total
            or normal_fifo_round_trips_total
            < self.state.normal_fifo_round_trips_total
        ):
            raise FormalSafetyError("terminal reconciliation counter regression")
        if (
            net_realized_pnl_usdt
            != gross_realized_pnl_usdt - actual_fees_usdt
        ):
            raise FormalSafetyError("terminal reconciliation accounting mismatch")
        if not (
            self.state.flatten_submission_count
            <= flatten_submission_count
            <= self.spec.maximum_flatten_submissions
        ):
            raise FormalSafetyError("terminal reconciliation flatten count mismatch")
        cleared = tuple(sorted(stale))
        self.state.owned_orders.clear()
        self.state.position_btc = Decimal("0")
        self.state.average_entry_usdt = Decimal("0")
        self.state.normal_bid_fills_total = normal_bid_fills_total
        self.state.normal_ask_fills_total = normal_ask_fills_total
        self.state.normal_fifo_round_trips_total = normal_fifo_round_trips_total
        self.state.gross_realized_pnl_usdt = gross_realized_pnl_usdt
        self.state.actual_fees_usdt = actual_fees_usdt
        self.state.net_realized_pnl_usdt = net_realized_pnl_usdt
        self.state.flatten_submission_count = flatten_submission_count
        self._save()
        return cleared

    def resume_r1(
        self,
        *,
        checkpoint_id: str,
        snapshots: Sequence[FormalObservation],
    ) -> None:
        if self.state.stage is not FormalStage.RESTART_REQUIRED_R1:
            raise FormalSafetyError("R1 resume is not expected")
        if checkpoint_id != self.state.checkpoint_id:
            raise FormalSafetyError("R1 checkpoint acknowledgement mismatch")
        self._validate_restart_pair(snapshots, self.state.position_btc)
        self.state.process_generation += 1
        self.state.r1_completed = True
        self.state.checkpoint_id = ""
        self.state.stage = FormalStage.SEEK_ROUND_TRIP
        self._apply_observation_clock(snapshots[1])
        self._save()

    def activate_r2_kill(self) -> tuple[FormalAction, ...]:
        if self.state.stage is not FormalStage.STOP_AFTER_ROUND_TRIP:
            raise FormalSafetyError("R2 kill activation is not ready")
        if self.state.owned_orders or self.state.position_btc != 0:
            raise FormalSafetyError("R2 kill requires flat and zero orders")
        if self.state.normal_fifo_round_trips_total < 1:
            raise FormalSafetyError("R2 requires a normal FIFO round trip")
        activation = canonical_sha256({
            "formal_run_id": self.state.formal_run_id,
            "process_generation": self.state.process_generation,
            "round_trips": self.state.normal_fifo_round_trips_total,
        })[:24]
        self.state.validation_kill_active = True
        self.state.validation_kill_id = activation
        self.state.checkpoint_id = activation
        self.state.stage = FormalStage.RESTART_REQUIRED_R2
        self._save()
        return (
            FormalAction(FormalActionType.PERSIST_R2_CHECKPOINT, {
                "activation_id": activation,
            }),
            FormalAction(FormalActionType.EXIT_RESTART_R2, {
                "checkpoint_id": activation,
            }),
        )

    def resume_r2(
        self,
        *,
        checkpoint_id: str,
        snapshots: Sequence[FormalObservation],
    ) -> None:
        if self.state.stage is not FormalStage.RESTART_REQUIRED_R2:
            raise FormalSafetyError("R2 resume is not expected")
        if checkpoint_id != self.state.checkpoint_id:
            raise FormalSafetyError("R2 checkpoint acknowledgement mismatch")
        if not self.state.validation_kill_active:
            raise FormalSafetyError("R2 validation kill was not persisted")
        self._validate_restart_pair(snapshots, Decimal("0"))
        self.state.process_generation += 1
        self.state.stage = FormalStage.KILL_LATCH_BLOCKED
        self._apply_observation_clock(snapshots[1])
        self._save()

    def release_r2_kill(
        self,
        *,
        acknowledgement_id: str,
        snapshots: Sequence[FormalObservation],
    ) -> tuple[FormalAction, ...]:
        if self.state.stage is not FormalStage.KILL_LATCH_BLOCKED:
            raise FormalSafetyError("R2 kill release is not expected")
        if acknowledgement_id != self.state.validation_kill_id:
            raise FormalSafetyError("R2 kill acknowledgement mismatch")
        self._validate_restart_pair(snapshots, Decimal("0"))
        self.state.validation_kill_active = False
        self.state.validation_kill_id = ""
        self.state.checkpoint_id = ""
        self.state.r2_completed = True
        self.state.stage = FormalStage.FINAL_RECONCILIATION
        self._apply_observation_clock(snapshots[1])
        self._save()
        return (FormalAction(FormalActionType.RELEASE_VALIDATION_KILL),)

    def finalize(self, snapshots: Sequence[FormalObservation]) -> tuple[FormalAction, ...]:
        if self.state.stage is not FormalStage.FINAL_RECONCILIATION:
            raise FormalSafetyError("formal finalization is not ready")
        self._validate_restart_pair(snapshots, Decimal("0"))
        if not self.state.r1_completed or not self.state.r2_completed:
            raise FormalSafetyError("mandatory restart checkpoints are incomplete")
        if self.state.normal_bid_fills_total < 1 or self.state.normal_ask_fills_total < 1:
            raise FormalSafetyError("both maker-fill sides are not proven")
        if self.state.normal_fifo_round_trips_total < 1:
            raise FormalSafetyError("normal FIFO maker round trip is not proven")
        self.state.stage = FormalStage.COMPLETE
        self._apply_observation_clock(snapshots[1])
        self._save()
        return (FormalAction(FormalActionType.FINALIZE),)

    def _validate_restart_pair(
        self,
        snapshots: Sequence[FormalObservation],
        expected_position: Decimal,
    ) -> None:
        if len(snapshots) != 2:
            raise FormalSafetyError("restart requires exactly two snapshots")
        for snapshot in snapshots:
            failure = snapshot.safety_failure(self.spec)
            if failure:
                raise FormalSafetyError(failure)
            if snapshot.open_owned_orders or snapshot.position_btc != expected_position:
                raise FormalSafetyError("restart order/position snapshot mismatch")
            if snapshot.account_binding != self.state.account_binding:
                raise FormalSafetyError("restart account binding mismatch")
            if (
                snapshot.normal_bid_fills_total != self.state.normal_bid_fills_total
                or snapshot.normal_ask_fills_total != self.state.normal_ask_fills_total
                or snapshot.normal_fifo_round_trips_total
                != self.state.normal_fifo_round_trips_total
                or snapshot.fill_cursor_timestamp_ms
                != self.state.fill_cursor_timestamp_ms
                or snapshot.fill_cursor_trade_id != self.state.fill_cursor_trade_id
                or snapshot.fill_deduplication_sha256
                != self.state.fill_deduplication_sha256
                or snapshot.average_entry_usdt != self.state.average_entry_usdt
                or snapshot.gross_realized_pnl_usdt
                != self.state.gross_realized_pnl_usdt
                or snapshot.actual_fees_usdt != self.state.actual_fees_usdt
                or snapshot.net_realized_pnl_usdt
                != self.state.net_realized_pnl_usdt
            ):
                raise FormalSafetyError("restart accounting snapshot mismatch")
        if snapshots[0].consistency_key != snapshots[1].consistency_key:
            raise FormalSafetyError("restart snapshots are inconsistent")
        self._require_monotonic_snapshot_pair(snapshots)
        if snapshots[0].sequence <= self.state.last_observation_sequence:
            raise FormalSafetyError("restart snapshot sequence was replayed")
        if snapshots[0].observed_at_ms <= self.state.last_observed_at_ms:
            raise FormalSafetyError("restart observation time was replayed")
        if snapshots[0].market_timestamp_ms <= self.state.last_market_timestamp_ms:
            raise FormalSafetyError("restart market time was replayed")

    @staticmethod
    def _require_monotonic_snapshot_pair(
        snapshots: Sequence[FormalObservation],
    ) -> None:
        first, second = snapshots
        if (
            second.sequence <= first.sequence
            or second.observed_at_ms <= first.observed_at_ms
            or second.market_timestamp_ms <= first.market_timestamp_ms
        ):
            raise FormalSafetyError("authoritative snapshot pair is non-monotonic")

    def _apply_observation_accounting(self, observation: FormalObservation) -> None:
        self.state.position_btc = observation.position_btc
        self.state.average_entry_usdt = observation.average_entry_usdt
        self.state.gross_realized_pnl_usdt = observation.gross_realized_pnl_usdt
        self.state.actual_fees_usdt = observation.actual_fees_usdt
        self.state.net_realized_pnl_usdt = observation.net_realized_pnl_usdt
        self.state.normal_bid_fills_total = observation.normal_bid_fills_total
        self.state.normal_ask_fills_total = observation.normal_ask_fills_total
        self.state.normal_fifo_round_trips_total = (
            observation.normal_fifo_round_trips_total
        )
        self.state.fill_cursor_timestamp_ms = observation.fill_cursor_timestamp_ms
        self.state.fill_cursor_trade_id = observation.fill_cursor_trade_id
        self.state.fill_deduplication_sha256 = observation.fill_deduplication_sha256
        self.state.equity_usdt = observation.equity_usdt
        self.state.peak_equity_usdt = observation.peak_equity_usdt

    def _apply_observation_clock(self, observation: FormalObservation) -> None:
        self.state.last_observation_sequence = observation.sequence
        self.state.last_observed_at_ms = observation.observed_at_ms
        self.state.last_market_timestamp_ms = observation.market_timestamp_ms


def make_fixture_spec(
    *,
    formal_run_id: str = "formal-fixture",
    package_id: str = "formal-package-fixture",
) -> FormalRunSpecification:
    values = {
        "formal_run_id": formal_run_id,
        "package_id": package_id,
        "package_specification_sha256": "a" * 64,
        "source_manifest_sha256": "b" * 64,
        "runtime_configuration_sha256": "c" * 64,
        "profile_binding_sha256": "d" * 64,
        "market_fingerprint": "fixture-market",
        "offline_completion_sha256": "e" * 64,
        "preflight_completion_sha256": "f" * 64,
        "preflight_decision_sha256": "1" * 64,
        "ccxt_source_sha256": "2" * 64,
    }
    spec = FormalRunSpecification(**values)
    spec.validate()
    return spec


def make_fixture_observation(
    spec: FormalRunSpecification,
    *,
    sequence: int = 1,
    observed_at_ms: int = 1_000,
    position_btc: object = "0",
    open_owned_orders: Iterable[tuple[str, str]] = (),
    normal_bid_fills_total: int = 0,
    normal_ask_fills_total: int = 0,
    normal_fifo_round_trips_total: int = 0,
    fill_cursor_timestamp_ms: int = 0,
    fill_cursor_trade_id: str = "",
    fill_deduplication_sha256: str = "0" * 64,
    average_entry_usdt: object = "0",
    gross_realized_pnl_usdt: object = "0",
    actual_fees_usdt: object = "0",
    net_realized_pnl_usdt: object | None = None,
    **overrides: object,
) -> FormalObservation:
    values: dict[str, object] = {
        "sequence": sequence,
        "observed_at_ms": observed_at_ms,
        "environment": "OKX_DEMO",
        "sandbox_mode": True,
        "simulated_trading_header": True,
        "account_binding": "fixture-account-binding",
        "market_fingerprint": spec.market_fingerprint,
        "symbol": spec.symbol,
        "position_mode": "net_mode",
        "leverage": Decimal("3"),
        "position_btc": _decimal(position_btc, "fixture position"),
        "open_owned_orders": tuple(open_owned_orders),
        "foreign_order_count": 0,
        "normal_bid_fills_total": normal_bid_fills_total,
        "normal_ask_fills_total": normal_ask_fills_total,
        "normal_fifo_round_trips_total": normal_fifo_round_trips_total,
        "fill_cursor_timestamp_ms": fill_cursor_timestamp_ms,
        "fill_cursor_trade_id": fill_cursor_trade_id,
        "fill_deduplication_sha256": fill_deduplication_sha256,
        "average_entry_usdt": _decimal(average_entry_usdt, "fixture entry"),
        "gross_realized_pnl_usdt": _decimal(gross_realized_pnl_usdt, "fixture gross PnL"),
        "actual_fees_usdt": _decimal(actual_fees_usdt, "fixture fees"),
        "net_realized_pnl_usdt": (
            _decimal(net_realized_pnl_usdt, "fixture net PnL")
            if net_realized_pnl_usdt is not None
            else _decimal(gross_realized_pnl_usdt, "fixture gross PnL")
            - _decimal(actual_fees_usdt, "fixture fees")
        ),
        "equity_usdt": Decimal("750"),
        "peak_equity_usdt": Decimal("750"),
        "available_equity_usdt": Decimal("750"),
        "maintenance_margin_usdt": Decimal("0"),
        "clock_skew_ms": 25,
        "market_timestamp_ms": 1_000,
        "market_age_ms": 25,
        "best_bid": Decimal("49999"),
        "best_ask": Decimal("50001"),
        "orders_complete": True,
        "trades_complete": True,
        "position_complete": True,
        "balance_complete": True,
        "fees_complete": True,
    }
    values.update(overrides)
    return FormalObservation(**values)


def make_fixture_quote(
    spec: FormalRunSpecification, observation: FormalObservation
) -> FrozenQuotePlan:
    return FrozenQuotePlan(
        profile_binding_sha256=spec.profile_binding_sha256,
        source_manifest_sha256=spec.source_manifest_sha256,
        runtime_configuration_sha256=spec.runtime_configuration_sha256,
        market_timestamp_ms=observation.market_timestamp_ms,
        best_bid=observation.best_bid,
        best_ask=observation.best_ask,
        bid_price=Decimal("49998"),
        ask_price=Decimal("50002"),
        quantity_btc=Decimal("0.01"),
        quote_allowed=True,
    )
