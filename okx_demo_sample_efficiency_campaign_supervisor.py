"""Fail-closed supervisor entrypoint for the sample-efficiency R2 protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

import okx_demo_multi_session_supervisor as legacy
from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_multi_session_prepare import (
    CAMPAIGN_ARTIFACT_ROOT,
    READY_STATUS,
    SESSION_ARTIFACT_ROOT,
    _risk_budget,
)
from okx_fill_restart_offline import _sha256
from okx_fill_restart_validation import canonical_sha256


PROTOCOL_ID = "okx-demo-sample-efficiency-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_sample_efficiency_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_sample_efficiency_campaign_supervisor.py"
R0_EVIDENCE_KIND = "sample_efficiency_r0_offline_repair"


def _read(path: Path) -> dict[str, object]:
    return legacy._read_json(path)


def _verify_child(
    root: Path,
    campaign_spec: Mapping[str, object],
    slot: Mapping[str, object],
    audit: Mapping[str, object],
) -> None:
    output = root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])
    terminal_path = output / "SOAK_PACKAGE_COMPLETED.json"
    completion_path = output / "completion_hashes.json"
    spec_path = output / "specification/soak_package_spec.json"
    manifest = _read(completion_path)
    terminal = _read(terminal_path)
    spec = _read(spec_path)
    failures = [
        relative for relative, expected in manifest.items()
        if not (output / relative).is_file()
        or _sha256(output / relative) != expected
    ]
    if any((
        failures,
        _sha256(completion_path) != terminal.get("completion_hashes_sha256"),
        _sha256(terminal_path) != audit.get("terminal_sha256"),
        _sha256(completion_path) != audit.get("completion_hashes_sha256"),
        terminal.get("campaign_authorized") is not False,
        terminal.get("soak_executed") is not False,
        terminal.get("execution_marker_created") is not False,
        spec.get("protocol_id") != "okx-demo-multi-session-economic-soak-v1",
        spec.get("package_id") != slot.get("package_id"),
        spec.get("run_id") != slot.get("run_id"),
        spec.get("session_id") != slot.get("session_id"),
        spec.get("campaign_package_id") != campaign_spec.get("package_id"),
        spec.get("campaign_id") != campaign_spec.get("campaign_id"),
        spec.get("campaign_slot") != slot.get("slot"),
        spec.get("source_manifest_sha256")
        != campaign_spec.get("source_manifest_sha256"),
        spec.get("risk_budget") != _risk_budget(),
        spec.get("soak_authorized") is not False,
        spec.get("soak_executed") is not False,
    )):
        raise legacy.CampaignSupervisorError(
            "successor R2 child package binding is invalid"
        )


def load_campaign_package(
    root: Path, package_id: str
) -> legacy.A2CampaignPackage:
    root = root.resolve()
    if not package_id.startswith("economic-package-"):
        raise legacy.CampaignSupervisorError("successor R2 package identity is invalid")
    output = root / CAMPAIGN_ARTIFACT_ROOT / package_id
    terminal_path = output / "A2_PACKAGE_COMPLETED.json"
    spec_path = output / "specification/campaign_package_spec.json"
    source_path = output / "specification/source_hashes.json"
    child_path = output / "specification/session_package_audits.json"
    completion_path = output / "completion_hashes.json"
    r0_path = output / "predecessor/r0_evidence_audit.json"
    r1_path = output / "predecessor/preflight_evidence_audit.json"
    rejected_path = output / "predecessor/rejected_prearm_package_audit.json"
    required = (
        terminal_path, spec_path, source_path, child_path, completion_path,
        r0_path, r1_path, rejected_path,
    )
    if not all(path.is_file() for path in required):
        raise legacy.CampaignSupervisorError("successor R2 package is incomplete")
    terminal = _read(terminal_path)
    spec = _read(spec_path)
    r0 = _read(r0_path)
    r1 = _read(r1_path)
    rejected = _read(rejected_path)
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
        spec.get("campaign_authorized") is not False,
        spec.get("campaign_executed") is not False,
        spec.get("execution_marker_created") is not False,
        spec.get("session_count") != CampaignLimits().maximum_sessions,
        spec.get("campaign_limits") != CampaignLimits().to_dict(),
        spec.get("session_risk_budget") != _risk_budget(),
        r0.get("evidence_kind") != R0_EVIDENCE_KIND,
        r0.get("passed") is not True,
        r0.get("economic_predecessor_verified") is not True,
        r1.get("passed") is not True,
        r1.get("zero_mutation_verified") is not True,
        r1.get("r0_evidence_id") != spec.get("r0_evidence_id"),
        rejected.get("package_id") != "economic-package-20260820T135400Z",
        rejected.get("arm_marker_exists") is not False,
        rejected.get("immutable_rejected_prearm") is not True,
    )):
        raise legacy.CampaignSupervisorError(
            "successor R2 package boundary is invalid"
        )
    canonical_spec = dict(spec)
    expected_spec_hash = str(canonical_spec.pop("specification_sha256", ""))
    if canonical_sha256(canonical_spec) != expected_spec_hash:
        raise legacy.CampaignSupervisorError(
            "successor R2 specification hash mismatch"
        )
    completion = _read(completion_path)
    failures = [
        relative for relative, expected in completion.items()
        if not (output / relative).is_file()
        or _sha256(output / relative) != expected
    ]
    if failures or _sha256(completion_path) != terminal.get(
        "completion_hashes_sha256"
    ):
        raise legacy.CampaignSupervisorError(
            "successor R2 completion hash mismatch"
        )
    sources = _read(source_path)
    source_failures = [
        relative for relative, expected in sources.items()
        if not (root / relative).is_file() or _sha256(root / relative) != expected
    ]
    if source_failures or canonical_sha256(sources) != spec.get(
        "source_manifest_sha256"
    ):
        raise legacy.CampaignSupervisorError("successor R2 source binding mismatch")
    slots = tuple(dict(item) for item in list(spec.get("session_slots") or []))
    if (
        len(slots) != 12
        or [item.get("slot") for item in slots] != list(range(1, 13))
        or len({item.get("package_id") for item in slots}) != 12
        or len({item.get("run_id") for item in slots}) != 12
        or len({item.get("session_id") for item in slots}) != 12
    ):
        raise legacy.CampaignSupervisorError(
            "successor R2 session identities are invalid"
        )
    child = _read(child_path)
    audits = list(child.get("packages") or [])
    if child.get("count") != 12 or len(audits) != 12:
        raise legacy.CampaignSupervisorError("successor child audit is incomplete")
    for slot, audit in zip(slots, audits, strict=True):
        _verify_child(root, spec, slot, dict(audit))
    return legacy.A2CampaignPackage(root, output, spec, slots)


def fail_closed_from_external_flat_confirmation(
    package: legacy.A2CampaignPackage,
    *,
    owner: str,
    now_ms: int,
    confirmation: Mapping[str, object],
) -> legacy.CampaignDecision:
    """Close a stuck campaign only; external confirmation is not economic evidence."""
    raw = dict(confirmation)
    seal = str(raw.pop("confirmation_sha256", ""))
    if any((
        canonical_sha256(raw) != seal,
        raw.get("campaign_id") != package.spec.get("campaign_id"),
        raw.get("position_btc") != "0",
        raw.get("open_orders") != 0,
        raw.get("reported_by") != "user",
        raw.get("authoritative_exchange_snapshot") is not False,
        raw.get("economic_evidence") is not False,
        raw.get("resume_authorized") is not False,
    )):
        raise legacy.CampaignSupervisorError(
            "external terminal confirmation binding is invalid"
        )
    supervisor = legacy.CampaignSupervisor.load(package)
    supervisor._append_event("EXTERNAL_FLAT_EMPTY_REPORTED", {
        "confirmation_sha256": seal,
        "economic_evidence": False,
        "resume_authorized": False,
    })
    return supervisor.fail_closed(
        owner=owner,
        now_ms=now_ms,
        reason="EXTERNAL_FLAT_EMPTY_AFTER_UNRESOLVED_CHILD_NOT_ECONOMIC_EVIDENCE",
    )


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
                "lease_owner_sha256": hashlib.sha256(
                    args.owner.encode("utf-8")
                ).hexdigest(),
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
                result = {"decision": decision.value}
            else:
                result = legacy.verify_campaign_run(package)
    except Exception as exc:
        print(
            f"R2_SUCCESSOR_SUPERVISOR_FAILED:{type(exc).__name__}:{exc}"
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
