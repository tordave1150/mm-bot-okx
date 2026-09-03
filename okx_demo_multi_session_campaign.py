"""Fail-closed durable accounting for the OKX Demo economic soak campaign.

This module is deliberately transport-free.  It consumes sealed, sanitized
session evidence produced by a separately authorized Demo session, enforces the
frozen per-session and aggregate budgets, and appends source-bound records to a
hash-chained campaign registry.  Importing or using it never loads credentials
or creates, amends, or cancels an order.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from okx_fill_restart_validation import canonical_sha256


class CampaignError(RuntimeError):
    """Campaign state or evidence is invalid and must fail closed."""


class CampaignDecision(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_PRODUCTION_READ_ONLY_SHADOW = (
        "READY_FOR_PRODUCTION_READ_ONLY_SHADOW"
    )
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_READY = "NOT_READY"


CREATE_COUNTER_EXTENSION_FIELDS = frozenset({
    "normal_create_dispatches",
    "normal_create_acknowledgements",
    "normal_create_rejections",
    "normal_create_unresolved",
    "reconciles",
})
TERMINAL_SPECIAL_CLOSURE_EXTENSION_FIELDS = frozenset({
    "special_closed_causal_fills",
    "special_closed_markouts_usdt",
})
SESSION_EXTENSION_FIELDS = (
    CREATE_COUNTER_EXTENSION_FIELDS | TERMINAL_SPECIAL_CLOSURE_EXTENSION_FIELDS
)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CampaignError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise CampaignError(f"{name} is not finite")
    return result


def _require_sha256(value: object, name: str) -> str:
    result = str(value)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise CampaignError(f"{name} is not a lowercase SHA-256")
    return result


def _validate_special_closed_fill(value: object) -> str:
    if not isinstance(value, Mapping):
        raise CampaignError("terminal special-closed fill is not a mapping")
    raw = dict(value)
    required = {
        "trade_id", "fill_side", "fill_timestamp_ms", "defense_timestamp_ms",
        "reentry_timestamp_ms", "workoff_timestamp_ms",
        "maker_reentry_observed", "maker_workoff_observed",
        "immediate_taker_flatten", "inventory_before_btc",
        "inventory_after_btc", "causal_binding",
    }
    if set(raw) != required:
        raise CampaignError("terminal special-closed fill schema is incomplete")
    trade_id = str(raw["trade_id"])
    fill_side = str(raw["fill_side"])
    fill_timestamp_ms = int(raw["fill_timestamp_ms"])
    defense_timestamp_ms = int(raw["defense_timestamp_ms"])
    reentry_timestamp_ms = int(raw["reentry_timestamp_ms"])
    maker_reentry = raw["maker_reentry_observed"] is True
    if any((
        not trade_id,
        fill_side not in {"buy", "sell"},
        fill_timestamp_ms <= 0,
        defense_timestamp_ms < fill_timestamp_ms,
        raw["immediate_taker_flatten"] is not True,
        raw["maker_workoff_observed"] is not False,
        int(raw["workoff_timestamp_ms"]) != 0,
        maker_reentry and reentry_timestamp_ms < defense_timestamp_ms,
        not maker_reentry and reentry_timestamp_ms != 0,
    )):
        raise CampaignError("terminal special-closed fill attribution is invalid")
    for name in ("inventory_before_btc", "inventory_after_btc"):
        inventory = _decimal(raw[name], name)
        if abs(inventory) > Decimal("0.01"):
            raise CampaignError(f"{name} exceeds the frozen inventory budget")
    binding_value = raw["causal_binding"]
    if not isinstance(binding_value, Mapping):
        raise CampaignError("terminal special-closed causal binding is missing")
    binding = dict(binding_value)
    required_binding = {
        "fill_order_id", "fill_quantity_btc", "fill_price_usdt",
        "remaining_workoff_btc", "reentry_client_order_id", "reentry_side",
        "reentry_quantity_btc", "workoff_trade_ids", "workoff_order_ids",
        "matched_workoff_btc",
    }
    if set(binding) != required_binding:
        raise CampaignError("terminal special-closed causal binding is incomplete")
    fill_quantity = _decimal(binding["fill_quantity_btc"], "causal fill quantity")
    remaining = _decimal(binding["remaining_workoff_btc"], "causal remainder")
    matched = _decimal(binding["matched_workoff_btc"], "causal matched quantity")
    reentry_quantity = _decimal(
        binding["reentry_quantity_btc"], "causal reentry quantity"
    )
    workoff_trade_ids = binding["workoff_trade_ids"]
    workoff_order_ids = binding["workoff_order_ids"]
    workoff_ids_valid = all(
        isinstance(values, list)
        and all(isinstance(item, str) and item for item in values)
        and len(values) == len(set(values))
        for values in (workoff_trade_ids, workoff_order_ids)
    )
    partial_workoff_binding_valid = (
        (matched == 0 and workoff_trade_ids == [] and workoff_order_ids == [])
        or (
            matched > 0
            and bool(workoff_trade_ids)
            and bool(workoff_order_ids)
        )
    )
    if any((
        not binding["fill_order_id"],
        fill_quantity <= 0,
        fill_quantity > Decimal("0.01"),
        remaining <= 0,
        matched < 0,
        remaining + matched != fill_quantity,
        not workoff_ids_valid,
        not partial_workoff_binding_valid,
        maker_reentry and not binding["reentry_client_order_id"],
        maker_reentry
        and binding["reentry_side"]
        != ("sell" if fill_side == "buy" else "buy"),
        maker_reentry and not (Decimal("0") < reentry_quantity <= fill_quantity),
    )):
        raise CampaignError("terminal special-closed quantities do not reconcile")
    return trade_id


def _json_payload(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_json_payload(value))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CampaignError(f"artifact reuse refused: {path}") from exc


@dataclass(frozen=True)
class CampaignLimits:
    maximum_sessions: int = 12
    maximum_campaign_wall_ms: int = 6 * 60 * 60 * 1000
    maximum_campaign_normal_creates: int = 720
    aggregate_hard_loss_usdt: Decimal = Decimal("75")
    maximum_session_wall_ms: int = 30 * 60 * 1000
    maximum_session_normal_creates: int = 60
    maximum_session_read_retries: int = 3
    maximum_session_mutation_retries: int = 0
    maximum_inventory_btc: Decimal = Decimal("0.01")
    maximum_owned_bid: int = 1
    maximum_owned_ask: int = 1
    maximum_unresolved_flatten: int = 1
    session_soft_drawdown_usdt: Decimal = Decimal("22.50")
    session_hard_drawdown_usdt: Decimal = Decimal("37.50")
    minimum_normal_fills: int = 24
    minimum_bid_fills: int = 8
    minimum_ask_fills: int = 8
    minimum_fill_balance: Decimal = Decimal("0.60")
    minimum_fifo_round_trips: int = 8
    maximum_special_flatten_fraction: Decimal = Decimal("0.20")

    def validate(self) -> None:
        expected = CampaignLimits()
        if self != expected:
            raise CampaignError("campaign risk or evidence limits drifted")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "maximum_sessions": self.maximum_sessions,
            "maximum_campaign_wall_ms": self.maximum_campaign_wall_ms,
            "maximum_campaign_normal_creates": self.maximum_campaign_normal_creates,
            "aggregate_hard_loss_usdt": str(self.aggregate_hard_loss_usdt),
            "maximum_session_wall_ms": self.maximum_session_wall_ms,
            "maximum_session_normal_creates": self.maximum_session_normal_creates,
            "maximum_session_read_retries": self.maximum_session_read_retries,
            "maximum_session_mutation_retries": self.maximum_session_mutation_retries,
            "maximum_inventory_btc": str(self.maximum_inventory_btc),
            "maximum_owned_bid": self.maximum_owned_bid,
            "maximum_owned_ask": self.maximum_owned_ask,
            "maximum_unresolved_flatten": self.maximum_unresolved_flatten,
            "session_soft_drawdown_usdt": str(self.session_soft_drawdown_usdt),
            "session_hard_drawdown_usdt": str(self.session_hard_drawdown_usdt),
            "minimum_normal_fills": self.minimum_normal_fills,
            "minimum_bid_fills": self.minimum_bid_fills,
            "minimum_ask_fills": self.minimum_ask_fills,
            "minimum_fill_balance": str(self.minimum_fill_balance),
            "minimum_fifo_round_trips": self.minimum_fifo_round_trips,
            "maximum_special_flatten_fraction": str(
                self.maximum_special_flatten_fraction
            ),
        }
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CampaignLimits":
        result = cls(
            maximum_sessions=int(value["maximum_sessions"]),
            maximum_campaign_wall_ms=int(value["maximum_campaign_wall_ms"]),
            maximum_campaign_normal_creates=int(
                value["maximum_campaign_normal_creates"]
            ),
            aggregate_hard_loss_usdt=_decimal(
                value["aggregate_hard_loss_usdt"], "aggregate_hard_loss_usdt"
            ),
            maximum_session_wall_ms=int(value["maximum_session_wall_ms"]),
            maximum_session_normal_creates=int(
                value["maximum_session_normal_creates"]
            ),
            maximum_session_read_retries=int(
                value["maximum_session_read_retries"]
            ),
            maximum_session_mutation_retries=int(
                value["maximum_session_mutation_retries"]
            ),
            maximum_inventory_btc=_decimal(
                value["maximum_inventory_btc"], "maximum_inventory_btc"
            ),
            maximum_owned_bid=int(value["maximum_owned_bid"]),
            maximum_owned_ask=int(value["maximum_owned_ask"]),
            maximum_unresolved_flatten=int(value["maximum_unresolved_flatten"]),
            session_soft_drawdown_usdt=_decimal(
                value["session_soft_drawdown_usdt"], "session_soft_drawdown_usdt"
            ),
            session_hard_drawdown_usdt=_decimal(
                value["session_hard_drawdown_usdt"], "session_hard_drawdown_usdt"
            ),
            minimum_normal_fills=int(value["minimum_normal_fills"]),
            minimum_bid_fills=int(value["minimum_bid_fills"]),
            minimum_ask_fills=int(value["minimum_ask_fills"]),
            minimum_fill_balance=_decimal(
                value["minimum_fill_balance"], "minimum_fill_balance"
            ),
            minimum_fifo_round_trips=int(value["minimum_fifo_round_trips"]),
            maximum_special_flatten_fraction=_decimal(
                value["maximum_special_flatten_fraction"],
                "maximum_special_flatten_fraction",
            ),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class CausalReentryEvidence:
    trade_id: str
    fill_side: str
    fill_timestamp_ms: int
    defense_timestamp_ms: int
    reentry_timestamp_ms: int
    workoff_timestamp_ms: int
    maker_reentry_observed: bool
    maker_workoff_observed: bool
    immediate_taker_flatten: bool
    inventory_before_btc: Decimal
    inventory_after_btc: Decimal
    causal_binding: Mapping[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.trade_id or self.fill_side not in {"buy", "sell"}:
            raise CampaignError("causal re-entry fill identity is invalid")
        if self.fill_timestamp_ms <= 0:
            raise CampaignError("causal re-entry fill timestamp is invalid")
        if self.defense_timestamp_ms < self.fill_timestamp_ms:
            raise CampaignError("inventory defense precedes its fill")
        if not (self.maker_reentry_observed or self.maker_workoff_observed):
            raise CampaignError("causal re-entry lacks maker continuation")
        if self.immediate_taker_flatten:
            raise CampaignError("immediate taker flatten cannot prove causal re-entry")
        if self.maker_reentry_observed:
            if self.reentry_timestamp_ms < self.defense_timestamp_ms:
                raise CampaignError("maker re-entry precedes inventory defense")
        elif self.reentry_timestamp_ms != 0:
            raise CampaignError("unobserved maker re-entry has a timestamp")
        if self.maker_workoff_observed:
            if self.workoff_timestamp_ms < self.defense_timestamp_ms:
                raise CampaignError("maker work-off precedes inventory defense")
        elif self.workoff_timestamp_ms != 0:
            raise CampaignError("unobserved maker work-off has a timestamp")
        for name, value in (
            ("inventory_before_btc", self.inventory_before_btc),
            ("inventory_after_btc", self.inventory_after_btc),
        ):
            if not value.is_finite() or abs(value) > Decimal("0.01"):
                raise CampaignError(f"{name} exceeds the frozen inventory budget")
        if self.causal_binding:
            binding = dict(self.causal_binding)
            required = {
                "fill_order_id",
                "fill_quantity_btc",
                "fill_price_usdt",
                "remaining_workoff_btc",
                "reentry_client_order_id",
                "reentry_side",
                "reentry_quantity_btc",
                "workoff_trade_ids",
                "workoff_order_ids",
                "matched_workoff_btc",
            }
            if set(binding) != required:
                raise CampaignError("causal binding schema is incomplete")
            fill_quantity = _decimal(
                binding["fill_quantity_btc"], "causal fill quantity"
            )
            remaining = _decimal(
                binding["remaining_workoff_btc"], "causal workoff remainder"
            )
            reentry_quantity = _decimal(
                binding["reentry_quantity_btc"], "causal reentry quantity"
            )
            matched = _decimal(
                binding["matched_workoff_btc"], "causal matched quantity"
            )
            if (
                not binding["fill_order_id"]
                or fill_quantity <= 0
                or fill_quantity > Decimal("0.01")
                or remaining < 0
                or matched < 0
                or remaining + matched != fill_quantity
            ):
                raise CampaignError("causal fill/workoff quantities do not reconcile")
            if self.maker_reentry_observed and (
                not binding["reentry_client_order_id"]
                or binding["reentry_side"]
                != ("sell" if self.fill_side == "buy" else "buy")
                or reentry_quantity <= 0
                or reentry_quantity > fill_quantity
            ):
                raise CampaignError("causal re-entry order binding is invalid")
            trade_ids = binding["workoff_trade_ids"]
            order_ids = binding["workoff_order_ids"]
            if not isinstance(trade_ids, list) or not isinstance(order_ids, list):
                raise CampaignError("causal workoff identities are invalid")
            if len(trade_ids) != len(order_ids) or len(trade_ids) != len(set(trade_ids)):
                raise CampaignError("causal workoff identities do not reconcile")
            if self.maker_workoff_observed and (
                remaining != 0 or not trade_ids or not all(order_ids)
            ):
                raise CampaignError("causal maker workoff binding is incomplete")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "trade_id": self.trade_id,
            "fill_side": self.fill_side,
            "fill_timestamp_ms": self.fill_timestamp_ms,
            "defense_timestamp_ms": self.defense_timestamp_ms,
            "reentry_timestamp_ms": self.reentry_timestamp_ms,
            "workoff_timestamp_ms": self.workoff_timestamp_ms,
            "maker_reentry_observed": self.maker_reentry_observed,
            "maker_workoff_observed": self.maker_workoff_observed,
            "immediate_taker_flatten": self.immediate_taker_flatten,
            "inventory_before_btc": str(self.inventory_before_btc),
            "inventory_after_btc": str(self.inventory_after_btc),
        }
        if self.causal_binding:
            payload["causal_binding"] = dict(self.causal_binding)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CausalReentryEvidence":
        result = cls(
            trade_id=str(value["trade_id"]),
            fill_side=str(value["fill_side"]),
            fill_timestamp_ms=int(value["fill_timestamp_ms"]),
            defense_timestamp_ms=int(value["defense_timestamp_ms"]),
            reentry_timestamp_ms=int(value["reentry_timestamp_ms"]),
            workoff_timestamp_ms=int(value["workoff_timestamp_ms"]),
            maker_reentry_observed=bool(value["maker_reentry_observed"]),
            maker_workoff_observed=bool(value["maker_workoff_observed"]),
            immediate_taker_flatten=bool(value["immediate_taker_flatten"]),
            inventory_before_btc=_decimal(
                value["inventory_before_btc"], "inventory_before_btc"
            ),
            inventory_after_btc=_decimal(
                value["inventory_after_btc"], "inventory_after_btc"
            ),
            causal_binding=(
                dict(value["causal_binding"])
                if isinstance(value.get("causal_binding"), Mapping)
                else {}
            ),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class SessionEvidence:
    session_id: str
    run_id: str
    source_sha256: str
    evidence_sha256: str
    started_at_ms: int
    ended_at_ms: int
    normal_creates: int
    normal_cancels: int
    order_amends: int
    self_trades: int
    read_retries: int
    mutation_retries: int
    maximum_owned_bid_observed: int
    maximum_owned_ask_observed: int
    maximum_inventory_btc_observed: Decimal
    maximum_drawdown_usdt: Decimal
    hard_kill_triggered: bool
    normal_bid_fills: int
    normal_ask_fills: int
    normal_fifo_round_trips: int
    realized_spread_pnl_usdt: Decimal
    inventory_pnl_usdt: Decimal
    normal_gross_pnl_usdt: Decimal
    normal_fees_usdt: Decimal
    normal_net_pnl_usdt: Decimal
    special_fill_count: int
    special_gross_pnl_usdt: Decimal
    special_fees_usdt: Decimal
    special_net_pnl_usdt: Decimal
    aggregate_gross_pnl_usdt: Decimal
    aggregate_fees_usdt: Decimal
    aggregate_net_pnl_usdt: Decimal
    flatten_dispatches: int
    quote_mode_ticks: int
    quote_mode_counters: Mapping[str, int]
    unclassified_quote_mode_ticks: int
    markouts_usdt: tuple[Decimal, ...]
    causal_reentry: tuple[CausalReentryEvidence, ...]
    fill_cursor_sha256: str
    final_position_btc: Decimal
    final_open_orders: int
    terminal_account_snapshots: int
    terminal_reconciled: bool
    pending_intent: bool
    ambiguous_intent: bool
    safety_violations: tuple[str, ...]
    live_endpoint_attempts: int
    live_orders: int
    accounting_audit: Mapping[str, object] = field(default_factory=dict)
    control_audit: Mapping[str, object] = field(default_factory=dict)
    extension_fields: Mapping[str, object] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return self.ended_at_ms - self.started_at_ms

    @property
    def normal_fill_count(self) -> int:
        return self.normal_bid_fills + self.normal_ask_fills

    @property
    def has_special_flatten_dispatch(self) -> bool:
        """Return the campaign-rate unit: one session with a flatten dispatch.

        ``special_fill_count`` is a trade-level accounting value.  A single
        reduce-only flatten may produce multiple fills, so it must never be
        summed or otherwise used as the campaign's session-level flatten rate.
        """
        return self.flatten_dispatches > 0

    @property
    def terminal_special_closed_fill_count(self) -> int:
        rows = self.extension_fields.get("special_closed_causal_fills", [])
        return len(rows) if isinstance(rows, Sequence) else 0

    @property
    def terminal_special_closed_markout_count(self) -> int:
        rows = self.extension_fields.get("special_closed_markouts_usdt", [])
        return len(rows) if isinstance(rows, Sequence) else 0

    @property
    def is_safe(self) -> bool:
        return not any((
            self.hard_kill_triggered,
            self.safety_violations,
            self.pending_intent,
            self.ambiguous_intent,
            self.live_endpoint_attempts,
            self.live_orders,
            self.final_position_btc,
            self.final_open_orders,
            not self.terminal_reconciled,
        ))

    def create_counters(self) -> dict[str, object]:
        extensions = dict(self.extension_fields)
        if extensions:
            return {
                "normal_create_dispatches": int(
                    extensions["normal_create_dispatches"]
                ),
                "normal_create_acknowledgements": int(
                    extensions["normal_create_acknowledgements"]
                ),
                "normal_create_rejections": int(
                    extensions["normal_create_rejections"]
                ),
                "normal_create_unresolved": int(
                    extensions["normal_create_unresolved"]
                ),
                "reconciles": extensions["reconciles"] is True,
            }
        control = dict(self.control_audit)
        audit = control.get("create_counter_audit")
        if isinstance(audit, Mapping):
            return {
                "normal_create_dispatches": int(
                    audit.get("normal_create_dispatches", self.normal_creates)
                ),
                "normal_create_acknowledgements": int(
                    audit.get("normal_create_acknowledgements", self.normal_creates)
                ),
                "normal_create_rejections": int(
                    audit.get("normal_create_rejections", 0)
                ),
                "normal_create_unresolved": int(
                    audit.get("normal_create_unresolved", 0)
                ),
                "reconciles": audit.get("reconciles") is True,
            }
        return {
            "normal_create_dispatches": self.normal_creates,
            "normal_create_acknowledgements": self.normal_creates,
            "normal_create_rejections": 0,
            "normal_create_unresolved": 0,
            "reconciles": True,
        }

    def validate(self, limits: CampaignLimits) -> None:
        limits.validate()
        if not self.session_id.startswith("economic:"):
            raise CampaignError("economic session identity is invalid")
        if not self.run_id.startswith("economic-"):
            raise CampaignError("economic run identity is invalid")
        _require_sha256(self.source_sha256, "source_sha256")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        _require_sha256(self.fill_cursor_sha256, "fill_cursor_sha256")
        if self.started_at_ms <= 0 or not 0 < self.duration_ms <= limits.maximum_session_wall_ms:
            raise CampaignError("session wall budget is invalid")
        bounded_counts = {
            "normal_creates": (self.normal_creates, limits.maximum_session_normal_creates),
            "normal_cancels": (self.normal_cancels, limits.maximum_session_normal_creates),
            "order_amends": (self.order_amends, 0),
            "self_trades": (self.self_trades, 0),
            "read_retries": (self.read_retries, limits.maximum_session_read_retries),
            "mutation_retries": (
                self.mutation_retries, limits.maximum_session_mutation_retries
            ),
            "maximum_owned_bid_observed": (
                self.maximum_owned_bid_observed, limits.maximum_owned_bid
            ),
            "maximum_owned_ask_observed": (
                self.maximum_owned_ask_observed, limits.maximum_owned_ask
            ),
            "flatten_dispatches": (
                self.flatten_dispatches, limits.maximum_unresolved_flatten
            ),
            "live_endpoint_attempts": (self.live_endpoint_attempts, 0),
            "live_orders": (self.live_orders, 0),
        }
        for name, (value, maximum) in bounded_counts.items():
            if value < 0 or value > maximum:
                raise CampaignError(f"{name} exceeds its frozen budget")
        if (
            self.maximum_inventory_btc_observed < 0
            or self.maximum_inventory_btc_observed > limits.maximum_inventory_btc
        ):
            raise CampaignError("observed inventory exceeds its frozen budget")
        if (
            self.maximum_drawdown_usdt < 0
            or self.maximum_drawdown_usdt > limits.session_hard_drawdown_usdt
        ):
            raise CampaignError("session drawdown exceeds its hard boundary")
        nonnegative = (
            self.normal_bid_fills,
            self.normal_ask_fills,
            self.normal_fifo_round_trips,
            self.special_fill_count,
            self.quote_mode_ticks,
            self.unclassified_quote_mode_ticks,
            self.final_open_orders,
            self.terminal_account_snapshots,
        )
        if any(value < 0 for value in nonnegative):
            raise CampaignError("session counter regression")
        # Legacy evidence did not describe FIFO identity reuse, so retain its
        # conservative pair-of-fills bound.  Successor evidence carries an
        # extended audit below: one exit trade may legitimately close several
        # partial FIFO entry lots, making ``fill_count // 2`` incorrect.
        extended_fifo_audit = (
            "normal_fifo_unique_completed_entry_fills"
            in self.accounting_audit
        )
        if (
            not extended_fifo_audit
            and self.normal_fifo_round_trips > self.normal_fill_count // 2
        ):
            raise CampaignError("FIFO round-trip counter exceeds fill evidence")
        if self.realized_spread_pnl_usdt + self.inventory_pnl_usdt != self.normal_gross_pnl_usdt:
            raise CampaignError("spread/inventory attribution does not equal normal gross PnL")
        if self.normal_gross_pnl_usdt - self.normal_fees_usdt != self.normal_net_pnl_usdt:
            raise CampaignError("normal fee attribution does not reconcile")
        if self.special_gross_pnl_usdt - self.special_fees_usdt != self.special_net_pnl_usdt:
            raise CampaignError("special fee attribution does not reconcile")
        if self.normal_gross_pnl_usdt + self.special_gross_pnl_usdt != self.aggregate_gross_pnl_usdt:
            raise CampaignError("aggregate gross PnL does not reconcile")
        if self.normal_fees_usdt + self.special_fees_usdt != self.aggregate_fees_usdt:
            raise CampaignError("aggregate fees do not reconcile")
        if self.normal_net_pnl_usdt + self.special_net_pnl_usdt != self.aggregate_net_pnl_usdt:
            raise CampaignError("aggregate net PnL does not reconcile")
        if any(value < 0 for value in (self.normal_fees_usdt, self.special_fees_usdt)):
            raise CampaignError("fee costs must be non-negative")
        counters = dict(self.quote_mode_counters)
        if any(not name or value < 0 for name, value in counters.items()):
            raise CampaignError("quote-mode counter is invalid")
        if sum(counters.values()) + self.unclassified_quote_mode_ticks != self.quote_mode_ticks:
            raise CampaignError("quote-mode counters do not reconcile")
        extensions = dict(self.extension_fields)
        has_special_closed = any(
            name in extensions for name in TERMINAL_SPECIAL_CLOSURE_EXTENSION_FIELDS
        )
        if has_special_closed:
            if not TERMINAL_SPECIAL_CLOSURE_EXTENSION_FIELDS.issubset(extensions):
                raise CampaignError("terminal special-closure evidence is incomplete")
            special_rows = extensions["special_closed_causal_fills"]
            special_markouts = extensions["special_closed_markouts_usdt"]
            if (
                not isinstance(special_rows, Sequence)
                or isinstance(special_rows, (str, bytes))
                or not isinstance(special_markouts, Sequence)
                or isinstance(special_markouts, (str, bytes))
            ):
                raise CampaignError("terminal special-closure evidence is not a sequence")
            special_trade_ids = [
                _validate_special_closed_fill(item) for item in special_rows
            ]
            parsed_special_markouts = tuple(
                _decimal(item, "terminal special-closed markout")
                for item in special_markouts
            )
            if len(special_trade_ids) != len(parsed_special_markouts):
                raise CampaignError("terminal special-closed markouts are incomplete")
            if len(self.markouts_usdt) != len(self.causal_reentry):
                raise CampaignError("causal maker markouts are incomplete")
            if len(self.causal_reentry) + len(special_trade_ids) != self.normal_fill_count:
                raise CampaignError("normal fill attribution does not reconcile")
        else:
            special_rows = []
            special_trade_ids = []
            if len(self.markouts_usdt) != self.normal_fill_count:
                raise CampaignError("markout evidence count does not equal normal fills")
            if len(self.causal_reentry) != self.normal_fill_count:
                raise CampaignError("causal re-entry evidence count does not equal normal fills")
        trade_ids = [item.trade_id for item in self.causal_reentry]
        all_trade_ids = trade_ids + special_trade_ids
        if len(all_trade_ids) != len(set(all_trade_ids)):
            raise CampaignError("normal fill attribution trade identity is duplicated")
        for item in self.causal_reentry:
            item.validate()
        if self.accounting_audit:
            audit = dict(self.accounting_audit)
            legacy_required = {
                "normal_fifo_match_fragments",
                "normal_fifo_completed_lots",
                "normal_inventory_cycles",
                "normal_matched_fragment_quantity_btc",
                "normal_completed_lot_quantity_btc",
                "open_fifo_lot_quantity_btc",
            }
            extended_required = legacy_required | {
                "normal_fifo_unique_completed_entry_fills",
                "normal_fifo_unique_exit_fills",
                "normal_fifo_evidence_trade_ids",
                "normal_fifo_exit_fill_reuse_count",
                "normal_fifo_multi_partial_completed_lots",
                "normal_fifo_multi_lot_exit_fills",
            }
            if set(audit) not in (legacy_required, extended_required):
                raise CampaignError("FIFO accounting audit schema is incomplete")
            fragments = int(audit["normal_fifo_match_fragments"])
            completed = int(audit["normal_fifo_completed_lots"])
            cycles = int(audit["normal_inventory_cycles"])
            matched_quantity = _decimal(
                audit["normal_matched_fragment_quantity_btc"],
                "FIFO matched fragment quantity",
            )
            completed_quantity = _decimal(
                audit["normal_completed_lot_quantity_btc"],
                "FIFO completed lot quantity",
            )
            open_quantity = _decimal(
                audit["open_fifo_lot_quantity_btc"], "FIFO open lot quantity"
            )
            if any(value < 0 for value in (
                fragments, completed, cycles,
                matched_quantity, completed_quantity, open_quantity,
            )):
                raise CampaignError("FIFO accounting audit counter regression")
            if (
                completed != self.normal_fifo_round_trips
                or fragments < completed
                or completed_quantity > matched_quantity
            ):
                raise CampaignError("FIFO fragment/lot counters do not reconcile")
            if set(audit) == extended_required:
                unique_entries = int(
                    audit["normal_fifo_unique_completed_entry_fills"]
                )
                unique_exits = int(audit["normal_fifo_unique_exit_fills"])
                evidence_trade_ids = int(audit["normal_fifo_evidence_trade_ids"])
                exit_reuse = int(audit["normal_fifo_exit_fill_reuse_count"])
                multi_partial_lots = int(
                    audit["normal_fifo_multi_partial_completed_lots"]
                )
                multi_lot_exits = int(audit["normal_fifo_multi_lot_exit_fills"])
                if any(value < 0 for value in (
                    unique_entries,
                    unique_exits,
                    evidence_trade_ids,
                    exit_reuse,
                    multi_partial_lots,
                    multi_lot_exits,
                )):
                    raise CampaignError("FIFO identity audit counter regression")
                if any((
                    unique_entries != completed,
                    completed > 0 and completed >= self.normal_fill_count,
                    completed > 0 and unique_exits == 0,
                    unique_exits > fragments,
                    evidence_trade_ids < max(unique_entries, unique_exits),
                    evidence_trade_ids > self.normal_fill_count,
                    evidence_trade_ids > unique_entries + unique_exits,
                    exit_reuse != fragments - unique_exits,
                    multi_partial_lots > completed,
                    multi_lot_exits > unique_exits,
                    cycles > completed,
                    completed == 0 and completed_quantity != 0,
                )):
                    raise CampaignError("FIFO identity/lot counters do not reconcile")
                control = dict(self.control_audit)
                if control:
                    bid_quantity = _decimal(
                        control.get("normal_bid_fill_quantity_btc", "-1"),
                        "normal bid fill quantity",
                    )
                    ask_quantity = _decimal(
                        control.get("normal_ask_fill_quantity_btc", "-1"),
                        "normal ask fill quantity",
                    )
                    if completed_quantity > min(bid_quantity, ask_quantity):
                        raise CampaignError(
                            "FIFO completed quantity exceeds opposing fill evidence"
                        )
        if self.control_audit:
            control = dict(self.control_audit)
            required = {
                "session_phase",
                "create_budget_partition",
                "placement_reason_counters",
                "normal_bid_fill_quantity_btc",
                "normal_ask_fill_quantity_btc",
            }
            allowed = required | {
                "create_counter_audit",
                "sample_efficiency_policy",
            }
            if not required.issubset(control) or set(control) - allowed:
                raise CampaignError("economic control audit schema is incomplete")
            partition = control["create_budget_partition"]
            reasons = control["placement_reason_counters"]
            if not isinstance(partition, Mapping) or not isinstance(reasons, Mapping):
                raise CampaignError("economic control audit mapping is invalid")
            if (
                int(partition.get("admission_create_cap", 0)) != 48
                or int(partition.get("workoff_create_reserve", 0)) != 12
                or int(partition.get("total_create_cap", 0)) != 60
            ):
                raise CampaignError("economic create budget partition drift")
            if control["session_phase"] != "TERMINAL":
                raise CampaignError("economic session did not reach terminal phase")
            if any(int(value) < 0 for value in reasons.values()):
                raise CampaignError("placement reason counter regression")
            if "sample_efficiency_policy" in control:
                policy = control["sample_efficiency_policy"]
                legacy_policy = {
                    "minimum_half_spread_bps": "4.0",
                    "maker_fee_rate": "0.0002",
                    "fee_edge_safety_buffer_usdt": "0.01",
                    "balanced_retention_threshold_ticks": 10,
                    "defense_retention_threshold_ticks": 20,
                    "draining_workoff_retention_threshold_ticks": 5,
                    "admission_create_cap": 48,
                    "workoff_create_reserve": 12,
                    "total_create_cap": 60,
                    "risk_expansion": False,
                }
                v2_policy = {
                    **legacy_policy,
                    "draining_workoff_max_quote_observations": 6,
                    "draining_workoff_max_refreshes": 3,
                }
                if not isinstance(policy, Mapping) or (
                    dict(policy) != legacy_policy and dict(policy) != v2_policy
                ):
                    raise CampaignError("sample-efficiency policy audit drift")
            bid_quantity = _decimal(
                control["normal_bid_fill_quantity_btc"], "normal bid fill quantity"
            )
            ask_quantity = _decimal(
                control["normal_ask_fill_quantity_btc"], "normal ask fill quantity"
            )
            causal_bid_quantity = sum(
                (
                    _decimal(
                        item.causal_binding.get("fill_quantity_btc", "0"),
                        "causal bid fill quantity",
                    )
                    for item in self.causal_reentry
                    if item.fill_side == "buy"
                ),
                Decimal("0"),
            )
            causal_ask_quantity = sum(
                (
                    _decimal(
                        item.causal_binding.get("fill_quantity_btc", "0"),
                        "causal ask fill quantity",
                    )
                    for item in self.causal_reentry
                    if item.fill_side == "sell"
                ),
                Decimal("0"),
            )
            special_bid_quantity = sum(
                (
                    _decimal(
                        dict(item["causal_binding"])["fill_quantity_btc"],
                        "terminal special-closed bid fill quantity",
                    )
                    for item in special_rows
                    if item["fill_side"] == "buy"
                ),
                Decimal("0"),
            )
            special_ask_quantity = sum(
                (
                    _decimal(
                        dict(item["causal_binding"])["fill_quantity_btc"],
                        "terminal special-closed ask fill quantity",
                    )
                    for item in special_rows
                    if item["fill_side"] == "sell"
                ),
                Decimal("0"),
            )
            if (
                bid_quantity < 0
                or ask_quantity < 0
                or bid_quantity != causal_bid_quantity + special_bid_quantity
                or ask_quantity != causal_ask_quantity + special_ask_quantity
            ):
                raise CampaignError("fill quantity counters do not reconcile")
            if "create_counter_audit" in control:
                create_audit = control["create_counter_audit"]
                if not isinstance(create_audit, Mapping):
                    raise CampaignError("create counter audit is invalid")
                dispatches = int(create_audit.get("normal_create_dispatches", -1))
                acknowledgements = int(
                    create_audit.get("normal_create_acknowledgements", -1)
                )
                rejections = int(create_audit.get("normal_create_rejections", -1))
                unresolved = int(create_audit.get("normal_create_unresolved", -1))
                if any(value < 0 for value in (
                    dispatches, acknowledgements, rejections, unresolved,
                )) or any((
                    dispatches != self.normal_creates,
                    dispatches != acknowledgements + rejections + unresolved,
                    unresolved != 0,
                    create_audit.get("mutation_retries") != 0,
                    create_audit.get("reconciles") is not True,
                )):
                    raise CampaignError("create dispatch/ack counters do not reconcile")
        extensions = dict(self.extension_fields)
        unknown_extensions = set(extensions) - SESSION_EXTENSION_FIELDS
        create_extensions_present = any(
            name in extensions for name in CREATE_COUNTER_EXTENSION_FIELDS
        )
        if unknown_extensions:
            raise CampaignError("session extension schema contains unknown fields")
        if (
            create_extensions_present
            and not CREATE_COUNTER_EXTENSION_FIELDS.issubset(extensions)
        ):
            raise CampaignError("create counter extension schema is incomplete")
        create_counters = self.create_counters()
        dispatches = int(create_counters["normal_create_dispatches"])
        acknowledgements = int(
            create_counters["normal_create_acknowledgements"]
        )
        rejections = int(create_counters["normal_create_rejections"])
        unresolved = int(create_counters["normal_create_unresolved"])
        if any(value < 0 for value in (
            dispatches, acknowledgements, rejections, unresolved,
        )) or any((
            dispatches != self.normal_creates,
            dispatches != acknowledgements + rejections + unresolved,
            unresolved != 0,
            create_counters["reconciles"] is not True,
        )):
            raise CampaignError("create counter extension does not reconcile")
        if self.final_position_btc != 0 or self.final_open_orders != 0:
            raise CampaignError("session terminal account is not flat and empty")
        if self.terminal_account_snapshots != 2 or not self.terminal_reconciled:
            raise CampaignError("session terminal reconciliation is incomplete")

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "source_sha256": self.source_sha256,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "normal_creates": self.normal_creates,
            "normal_cancels": self.normal_cancels,
            "order_amends": self.order_amends,
            "self_trades": self.self_trades,
            "read_retries": self.read_retries,
            "mutation_retries": self.mutation_retries,
            "maximum_owned_bid_observed": self.maximum_owned_bid_observed,
            "maximum_owned_ask_observed": self.maximum_owned_ask_observed,
            "maximum_inventory_btc_observed": str(self.maximum_inventory_btc_observed),
            "maximum_drawdown_usdt": str(self.maximum_drawdown_usdt),
            "hard_kill_triggered": self.hard_kill_triggered,
            "normal_bid_fills": self.normal_bid_fills,
            "normal_ask_fills": self.normal_ask_fills,
            "normal_fifo_round_trips": self.normal_fifo_round_trips,
            "realized_spread_pnl_usdt": str(self.realized_spread_pnl_usdt),
            "inventory_pnl_usdt": str(self.inventory_pnl_usdt),
            "normal_gross_pnl_usdt": str(self.normal_gross_pnl_usdt),
            "normal_fees_usdt": str(self.normal_fees_usdt),
            "normal_net_pnl_usdt": str(self.normal_net_pnl_usdt),
            "special_fill_count": self.special_fill_count,
            "special_gross_pnl_usdt": str(self.special_gross_pnl_usdt),
            "special_fees_usdt": str(self.special_fees_usdt),
            "special_net_pnl_usdt": str(self.special_net_pnl_usdt),
            "aggregate_gross_pnl_usdt": str(self.aggregate_gross_pnl_usdt),
            "aggregate_fees_usdt": str(self.aggregate_fees_usdt),
            "aggregate_net_pnl_usdt": str(self.aggregate_net_pnl_usdt),
            "flatten_dispatches": self.flatten_dispatches,
            "quote_mode_ticks": self.quote_mode_ticks,
            "quote_mode_counters": dict(sorted(self.quote_mode_counters.items())),
            "unclassified_quote_mode_ticks": self.unclassified_quote_mode_ticks,
            "markouts_usdt": [str(value) for value in self.markouts_usdt],
            "causal_reentry": [item.to_dict() for item in self.causal_reentry],
            "fill_cursor_sha256": self.fill_cursor_sha256,
            "final_position_btc": str(self.final_position_btc),
            "final_open_orders": self.final_open_orders,
            "terminal_account_snapshots": self.terminal_account_snapshots,
            "terminal_reconciled": self.terminal_reconciled,
            "pending_intent": self.pending_intent,
            "ambiguous_intent": self.ambiguous_intent,
            "safety_violations": list(self.safety_violations),
            "live_endpoint_attempts": self.live_endpoint_attempts,
            "live_orders": self.live_orders,
        }
        if self.accounting_audit:
            payload["accounting_audit"] = dict(self.accounting_audit)
        if self.control_audit:
            payload["control_audit"] = dict(self.control_audit)
        payload.update(self.extension_fields)
        return payload

    def to_dict(self) -> dict[str, object]:
        return {**self.payload(), "evidence_sha256": self.evidence_sha256}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object], limits: CampaignLimits | None = None
    ) -> "SessionEvidence":
        raw = dict(value)
        evidence_sha256 = _require_sha256(
            raw.pop("evidence_sha256", ""), "evidence_sha256"
        )
        if canonical_sha256(raw) != evidence_sha256:
            raise CampaignError("session evidence seal mismatch")
        counters_value = raw["quote_mode_counters"]
        if not isinstance(counters_value, Mapping):
            raise CampaignError("quote-mode counters are not a mapping")
        causal_value = raw["causal_reentry"]
        if not isinstance(causal_value, Sequence) or isinstance(causal_value, (str, bytes)):
            raise CampaignError("causal re-entry evidence is not a sequence")
        markouts_value = raw["markouts_usdt"]
        if not isinstance(markouts_value, Sequence) or isinstance(markouts_value, (str, bytes)):
            raise CampaignError("markout evidence is not a sequence")
        safety_value = raw["safety_violations"]
        if not isinstance(safety_value, Sequence) or isinstance(safety_value, (str, bytes)):
            raise CampaignError("safety violations are not a sequence")
        result = cls(
            session_id=str(raw["session_id"]),
            run_id=str(raw["run_id"]),
            source_sha256=str(raw["source_sha256"]),
            evidence_sha256=evidence_sha256,
            started_at_ms=int(raw["started_at_ms"]),
            ended_at_ms=int(raw["ended_at_ms"]),
            normal_creates=int(raw["normal_creates"]),
            normal_cancels=int(raw["normal_cancels"]),
            order_amends=int(raw["order_amends"]),
            self_trades=int(raw["self_trades"]),
            read_retries=int(raw["read_retries"]),
            mutation_retries=int(raw["mutation_retries"]),
            maximum_owned_bid_observed=int(raw["maximum_owned_bid_observed"]),
            maximum_owned_ask_observed=int(raw["maximum_owned_ask_observed"]),
            maximum_inventory_btc_observed=_decimal(
                raw["maximum_inventory_btc_observed"],
                "maximum_inventory_btc_observed",
            ),
            maximum_drawdown_usdt=_decimal(
                raw["maximum_drawdown_usdt"], "maximum_drawdown_usdt"
            ),
            hard_kill_triggered=bool(raw["hard_kill_triggered"]),
            normal_bid_fills=int(raw["normal_bid_fills"]),
            normal_ask_fills=int(raw["normal_ask_fills"]),
            normal_fifo_round_trips=int(raw["normal_fifo_round_trips"]),
            realized_spread_pnl_usdt=_decimal(
                raw["realized_spread_pnl_usdt"], "realized_spread_pnl_usdt"
            ),
            inventory_pnl_usdt=_decimal(
                raw["inventory_pnl_usdt"], "inventory_pnl_usdt"
            ),
            normal_gross_pnl_usdt=_decimal(
                raw["normal_gross_pnl_usdt"], "normal_gross_pnl_usdt"
            ),
            normal_fees_usdt=_decimal(raw["normal_fees_usdt"], "normal_fees_usdt"),
            normal_net_pnl_usdt=_decimal(
                raw["normal_net_pnl_usdt"], "normal_net_pnl_usdt"
            ),
            special_fill_count=int(raw["special_fill_count"]),
            special_gross_pnl_usdt=_decimal(
                raw["special_gross_pnl_usdt"], "special_gross_pnl_usdt"
            ),
            special_fees_usdt=_decimal(
                raw["special_fees_usdt"], "special_fees_usdt"
            ),
            special_net_pnl_usdt=_decimal(
                raw["special_net_pnl_usdt"], "special_net_pnl_usdt"
            ),
            aggregate_gross_pnl_usdt=_decimal(
                raw["aggregate_gross_pnl_usdt"], "aggregate_gross_pnl_usdt"
            ),
            aggregate_fees_usdt=_decimal(
                raw["aggregate_fees_usdt"], "aggregate_fees_usdt"
            ),
            aggregate_net_pnl_usdt=_decimal(
                raw["aggregate_net_pnl_usdt"], "aggregate_net_pnl_usdt"
            ),
            flatten_dispatches=int(raw["flatten_dispatches"]),
            quote_mode_ticks=int(raw["quote_mode_ticks"]),
            quote_mode_counters={
                str(name): int(count) for name, count in counters_value.items()
            },
            unclassified_quote_mode_ticks=int(raw["unclassified_quote_mode_ticks"]),
            markouts_usdt=tuple(
                _decimal(item, "markout_usdt") for item in markouts_value
            ),
            causal_reentry=tuple(
                CausalReentryEvidence.from_dict(item) for item in causal_value
            ),
            fill_cursor_sha256=str(raw["fill_cursor_sha256"]),
            final_position_btc=_decimal(raw["final_position_btc"], "final_position_btc"),
            final_open_orders=int(raw["final_open_orders"]),
            terminal_account_snapshots=int(raw["terminal_account_snapshots"]),
            terminal_reconciled=bool(raw["terminal_reconciled"]),
            pending_intent=bool(raw["pending_intent"]),
            ambiguous_intent=bool(raw["ambiguous_intent"]),
            safety_violations=tuple(str(item) for item in safety_value),
            live_endpoint_attempts=int(raw["live_endpoint_attempts"]),
            live_orders=int(raw["live_orders"]),
            accounting_audit=(
                dict(raw["accounting_audit"])
                if isinstance(raw.get("accounting_audit"), Mapping)
                else {}
            ),
            control_audit=(
                dict(raw["control_audit"])
                if isinstance(raw.get("control_audit"), Mapping)
                else {}
            ),
            extension_fields={
                key: raw[key]
                for key in SESSION_EXTENSION_FIELDS
                if key in raw
            },
        )
        result.validate(limits or CampaignLimits())
        return result


def seal_session_evidence(value: Mapping[str, object]) -> dict[str, object]:
    if "evidence_sha256" in value:
        raise CampaignError("session evidence is already sealed")
    payload = dict(value)
    return {**payload, "evidence_sha256": canonical_sha256(payload)}


@dataclass(frozen=True)
class CampaignManifest:
    campaign_id: str
    source_sha256: str
    created_at_ms: int
    formal_predecessor: str
    soak_predecessor: str
    formal_completed_sha256: str
    soak_completed_sha256: str
    limits: CampaignLimits = CampaignLimits()
    schema_version: int = 1

    def validate(self) -> None:
        if not self.campaign_id.startswith("economic-campaign-"):
            raise CampaignError("campaign identity is invalid")
        if self.created_at_ms <= 0:
            raise CampaignError("campaign timestamp is invalid")
        if self.formal_predecessor != "formal-package-20260810T123953Z":
            raise CampaignError("formal predecessor drift")
        if self.soak_predecessor != "soak-package-20260811T140223Z":
            raise CampaignError("soak predecessor drift")
        _require_sha256(self.source_sha256, "source_sha256")
        _require_sha256(self.formal_completed_sha256, "formal_completed_sha256")
        _require_sha256(self.soak_completed_sha256, "soak_completed_sha256")
        self.limits.validate()
        if self.schema_version != 1:
            raise CampaignError("campaign schema drift")

    def payload(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "source_sha256": self.source_sha256,
            "created_at_ms": self.created_at_ms,
            "formal_predecessor": self.formal_predecessor,
            "soak_predecessor": self.soak_predecessor,
            "formal_completed_sha256": self.formal_completed_sha256,
            "soak_completed_sha256": self.soak_completed_sha256,
            "limits": self.limits.to_dict(),
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self.payload()
        return {**payload, "manifest_sha256": canonical_sha256(payload)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CampaignManifest":
        raw = dict(value)
        expected = _require_sha256(raw.pop("manifest_sha256", ""), "manifest_sha256")
        if canonical_sha256(raw) != expected:
            raise CampaignError("campaign manifest seal mismatch")
        limits = raw.get("limits")
        if not isinstance(limits, Mapping):
            raise CampaignError("campaign limits are missing")
        result = cls(
            campaign_id=str(raw["campaign_id"]),
            source_sha256=str(raw["source_sha256"]),
            created_at_ms=int(raw["created_at_ms"]),
            formal_predecessor=str(raw["formal_predecessor"]),
            soak_predecessor=str(raw["soak_predecessor"]),
            formal_completed_sha256=str(raw["formal_completed_sha256"]),
            soak_completed_sha256=str(raw["soak_completed_sha256"]),
            limits=CampaignLimits.from_dict(limits),
            schema_version=int(raw["schema_version"]),
        )
        result.validate()
        return result


class CampaignRegistry:
    """Append-only campaign state with exact aggregate and decision gates."""

    def __init__(self, root: Path, manifest: CampaignManifest):
        self.root = root
        self.manifest = manifest
        self.manifest_path = root / "campaign_manifest.json"
        self.registry_path = root / "campaign_registry.jsonl"

    @classmethod
    def initialize(cls, root: Path, manifest: CampaignManifest) -> "CampaignRegistry":
        manifest.validate()
        if root.exists():
            raise CampaignError("campaign artifact reuse refused")
        root.mkdir(parents=True, exist_ok=False)
        result = cls(root, manifest)
        _write_new(result.manifest_path, manifest.to_dict())
        result._append("CAMPAIGN_CREATED", {
            "manifest_sha256": manifest.to_dict()["manifest_sha256"],
            "network_authorized": False,
            "orders_authorized": False,
            "production_authorized": False,
        })
        return result

    @classmethod
    def load(cls, root: Path) -> "CampaignRegistry":
        try:
            value = json.loads((root / "campaign_manifest.json").read_text(
                encoding="utf-8"
            ))
        except Exception as exc:
            raise CampaignError("campaign manifest cannot be loaded") from exc
        result = cls(root, CampaignManifest.from_dict(value))
        result.records()
        return result

    def records(self) -> tuple[dict[str, object], ...]:
        if not self.registry_path.is_file():
            raise CampaignError("campaign registry is missing")
        records: list[dict[str, object]] = []
        previous_hash = "GENESIS"
        try:
            lines = self.registry_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CampaignError("campaign registry cannot be read") from exc
        for expected_sequence, line in enumerate(lines, start=1):
            if not line.strip():
                raise CampaignError("campaign registry contains an empty record")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CampaignError("campaign registry is truncated or corrupt") from exc
            record_hash = _require_sha256(record.pop("record_hash", ""), "record_hash")
            if record.get("sequence") != expected_sequence:
                raise CampaignError("campaign registry sequence regression")
            if record.get("campaign_id") != self.manifest.campaign_id:
                raise CampaignError("campaign registry identity mismatch")
            if record.get("previous_hash") != previous_hash:
                raise CampaignError("campaign registry hash-chain mismatch")
            if canonical_sha256(record) != record_hash:
                raise CampaignError("campaign registry record hash mismatch")
            record["record_hash"] = record_hash
            records.append(record)
            previous_hash = record_hash
        if not records or records[0].get("event") != "CAMPAIGN_CREATED":
            raise CampaignError("campaign registry genesis is invalid")
        return tuple(records)

    def _append(self, event: str, payload: Mapping[str, object]) -> dict[str, object]:
        records: tuple[dict[str, object], ...]
        if self.registry_path.exists():
            records = self.records()
        else:
            records = ()
        if records and records[-1]["event"] == "CAMPAIGN_DECIDED":
            raise CampaignError("campaign is terminal")
        core: dict[str, object] = {
            "sequence": len(records) + 1,
            "campaign_id": self.manifest.campaign_id,
            "event": event,
            "payload": dict(payload),
            "previous_hash": records[-1]["record_hash"] if records else "GENESIS",
        }
        record = {**core, "record_hash": canonical_sha256(core)}
        mode = "a" if self.registry_path.exists() else "x"
        try:
            with self.registry_path.open(mode, encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception as exc:
            raise CampaignError("campaign registry append failed") from exc
        return record

    def sessions(self) -> tuple[SessionEvidence, ...]:
        return tuple(
            SessionEvidence.from_dict(record["payload"]["session"], self.manifest.limits)
            for record in self.records()
            if record["event"] == "SESSION_ACCEPTED"
        )

    def terminal_decision(self) -> CampaignDecision | None:
        terminal = [record for record in self.records() if record["event"] == "CAMPAIGN_DECIDED"]
        if not terminal:
            return None
        if len(terminal) != 1:
            raise CampaignError("campaign contains multiple terminal decisions")
        return CampaignDecision(str(terminal[0]["payload"]["decision"]))

    def aggregate(self, sessions: Iterable[SessionEvidence] | None = None) -> dict[str, object]:
        rows = tuple(self.sessions() if sessions is None else sessions)
        create_counters = tuple(item.create_counters() for item in rows)
        bid = sum(item.normal_bid_fills for item in rows)
        ask = sum(item.normal_ask_fills for item in rows)
        maximum = max(bid, ask, 1)
        minimum = min(bid, ask)
        markouts = tuple(value for item in rows for value in item.markouts_usdt)
        terminal_special_closed_fills = sum(
            item.terminal_special_closed_fill_count for item in rows
        )
        terminal_special_closed_markouts = sum(
            item.terminal_special_closed_markout_count for item in rows
        )
        campaign_wall_ms = (
            rows[-1].ended_at_ms - rows[0].started_at_ms if rows else 0
        )
        return {
            "session_count": len(rows),
            "campaign_wall_ms": campaign_wall_ms,
            "campaign_active_ms": sum(item.duration_ms for item in rows),
            "normal_creates": sum(item.normal_creates for item in rows),
            "normal_create_dispatches": sum(
                int(item["normal_create_dispatches"]) for item in create_counters
            ),
            "normal_create_acknowledgements": sum(
                int(item["normal_create_acknowledgements"])
                for item in create_counters
            ),
            "normal_create_rejections": sum(
                int(item["normal_create_rejections"])
                for item in create_counters
            ),
            "normal_create_unresolved": sum(
                int(item["normal_create_unresolved"])
                for item in create_counters
            ),
            "create_counter_reconciles": all(
                item["reconciles"] is True for item in create_counters
            ),
            "normal_cancels": sum(item.normal_cancels for item in rows),
            "normal_bid_fills": bid,
            "normal_ask_fills": ask,
            "normal_fill_count": bid + ask,
            "fill_balance": str(Decimal(minimum) / Decimal(maximum)),
            "normal_fifo_round_trips": sum(
                item.normal_fifo_round_trips for item in rows
            ),
            "realized_spread_pnl_usdt": str(sum(
                (item.realized_spread_pnl_usdt for item in rows), Decimal("0")
            )),
            "inventory_pnl_usdt": str(sum(
                (item.inventory_pnl_usdt for item in rows), Decimal("0")
            )),
            "normal_gross_pnl_usdt": str(sum(
                (item.normal_gross_pnl_usdt for item in rows), Decimal("0")
            )),
            "normal_fees_usdt": str(sum(
                (item.normal_fees_usdt for item in rows), Decimal("0")
            )),
            "normal_net_pnl_usdt": str(sum(
                (item.normal_net_pnl_usdt for item in rows), Decimal("0")
            )),
            "special_gross_pnl_usdt": str(sum(
                (item.special_gross_pnl_usdt for item in rows), Decimal("0")
            )),
            "special_fees_usdt": str(sum(
                (item.special_fees_usdt for item in rows), Decimal("0")
            )),
            "special_net_pnl_usdt": str(sum(
                (item.special_net_pnl_usdt for item in rows), Decimal("0")
            )),
            "aggregate_gross_pnl_usdt": str(sum(
                (item.aggregate_gross_pnl_usdt for item in rows), Decimal("0")
            )),
            "aggregate_fees_usdt": str(sum(
                (item.aggregate_fees_usdt for item in rows), Decimal("0")
            )),
            "aggregate_net_pnl_usdt": str(sum(
                (item.aggregate_net_pnl_usdt for item in rows), Decimal("0")
            )),
            "special_flatten_sessions": sum(
                item.has_special_flatten_dispatch for item in rows
            ),
            "special_flatten_fraction": str(
                Decimal(sum(item.has_special_flatten_dispatch for item in rows))
                / Decimal(max(len(rows), 1))
            ),
            "maximum_session_drawdown_usdt": str(max(
                (item.maximum_drawdown_usdt for item in rows), default=Decimal("0")
            )),
            "unclassified_quote_mode_ticks": sum(
                item.unclassified_quote_mode_ticks for item in rows
            ),
            "causal_reentry_records": sum(len(item.causal_reentry) for item in rows),
            "markout_count": len(markouts),
            "terminal_special_closed_fill_count": terminal_special_closed_fills,
            "terminal_special_closed_markout_count": terminal_special_closed_markouts,
            "normal_markout_attribution_reconciles": (
                len(markouts) + terminal_special_closed_markouts == bid + ask
                and terminal_special_closed_fills
                == terminal_special_closed_markouts
            ),
            "mean_markout_usdt": str(
                sum(markouts, Decimal("0")) / Decimal(max(len(markouts), 1))
            ),
            "unsafe_sessions": sum(not item.is_safe for item in rows),
            "live_endpoint_attempts": sum(item.live_endpoint_attempts for item in rows),
            "live_orders": sum(item.live_orders for item in rows),
        }

    def evaluate(
        self, sessions: Iterable[SessionEvidence] | None = None
    ) -> tuple[CampaignDecision, tuple[str, ...]]:
        rows = tuple(self.sessions() if sessions is None else sessions)
        aggregate = self.aggregate(rows)
        limits = self.manifest.limits
        unsafe: list[str] = []
        if any(not item.is_safe for item in rows):
            unsafe.append("UNSAFE_SESSION")
        if _decimal(aggregate["aggregate_net_pnl_usdt"], "aggregate_net") <= -limits.aggregate_hard_loss_usdt:
            unsafe.append("AGGREGATE_HARD_LOSS")
        if int(aggregate["campaign_wall_ms"]) > limits.maximum_campaign_wall_ms:
            unsafe.append("CAMPAIGN_WALL_BUDGET")
        if int(aggregate["normal_creates"]) > limits.maximum_campaign_normal_creates:
            unsafe.append("CAMPAIGN_CREATE_BUDGET")
        if len(rows) > limits.maximum_sessions:
            unsafe.append("CAMPAIGN_SESSION_BUDGET")
        if unsafe:
            return CampaignDecision.NOT_READY, tuple(unsafe)
        if len(rows) < limits.maximum_sessions:
            return CampaignDecision.IN_PROGRESS, ()
        sample: list[str] = []
        if int(aggregate["normal_fill_count"]) < limits.minimum_normal_fills:
            sample.append("NORMAL_FILL_FLOOR")
        if int(aggregate["normal_bid_fills"]) < limits.minimum_bid_fills:
            sample.append("BID_FILL_FLOOR")
        if int(aggregate["normal_ask_fills"]) < limits.minimum_ask_fills:
            sample.append("ASK_FILL_FLOOR")
        if _decimal(aggregate["fill_balance"], "fill_balance") < limits.minimum_fill_balance:
            sample.append("FILL_BALANCE_FLOOR")
        if int(aggregate["normal_fifo_round_trips"]) < limits.minimum_fifo_round_trips:
            sample.append("FIFO_ROUND_TRIP_FLOOR")
        if sample:
            return CampaignDecision.INSUFFICIENT_EVIDENCE, tuple(sample)
        economic: list[str] = []
        if _decimal(aggregate["normal_net_pnl_usdt"], "normal_net") <= 0:
            economic.append("NORMAL_NET_NOT_POSITIVE")
        if _decimal(
            aggregate["special_flatten_fraction"], "special_flatten_fraction"
        ) > limits.maximum_special_flatten_fraction:
            economic.append("SPECIAL_FLATTEN_RATE")
        if int(aggregate["unclassified_quote_mode_ticks"]) != 0:
            economic.append("UNCLASSIFIED_QUOTE_MODE")
        if int(aggregate["causal_reentry_records"]) != int(aggregate["normal_fill_count"]):
            economic.append("CAUSAL_REENTRY_RECONCILIATION")
        if economic:
            return CampaignDecision.NOT_READY, tuple(economic)
        return CampaignDecision.READY_FOR_PRODUCTION_READ_ONLY_SHADOW, ()

    def register_session(self, session: SessionEvidence) -> CampaignDecision:
        if self.terminal_decision() is not None:
            raise CampaignError("campaign is already terminal")
        session.validate(self.manifest.limits)
        if session.source_sha256 != self.manifest.source_sha256:
            raise CampaignError("session source binding mismatch")
        previous = self.sessions()
        if any(
            item.session_id == session.session_id or item.run_id == session.run_id
            for item in previous
        ):
            raise CampaignError("session identity reuse refused")
        if previous and session.started_at_ms < previous[-1].ended_at_ms:
            raise CampaignError("economic sessions overlap or regress in time")
        if len(previous) >= self.manifest.limits.maximum_sessions:
            raise CampaignError("campaign session budget exhausted")
        candidate = (*previous, session)
        aggregate = self.aggregate(candidate)
        if int(aggregate["campaign_wall_ms"]) > self.manifest.limits.maximum_campaign_wall_ms:
            raise CampaignError("campaign wall budget would be exceeded")
        if int(aggregate["normal_creates"]) > self.manifest.limits.maximum_campaign_normal_creates:
            raise CampaignError("campaign create budget would be exceeded")
        self._append("SESSION_ACCEPTED", {
            "session": session.to_dict(),
            "aggregate_after_session": aggregate,
        })
        decision, reasons = self.evaluate(candidate)
        if decision is not CampaignDecision.IN_PROGRESS:
            self._append("CAMPAIGN_DECIDED", {
                "decision": decision.value,
                "reasons": list(reasons),
                "aggregate": aggregate,
                "production_authorized": False,
                "live_mode_available": False,
            })
        return decision

    def fail_closed(self, reason: str) -> CampaignDecision:
        if not reason or self.terminal_decision() is not None:
            raise CampaignError("campaign failure decision is invalid")
        self._append("CAMPAIGN_DECIDED", {
            "decision": CampaignDecision.NOT_READY.value,
            "reasons": [reason],
            "aggregate": self.aggregate(),
            "production_authorized": False,
            "live_mode_available": False,
        })
        return CampaignDecision.NOT_READY


def verify_registry(root: Path) -> dict[str, object]:
    registry = CampaignRegistry.load(root)
    records = registry.records()
    sessions = registry.sessions()
    decision, reasons = registry.evaluate(sessions)
    terminal = registry.terminal_decision()
    explicit_fail_closed = False
    if terminal is not None and terminal is not decision:
        terminal_record = records[-1]
        explicit_fail_closed = bool(
            terminal is CampaignDecision.NOT_READY
            and terminal_record.get("event") == "CAMPAIGN_DECIDED"
            and list(terminal_record.get("payload", {}).get("reasons") or [])
        )
        if not explicit_fail_closed:
            raise CampaignError("durable terminal decision disagrees with recomputation")
    return {
        "campaign_id": registry.manifest.campaign_id,
        "records": len(records),
        "sessions": len(sessions),
        "tail_sha256": records[-1]["record_hash"],
        "recomputed_decision": decision.value,
        "decision_reasons": list(reasons),
        "terminal_decision": None if terminal is None else terminal.value,
        "explicit_fail_closed": explicit_fail_closed,
        "aggregate": registry.aggregate(sessions),
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }
