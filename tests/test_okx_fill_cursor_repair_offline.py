from pathlib import Path

import pytest

from okx_fill_cursor_repair_offline import (
    FAILED_FORMAL_RUN_ID,
    FAILED_PACKAGE_ID,
    FAILED_STATUS,
    FillCursorRepairError,
    build_repair_spec,
    repair_source_hashes,
    verify_failed_predecessor,
)


ROOT = Path(__file__).resolve().parents[1]


def test_failed_formal_predecessor_is_frozen_flat_and_not_reusable() -> None:
    audit = verify_failed_predecessor(ROOT)
    assert audit["passed"] is True
    assert audit["package_id"] == FAILED_PACKAGE_ID
    assert audit["formal_run_id"] == FAILED_FORMAL_RUN_ID
    assert audit["status"] == FAILED_STATUS
    assert audit["formal_execution_marker_count"] == 1
    assert audit["R1_completed"] is False
    assert audit["R2_completed"] is False
    assert audit["terminal_position_btc"] == "0"
    assert audit["terminal_open_orders"] == 0
    assert audit["immutable"] is True


def test_repair_spec_binds_union_partial_flatten_and_fresh_ids() -> None:
    predecessor = verify_failed_predecessor(ROOT)
    hashes = repair_source_hashes(ROOT)
    spec = build_repair_spec(
        repair_id="repair-offline-fixture",
        predecessor=predecessor,
        source_hashes=hashes,
    )

    contract = spec["repair_contract"]
    assert contract["query_order"] == ["paginated_history", "recent_tail"]
    assert contract["recent_tail_limit"] == 100
    assert contract["union_key"] == "trade_id"
    assert contract["conflicting_overlap"] == "fail_closed"
    assert contract["single_flatten_create_maximum"] == 1
    assert contract["multi_partial_flatten_supported"] is True
    identifiers = spec["fresh_identifier_contract"]
    assert identifiers["forbidden_package_id"] == FAILED_PACKAGE_ID
    assert identifiers["forbidden_formal_run_id"] == FAILED_FORMAL_RUN_ID
    assert identifiers["fresh_preflight_token_required"] is True
    assert spec["network_allowed"] is False
    assert spec["orders_allowed"] is False
    assert spec["successor_protocol_active"] is False


def test_repair_sources_include_successor_protocol_and_runtime() -> None:
    hashes = repair_source_hashes(ROOT)
    for relative in (
        "AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md",
        "okx_fill_cursor_repair_offline.py",
        "okx_fill_restart_gateway.py",
        "okx_fill_restart_executor.py",
        "okx_fill_restart_validation.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_validation.py",
    ):
        assert len(hashes[relative]) == 64


def test_repair_identity_must_be_fresh_and_non_overlapping() -> None:
    predecessor = verify_failed_predecessor(ROOT)
    hashes = repair_source_hashes(ROOT)
    with pytest.raises(FillCursorRepairError, match="repair ID"):
        build_repair_spec(
            repair_id=FAILED_FORMAL_RUN_ID,
            predecessor=predecessor,
            source_hashes=hashes,
        )
