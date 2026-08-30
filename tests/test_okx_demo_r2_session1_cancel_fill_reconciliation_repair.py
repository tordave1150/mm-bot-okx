from pathlib import Path

import okx_demo_r2_session1_cancel_fill_reconciliation_repair_offline as repair


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_session1_predecessor_is_immutable_and_failed_closed() -> None:
    predecessor = repair.verify_predecessor(PROJECT_ROOT)
    assert predecessor["immutable"] is True
    assert predecessor["normal_create_dispatches"] == 4
    assert predecessor["flatten_dispatches"] == 0
    assert predecessor["mutation_retries"] == 0
    assert predecessor["session_2_authorized"] is False


def test_session1_diagnostic_requires_trade_proven_fill() -> None:
    result = repair.diagnostic(PROJECT_ROOT)
    assert result["passed"] is True
    assert result["old_owned_trade_union_rows"] == 0
    assert result["repair"]["filled_during_cancel_requires_owned_trade_quantity_proof"] is True
    assert result["repair"]["persistent_ambiguity_blocks_flatten_before_dispatch"] is True


def test_promotion_rehearsal_preserves_frozen_risk() -> None:
    result = repair.projection()
    assert result["passed"] is True
    assert result["mutation_retries"] == 0
    assert result["risk_limits_changed"] is False
    assert len(result["fixtures"]) == 3
