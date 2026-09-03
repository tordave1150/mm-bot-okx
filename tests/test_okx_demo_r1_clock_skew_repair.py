from pathlib import Path

import okx_demo_r1_clock_skew_repair_offline as repair


ROOT = Path(__file__).resolve().parents[1]


def test_failed_clock_skew_preflight_is_immutable_and_non_resumable() -> None:
    audit = repair.verify_failed_preflight(ROOT)
    assert audit["failure_reason"] == "CLOCK_SKEW_EXCEEDS_FROZEN_LIMIT"
    assert audit["resume_authorized"] is False
    assert audit["identity_reuse_authorized"] is False
    assert audit["terminal_account_authoritative"] is False
