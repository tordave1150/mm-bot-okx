"""Deterministic, network-free operational fault model for a future Demo soak."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Iterable, Mapping, Sequence

from okx_fill_restart_validation import canonical_sha256


class SoakFixtureError(RuntimeError):
    """A modeled operational ambiguity that must fail closed."""


class FixtureDecision(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY_READ_ONLY = "RETRY_READ_ONLY"
    PLACEMENT_BLOCKED = "PLACEMENT_BLOCKED"
    TERMINAL_COMPLETE = "TERMINAL_COMPLETE"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class MutationSnapshot:
    normal_creates: int
    cancels: int
    flatten_dispatches: int
    account_configuration_mutations: int
    live_endpoint_attempts: int
    live_orders: int

    @property
    def total_order_mutations(self) -> int:
        return self.normal_creates + self.cancels + self.flatten_dispatches


@dataclass(frozen=True)
class FillRow:
    trade_id: str
    client_order_id: str
    side: str
    quantity_btc: Decimal
    price_usdt: Decimal
    fee_usdt: Decimal
    timestamp_ms: int

    @property
    def fingerprint(self) -> str:
        return canonical_sha256({
            "trade_id": self.trade_id,
            "client_order_id": self.client_order_id,
            "side": self.side,
            "quantity_btc": str(self.quantity_btc),
            "price_usdt": str(self.price_usdt),
            "fee_usdt": str(self.fee_usdt),
            "timestamp_ms": self.timestamp_ms,
        })


@dataclass(frozen=True)
class AccountPoint:
    binding: str
    position_btc: Decimal
    open_owned_ids: tuple[str, ...] = ()


@dataclass
class BoundedSoakFaultHarness:
    """A small state machine that proves mutation deltas for injected faults."""

    binding: str = "offline-fixture-binding"
    controller_position_btc: Decimal = Decimal("0")
    engine_position_btc: Decimal = Decimal("0")
    pending_intent: str = ""
    ambiguous_intent: str = ""
    open_owned: dict[str, str] = field(default_factory=dict)
    closed_owned: set[str] = field(default_factory=set)
    fill_fingerprints: dict[str, str] = field(default_factory=dict)
    normal_creates: int = 0
    cancels: int = 0
    flatten_dispatches: int = 0
    account_configuration_mutations: int = 0
    live_endpoint_attempts: int = 0
    live_orders: int = 0
    consecutive_absent_observations: int = 0
    halted_reason: str = ""
    terminal_complete: bool = False
    lease_owner: str = ""
    lease_expiry_ms: int = 0
    durable_hash: str = "genesis"
    evidence: list[dict[str, object]] = field(default_factory=list)

    def snapshot(self) -> MutationSnapshot:
        return MutationSnapshot(
            normal_creates=self.normal_creates,
            cancels=self.cancels,
            flatten_dispatches=self.flatten_dispatches,
            account_configuration_mutations=self.account_configuration_mutations,
            live_endpoint_attempts=self.live_endpoint_attempts,
            live_orders=self.live_orders,
        )

    def _record(self, event: str, **payload: object) -> None:
        item = {"event": event, **payload}
        item["previous_hash"] = self.durable_hash
        self.durable_hash = canonical_sha256(item)
        item["record_hash"] = self.durable_hash
        self.evidence.append(item)

    def _halt(self, reason: str) -> FixtureDecision:
        if not self.halted_reason:
            self.halted_reason = reason
            self._record("FAIL_CLOSED", reason=reason)
        return FixtureDecision.FAIL_CLOSED

    @property
    def placement_allowed(self) -> bool:
        return not any((
            self.halted_reason,
            self.terminal_complete,
            self.pending_intent,
            self.ambiguous_intent,
        ))

    def acquire_lease(self, owner: str, *, now_ms: int, ttl_ms: int) -> None:
        if not owner or ttl_ms <= 0:
            raise SoakFixtureError("invalid single-instance lease")
        if self.lease_owner and now_ms <= self.lease_expiry_ms:
            raise SoakFixtureError("single-instance lease collision")
        self.lease_owner = owner
        self.lease_expiry_ms = now_ms + ttl_ms
        self._record("LEASE_ACQUIRED", owner=owner, expiry_ms=self.lease_expiry_ms)

    def restart(
        self,
        owner: str,
        *,
        now_ms: int,
        ttl_ms: int,
        expected_durable_hash: str,
    ) -> FixtureDecision:
        if expected_durable_hash != self.durable_hash:
            return self._halt("DURABLE_HASH_MISMATCH")
        try:
            self.acquire_lease(owner, now_ms=now_ms, ttl_ms=ttl_ms)
        except SoakFixtureError:
            return self._halt("SINGLE_INSTANCE_COLLISION")
        self._record(
            "PROCESS_RESTORED",
            pending_intent=bool(self.pending_intent),
            ambiguous_intent=bool(self.ambiguous_intent),
            owned_open_orders=len(self.open_owned),
        )
        return (
            FixtureDecision.PLACEMENT_BLOCKED
            if self.pending_intent or self.ambiguous_intent
            else FixtureDecision.CONTINUE
        )

    def begin_create(self, client_order_id: str) -> None:
        if not self.placement_allowed or not client_order_id:
            raise SoakFixtureError("create intent is not permitted")
        self.pending_intent = client_order_id
        self._record("CREATE_INTENT_PERSISTED", client_order_id=client_order_id)

    def timeout_before_dispatch(self) -> FixtureDecision:
        if not self.pending_intent:
            raise SoakFixtureError("no pending create intent")
        client_id = self.pending_intent
        self.pending_intent = ""
        self._record("TIMEOUT_BEFORE_DISPATCH", client_order_id=client_id)
        return FixtureDecision.CONTINUE

    def block_before_dispatch(
        self,
        client_order_id: str,
        *,
        classification: str,
    ) -> FixtureDecision:
        if (
            client_order_id != self.pending_intent
            or self.ambiguous_intent
            or classification != "PRE_DISPATCH_POST_ONLY_WOULD_CROSS"
        ):
            self._halt("UNBOUND_PRE_DISPATCH_BLOCK")
            raise SoakFixtureError("pre-dispatch block is not authoritative")
        self.pending_intent = ""
        self._record(
            "CREATE_BLOCKED_BEFORE_DISPATCH",
            client_order_id=client_order_id,
            classification=classification,
        )
        return FixtureDecision.CONTINUE

    def dispatch_create(self) -> None:
        if not self.pending_intent or self.ambiguous_intent:
            raise SoakFixtureError("create dispatch is not permitted")
        self.normal_creates += 1
        self.ambiguous_intent = self.pending_intent
        self.pending_intent = ""
        self._record("CREATE_DISPATCHED", client_order_id=self.ambiguous_intent)

    def timeout_after_dispatch(self) -> FixtureDecision:
        if not self.ambiguous_intent:
            raise SoakFixtureError("no dispatched create intent")
        self._record("TIMEOUT_AFTER_DISPATCH", client_order_id=self.ambiguous_intent)
        return FixtureDecision.PLACEMENT_BLOCKED

    def acknowledge_create(self, client_order_id: str, order_id: str) -> None:
        if client_order_id in self.open_owned:
            if self.open_owned[client_order_id] != order_id:
                self._halt("CONFLICTING_CREATE_ACKNOWLEDGEMENT")
                raise SoakFixtureError("conflicting duplicate create acknowledgement")
            self._record("DUPLICATE_CREATE_ACK_IGNORED", client_order_id=client_order_id)
            return
        if client_order_id != self.ambiguous_intent or not order_id:
            self._halt("UNBOUND_CREATE_ACKNOWLEDGEMENT")
            raise SoakFixtureError("create acknowledgement identity mismatch")
        self.open_owned[client_order_id] = order_id
        self.ambiguous_intent = ""
        self.consecutive_absent_observations = 0
        self._record("CREATE_ACKNOWLEDGED", client_order_id=client_order_id)

    def acknowledge_rejected_create(
        self,
        client_order_id: str,
        *,
        classification: str,
    ) -> None:
        """Consume one dispatched intent after authoritative terminal rejection."""
        if (
            client_order_id != self.ambiguous_intent
            or classification != "REJECTED_TERMINAL_ABSENT"
        ):
            self._halt("UNBOUND_REJECTED_CREATE_ACKNOWLEDGEMENT")
            raise SoakFixtureError("rejected create acknowledgement is not authoritative")
        self.ambiguous_intent = ""
        self.consecutive_absent_observations = 0
        self._record(
            "CREATE_REJECTION_RECONCILED",
            client_order_id=client_order_id,
            classification=classification,
            mutation_retry=False,
        )

    def reconcile_dispatched_absent(self) -> FixtureDecision:
        if not self.ambiguous_intent:
            raise SoakFixtureError("no ambiguous create to reconcile")
        self.consecutive_absent_observations += 1
        self._record(
            "AMBIGUOUS_CREATE_ABSENT",
            observations=self.consecutive_absent_observations,
        )
        if self.consecutive_absent_observations < 2:
            return FixtureDecision.PLACEMENT_BLOCKED
        self.ambiguous_intent = ""
        self.consecutive_absent_observations = 0
        return FixtureDecision.CONTINUE

    def cancel_owned(self, client_order_id: str) -> None:
        if client_order_id not in self.open_owned:
            self._halt("FOREIGN_OR_UNKNOWN_CANCEL")
            raise SoakFixtureError("cancel is not owned")
        self.cancels += 1
        self.open_owned.pop(client_order_id)
        self.closed_owned.add(client_order_id)
        self._record("OWNED_CANCEL_DISPATCHED", client_order_id=client_order_id)

    def ingest_fill(self, fill: FillRow) -> bool:
        previous = self.fill_fingerprints.get(fill.trade_id)
        if previous:
            if previous != fill.fingerprint:
                self._halt("CONFLICTING_DUPLICATE_FILL")
                raise SoakFixtureError("conflicting duplicate fill")
            self._record("DUPLICATE_FILL_IGNORED", trade_id=fill.trade_id)
            return False
        if fill.client_order_id not in self.open_owned and (
            fill.client_order_id != self.ambiguous_intent
            and fill.client_order_id not in self.closed_owned
        ):
            self._halt("FOREIGN_FILL_IDENTITY")
            raise SoakFixtureError("fill does not belong to controller")
        if fill.side not in {"buy", "sell"} or fill.quantity_btc <= 0:
            self._halt("INVALID_FILL")
            raise SoakFixtureError("fill is invalid")
        signed = fill.quantity_btc if fill.side == "buy" else -fill.quantity_btc
        self.controller_position_btc += signed
        self.engine_position_btc += signed
        self.fill_fingerprints[fill.trade_id] = fill.fingerprint
        self.ambiguous_intent = ""
        self.open_owned.pop(fill.client_order_id, None)
        self.closed_owned.add(fill.client_order_id)
        self._record("FILL_INGESTED", trade_id=fill.trade_id, side=fill.side)
        return True

    @staticmethod
    def union_fills(
        paginated_history: Sequence[FillRow], recent_tail: Sequence[FillRow]
    ) -> tuple[FillRow, ...]:
        by_trade: dict[str, FillRow] = {}
        for fill in (*paginated_history, *recent_tail):
            previous = by_trade.get(fill.trade_id)
            if previous and previous.fingerprint != fill.fingerprint:
                raise SoakFixtureError("fill union conflict")
            by_trade[fill.trade_id] = fill
        return tuple(sorted(
            by_trade.values(), key=lambda item: (item.timestamp_ms, item.trade_id)
        ))

    def start_flatten(self) -> None:
        if self.flatten_dispatches or self.controller_position_btc == 0:
            raise SoakFixtureError("single-flight flatten is not permitted")
        if self.pending_intent or self.ambiguous_intent or self.open_owned:
            raise SoakFixtureError("flatten requires no unresolved normal order")
        self.flatten_dispatches = 1
        self._record(
            "REDUCE_ONLY_FLATTEN_DISPATCHED",
            position_btc=str(self.controller_position_btc),
        )

    def flatten_partial(self, trade_id: str, quantity_btc: Decimal) -> None:
        if self.flatten_dispatches != 1 or not trade_id or quantity_btc <= 0:
            raise SoakFixtureError("flatten partial is invalid")
        fingerprint = canonical_sha256({
            "trade_id": trade_id,
            "quantity_btc": str(quantity_btc),
            "direction": "reduce",
        })
        previous = self.fill_fingerprints.get(trade_id)
        if previous:
            if previous != fingerprint:
                self._halt("CONFLICTING_FLATTEN_FILL")
                raise SoakFixtureError("conflicting flatten partial")
            return
        direction = Decimal("-1") if self.controller_position_btc > 0 else Decimal("1")
        next_position = self.controller_position_btc + direction * quantity_btc
        if self.controller_position_btc * next_position < 0:
            self._halt("FLATTEN_OVERFILL")
            raise SoakFixtureError("flatten crossed through flat")
        self.controller_position_btc = next_position
        self.engine_position_btc = next_position
        self.fill_fingerprints[trade_id] = fingerprint
        self._record("FLATTEN_PARTIAL_INGESTED", trade_id=trade_id)

    def inject_read_failure(self, attempt: int, maximum_attempts: int) -> FixtureDecision:
        if attempt < 1 or maximum_attempts < 1:
            return self._halt("INVALID_READ_RETRY_BUDGET")
        self._record(
            "READ_FAILURE_INJECTED",
            attempt=attempt,
            maximum_attempts=maximum_attempts,
        )
        if attempt < maximum_attempts:
            return FixtureDecision.RETRY_READ_ONLY
        return self._halt("READ_RETRY_EXHAUSTED")

    @staticmethod
    def classify_book(
        *,
        bid: object,
        ask: object,
        signed_age_ms: object,
        maximum_age_ms: int,
        maximum_clock_skew_ms: int,
    ) -> FixtureDecision:
        if maximum_age_ms < 0 or maximum_clock_skew_ms < 0:
            return FixtureDecision.FAIL_CLOSED
        try:
            bid_value = Decimal(str(bid))
            ask_value = Decimal(str(ask))
            age = int(signed_age_ms)
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return FixtureDecision.FAIL_CLOSED
        if not bid_value.is_finite() or not ask_value.is_finite():
            return FixtureDecision.FAIL_CLOSED
        if bid_value <= 0 or ask_value <= 0 or bid_value >= ask_value:
            return FixtureDecision.FAIL_CLOSED
        if age < -maximum_clock_skew_ms or age > maximum_age_ms:
            return FixtureDecision.PLACEMENT_BLOCKED
        return FixtureDecision.CONTINUE

    def terminal_reconcile(
        self,
        snapshots: Sequence[AccountPoint],
        *,
        evidence_write_succeeds: bool = True,
    ) -> FixtureDecision:
        if len(snapshots) != 2:
            return self._halt("TERMINAL_SNAPSHOT_COUNT")
        if any(snapshot.binding != self.binding for snapshot in snapshots):
            return self._halt("TERMINAL_BINDING_MISMATCH")
        if any(snapshot.position_btc != 0 for snapshot in snapshots):
            return self._halt("TERMINAL_NON_FLAT")
        if any(snapshot.open_owned_ids for snapshot in snapshots):
            return self._halt("TERMINAL_OPEN_ORDERS")
        if any((
            self.controller_position_btc != 0,
            self.engine_position_btc != 0,
            bool(self.open_owned),
            bool(self.pending_intent),
            bool(self.ambiguous_intent),
        )):
            return self._halt("TERMINAL_DURABLE_STATE_MISMATCH")
        if not evidence_write_succeeds:
            return self._halt("TERMINAL_EVIDENCE_WRITE_FAILED")
        self._record("TERMINAL_ACCOUNT_PAIR_RECONCILED", snapshots=2)
        self.terminal_complete = True
        return FixtureDecision.TERMINAL_COMPLETE


def scenario_matrix() -> tuple[dict[str, object], ...]:
    """Return the frozen fault matrix used by evidence and review tooling."""

    rows = (
        ("timeout_before_dispatch", "CONTINUE", 0, "no transport mutation"),
        ("timeout_after_dispatch", "PLACEMENT_BLOCKED", 1, "authoritative resolution"),
        ("duplicate_fill", "CONTINUE", 0, "idempotent fingerprint"),
        ("conflicting_duplicate_fill", "FAIL_CLOSED", 0, "conflict evidence"),
        ("paginated_tail_omission", "CONTINUE", 0, "union and deduplicate"),
        ("read_rate_limit", "RETRY_READ_ONLY", 0, "bounded read retry"),
        ("read_retry_exhaustion", "FAIL_CLOSED", 0, "terminal decision"),
        ("future_beyond_skew", "PLACEMENT_BLOCKED", 0, "fresh book"),
        ("single_instance_collision", "FAIL_CLOSED", 0, "lease expiry"),
        ("durable_hash_mismatch", "FAIL_CLOSED", 0, "manual recovery"),
        ("multi_partial_flatten", "CONTINUE", 1, "flat reconciliation"),
        ("terminal_account_pair", "TERMINAL_COMPLETE", 0, "completion evidence"),
    )
    return tuple({
        "scenario": name,
        "expected_decision": decision,
        "maximum_order_mutation_delta": delta,
        "required_evidence": evidence,
        "network_allowed": False,
        "live_allowed": False,
        "account_configuration_mutation_allowed": False,
    } for name, decision, delta, evidence in rows)


def public_state(harness: BoundedSoakFaultHarness) -> Mapping[str, object]:
    """Sanitized public state for evidence fixtures."""

    return {
        "mutation_snapshot": asdict(harness.snapshot()),
        "position_btc": str(harness.controller_position_btc),
        "owned_open_orders": len(harness.open_owned),
        "pending_intent": bool(harness.pending_intent),
        "ambiguous_intent": bool(harness.ambiguous_intent),
        "halted_reason": harness.halted_reason,
        "terminal_complete": harness.terminal_complete,
        "evidence_records": len(harness.evidence),
        "durable_hash": harness.durable_hash,
    }
