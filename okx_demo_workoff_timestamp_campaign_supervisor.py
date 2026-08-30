"""Fail-closed supervisor for the work-off timestamp successor campaign."""

from __future__ import annotations

import json
from pathlib import Path

import okx_demo_terminal_causal_cli_campaign_supervisor as base
from okx_demo_multi_session_prepare import CAMPAIGN_ARTIFACT_ROOT
from okx_demo_multi_session_supervisor import A2CampaignPackage, CampaignSupervisorError


PROTOCOL_ID = "okx-demo-workoff-timestamp-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_workoff_timestamp_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_workoff_timestamp_campaign_supervisor.py"
EVIDENCE_KIND = "workoff_timestamp_r0_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260821T173615Z"
FAILED_RUN_ID = "economic-campaign-run-20260821T173615Z"
FAILED_SESSION_ID = "soak-package-20260821T173615Z-s12-870c0378a3"


def load_campaign_package(root: Path, package_id: str) -> A2CampaignPackage:
    package = base.load_campaign_package(
        root,
        package_id,
        protocol_id=PROTOCOL_ID,
        preparation_source=PREPARATION_SOURCE,
        execution_source=EXECUTION_SOURCE,
        evidence_kind=EVIDENCE_KIND,
        failed_package_id=FAILED_PACKAGE_ID,
        failed_run_id=FAILED_RUN_ID,
        failed_session_id=FAILED_SESSION_ID,
        failed_audit_name="failed_workoff_timestamp_package_audit.json",
        require_workoff_timestamp_repair=True,
        unstarted_slots_field=None,
    )
    audit_path = (
        root.resolve() / CAMPAIGN_ARTIFACT_ROOT / package_id
        / "predecessor/failed_workoff_timestamp_package_audit.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if any((
        audit.get("all_slots_attempted") is not True,
        audit.get("completed_slots") != list(range(1, 12)),
        audit.get("failed_slots") != [12],
        audit.get("terminal_account_snapshots") != 2,
        audit.get("terminal_position_btc") != "0",
        audit.get("terminal_open_orders") != 0,
    )):
        raise CampaignSupervisorError("work-off predecessor terminal binding invalid")
    return package


def main() -> int:
    return base._main_with_loader(
        load_campaign_package,
        "WORKOFF_TIMESTAMP_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
