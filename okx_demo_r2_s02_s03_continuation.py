"""Offline preparation and admission verification for current R2 Sessions s02-s03."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from okx_demo_adapter import DemoAdapterError
from okx_demo_staged_validation import compute_candidate_fingerprint

ROOT = Path(__file__).resolve().parent
EXPECTED_CANDIDATE_FINGERPRINT = "ef993bc42ffbb19cf1bfc94d3dcca32cd19909c9b0d9da73ab0a01ab0088ffb8"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _verify_hashes(directory: Path) -> None:
    manifest_path = directory / "completion_hashes.json"
    if not manifest_path.is_file():
        raise DemoAdapterError(f"Missing completion hashes: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise DemoAdapterError("Invalid completion hash manifest")
    for relative_name, expected in manifest.items():
        artifact = directory / relative_name
        if not artifact.is_file() or _hash(artifact) != expected:
            raise DemoAdapterError(f"Predecessor hash verification failed: {relative_name}")


def _completion_hashes(directory: Path, terminal_name: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", terminal_name}
    return {
        item.relative_to(directory).as_posix(): _hash(item)
        for item in sorted(directory.rglob("*"))
        if item.is_file() and item.name not in ignored and not item.name.endswith(".tmp")
    }


def _reject_unfinished_prior_stage_c_session(
    *, root: Path, campaign_id: str, requested_session_id: str, slots: Mapping[str, Mapping[str, Any]]
) -> None:
    """A guarded Stage C identity is never reusable; incomplete evidence blocks successors."""
    requested = slots.get(requested_session_id)
    if requested is None:
        return
    requested_index = int(requested.get("slot_index", 0))
    for guard_path in (root / "artifacts" / "r2_stage_c_execution").glob("*/R2_STAGE_C_INTERRUPTION_GUARD.json"):
        try:
            guard = json.loads(guard_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoAdapterError("Stage C interruption guard is unreadable") from exc
        guarded_sessions = guard.get("session_ids")
        if guard.get("campaign_id") != campaign_id or not isinstance(guarded_sessions, list):
            continue
        guarded_indexes = [int(slots[s]["slot_index"]) for s in guarded_sessions if s in slots]
        if not guarded_indexes:
            continue
        completed_path = guard_path.parent / "R2_STAGE_C_EXECUTION_COMPLETED.json"
        failed_path = guard_path.parent / "R2_STAGE_C_EXECUTION_FAILED.json"
        if requested_session_id in guarded_sessions:
            raise DemoAdapterError("Prior Stage C session identity is consumed; reuse admission blocked")
        if max(guarded_indexes) < requested_index and not completed_path.is_file():
            raise DemoAdapterError("Prior Stage C session lacks terminal evidence; successor admission blocked")
        if max(guarded_indexes) < requested_index and failed_path.is_file():
            raise DemoAdapterError("Prior Stage C session has terminal failure evidence; successor admission blocked")
        if max(guarded_indexes) < requested_index:
            try:
                completed = json.loads(completed_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DemoAdapterError("Prior Stage C terminal evidence is unreadable") from exc
            if any((
                completed.get("status") != "R2_CURRENT_SINGLE_SESSION_TERMINAL_PASSED",
                completed.get("campaign_id") != campaign_id,
                completed.get("session_ids") != guarded_sessions,
                completed.get("interruption_guard_superseded_by_terminal") is not True,
            )):
                raise DemoAdapterError("Prior Stage C terminal evidence is not admissible")
            _verify_hashes(guard_path.parent)


def verify_current_stage_c_predecessors(
    *,
    root: Path,
    r0_evidence_id: str,
    r1_run_id: str,
    r2_prep_ref: str,
    final_prep_ref: str,
    canary_run_id: str,
    campaign_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], datetime]:
    """Return exactly s02/s03 only after the complete local chain passes."""
    active_fingerprint = compute_candidate_fingerprint(root)["candidate_fingerprint"]
    if active_fingerprint != EXPECTED_CANDIDATE_FINGERPRINT:
        raise DemoAdapterError("Candidate fingerprint drift detected")

    r1_dir = root / "artifacts" / "r1_read_only_preflight_runs" / r1_run_id
    r2_dir = root / "artifacts" / "r2_economic_campaign_preparation" / r2_prep_ref
    final_dir = root / "artifacts" / "r2_final_admission_preparation" / final_prep_ref
    canary_dir = root / "artifacts" / "r2_canary_runs" / canary_run_id
    for directory in (r1_dir, r2_dir, final_dir, canary_dir):
        _verify_hashes(directory)

    r1 = json.loads((r1_dir / "R1_PREFLIGHT_PASSED.json").read_text(encoding="utf-8"))
    r2 = json.loads((r2_dir / "R2_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    final = json.loads((final_dir / "R2_FINAL_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    canary = json.loads((canary_dir / "R2_CANARY_EXECUTION_COMPLETED.json").read_text(encoding="utf-8"))
    if any((
        r1.get("status") != "R1_PREFLIGHT_PASSED",
        r1.get("r0_evidence_id") != r0_evidence_id,
        r1.get("candidate_fingerprint") != active_fingerprint,
        r1.get("create_attempts") != 0,
        r1.get("cancel_attempts") != 0,
        r1.get("flatten_attempts") != 0,
        r1.get("account_mutation_attempts") != 0,
        r1.get("live_endpoint_attempts") != 0,
    )):
        raise DemoAdapterError("R1 predecessor admission failed")
    if any((
        r2.get("status") != "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION",
        r2.get("candidate_fingerprint") != active_fingerprint,
        r2.get("credential_reads") != 0,
        r2.get("network_attempts") != 0,
        r2.get("create_attempts") != 0,
        r2.get("cancel_attempts") != 0,
        r2.get("flatten_attempts") != 0,
    )):
        raise DemoAdapterError("R2 preparation is not eligible")
    if (r2.get("r0_evidence_id") != r0_evidence_id or r2.get("r1_run_id") != r1_run_id
            or r2.get("r2_campaign_id") != campaign_id):
        raise DemoAdapterError("R2 preparation identity mismatch")
    if any((
        final.get("status") != "R2_FINAL_PREPARATION_PASSED",
        final.get("r0_evidence_id") != r0_evidence_id,
        final.get("r1_run_id") != r1_run_id,
        final.get("r2_prep_ref") != r2_prep_ref,
        final.get("candidate_fingerprint") != active_fingerprint,
        final.get("orders_created") != 0,
        final.get("orders_cancelled") != 0,
        final.get("flatten_attempts") != 0,
        final.get("account_mutations") != 0,
        final.get("live_endpoint_attempts") != 0,
    )):
        raise DemoAdapterError("Final-admission predecessor identity mismatch")
    if any((
        canary.get("status") != "R2_CANARY_PASSED",
        canary.get("canary_hard_checkpoint_passed") is not True,
        canary.get("r2_final_prep_ref") != final_prep_ref,
        canary.get("r2_prep_ref") != r2_prep_ref,
        canary.get("r1_run_id") != r1_run_id,
        canary.get("r0_evidence_id") != r0_evidence_id,
        canary.get("candidate_fingerprint") != active_fingerprint,
        canary.get("session_2_started") is not False,
        canary.get("account_mutations") != 0,
        canary.get("live_endpoint_attempts") != 0,
    )):
        raise DemoAdapterError("Canary checkpoint predecessor admission failed")

    identity = json.loads((r2_dir / "r2_identity.json").read_text(encoding="utf-8"))
    schedule = json.loads((r2_dir / "session_schedule.json").read_text(encoding="utf-8"))
    if identity.get("campaign_id") != campaign_id:
        raise DemoAdapterError("R2 campaign identity mismatch")
    slots = [slot for slot in schedule.get("session_slots", []) if slot.get("slot_index") in {2, 3}]
    if [slot.get("slot_index") for slot in slots] != [2, 3]:
        raise DemoAdapterError("Current campaign Sessions s02/s03 are unavailable")
    started_at = datetime.fromisoformat(identity["created_at_utc"].replace("Z", "+00:00"))
    deadline = started_at + timedelta(milliseconds=int(schedule["maximum_campaign_wall_ms"]))
    return canary, slots, deadline


def build_current_stage_c_continuation_package(
    *,
    root: Path = ROOT,
    stamp: str,
    r0_evidence_id: str,
    r1_run_id: str,
    r2_prep_ref: str,
    final_prep_ref: str,
    canary_run_id: str,
    campaign_id: str,
) -> Path:
    canary, slots, deadline = verify_current_stage_c_predecessors(
        root=root, r0_evidence_id=r0_evidence_id, r1_run_id=r1_run_id,
        r2_prep_ref=r2_prep_ref, final_prep_ref=final_prep_ref,
        canary_run_id=canary_run_id, campaign_id=campaign_id,
    )
    now = datetime.now(timezone.utc)
    remaining_ms = max(0, int((deadline - now).total_seconds() * 1000))
    package_id = f"r2-current-stage-c-prep-{stamp}"
    output = root / "artifacts" / "r2_current_stage_c_preparation" / package_id
    if output.exists():
        raise DemoAdapterError("Continuation package identity already exists")
    output.mkdir(parents=True)
    _write_json(output / "predecessor_manifest.json", {
        "r0_evidence_id": r0_evidence_id, "r1_run_id": r1_run_id,
        "r2_prep_ref": r2_prep_ref, "final_prep_ref": final_prep_ref,
        "canary_run_id": canary_run_id, "campaign_id": campaign_id,
        "canary_checkpoint_passed": canary["canary_hard_checkpoint_passed"],
        "candidate_fingerprint": EXPECTED_CANDIDATE_FINGERPRINT,
    })
    _write_json(output / "session_contract.json", {
        "execution_scope": "R2_CURRENT_STAGE_C_S02_S03",
        "sessions": slots,
        "predecessor_gate": "Verify hashes, IDs, Canary terminal evidence, and wall-time before credentials or transport.",
        "per_session_terminal_gate": {
            "position_btc": 0.0, "open_orders": 0, "reconciliation": True, "mutation_retries": 0,
        },
        "campaign_deadline_utc": deadline.isoformat(),
        "remaining_wall_ms_at_preparation": remaining_ms,
        "minimum_remaining_wall_ms_to_start_session": 1800000,
        "frozen_limits": {
            "max_normal_creates_per_session": 60, "max_campaign_normal_creates": 720,
            "maximum_inventory_btc": 0.01, "hard_loss_usdt": 75, "mutation_retries": 0,
        },
        "next_session_after_s03": "STOP_PENDING_SEPARATE_AUTHORIZATION",
    })
    _write_json(output / "offline_test_summary.json", {
        "socket_denied": True, "credential_reads": 0, "network_access": False,
        "orders_created": 0, "mutation_retries": 0,
    })
    hashes = _completion_hashes(output, "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json")
    _write_json(output / "completion_hashes.json", hashes)
    _write_json(output / "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json", {
        "status": "R2_CURRENT_STAGE_C_PREPARATION_PASSED",
        "package_id": package_id, "campaign_id": campaign_id,
        "r0_evidence_id": r0_evidence_id, "r1_run_id": r1_run_id,
        "r2_prep_ref": r2_prep_ref, "final_prep_ref": final_prep_ref,
        "canary_run_id": canary_run_id, "sessions": [slot["session_id"] for slot in slots],
        "completion_hashes_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "files_verified": len(hashes), "network_access": False, "credential_reads": 0,
        "orders_created": 0, "orders_cancelled": 0, "flatten_attempts": 0,
        "mutation_retries": 0, "production_authorized": False,
        "next_step": "R2_SESSION_2_REQUIRES_SEPARATE_EXPLICIT_AUTHORIZATION",
    })
    return output


def admit_current_stage_c_execution(
    *,
    root: Path,
    continuation_prep_ref: str,
    campaign_id: str,
    session_id: str,
    arm_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed before credentials or transport for either current Stage C slot."""
    package_dir = root / "artifacts" / "r2_current_stage_c_preparation" / continuation_prep_ref
    _verify_hashes(package_dir)
    terminal = json.loads((package_dir / "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    contract = json.loads((package_dir / "session_contract.json").read_text(encoding="utf-8"))
    if terminal.get("status") != "R2_CURRENT_STAGE_C_PREPARATION_PASSED":
        raise DemoAdapterError("Current Stage C preparation is not passed")
    if terminal.get("campaign_id") != campaign_id:
        raise DemoAdapterError("Current Stage C campaign identity mismatch")
    slots = {slot["session_id"]: slot for slot in contract.get("sessions", [])}
    slot = slots.get(session_id)
    if slot is None or slot.get("expected_session_arm_token") != arm_token:
        raise DemoAdapterError("Current Stage C session or arm token mismatch")
    _reject_unfinished_prior_stage_c_session(
        root=root, campaign_id=campaign_id, requested_session_id=session_id, slots=slots,
    )
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise DemoAdapterError("Current Stage C admission requires an aware UTC timestamp")
    deadline = datetime.fromisoformat(contract["campaign_deadline_utc"])
    required = timedelta(milliseconds=int(contract["minimum_remaining_wall_ms_to_start_session"]))
    if at + required > deadline:
        raise DemoAdapterError("Campaign wall-time is insufficient for a safe Stage C session")
    return slot


def execute_current_stage_c_session(
    *,
    root: Path,
    continuation_prep_ref: str,
    campaign_id: str,
    session_id: str,
    arm_token: str,
    admission_now: datetime | None = None,
    stamp: str | None = None,
    execution_stage: str = "ECONOMIC_QUALIFICATION",
    exchange: Any | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    passphrase: str | None = None,
    load_env_file: bool = True,
    warmup_ticks: int = 14,
    cycles_per_session: int | None = None,
    tick_interval_s: float = 0.5,
    resting_s: float = 1.0,
) -> Path:
    """Execute one separately authorized current Stage C slot after local admission."""
    slot = admit_current_stage_c_execution(
        root=root, continuation_prep_ref=continuation_prep_ref, campaign_id=campaign_id,
        session_id=session_id, arm_token=arm_token, now=admission_now,
    )
    terminal = json.loads((root / "artifacts" / "r2_current_stage_c_preparation" / continuation_prep_ref /
                           "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    execution_slot = dict(slot)
    execution_slot["qualification_slot"] = execution_slot["slot_name"].upper()
    execution_slot["nonce"] = session_id.rsplit(":p0:", 1)[-1]
    execution_slot["expected_arm_token"] = arm_token
    # Deterministic fixtures may inject an in-memory exchange. Real execution
    # must instead use a separate child worker so this parent can durably record
    # an abrupt worker exit without relying on the child's atexit hooks.
    if exchange is not None:
        from okx_demo_r2_stage_c_executor import execute_r2_stage_c
        return execute_r2_stage_c(
            root=root, stamp=stamp, campaign_id=campaign_id,
            execution_scope="R2_CURRENT_STAGE_C_SINGLE_SESSION", slot_schedule=[execution_slot],
            r0_closure_ref=terminal["r0_evidence_id"], r1_run_id=terminal["r1_run_id"],
            r2_canary_run_id=terminal["canary_run_id"], stage_c_prep_ref=continuation_prep_ref,
            execution_stage=execution_stage, exchange=exchange, api_key=api_key,
            api_secret=api_secret, passphrase=passphrase, load_env_file=load_env_file,
            warmup_ticks=warmup_ticks, cycles_per_session=cycles_per_session,
            tick_interval_s=tick_interval_s, resting_s=resting_s,
        )

    if any(value is not None for value in (api_key, api_secret, passphrase)):
        raise DemoAdapterError("Real Stage C worker reads credentials only inside its supervised child process")
    if cycles_per_session is not None and execution_stage == "ECONOMIC_QUALIFICATION":
        raise DemoAdapterError("Canonical economic qualification forbids a fixed cycle override")

    run_stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / "artifacts" / "r2_stage_c_execution" / f"r2-stage-c-run-{run_stamp}"
    if run_dir.exists():
        raise DemoAdapterError("Stage C run identity already exists")
    command = [
        sys.executable, str(root / "okx_demo_r2_stage_c_worker.py"),
        "--root", str(root), "--stamp", run_stamp, "--campaign-id", campaign_id,
        "--r0-evidence-id", str(terminal["r0_evidence_id"]), "--r1-run-id", str(terminal["r1_run_id"]),
        "--canary-run-id", str(terminal["canary_run_id"]), "--stage-c-prep-ref", continuation_prep_ref,
        "--execution-stage", execution_stage, "--slot-json", json.dumps(execution_slot, sort_keys=True),
        "--warmup-ticks", str(warmup_ticks), "--tick-interval-s", str(tick_interval_s),
        "--resting-s", str(resting_s),
    ]
    from okx_demo_r2_stage_c_supervisor import supervise_worker
    exit_code = supervise_worker(
        run_dir=run_dir, command=command, campaign_id=campaign_id, session_id=session_id,
    )
    failed = run_dir / "R2_STAGE_C_EXECUTION_FAILED.json"
    completed = run_dir / "R2_STAGE_C_EXECUTION_COMPLETED.json"
    if exit_code != 0 or failed.is_file() or not completed.is_file():
        raise DemoAdapterError("Supervised Stage C worker ended without admissible terminal success")
    return run_dir
