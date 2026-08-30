import json
from pathlib import Path
import socket

import pytest

from okx_fill_restart_formal import FormalRunSpecification, canonical_sha256
from okx_fill_restart_formal_prepare import (
    DEFAULT_OFFLINE_RUN_ID,
    DEFAULT_PREFLIGHT_RUN_ID,
    FORMAL_PACKAGE_STATUS,
    FormalPackageError,
    _OfflineSocketGuard,
    _artifact_secret_scan,
    build_formal_package_spec,
    formal_source_hashes,
    verify_preflight_evidence,
)
from okx_fill_restart_offline import verify_predecessors


ROOT = Path(__file__).resolve().parents[1]


def _inputs():
    preflight_result = json.loads(
        (
            ROOT / "artifacts" / "okx_demo_fill_restart_validation"
            / "preflight-20260805T-arm02" / "preflight" / "preflight_result.json"
        ).read_text(encoding="utf-8")
    )
    return (
        verify_predecessors(ROOT),
        {
            "passed": True,
            "offline_run_id": DEFAULT_OFFLINE_RUN_ID,
            "completion_hashes_sha256": "a" * 64,
        },
        {
            "passed": True,
            "market_fingerprint": preflight_result["market_fingerprint"],
            "market_spec": preflight_result["market_spec"],
            "completion_hashes_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
        formal_source_hashes(ROOT),
    )


def test_stale_preflight_is_rejected_after_formal_source_repairs() -> None:
    with pytest.raises(FormalPackageError, match="preflight-bound source changed"):
        verify_preflight_evidence(ROOT, "preflight-20260805T-arm02")


def test_formal_sources_extend_without_changing_preflight_bound_sources() -> None:
    hashes = formal_source_hashes(ROOT)
    assert len(hashes) == 35
    for relative in (
        "okx_fill_restart_formal.py",
        "okx_fill_restart_gateway.py",
        "okx_fill_restart_executor.py",
        "okx_fill_restart_formal_prepare.py",
        "tests/test_okx_fill_restart_formal.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_executor.py",
        "tests/test_okx_fill_restart_formal_prepare.py",
    ):
        assert len(hashes[relative]) == 64


def test_formal_package_spec_is_complete_but_not_armed() -> None:
    predecessor, offline, preflight, hashes = _inputs()
    spec, controller = build_formal_package_spec(
        root=ROOT,
        package_id="formal-package-fixture",
        formal_run_id="formal-fixture",
        predecessor_audit=predecessor,
        offline_audit=offline,
        preflight_audit=preflight,
        hashes=hashes,
    )
    FormalRunSpecification.from_dict(controller.to_dict())
    boundary = spec["package_boundary"]
    assert boundary["network_attempts"] == 0
    assert boundary["preflight_executed_in_this_task"] is False
    assert boundary["orders_submitted"] == 0
    assert boundary["formal_execution_armed"] is False
    assert boundary["formal_arm_token_issued"] is False
    assert boundary["execution_marker_created"] is False
    assert boundary["live_mode_available"] is False
    assert spec["runtime_configuration"]["package_freeze_mode"] == "OFFLINE_FIXTURE"
    assert spec["runtime_configuration"]["execution_mode_after_separate_arm"] == "OKX_DEMO"
    assert spec["profile_binding"]["profile_name"] == "FEE_AWARE_SPREAD_6"
    assert spec["risk_budget"]["maximum_inventory_btc"] == "0.01"
    assert spec["risk_budget"]["maximum_normal_creates"] == 120
    assert spec["endpoint_contract"]["fallback_to_live"] is False
    assert spec["endpoint_contract"]["normal_create_non_secret_fields"]["postOnly"] is True
    assert spec["ccxt_contract"]["exchange_amount_contracts"] == "1"
    assert spec["restart_contract"]["new_run_id_on_resume"] is False
    assert spec["artifact_contract"]["formal_execution_marker_created_by_package_freeze"] is False

    base = dict(spec)
    base.pop("package_specification_sha256")
    base.pop("package_hash_contract")
    base.pop("formal_controller_specification")
    assert canonical_sha256(base) == controller.package_specification_sha256


def test_formal_package_rejects_failed_evidence_and_bad_identity() -> None:
    predecessor, offline, preflight, hashes = _inputs()
    failed = dict(preflight)
    failed["passed"] = False
    with pytest.raises(FormalPackageError, match="offline or preflight"):
        build_formal_package_spec(
            root=ROOT,
            package_id="formal-package-fixture",
            formal_run_id="formal-fixture",
            predecessor_audit=predecessor,
            offline_audit=offline,
            preflight_audit=failed,
            hashes=hashes,
        )
    with pytest.raises(FormalPackageError, match="package ID"):
        build_formal_package_spec(
            root=ROOT,
            package_id="bad",
            formal_run_id="formal-fixture",
            predecessor_audit=predecessor,
            offline_audit=offline,
            preflight_audit=preflight,
            hashes=hashes,
        )


def test_missing_preflight_and_secret_pattern_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(FormalPackageError, match="incomplete"):
        verify_preflight_evidence(tmp_path, "preflight-missing")
    (tmp_path / "safe.json").write_text(
        '{"status":"%s","orders":0}\n' % FORMAL_PACKAGE_STATUS,
        encoding="utf-8",
    )
    assert _artifact_secret_scan(tmp_path)["passed"] is True
    (tmp_path / "bad.txt").write_text(
        "OKX_API_KEY=definitely-not-a-real-key\n", encoding="utf-8"
    )
    scan = _artifact_secret_scan(tmp_path)
    assert scan["passed"] is False
    assert scan["secret_pattern_matches"] == ["bad.txt"]
    (tmp_path / "bad.txt").unlink()

    guard = _OfflineSocketGuard()
    with guard:
        candidate = socket.socket()
        try:
            with pytest.raises(FormalPackageError, match="network access"):
                candidate.connect(("127.0.0.1", 1))
        finally:
            candidate.close()
    assert guard.attempts == ["tuple"]
