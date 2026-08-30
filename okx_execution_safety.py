"""Offline execution-safety contracts for a future OKX adapter.

This module deliberately performs no network I/O and is not wired into the
running bot.  It makes the safety semantics testable before any production
integration is authorized: deterministic order intent identity, fail-closed
startup reconciliation, cancel-before-replace ordering, and a persistent
kill-switch latch contract.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable


class SafetyContractError(ValueError):
    """Raised when a safety contract is malformed or cannot be released."""


@dataclass(frozen=True)
class OrderIntent:
    """Canonical identity for one intended exchange order."""

    session_id: str
    generation: int
    symbol: str
    side: str
    price: Decimal
    contracts: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise SafetyContractError("session_id must not be empty")
        if self.generation < 0:
            raise SafetyContractError("generation must be non-negative")
        if self.side not in {"buy", "sell"}:
            raise SafetyContractError("side must be buy or sell")
        if self.price <= 0 or self.contracts <= 0:
            raise SafetyContractError("price and contracts must be positive")

    @property
    def client_order_id(self) -> str:
        """Return a compact deterministic identifier without leaking inputs."""
        payload = {
            "contracts": str(self.contracts.normalize()),
            "generation": self.generation,
            "price": str(self.price.normalize()),
            "reduce_only": self.reduce_only,
            "session_id": self.session_id,
            "side": self.side,
            "symbol": self.symbol,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"bt{digest[:26]}"


@dataclass(frozen=True)
class ExchangeOrder:
    """Minimum exchange order snapshot needed for ownership reconciliation."""

    order_id: str
    client_order_id: str
    side: str


@dataclass(frozen=True)
class ReconciliationResult:
    """Fail-closed startup/order reconciliation outcome."""

    allow_quoting: bool
    reason: str
    foreign_order_ids: tuple[str, ...] = ()
    missing_expected_client_ids: tuple[str, ...] = ()


def evaluate_startup_reconciliation(
    *,
    expected_client_order_ids: Iterable[str],
    exchange_orders: Iterable[ExchangeOrder],
    local_position_base: Decimal,
    exchange_position_base: Decimal,
    base_step: Decimal,
    orders_fetch_ok: bool,
    position_fetch_ok: bool,
    missing_order_trades_reconciled: bool = False,
) -> ReconciliationResult:
    """Allow quoting only after order ownership and position state reconcile."""
    if not orders_fetch_ok or not position_fetch_ok:
        return ReconciliationResult(False, "exchange_snapshot_incomplete")
    if base_step <= 0:
        raise SafetyContractError("base_step must be positive")

    expected = set(expected_client_order_ids)
    orders = tuple(exchange_orders)
    foreign = tuple(
        sorted(order.order_id for order in orders if order.client_order_id not in expected)
    )
    if foreign:
        return ReconciliationResult(
            False,
            "foreign_or_unowned_open_orders",
            foreign_order_ids=foreign,
        )

    live_client_ids = {order.client_order_id for order in orders}
    missing = tuple(sorted(expected - live_client_ids))
    if missing and not missing_order_trades_reconciled:
        return ReconciliationResult(
            False,
            "missing_expected_orders_require_trade_reconciliation",
            missing_expected_client_ids=missing,
        )

    sides = [order.side for order in orders]
    if any(side not in {"buy", "sell"} for side in sides):
        return ReconciliationResult(False, "unknown_open_order_side")
    if sides.count("buy") > 1 or sides.count("sell") > 1:
        return ReconciliationResult(False, "duplicate_open_order_side")

    if abs(exchange_position_base - local_position_base) > base_step:
        return ReconciliationResult(False, "position_mismatch_exceeds_base_step")
    return ReconciliationResult(True, "reconciled")


class CancelOutcome(str, Enum):
    """Evidence obtained after a cancellation attempt."""

    CONFIRMED_GONE = "confirmed_gone"
    STILL_OPEN = "still_open"
    UNKNOWN = "unknown"


def replacement_is_safe(cancel_outcome: CancelOutcome) -> bool:
    """A replacement is legal only after the previous order is confirmed gone."""
    return cancel_outcome is CancelOutcome.CONFIRMED_GONE


@dataclass
class KillSwitchLatch:
    """Serializable latch that cannot be reset while exposure remains."""

    active: bool = False
    reason: str = ""
    activated_at: float = 0.0
    activation_id: str = ""

    def activate(self, reason: str, *, now: float | None = None) -> str:
        if self.active:
            return self.activation_id
        if not reason.strip():
            raise SafetyContractError("kill-switch reason must not be empty")
        self.active = True
        self.reason = reason
        self.activated_at = time.time() if now is None else now
        payload = f"{self.reason}|{self.activated_at:.9f}"
        self.activation_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
        return self.activation_id

    def release(
        self,
        *,
        acknowledgement_id: str,
        exchange_position_base: Decimal,
        base_step: Decimal,
        exchange_open_order_count: int,
    ) -> None:
        if not self.active:
            return
        if acknowledgement_id != self.activation_id:
            raise SafetyContractError("kill-switch acknowledgement does not match")
        if base_step <= 0:
            raise SafetyContractError("base_step must be positive")
        if abs(exchange_position_base) > base_step:
            raise SafetyContractError("cannot release kill switch with open position")
        if exchange_open_order_count != 0:
            raise SafetyContractError("cannot release kill switch with open orders")
        self.active = False
        self.reason = ""
        self.activated_at = 0.0
        self.activation_id = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "KillSwitchLatch":
        latch = cls(
            active=bool(value.get("active", False)),
            reason=str(value.get("reason", "")),
            activated_at=float(value.get("activated_at", 0.0)),
            activation_id=str(value.get("activation_id", "")),
        )
        if latch.active and not (latch.reason and latch.activation_id):
            raise SafetyContractError("active kill-switch snapshot is incomplete")
        return latch
