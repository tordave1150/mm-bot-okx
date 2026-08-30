"""Fail-closed supervisor for the FIFO-attribution successor campaign."""

from __future__ import annotations

from pathlib import Path

import okx_demo_terminal_causal_cli_campaign_supervisor as base
from okx_demo_multi_session_supervisor import A2CampaignPackage


PROTOCOL_ID = "okx-demo-fifo-attribution-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_fifo_attribution_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_fifo_attribution_campaign_supervisor.py"
EVIDENCE_KIND = "fifo_attribution_r0_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260821T152208Z"
FAILED_RUN_ID = "economic-campaign-run-20260821T152208Z"
FAILED_SESSION_ID = "soak-package-20260821T152208Z-s02-7bcaa22607"


def load_campaign_package(root: Path, package_id: str) -> A2CampaignPackage:
    return base.load_campaign_package(
        root,
        package_id,
        protocol_id=PROTOCOL_ID,
        preparation_source=PREPARATION_SOURCE,
        execution_source=EXECUTION_SOURCE,
        evidence_kind=EVIDENCE_KIND,
        failed_package_id=FAILED_PACKAGE_ID,
        failed_run_id=FAILED_RUN_ID,
        failed_session_id=FAILED_SESSION_ID,
        failed_audit_name="failed_fifo_attribution_package_audit.json",
        require_fifo_attribution=True,
        unstarted_slots_field="slot_3_through_12_started",
    )


def main() -> int:
    return base._main_with_loader(
        load_campaign_package,
        "FIFO_ATTRIBUTION_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
