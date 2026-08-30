from datetime import datetime, timezone
import json
from pathlib import Path
import socket

import pytest

import okx_fill_restart_preflight_prepare as preparation_module

from okx_fill_restart_preflight import expected_arm_token
from okx_fill_restart_preflight_prepare import (
    PreflightPreparationError,
    _OfflineSocketGuard,
    make_identifiers,
    prepare_preflight_package,
)


ROOT = Path(__file__).resolve().parents[1]


def test_identifiers_are_fresh_scoped_and_deterministically_bound() -> None:
    values = make_identifiers(
        repair_id="repair-offline-20260805T155548Z",
        source_manifest_sha256="a" * 64,
        now=datetime(2026, 8, 5, 16, 20, 30, tzinfo=timezone.utc),
    )
    assert values["preparation_id"] == "preflight-package-20260805T162030Z"
    assert values["run_id"] == "preflight-20260805T162030Z"
    assert values["session_id"].startswith(
        "preflight:preflight-20260805T162030Z:p0:"
    )
    assert values["arm_token"] == expected_arm_token(values["session_id"])


def test_offline_socket_guard_blocks_dispatch_and_counts_attempt() -> None:
    guard = _OfflineSocketGuard()
    with guard:
        with pytest.raises(PreflightPreparationError, match="network access"):
            socket.create_connection(("127.0.0.1", 9))
    assert guard.attempts == ["tuple"]


def test_r2_repair_evidence_kind_can_prepare_offline_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "r2_warmup_audit_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"runtime.py": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(
        preparation_module, "ARTIFACT_ROOT", Path("preflight_runs")
    )
    output, identifiers = prepare_preflight_package(
        tmp_path, "r2-repair-offline-fixture"
    )
    assert output.is_dir()
    assert identifiers["run_id"].startswith("preflight-")
    assert not (
        tmp_path / "preflight_runs" / identifiers["run_id"]
    ).exists()
    terminal = output / "PREFLIGHT_PREPARATION_COMPLETED.json"
    assert terminal.is_file()


def test_multi_session_a0_can_prepare_only_a_fresh_unarmed_a1_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "multi-session-a0-offline-fixture"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "multi_session_a0_offline_build",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(
        preparation_module, "ARTIFACT_ROOT", Path("preflight_runs")
    )

    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)

    spec = json.loads(
        (output / "specification" / "preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    terminal = json.loads(
        (output / "PREFLIGHT_PREPARATION_COMPLETED.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == "multi_session_a0_offline_build"
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert terminal["network_attempts"] == 0
    assert terminal["orders_submitted"] == 0
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_sample_efficiency_r0_can_prepare_only_a_fresh_unarmed_r1_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "economic-repair-offline-fixture"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "sample_efficiency_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(
        preparation_module, "ARTIFACT_ROOT", Path("preflight_runs")
    )

    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification" / "preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "sample_efficiency_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_terminal_recovery_can_prepare_only_fresh_unarmed_r1_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "terminal-recovery-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "r2_post_start_terminal_recovery_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(
        preparation_module, "ARTIFACT_ROOT", Path("preflight_runs")
    )

    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification" / "preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "r2_post_start_terminal_recovery_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_terminal_causal_cli_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "terminal-causal-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module, "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "terminal_causal_cli_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module, "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages"))
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads((output / "specification/preflight_preparation.json").read_text(encoding="utf-8"))
    assert spec["protocol_id"] == "okx-demo-multi-session-a1-preflight-preparation-v1"
    assert spec["predecessor_evidence_kind"] == "terminal_causal_cli_r0_offline_repair"
    assert spec["preflight_authorized"] is False
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_fifo_attribution_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "fifo-attribution-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "fifo_attribution_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module,
        "PREPARATION_ARTIFACT_ROOT",
        Path("preflight_packages"),
    )
    monkeypatch.setattr(
        preparation_module, "ARTIFACT_ROOT", Path("preflight_runs")
    )
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "fifo_attribution_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_workoff_timestamp_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "workoff-timestamp-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "workoff_timestamp_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    assert spec["protocol_id"] == "okx-demo-multi-session-a1-preflight-preparation-v1"
    assert spec["predecessor_evidence_kind"] == "workoff_timestamp_r0_offline_repair"
    assert spec["preflight_authorized"] is False
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_terminal_special_closure_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "terminal-special-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "terminal_special_closure_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "terminal_special_closure_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_markout_special_closure_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "markout-special-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "markout_special_closure_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "markout_special_closure_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_owned_cancel_repair_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "owned-cancel-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "owned_cancel_reconciliation_r0_offline_repair",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "owned_cancel_reconciliation_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_post_wall_audit_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "post-wall-audit-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": "post_wall_interruption_r0_offline_audit",
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    )
    assert spec["protocol_id"] == (
        "okx-demo-multi-session-a1-preflight-preparation-v1"
    )
    assert spec["predecessor_evidence_kind"] == (
        "post_wall_interruption_r0_offline_audit"
    )
    assert spec["preflight_authorized"] is False
    assert spec["preflight_executed"] is False
    assert identifiers["arm_token"] not in serialized
    assert not (tmp_path / "preflight_runs" / identifiers["run_id"]).exists()


def test_market_bootstrap_terminal_reconciliation_can_prepare_only_unarmed_r1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_id = "market-bootstrap-terminal-repair-offline-20990101T000000Z"
    monkeypatch.setattr(
        preparation_module,
        "verify_offline_evidence",
        lambda root, repair_id: {
            "evidence_kind": (
                "market_bootstrap_terminal_reconciliation_r0_offline_repair"
            ),
            "completion_hashes_sha256": "a" * 64,
            "terminal_sha256": "b" * 64,
            "decision_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        preparation_module,
        "preflight_source_hashes",
        lambda root: {"AGENTS.md": "d" * 64},
    )
    monkeypatch.setattr(
        preparation_module, "PREPARATION_ARTIFACT_ROOT", Path("preflight_packages")
    )
    monkeypatch.setattr(preparation_module, "ARTIFACT_ROOT", Path("preflight_runs"))
    output, identifiers = prepare_preflight_package(tmp_path, evidence_id)
    spec = json.loads(
        (output / "specification/preflight_preparation.json").read_text(
            encoding="utf-8"
        )
    )
    assert spec["protocol_id"] == "okx-demo-multi-session-a1-preflight-preparation-v1"
    assert spec["predecessor_evidence_kind"] == (
        "market_bootstrap_terminal_reconciliation_r0_offline_repair"
    )
    assert spec["preflight_authorized"] is False
    assert identifiers["arm_token"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file()
    )
