import json
from pathlib import Path

import pytest

import okx_demo_multi_session_a0_offline as offline
from okx_fill_restart_offline import _sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_protocol_predecessors_and_matrices_are_exact() -> None:
    hashes = offline.source_hashes(PROJECT_ROOT)
    assert hashes["AGENTS.md"] == hashes[offline.SUCCESSOR_SOURCE]
    soak = offline.verify_soak_predecessor(PROJECT_ROOT)
    assert soak["immutable"] is True
    assert soak["economic_promotion_evidence"] is False
    repair_failure = offline.verify_repair_failed_a2_predecessor(PROJECT_ROOT)
    assert repair_failure["immutable"] is True
    assert repair_failure["historical_create_counter_mismatch"] is True
    assert repair_failure["reported_normal_creates"] == 17
    assert repair_failure["authoritative_normal_create_dispatches"] == 18
    assert repair_failure["historical_failed_accounting_components_omitted"] is True
    clock_failure = offline.verify_clock_gate_failed_a2_predecessor(PROJECT_ROOT)
    assert clock_failure["immutable"] is True
    assert clock_failure["pre_mutation_failure"] is True
    assert clock_failure["normal_create_dispatches"] == 0
    assert clock_failure["clock_skew_ms"] == [2015, 2013]
    projection_failure = (
        offline.verify_registry_projection_failed_a2_predecessor(PROJECT_ROOT)
    )
    assert projection_failure["immutable"] is True
    assert projection_failure["source_evidence_seal_valid"] is True
    assert projection_failure["registry_projection_seal_valid"] is False
    assert projection_failure["session_two_never_authorized"] is True
    assert projection_failure["normal_create_dispatches"] == 38
    assert projection_failure["normal_create_acknowledgements"] == 38
    causal_clock_failure = offline.verify_causal_clock_failed_a2_predecessor(
        PROJECT_ROOT
    )
    assert causal_clock_failure["immutable"] is True
    assert causal_clock_failure["future_fill_within_clock_skew_budget"] is True
    assert causal_clock_failure["clock_skew_ms"] == [1307, 1311]
    assert causal_clock_failure["session_two_never_authorized"] is True
    assert causal_clock_failure["normal_create_dispatches"] == 2
    assert causal_clock_failure["normal_create_acknowledgements"] == 2
    cross_failure = offline.verify_pre_dispatch_cross_failed_a2_predecessor(
        PROJECT_ROOT
    )
    assert cross_failure["immutable"] is True
    assert cross_failure[
        "historical_pre_dispatch_cross_treated_as_session_failure"
    ] is True
    assert cross_failure["historical_pre_dispatch_mutation_delta"] == 0
    assert cross_failure[
        "historical_attempted_aggregate_component_mismatch"
    ] is True
    assert cross_failure["later_slots_never_authorized_or_started"] is True
    assert cross_failure["normal_create_dispatches"] == 168
    assert cross_failure["normal_create_acknowledgements"] == 168
    interrupted = offline.verify_interrupted_a2_predecessor(PROJECT_ROOT)
    assert interrupted["immutable"] is True
    assert interrupted["interrupted"] is True
    assert interrupted["campaign_incomplete"] is True
    assert interrupted["active_slot"] == 8
    assert interrupted["completed_slots"] == [1, 2, 3, 4, 5, 6, 7]
    assert interrupted["authoritative_terminal_account_unknown"] is True
    assert interrupted["last_durable_local_inventory_btc"] == "-0.010"
    assert interrupted["last_durable_owned_orders"] == 0
    assert interrupted[
        "same_generation_resume_rerun_or_recovery_allowed"
    ] is False
    assert interrupted["fresh_read_only_preflight_required"] is True
    requirements = offline.requirement_matrix()
    assert len(requirements) >= 7
    matrix = offline.scenario_matrix()
    assert len(matrix["groups"]) == 10
    assert sum(len(group["cases"]) for group in matrix["groups"]) == len(
        offline.EXPECTED_DECISIONS
    )
    assert matrix["A3_authorized"] is False


def test_offline_builder_writes_terminal_marker_last_and_refuses_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(offline, "ARTIFACT_ROOT", tmp_path)

    def fake_suites(root: Path, output: Path):
        suites = {
            name: {
                "returncode": 0,
                "passed": count,
                "network_attempts": 0,
                "live_endpoint_attempts": 0,
                "optuna_imported": False,
                "passed_gate": True,
            }
            for name, count in (
                ("multi_session_targeted", 1),
                ("root_non_optuna", 1),
                ("backtest_non_optuna", 1),
            )
        }
        offline._write_json(output / "tests/test_summary.json", suites)
        return suites

    monkeypatch.setattr(offline, "run_suites", fake_suites)
    evidence_id = "multi-session-a0-offline-fixture"
    output = offline.run_offline(PROJECT_ROOT, evidence_id)
    marker_path = output / "A0_OFFLINE_BUILD_COMPLETED.json"
    marker = json.loads(marker_path.read_text())
    assert marker["A0_passed"] is True
    assert marker["network_attempts"] == 0
    assert marker["orders_submitted"] == 0
    hashes = json.loads((output / "completion_hashes.json").read_text())
    assert all(_sha256(output / relative) == expected for relative, expected in hashes.items())
    other_times = [
        path.stat().st_mtime_ns
        for path in output.rglob("*")
        if path.is_file() and path != marker_path
    ]
    assert marker_path.stat().st_mtime_ns >= max(other_times)
    with pytest.raises(offline.A0OfflineError, match="reuse"):
        offline.run_offline(PROJECT_ROOT, evidence_id)


def test_invalid_a0_identity_fails_before_writing(tmp_path: Path) -> None:
    with pytest.raises(offline.A0OfflineError, match="identity"):
        offline.run_offline(PROJECT_ROOT, "bad-id")
    assert not list(tmp_path.iterdir())
