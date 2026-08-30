from pathlib import Path

import okx_demo_multi_session_supervisor as legacy
import okx_demo_terminal_causal_cli_campaign_prepare as prepare
import okx_demo_terminal_causal_cli_campaign_supervisor as supervisor


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "terminal-causal-repair-offline-20260821T150036Z"


def test_failed_terminal_causal_predecessor_is_immutable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["rerun_authorized"] is False
    assert audit["slot_2_through_12_started"] is False


def test_campaign_decision_cli_serializes_enum_value() -> None:
    assert supervisor._decision_payload(legacy.CampaignDecision.NOT_READY) == {
        "decision": "NOT_READY"
    }
