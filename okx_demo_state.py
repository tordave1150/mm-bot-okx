"""Fail-closed persistent state for the isolated OKX demo runtime."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from okx_execution_safety import KillSwitchLatch, SafetyContractError


STATE_SCHEMA_VERSION = 1
DEMO_ENVIRONMENT = "OKX_DEMO"
MAXIMUM_STATE_AGE_MS = 15 * 60 * 1_000
MAXIMUM_STATE_FUTURE_SKEW_MS = 1_500


class DemoStateError(RuntimeError):
    """State is unavailable, corrupt, mismatched, or could not be persisted."""


@dataclass
class FillCursor:
    """Monotonic trade cursor with deterministic same-timestamp deduplication."""

    timestamp_ms: int = 0
    ids_at_timestamp: list[str] = field(default_factory=list)

    def select_new(self, trades: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen_at_cursor = set(self.ids_at_timestamp)
        ordered = sorted(
            trades,
            key=lambda row: (int(row.get("timestamp") or 0), str(row.get("id") or "")),
        )
        for trade in ordered:
            trade_id = str(trade.get("id") or "")
            timestamp = int(trade.get("timestamp") or 0)
            if not trade_id or timestamp <= 0:
                raise DemoStateError("trade lacks stable id/timestamp")
            if timestamp < self.timestamp_ms:
                continue
            if timestamp == self.timestamp_ms and trade_id in seen_at_cursor:
                continue
            selected.append(trade)
            if timestamp > self.timestamp_ms:
                self.timestamp_ms = timestamp
                self.ids_at_timestamp = [trade_id]
                seen_at_cursor = {trade_id}
            else:
                self.ids_at_timestamp.append(trade_id)
                seen_at_cursor.add(trade_id)
        self.ids_at_timestamp = sorted(set(self.ids_at_timestamp))
        return selected


@dataclass
class DemoRuntimeState:
    environment: str
    account_uid: str
    symbol: str
    market_fingerprint: str
    profile_binding_sha256: str
    session_id: str
    client_order_generation: int = 0
    owned_open_orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    fill_cursor: FillCursor = field(default_factory=FillCursor)
    inventory_btc: float = 0.0
    average_entry_price: float = 0.0
    gross_realized_pnl_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    net_realized_pnl_usdt: float = 0.0
    peak_equity_usdt: float = 0.0
    current_equity_usdt: float = 0.0
    kill_switch: KillSwitchLatch = field(default_factory=KillSwitchLatch)
    flatten_state: str = "IDLE"
    flatten_client_order_id: str = ""
    flatten_order_id: str = ""
    flatten_attempts: int = 0
    defensive_overlay_state: dict[str, Any] = field(default_factory=dict)
    updated_at_ms: int = 0
    schema_version: int = STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.environment != DEMO_ENVIRONMENT:
            raise DemoStateError("state environment is not OKX_DEMO")
        if not all((self.account_uid, self.symbol, self.market_fingerprint,
                    self.profile_binding_sha256, self.session_id)):
            raise DemoStateError("state identity binding is incomplete")
        if self.client_order_generation < 0:
            raise DemoStateError("negative client order generation")
        if self.flatten_state not in {"IDLE", "SUBMITTED", "CONFIRMED", "UNKNOWN"}:
            raise DemoStateError("unknown flatten state")
        if self.flatten_attempts < 0 or self.flatten_attempts > 1:
            raise DemoStateError("flatten attempts violate single-flight policy")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fill_cursor"] = asdict(self.fill_cursor)
        payload["kill_switch"] = self.kill_switch.to_dict()
        payload["updated_at_ms"] = int(time.time() * 1000)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DemoRuntimeState":
        if int(payload.get("schema_version", 0)) != STATE_SCHEMA_VERSION:
            raise DemoStateError("state schema mismatch")
        try:
            cursor = FillCursor(**payload.get("fill_cursor", {}))
            latch = KillSwitchLatch.from_dict(payload.get("kill_switch", {}))
        except (TypeError, ValueError, SafetyContractError) as exc:
            raise DemoStateError("invalid nested state") from exc
        values = dict(payload)
        values["fill_cursor"] = cursor
        values["kill_switch"] = latch
        try:
            return cls(**values)
        except (TypeError, ValueError) as exc:
            raise DemoStateError("invalid runtime state") from exc


class DemoStateStore:
    """Atomic JSON store whose errors propagate to the quote-halt boundary."""

    def __init__(self, path: Path):
        self.path = path

    def save(self, state: DemoRuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise DemoStateError(f"state save failed: {self.path}") from exc

    def load(
        self,
        *,
        account_uid: str,
        symbol: str,
        market_fingerprint: str,
        profile_binding_sha256: str,
        maximum_age_ms: int = MAXIMUM_STATE_AGE_MS,
        now_ms: int | None = None,
    ) -> DemoRuntimeState | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            state = DemoRuntimeState.from_dict(payload)
        except Exception as exc:
            if isinstance(exc, DemoStateError):
                raise
            raise DemoStateError(f"state load failed: {self.path}") from exc
        observed_now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        updated_at_ms = int(state.updated_at_ms)
        if maximum_age_ms <= 0:
            raise DemoStateError("state maximum age must be positive")
        if updated_at_ms <= 0:
            raise DemoStateError("state update timestamp is missing")
        if observed_now_ms - updated_at_ms > maximum_age_ms:
            raise DemoStateError("state snapshot is stale")
        if updated_at_ms - observed_now_ms > MAXIMUM_STATE_FUTURE_SKEW_MS:
            raise DemoStateError("state snapshot is future-dated")
        expected = {
            "environment": DEMO_ENVIRONMENT,
            "account_uid": account_uid,
            "symbol": symbol,
            "market_fingerprint": market_fingerprint,
            "profile_binding_sha256": profile_binding_sha256,
        }
        actual = {name: getattr(state, name) for name in expected}
        mismatches = [name for name in expected if actual[name] != expected[name]]
        if mismatches:
            raise DemoStateError("state identity mismatch: " + ",".join(mismatches))
        return state
