"""Fail-closed admission layer for execution-environment successor campaigns."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import okx_demo_multi_session_supervisor as legacy
from okx_fill_restart_offline import _sha256


PROTOCOL_ID = "okx-demo-execution-environment-economic-campaign-v1"
REPAIR_KIND = "execution_environment_transport_r0_offline_repair"
R0_EVIDENCE_KINDS = frozenset({
    REPAIR_KIND,
    "r2_session5_terminal_reconciliation_r0_offline_repair",
    "r2_session1_cancel_fill_reconciliation_r0_offline_repair",
})
READY_STATUS = "R2_SUCCESSOR_PACKAGE_READY"


class SuccessorSupervisorError(RuntimeError):
    pass


def verify_package(root: Path, package_id: str) -> dict[str, object]:
    path = root / legacy.CAMPAIGN_ARTIFACT_ROOT / package_id
    spec_path = path / "specification" / "campaign_package_spec.json"
    source_path = path / "specification" / "source_hashes.json"
    if not spec_path.is_file() or not source_path.is_file():
        raise SuccessorSupervisorError("successor package is incomplete")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    required = ("okx_demo_execution_environment_successor_prepare.py",
                "okx_demo_execution_environment_successor_supervisor.py")
    if any((
        spec.get("protocol_id") != PROTOCOL_ID,
        spec.get("execution_source") != Path(__file__).name,
        spec.get("r0_evidence_kind") not in R0_EVIDENCE_KINDS,
        spec.get("session_count") != 12,
        spec.get("campaign_authorized") is not False,
        spec.get("campaign_executed") is not False,
        spec.get("network_authorized_during_freeze") is not False,
        spec.get("orders_authorized_during_freeze") is not False,
        any(not isinstance(sources.get(name), str) or _sha256(root / name) != sources[name]
            for name in required),
    )):
        raise SuccessorSupervisorError("successor package binding is invalid")
    return {"passed": True, "package_id": package_id,
            "source_manifest_sha256": hashlib.sha256(
                json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()}


def load_campaign_package(root: Path, package_id: str) -> legacy.A2CampaignPackage:
    """Load a sealed successor package through the legacy durable engine."""
    verify_package(root, package_id)
    try:
        return legacy.load_campaign_package(
            root,
            package_id,
            expected_protocol_id=PROTOCOL_ID,
            expected_ready_status=READY_STATUS,
        )
    except legacy.CampaignSupervisorError as exc:
        raise SuccessorSupervisorError(
            "successor package cannot enter the durable campaign engine"
        ) from exc


def arm_authorized(
    root: Path, package_id: str, arm_token: str, *, now_ms: int | None = None
) -> legacy.CampaignSupervisor:
    """Create the one fresh durable campaign run after exact R2 authorization."""
    package = load_campaign_package(root, package_id)
    observed_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        return legacy.CampaignSupervisor.arm(
            package,
            campaign_arm_token=arm_token,
            now_ms=observed_now_ms,
        )
    except legacy.CampaignSupervisorError as exc:
        raise SuccessorSupervisorError("successor campaign arm was refused") from exc
