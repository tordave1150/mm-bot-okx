from pathlib import Path

import okx_demo_r1_network_transport_analysis_offline as analysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_failed_r1_transport_run_is_immutable_and_non_mutating() -> None:
    result = analysis.verify_failed_run(PROJECT_ROOT)
    assert result["immutable"] is True
    assert result["network_read_attempts"] == 6
    assert result["mutation_attempts"] == 0
    assert result["identity_reuse_authorized"] is False


def test_diagnosis_does_not_misclassify_environment_transport_as_code_defect() -> None:
    result = analysis.diagnosis()
    assert result["passed"] is True
    assert result["environment_network_capable"] is False
    assert result["adapter_defect_proven"] is False
    assert result["risk_expansion"] is False
