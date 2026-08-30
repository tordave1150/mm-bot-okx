"""Fail-closed supervisor for the post-terminal-recovery R2 campaign."""

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


PROTOCOL_ID = "okx-demo-terminal-recovery-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_terminal_recovery_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_terminal_recovery_campaign_supervisor.py"
EVIDENCE_KIND = "r2_post_start_terminal_recovery_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260820T141231Z"
FAILED_RUN_ID = "economic-campaign-run-20260820T141231Z"
FAILED_SESSION_ID = "soak-package-20260820T141231Z-s01-83ef4ee73e"


def _read(path: Path) -> dict[str, object]:
    return legacy._read_json(path)


def _decision_payload(decision: legacy.CampaignDecision) -> dict[str, object]:
    return {"decision": decision.value}


def load_campaign_package(root: Path, package_id: str) -> legacy.A2CampaignPackage:
    root = root.resolve()
    if not package_id.startswith("economic-package-"):
        raise legacy.CampaignSupervisorError("terminal-recovery package identity invalid")
    output = root / CAMPAIGN_ARTIFACT_ROOT / package_id
    terminal_path = output / "A2_PACKAGE_COMPLETED.json"
    spec_path = output / "specification/campaign_package_spec.json"
    source_path = output / "specification/source_hashes.json"
    child_path = output / "specification/session_package_audits.json"
    completion_path = output / "completion_hashes.json"
    repair_path = output / "predecessor/r0_evidence_audit.json"
    r1_path = output / "predecessor/preflight_evidence_audit.json"
    failed_path = output / "predecessor/failed_post_start_package_audit.json"
    required = (terminal_path, spec_path, source_path, child_path, completion_path, repair_path, r1_path, failed_path)
    if not all(path.is_file() for path in required):
        raise legacy.CampaignSupervisorError("terminal-recovery package incomplete")
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
        spec.get("protocol_id") != PROTOCOL_ID,
        spec.get("preparation_source") != PREPARATION_SOURCE,
        spec.get("execution_source") != EXECUTION_SOURCE,
        spec.get("child_execution_source") != "okx_demo_terminal_recovery_session_start.py",
        spec.get("campaign_authorized") is not False,
        spec.get("campaign_executed") is not False,
        spec.get("execution_marker_created") is not False,
        spec.get("session_count") != CampaignLimits().maximum_sessions,
        spec.get("campaign_limits") != CampaignLimits().to_dict(),
        spec.get("session_risk_budget") != _risk_budget(),
        spec.get("two_snapshot_terminal_reconciliation_required") is not True,
        spec.get("mutation_retry_attempts") != 0,
        repair.get("evidence_kind") != EVIDENCE_KIND,
        repair.get("passed") is not True,
        repair.get("failed_campaign_decision") != "NOT_READY",
        repair.get("account_confirmation_is_exchange_authoritative") is not False,
        r1.get("passed") is not True,
        r1.get("zero_mutation_verified") is not True,
        r1.get("r0_evidence_id") != spec.get("repair_evidence_id"),
        failed.get("package_id") != FAILED_PACKAGE_ID,
        failed.get("campaign_run_id") != FAILED_RUN_ID,
        failed.get("session_package_id") != FAILED_SESSION_ID,
        failed.get("immutable_failed_post_start") is not True,
        failed.get("campaign_decision") != "NOT_READY",
        failed.get("resume_authorized") is not False,
        failed.get("flatten_retry_authorized") is not False,
        failed.get("slot_2_through_12_started") is not False,
    )):
        raise legacy.CampaignSupervisorError("terminal-recovery package boundary invalid")
    canonical = dict(spec)
    expected_spec_hash = str(canonical.pop("specification_sha256", ""))
    if canonical_sha256(canonical) != expected_spec_hash:
        raise legacy.CampaignSupervisorError("terminal-recovery specification hash mismatch")
    completion = _read(completion_path)
    failures = [relative for relative, expected in completion.items() if not (output / relative).is_file() or _sha256(output / relative) != expected]
    if failures or _sha256(completion_path) != terminal.get("completion_hashes_sha256"):
        raise legacy.CampaignSupervisorError("terminal-recovery completion hash mismatch")
    sources = _read(source_path)
    source_failures = [relative for relative, expected in sources.items() if not (root / relative).is_file() or _sha256(root / relative) != expected]
    if source_failures or canonical_sha256(sources) != spec.get("source_manifest_sha256"):
        raise legacy.CampaignSupervisorError("terminal-recovery source binding mismatch")
    slots = tuple(dict(item) for item in list(spec.get("session_slots") or []))
    if len(slots) != 12 or [x.get("slot") for x in slots] != list(range(1, 13)) or len({x.get("package_id") for x in slots}) != 12 or len({x.get("run_id") for x in slots}) != 12 or len({x.get("session_id") for x in slots}) != 12:
        raise legacy.CampaignSupervisorError("terminal-recovery session identities invalid")
    child = _read(child_path)
    audits = list(child.get("packages") or [])
    if child.get("count") != 12 or len(audits) != 12:
        raise legacy.CampaignSupervisorError("terminal-recovery child audit incomplete")
    for slot, audit in zip(slots, audits, strict=True):
        previous._verify_child(root, spec, slot, dict(audit))
        child_spec = _read(
            root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])
            / "specification/soak_package_spec.json"
        )
        if any((
            child_spec.get("execution_source")
            != "okx_demo_terminal_recovery_session_start.py",
            child_spec.get("ambiguous_flatten_mutation_retry_attempts") != 0,
            child_spec.get("two_snapshot_terminal_reconciliation_required")
            is not True,
        )):
            raise legacy.CampaignSupervisorError(
                "terminal-recovery child execution binding invalid"
            )
    return legacy.A2CampaignPackage(root, output, spec, slots)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("describe", "arm", "authorize-next", "accept-active", "verify"))
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
                "package_id": package.spec["package_id"], "campaign_id": package.spec["campaign_id"],
                "run_id": package.spec["run_id"], "campaign_session_id": package.spec["campaign_session_id"],
                "expected_campaign_arm_token": package.expected_campaign_arm_token,
                "session_count": len(package.slots), "campaign_started": (package.output / "campaign_run").exists(),
            }
        elif args.command == "arm":
            supervisor = legacy.CampaignSupervisor.arm(package, campaign_arm_token=args.campaign_arm_token, now_ms=args.now_ms)
            supervisor.acquire_lease(owner=args.owner, now_ms=args.now_ms, ttl_ms=args.lease_ttl_ms)
            result = {"campaign_id": package.spec["campaign_id"], "armed": True, "lease_owner_sha256": hashlib.sha256(args.owner.encode()).hexdigest()}
        else:
            supervisor = legacy.CampaignSupervisor.load(package)
            if args.command == "authorize-next":
                slot = supervisor.authorize_next_session(owner=args.owner, now_ms=args.now_ms)
                result = {**slot, "expected_session_arm_token": f"OKX_DEMO:{slot['session_id']}"}
            elif args.command == "accept-active":
                decision = supervisor.accept_active_session_artifact(owner=args.owner, now_ms=args.now_ms)
                result = _decision_payload(decision)
            else:
                result = {"package_id": package.spec["package_id"], "verified": True, "campaign_state_exists": supervisor.state_path.is_file()}
    except Exception as exc:
        print(f"TERMINAL_RECOVERY_CAMPAIGN_SUPERVISOR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
