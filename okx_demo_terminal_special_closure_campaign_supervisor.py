"""Fail-closed supervisor for the terminal special-closure successor campaign."""

from __future__ import annotations

import json
from pathlib import Path

import okx_demo_terminal_causal_cli_campaign_supervisor as base
from okx_demo_multi_session_prepare import CAMPAIGN_ARTIFACT_ROOT
from okx_demo_multi_session_supervisor import A2CampaignPackage, CampaignSupervisorError


PROTOCOL_ID = "okx-demo-terminal-special-closure-economic-campaign-v1"
PREPARATION_SOURCE = "okx_demo_terminal_special_closure_campaign_prepare.py"
EXECUTION_SOURCE = "okx_demo_terminal_special_closure_campaign_supervisor.py"
EVIDENCE_KIND = "terminal_special_closure_r0_offline_repair"
FAILED_PACKAGE_ID = "economic-package-20260822T040609Z"
FAILED_RUN_ID = "economic-campaign-run-20260822T040609Z"
FAILED_SESSION_ID = "soak-package-20260822T040609Z-s11-7ab96d4c71"


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
        failed_audit_name="failed_terminal_special_closure_package_audit.json",
        require_terminal_special_closure_repair=True,
        unstarted_slots_field="slot_12_started",
    )
    audit_path = (
        root.resolve() / CAMPAIGN_ARTIFACT_ROOT / package_id
        / "predecessor/failed_terminal_special_closure_package_audit.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if any((
        audit.get("completed_slots") != list(range(1, 11)),
        audit.get("failed_slots") != [11],
        audit.get("slot_12_started") is not False,
        audit.get("terminal_account_snapshots") != 2,
        audit.get("terminal_position_btc") != "0",
        audit.get("terminal_open_orders") != 0,
        audit.get("special_flatten_sessions") != 4,
    )):
        raise CampaignSupervisorError(
            "terminal special-closure predecessor binding invalid"
        )
    if package.spec.get("special_flatten_session_limit") != 2:
        raise CampaignSupervisorError("special-flatten limit binding invalid")
    return package


def main() -> int:
    return base._main_with_loader(
        load_campaign_package,
        "TERMINAL_SPECIAL_CAMPAIGN_SUPERVISOR_FAILED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
