"""Fail-closed supervisor for the terminal-causal/CLI successor campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import okx_demo_multi_session_supervisor as legacy
import okx_demo_sample_efficiency_campaign_supervisor as previous
from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_multi_session_prepare import (
    CAMPAIGN_ARTIFACT_ROOT,
    READY_STATUS,
    SESSION_ARTIFACT_ROOT,
    _risk_budget,
)
from okx_fill_restart_offline import _sha256
from okx_fill_restart_validation import canonical_sha256


PROTOCOL_ID = "okx-demo-terminal-causal-cli-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_terminal_causal_cli_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_terminal_causal_cli_campaign_supervisor.py"
CHILD_EXECUTION_SOURCE = "okx_demo_terminal_recovery_session_start.py"
EVIDENCE_KIND = "terminal_causal_cli_r0_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260820T165217Z"
FAILED_RUN_ID = "economic-campaign-run-20260820T165217Z"
FAILED_SESSION_ID = "soak-package-20260820T165217Z-s01-8415f255c1"


def _read(path: Path) -> dict[str, object]:
    return legacy._read_json(path)


def _decision_payload(decision: legacy.CampaignDecision) -> dict[str, object]:
    """Serialize the enum by value; CampaignDecision has no to_dict method."""
    return {"decision": decision.value}


def load_campaign_package(
    root: Path,
    package_id: str,
    *,
    protocol_id: str = PROTOCOL_ID,
    preparation_source: str = PREPARATION_SOURCE,
    execution_source: str = EXECUTION_SOURCE,
    evidence_kind: str = EVIDENCE_KIND,
    failed_package_id: str = FAILED_PACKAGE_ID,
    failed_run_id: str = FAILED_RUN_ID,
    failed_session_id: str = FAILED_SESSION_ID,
    failed_audit_name: str = "failed_terminal_causal_package_audit.json",
    require_fifo_attribution: bool = False,
    require_workoff_timestamp_repair: bool = False,
    require_terminal_special_closure_repair: bool = False,
    require_markout_special_closure_repair: bool = False,
    require_owned_cancel_reconciliation: bool = False,
    require_post_wall_interruption: bool = False,
    unstarted_slots_field: str | None = "slot_2_through_12_started",
) -> legacy.A2CampaignPackage:
    root = root.resolve()
    if not package_id.startswith("economic-package-"):
        raise legacy.CampaignSupervisorError("terminal-causal package identity invalid")
    output = root / CAMPAIGN_ARTIFACT_ROOT / package_id
    terminal_path = output / "A2_PACKAGE_COMPLETED.json"
    spec_path = output / "specification/campaign_package_spec.json"
    source_path = output / "specification/source_hashes.json"
    child_path = output / "specification/session_package_audits.json"
    completion_path = output / "completion_hashes.json"
    repair_path = output / "predecessor/r0_evidence_audit.json"
    r1_path = output / "predecessor/preflight_evidence_audit.json"
    failed_path = output / "predecessor" / failed_audit_name
    required = (
        terminal_path, spec_path, source_path, child_path, completion_path,
        repair_path, r1_path, failed_path,
    )
    if not all(path.is_file() for path in required):
        raise legacy.CampaignSupervisorError("terminal-causal package incomplete")

    terminal, spec = _read(terminal_path), _read(spec_path)
    repair, r1, failed = _read(repair_path), _read(r1_path), _read(failed_path)
    if any((
        terminal.get("status") != READY_STATUS,
        terminal.get("package_id") != package_id,
        terminal.get("campaign_authorized") is not False,
        terminal.get("campaign_executed") is not False,
        terminal.get("network_attempts") != 0,
        terminal.get("orders_submitted") != 0,
        spec.get("package_id") != package_id,
        spec.get("protocol_id") != protocol_id,
        spec.get("preparation_source") != preparation_source,
        spec.get("execution_source") != execution_source,
        spec.get("child_execution_source") != CHILD_EXECUTION_SOURCE,
        spec.get("campaign_authorized") is not False,
        spec.get("campaign_executed") is not False,
        spec.get("execution_marker_created") is not False,
        spec.get("session_count") != CampaignLimits().maximum_sessions,
        spec.get("campaign_limits") != CampaignLimits().to_dict(),
        spec.get("session_risk_budget") != _risk_budget(),
        spec.get("two_snapshot_terminal_reconciliation_required") is not True,
        spec.get("terminal_causal_fill_attribution_repair_required") is not True,
        spec.get("campaign_decision_cli_serialization_repair_required") is not True,
        spec.get("mutation_retry_attempts") != 0,
        repair.get("evidence_kind") != evidence_kind,
        repair.get("passed") is not True,
        repair.get("failed_campaign_decision") != "NOT_READY",
        r1.get("passed") is not True,
        r1.get("zero_mutation_verified") is not True,
        r1.get("r0_evidence_id") != spec.get("repair_evidence_id"),
        failed.get("package_id") != failed_package_id,
        failed.get("campaign_run_id") != failed_run_id,
        failed.get("session_package_id") != failed_session_id,
        failed.get("immutable_failed_post_start") is not True,
        failed.get("campaign_decision") != "NOT_READY",
        failed.get("resume_authorized") is not False,
        failed.get("rerun_authorized") is not False,
        (
            unstarted_slots_field is not None
            and failed.get(unstarted_slots_field) is not False
        ),
    )):
        raise legacy.CampaignSupervisorError("terminal-causal package boundary invalid")
    if require_fifo_attribution and any((
        spec.get("fifo_attribution_identity_audit_required") is not True,
        spec.get("multi_partial_fifo_fill_support_required") is not True,
    )):
        raise legacy.CampaignSupervisorError("FIFO attribution package binding invalid")
    if require_workoff_timestamp_repair and any((
        spec.get("workoff_completion_timestamp_repair_required") is not True,
        spec.get("partial_workoff_restart_reconciliation_required") is not True,
    )):
        raise legacy.CampaignSupervisorError(
            "work-off timestamp package binding invalid"
        )
    if require_terminal_special_closure_repair and any((
        spec.get("terminal_special_fill_closure_required") is not True,
        spec.get("controller_engine_account_multi_partial_reconciliation_required")
        is not True,
        spec.get("bounded_timeboxed_workoff_required") is not True,
    )):
        raise legacy.CampaignSupervisorError(
            "terminal special-closure package binding invalid"
        )
    if require_markout_special_closure_repair and any((
        spec.get("normal_markout_special_closure_attribution_required") is not True,
        spec.get("terminal_special_closed_markout_serialization_required") is not True,
        spec.get("normal_markout_attribution_reconciliation_required") is not True,
    )):
        raise legacy.CampaignSupervisorError(
            "markout/special-closure package binding invalid"
        )
    if require_owned_cancel_reconciliation and any((
        spec.get("authoritative_owned_cancel_reconciliation_required") is not True,
        spec.get("cancel_fill_cursor_union_required") is not True,
        spec.get("cancel_reconciliation_read_attempts") != 3,
        spec.get("cancel_reconciliation_interval_seconds") != 2,
        spec.get("cancel_mutation_retry_attempts") != 0,
    )):
        raise legacy.CampaignSupervisorError(
            "owned-cancel reconciliation package binding invalid"
        )
    if require_post_wall_interruption and any((
        spec.get("post_wall_interruption_audit_required") is not True,
        spec.get("completed_active_session_ingestion_required") is not True,
        spec.get("stale_lease_recovery_required") is not True,
        spec.get("campaign_wall_fail_closed_required") is not True,
        spec.get("resume_after_campaign_wall_authorized") is not False,
        spec.get("campaign_limits", {}).get("maximum_campaign_wall_ms")
        != 21_600_000,
    )):
        raise legacy.CampaignSupervisorError(
            "post-wall interruption package binding invalid"
        )

    canonical = dict(spec)
    expected_spec_hash = str(canonical.pop("specification_sha256", ""))
    if canonical_sha256(canonical) != expected_spec_hash:
        raise legacy.CampaignSupervisorError("terminal-causal specification hash mismatch")
    completion = _read(completion_path)
    failures = [
        relative for relative, expected in completion.items()
        if not (output / relative).is_file() or _sha256(output / relative) != expected
    ]
    if failures or _sha256(completion_path) != terminal.get("completion_hashes_sha256"):
        raise legacy.CampaignSupervisorError("terminal-causal completion hash mismatch")
    sources = _read(source_path)
    source_failures = [
        relative for relative, expected in sources.items()
        if not (root / relative).is_file() or _sha256(root / relative) != expected
    ]
    if source_failures or canonical_sha256(sources) != spec.get("source_manifest_sha256"):
        raise legacy.CampaignSupervisorError("terminal-causal source binding mismatch")

    slots = tuple(dict(item) for item in list(spec.get("session_slots") or []))
    if (
        len(slots) != 12
        or [item.get("slot") for item in slots] != list(range(1, 13))
        or len({item.get("package_id") for item in slots}) != 12
        or len({item.get("run_id") for item in slots}) != 12
        or len({item.get("session_id") for item in slots}) != 12
    ):
        raise legacy.CampaignSupervisorError("terminal-causal session identities invalid")
    child = _read(child_path)
    audits = list(child.get("packages") or [])
    if child.get("count") != 12 or len(audits) != 12:
        raise legacy.CampaignSupervisorError("terminal-causal child audit incomplete")
    for slot, audit in zip(slots, audits, strict=True):
        previous._verify_child(root, spec, slot, dict(audit))
        child_spec = _read(
            root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])
            / "specification/soak_package_spec.json"
        )
        if any((
            child_spec.get("execution_source") != CHILD_EXECUTION_SOURCE,
            child_spec.get("ambiguous_flatten_mutation_retry_attempts") != 0,
            child_spec.get("two_snapshot_terminal_reconciliation_required") is not True,
            child_spec.get("terminal_causal_fill_attribution_repair_required") is not True,
            require_fifo_attribution
            and child_spec.get("fifo_attribution_identity_audit_required") is not True,
            require_workoff_timestamp_repair
            and child_spec.get("workoff_completion_timestamp_repair_required")
            is not True,
            require_workoff_timestamp_repair
            and child_spec.get("partial_workoff_restart_reconciliation_required")
            is not True,
            require_terminal_special_closure_repair
            and child_spec.get("terminal_special_fill_closure_required") is not True,
            require_terminal_special_closure_repair
            and child_spec.get(
                "controller_engine_account_multi_partial_reconciliation_required"
            ) is not True,
            require_terminal_special_closure_repair
            and child_spec.get("bounded_timeboxed_workoff_required") is not True,
            require_markout_special_closure_repair
            and child_spec.get(
                "normal_markout_special_closure_attribution_required"
            ) is not True,
            require_markout_special_closure_repair
            and child_spec.get(
                "terminal_special_closed_markout_serialization_required"
            ) is not True,
            require_markout_special_closure_repair
            and child_spec.get(
                "normal_markout_attribution_reconciliation_required"
            ) is not True,
            require_owned_cancel_reconciliation
            and child_spec.get(
                "authoritative_owned_cancel_reconciliation_required"
            ) is not True,
            require_owned_cancel_reconciliation
            and child_spec.get("cancel_fill_cursor_union_required") is not True,
            require_owned_cancel_reconciliation
            and child_spec.get("cancel_reconciliation_read_attempts") != 3,
            require_owned_cancel_reconciliation
            and child_spec.get("cancel_reconciliation_interval_seconds") != 2,
            require_owned_cancel_reconciliation
            and child_spec.get("cancel_mutation_retry_attempts") != 0,
            require_post_wall_interruption
            and child_spec.get("post_wall_interruption_audit_required") is not True,
            require_post_wall_interruption
            and child_spec.get("completed_active_session_ingestion_required")
            is not True,
            require_post_wall_interruption
            and child_spec.get("campaign_wall_fail_closed_required") is not True,
            require_post_wall_interruption
            and child_spec.get("resume_after_campaign_wall_authorized") is not False,
        )):
            raise legacy.CampaignSupervisorError(
                "terminal-causal child execution binding invalid"
            )
    return legacy.A2CampaignPackage(root, output, spec, slots)


def _main_with_loader(loader: object, failure_prefix: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("describe", "arm", "authorize-next", "accept-active", "verify")
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--campaign-arm-token", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--now-ms", type=int, default=0)
    parser.add_argument("--lease-ttl-ms", type=int, default=21_900_000)
    args = parser.parse_args()
    try:
        package = loader(args.root, args.package_id)  # type: ignore[operator]
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
            supervisor = legacy.CampaignSupervisor.arm(
                package,
                campaign_arm_token=args.campaign_arm_token,
                now_ms=args.now_ms,
            )
            supervisor.acquire_lease(
                owner=args.owner, now_ms=args.now_ms, ttl_ms=args.lease_ttl_ms
            )
            result = {
                "campaign_id": package.spec["campaign_id"],
                "armed": True,
                "lease_owner_sha256": hashlib.sha256(args.owner.encode()).hexdigest(),
            }
        else:
            supervisor = legacy.CampaignSupervisor.load(package)
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
                result = _decision_payload(decision)
            else:
                result = {
                    "package_id": package.spec["package_id"],
                    "verified": True,
                    "campaign_state_exists": supervisor.state_path.is_file(),
                }
    except Exception as exc:
        print(f"{failure_prefix}:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    return _main_with_loader(
        load_campaign_package,
        "TERMINAL_CAUSAL_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
