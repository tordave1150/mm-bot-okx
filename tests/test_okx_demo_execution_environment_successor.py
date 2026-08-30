import hashlib
import json
from pathlib import Path

import pytest

import okx_demo_execution_environment_successor_supervisor as supervisor
import okx_demo_execution_environment_successor_prepare as preparer


def _package(root: Path, package_id: str = "economic-package-fixture") -> Path:
    output = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / package_id
    (output / "specification").mkdir(parents=True)
    sources = {}
    for name in ("okx_demo_execution_environment_successor_prepare.py", "okx_demo_execution_environment_successor_supervisor.py"):
        path = root / name
        path.write_text("fixture", encoding="utf-8")
        sources[name] = hashlib.sha256(b"fixture").hexdigest()
    spec = {"protocol_id": supervisor.PROTOCOL_ID, "execution_source": "okx_demo_execution_environment_successor_supervisor.py", "r0_evidence_kind": supervisor.REPAIR_KIND, "session_count": 12, "campaign_authorized": False, "campaign_executed": False, "network_authorized_during_freeze": False, "orders_authorized_during_freeze": False}
    (output / "specification/campaign_package_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (output / "specification/source_hashes.json").write_text(json.dumps(sources), encoding="utf-8")
    return output


def test_source_tamper_is_rejected(tmp_path: Path) -> None:
    _package(tmp_path)
    assert supervisor.verify_package(tmp_path, "economic-package-fixture")["passed"] is True
    (tmp_path / "okx_demo_execution_environment_successor_prepare.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(supervisor.SuccessorSupervisorError):
        supervisor.verify_package(tmp_path, "economic-package-fixture")


def test_unauthorized_start_is_rejected(tmp_path: Path) -> None:
    output = _package(tmp_path)
    spec_path = output / "specification/campaign_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")); spec["campaign_authorized"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(supervisor.SuccessorSupervisorError):
        supervisor.verify_package(tmp_path, "economic-package-fixture")


def test_session5_terminal_repair_kind_is_accepted_by_successor_package(
    tmp_path: Path,
) -> None:
    output = _package(tmp_path)
    spec_path = output / "specification/campaign_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["r0_evidence_kind"] = (
        "r2_session5_terminal_reconciliation_r0_offline_repair"
    )
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    assert supervisor.verify_package(tmp_path, "economic-package-fixture")["passed"] is True


def test_identity_collision_is_rejected(tmp_path: Path) -> None:
    _package(tmp_path)
    with pytest.raises(FileExistsError):
        (tmp_path / "artifacts/okx_demo_multi_session_economic_soak/packages/economic-package-fixture").mkdir()


def test_successor_loader_binds_its_own_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _package(tmp_path)
    captured: dict[str, object] = {}

    def fake_loader(root: Path, package_id: str, **kwargs: object) -> object:
        captured["root"] = root
        captured["package_id"] = package_id
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(supervisor.legacy, "load_campaign_package", fake_loader)
    assert supervisor.load_campaign_package(tmp_path, "economic-package-fixture") is not None
    assert captured["package_id"] == "economic-package-fixture"
    assert captured["expected_protocol_id"] == supervisor.PROTOCOL_ID
    assert captured["expected_ready_status"] == supervisor.READY_STATUS


def test_r0_r1_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "artifacts/okx_demo_fill_restart_validation/preflight-fixture"
    (run / "preflight").mkdir(parents=True); (run / "predecessor").mkdir()
    (run / "preflight/preflight_result.json").write_text(json.dumps({"passed": True, "mutation_attempts": 0, "initial_snapshot": {"position_btc": 0.0, "open_orders": 0}}), encoding="utf-8")
    (run / "predecessor/offline_evidence_audit.json").write_text(json.dumps({"repair_id": "wrong"}), encoding="utf-8")
    monkeypatch.setattr(preparer, "verify_offline_evidence", lambda *_: {"passed": True, "evidence_kind": preparer.REPAIR_KIND})
    monkeypatch.setattr(preparer, "verify_preflight_evidence", lambda *_: {"passed": True})
    with pytest.raises(preparer.PrepareError, match="not eligible"):
        preparer.prepare(tmp_path, "expected", "preflight-fixture")
