from pathlib import Path

import okx_demo_r2_session1_special_closure_repair_offline as repair


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_failed_session1_is_immutable_and_terminally_safe() -> None:
    predecessor = repair.verify_predecessor(PROJECT_ROOT)
    assert predecessor["immutable"] is True
    assert predecessor["active_failed_slot"] == 1
    assert predecessor["session_2_started"] is False
    assert predecessor["terminal_account_authoritative"] is True
    assert predecessor["final_position_btc"] == "0"
    assert predecessor["final_open_orders"] == 0
    assert predecessor["mutation_retries"] == 0


def test_root_cause_is_double_counted_cross_zero_workoff() -> None:
    result = repair.diagnose(PROJECT_ROOT)
    assert result["passed"] is True
    assert result["pre_flatten_engine_inventory_btc"] == "-0.0055"
    assert result["old_causal_inventory_btc"] == "-0.0035"
    assert result["mismatch_btc"] == "0.0020"
    assert result["repair"]["opposing_fill_quantity_is_fifo_netted"] is True


def test_projection_preserves_fail_closed_frozen_boundary() -> None:
    result = repair.projection()
    assert result["passed"] is True
    assert result["mutation_retries"] == 0
    assert result["risk_limits_changed"] is False
    assert len(result["fixtures"]) == 4
