from pathlib import Path

import pytest

import okx_demo_terminal_recovery_campaign_prepare as prepare
import okx_demo_terminal_recovery_campaign_supervisor as supervisor
import okx_demo_terminal_recovery_session_start as session_start
from okx_demo_terminal_recovery_executor import TerminalRecoveryExecutor


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ID = "terminal-recovery-offline-20260820T162258Z"


def test_failed_post_start_predecessor_hashes_are_still_immutable() -> None:
    audit = prepare._failed_predecessor_audit(ROOT, EVIDENCE_ID)
    assert audit["package_id"] == supervisor.FAILED_PACKAGE_ID
    assert audit["campaign_run_id"] == supervisor.FAILED_RUN_ID
    assert audit["session_package_id"] == supervisor.FAILED_SESSION_ID
    assert audit["resume_authorized"] is False
    assert audit["flatten_retry_authorized"] is False
    assert audit["slot_2_through_12_started"] is False


def test_old_failed_package_is_not_a_successor_package() -> None:
    with pytest.raises(supervisor.legacy.CampaignSupervisorError):
        supervisor.load_campaign_package(ROOT, supervisor.FAILED_PACKAGE_ID)


def test_source_bound_session_start_uses_terminal_recovery_executor() -> None:
    assert session_start.TerminalRecoveryExecutor is TerminalRecoveryExecutor


def test_campaign_decision_cli_payload_serializes_enum_value() -> None:
    payload = supervisor._decision_payload(
        supervisor.legacy.CampaignDecision.NOT_READY
    )
    assert payload == {"decision": "NOT_READY"}
