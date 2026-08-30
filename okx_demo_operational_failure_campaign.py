"""Bounded, source-bound operational failure campaign accounting.

The campaign is an offline evidence layer during A0.  It defines ten finite
scenario groups, validates the mutation/retry/terminal boundaries of each run,
and persists them in an append-only hash chain.  It contains no transport or
credential loader and cannot execute A3 by itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from okx_demo_multi_session_campaign import CampaignError
from okx_fill_restart_validation import canonical_sha256


class FailureMatrixStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"


SCENARIO_GROUPS: Mapping[str, tuple[str, ...]] = {
    "dispatch_ambiguity": (
        "timeout_before_create_dispatch",
        "timeout_after_create_dispatch",
        "duplicate_create_ack",
        "no_mutation_retry_after_ambiguous_dispatch",
    ),
    "read_retry": (
        "read_timeout",
        "bounded_rate_limit_backoff",
        "read_retry_exhaustion",
    ),
    "fill_delivery": (
        "duplicate_fill",
        "delayed_fill",
        "reordered_fill",
        "paginated_history_omission",
        "recent_tail_recovery",
        "conflicting_duplicate_fill",
    ),
    "restart_boundaries": (
        "restart_before_dispatch",
        "restart_after_intent",
        "restart_after_ack",
        "restart_after_fill",
        "restart_after_cancel",
        "restart_at_r1",
        "restart_at_r2",
    ),
    "lease_recovery": (
        "single_instance_lease_collision",
        "verified_stale_lease_recovery",
    ),
    "durable_evidence": (
        "state_store_failure",
        "hash_chain_truncation",
        "hash_chain_corruption",
        "source_binding_drift",
        "terminal_evidence_write_failure",
    ),
    "market_books": (
        "stale_book",
        "exact_age_boundary",
        "future_within_skew",
        "future_beyond_skew",
        "crossed_book",
        "empty_book",
        "invalid_book",
    ),
    "ownership_account": (
        "foreign_order",
        "unexplained_order_disappearance",
        "account_mismatch",
        "counter_regression",
        "pending_intent",
        "controller_engine_gateway_account_mismatch",
    ),
    "shutdown_flatten": (
        "clean_shutdown",
        "owned_only_cancel",
        "multi_partial_flatten",
        "no_second_flatten",
        "two_terminal_flat_snapshots",
    ),
    "terminal_reporting": (
        "non_flat_terminal_account",
        "terminal_open_orders",
        "terminal_account_only_failure_report",
        "terminal_completion_success",
    ),
}

EXPECTED_DECISIONS: Mapping[str, str] = {
    "timeout_before_create_dispatch": "CONTINUE",
    "timeout_after_create_dispatch": "PLACEMENT_BLOCKED",
    "duplicate_create_ack": "CONTINUE",
    "no_mutation_retry_after_ambiguous_dispatch": "PLACEMENT_BLOCKED",
    "read_timeout": "RETRY_READ_ONLY",
    "bounded_rate_limit_backoff": "RETRY_READ_ONLY",
    "read_retry_exhaustion": "FAIL_CLOSED",
    "duplicate_fill": "CONTINUE",
    "delayed_fill": "CONTINUE",
    "reordered_fill": "CONTINUE",
    "paginated_history_omission": "CONTINUE",
    "recent_tail_recovery": "CONTINUE",
    "conflicting_duplicate_fill": "FAIL_CLOSED",
    "restart_before_dispatch": "CONTINUE",
    "restart_after_intent": "PLACEMENT_BLOCKED",
    "restart_after_ack": "CONTINUE",
    "restart_after_fill": "CONTINUE",
    "restart_after_cancel": "CONTINUE",
    "restart_at_r1": "CONTINUE",
    "restart_at_r2": "CONTINUE",
    "single_instance_lease_collision": "FAIL_CLOSED",
    "verified_stale_lease_recovery": "CONTINUE",
    "state_store_failure": "FAIL_CLOSED",
    "hash_chain_truncation": "FAIL_CLOSED",
    "hash_chain_corruption": "FAIL_CLOSED",
    "source_binding_drift": "FAIL_CLOSED",
    "terminal_evidence_write_failure": "FAIL_CLOSED",
    "stale_book": "PLACEMENT_BLOCKED",
    "exact_age_boundary": "CONTINUE",
    "future_within_skew": "CONTINUE",
    "future_beyond_skew": "PLACEMENT_BLOCKED",
    "crossed_book": "FAIL_CLOSED",
    "empty_book": "FAIL_CLOSED",
    "invalid_book": "FAIL_CLOSED",
    "foreign_order": "FAIL_CLOSED",
    "unexplained_order_disappearance": "FAIL_CLOSED",
    "account_mismatch": "FAIL_CLOSED",
    "counter_regression": "FAIL_CLOSED",
    "pending_intent": "PLACEMENT_BLOCKED",
    "controller_engine_gateway_account_mismatch": "FAIL_CLOSED",
    "clean_shutdown": "TERMINAL_COMPLETE",
    "owned_only_cancel": "CONTINUE",
    "multi_partial_flatten": "CONTINUE",
    "no_second_flatten": "CONTINUE",
    "two_terminal_flat_snapshots": "TERMINAL_COMPLETE",
    "non_flat_terminal_account": "FAIL_CLOSED",
    "terminal_open_orders": "FAIL_CLOSED",
    "terminal_account_only_failure_report": "FAIL_CLOSED",
    "terminal_completion_success": "TERMINAL_COMPLETE",
}


def _require_sha256(value: object, name: str) -> str:
    result = str(value)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise CampaignError(f"{name} is not a lowercase SHA-256")
    return result


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CampaignError(f"failure-campaign artifact reuse refused: {path}") from exc


@dataclass(frozen=True)
class FailureCampaignLimits:
    maximum_runs: int = 10
    maximum_run_wall_ms: int = 10 * 60 * 1000
    maximum_run_normal_creates: int = 4
    maximum_campaign_normal_creates: int = 40
    maximum_run_unresolved_flatten: int = 1
    maximum_mutation_retries: int = 0

    def validate(self) -> None:
        if self != FailureCampaignLimits():
            raise CampaignError("failure-campaign budget drift")

    def to_dict(self) -> dict[str, int]:
        return {
            "maximum_runs": self.maximum_runs,
            "maximum_run_wall_ms": self.maximum_run_wall_ms,
            "maximum_run_normal_creates": self.maximum_run_normal_creates,
            "maximum_campaign_normal_creates": self.maximum_campaign_normal_creates,
            "maximum_run_unresolved_flatten": self.maximum_run_unresolved_flatten,
            "maximum_mutation_retries": self.maximum_mutation_retries,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FailureCampaignLimits":
        result = cls(**{name: int(value[name]) for name in cls.__dataclass_fields__})
        result.validate()
        return result


@dataclass(frozen=True)
class FailureCaseEvidence:
    case: str
    injection_point: str
    expected_decision: str
    observed_decision: str
    expected_durable_state: str
    observed_durable_state: str
    mutation_delta: int
    maximum_mutation_delta: int
    read_retries: int
    mutation_retries: int
    passed: bool

    def validate(self, group: str) -> None:
        if self.case not in SCENARIO_GROUPS[group]:
            raise CampaignError("failure case is not bound to its scenario group")
        expected = EXPECTED_DECISIONS[self.case]
        if self.expected_decision != expected or self.observed_decision != expected:
            raise CampaignError("failure case decision mismatch")
        if not self.injection_point or not self.expected_durable_state:
            raise CampaignError("failure case injection contract is incomplete")
        if self.observed_durable_state != self.expected_durable_state:
            raise CampaignError("failure case durable state mismatch")
        if (
            self.mutation_delta < 0
            or self.maximum_mutation_delta < 0
            or self.mutation_delta > self.maximum_mutation_delta
        ):
            raise CampaignError("failure case mutation delta exceeded")
        if self.read_retries < 0 or self.read_retries > 3:
            raise CampaignError("failure case read retry budget exceeded")
        if self.mutation_retries != 0:
            raise CampaignError("failure case retried a mutation")
        if not self.passed:
            raise CampaignError("failure case did not pass")

    def to_dict(self) -> dict[str, object]:
        return {
            "case": self.case,
            "injection_point": self.injection_point,
            "expected_decision": self.expected_decision,
            "observed_decision": self.observed_decision,
            "expected_durable_state": self.expected_durable_state,
            "observed_durable_state": self.observed_durable_state,
            "mutation_delta": self.mutation_delta,
            "maximum_mutation_delta": self.maximum_mutation_delta,
            "read_retries": self.read_retries,
            "mutation_retries": self.mutation_retries,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object], group: str) -> "FailureCaseEvidence":
        result = cls(
            case=str(value["case"]),
            injection_point=str(value["injection_point"]),
            expected_decision=str(value["expected_decision"]),
            observed_decision=str(value["observed_decision"]),
            expected_durable_state=str(value["expected_durable_state"]),
            observed_durable_state=str(value["observed_durable_state"]),
            mutation_delta=int(value["mutation_delta"]),
            maximum_mutation_delta=int(value["maximum_mutation_delta"]),
            read_retries=int(value["read_retries"]),
            mutation_retries=int(value["mutation_retries"]),
            passed=bool(value["passed"]),
        )
        result.validate(group)
        return result


@dataclass(frozen=True)
class FailureRunEvidence:
    run_id: str
    group: str
    source_sha256: str
    evidence_sha256: str
    started_at_ms: int
    ended_at_ms: int
    normal_creates: int
    cancels: int
    flatten_dispatches: int
    mutation_retries: int
    cases: tuple[FailureCaseEvidence, ...]
    terminal_mode: str
    final_position_btc: str
    final_open_orders: int
    pending_intent: bool
    ambiguous_intent: bool
    unresolved_reason: str
    account_configuration_mutations: int
    credential_accesses: int
    live_endpoint_attempts: int
    live_orders: int
    optuna_imported: bool
    validation_opened: bool
    holdout_opened: bool
    git_write_operation: bool

    @property
    def duration_ms(self) -> int:
        return self.ended_at_ms - self.started_at_ms

    def validate(self, limits: FailureCampaignLimits) -> None:
        limits.validate()
        if not self.run_id.startswith("failure:") or self.group not in SCENARIO_GROUPS:
            raise CampaignError("failure run identity or group is invalid")
        _require_sha256(self.source_sha256, "source_sha256")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        if self.started_at_ms <= 0 or not 0 < self.duration_ms <= limits.maximum_run_wall_ms:
            raise CampaignError("failure run wall budget exceeded")
        for name, value, maximum in (
            ("normal_creates", self.normal_creates, limits.maximum_run_normal_creates),
            ("cancels", self.cancels, limits.maximum_run_normal_creates),
            (
                "flatten_dispatches",
                self.flatten_dispatches,
                limits.maximum_run_unresolved_flatten,
            ),
            ("mutation_retries", self.mutation_retries, 0),
            ("account_configuration_mutations", self.account_configuration_mutations, 0),
            ("credential_accesses", self.credential_accesses, 0),
            ("live_endpoint_attempts", self.live_endpoint_attempts, 0),
            ("live_orders", self.live_orders, 0),
        ):
            if value < 0 or value > maximum:
                raise CampaignError(f"{name} exceeds failure-run budget")
        forbidden = (
            self.optuna_imported,
            self.validation_opened,
            self.holdout_opened,
            self.git_write_operation,
        )
        if any(forbidden):
            raise CampaignError("failure run crossed a prohibited boundary")
        expected_cases = SCENARIO_GROUPS[self.group]
        observed_cases = tuple(item.case for item in self.cases)
        if observed_cases != expected_cases:
            raise CampaignError("failure run case coverage or ordering mismatch")
        for item in self.cases:
            item.validate(self.group)
        if self.terminal_mode == "FLAT_EMPTY":
            if any((
                self.final_position_btc != "0",
                self.final_open_orders != 0,
                self.pending_intent,
                self.ambiguous_intent,
                bool(self.unresolved_reason),
            )):
                raise CampaignError("flat failure run terminal state mismatch")
        elif self.terminal_mode == "UNRESOLVED_FAIL_CLOSED":
            if not self.unresolved_reason:
                raise CampaignError("unresolved failure run lacks a reason")
            if self.mutation_retries != 0:
                raise CampaignError("unresolved failure run retried mutation")
        else:
            raise CampaignError("failure run terminal mode is invalid")

    def payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "group": self.group,
            "source_sha256": self.source_sha256,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "normal_creates": self.normal_creates,
            "cancels": self.cancels,
            "flatten_dispatches": self.flatten_dispatches,
            "mutation_retries": self.mutation_retries,
            "cases": [item.to_dict() for item in self.cases],
            "terminal_mode": self.terminal_mode,
            "final_position_btc": self.final_position_btc,
            "final_open_orders": self.final_open_orders,
            "pending_intent": self.pending_intent,
            "ambiguous_intent": self.ambiguous_intent,
            "unresolved_reason": self.unresolved_reason,
            "account_configuration_mutations": self.account_configuration_mutations,
            "credential_accesses": self.credential_accesses,
            "live_endpoint_attempts": self.live_endpoint_attempts,
            "live_orders": self.live_orders,
            "optuna_imported": self.optuna_imported,
            "validation_opened": self.validation_opened,
            "holdout_opened": self.holdout_opened,
            "git_write_operation": self.git_write_operation,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.payload(), "evidence_sha256": self.evidence_sha256}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        limits: FailureCampaignLimits | None = None,
    ) -> "FailureRunEvidence":
        raw = dict(value)
        seal = _require_sha256(raw.pop("evidence_sha256", ""), "evidence_sha256")
        if canonical_sha256(raw) != seal:
            raise CampaignError("failure run evidence seal mismatch")
        cases = raw["cases"]
        if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
            raise CampaignError("failure run cases are invalid")
        group = str(raw["group"])
        if group not in SCENARIO_GROUPS:
            raise CampaignError("failure run group is invalid")
        result = cls(
            run_id=str(raw["run_id"]),
            group=group,
            source_sha256=str(raw["source_sha256"]),
            evidence_sha256=seal,
            started_at_ms=int(raw["started_at_ms"]),
            ended_at_ms=int(raw["ended_at_ms"]),
            normal_creates=int(raw["normal_creates"]),
            cancels=int(raw["cancels"]),
            flatten_dispatches=int(raw["flatten_dispatches"]),
            mutation_retries=int(raw["mutation_retries"]),
            cases=tuple(FailureCaseEvidence.from_dict(item, group) for item in cases),
            terminal_mode=str(raw["terminal_mode"]),
            final_position_btc=str(raw["final_position_btc"]),
            final_open_orders=int(raw["final_open_orders"]),
            pending_intent=bool(raw["pending_intent"]),
            ambiguous_intent=bool(raw["ambiguous_intent"]),
            unresolved_reason=str(raw["unresolved_reason"]),
            account_configuration_mutations=int(raw["account_configuration_mutations"]),
            credential_accesses=int(raw["credential_accesses"]),
            live_endpoint_attempts=int(raw["live_endpoint_attempts"]),
            live_orders=int(raw["live_orders"]),
            optuna_imported=bool(raw["optuna_imported"]),
            validation_opened=bool(raw["validation_opened"]),
            holdout_opened=bool(raw["holdout_opened"]),
            git_write_operation=bool(raw["git_write_operation"]),
        )
        result.validate(limits or FailureCampaignLimits())
        return result


def seal_failure_run(value: Mapping[str, object]) -> dict[str, object]:
    if "evidence_sha256" in value:
        raise CampaignError("failure run evidence is already sealed")
    payload = dict(value)
    return {**payload, "evidence_sha256": canonical_sha256(payload)}


@dataclass(frozen=True)
class FailureCampaignManifest:
    campaign_id: str
    source_sha256: str
    created_at_ms: int
    economic_campaign_id: str
    limits: FailureCampaignLimits = FailureCampaignLimits()
    schema_version: int = 1

    def validate(self) -> None:
        if not self.campaign_id.startswith("failure-campaign-"):
            raise CampaignError("failure campaign identity is invalid")
        if not self.economic_campaign_id.startswith("economic-campaign-"):
            raise CampaignError("economic campaign binding is invalid")
        if self.created_at_ms <= 0 or self.schema_version != 1:
            raise CampaignError("failure campaign metadata is invalid")
        _require_sha256(self.source_sha256, "source_sha256")
        self.limits.validate()

    def payload(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "source_sha256": self.source_sha256,
            "created_at_ms": self.created_at_ms,
            "economic_campaign_id": self.economic_campaign_id,
            "limits": self.limits.to_dict(),
            "scenario_groups": {
                name: list(cases) for name, cases in SCENARIO_GROUPS.items()
            },
            "schema_version": self.schema_version,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self.payload()
        return {**payload, "manifest_sha256": canonical_sha256(payload)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FailureCampaignManifest":
        raw = dict(value)
        seal = _require_sha256(raw.pop("manifest_sha256", ""), "manifest_sha256")
        if canonical_sha256(raw) != seal:
            raise CampaignError("failure campaign manifest seal mismatch")
        if raw.get("scenario_groups") != {
            name: list(cases) for name, cases in SCENARIO_GROUPS.items()
        }:
            raise CampaignError("failure scenario matrix drift")
        limits = raw["limits"]
        if not isinstance(limits, Mapping):
            raise CampaignError("failure campaign limits are invalid")
        result = cls(
            campaign_id=str(raw["campaign_id"]),
            source_sha256=str(raw["source_sha256"]),
            created_at_ms=int(raw["created_at_ms"]),
            economic_campaign_id=str(raw["economic_campaign_id"]),
            limits=FailureCampaignLimits.from_dict(limits),
            schema_version=int(raw["schema_version"]),
        )
        result.validate()
        return result


class FailureCampaignRegistry:
    def __init__(self, root: Path, manifest: FailureCampaignManifest):
        self.root = root
        self.manifest = manifest
        self.manifest_path = root / "failure_campaign_manifest.json"
        self.registry_path = root / "failure_campaign_registry.jsonl"

    @classmethod
    def initialize(
        cls, root: Path, manifest: FailureCampaignManifest
    ) -> "FailureCampaignRegistry":
        manifest.validate()
        if root.exists():
            raise CampaignError("failure campaign artifact reuse refused")
        root.mkdir(parents=True, exist_ok=False)
        result = cls(root, manifest)
        _write_new(result.manifest_path, manifest.to_dict())
        result._append("FAILURE_CAMPAIGN_CREATED", {
            "manifest_sha256": manifest.to_dict()["manifest_sha256"],
            "network_authorized": False,
            "orders_authorized": False,
            "production_authorized": False,
        })
        return result

    @classmethod
    def load(cls, root: Path) -> "FailureCampaignRegistry":
        try:
            value = json.loads((root / "failure_campaign_manifest.json").read_text(
                encoding="utf-8"
            ))
        except Exception as exc:
            raise CampaignError("failure campaign manifest cannot be loaded") from exc
        result = cls(root, FailureCampaignManifest.from_dict(value))
        result.records()
        return result

    def records(self) -> tuple[dict[str, object], ...]:
        try:
            lines = self.registry_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CampaignError("failure campaign registry cannot be read") from exc
        records: list[dict[str, object]] = []
        previous = "GENESIS"
        for sequence, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CampaignError("failure campaign registry is corrupt") from exc
            seal = _require_sha256(record.pop("record_hash", ""), "record_hash")
            if record.get("sequence") != sequence:
                raise CampaignError("failure campaign sequence regression")
            if record.get("campaign_id") != self.manifest.campaign_id:
                raise CampaignError("failure campaign registry identity mismatch")
            if record.get("previous_hash") != previous:
                raise CampaignError("failure campaign hash-chain mismatch")
            if canonical_sha256(record) != seal:
                raise CampaignError("failure campaign record hash mismatch")
            record["record_hash"] = seal
            records.append(record)
            previous = seal
        if not records or records[0].get("event") != "FAILURE_CAMPAIGN_CREATED":
            raise CampaignError("failure campaign genesis is invalid")
        return tuple(records)

    def _append(self, event: str, payload: Mapping[str, object]) -> None:
        records = self.records() if self.registry_path.exists() else ()
        if records and records[-1]["event"] == "FAILURE_MATRIX_TERMINAL":
            raise CampaignError("failure campaign is terminal")
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
            raise CampaignError("failure campaign registry append failed") from exc

    def runs(self) -> tuple[FailureRunEvidence, ...]:
        return tuple(
            FailureRunEvidence.from_dict(record["payload"]["run"], self.manifest.limits)
            for record in self.records()
            if record["event"] == "FAILURE_RUN_ACCEPTED"
        )

    def aggregate(self, runs: Sequence[FailureRunEvidence] | None = None) -> dict[str, object]:
        rows = tuple(self.runs() if runs is None else runs)
        cases = [case for run in rows for case in run.cases]
        return {
            "run_count": len(rows),
            "normal_creates": sum(run.normal_creates for run in rows),
            "cancels": sum(run.cancels for run in rows),
            "flatten_dispatches": sum(run.flatten_dispatches for run in rows),
            "mutation_retries": sum(run.mutation_retries for run in rows),
            "case_count": len(cases),
            "passed_cases": sum(case.passed for case in cases),
            "covered_groups": sorted(run.group for run in rows),
            "covered_cases": sorted(case.case for case in cases),
            "credential_accesses": sum(run.credential_accesses for run in rows),
            "live_endpoint_attempts": sum(run.live_endpoint_attempts for run in rows),
            "live_orders": sum(run.live_orders for run in rows),
            "account_configuration_mutations": sum(
                run.account_configuration_mutations for run in rows
            ),
        }

    def evaluate(
        self, runs: Sequence[FailureRunEvidence] | None = None
    ) -> tuple[FailureMatrixStatus, tuple[str, ...]]:
        rows = tuple(self.runs() if runs is None else runs)
        aggregate = self.aggregate(rows)
        limits = self.manifest.limits
        failed: list[str] = []
        if int(aggregate["normal_creates"]) > limits.maximum_campaign_normal_creates:
            failed.append("CAMPAIGN_CREATE_BUDGET")
        for name in (
            "mutation_retries",
            "credential_accesses",
            "live_endpoint_attempts",
            "live_orders",
            "account_configuration_mutations",
        ):
            if int(aggregate[name]) != 0:
                failed.append(name.upper())
        if len(rows) > limits.maximum_runs:
            failed.append("CAMPAIGN_RUN_BUDGET")
        if failed:
            return FailureMatrixStatus.FAILED, tuple(failed)
        if len(rows) < limits.maximum_runs:
            return FailureMatrixStatus.IN_PROGRESS, ()
        expected_groups = sorted(SCENARIO_GROUPS)
        expected_cases = sorted(case for cases in SCENARIO_GROUPS.values() for case in cases)
        if aggregate["covered_groups"] != expected_groups:
            failed.append("SCENARIO_GROUP_COVERAGE")
        if aggregate["covered_cases"] != expected_cases:
            failed.append("SCENARIO_CASE_COVERAGE")
        if int(aggregate["passed_cases"]) != len(expected_cases):
            failed.append("SCENARIO_CASE_FAILURE")
        if failed:
            return FailureMatrixStatus.FAILED, tuple(failed)
        return FailureMatrixStatus.PASSED, ()

    def register_run(self, run: FailureRunEvidence) -> FailureMatrixStatus:
        records = self.records()
        if records[-1]["event"] == "FAILURE_MATRIX_TERMINAL":
            raise CampaignError("failure campaign is terminal")
        run.validate(self.manifest.limits)
        if run.source_sha256 != self.manifest.source_sha256:
            raise CampaignError("failure run source binding mismatch")
        previous = self.runs()
        if any(item.run_id == run.run_id or item.group == run.group for item in previous):
            raise CampaignError("failure run identity or scenario group reuse refused")
        if len(previous) >= self.manifest.limits.maximum_runs:
            raise CampaignError("failure run budget exhausted")
        candidate = (*previous, run)
        aggregate = self.aggregate(candidate)
        if int(aggregate["normal_creates"]) > self.manifest.limits.maximum_campaign_normal_creates:
            raise CampaignError("failure campaign create budget would be exceeded")
        self._append("FAILURE_RUN_ACCEPTED", {
            "run": run.to_dict(),
            "aggregate_after_run": aggregate,
        })
        status, reasons = self.evaluate(candidate)
        if status is not FailureMatrixStatus.IN_PROGRESS:
            self._append("FAILURE_MATRIX_TERMINAL", {
                "matrix_status": status.value,
                "reasons": list(reasons),
                "aggregate": aggregate,
                "production_authorized": False,
                "live_mode_available": False,
            })
        return status


def verify_failure_registry(root: Path) -> dict[str, object]:
    registry = FailureCampaignRegistry.load(root)
    records = registry.records()
    runs = registry.runs()
    status, reasons = registry.evaluate(runs)
    terminal = [record for record in records if record["event"] == "FAILURE_MATRIX_TERMINAL"]
    if terminal:
        if len(terminal) != 1 or terminal[0]["payload"]["matrix_status"] != status.value:
            raise CampaignError("failure matrix terminal status mismatch")
    return {
        "campaign_id": registry.manifest.campaign_id,
        "records": len(records),
        "runs": len(runs),
        "tail_sha256": records[-1]["record_hash"],
        "matrix_status": status.value,
        "reasons": list(reasons),
        "aggregate": registry.aggregate(runs),
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }
