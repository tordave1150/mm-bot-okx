from pathlib import Path

import pytest

from okx_fill_cursor_formal_prepare import (
    FAILED_FORMAL_RUN_ID,
    FAILED_PACKAGE_ID,
    PREFLIGHT_RUN_ID,
    REPAIR_ID,
    SuccessorFormalPackageError,
    build_successor_formal_spec,
    successor_formal_source_hashes,
)
from okx_fill_restart_formal_prepare import verify_preflight_evidence
from okx_fill_restart_preflight import verify_offline_evidence


ROOT = Path(__file__).resolve().parents[1]


def _inputs():
    return (
        verify_offline_evidence(ROOT, REPAIR_ID),
        verify_preflight_evidence(ROOT, PREFLIGHT_RUN_ID),
        successor_formal_source_hashes(ROOT),
    )


def test_successor_sources_extend_preflight_without_mutating_repair_runtime() -> None:
    hashes = successor_formal_source_hashes(ROOT)
    assert "okx_fill_cursor_formal_prepare.py" in hashes
    assert "tests/test_okx_fill_cursor_formal_prepare.py" in hashes
    assert "okx_fill_restart_gateway.py" in hashes
    assert "okx_fill_restart_executor.py" in hashes


def test_successor_spec_binds_union_partial_flatten_and_fresh_identity() -> None:
    repair, preflight, hashes = _inputs()
    spec, formal = build_successor_formal_spec(
        root=ROOT,
        package_id="formal-package-successor-fixture",
        formal_run_id="formal-successor-fixture",
        repair_audit=repair,
        preflight_audit=preflight,
        hashes=hashes,
    )
    pagination = spec["runtime_configuration"]["fill_pagination"]
    flatten = spec["runtime_configuration"]["flatten_partial_fill_contract"]
    assert pagination["query_order"] == ["paginated_history", "recent_tail"]
    assert pagination["conflicting_duplicate_policy"] == "fail_closed"
    assert flatten["maximum_flatten_creates"] == 1
    assert flatten["exact_sum_to_known_position"] is True
    assert spec["package_boundary"]["formal_execution_armed"] is False
    assert spec["package_boundary"]["execution_marker_created"] is False
    assert formal.session_id.startswith("formal:formal-successor-fixture:p0:")
    assert formal.expected_arm_token == f"OKX_DEMO:{formal.session_id}"


def test_failed_predecessor_identities_cannot_be_reused() -> None:
    repair, preflight, hashes = _inputs()
    with pytest.raises(SuccessorFormalPackageError, match="package ID"):
        build_successor_formal_spec(
            root=ROOT,
            package_id=FAILED_PACKAGE_ID,
            formal_run_id="formal-successor-fixture",
            repair_audit=repair,
            preflight_audit=preflight,
            hashes=hashes,
        )
    with pytest.raises(SuccessorFormalPackageError, match="run ID"):
        build_successor_formal_spec(
            root=ROOT,
            package_id="formal-package-successor-fixture",
            formal_run_id=FAILED_FORMAL_RUN_ID,
            repair_audit=repair,
            preflight_audit=preflight,
            hashes=hashes,
        )
