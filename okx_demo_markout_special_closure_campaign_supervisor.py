"""Fail-closed supervisor for the markout/special-closure successor campaign."""

from __future__ import annotations

import json
from pathlib import Path

import okx_demo_terminal_causal_cli_campaign_supervisor as base
from okx_demo_multi_session_prepare import CAMPAIGN_ARTIFACT_ROOT
from okx_demo_multi_session_supervisor import A2CampaignPackage, CampaignSupervisorError


PROTOCOL_ID = "okx-demo-markout-special-closure-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_markout_special_closure_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_markout_special_closure_campaign_supervisor.py"
EVIDENCE_KIND = "markout_special_closure_r0_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260822T091916Z"
FAILED_RUN_ID = "economic-campaign-run-20260822T091916Z"
FAILED_SESSION_ID = "soak-package-20260822T091916Z-s01-b5c6b47506"


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
        failed_audit_name="failed_markout_special_closure_package_audit.json",
        require_markout_special_closure_repair=True,
        unstarted_slots_field="slots_2_through_12_started",
    )
    audit_path = (
        root.resolve() / CAMPAIGN_ARTIFACT_ROOT / package_id
        / "predecessor/failed_markout_special_closure_package_audit.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if any((
        audit.get("completed_slots") != [],
        audit.get("failed_slots") != [1],
        audit.get("slots_2_through_12_started") is not False,
        audit.get("terminal_account_snapshots") != 2,
        audit.get("terminal_position_btc") != "0",
        audit.get("terminal_open_orders") != 0,
        audit.get("normal_fill_count") != 3,
        audit.get("causal_maker_fill_count") != 2,
        audit.get("causal_markout_count") != 2,
        audit.get("terminal_special_closed_fill_count") != 1,
        audit.get("terminal_special_closed_markout_count") != 1,
    )):
        raise CampaignSupervisorError(
            "markout/special-closure predecessor binding invalid"
        )
    if package.spec.get("special_flatten_session_limit") != 2:
        raise CampaignSupervisorError("special-flatten limit binding invalid")
    return package


def main() -> int:
    return base._main_with_loader(
        load_campaign_package,
        "MARKOUT_SPECIAL_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
