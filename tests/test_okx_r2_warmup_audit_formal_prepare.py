from datetime import datetime, timezone
from pathlib import Path

import pytest

from okx_fill_restart_formal_prepare import verify_preflight_evidence
from okx_fill_restart_preflight import verify_offline_evidence
from okx_r2_warmup_audit_formal_prepare import (
    FAILED_FORMAL_RUN_ID,
    FAILED_PACKAGE_ID,
    PREFLIGHT_RUN_ID,
    REPAIR_ID,
    R2FormalPackageError,
    _build_spec,
    _fresh_identifiers,
    _predecessor_template,
    _source_hashes,
)


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_formal_identifiers_are_new_and_deterministic() -> None:
    package_id, run_id = _fresh_identifiers(
        datetime(2026, 8, 7, 14, 40, 0, tzinfo=timezone.utc)
    )
    assert package_id == "formal-package-20260807T144000Z"
    assert run_id == "formal-20260807T144000Z"
    assert package_id != FAILED_PACKAGE_ID
    assert run_id != FAILED_FORMAL_RUN_ID


def test_formal_identifier_timestamp_must_be_timezone_aware() -> None:
    with pytest.raises(R2FormalPackageError, match="timezone aware"):
        _fresh_identifiers(datetime(2026, 8, 7, 14, 40, 0))


def test_failed_r2_formal_template_remains_read_only_and_immutable() -> None:
    template = _predecessor_template(ROOT)
    assert template["package_id"] == FAILED_PACKAGE_ID
    assert template["formal_run_id"] == FAILED_FORMAL_RUN_ID
    assert template["risk_budget"]["maximum_normal_creates"] == 120
    assert template["risk_budget"]["maximum_unresolved_flatten"] == 1


def test_successor_spec_binds_r2_repair_preflight_and_new_audit_contract() -> None:
    repair = verify_offline_evidence(ROOT, REPAIR_ID)
    preflight = verify_preflight_evidence(ROOT, PREFLIGHT_RUN_ID)
    hashes = _source_hashes(ROOT)
    spec, formal = _build_spec(
        root=ROOT,
        package_id="formal-package-fixture-r2-successor",
        formal_run_id="formal-fixture-r2-successor",
        repair_audit=repair,
        preflight_audit=preflight,
        hashes=hashes,
    )
    contract = spec["runtime_configuration"]["r2_warmup_audit_contract"]
    assert contract["new_process_quote_engine_warmed_before_probe"] is True
    assert contract["warmup_and_probe_protected_mutation_delta"] == 0
    assert contract["hash_chained_gateway_audit_per_generation"] is True
    assert contract["terminal_counters_cumulative_across_generations"] is True
    assert contract["terminal_order_event_reconciliation"] is True
    assert spec["package_boundary"]["network_attempts"] == 0
    assert spec["package_boundary"]["execution_marker_created"] is False
    assert formal.offline_completion_sha256 == repair["completion_hashes_sha256"]
    assert formal.preflight_completion_sha256 == preflight[
        "completion_hashes_sha256"
    ]


def test_successor_spec_rejects_wrong_repair_kind() -> None:
    repair = dict(verify_offline_evidence(ROOT, REPAIR_ID))
    repair["evidence_kind"] = "future_book_timestamp_repair"
    preflight = verify_preflight_evidence(ROOT, PREFLIGHT_RUN_ID)
    with pytest.raises(R2FormalPackageError, match="repair or preflight"):
        _build_spec(
            root=ROOT,
            package_id="formal-package-fixture-wrong-repair",
            formal_run_id="formal-fixture-wrong-repair",
            repair_audit=repair,
            preflight_audit=preflight,
            hashes=_source_hashes(ROOT),
        )


def test_formal_source_manifest_contains_r2_repair_runtime_and_fixtures() -> None:
    hashes = _source_hashes(ROOT)
    assert "okx_r2_warmup_audit_formal_prepare.py" in hashes
    assert "tests/test_okx_r2_warmup_audit_formal_prepare.py" in hashes
    assert "okx_r2_warmup_audit_repair_offline.py" in hashes
    assert "tests/test_okx_r2_warmup_audit_counter_repair.py" in hashes

