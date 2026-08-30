from pathlib import Path

import okx_demo_r2_session5_terminal_reconciliation_repair_offline as repair


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_failed_session5_boundary_is_immutable_and_unresolved() -> None:
    predecessor = repair.verify_predecessor(PROJECT_ROOT)

    assert predecessor["immutable"] is True
    assert predecessor["completed_slots"] == [1, 2, 3, 4]
    assert predecessor["active_failed_slot"] == 5
    assert predecessor["sessions_6_through_12_started"] is False
    assert predecessor["terminal_account_authoritative"] is False
    assert predecessor["mutation_retries"] == 0
    assert predecessor["live_endpoint_attempts"] == 0
    assert predecessor["live_orders"] == 0


def test_session5_root_cause_binds_read_exhaustion_to_stale_cancel_view() -> None:
    diagnostic = repair.diagnose(PROJECT_ROOT)

    assert diagnostic["passed"] is True
    assert diagnostic["primary_fetch_account_retry_rows"] == 3
    assert diagnostic["shutdown_fetch_account_retry_rows"] == 1
    assert diagnostic["flatten_intents"] == 1
    assert diagnostic["flatten_acknowledged_events"] == 0
    assert diagnostic["repair"]["flatten_requires_durable_cancel_convergence"] is True


def test_repair_projection_preserves_frozen_mutation_boundary() -> None:
    projection = repair.repair_projection()

    assert projection["passed"] is True
    assert projection["read_attempts"] == 3
    assert projection["mutation_retries"] == 0
    assert projection["risk_limits_changed"] is False
    assert any(
        row["expected"].startswith("one cancel; zero flatten")
        for row in projection["fixtures"]
    )
