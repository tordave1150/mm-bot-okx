"""Durable fail-closed supervisor for one frozen A2 economic campaign.

The supervisor is a local control plane.  It has no exchange client and no
credential loader.  It arms an exact frozen campaign, authorizes at most one
predeclared child session at a time, accepts sealed terminal session evidence,
and writes the campaign terminal marker last.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from okx_demo_multi_session_campaign import (
    CampaignDecision,
    CampaignError,
    CampaignLimits,
    CampaignManifest,
    CampaignRegistry,
    SessionEvidence,
    verify_registry,
)
from okx_demo_multi_session_prepare import (
    CAMPAIGN_ARTIFACT_ROOT,
    FORMAL_COMPLETED_SHA256,
    FORMAL_PACKAGE_ID,
    READY_STATUS,
    SESSION_ARTIFACT_ROOT,
    SOAK_COMPLETED_SHA256,
    SOAK_PACKAGE_ID,
)
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_validation import canonical_sha256


class CampaignSupervisorError(RuntimeError):
    pass


def _attempted_aggregate(
    completed_aggregate: dict[str, object],
    failed_aggregate: dict[str, object],
    *,
    attempted_session_count: int,
) -> dict[str, object]:
    attempted = dict(completed_aggregate)
    for key in (
        "normal_creates",
        "normal_create_dispatches",
        "normal_create_acknowledgements",
        "normal_create_rejections",
        "normal_create_unresolved",
        "normal_cancels",
        "normal_bid_fills",
        "normal_ask_fills",
        "normal_fifo_round_trips",
        "markout_count",
        "causal_reentry_records",
        "unclassified_quote_mode_ticks",
        "special_flatten_sessions",
        "unsafe_sessions",
    ):
        attempted[key] = int(completed_aggregate.get(key, 0)) + int(
            failed_aggregate.get(key, 0)
        )
    attempted["normal_fill_count"] = (
        int(attempted["normal_bid_fills"])
        + int(attempted["normal_ask_fills"])
    )
    larger = max(
        int(attempted["normal_bid_fills"]),
        int(attempted["normal_ask_fills"]),
        1,
    )
    attempted["fill_balance"] = str(
        Decimal(min(
            int(attempted["normal_bid_fills"]),
            int(attempted["normal_ask_fills"]),
        )) / Decimal(larger)
    )
    for key in (
        "realized_spread_pnl_usdt",
        "inventory_pnl_usdt",
        "normal_gross_pnl_usdt",
        "normal_fees_usdt",
        "normal_net_pnl_usdt",
        "special_gross_pnl_usdt",
        "special_fees_usdt",
        "special_net_pnl_usdt",
    ):
        attempted[key] = str(
            Decimal(str(completed_aggregate.get(key, "0")))
            + Decimal(str(failed_aggregate.get(key, "0")))
        )
    attempted["aggregate_gross_pnl_usdt"] = str(
        Decimal(str(attempted["normal_gross_pnl_usdt"]))
        + Decimal(str(attempted["special_gross_pnl_usdt"]))
    )
    attempted["aggregate_fees_usdt"] = str(
        Decimal(str(attempted["normal_fees_usdt"]))
        + Decimal(str(attempted["special_fees_usdt"]))
    )
    attempted["aggregate_net_pnl_usdt"] = str(
        Decimal(str(attempted["normal_net_pnl_usdt"]))
        + Decimal(str(attempted["special_net_pnl_usdt"]))
    )
    attempted["maximum_session_drawdown_usdt"] = str(max(
        Decimal(str(completed_aggregate.get(
            "maximum_session_drawdown_usdt", "0"
        ))),
        Decimal(str(failed_aggregate.get("maximum_drawdown_usdt", "0"))),
    ))
    attempted["create_counter_reconciles"] = (
        int(attempted["normal_create_dispatches"])
        == int(attempted["normal_create_acknowledgements"])
        + int(attempted["normal_create_rejections"])
        + int(attempted["normal_create_unresolved"])
    )
    attempted["economic_attribution_reconciles"] = all((
        Decimal(str(attempted["realized_spread_pnl_usdt"]))
        + Decimal(str(attempted["inventory_pnl_usdt"]))
        == Decimal(str(attempted["normal_gross_pnl_usdt"])),
        Decimal(str(attempted["normal_gross_pnl_usdt"]))
        - Decimal(str(attempted["normal_fees_usdt"]))
        == Decimal(str(attempted["normal_net_pnl_usdt"])),
        Decimal(str(attempted["special_gross_pnl_usdt"]))
        - Decimal(str(attempted["special_fees_usdt"]))
        == Decimal(str(attempted["special_net_pnl_usdt"])),
        Decimal(str(attempted["aggregate_gross_pnl_usdt"]))
        - Decimal(str(attempted["aggregate_fees_usdt"]))
        == Decimal(str(attempted["aggregate_net_pnl_usdt"])),
    ))
    attempted["attempted_session_count"] = attempted_session_count
    return attempted


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CampaignSupervisorError(f"durable JSON unavailable: {path.name}") from exc
    if not isinstance(value, dict):
        raise CampaignSupervisorError(f"durable JSON is not an object: {path.name}")
    return value


def _sealed(payload: Mapping[str, object], field: str) -> dict[str, object]:
    core = dict(payload)
    return {**core, field: canonical_sha256(core)}


def _verify_seal(value: Mapping[str, object], field: str) -> dict[str, object]:
    raw = dict(value)
    expected = str(raw.pop(field, ""))
    if len(expected) != 64 or canonical_sha256(raw) != expected:
        raise CampaignSupervisorError(f"{field} seal mismatch")
    return raw


@dataclass(frozen=True)
class A2CampaignPackage:
    root: Path
    output: Path
    spec: dict[str, object]
    slots: tuple[dict[str, object], ...]

    @property
    def expected_campaign_arm_token(self) -> str:
        return f"OKX_DEMO:{self.spec['campaign_session_id']}"

    def session_output(self, slot: Mapping[str, object]) -> Path:
        return self.root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])


def load_campaign_package(
    root: Path,
    package_id: str,
    *,
    expected_protocol_id: str = "okx-demo-multi-session-economic-campaign-v1",
    expected_ready_status: str = READY_STATUS,
) -> A2CampaignPackage:
    root = root.resolve()
    if not package_id.startswith("economic-package-"):
        raise CampaignSupervisorError("A2 campaign package identity is invalid")
    output = root / CAMPAIGN_ARTIFACT_ROOT / package_id
    terminal_path = output / "A2_PACKAGE_COMPLETED.json"
    spec_path = output / "specification/campaign_package_spec.json"
    source_path = output / "specification/source_hashes.json"
    child_path = output / "specification/session_package_audits.json"
    completion_path = output / "completion_hashes.json"
    if not all(path.is_file() for path in (
        terminal_path, spec_path, source_path, child_path, completion_path
    )):
        raise CampaignSupervisorError("A2 campaign package is incomplete")
    terminal = _read_json(terminal_path)
    spec = _read_json(spec_path)
    if any((
        terminal.get("status") != expected_ready_status,
        terminal.get("package_id") != package_id,
        terminal.get("campaign_authorized") is not False,
        terminal.get("campaign_executed") is not False,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        spec.get("package_id") != package_id,
        spec.get("protocol_id") != expected_protocol_id,
        spec.get("campaign_authorized") is not False,
        spec.get("campaign_executed") is not False,
        spec.get("session_count") != CampaignLimits().maximum_sessions,
    )):
        raise CampaignSupervisorError("A2 campaign package boundary is invalid")
    canonical_spec = dict(spec)
    expected_spec_hash = str(canonical_spec.pop("specification_sha256", ""))
    if canonical_sha256(canonical_spec) != expected_spec_hash:
        raise CampaignSupervisorError("A2 campaign specification hash mismatch")
    completion = _read_json(completion_path)
    failures = [
        relative for relative, expected in completion.items()
        if not (output / relative).is_file()
        or _sha256(output / relative) != expected
    ]
    if failures or _sha256(completion_path) != terminal.get("completion_hashes_sha256"):
        raise CampaignSupervisorError("A2 campaign completion hash mismatch")
    sources = _read_json(source_path)
    source_failures = [
        relative for relative, expected in sources.items()
        if not (root / relative).is_file() or _sha256(root / relative) != expected
    ]
    if source_failures or canonical_sha256(sources) != spec.get("source_manifest_sha256"):
        raise CampaignSupervisorError("A2 campaign source binding mismatch")
    slots = tuple(dict(item) for item in list(spec.get("session_slots") or []))
    if (
        len(slots) != CampaignLimits().maximum_sessions
        or [item.get("slot") for item in slots] != list(range(1, 13))
        or len({item.get("package_id") for item in slots}) != 12
        or len({item.get("run_id") for item in slots}) != 12
        or len({item.get("session_id") for item in slots}) != 12
    ):
        raise CampaignSupervisorError("A2 session slot identities are invalid")
    child = _read_json(child_path)
    audits = list(child.get("packages") or [])
    if child.get("count") != 12 or len(audits) != 12:
        raise CampaignSupervisorError("A2 child package audit is incomplete")
    for slot, audit in zip(slots, audits, strict=True):
        child_output = root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])
        child_terminal = child_output / "SOAK_PACKAGE_COMPLETED.json"
        child_completion = child_output / "completion_hashes.json"
        if any((
            audit.get("package_id") != slot.get("package_id"),
            not child_terminal.is_file(),
            not child_completion.is_file(),
            _sha256(child_terminal) != audit.get("terminal_sha256"),
            _sha256(child_completion) != audit.get("completion_hashes_sha256"),
        )):
            raise CampaignSupervisorError("A2 child package binding mismatch")
    return A2CampaignPackage(root, output, spec, slots)


class CampaignSupervisor:
    """Single-instance durable sequencer for predeclared A2 sessions."""

    def __init__(self, package: A2CampaignPackage):
        self.package = package
        self.run = package.output / "campaign_run"
        self.registry_root = self.run / "registry"
        self.marker_path = self.run / "A2_CAMPAIGN_ARMED.json"
        self.state_path = self.run / "state/supervisor_state.json"
        self.events_path = self.run / "streams/supervisor_events.jsonl"
        self.lease_path = self.run / "state/campaign_lease.json"

    @classmethod
    def arm(
        cls,
        package: A2CampaignPackage,
        *,
        campaign_arm_token: str,
        now_ms: int,
    ) -> "CampaignSupervisor":
        result = cls(package)
        if result.run.exists():
            raise CampaignSupervisorError("A2 campaign execution reuse refused")
        if now_ms <= 0:
            raise CampaignSupervisorError("A2 campaign arm timestamp is invalid")
        expected = package.expected_campaign_arm_token
        expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
        if (
            campaign_arm_token != expected
            or expected_hash != package.spec.get("campaign_arm_token_sha256")
        ):
            raise CampaignSupervisorError("A2 campaign arm token mismatch")
        result.run.mkdir(parents=True)
        _write_json(result.marker_path, {
            "package_id": package.spec["package_id"],
            "campaign_id": package.spec["campaign_id"],
            "run_id": package.spec["run_id"],
            "campaign_session_id": package.spec["campaign_session_id"],
            "armed_at_ms": now_ms,
            "arm_token_sha256": expected_hash,
            "arm_token_serialized": False,
            "scope": "A2_MULTI_SESSION_ECONOMIC_SOAK",
            "normal_post_only_orders_authorized": True,
            "single_flight_reduce_only_flatten_authorized": True,
            "live_authorized": False,
            "account_configuration_mutation_authorized": False,
            "production_authorized": False,
        })
        manifest = CampaignManifest(
            campaign_id=str(package.spec["campaign_id"]),
            source_sha256=str(package.spec["source_manifest_sha256"]),
            created_at_ms=now_ms,
            formal_predecessor=FORMAL_PACKAGE_ID,
            soak_predecessor=SOAK_PACKAGE_ID,
            formal_completed_sha256=FORMAL_COMPLETED_SHA256,
            soak_completed_sha256=SOAK_COMPLETED_SHA256,
            limits=CampaignLimits.from_dict(
                dict(package.spec["campaign_limits"])
            ),
        )
        CampaignRegistry.initialize(result.registry_root, manifest)
        result._write_state({
            "campaign_id": package.spec["campaign_id"],
            "armed_at_ms": now_ms,
            "active_slot": None,
            "completed_slots": [],
            "failed_slots": [],
            "failed_session_aggregate": {
                "normal_creates": 0,
                "normal_cancels": 0,
                "normal_bid_fills": 0,
                "normal_ask_fills": 0,
                "normal_fifo_round_trips": 0,
                "normal_net_pnl_usdt": "0",
                "special_net_pnl_usdt": "0",
                "aggregate_net_pnl_usdt": "0",
            },
            "terminal_account_authoritative": False,
            "terminal_final_position_btc": None,
            "terminal_final_open_orders": None,
            "terminal_decision": None,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        result._append_event("CAMPAIGN_ARMED", {
            "marker_sha256": _sha256(result.marker_path),
            "orders_dispatched": 0,
        })
        return result

    @classmethod
    def load(cls, package: A2CampaignPackage) -> "CampaignSupervisor":
        result = cls(package)
        if not all(path.is_file() for path in (
            result.marker_path, result.state_path, result.events_path
        )):
            raise CampaignSupervisorError("A2 campaign durable state is incomplete")
        marker = _read_json(result.marker_path)
        if any((
            marker.get("package_id") != package.spec.get("package_id"),
            marker.get("campaign_id") != package.spec.get("campaign_id"),
            marker.get("arm_token_serialized") is not False,
            marker.get("production_authorized") is not False,
        )):
            raise CampaignSupervisorError("A2 campaign marker binding mismatch")
        result.state()
        result.events()
        registry = CampaignRegistry.load(result.registry_root)
        state = result.state()
        sessions = registry.sessions()
        completed = list(state["completed_slots"])
        if len(sessions) != len(completed) or completed != list(range(1, len(completed) + 1)):
            raise CampaignSupervisorError("A2 supervisor/registry reconciliation mismatch")
        return result

    def _write_state(self, payload: Mapping[str, object]) -> None:
        _write_json(self.state_path, _sealed(payload, "state_sha256"))

    def state(self) -> dict[str, object]:
        return _verify_seal(_read_json(self.state_path), "state_sha256")

    def events(self) -> tuple[dict[str, object], ...]:
        if not self.events_path.is_file():
            raise CampaignSupervisorError("A2 supervisor event stream is missing")
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        previous = "GENESIS"
        rows: list[dict[str, object]] = []
        for sequence, line in enumerate(lines, start=1):
            try:
                raw = json.loads(line)
            except Exception as exc:
                raise CampaignSupervisorError("A2 supervisor event stream is corrupt") from exc
            expected = str(raw.pop("event_sha256", ""))
            if any((
                raw.get("sequence") != sequence,
                raw.get("previous_sha256") != previous,
                raw.get("campaign_id") != self.package.spec.get("campaign_id"),
                canonical_sha256(raw) != expected,
            )):
                raise CampaignSupervisorError("A2 supervisor event chain mismatch")
            raw["event_sha256"] = expected
            rows.append(raw)
            previous = expected
        if not rows or rows[0]["event"] != "CAMPAIGN_ARMED":
            raise CampaignSupervisorError("A2 supervisor event genesis is invalid")
        return tuple(rows)

    def _append_event(self, event: str, payload: Mapping[str, object]) -> None:
        rows = self.events() if self.events_path.exists() else ()
        core = {
            "sequence": len(rows) + 1,
            "campaign_id": self.package.spec["campaign_id"],
            "event": event,
            "payload": dict(payload),
            "previous_sha256": rows[-1]["event_sha256"] if rows else "GENESIS",
        }
        sealed = {**core, "event_sha256": canonical_sha256(core)}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if self.events_path.exists() else "x"
        with self.events_path.open(mode, encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(sealed, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def acquire_lease(self, *, owner: str, now_ms: int, ttl_ms: int) -> None:
        maximum_ttl = CampaignLimits().maximum_campaign_wall_ms + 5 * 60 * 1000
        if not owner or now_ms <= 0 or not 0 < ttl_ms <= maximum_ttl:
            raise CampaignSupervisorError("A2 campaign lease request is invalid")
        recovered = False
        if self.lease_path.exists():
            lease = _verify_seal(_read_json(self.lease_path), "lease_sha256")
            if any((
                lease.get("campaign_id") != self.package.spec.get("campaign_id"),
                lease.get("source_sha256") != self.package.spec.get("source_manifest_sha256"),
            )):
                raise CampaignSupervisorError("A2 campaign lease binding mismatch")
            if lease.get("status") == "ACTIVE" and now_ms <= int(lease["expires_at_ms"]):
                raise CampaignSupervisorError("A2 campaign lease collision")
            recovered = lease.get("status") == "ACTIVE"
        payload = {
            "campaign_id": self.package.spec["campaign_id"],
            "source_sha256": self.package.spec["source_manifest_sha256"],
            "owner": owner,
            "acquired_at_ms": now_ms,
            "expires_at_ms": now_ms + ttl_ms,
            "status": "ACTIVE",
            "stale_lease_recovered": recovered,
        }
        _write_json(self.lease_path, _sealed(payload, "lease_sha256"))
        self._append_event("LEASE_ACQUIRED", {
            "owner_sha256": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
            "stale_recovery": recovered,
            "expires_at_ms": now_ms + ttl_ms,
        })

    def _require_owner(self, owner: str, now_ms: int) -> None:
        lease = _verify_seal(_read_json(self.lease_path), "lease_sha256")
        if any((
            lease.get("status") != "ACTIVE",
            lease.get("owner") != owner,
            now_ms > int(lease.get("expires_at_ms", 0)),
        )):
            raise CampaignSupervisorError("A2 campaign lease is not authoritative")

    def authorize_next_session(
        self, *, owner: str, now_ms: int
    ) -> dict[str, object]:
        self._require_owner(owner, now_ms)
        state = self.state()
        if state["terminal_decision"] is not None:
            raise CampaignSupervisorError("A2 campaign is terminal")
        if state["active_slot"] is not None:
            raise CampaignSupervisorError("A2 campaign already has an active session")
        elapsed = now_ms - int(state["armed_at_ms"])
        limits = CampaignLimits.from_dict(dict(self.package.spec["campaign_limits"]))
        if elapsed < 0 or elapsed > limits.maximum_campaign_wall_ms:
            self.fail_closed(owner=owner, now_ms=now_ms, reason="CAMPAIGN_WALL_BUDGET")
            raise CampaignSupervisorError("A2 campaign wall budget exhausted")
        completed = list(state["completed_slots"])
        if len(completed) >= limits.maximum_sessions:
            raise CampaignSupervisorError("A2 campaign session budget exhausted")
        slot = dict(self.package.slots[len(completed)])
        child = self.package.session_output(slot)
        if (child / "soak_run/SOAK_EXECUTION_ARMED.json").exists():
            self.fail_closed(owner=owner, now_ms=now_ms, reason="SESSION_MARKER_REUSE")
            raise CampaignSupervisorError("A2 child execution marker reuse refused")
        gate = {
            "campaign_package_id": self.package.spec["package_id"],
            "campaign_id": self.package.spec["campaign_id"],
            "campaign_run_id": self.package.spec["run_id"],
            "campaign_slot": slot["slot"],
            "session_package_id": slot["package_id"],
            "session_run_id": slot["run_id"],
            "session_id": slot["session_id"],
            "source_manifest_sha256": self.package.spec["source_manifest_sha256"],
            "campaign_arm_marker_sha256": _sha256(self.marker_path),
            "session_arm_token_sha256": slot["arm_token_sha256"],
            "session_arm_token_serialized": False,
            "authorized_at_ms": now_ms,
            "status": "ACTIVE_SINGLE_SESSION",
            "normal_post_only_orders_authorized": True,
            "single_flight_reduce_only_flatten_authorized": True,
            "live_authorized": False,
            "account_configuration_mutation_authorized": False,
            "production_authorized": False,
        }
        gate["authorization_sha256"] = canonical_sha256(gate)
        gate_path = child / "campaign_authorization.json"
        if gate_path.exists():
            raise CampaignSupervisorError("A2 session authorization reuse refused")
        _write_json(gate_path, gate)
        state["active_slot"] = slot["slot"]
        state["terminal_account_authoritative"] = False
        state["terminal_final_position_btc"] = None
        state["terminal_final_open_orders"] = None
        self._write_state(state)
        self._append_event("SESSION_AUTHORIZED", {
            "slot": slot["slot"],
            "package_id": slot["package_id"],
            "authorization_sha256": gate["authorization_sha256"],
        })
        return slot

    def accept_session(
        self,
        *,
        owner: str,
        now_ms: int,
        sealed_session: Mapping[str, object],
    ) -> CampaignDecision:
        self._require_owner(owner, now_ms)
        state = self.state()
        active = state.get("active_slot")
        if not isinstance(active, int) or active < 1 or active > 12:
            raise CampaignSupervisorError("A2 campaign has no active session")
        slot = dict(self.package.slots[active - 1])
        try:
            session = SessionEvidence.from_dict(
                sealed_session,
                CampaignLimits.from_dict(dict(self.package.spec["campaign_limits"])),
            )
            if any((
                session.session_id != slot["session_id"],
                session.run_id != slot["run_id"],
                session.source_sha256 != self.package.spec["source_manifest_sha256"],
                session.ended_at_ms > int(state["armed_at_ms"])
                + CampaignLimits().maximum_campaign_wall_ms,
            )):
                raise CampaignError("session/package identity or wall binding mismatch")
            decision = CampaignRegistry.load(self.registry_root).register_session(session)
        except Exception as exc:
            self.fail_closed(
                owner=owner,
                now_ms=now_ms,
                reason=f"SESSION_EVIDENCE_REJECTED:{type(exc).__name__}",
            )
            raise CampaignSupervisorError("A2 session evidence rejected fail closed") from exc
        completed = list(state["completed_slots"])
        completed.append(active)
        state["completed_slots"] = completed
        state["active_slot"] = None
        state["terminal_account_authoritative"] = True
        state["terminal_final_position_btc"] = "0"
        state["terminal_final_open_orders"] = 0
        if decision is not CampaignDecision.IN_PROGRESS:
            state["terminal_decision"] = decision.value
        self._write_state(state)
        consumed_path = (
            self.package.session_output(slot) / "campaign_authorization_consumed.json"
        )
        if consumed_path.exists():
            raise CampaignSupervisorError("A2 session authorization consumption reused")
        _write_json(consumed_path, {
            "campaign_id": self.package.spec["campaign_id"],
            "slot": active,
            "session_id": session.session_id,
            "session_evidence_sha256": session.evidence_sha256,
            "consumed_at_ms": now_ms,
            "orders_authorized_beyond_session": False,
            "production_authorized": False,
        })
        self._append_event("SESSION_ACCEPTED", {
            "slot": active,
            "session_sha256": session.evidence_sha256,
            "decision": decision.value,
        })
        if decision is not CampaignDecision.IN_PROGRESS:
            self._finalize(decision)
        return decision

    def accept_active_session_artifact(
        self, *, owner: str, now_ms: int
    ) -> CampaignDecision:
        """Accept only hash-verified terminal evidence from the active child."""
        self._require_owner(owner, now_ms)
        state = self.state()
        active = state.get("active_slot")
        if not isinstance(active, int) or active < 1 or active > 12:
            raise CampaignSupervisorError("A2 campaign has no active session")
        slot = dict(self.package.slots[active - 1])
        child = self.package.session_output(slot)
        terminal_path = child / "COMPLETED.json"
        failed_path = child / "soak_run/FAILED.json"
        if failed_path.is_file() and not terminal_path.exists():
            return self._accept_failed_session_artifact(
                owner=owner,
                now_ms=now_ms,
                state=state,
                slot=slot,
                child=child,
            )
        completion_path = child / "soak_run/completion_hashes.json"
        evidence_path = child / "soak_run/audits/economic_session_evidence.json"
        try:
            terminal = _read_json(terminal_path)
            completion = _read_json(completion_path)
            failures = [
                relative for relative, expected in completion.items()
                if not (child / relative).is_file()
                or _sha256(child / relative) != expected
            ]
            if any((
                failures,
                _sha256(completion_path) != terminal.get("completion_hashes_sha256"),
                terminal.get("status") != "OKX_DEMO_ECONOMIC_SESSION_SUPPORT",
                terminal.get("package_id") != slot["package_id"],
                terminal.get("run_id") != slot["run_id"],
                terminal.get("final_position_btc") != "0",
                terminal.get("final_open_orders") != 0,
                terminal.get("two_flat_empty_snapshots") is not True,
                terminal.get("controller_engine_gateway_reconciled") is not True,
                terminal.get("live_endpoint_attempts") != 0,
                terminal.get("live_orders") != 0,
                not evidence_path.is_file(),
                "soak_run/audits/economic_session_evidence.json" not in completion,
            )):
                raise CampaignSupervisorError(
                    "A2 child terminal or completion evidence is invalid"
                )
            sealed_session = _read_json(evidence_path)
        except Exception as exc:
            self.fail_closed(
                owner=owner,
                now_ms=now_ms,
                reason=f"CHILD_TERMINAL_EVIDENCE_REJECTED:{type(exc).__name__}",
            )
            raise CampaignSupervisorError(
                "A2 child terminal evidence rejected fail closed"
            ) from exc
        return self.accept_session(
            owner=owner,
            now_ms=now_ms,
            sealed_session=sealed_session,
        )

    def _accept_failed_session_artifact(
        self,
        *,
        owner: str,
        now_ms: int,
        state: dict[str, object],
        slot: dict[str, object],
        child: Path,
    ) -> CampaignDecision:
        failed_path = child / "soak_run/FAILED.json"
        completion_path = child / "soak_run/completion_hashes.json"
        evidence_path = (
            child / "soak_run/audits/economic_session_failure_evidence.json"
        )
        snapshots_path = child / "soak_run/terminal/account_snapshots.json"
        gateway_audit_path = child / "soak_run/audits/gateway_audit.json"
        try:
            failed = _read_json(failed_path)
            completion = _read_json(completion_path)
            evidence = _read_json(evidence_path)
            snapshots = _read_json(snapshots_path)
            gateway_audit: dict[str, object] = {}
            evidence_seal = str(evidence.pop("evidence_sha256", ""))
            if canonical_sha256(evidence) != evidence_seal:
                raise CampaignSupervisorError(
                    "failed child evidence seal mismatch"
                )
            evidence["evidence_sha256"] = evidence_seal
            failures = [
                relative for relative, expected in completion.items()
                if not (child / relative).is_file()
                or _sha256(child / relative) != expected
            ]
            first = snapshots.get("first")
            second = snapshots.get("second")
            required_completion = {
                "soak_run/audits/economic_session_failure_evidence.json",
                "soak_run/terminal/account_snapshots.json",
            }
            pre_market_failure = failed.get("failure_stage") == "PRE_MARKET_BOOTSTRAP"
            if pre_market_failure:
                required_completion.add("soak_run/audits/gateway_audit.json")
                gateway_audit = _read_json(gateway_audit_path)
            bounded = (
                0 <= int(failed.get("normal_creates", -1)) <= 60
                and 0 <= int(failed.get("normal_cancels", -1)) <= 60
                and int(failed.get("normal_bid_fills", -1)) >= 0
                and int(failed.get("normal_ask_fills", -1)) >= 0
                and int(failed.get("normal_fifo_round_trips", -1)) >= 0
            )
            accounting_fields = (
                "realized_spread_pnl_usdt",
                "inventory_pnl_usdt",
                "normal_gross_pnl_usdt",
                "normal_fees_usdt",
                "normal_net_pnl_usdt",
                "special_gross_pnl_usdt",
                "special_fees_usdt",
                "special_net_pnl_usdt",
                "aggregate_gross_pnl_usdt",
                "aggregate_fees_usdt",
                "aggregate_net_pnl_usdt",
                "maximum_drawdown_usdt",
            )
            decimals = {
                key: Decimal(str(failed[key])) for key in accounting_fields
            }
            accounting_reconciles = all((
                decimals["realized_spread_pnl_usdt"]
                + decimals["inventory_pnl_usdt"]
                == decimals["normal_gross_pnl_usdt"],
                decimals["normal_gross_pnl_usdt"]
                - decimals["normal_fees_usdt"]
                == decimals["normal_net_pnl_usdt"],
                decimals["special_gross_pnl_usdt"]
                - decimals["special_fees_usdt"]
                == decimals["special_net_pnl_usdt"],
                decimals["normal_gross_pnl_usdt"]
                + decimals["special_gross_pnl_usdt"]
                == decimals["aggregate_gross_pnl_usdt"],
                decimals["normal_fees_usdt"]
                + decimals["special_fees_usdt"]
                == decimals["aggregate_fees_usdt"],
                decimals["normal_net_pnl_usdt"]
                + decimals["special_net_pnl_usdt"]
                == decimals["aggregate_net_pnl_usdt"],
                decimals["normal_fees_usdt"] >= 0,
                decimals["special_fees_usdt"] >= 0,
                Decimal("0") <= decimals["maximum_drawdown_usdt"]
                <= Decimal("37.50"),
            ))
            create_dispatches = int(failed.get("normal_create_dispatches", -1))
            create_acknowledgements = int(
                failed.get("normal_create_acknowledgements", -1)
            )
            create_rejections = int(failed.get("normal_create_rejections", -1))
            create_unresolved = int(failed.get("normal_create_unresolved", -1))
            create_counters_reconcile = all((
                create_dispatches == int(failed.get("normal_creates", -2)),
                min(
                    create_dispatches,
                    create_acknowledgements,
                    create_rejections,
                    create_unresolved,
                ) >= 0,
                create_dispatches
                == create_acknowledgements + create_rejections + create_unresolved,
                failed.get("mutation_retries") == 0,
            ))
            failure_evidence_matches = all(
                evidence.get(key) == failed.get(key)
                for key in (
                    *accounting_fields,
                    "normal_creates",
                    "normal_create_dispatches",
                    "normal_create_acknowledgements",
                    "normal_create_rejections",
                    "normal_create_unresolved",
                    "normal_bid_fills",
                    "normal_ask_fills",
                    "normal_fifo_round_trips",
                    "special_fill_count",
                )
            )
            pre_market_reconciles = (
                not pre_market_failure
                or all((
                    failed.get("market_bootstrap_completed") is False,
                    failed.get("terminal_reconciliation_mode")
                    == "ACCOUNT_ONLY_TWO_SNAPSHOT",
                    failed.get("normal_creates") == 0,
                    failed.get("normal_create_dispatches") == 0,
                    failed.get("normal_create_acknowledgements") == 0,
                    failed.get("normal_create_rejections") == 0,
                    failed.get("normal_create_unresolved") == 0,
                    failed.get("normal_cancels") == 0,
                    failed.get("normal_bid_fills") == 0,
                    failed.get("normal_ask_fills") == 0,
                    failed.get("normal_fifo_round_trips") == 0,
                    failed.get("special_fill_count") == 0,
                    gateway_audit.get("mutation_call_count") == 0,
                    gateway_audit.get("flatten_dispatches") == 0,
                    gateway_audit.get("live_endpoint_attempts") == 0,
                    gateway_audit.get("live_orders") == 0,
                ))
            )
            if any((
                failures,
                not required_completion.issubset(completion),
                _sha256(completion_path)
                != failed.get("completion_hashes_sha256"),
                failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
                failed.get("package_id") != slot["package_id"],
                failed.get("run_id") != slot["run_id"],
                failed.get("session_id") != slot["session_id"],
                failed.get("source_sha256")
                != self.package.spec["source_manifest_sha256"],
                failed.get("two_flat_empty_snapshots") is not True,
                failed.get("terminal_account_authoritative") is not True,
                failed.get("final_position_btc") != "0",
                failed.get("final_open_orders") != 0,
                failed.get("live_endpoint_attempts") != 0,
                failed.get("live_orders") != 0,
                failed.get("terminal_written_last") is not True,
                not bounded,
                not accounting_reconciles,
                not create_counters_reconcile,
                not failure_evidence_matches,
                not pre_market_reconciles,
                not isinstance(first, dict),
                not isinstance(second, dict),
                isinstance(first, dict) and first.get("position_btc") != "0",
                isinstance(second, dict) and second.get("position_btc") != "0",
                isinstance(first, dict) and first.get("open_orders") != 0,
                isinstance(second, dict) and second.get("open_orders") != 0,
                evidence.get("evidence_kind")
                != "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
                evidence.get("run_id") != slot["run_id"],
            )):
                raise CampaignSupervisorError(
                    "failed child terminal evidence is invalid"
                )
        except Exception as exc:
            self.fail_closed(
                owner=owner,
                now_ms=now_ms,
                reason=f"CHILD_FAILURE_EVIDENCE_REJECTED:{type(exc).__name__}",
            )
            raise CampaignSupervisorError(
                "A2 failed child evidence rejected fail closed"
            ) from exc

        active = int(slot["slot"])
        failed_slots = list(state.get("failed_slots", []))
        if active in failed_slots:
            raise CampaignSupervisorError("failed child slot reuse refused")
        failed_slots.append(active)
        aggregate = {
            "normal_creates": int(failed["normal_creates"]),
            "normal_create_dispatches": int(
                failed["normal_create_dispatches"]
            ),
            "normal_create_acknowledgements": int(
                failed["normal_create_acknowledgements"]
            ),
            "normal_create_rejections": int(
                failed["normal_create_rejections"]
            ),
            "normal_create_unresolved": int(
                failed["normal_create_unresolved"]
            ),
            "normal_cancels": int(failed["normal_cancels"]),
            "normal_bid_fills": int(failed["normal_bid_fills"]),
            "normal_ask_fills": int(failed["normal_ask_fills"]),
            "normal_fifo_round_trips": int(
                failed["normal_fifo_round_trips"]
            ),
            "realized_spread_pnl_usdt": str(
                failed["realized_spread_pnl_usdt"]
            ),
            "inventory_pnl_usdt": str(failed["inventory_pnl_usdt"]),
            "normal_gross_pnl_usdt": str(failed["normal_gross_pnl_usdt"]),
            "normal_fees_usdt": str(failed["normal_fees_usdt"]),
            "normal_net_pnl_usdt": str(failed["normal_net_pnl_usdt"]),
            "special_gross_pnl_usdt": str(failed["special_gross_pnl_usdt"]),
            "special_fees_usdt": str(failed["special_fees_usdt"]),
            "special_net_pnl_usdt": str(failed["special_net_pnl_usdt"]),
            "aggregate_gross_pnl_usdt": str(
                failed["aggregate_gross_pnl_usdt"]
            ),
            "aggregate_fees_usdt": str(failed["aggregate_fees_usdt"]),
            "aggregate_net_pnl_usdt": str(failed["aggregate_net_pnl_usdt"]),
            "maximum_drawdown_usdt": str(failed["maximum_drawdown_usdt"]),
            "markout_count": len(failed.get("markouts_usdt") or []),
            "causal_reentry_records": len(failed.get("causal_reentry") or []),
            "unclassified_quote_mode_ticks": int(
                failed.get("unclassified_quote_mode_ticks", 0)
            ),
            "special_flatten_sessions": (
                1 if int(failed.get("special_fill_count", 0)) > 0 else 0
            ),
            "unsafe_sessions": 1,
        }
        attempted = {
            "slot": active,
            "package_id": slot["package_id"],
            "run_id": slot["run_id"],
            "session_id": slot["session_id"],
            "failed_sha256": _sha256(failed_path),
            "failure_evidence_sha256": _sha256(evidence_path),
            "completion_hashes_sha256": _sha256(completion_path),
            "terminal_account_authoritative": True,
            "aggregate": aggregate,
        }
        attempted["attempt_sha256"] = canonical_sha256(attempted)
        _write_json(
            self.run / f"attempted_sessions/slot-{active:02d}.json",
            attempted,
        )
        decision = CampaignRegistry.load(self.registry_root).fail_closed(
            "CHILD_ECONOMIC_SESSION_FAILED"
        )
        state["failed_slots"] = failed_slots
        state["failed_session_aggregate"] = aggregate
        state["active_slot"] = None
        state["terminal_account_authoritative"] = True
        state["terminal_final_position_btc"] = "0"
        state["terminal_final_open_orders"] = 0
        state["terminal_decision"] = decision.value
        self._write_state(state)
        self._append_event("FAILED_SESSION_ACCOUNTED", {
            "slot": active,
            "attempt_sha256": attempted["attempt_sha256"],
            "normal_creates": aggregate["normal_creates"],
            "normal_fill_count": (
                aggregate["normal_bid_fills"] + aggregate["normal_ask_fills"]
            ),
            "terminal_account_authoritative": True,
        })
        self._finalize(decision)
        return decision

    def fail_closed(self, *, owner: str, now_ms: int, reason: str) -> CampaignDecision:
        self._require_owner(owner, now_ms)
        state = self.state()
        if state["terminal_decision"] is not None:
            raise CampaignSupervisorError("A2 campaign is already terminal")
        registry = CampaignRegistry.load(self.registry_root)
        decision = registry.fail_closed(reason)
        state["terminal_decision"] = decision.value
        self._write_state(state)
        self._append_event("CAMPAIGN_FAILED_CLOSED", {"reason": reason})
        self._finalize(decision)
        return decision

    def _finalize(self, decision: CampaignDecision) -> None:
        terminal_path = self.run / "A2_CAMPAIGN_COMPLETED.json"
        if terminal_path.exists():
            raise CampaignSupervisorError("A2 terminal marker reuse refused")
        registry_audit = verify_registry(self.registry_root)
        if registry_audit["terminal_decision"] != decision.value:
            raise CampaignSupervisorError("A2 terminal registry decision mismatch")
        state = self.state()
        completed_aggregate = dict(registry_audit["aggregate"])
        failed_aggregate = dict(state.get("failed_session_aggregate") or {})
        attempted_session_count = (
            len(state["completed_slots"]) + len(state.get("failed_slots", []))
        )
        attempted_aggregate = _attempted_aggregate(
            completed_aggregate,
            failed_aggregate,
            attempted_session_count=attempted_session_count,
        )
        _write_json(self.run / "audits/registry_audit.json", registry_audit)
        _write_json(self.run / "decision/campaign_decision.json", {
            "decision": decision.value,
            "campaign_id": self.package.spec["campaign_id"],
            "session_count": len(state["completed_slots"]),
            "attempted_session_count": attempted_session_count,
            "completed_aggregate": completed_aggregate,
            "attempted_aggregate": attempted_aggregate,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        excluded = {"completion_hashes.json", "A2_CAMPAIGN_COMPLETED.json"}
        manifest = dict(sorted(
            (path.relative_to(self.run).as_posix(), _sha256(path))
            for path in self.run.rglob("*")
            if (
                path.is_file()
                and not path.is_symlink()
                and path.name not in excluded
                and path.suffix != ".tmp"
            )
        ))
        _write_json(self.run / "completion_hashes.json", manifest)
        terminal_account_authoritative = bool(
            state.get("terminal_account_authoritative")
        )
        _write_json(terminal_path, {
            "status": decision.value,
            "campaign_id": self.package.spec["campaign_id"],
            "run_id": self.package.spec["run_id"],
            "sessions_completed": len(state["completed_slots"]),
            "sessions_failed": len(state.get("failed_slots", [])),
            "sessions_attempted": attempted_session_count,
            "completion_files_checked": len(manifest),
            "completion_hashes_sha256": _sha256(
                self.run / "completion_hashes.json"
            ),
            "terminal_account_status": (
                "VERIFIED_FLAT_EMPTY_FROM_SESSION_EVIDENCE"
                if terminal_account_authoritative
                else "UNRESOLVED_FAIL_CLOSED"
            ),
            "terminal_account_authoritative": terminal_account_authoritative,
            "final_position_btc": (
                state.get("terminal_final_position_btc")
                if terminal_account_authoritative else None
            ),
            "final_open_orders": (
                state.get("terminal_final_open_orders")
                if terminal_account_authoritative else None
            ),
            "attempted_aggregate": attempted_aggregate,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "terminal_written_last": True,
        })


def verify_campaign_run(package: A2CampaignPackage) -> dict[str, object]:
    supervisor = CampaignSupervisor.load(package)
    terminal_path = supervisor.run / "A2_CAMPAIGN_COMPLETED.json"
    state = supervisor.state()
    result = {
        "campaign_id": package.spec["campaign_id"],
        "run_id": package.spec["run_id"],
        "sessions_completed": len(state["completed_slots"]),
        "sessions_failed": len(state.get("failed_slots", [])),
        "sessions_attempted": (
            len(state["completed_slots"]) + len(state.get("failed_slots", []))
        ),
        "terminal_decision": state["terminal_decision"],
        "active_slot": state["active_slot"],
        "event_count": len(supervisor.events()),
        "registry": verify_registry(supervisor.registry_root),
        "terminal_written": terminal_path.is_file(),
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }
    if terminal_path.is_file():
        terminal = _read_json(terminal_path)
        completion_path = supervisor.run / "completion_hashes.json"
        manifest = _read_json(completion_path)
        failures = [
            relative for relative, expected in manifest.items()
            if not (supervisor.run / relative).is_file()
            or _sha256(supervisor.run / relative) != expected
        ]
        if failures or _sha256(completion_path) != terminal.get(
            "completion_hashes_sha256"
        ):
            raise CampaignSupervisorError("A2 terminal completion hash mismatch")
        result["completion_files_checked"] = len(manifest)
        result["completion_hashes_sha256"] = _sha256(completion_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("describe", "arm", "authorize-next", "accept-active", "verify"),
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--campaign-arm-token", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--now-ms", type=int, default=0)
    parser.add_argument("--lease-ttl-ms", type=int, default=21_900_000)
    args = parser.parse_args()
    try:
        package = load_campaign_package(args.root, args.package_id)
        if args.command == "describe":
            result: dict[str, object] = {
                "package_id": package.spec["package_id"],
                "campaign_id": package.spec["campaign_id"],
                "run_id": package.spec["run_id"],
                "campaign_session_id": package.spec["campaign_session_id"],
                "expected_campaign_arm_token": package.expected_campaign_arm_token,
                "session_count": len(package.slots),
                "campaign_started": (package.output / "campaign_run").exists(),
            }
        elif args.command == "arm":
            supervisor = CampaignSupervisor.arm(
                package,
                campaign_arm_token=args.campaign_arm_token,
                now_ms=args.now_ms,
            )
            supervisor.acquire_lease(
                owner=args.owner,
                now_ms=args.now_ms,
                ttl_ms=args.lease_ttl_ms,
            )
            result = {
                "campaign_id": package.spec["campaign_id"],
                "armed": True,
                "lease_owner_sha256": hashlib.sha256(
                    args.owner.encode("utf-8")
                ).hexdigest(),
            }
        else:
            supervisor = CampaignSupervisor.load(package)
            if args.command == "authorize-next":
                slot = supervisor.authorize_next_session(
                    owner=args.owner, now_ms=args.now_ms
                )
                result = {
                    **slot,
                    "expected_session_arm_token": f"OKX_DEMO:{slot['session_id']}",
                }
            elif args.command == "accept-active":
                decision = supervisor.accept_active_session_artifact(
                    owner=args.owner, now_ms=args.now_ms
                )
                result = {"decision": decision.value}
            else:
                result = verify_campaign_run(package)
    except Exception as exc:
        print(f"A2_CAMPAIGN_SUPERVISOR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
