"""Durable causal-activity controller for one economic Demo session.

The controller is transport independent.  It classifies every continuation
tick, records the causal chain from each maker fill through inventory defense
and maker re-entry/work-off, and refuses to treat a taker flatten as normal
economic evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Mapping

from okx_demo_multi_session_campaign import CampaignError, CausalReentryEvidence
from okx_fill_restart_validation import canonical_sha256


class QuoteMode(str, Enum):
    BALANCED_TWO_SIDED = "BALANCED_TWO_SIDED"
    ONE_SIDED_SELL_DEFENSE = "ONE_SIDED_SELL_DEFENSE"
    ONE_SIDED_BUY_DEFENSE = "ONE_SIDED_BUY_DEFENSE"
    CAUSAL_REENTRY = "CAUSAL_REENTRY"
    PLACEMENT_BLOCKED = "PLACEMENT_BLOCKED"
    HARD_KILL_CONTINUATION = "HARD_KILL_CONTINUATION"


class SessionPhase(str, Enum):
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class FeeAwareQuoteDecision:
    bid_price_usdt: Decimal
    ask_price_usdt: Decimal
    gross_edge_usdt: Decimal
    maker_fees_usdt: Decimal
    safety_buffer_usdt: Decimal
    net_edge_usdt: Decimal
    eligible: bool


@dataclass(frozen=True)
class SampleEfficiencyQuotePolicy:
    """Transport-independent successor quote policy with frozen risk bounds."""

    minimum_half_spread_bps: Decimal = Decimal("4.0")
    maker_fee_rate: Decimal = Decimal("0.0002")
    fee_edge_safety_buffer_usdt: Decimal = Decimal("0.01")
    balanced_retention_threshold_ticks: int = 10
    defense_retention_threshold_ticks: int = 20
    draining_workoff_retention_threshold_ticks: int = 5
    draining_workoff_max_quote_observations: int = 6
    draining_workoff_max_refreshes: int = 3
    admission_create_cap: int = 48
    workoff_create_reserve: int = 12

    def validate(self) -> None:
        half_spread = _decimal(
            self.minimum_half_spread_bps, "minimum_half_spread_bps"
        )
        fee_rate = _decimal(self.maker_fee_rate, "maker_fee_rate")
        buffer = _decimal(
            self.fee_edge_safety_buffer_usdt,
            "fee_edge_safety_buffer_usdt",
        )
        if half_spread <= fee_rate * Decimal("10000"):
            raise CampaignError("sample-efficiency spread is not fee positive")
        if fee_rate < 0 or buffer < 0:
            raise CampaignError("sample-efficiency fee policy is invalid")
        if not 2 <= self.balanced_retention_threshold_ticks <= 10:
            raise CampaignError("balanced quote retention is outside bounded repair")
        if not (
            self.balanced_retention_threshold_ticks
            <= self.defense_retention_threshold_ticks
            <= 20
        ):
            raise CampaignError("defense quote retention is outside bounded repair")
        if not (
            2 <= self.draining_workoff_retention_threshold_ticks
            <= self.balanced_retention_threshold_ticks
        ):
            raise CampaignError("draining work-off retention is outside bounded repair")
        if not 2 <= self.draining_workoff_max_quote_observations <= 10:
            raise CampaignError("draining work-off observation bound is invalid")
        if not 1 <= self.draining_workoff_max_refreshes <= 3:
            raise CampaignError("draining work-off refresh bound is invalid")
        if self.admission_create_cap != 48 or self.workoff_create_reserve != 12:
            raise CampaignError("sample-efficiency create partition drift")
        if self.admission_create_cap + self.workoff_create_reserve != 60:
            raise CampaignError("sample-efficiency total create budget drift")

    def retention_threshold_ticks(
        self, inventory_btc: Decimal, *, draining: bool = False
    ) -> int:
        self.validate()
        inventory = _decimal(inventory_btc, "inventory_btc")
        if abs(inventory) > Decimal("0.01"):
            raise CampaignError("sample-efficiency inventory exceeds frozen cap")
        if draining and inventory != 0:
            return self.draining_workoff_retention_threshold_ticks
        return (
            self.balanced_retention_threshold_ticks
            if inventory == 0
            else self.defense_retention_threshold_ticks
        )

    def refresh_draining_workoff(
        self, *, observations: int, refreshes: int, inventory_btc: Decimal,
    ) -> bool:
        """Return whether one reserved maker work-off refresh is due.

        A price can remain tick-valid while becoming non-productive at the end
        of a session. This bounded rule refreshes only an already-owned,
        correctly-sided work-off quote; it never authorizes new exposure.
        """
        self.validate()
        inventory = _decimal(inventory_btc, "inventory_btc")
        if inventory == 0:
            return False
        if observations < 0 or refreshes < 0:
            raise CampaignError("draining work-off counters regressed")
        return bool(
            observations >= self.draining_workoff_max_quote_observations
            and refreshes < self.draining_workoff_max_refreshes
        )

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "minimum_half_spread_bps": str(self.minimum_half_spread_bps),
            "maker_fee_rate": str(self.maker_fee_rate),
            "fee_edge_safety_buffer_usdt": str(
                self.fee_edge_safety_buffer_usdt
            ),
            "balanced_retention_threshold_ticks": (
                self.balanced_retention_threshold_ticks
            ),
            "defense_retention_threshold_ticks": (
                self.defense_retention_threshold_ticks
            ),
            "draining_workoff_retention_threshold_ticks": (
                self.draining_workoff_retention_threshold_ticks
            ),
            "draining_workoff_max_quote_observations": (
                self.draining_workoff_max_quote_observations
            ),
            "draining_workoff_max_refreshes": self.draining_workoff_max_refreshes,
            "admission_create_cap": self.admission_create_cap,
            "workoff_create_reserve": self.workoff_create_reserve,
            "total_create_cap": (
                self.admission_create_cap + self.workoff_create_reserve
            ),
            "risk_expansion": False,
        }


def fee_aware_quote_pair(
    *,
    best_bid_usdt: Decimal,
    best_ask_usdt: Decimal,
    quantity_btc: Decimal,
    maker_fee_rate: Decimal,
    minimum_half_spread_bps: Decimal,
    tick_size_usdt: Decimal,
    safety_buffer_usdt: Decimal,
) -> FeeAwareQuoteDecision:
    bid = _decimal(best_bid_usdt, "best_bid_usdt")
    ask = _decimal(best_ask_usdt, "best_ask_usdt")
    quantity = _decimal(quantity_btc, "quantity_btc")
    fee_rate = _decimal(maker_fee_rate, "maker_fee_rate")
    half_bps = _decimal(minimum_half_spread_bps, "minimum_half_spread_bps")
    tick = _decimal(tick_size_usdt, "tick_size_usdt")
    buffer = _decimal(safety_buffer_usdt, "safety_buffer_usdt")
    if bid <= 0 or ask <= bid or quantity <= 0 or fee_rate < 0:
        raise CampaignError("fee-aware quote input is invalid")
    if half_bps <= 0 or tick <= 0 or buffer < 0:
        raise CampaignError("fee-aware quote policy is invalid")
    mid = (bid + ask) / Decimal("2")
    half_spread = max(mid * half_bps / Decimal("10000"), tick)
    quote_bid = ((mid - half_spread) / tick).to_integral_value(
        rounding=ROUND_FLOOR
    ) * tick
    quote_ask = ((mid + half_spread) / tick).to_integral_value(
        rounding=ROUND_CEILING
    ) * tick
    quote_bid = min(quote_bid, bid)
    quote_ask = max(quote_ask, ask)
    gross = (quote_ask - quote_bid) * quantity
    fees = (quote_ask + quote_bid) * quantity * fee_rate
    net = gross - fees - buffer
    return FeeAwareQuoteDecision(
        bid_price_usdt=quote_bid,
        ask_price_usdt=quote_ask,
        gross_edge_usdt=gross,
        maker_fees_usdt=fees,
        safety_buffer_usdt=buffer,
        net_edge_usdt=net,
        eligible=net > 0,
    )


def quote_still_valid(
    *, existing_price_usdt: Decimal, target_price_usdt: Decimal,
    tick_size_usdt: Decimal, threshold_ticks: int,
) -> bool:
    existing = _decimal(existing_price_usdt, "existing_price_usdt")
    target = _decimal(target_price_usdt, "target_price_usdt")
    tick = _decimal(tick_size_usdt, "tick_size_usdt")
    if existing <= 0 or target <= 0 or tick <= 0 or threshold_ticks < 0:
        raise CampaignError("quote retention input is invalid")
    return abs(existing - target) <= tick * threshold_ticks


def fee_aware_workoff_edge(
    *,
    entry_price_usdt: Decimal,
    exit_price_usdt: Decimal,
    entry_side: str,
    quantity_btc: Decimal,
    maker_fee_rate: Decimal,
    safety_buffer_usdt: Decimal,
) -> Decimal:
    entry = _decimal(entry_price_usdt, "entry_price_usdt")
    exit_price = _decimal(exit_price_usdt, "exit_price_usdt")
    quantity = _decimal(quantity_btc, "quantity_btc")
    fee_rate = _decimal(maker_fee_rate, "maker_fee_rate")
    buffer = _decimal(safety_buffer_usdt, "safety_buffer_usdt")
    if entry <= 0 or exit_price <= 0 or quantity <= 0 or entry_side not in {
        "buy", "sell"
    }:
        raise CampaignError("fee-aware workoff input is invalid")
    direction = Decimal("1") if entry_side == "buy" else Decimal("-1")
    gross = quantity * (exit_price - entry) * direction
    fees = (entry + exit_price) * quantity * fee_rate
    return gross - fees - buffer


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CampaignError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise CampaignError(f"{name} is not finite")
    return result


@dataclass
class PendingCausalFill:
    trade_id: str
    fill_side: str
    fill_timestamp_ms: int
    inventory_before_btc: Decimal
    inventory_after_btc: Decimal
    defense_timestamp_ms: int = 0
    reentry_timestamp_ms: int = 0
    workoff_timestamp_ms: int = 0
    maker_reentry_observed: bool = False
    maker_workoff_observed: bool = False
    immediate_taker_flatten: bool = False
    fill_order_id: str = ""
    fill_quantity_btc: Decimal = Decimal("0")
    fill_price_usdt: Decimal = Decimal("0")
    remaining_workoff_btc: Decimal = Decimal("0")
    reentry_client_order_id: str = ""
    reentry_side: str = ""
    reentry_quantity_btc: Decimal = Decimal("0")
    workoff_trade_ids: list[str] = field(default_factory=list)
    workoff_order_ids: list[str] = field(default_factory=list)
    matched_workoff_btc: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "trade_id": self.trade_id,
            "fill_side": self.fill_side,
            "fill_timestamp_ms": self.fill_timestamp_ms,
            "inventory_before_btc": str(self.inventory_before_btc),
            "inventory_after_btc": str(self.inventory_after_btc),
            "defense_timestamp_ms": self.defense_timestamp_ms,
            "reentry_timestamp_ms": self.reentry_timestamp_ms,
            "workoff_timestamp_ms": self.workoff_timestamp_ms,
            "maker_reentry_observed": self.maker_reentry_observed,
            "maker_workoff_observed": self.maker_workoff_observed,
            "immediate_taker_flatten": self.immediate_taker_flatten,
            "causal_binding": {
                "fill_order_id": self.fill_order_id,
                "fill_quantity_btc": str(self.fill_quantity_btc),
                "fill_price_usdt": str(self.fill_price_usdt),
                "remaining_workoff_btc": str(self.remaining_workoff_btc),
                "reentry_client_order_id": self.reentry_client_order_id,
                "reentry_side": self.reentry_side,
                "reentry_quantity_btc": str(self.reentry_quantity_btc),
                "workoff_trade_ids": list(self.workoff_trade_ids),
                "workoff_order_ids": list(self.workoff_order_ids),
                "matched_workoff_btc": str(self.matched_workoff_btc),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PendingCausalFill":
        binding = value.get("causal_binding")
        if not isinstance(binding, Mapping):
            binding = {}
        result = cls(
            trade_id=str(value["trade_id"]),
            fill_side=str(value["fill_side"]),
            fill_timestamp_ms=int(value["fill_timestamp_ms"]),
            inventory_before_btc=_decimal(
                value["inventory_before_btc"], "inventory_before_btc"
            ),
            inventory_after_btc=_decimal(
                value["inventory_after_btc"], "inventory_after_btc"
            ),
            defense_timestamp_ms=int(value["defense_timestamp_ms"]),
            reentry_timestamp_ms=int(value["reentry_timestamp_ms"]),
            workoff_timestamp_ms=int(value["workoff_timestamp_ms"]),
            maker_reentry_observed=bool(value["maker_reentry_observed"]),
            maker_workoff_observed=bool(value["maker_workoff_observed"]),
            immediate_taker_flatten=bool(value["immediate_taker_flatten"]),
            fill_order_id=str(binding.get("fill_order_id", "")),
            fill_quantity_btc=_decimal(
                binding.get("fill_quantity_btc", "0"), "fill_quantity_btc"
            ),
            fill_price_usdt=_decimal(
                binding.get("fill_price_usdt", "0"), "fill_price_usdt"
            ),
            remaining_workoff_btc=_decimal(
                binding.get("remaining_workoff_btc", "0"),
                "remaining_workoff_btc",
            ),
            reentry_client_order_id=str(
                binding.get("reentry_client_order_id", "")
            ),
            reentry_side=str(binding.get("reentry_side", "")),
            reentry_quantity_btc=_decimal(
                binding.get("reentry_quantity_btc", "0"),
                "reentry_quantity_btc",
            ),
            workoff_trade_ids=[
                str(item) for item in binding.get("workoff_trade_ids", [])
            ],
            workoff_order_ids=[
                str(item) for item in binding.get("workoff_order_ids", [])
            ],
            matched_workoff_btc=_decimal(
                binding.get("matched_workoff_btc", "0"),
                "matched_workoff_btc",
            ),
        )
        if result.maker_workoff_observed:
            if result.workoff_timestamp_ms <= 0 or result.remaining_workoff_btc != 0:
                raise CampaignError("completed maker work-off state is invalid")
        elif result.workoff_timestamp_ms != 0:
            raise CampaignError("unobserved maker work-off state has a timestamp")
        if (
            result.remaining_workoff_btc < 0
            or result.matched_workoff_btc < 0
            or result.remaining_workoff_btc + result.matched_workoff_btc
            != result.fill_quantity_btc
            or len(result.workoff_trade_ids) != len(result.workoff_order_ids)
            or len(result.workoff_trade_ids) != len(set(result.workoff_trade_ids))
        ):
            raise CampaignError("maker work-off restart state does not reconcile")
        return result


@dataclass
class EconomicSessionController:
    session_id: str
    source_sha256: str
    maximum_inventory_btc: Decimal = Decimal("0.01")
    quote_mode_counters: dict[str, int] = field(default_factory=lambda: {
        mode.value: 0 for mode in QuoteMode
    })
    quote_mode_ticks: int = 0
    unclassified_quote_mode_ticks: int = 0
    pending_fills: dict[str, PendingCausalFill] = field(default_factory=dict)
    completed_fills: dict[str, PendingCausalFill] = field(default_factory=dict)
    special_closed_fills: dict[str, PendingCausalFill] = field(default_factory=dict)
    markouts_usdt: dict[str, Decimal] = field(default_factory=dict)
    events: list[dict[str, object]] = field(default_factory=list)
    tail_sha256: str = "GENESIS"
    last_timestamp_ms: int = 0
    schema_version: int = 2
    phase: SessionPhase = SessionPhase.ACTIVE
    admission_create_cap: int = 48
    workoff_create_reserve: int = 12
    placement_reason_counters: dict[str, int] = field(default_factory=dict)
    normal_bid_fill_quantity_btc: Decimal = Decimal("0")
    normal_ask_fill_quantity_btc: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.session_id or len(self.source_sha256) != 64:
            raise CampaignError("economic controller binding is incomplete")
        if any(ch not in "0123456789abcdef" for ch in self.source_sha256):
            raise CampaignError("economic controller source hash is invalid")
        if self.maximum_inventory_btc != Decimal("0.01"):
            raise CampaignError("economic controller inventory budget drift")
        if set(self.quote_mode_counters) != {mode.value for mode in QuoteMode}:
            raise CampaignError("quote-mode counter schema drift")
        if any(value < 0 for value in self.quote_mode_counters.values()):
            raise CampaignError("quote-mode counter regression")
        if self.admission_create_cap + self.workoff_create_reserve != 60:
            raise CampaignError("economic create budget partition drift")
        if any(value < 0 for value in self.placement_reason_counters.values()):
            raise CampaignError("placement reason counter regression")
        if any(value < 0 for value in (
            self.normal_bid_fill_quantity_btc,
            self.normal_ask_fill_quantity_btc,
        )):
            raise CampaignError("fill quantity counter regression")

    def enter_draining(
        self, *, timestamp_ms: int, normal_creates: int,
        timebox_expiring: bool = False,
    ) -> None:
        if normal_creates > 60 or (
            normal_creates < self.admission_create_cap and not timebox_expiring
        ):
            raise CampaignError("DRAINING transition is outside reserved budget")
        if self.phase is SessionPhase.TERMINAL:
            raise CampaignError("terminal economic session cannot resume")
        if self.phase is SessionPhase.DRAINING:
            return
        self.phase = SessionPhase.DRAINING
        self._record(
            "DRAINING_ENTERED",
            timestamp_ms,
            normal_creates=normal_creates,
            remaining_workoff_creates=60 - normal_creates,
            new_exposure_authorized=False,
            timebox_expiring=timebox_expiring,
        )

    def finish(self, *, timestamp_ms: int) -> None:
        if self.pending_fills:
            raise CampaignError("terminal transition has pending causal fills")
        self.phase = SessionPhase.TERMINAL
        self._record("ECONOMIC_SESSION_TERMINAL", timestamp_ms)

    def record_placement_reason(self, *, reason: str, timestamp_ms: int) -> None:
        if reason not in {
            "FEE_EDGE_BLOCKED",
            "QUOTE_STILL_VALID",
            "DRAINING_WORKOFF_RETAINED_AT_CAP",
            "DRAINING_WORKOFF_REFRESH_DUE",
            "FILL_IMBALANCE_BLOCKED",
            "DRAINING_NEW_EXPOSURE_BLOCKED",
            "POST_ONLY_REJECTED",
            "POST_ONLY_CROSS_BLOCKED",
        }:
            raise CampaignError("unknown placement reason")
        self.placement_reason_counters[reason] = (
            self.placement_reason_counters.get(reason, 0) + 1
        )
        self._record("PLACEMENT_DECISION", timestamp_ms, reason=reason)

    def side_worsens_fill_imbalance(self, side: str) -> bool:
        if side not in {"buy", "sell"}:
            raise CampaignError("fill imbalance side is invalid")
        bids = self.normal_bid_fill_quantity_btc
        asks = self.normal_ask_fill_quantity_btc
        if bids == asks:
            return False
        larger = max(bids, asks)
        smaller = min(bids, asks)
        if larger == 0 or smaller / larger >= Decimal("0.60"):
            return False
        return (side == "buy" and bids > asks) or (
            side == "sell" and asks > bids
        )

    def _record(self, event: str, timestamp_ms: int, **payload: object) -> None:
        if timestamp_ms <= 0 or timestamp_ms < self.last_timestamp_ms:
            raise CampaignError("economic event timestamp regression")
        core: dict[str, object] = {
            "sequence": len(self.events) + 1,
            "session_id": self.session_id,
            "event": event,
            "timestamp_ms": timestamp_ms,
            "payload": payload,
            "previous_hash": self.tail_sha256,
        }
        record = {**core, "record_hash": canonical_sha256(core)}
        self.events.append(record)
        self.tail_sha256 = str(record["record_hash"])
        self.last_timestamp_ms = timestamp_ms

    def classify_tick(
        self,
        *,
        timestamp_ms: int,
        inventory_btc: Decimal,
        market_gate_open: bool = True,
        account_gate_open: bool = True,
        clock_gate_open: bool = True,
        fee_gate_open: bool = True,
        unresolved_intent: bool = False,
        hard_kill: bool = False,
    ) -> QuoteMode:
        inventory = _decimal(inventory_btc, "inventory_btc")
        if abs(inventory) > self.maximum_inventory_btc:
            raise CampaignError("economic tick inventory exceeds budget")
        mode: QuoteMode
        if hard_kill:
            mode = QuoteMode.HARD_KILL_CONTINUATION
        elif not all((
            market_gate_open,
            account_gate_open,
            clock_gate_open,
            fee_gate_open,
        )) or unresolved_intent:
            mode = QuoteMode.PLACEMENT_BLOCKED
        elif any(item.maker_reentry_observed and not item.maker_workoff_observed
                 for item in self.pending_fills.values()):
            mode = QuoteMode.CAUSAL_REENTRY
        elif inventory > 0:
            mode = QuoteMode.ONE_SIDED_SELL_DEFENSE
        elif inventory < 0:
            mode = QuoteMode.ONE_SIDED_BUY_DEFENSE
        else:
            mode = QuoteMode.BALANCED_TWO_SIDED
        self.quote_mode_ticks += 1
        self.quote_mode_counters[mode.value] += 1
        self._record(
            "QUOTE_MODE_CLASSIFIED",
            timestamp_ms,
            quote_mode=mode.value,
            inventory_btc=str(inventory),
            hard_kill=hard_kill,
            unresolved_intent=unresolved_intent,
            market_gate_open=market_gate_open,
            account_gate_open=account_gate_open,
            clock_gate_open=clock_gate_open,
            fee_gate_open=fee_gate_open,
        )
        return mode

    def observe_fill(
        self,
        *,
        trade_id: str,
        side: str,
        timestamp_ms: int,
        observed_at_ms: int | None = None,
        inventory_before_btc: Decimal,
        inventory_after_btc: Decimal,
        fill_order_id: str = "legacy-fixture",
        fill_quantity_btc: Decimal | None = None,
        fill_price_usdt: Decimal = Decimal("0"),
    ) -> None:
        if (
            not trade_id
            or trade_id in self.pending_fills
            or trade_id in self.completed_fills
            or trade_id in self.special_closed_fills
        ):
            raise CampaignError("normal fill identity is missing or duplicated")
        if side not in {"buy", "sell"}:
            raise CampaignError("normal fill side is invalid")
        before = _decimal(inventory_before_btc, "inventory_before_btc")
        after = _decimal(inventory_after_btc, "inventory_after_btc")
        if abs(before) > self.maximum_inventory_btc or abs(after) > self.maximum_inventory_btc:
            raise CampaignError("normal fill inventory exceeds budget")
        quantity = (
            abs(after - before)
            if fill_quantity_btc is None
            else _decimal(fill_quantity_btc, "fill_quantity_btc")
        )
        price = _decimal(fill_price_usdt, "fill_price_usdt")
        if not fill_order_id or quantity <= 0 or quantity > self.maximum_inventory_btc:
            raise CampaignError("normal fill binding is outside budget")
        if price < 0:
            raise CampaignError("normal fill price is invalid")
        signed_quantity = quantity if side == "buy" else -quantity
        if after != before + signed_quantity:
            raise CampaignError("normal fill inventory transition does not reconcile")
        opposite_inventory = (
            (before > 0 and side == "sell")
            or (before < 0 and side == "buy")
        )
        workoff_quantity = (
            min(quantity, abs(before)) if opposite_inventory else Decimal("0")
        )
        opening_quantity = quantity - workoff_quantity
        if workoff_quantity > 0:
            available_workoff = sum(
                (
                    prior.remaining_workoff_btc
                    for prior in (*self.pending_fills.values(), *self.completed_fills.values())
                    if prior.fill_side != side and prior.remaining_workoff_btc > 0
                ),
                Decimal("0"),
            )
            if available_workoff < workoff_quantity:
                raise CampaignError("maker work-off causal inventory underflow")
        record_timestamp = max(
            timestamp_ms,
            self.last_timestamp_ms,
            timestamp_ms if observed_at_ms is None else int(observed_at_ms),
        )
        item = PendingCausalFill(
            trade_id=trade_id,
            fill_side=side,
            fill_timestamp_ms=timestamp_ms,
            inventory_before_btc=before,
            inventory_after_btc=after,
            fill_order_id=fill_order_id,
            fill_quantity_btc=quantity,
            fill_price_usdt=price,
            # Only the part of this maker fill that opens inventory needs a
            # later causal work-off.  The opposing part already works off the
            # prior signed inventory and must not be counted a second time as
            # a fresh causal remainder.
            remaining_workoff_btc=opening_quantity,
            matched_workoff_btc=workoff_quantity,
            workoff_trade_ids=([trade_id] if workoff_quantity > 0 else []),
            workoff_order_ids=([fill_order_id] if workoff_quantity > 0 else []),
        )
        self.pending_fills[trade_id] = item
        self._record(
            "NORMAL_MAKER_FILL_OBSERVED",
            record_timestamp,
            trade_id=trade_id,
            side=side,
            fill_timestamp_ms=timestamp_ms,
            inventory_before_btc=str(before),
            inventory_after_btc=str(after),
            fill_order_id=fill_order_id,
            fill_quantity_btc=str(quantity),
            fill_price_usdt=str(price),
        )
        if side == "buy":
            self.normal_bid_fill_quantity_btc += quantity
        else:
            self.normal_ask_fill_quantity_btc += quantity
        # The opposing portion of a maker fill is durable quantity-matched
        # work-off for the oldest eligible causal lots.  This remains true
        # when the fill crosses through zero; only the residual crossing
        # quantity opens a new causal lot.
        if workoff_quantity > 0:
            workoff_remaining = workoff_quantity
            candidates = sorted(
                (*self.pending_fills.values(), *self.completed_fills.values()),
                key=lambda row: (row.fill_timestamp_ms, row.trade_id),
            )
            for prior in candidates:
                if (
                    prior.trade_id == trade_id
                    or prior.fill_side == side
                    or prior.remaining_workoff_btc <= 0
                ):
                    continue
                matched = min(workoff_remaining, prior.remaining_workoff_btc)
                self.observe_maker_workoff(
                    trade_id=prior.trade_id,
                    timestamp_ms=record_timestamp,
                    workoff_trade_id=trade_id,
                    workoff_order_id=fill_order_id,
                    matched_quantity_btc=matched,
                )
                workoff_remaining -= matched
                if workoff_remaining == 0:
                    break
            if workoff_remaining != 0:
                raise CampaignError("maker work-off causal inventory underflow")
            self._record(
                "MAKER_FILL_NETTING_OBSERVED",
                record_timestamp,
                trade_id=trade_id,
                workoff_quantity_btc=str(workoff_quantity),
                opening_quantity_btc=str(opening_quantity),
            )
        self._complete_if_ready(item)

    def observe_inventory_defense(self, *, trade_id: str, timestamp_ms: int) -> None:
        item = self._pending(trade_id)
        if item.defense_timestamp_ms:
            raise CampaignError("inventory defense is duplicated")
        if timestamp_ms < item.fill_timestamp_ms:
            raise CampaignError("inventory defense precedes fill")
        item.defense_timestamp_ms = timestamp_ms
        self._record("INVENTORY_DEFENSE_OBSERVED", timestamp_ms, trade_id=trade_id)
        if item.remaining_workoff_btc == 0 and not item.maker_workoff_observed:
            # A fill used entirely to reduce prior inventory has no new causal
            # remainder.  Its completion becomes admissible only after the
            # inventory-defense event is durable, preserving event causality.
            item.maker_workoff_observed = True
            item.workoff_timestamp_ms = timestamp_ms
            self._record(
                "MAKER_WORKOFF_OBSERVED",
                timestamp_ms,
                trade_id=trade_id,
                workoff_trade_id=trade_id,
                workoff_order_id=item.fill_order_id,
                matched_quantity_btc=str(item.matched_workoff_btc),
                remaining_workoff_btc="0",
            )
            self._complete_if_ready(item)

    def observe_maker_reentry(
        self,
        *,
        trade_id: str,
        timestamp_ms: int,
        client_order_id: str = "legacy-fixture",
        side: str = "",
        quantity_btc: Decimal | None = None,
    ) -> None:
        item = self._pending(trade_id)
        if not item.defense_timestamp_ms:
            raise CampaignError("maker re-entry lacks inventory defense")
        if item.maker_reentry_observed:
            return
        if timestamp_ms < item.defense_timestamp_ms:
            raise CampaignError("maker re-entry precedes inventory defense")
        expected_side = "sell" if item.fill_side == "buy" else "buy"
        bound_side = side or expected_side
        quantity = item.fill_quantity_btc if quantity_btc is None else _decimal(
            quantity_btc, "reentry_quantity_btc"
        )
        if not client_order_id or bound_side != expected_side or quantity <= 0:
            raise CampaignError("maker re-entry order binding is invalid")
        item.maker_reentry_observed = True
        item.reentry_timestamp_ms = timestamp_ms
        item.reentry_client_order_id = client_order_id
        item.reentry_side = bound_side
        item.reentry_quantity_btc = min(quantity, item.fill_quantity_btc)
        self._record(
            "MAKER_REENTRY_OBSERVED",
            timestamp_ms,
            trade_id=trade_id,
            client_order_id=client_order_id,
            side=bound_side,
            quantity_btc=str(item.reentry_quantity_btc),
        )
        self._complete_if_ready(item)

    def bind_maker_reentry_order(
        self,
        *,
        client_order_id: str,
        side: str,
        quantity_btc: Decimal,
        timestamp_ms: int,
    ) -> tuple[str, ...]:
        remaining = _decimal(quantity_btc, "reentry_order_quantity_btc")
        if remaining <= 0:
            raise CampaignError("maker re-entry order quantity is invalid")
        bound: list[str] = []
        for item in sorted(
            self.pending_fills.values(),
            key=lambda row: (row.fill_timestamp_ms, row.trade_id),
        ):
            expected_side = "sell" if item.fill_side == "buy" else "buy"
            if expected_side != side or item.maker_reentry_observed:
                continue
            allocated = min(remaining, item.fill_quantity_btc)
            self.observe_maker_reentry(
                trade_id=item.trade_id,
                timestamp_ms=timestamp_ms,
                client_order_id=client_order_id,
                side=side,
                quantity_btc=allocated,
            )
            bound.append(item.trade_id)
            remaining -= allocated
            if remaining == 0:
                break
        return tuple(bound)

    def observe_maker_workoff(
        self,
        *,
        trade_id: str,
        timestamp_ms: int,
        workoff_trade_id: str = "legacy-fixture",
        workoff_order_id: str = "legacy-fixture",
        matched_quantity_btc: Decimal | None = None,
    ) -> None:
        item = self._pending(trade_id)
        if not item.defense_timestamp_ms:
            raise CampaignError("maker work-off lacks inventory defense")
        if timestamp_ms < item.defense_timestamp_ms:
            raise CampaignError("maker work-off precedes inventory defense")
        quantity = (
            item.remaining_workoff_btc
            if matched_quantity_btc is None
            else _decimal(matched_quantity_btc, "matched_workoff_quantity_btc")
        )
        if not workoff_trade_id or not workoff_order_id or quantity <= 0:
            raise CampaignError("maker work-off binding is invalid")
        if quantity > item.remaining_workoff_btc:
            raise CampaignError("maker work-off exceeds causal fill remainder")
        if workoff_trade_id in item.workoff_trade_ids:
            raise CampaignError("maker work-off fill is duplicated")
        item.workoff_trade_ids.append(workoff_trade_id)
        item.workoff_order_ids.append(workoff_order_id)
        item.matched_workoff_btc += quantity
        item.remaining_workoff_btc -= quantity
        item.maker_workoff_observed = item.remaining_workoff_btc == 0
        # This field is the causal completion timestamp, not the timestamp of
        # the latest partial match.  Partial identities and quantities remain
        # durable in causal_binding while the completion timestamp stays zero.
        item.workoff_timestamp_ms = timestamp_ms if item.maker_workoff_observed else 0
        self._record(
            (
                "MAKER_WORKOFF_OBSERVED"
                if item.maker_workoff_observed
                else "MAKER_WORKOFF_PARTIAL_OBSERVED"
            ),
            timestamp_ms,
            trade_id=trade_id,
            workoff_trade_id=workoff_trade_id,
            workoff_order_id=workoff_order_id,
            matched_quantity_btc=str(quantity),
            remaining_workoff_btc=str(item.remaining_workoff_btc),
        )
        self._complete_if_ready(item)

    def observe_markout(
        self, *, trade_id: str, timestamp_ms: int, markout_usdt: Decimal
    ) -> None:
        if (
            trade_id not in self.pending_fills
            and trade_id not in self.completed_fills
            and trade_id not in self.special_closed_fills
        ):
            raise CampaignError("markout does not map to a normal fill")
        value = _decimal(markout_usdt, "markout_usdt")
        prior = self.markouts_usdt.get(trade_id)
        if prior is not None and prior != value:
            raise CampaignError("normal fill markout changed")
        self.markouts_usdt[trade_id] = value
        self._record(
            "MARKOUT_OBSERVED", timestamp_ms, trade_id=trade_id, markout_usdt=str(value)
        )

    def authorize_taker_flatten(self, *, reason: str, timestamp_ms: int) -> None:
        if reason not in {"SHUTDOWN", "EMERGENCY_HARD_KILL"}:
            raise CampaignError("taker flatten is not authorized for normal economics")
        self._record(
            "SPECIAL_TAKER_FLATTEN_AUTHORIZED",
            timestamp_ms,
            reason=reason,
            normal_economic_evidence=False,
        )

    def close_with_special_flatten(
        self,
        *,
        timestamp_ms: int,
        inventory_before_btc: Decimal,
        flatten_quantity_btc: Decimal,
    ) -> tuple[str, ...]:
        """Close outstanding causal remainders without maker work-off credit."""
        inventory = _decimal(inventory_before_btc, "special closure inventory")
        quantity = _decimal(flatten_quantity_btc, "special closure quantity")
        if inventory == 0 or quantity != abs(inventory) or quantity > Decimal("0.01"):
            raise CampaignError("special closure does not match terminal inventory")
        candidates = [
            item
            for item in (*self.pending_fills.values(), *self.completed_fills.values())
            if item.remaining_workoff_btc > 0
        ]
        causal_inventory = sum(
            (
                item.remaining_workoff_btc
                if item.fill_side == "buy"
                else -item.remaining_workoff_btc
                for item in candidates
            ),
            Decimal("0"),
        )
        if causal_inventory != inventory:
            raise CampaignError(
                "special closure controller/engine inventory does not reconcile"
            )
        closed: list[str] = []
        for item in candidates:
            item.immediate_taker_flatten = True
            self.pending_fills.pop(item.trade_id, None)
            self.completed_fills.pop(item.trade_id, None)
            self.special_closed_fills[item.trade_id] = item
            closed.append(item.trade_id)
        self._record(
            "SPECIAL_FLATTEN_CAUSAL_CLOSURE",
            timestamp_ms,
            trade_ids=sorted(closed),
            inventory_before_btc=str(inventory),
            flatten_quantity_btc=str(quantity),
            normal_economic_evidence=False,
            maker_workoff_credit=False,
        )
        return tuple(sorted(closed))

    def _pending(self, trade_id: str) -> PendingCausalFill:
        item = self.pending_fills.get(trade_id) or self.completed_fills.get(trade_id)
        if item is None:
            raise CampaignError("causal fill is not known")
        return item

    def _complete_if_ready(self, item: PendingCausalFill) -> None:
        if not (item.maker_reentry_observed or item.maker_workoff_observed):
            return
        self.completed_fills[item.trade_id] = item
        self.pending_fills.pop(item.trade_id, None)

    def causal_evidence(self, *, require_complete: bool = True) -> tuple[CausalReentryEvidence, ...]:
        if require_complete and self.pending_fills:
            raise CampaignError("causal re-entry evidence is incomplete")
        rows = [*self.completed_fills.values()]
        result: list[CausalReentryEvidence] = []
        for item in sorted(rows, key=lambda row: (row.fill_timestamp_ms, row.trade_id)):
            evidence = CausalReentryEvidence(
                trade_id=item.trade_id,
                fill_side=item.fill_side,
                fill_timestamp_ms=item.fill_timestamp_ms,
                defense_timestamp_ms=item.defense_timestamp_ms,
                reentry_timestamp_ms=item.reentry_timestamp_ms,
                workoff_timestamp_ms=item.workoff_timestamp_ms,
                maker_reentry_observed=item.maker_reentry_observed,
                maker_workoff_observed=item.maker_workoff_observed,
                immediate_taker_flatten=item.immediate_taker_flatten,
                inventory_before_btc=item.inventory_before_btc,
                inventory_after_btc=item.inventory_after_btc,
                causal_binding={
                    "fill_order_id": item.fill_order_id,
                    "fill_quantity_btc": str(item.fill_quantity_btc),
                    "fill_price_usdt": str(item.fill_price_usdt),
                    "remaining_workoff_btc": str(item.remaining_workoff_btc),
                    "reentry_client_order_id": item.reentry_client_order_id,
                    "reentry_side": item.reentry_side,
                    "reentry_quantity_btc": str(item.reentry_quantity_btc),
                    "workoff_trade_ids": list(item.workoff_trade_ids),
                    "workoff_order_ids": list(item.workoff_order_ids),
                    "matched_workoff_btc": str(item.matched_workoff_btc),
                },
            )
            evidence.validate()
            result.append(evidence)
        return tuple(result)

    def evidence(self, *, require_complete: bool = True) -> dict[str, object]:
        causal = self.causal_evidence(require_complete=require_complete)
        fill_ids = {item.trade_id for item in causal}
        special_ids = set(self.special_closed_fills)
        if require_complete and set(self.markouts_usdt) != fill_ids | special_ids:
            raise CampaignError("normal fill markout evidence is incomplete")
        if sum(self.quote_mode_counters.values()) != self.quote_mode_ticks:
            raise CampaignError("quote-mode counters do not reconcile")
        return {
            "quote_mode_ticks": self.quote_mode_ticks,
            "quote_mode_counters": dict(sorted(self.quote_mode_counters.items())),
            "unclassified_quote_mode_ticks": self.unclassified_quote_mode_ticks,
            "causal_reentry": [item.to_dict() for item in causal],
            "markouts_usdt": [
                str(self.markouts_usdt[item.trade_id])
                for item in causal if item.trade_id in self.markouts_usdt
            ],
            "pending_causal_fills": [
                item.to_dict()
                for item in sorted(
                    self.pending_fills.values(),
                    key=lambda row: (row.fill_timestamp_ms, row.trade_id),
                )
            ],
            "special_closed_causal_fills": [
                item.to_dict()
                for item in sorted(
                    self.special_closed_fills.values(),
                    key=lambda row: (row.fill_timestamp_ms, row.trade_id),
                )
            ],
            "special_closed_markouts_usdt": [
                str(self.markouts_usdt[item.trade_id])
                for item in sorted(
                    self.special_closed_fills.values(),
                    key=lambda row: (row.fill_timestamp_ms, row.trade_id),
                )
                if item.trade_id in self.markouts_usdt
            ],
            "session_phase": self.phase.value,
            "create_budget_partition": {
                "admission_create_cap": self.admission_create_cap,
                "workoff_create_reserve": self.workoff_create_reserve,
                "total_create_cap": 60,
            },
            "placement_reason_counters": dict(
                sorted(self.placement_reason_counters.items())
            ),
            "normal_bid_fill_quantity_btc": str(
                self.normal_bid_fill_quantity_btc
            ),
            "normal_ask_fill_quantity_btc": str(
                self.normal_ask_fill_quantity_btc
            ),
            "event_count": len(self.events),
            "event_tail_sha256": self.tail_sha256,
        }

    def snapshot(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "session_id": self.session_id,
            "source_sha256": self.source_sha256,
            "maximum_inventory_btc": str(self.maximum_inventory_btc),
            "quote_mode_counters": dict(sorted(self.quote_mode_counters.items())),
            "quote_mode_ticks": self.quote_mode_ticks,
            "unclassified_quote_mode_ticks": self.unclassified_quote_mode_ticks,
            "pending_fills": {
                key: value.to_dict() for key, value in sorted(self.pending_fills.items())
            },
            "completed_fills": {
                key: value.to_dict() for key, value in sorted(self.completed_fills.items())
            },
            "special_closed_fills": {
                key: value.to_dict()
                for key, value in sorted(self.special_closed_fills.items())
            },
            "markouts_usdt": {
                key: str(value) for key, value in sorted(self.markouts_usdt.items())
            },
            "events": self.events,
            "tail_sha256": self.tail_sha256,
            "last_timestamp_ms": self.last_timestamp_ms,
            "schema_version": self.schema_version,
            "phase": self.phase.value,
            "admission_create_cap": self.admission_create_cap,
            "workoff_create_reserve": self.workoff_create_reserve,
            "placement_reason_counters": dict(
                sorted(self.placement_reason_counters.items())
            ),
            "normal_bid_fill_quantity_btc": str(
                self.normal_bid_fill_quantity_btc
            ),
            "normal_ask_fill_quantity_btc": str(
                self.normal_ask_fill_quantity_btc
            ),
        }
        return {**payload, "snapshot_sha256": canonical_sha256(payload)}

    @classmethod
    def restore(cls, value: Mapping[str, object]) -> "EconomicSessionController":
        raw = dict(value)
        seal = str(raw.pop("snapshot_sha256", ""))
        if len(seal) != 64 or canonical_sha256(raw) != seal:
            raise CampaignError("economic controller snapshot seal mismatch")
        events = raw["events"]
        if not isinstance(events, list):
            raise CampaignError("economic controller events are invalid")
        previous = "GENESIS"
        for sequence, record_value in enumerate(events, start=1):
            record = dict(record_value)
            record_hash = str(record.pop("record_hash", ""))
            if (
                record.get("sequence") != sequence
                or record.get("previous_hash") != previous
                or canonical_sha256(record) != record_hash
            ):
                raise CampaignError("economic controller event chain mismatch")
            previous = record_hash
        if previous != raw["tail_sha256"]:
            raise CampaignError("economic controller tail mismatch")
        pending = raw["pending_fills"]
        completed = raw["completed_fills"]
        special_closed = raw.get("special_closed_fills", {})
        counters = raw["quote_mode_counters"]
        markouts = raw["markouts_usdt"]
        if not all(isinstance(item, Mapping) for item in (
            pending, completed, special_closed, counters, markouts
        )):
            raise CampaignError("economic controller nested state is invalid")
        result = cls(
            session_id=str(raw["session_id"]),
            source_sha256=str(raw["source_sha256"]),
            maximum_inventory_btc=_decimal(
                raw["maximum_inventory_btc"], "maximum_inventory_btc"
            ),
            quote_mode_counters={str(key): int(item) for key, item in counters.items()},
            quote_mode_ticks=int(raw["quote_mode_ticks"]),
            unclassified_quote_mode_ticks=int(raw["unclassified_quote_mode_ticks"]),
            pending_fills={
                str(key): PendingCausalFill.from_dict(item)
                for key, item in pending.items()
            },
            completed_fills={
                str(key): PendingCausalFill.from_dict(item)
                for key, item in completed.items()
            },
            special_closed_fills={
                str(key): PendingCausalFill.from_dict(item)
                for key, item in special_closed.items()
            },
            markouts_usdt={
                str(key): _decimal(item, "markout_usdt") for key, item in markouts.items()
            },
            events=[dict(item) for item in events],
            tail_sha256=str(raw["tail_sha256"]),
            last_timestamp_ms=int(raw["last_timestamp_ms"]),
            schema_version=int(raw["schema_version"]),
            phase=SessionPhase(str(raw.get("phase", SessionPhase.ACTIVE.value))),
            admission_create_cap=int(raw.get("admission_create_cap", 48)),
            workoff_create_reserve=int(raw.get("workoff_create_reserve", 12)),
            placement_reason_counters={
                str(key): int(item)
                for key, item in dict(
                    raw.get("placement_reason_counters", {})
                ).items()
            },
            normal_bid_fill_quantity_btc=_decimal(
                raw.get("normal_bid_fill_quantity_btc", "0"),
                "normal_bid_fill_quantity_btc",
            ),
            normal_ask_fill_quantity_btc=_decimal(
                raw.get("normal_ask_fill_quantity_btc", "0"),
                "normal_ask_fill_quantity_btc",
            ),
        )
        if result.schema_version != 2:
            raise CampaignError("economic controller schema drift")
        return result
