"""Offline exact-chain admission tests for the current R2 Stage C continuation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_s02_s03_continuation import EXPECTED_CANDIDATE_FINGERPRINT, verify_current_stage_c_predecessors
from okx_demo_staged_validation import SOURCE_FILES, compute_candidate_fingerprint


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _chain(root: Path, omit_r1_create: bool = False) -> dict[str, str]:
    fp = compute_candidate_fingerprint(Path.cwd())["candidate_fingerprint"]
    ids = {"r0": "r0-new", "r1": "r1-new", "r2": "r2-new", "final": "final-new", "canary": "canary-new", "campaign": "campaign-new"}
    r1 = {"status": "R1_PREFLIGHT_PASSED", "r0_evidence_id": ids["r0"], "candidate_fingerprint": fp, "cancel_attempts": 0, "flatten_attempts": 0, "account_mutation_attempts": 0, "live_endpoint_attempts": 0}
    if not omit_r1_create:
        r1["create_attempts"] = 0
    base = root / "artifacts/r1_read_only_preflight_runs" / ids["r1"]
    _write(base / "R1_PREFLIGHT_PASSED.json", r1); _write(base / "completion_hashes.json", {})
    r2 = {"status": "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION", "r0_evidence_id": ids["r0"], "r1_run_id": ids["r1"], "r2_campaign_id": ids["campaign"], "candidate_fingerprint": fp, "credential_reads": 0, "network_attempts": 0, "create_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0}
    base = root / "artifacts/r2_economic_campaign_preparation" / ids["r2"]
    _write(base / "R2_PREPARATION_COMPLETED.json", r2); _write(base / "completion_hashes.json", {})
    _write(base / "r2_identity.json", {"campaign_id": ids["campaign"], "created_at_utc": datetime.now(timezone.utc).isoformat()})
    _write(base / "session_schedule.json", {"maximum_campaign_wall_ms": 21600000, "session_slots": [{"slot_index": 2, "session_id": "s2"}, {"slot_index": 3, "session_id": "s3"}]})
    final = {"status": "R2_FINAL_PREPARATION_PASSED", "r0_evidence_id": ids["r0"], "r1_run_id": ids["r1"], "r2_prep_ref": ids["r2"], "candidate_fingerprint": fp, "orders_created": 0, "orders_cancelled": 0, "flatten_attempts": 0, "account_mutations": 0, "live_endpoint_attempts": 0}
    base = root / "artifacts/r2_final_admission_preparation" / ids["final"]
    _write(base / "R2_FINAL_PREPARATION_COMPLETED.json", final); _write(base / "completion_hashes.json", {})
    canary = {"status": "R2_CANARY_PASSED", "canary_hard_checkpoint_passed": True, "r2_final_prep_ref": ids["final"], "r2_prep_ref": ids["r2"], "r1_run_id": ids["r1"], "r0_evidence_id": ids["r0"], "candidate_fingerprint": fp, "session_2_started": False, "account_mutations": 0, "live_endpoint_attempts": 0}
    base = root / "artifacts/r2_canary_runs" / ids["canary"]
    _write(base / "R2_CANARY_EXECUTION_COMPLETED.json", canary); _write(base / "completion_hashes.json", {})
    return ids


def test_current_candidate_covers_continuation_source() -> None:
    assert "okx_demo_r2_s02_s03_continuation.py" in SOURCE_FILES
    assert compute_candidate_fingerprint(Path.cwd())["candidate_fingerprint"] == EXPECTED_CANDIDATE_FINGERPRINT


def test_continuation_accepts_only_complete_exact_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "okx_demo_r2_s02_s03_continuation.compute_candidate_fingerprint",
        lambda root: {"candidate_fingerprint": EXPECTED_CANDIDATE_FINGERPRINT},
    )
    ids = _chain(tmp_path)
    _, slots, _ = verify_current_stage_c_predecessors(root=tmp_path, r0_evidence_id=ids["r0"], r1_run_id=ids["r1"], r2_prep_ref=ids["r2"], final_prep_ref=ids["final"], canary_run_id=ids["canary"], campaign_id=ids["campaign"])
    assert [slot["session_id"] for slot in slots] == ["s2", "s3"]
    ids = _chain(tmp_path / "missing", omit_r1_create=True)
    with pytest.raises(DemoAdapterError, match="R1 predecessor admission failed"):
        verify_current_stage_c_predecessors(root=tmp_path / "missing", r0_evidence_id=ids["r0"], r1_run_id=ids["r1"], r2_prep_ref=ids["r2"], final_prep_ref=ids["final"], canary_run_id=ids["canary"], campaign_id=ids["campaign"])
