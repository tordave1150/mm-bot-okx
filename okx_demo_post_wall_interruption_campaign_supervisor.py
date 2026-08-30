"""Fail-closed supervisor for the post-wall interruption successor campaign."""

from __future__ import annotations

import json
from pathlib import Path

import okx_demo_terminal_causal_cli_campaign_supervisor as base
from okx_demo_multi_session_prepare import CAMPAIGN_ARTIFACT_ROOT
from okx_demo_multi_session_supervisor import A2CampaignPackage, CampaignSupervisorError


PROTOCOL_ID = "okx-demo-post-wall-interruption-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_post_wall_interruption_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_post_wall_interruption_campaign_supervisor.py"
EVIDENCE_KIND = "post_wall_interruption_r0_offline_audit"
FAILED_PACKAGE_ID = "economic-package-20260826T160834Z"
FAILED_RUN_ID = "economic-campaign-run-20260826T160834Z"
TERMINAL_SESSION_ID = "soak-package-20260826T160834Z-s05-0d028be127"


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
        failed_session_id=TERMINAL_SESSION_ID,
        failed_audit_name="failed_post_wall_package_audit.json",
        require_owned_cancel_reconciliation=True,
        require_post_wall_interruption=True,
        unstarted_slots_field="slots_6_through_12_started",
    )
    audit_path = (
        root.resolve() / CAMPAIGN_ARTIFACT_ROOT / package_id
        / "predecessor/failed_post_wall_package_audit.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if any((
        audit.get("completed_slots") != [1, 2, 3, 4, 5],
        audit.get("failed_slots") != [],
        audit.get("slots_6_through_12_started") is not False,
        audit.get("terminal_reason") != "CAMPAIGN_WALL_BUDGET",
        audit.get("stale_lease_recovered") is not True,
        audit.get("completed_active_slot_ingested") is not True,
        audit.get("terminal_account_authoritative") is not True,
        audit.get("terminal_position_btc") != "0",
        audit.get("terminal_open_orders") != 0,
        audit.get("normal_fill_count") != 43,
        audit.get("normal_fifo_round_trips") != 21,
        audit.get("special_flatten_sessions") != 1,
        audit.get("unclassified_quote_mode_ticks") != 0,
        audit.get("unsafe_sessions") != 0,
    )):
        raise CampaignSupervisorError("post-wall predecessor binding invalid")
    if package.spec.get("special_flatten_session_limit") != 2:
        raise CampaignSupervisorError("special-flatten limit binding invalid")
    return package


def main() -> int:
    return base._main_with_loader(
        load_campaign_package,
        "POST_WALL_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
