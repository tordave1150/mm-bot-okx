from pathlib import Path

import okx_demo_r2_bootstrap_exposure_admission_repair_offline as repair
from okx_demo_multi_session_prepare import _admissible_r0_for_r2


ROOT = Path(__file__).resolve().parents[1]


def test_passed_r1_lineage_is_bound_to_non_reusable_admission() -> None:
    result = repair.verify_r2_predecessor(ROOT)

    assert result["r1_preflight_passed"] is True
    assert result["r1_mutation_attempts"] == 0
    assert result["r1_live_endpoint_attempts"] == 0
    assert result["failed_R1_identity_reusable"] is False


def test_r2_mapping_rejects_reusable_or_mutating_r1_lineage() -> None:
    accepted = {
        "passed": True,
        "evidence_kind": "r2_bootstrap_exposure_admission_r0_offline_repair",
        "r1_preflight_passed": True,
        "r1_mutation_attempts": 0,
        "r1_live_endpoint_attempts": 0,
        "failed_R1_identity_reusable": False,
        "resume_authorized": False,
    }
    assert _admissible_r0_for_r2(accepted) is True
    assert _admissible_r0_for_r2({**accepted, "r1_mutation_attempts": 1}) is False
    assert _admissible_r0_for_r2({**accepted, "failed_R1_identity_reusable": True}) is False
