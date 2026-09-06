"""Offline proofs for the Stage C interruption guard."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from okx_demo_adapter import DemoAdapterError
from okx_demo_r2_s02_s03_continuation import _reject_unfinished_prior_stage_c_session
from okx_demo_r2_stage_c_executor import (
    write_interruption_guard,
    write_post_flatten_terminal_failure,
    write_unterminated_stage_c_failure,
)


def test_guard_is_written_before_execution_and_denies_reuse(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    guard = write_interruption_guard(
        run_dir=run_dir,
        run_id="run-1",
        campaign_id="campaign-1",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION",
        slots=[{"session_id": "s02", "slot_index": 2}],
    )
    payload = json.loads(guard.read_text(encoding="utf-8"))
    assert payload["status"] == "R2_STAGE_C_IN_PROGRESS_OR_INTERRUPTED_NOT_ACCEPTABLE"
    assert payload["credential_reads_at_guard_write"] == 0
    assert payload["network_attempts_at_guard_write"] == 0
    assert payload["order_mutations_at_guard_write"] == 0
    assert payload["retry_authorized"] is False
    assert payload["identity_reuse_authorized"] is False


def test_unfinished_prior_guard_blocks_successor(tmp_path: Path) -> None:
    guard_dir = tmp_path / "artifacts" / "r2_stage_c_execution" / "run-2"
    guard_dir.mkdir(parents=True)
    write_interruption_guard(
        run_dir=guard_dir,
        run_id="run-2",
        campaign_id="campaign-2",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION",
        slots=[{"session_id": "s02", "slot_index": 2}],
    )
    slots = {"s02": {"slot_index": 2}, "s03": {"slot_index": 3}}
    with pytest.raises(DemoAdapterError, match="Prior Stage C session lacks terminal evidence"):
        _reject_unfinished_prior_stage_c_session(
            root=tmp_path, campaign_id="campaign-2", requested_session_id="s03", slots=slots,
        )


def test_post_flatten_failure_writes_immutable_terminal_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    state_path = run_dir / "state" / "S02_runtime_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"flatten_state":"SUBMITTED"}\n', encoding="utf-8")
    write_interruption_guard(
        run_dir=run_dir, run_id="run-3", campaign_id="campaign-3",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION",
        slots=[{"session_id": "s02", "slot_index": 2}],
    )
    audited = SimpleNamespace(
        normal_orders_created=2, flatten_orders_created=1, orders_cancelled=2,
        mutation_retry_attempts=0, live_endpoint_attempts=0,
    )
    terminal = write_post_flatten_terminal_failure(
        run_dir=run_dir, run_id="run-3", campaign_id="campaign-3", session_id="s02",
        phase="POST_FLATTEN_RECONCILIATION", state_path=state_path, audited_exchange=audited,
    )
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    assert payload["status"] == "R2_STAGE_C_EXECUTION_FAILED_NOT_ACCEPTABLE"
    assert payload["failure_phase"] == "POST_FLATTEN_RECONCILIATION"
    assert payload["terminal_account_authoritative"] is False
    evidence = json.loads((run_dir / "terminal_failure_evidence.json").read_text(encoding="utf-8"))
    assert evidence["flatten_dispatches"] == 1
    assert evidence["mutation_retries"] == 0
    assert evidence["local_state_sha256"]
    original = terminal.read_bytes()
    write_post_flatten_terminal_failure(
        run_dir=run_dir, run_id="other", campaign_id="other", session_id="other",
        phase="OTHER", state_path=state_path, audited_exchange=audited,
    )
    assert terminal.read_bytes() == original


def test_guard_blocks_same_failed_identity_and_successor(tmp_path: Path) -> None:
    guard_dir = tmp_path / "artifacts" / "r2_stage_c_execution" / "run-4"
    guard_dir.mkdir(parents=True)
    write_interruption_guard(
        run_dir=guard_dir, run_id="run-4", campaign_id="campaign-4",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION", slots=[{"session_id": "s02", "slot_index": 2}],
    )
    audited = SimpleNamespace(normal_orders_created=0, flatten_orders_created=1, orders_cancelled=0,
                              mutation_retry_attempts=0, live_endpoint_attempts=0)
    write_post_flatten_terminal_failure(
        run_dir=guard_dir, run_id="run-4", campaign_id="campaign-4", session_id="s02",
        phase="POST_FLATTEN_RECONCILIATION", state_path=guard_dir / "missing.json", audited_exchange=audited,
    )
    slots = {"s02": {"slot_index": 2}, "s03": {"slot_index": 3}}
    with pytest.raises(DemoAdapterError, match="identity is consumed"):
        _reject_unfinished_prior_stage_c_session(root=tmp_path, campaign_id="campaign-4", requested_session_id="s02", slots=slots)
    with pytest.raises(DemoAdapterError, match="lacks terminal evidence"):
        _reject_unfinished_prior_stage_c_session(root=tmp_path, campaign_id="campaign-4", requested_session_id="s03", slots=slots)


def test_unhandled_post_guard_exit_writes_lifecycle_failure_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-5"
    run_dir.mkdir()
    write_interruption_guard(
        run_dir=run_dir, run_id="run-5", campaign_id="campaign-5",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION", slots=[{"session_id": "s02", "slot_index": 2}],
    )
    state_path = run_dir / "state" / "S02_runtime_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"owned_open_orders":{"intent":"INTENT_PENDING"}}\n', encoding="utf-8")
    audited = SimpleNamespace(normal_orders_created=1, flatten_orders_created=0, orders_cancelled=0,
                              mutation_retry_attempts=0, live_endpoint_attempts=0)
    terminal = write_unterminated_stage_c_failure(
        run_dir=run_dir, run_id="run-5", campaign_id="campaign-5", session_ids=["s02"],
        audited_exchange=audited,
    )
    marker = json.loads(terminal.read_text(encoding="utf-8"))
    assert marker["failure_reason_code"] == "UNHANDLED_STAGE_C_LIFECYCLE_EXCEPTION_OR_INTERRUPTION"
    assert marker["terminal_account_authoritative"] is False
    assert marker["identity_reuse_authorized"] is False
    evidence = json.loads((run_dir / "terminal_failure_evidence.json").read_text(encoding="utf-8"))
    assert evidence["normal_create_dispatches"] == 1
    assert evidence["local_state_sha256"]


def test_successor_rejects_terminal_marker_without_exact_campaign_identity(tmp_path: Path) -> None:
    guard_dir = tmp_path / "artifacts" / "r2_stage_c_execution" / "run-6"
    guard_dir.mkdir(parents=True)
    write_interruption_guard(
        run_dir=guard_dir, run_id="run-6", campaign_id="campaign-6",
        execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION", slots=[{"session_id": "s02", "slot_index": 2}],
    )
    (guard_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json").write_text(
        json.dumps({"status": "R2_CURRENT_SINGLE_SESSION_TERMINAL_PASSED", "session_ids": ["s02"],
                    "interruption_guard_superseded_by_terminal": True}), encoding="utf-8"
    )
    (guard_dir / "completion_hashes.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DemoAdapterError, match="not admissible"):
        _reject_unfinished_prior_stage_c_session(
            root=tmp_path, campaign_id="campaign-6", requested_session_id="s03",
            slots={"s02": {"slot_index": 2}, "s03": {"slot_index": 3}},
        )
