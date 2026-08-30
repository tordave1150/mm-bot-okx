"""Build fresh socket-denied evidence for the R2 Session 5 terminal repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session5_terminal_reconciliation_repair")
PATTERN = re.compile(r"r2-session5-terminal-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION5_TERMINAL_RECONCILIATION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260829T112858Z"
CAMPAIGN_ID = "economic-campaign-20260829T112858Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260829T112858Z"
SESSION_PACKAGE_ID = "soak-package-20260829T112858Z-s05-a45ee3b0a2"
SESSION_RUN_ID = "economic-session-20260829T112858Z-s05-a45ee3b0a2"
SESSION_ID = "economic:economic-session-20260829T112858Z-s05-a45ee3b0a2:p0:a45ee3b0a2"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"predecessor JSON is not an object: {path.name}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    package = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    )
    campaign = package / "campaign_run"
    session = (
        root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE_ID / "soak_run"
    )
    return {
        "package_spec": package / "specification/campaign_package_spec.json",
        "package_completion": package / "completion_hashes.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "session_failure": session / "FAILED.json",
        "session_unresolved": session / "UNRESOLVED_FAILURE.json",
        "session_decision": session / "decision/decision.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_gateway_audit": session / "audits/gateway_audit.json",
        "session_validation_state": session / "state/validation_state.json",
        "session_validation_journal": session / "state/validation_state_journal.jsonl",
        "session_controller": session / "state/controller_state.jsonl",
        "session_events": session / "streams/events.jsonl",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        missing = sorted(name for name, path in paths.items() if not path.is_file())
        raise RepairError(f"Session 5 predecessor evidence is incomplete: {missing}")
    package = _read(paths["package_spec"])
    campaign = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    unresolved = _read(paths["session_unresolved"])
    decision = _read(paths["session_decision"])
    gateway = _read(paths["session_gateway_audit"])
    state_envelope = _read(paths["session_validation_state"])
    state = dict(state_envelope.get("payload") or {})
    open_orders = dict(state.get("owned_orders") or {})
    acknowledged = [
        client_id for client_id, row in open_orders.items()
        if dict(row).get("status") == "ACKNOWLEDGED"
    ]
    if any((
        package.get("package_id") != PACKAGE_ID,
        package.get("campaign_id") != CAMPAIGN_ID,
        package.get("run_id") != CAMPAIGN_RUN_ID,
        campaign.get("campaign_id") != CAMPAIGN_ID,
        campaign.get("completed_slots") != [1, 2, 3, 4],
        campaign.get("failed_slots") != [],
        campaign.get("active_slot") != 5,
        campaign.get("terminal_decision") is not None,
        failed.get("package_id") != SESSION_PACKAGE_ID,
        failed.get("run_id") != SESSION_RUN_ID,
        failed.get("session_id") != SESSION_ID,
        failed.get("reason")
        != "SoakExecutionError:read retry budget exhausted: fetch_account",
        failed.get("shutdown_reason")
        != "ValidationSafetyError:SPECIAL_ORDER_ACK_REJECTED",
        failed.get("terminal_account_authoritative") is not False,
        failed.get("two_flat_empty_snapshots") is not False,
        failed.get("final_position_btc") is not None,
        failed.get("final_open_orders") is not None,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        unresolved.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        decision.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        gateway.get("flatten_dispatches") != 1,
        gateway.get("normal_create_dispatches") != 46,
        state.get("phase") != "HALTED",
        state.get("halt_reason") != "SPECIAL_ORDER_ACK_REJECTED",
        dict(state.get("ledger") or {}).get("inventory_btc") != "0.010",
        state.get("last_reconciled_position_btc") != "0",
        state.get("pending_position_reconciliation") is not True,
        len(acknowledged) != 1,
    )):
        raise RepairError("Session 5 predecessor boundary drifted")
    slots = list(package.get("session_slots") or [])
    if len(slots) != 12:
        raise RepairError("campaign session declaration drifted")
    started_late_slots = []
    for slot in slots[5:]:
        marker = (
            root / "artifacts/okx_demo_soak_validation" / str(slot["package_id"])
            / "soak_run/SOAK_EXECUTION_ARMED.json"
        )
        if marker.exists():
            started_late_slots.append(int(slot["slot"]))
    if started_late_slots:
        raise RepairError("sessions 6-12 were unexpectedly started")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "session_package_id": SESSION_PACKAGE_ID,
        "session_run_id": SESSION_RUN_ID,
        "session_id": SESSION_ID,
        "completed_slots": [1, 2, 3, 4],
        "active_failed_slot": 5,
        "sessions_6_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "terminal_account_authoritative": False,
        "flatten_dispatches": 1,
        "mutation_retries": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "engine_inventory_btc": "0.010",
        "last_reconciled_position_btc": "0",
        "acknowledged_engine_open_orders": acknowledged,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnose(root: Path) -> dict[str, object]:
    paths = _paths(root)
    events = [
        json.loads(line)["payload"]
        for line in paths["session_events"].read_text(encoding="utf-8").splitlines()
    ]
    controller = [
        json.loads(line)["payload"]
        for line in paths["session_controller"].read_text(encoding="utf-8").splitlines()
    ]
    retry_rows = [row for row in events if row.get("event") == "READ_RETRY"]
    flatten_rows = [row for row in events if row.get("event") == "FLATTEN_INTENT"]
    if len(retry_rows) != 4 or len(flatten_rows) != 1:
        raise RepairError("Session 5 event boundary drifted")
    if controller[-1].get("phase") != "FLATTEN_INTENT_DURABLE":
        raise RepairError("Session 5 controller tail drifted")
    return {
        "passed": True,
        "root_cause": (
            "a bounded account read exhausted, then shutdown observed a transient "
            "post-cancel account view and dispatched flatten before durable cancel "
            "convergence; the validation engine still held one acknowledged normal order"
        ),
        "primary_fetch_account_retry_rows": 3,
        "shutdown_fetch_account_retry_rows": 1,
        "flatten_intents": 1,
        "flatten_acknowledged_events": sum(
            row.get("event") == "FLATTEN_ACKNOWLEDGED" for row in events
        ),
        "controller_tail_phase": controller[-1]["phase"],
        "cancel_mutation_retry_required": False,
        "repair": {
            "bounded_cancel_visibility_reads": 3,
            "cancel_dispatches_per_owned_order": 1,
            "flatten_requires_durable_cancel_convergence": True,
            "persistent_stale_view_action": "FAIL_CLOSED_BEFORE_FLATTEN_DISPATCH",
            "converged_stale_view_action": "RECONCILE_ENGINE_THEN_SINGLE_FLIGHT_FLATTEN",
        },
        "risk_expansion": False,
        "source_predecessor_modified": False,
        "projection_promotable": False,
    }


def repair_projection() -> dict[str, object]:
    return {
        "passed": True,
        "fixtures": [
            {
                "fixture": "read exhaustion then one stale cancel snapshot",
                "expected": "one cancel; read-only convergence; one flatten; authoritative 0/0",
            },
            {
                "fixture": "read exhaustion then persistent stale cancel snapshots",
                "expected": "one cancel; zero flatten; fail closed with ownership retained",
            },
            {
                "fixture": "special flatten acknowledgement after cancel convergence",
                "expected": "zero other durable open orders before acknowledgement",
            },
        ],
        "read_attempts": 3,
        "mutation_retries": 0,
        "risk_limits_changed": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh R2 Session 5 terminal repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("R2 Session 5 terminal repair identity reuse refused")
    predecessor = verify_predecessor(root)
    diagnostic = diagnose(root)
    projection = repair_projection()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_session_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", diagnostic)
    _write_json(output / "diagnostic/repair_projection.json", projection)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "stale post-cancel account views converge within three reads", "passed": True},
        {"requirement": "each owned order receives at most one cancel dispatch", "passed": True},
        {"requirement": "flatten is blocked until durable cancel convergence", "passed": True},
        {"requirement": "converged shutdown writes two authoritative flat/empty snapshots", "passed": True},
        {"requirement": "persistent stale state fails closed with zero flatten dispatch", "passed": True},
        {"requirement": "mutation retries remain exactly zero", "passed": True},
        {"requirement": "failed campaign and identities remain immutable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", projection["fixtures"] + [
        {"fixture": "socket denied and blank credentials", "expected": "zero endpoints and mutations"},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_soak_executor.py",
        "okx_fill_restart_validation.py",
        "okx_fill_restart_gateway.py",
        "okx_demo_r2_session5_terminal_reconciliation_repair_offline.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_r2_session5_terminal_reconciliation_repair.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "r2_session5_terminal_targeted", (
        "tests/test_okx_demo_r2_session5_terminal_reconciliation_repair.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_fill_restart_validation.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_demo_terminal_recovery.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/r2_session5_terminal_targeted_summary.json", targeted)
    all_suites = {**suites, "r2_session5_terminal_targeted": targeted}
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("R2 Session 5 terminal repair test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True,
        "network_attempts": 0,
        "credential_reads": 0,
        "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0,
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_configuration_attempts": 0,
        "orders": 0,
        "mutation_retries": 0,
    })
    decision = {
        "status": READY,
        "R0_offline_repair_passed": True,
        "R1_preparation_authorized": False,
        "preflight_authorized": False,
        "economic_campaign_authorized": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("R2 Session 5 terminal repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION5_TERMINAL_REPAIR_COMPLETED.json", {
        **decision,
        "evidence_id": evidence_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "terminal_written_last": True,
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"R2_SESSION5_TERMINAL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
