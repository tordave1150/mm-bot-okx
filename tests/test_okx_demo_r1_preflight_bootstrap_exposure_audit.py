from pathlib import Path

import okx_demo_r1_preflight_bootstrap_exposure_audit_offline as audit


ROOT = Path(__file__).resolve().parents[1]


def test_failed_preflight_exposure_is_immutable_and_non_resumable() -> None:
    result = audit.verify_failed_preflight(ROOT)

    assert result["failure_reason"] == "UNOWNED_DEMO_EXPOSURE"
    assert result["signed_position_btc"] == "-0.01"
    assert result["open_orders"] == 0
    assert result["mutation_attempts"] == 0
    assert result["terminal_account_authoritative"] is False
    assert result["rerun_authorized"] is False
    assert result["resume_authorized"] is False
