from pathlib import Path

import pytest

from okx_fill_restart_offline import (
    ARTIFACT_ROOT,
    OfflineEvidenceError,
    build_offline_spec,
    run_reference_fixture,
    source_hashes,
    verify_predecessors,
)


ROOT = Path(__file__).resolve().parents[1]


def test_predecessor_audit_passes_read_only() -> None:
    audit = verify_predecessors(ROOT)
    assert audit["passed"] is True
    assert audit["v16_non_authority_checked"] == 19
    assert audit["v16_non_authority_failures"] == []
    assert audit["prior_runtime_non_authority_checked"] == 17
    assert audit["prior_runtime_non_authority_failures"] == []
    assert audit["prior_artifacts_checked"] == 80
    assert audit["prior_artifact_failures"] == []
    assert all(audit["fixed_hashes"].values())


def test_offline_spec_is_demo_only_non_armed_and_exactly_bound() -> None:
    audit = verify_predecessors(ROOT)
    hashes = source_hashes(ROOT)
    spec, binding = build_offline_spec(
        root=ROOT,
        run_id="offline-spec-fixture",
        predecessor_audit=audit,
        hashes=hashes,
    )
    runtime = spec["runtime_configuration"]
    assert binding.execution_mode == "OFFLINE_FIXTURE"
    assert runtime["network_allowed"] is False
    assert runtime["preflight_allowed"] is False
    assert runtime["orders_allowed"] is False
    assert runtime["formal_execution_armed"] is False
    assert runtime["live_mode_available"] is False
    assert spec["profile_binding"]["profile_name"] == "FEE_AWARE_SPREAD_6"
    assert spec["profile_binding"]["profile_id"] == "mm-v1-6-profile-02"
    assert spec["risk_budget"]["maximum_inventory_btc"] == "0.01"
    assert spec["risk_budget"]["leverage"] == 3
    assert spec["ccxt_contract"]["inspection_only"] is True
    assert spec["ccxt_contract"]["network_used"] is False
    assert len(spec["fixture_manifest"]) >= 50


def test_reference_fixture_completes_r1_r2_without_external_activity(
    tmp_path: Path,
) -> None:
    audit = verify_predecessors(ROOT)
    spec, binding = build_offline_spec(
        root=ROOT,
        run_id="offline-reference-fixture",
        predecessor_audit=audit,
        hashes=source_hashes(ROOT),
    )
    result = run_reference_fixture(tmp_path, binding)
    assert spec["runtime_binding"] == binding.to_dict()
    assert result["passed"] is True
    assert result["r1_completed"] is True
    assert result["r2_completed"] is True
    assert result["normal_bid_fills"] == 1
    assert result["normal_ask_fills"] == 1
    assert result["normal_fifo_round_trips"] == 1
    assert result["final_position_btc"] == "0.00"
    assert result["final_owned_open_orders"] == 0
    assert result["resume_token_plaintext_persisted"] is False
    assert result["external_network_attempts"] == 0
    assert result["external_preflight_attempts"] == 0
    assert result["external_order_submissions"] == 0


def test_artifact_root_and_final_completion_are_reserved_for_formal_close() -> None:
    assert ARTIFACT_ROOT.as_posix() == "artifacts/okx_demo_fill_restart_validation"
    audit = verify_predecessors(ROOT)
    spec, _ = build_offline_spec(
        root=ROOT,
        run_id="offline-artifact-contract",
        predecessor_audit=audit,
        hashes=source_hashes(ROOT),
    )
    contract = spec["artifact_contract"]
    assert contract["non_overwriting"] is True
    assert contract["formal_execution_marker_created"] is False
    assert contract["completed_json_reserved_for_formal_close"] is True
    assert contract["offline_phase_terminal"] == "OFFLINE_PHASE_COMPLETED.json"


def test_failed_predecessor_audit_blocks_specification() -> None:
    with pytest.raises(OfflineEvidenceError, match="predecessor"):
        build_offline_spec(
            root=ROOT,
            run_id="offline-blocked",
            predecessor_audit={"passed": False},
            hashes=source_hashes(ROOT),
        )
