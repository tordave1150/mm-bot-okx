from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import okx_demo_multi_session_prepare as prepare_module
from okx_demo_multi_session_prepare import (
    A2PackageError,
    _admissible_r0_for_r2,
    _identifiers,
    _predecessor_r0_evidence_id,
    _verify_a1,
    prepare,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _a0() -> dict[str, object]:
    return {
        "passed": True,
        "evidence_kind": "multi_session_a0_offline_build",
        "evidence_id": "multi-session-a0-offline-fixture",
        "completion_hashes_sha256": "a" * 64,
        "terminal_sha256": "b" * 64,
        "decision_sha256": "c" * 64,
        "formal_predecessor_verified": True,
        "soak_predecessor_verified": True,
        "failed_a2_predecessor_verified": True,
    }


def _a1() -> dict[str, object]:
    return {
        "passed": True,
        "run_id": "preflight-fixture",
        "completion_hashes_sha256": "d" * 64,
        "terminal_sha256": "e" * 64,
        "decision_sha256": "f" * 64,
        "market_fingerprint": "1" * 64,
        "market_spec": {"symbol": "BTC/USDT:USDT", "linear": True},
        "endpoint_hosts": ["www.okx.com"],
    }


def _patch_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture.py"
    fixture.write_text("# source fixture\n", encoding="utf-8")
    sources = {"fixture.py": _sha(fixture)}
    monkeypatch.setattr(
        prepare_module, "verify_offline_evidence", lambda root, evidence_id: _a0()
    )
    monkeypatch.setattr(
        prepare_module,
        "_verify_a1",
        lambda root, preflight_run_id, a0_evidence_id: _a1(),
    )
    monkeypatch.setattr(prepare_module, "_source_hashes", lambda root: sources)


def test_identifiers_predeclare_exactly_twelve_unique_bound_sessions() -> None:
    values = _identifiers(
        source_manifest_sha256="a" * 64,
        preflight_completion_sha256="b" * 64,
        now=datetime(2026, 8, 11, 17, 0, 0, tzinfo=timezone.utc),
    )
    slots = list(values["session_slots"])
    assert values["package_id"] == "economic-package-20260811T170000Z"
    assert values["campaign_id"] == "economic-campaign-20260811T170000Z"
    assert len(slots) == 12
    assert [slot["slot"] for slot in slots] == list(range(1, 13))
    assert len({slot["package_id"] for slot in slots}) == 12
    assert len({slot["run_id"] for slot in slots}) == 12
    assert len({slot["session_id"] for slot in slots}) == 12
    for slot in slots:
        expected = hashlib.sha256(
            f"OKX_DEMO:{slot['session_id']}".encode("utf-8")
        ).hexdigest()
        assert slot["arm_token_sha256"] == expected
        assert slot["arm_token_serialized"] is False


def test_successor_risk_budget_changes_economic_policy_without_risk_expansion() -> None:
    budget = prepare_module._risk_budget()
    assert budget["economic_repair_version"] == "r0-terminal-workoff-v2"
    assert budget["minimum_half_spread_bps"] == "4.0"
    assert budget["balanced_quote_retention_threshold_ticks"] == 10
    assert budget["defense_quote_retention_threshold_ticks"] == 20
    assert budget["draining_workoff_max_quote_observations"] == 6
    assert budget["draining_workoff_max_refreshes"] == 3
    assert budget["session_wall_minutes"] == 30
    assert budget["session_normal_create_cap"] == 60
    assert budget["admission_create_cap"] == 48
    assert budget["maker_workoff_create_reserve"] == 12
    assert budget["maximum_inventory_btc"] == "0.01"
    assert budget["leverage"] == 3
    assert budget["ambiguous_mutation_retry_attempts"] == 0


def test_v2_terminal_workoff_r0_admission_is_exact_and_fail_closed() -> None:
    accepted = {
        "passed": True,
        "evidence_kind": "r2_terminal_workoff_v2_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False,
    }
    assert _admissible_r0_for_r2(accepted) is True
    assert _admissible_r0_for_r2({
        **accepted, "terminal_account_authoritative": False,
    }) is False
    assert _predecessor_r0_evidence_id(accepted) is None
    assert _predecessor_r0_evidence_id({
        **accepted,
        "offline_run_id": "r2-terminal-workoff-v2-repair-offline-fixture",
    }) == "r2-terminal-workoff-v2-repair-offline-fixture"


def test_clock_skew_r0_admission_requires_failed_identity_and_nonresume() -> None:
    accepted = {
        "passed": True,
        "evidence_kind": "r1_clock_skew_r0_offline_repair",
        "offline_run_id": "r1-clock-skew-repair-offline-fixture",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": False,
        "resume_authorized": False,
        "failed_preparation_id": "preflight-package-fixture",
        "failed_run_id": "preflight-fixture",
        "failed_session_id": "preflight:fixture:p0:nonce",
    }
    assert _admissible_r0_for_r2(accepted) is True
    assert _predecessor_r0_evidence_id(accepted) == "r1-clock-skew-repair-offline-fixture"
    assert _admissible_r0_for_r2({**accepted, "resume_authorized": True}) is False
    assert _admissible_r0_for_r2({**accepted, "failed_session_id": None}) is False


def test_special_flatten_r1_admission_requires_nonreusable_failed_identity() -> None:
    accepted = {
        "passed": True,
        "evidence_kind": "r1_special_flatten_admission_r0_offline_repair",
        "offline_run_id": "r1-special-flatten-admission-repair-offline-fixture",
        "failed_preflight_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "failed_identity_reusable": False,
        "rerun_authorized": False,
        "resume_authorized": False,
    }
    assert _admissible_r0_for_r2(accepted) is True
    assert _predecessor_r0_evidence_id(accepted) == accepted["offline_run_id"]
    assert _admissible_r0_for_r2({**accepted, "failed_identity_reusable": True}) is False


def test_prepare_freezes_campaign_and_children_without_execution_or_token_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepare(tmp_path, monkeypatch)
    output, identifiers = prepare(
        tmp_path,
        a0_evidence_id="multi-session-a0-offline-fixture",
        preflight_run_id="preflight-fixture",
        run_suite=lambda *args: {"passed_gate": True, "passed": 1},
    )
    terminal = json.loads(
        (output / "A2_PACKAGE_COMPLETED.json").read_text(encoding="utf-8")
    )
    audits = json.loads(
        (output / "specification/session_package_audits.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (
            output,
            tmp_path / prepare_module.SESSION_ARTIFACT_ROOT,
        )
        for path in base.rglob("*")
        if path.is_file()
    )
    assert terminal["status"] == (
        "A2_MULTI_SESSION_ECONOMIC_PACKAGE_FROZEN_OFFLINE"
    )
    assert terminal["campaign_authorized"] is False
    assert terminal["campaign_executed"] is False
    assert terminal["network_attempts"] == 0
    assert terminal["orders_submitted"] == 0
    assert audits["count"] == 12
    assert len(audits["packages"]) == 12
    assert identifiers["campaign_arm_token"] not in serialized
    assert not (output / "campaign_run").exists()
    for slot in identifiers["session_slots"]:
        child = tmp_path / prepare_module.SESSION_ARTIFACT_ROOT / slot["package_id"]
        child_terminal = json.loads(
            (child / "SOAK_PACKAGE_COMPLETED.json").read_text(encoding="utf-8")
        )
        assert child_terminal["campaign_authorized"] is False
        assert child_terminal["soak_executed"] is False
        assert not (child / "soak_run/SOAK_EXECUTION_ARMED.json").exists()


def test_a1_verifier_rejects_unresolved_or_unexpected_endpoint_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "preflight-fixture"
    output = (
        tmp_path
        / "artifacts/okx_demo_fill_restart_validation"
        / run_id
    )
    for relative, payload in {
        "preflight/preflight_result.json": {
            "transport_audit": {"endpoint_hosts": ["{hostname}"]},
            "mutation_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        },
        "specification/preflight_spec.json": {
            "protocol_id": "okx-demo-multi-session-a1-read-only-preflight-v1"
        },
        "predecessor/offline_evidence_audit.json": {
            "evidence_kind": "multi_session_a0_offline_build",
            "evidence_id": "multi-session-a0-offline-fixture",
        },
    }.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        prepare_module,
        "verify_preflight_evidence",
        lambda root, requested: {"passed": True, "run_id": requested},
    )
    with pytest.raises(A2PackageError, match="not eligible"):
        _verify_a1(
            tmp_path,
            preflight_run_id=run_id,
            a0_evidence_id="multi-session-a0-offline-fixture",
        )


def test_prepare_rejects_wrong_a0_kind_before_creating_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(
        prepare_module,
        "verify_offline_evidence",
        lambda root, evidence_id: {**_a0(), "evidence_kind": "legacy"},
    )
    with pytest.raises(A2PackageError, match="A0 evidence"):
        prepare(
            tmp_path,
            a0_evidence_id="multi-session-a0-offline-fixture",
            preflight_run_id="preflight-fixture",
            run_suite=lambda *args: {"passed_gate": True},
        )
