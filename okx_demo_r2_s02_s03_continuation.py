"""Offline preparation and admission verification for current R2 Sessions s02-s03."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from okx_demo_adapter import DemoAdapterError
from okx_demo_staged_validation import compute_candidate_fingerprint

ROOT = Path(__file__).resolve().parent
EXPECTED_CANDIDATE_FINGERPRINT = "1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf"


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
    if compute_candidate_fingerprint(root)["candidate_fingerprint"] != EXPECTED_CANDIDATE_FINGERPRINT:
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
    if r1.get("status") != "R1_PREFLIGHT_PASSED" or r1.get("r0_evidence_id") != r0_evidence_id:
        raise DemoAdapterError("R1 predecessor admission failed")
    if r2.get("status") != "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION":
        raise DemoAdapterError("R2 preparation is not eligible")
    if (r2.get("r0_evidence_id") != r0_evidence_id or r2.get("r1_run_id") != r1_run_id
            or r2.get("r2_campaign_id") != campaign_id):
        raise DemoAdapterError("R2 preparation identity mismatch")
    if (final.get("status") != "R2_FINAL_PREPARATION_PASSED"
            or final.get("r0_evidence_id") != r0_evidence_id
            or final.get("r1_run_id") != r1_run_id
            or final.get("r2_prep_ref") != r2_prep_ref):
        raise DemoAdapterError("Final-admission predecessor identity mismatch")
    if (canary.get("status") != "R2_CANARY_PASSED"
            or canary.get("canary_hard_checkpoint_passed") is not True
            or canary.get("r2_final_prep_ref") != final_prep_ref
            or canary.get("r2_prep_ref") != r2_prep_ref
            or canary.get("r1_run_id") != r1_run_id
            or canary.get("r0_evidence_id") != r0_evidence_id):
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
        session_id=session_id, arm_token=arm_token,
    )
    terminal = json.loads((root / "artifacts" / "r2_current_stage_c_preparation" / continuation_prep_ref /
                           "R2_CURRENT_STAGE_C_PREPARATION_COMPLETED.json").read_text(encoding="utf-8"))
    execution_slot = dict(slot)
    execution_slot["qualification_slot"] = execution_slot["slot_name"].upper()
    execution_slot["nonce"] = session_id.rsplit(":p0:", 1)[-1]
    execution_slot["expected_arm_token"] = arm_token
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
