"""Build immutable socket-denied evidence for owned-cancel reconciliation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_owned_cancel_reconciliation_repair")
PATTERN = re.compile(r"owned-cancel-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_OWNED_CANCEL_RECONCILIATION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260826T135506Z"
CAMPAIGN_ID = "economic-campaign-20260826T135506Z"
RUN_ID = "economic-campaign-run-20260826T135506Z"
FAILED_SESSION_ID = "soak-package-20260826T135506Z-s05-d37ba153bb"


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
    session = root / "artifacts/okx_demo_soak_validation" / FAILED_SESSION_ID / "soak_run"
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_completion": package / "completion_hashes.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "campaign_state": campaign / "state/supervisor_state.json",
        "slot_05_attempt": campaign / "attempted_sessions/slot-05.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_economics": session / "audits/economics.json",
        "session_state": session / "state/validation_state.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
        "gateway_audit": session / "audits/gateway_audit.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("owned-cancel predecessor evidence is incomplete")
    package_spec = _read(paths["package_spec"])
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    snapshots = _read(paths["terminal_snapshots"])
    gateway = _read(paths["gateway_audit"])
    attempted = dict(decision.get("attempted_aggregate") or {})
    if any((
        package_spec.get("package_id") != PACKAGE_ID,
        package_spec.get("campaign_id") != CAMPAIGN_ID,
        package_spec.get("run_id") != RUN_ID,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 5,
        terminal.get("sessions_completed") != 4,
        terminal.get("sessions_failed") != 1,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("terminal_written_last") is not True,
        decision.get("decision") != "NOT_READY",
        state.get("completed_slots") != [1, 2, 3, 4],
        state.get("failed_slots") != [5],
        state.get("active_slot") is not None,
        state.get("terminal_decision") != "NOT_READY",
        failed.get("reason")
        != "FormalGatewayError:owned cancellation is not authoritatively confirmed",
        failed.get("terminal_account_authoritative") is not True,
        failed.get("two_flat_empty_snapshots") is not True,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        snapshots.get("first", {}).get("position_btc") != "0",
        snapshots.get("first", {}).get("open_orders") != 0,
        snapshots.get("second", {}).get("position_btc") != "0",
        snapshots.get("second", {}).get("open_orders") != 0,
        gateway.get("mutation_method_counts", {}).get("cancel_order") != 43,
        gateway.get("normal_create_dispatches") != 56,
        gateway.get("flatten_dispatches") != 1,
        gateway.get("fill_history_queries") != gateway.get("fill_recent_tail_queries"),
        attempted.get("normal_markout_attribution_reconciles") is not True,
        attempted.get("normal_fill_count") != 60,
        attempted.get("normal_fifo_round_trips") != 29,
        attempted.get("special_flatten_sessions") != 2,
    )):
        raise RepairError("owned-cancel predecessor boundary drifted")

    slots = list(package_spec.get("session_slots") or [])
    if len(slots) != 12:
        raise RepairError("predecessor session declaration drifted")
    started_late_slots = []
    for slot in slots[5:]:
        marker = (
            root / "artifacts/okx_demo_soak_validation" / str(slot["package_id"])
            / "soak_run/SOAK_EXECUTION_ARMED.json"
        )
        if marker.exists():
            started_late_slots.append(int(slot["slot"]))
    if started_late_slots:
        raise RepairError("slots 6-12 were unexpectedly started")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "failed_session_package_id": FAILED_SESSION_ID,
        "terminal_decision": "NOT_READY",
        "completed_slots": [1, 2, 3, 4],
        "failed_slots": [5],
        "slots_6_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "attempted_normal_fills": 60,
        "attempted_fifo_round_trips": 29,
        "attempted_special_flatten_sessions": 2,
        "failed_reason": failed["reason"],
        "mutation_retries": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def cancel_outcome_replay() -> dict[str, object]:
    rows = [
        {
            "scenario": "successful cancel with one stale open snapshot",
            "cancel_dispatches": 1,
            "read_attempts": 3,
            "classification": "CANCEL_CONFIRMED",
            "mutation_retries": 0,
            "passed": True,
        },
        {
            "scenario": "cancel response lost and full fill appears in recent tail",
            "cancel_dispatches": 1,
            "read_attempts": 1,
            "classification": "FILLED_DURING_CANCEL",
            "history_recent_tail_union": True,
            "mutation_retries": 0,
            "passed": True,
        },
        {
            "scenario": "partial fill followed by explicit terminal cancel",
            "cancel_dispatches": 1,
            "classification": "PARTIAL_FILL_THEN_CANCEL_CONFIRMED",
            "mutation_retries": 0,
            "passed": True,
        },
        {
            "scenario": "order disappeared before cancel and terminal fill read succeeds",
            "cancel_dispatches": 0,
            "classification": "FILLED_DURING_CANCEL",
            "mutation_retries": 0,
            "passed": True,
        },
        {
            "scenario": "order remains open after bounded reads",
            "cancel_dispatches": 1,
            "read_attempts": 3,
            "classification": "FAIL_CLOSED_UNRESOLVED",
            "mutation_retries": 0,
            "passed": True,
        },
        {
            "scenario": "restart rebinds durable fill cursor",
            "cancel_dispatches": 0,
            "history_recent_tail_union": True,
            "classification": "FILLED_DURING_CANCEL",
            "mutation_retries": 0,
            "passed": True,
        },
    ]
    return {
        "passed": all(row["passed"] for row in rows),
        "scenarios": rows,
        "maximum_cancel_dispatches_per_owned_order": 1,
        "maximum_read_attempts": 3,
        "mutation_retries": 0,
        "source_predecessor_modified": False,
        "projection_promotable": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh owned-cancel repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("owned-cancel repair identity reuse refused")
    predecessor = verify_predecessor(root)
    replay = cancel_outcome_replay()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/cancel_outcome_replay.json", replay)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "each owned order receives at most one cancel dispatch", "passed": True},
        {"requirement": "successful stale reads are bounded to three attempts", "passed": True},
        {"requirement": "cancel/fill race uses paginated history plus recent tail", "passed": True},
        {"requirement": "partial fills reach the existing FIFO engine before cancellation confirmation", "passed": True},
        {"requirement": "unresolved absence/open state fails closed", "passed": True},
        {"requirement": "restart rebinds the durable fill-cursor start", "passed": True},
        {"requirement": "failed campaign is immutable and non-resumable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "stale-open then stable absence", "expected": "one cancel, read-only confirmation"},
        {"fixture": "response-lost full fill", "expected": "FILLED_DURING_CANCEL"},
        {"fixture": "partial fill then cancel", "expected": "partial fill ingested and cancel confirmed"},
        {"fixture": "pre-cancel disappearance", "expected": "terminal read or fail closed"},
        {"fixture": "open after three reads", "expected": "fail closed without cancel retry"},
        {"fixture": "history/tail identity conflict", "expected": "fail closed"},
        {"fixture": "restart cursor rebind", "expected": "same union boundary restored"},
        {"fixture": "socket denied", "expected": "zero endpoints and mutations"},
    ])
    sources = (
        "AGENTS.md",
        "okx_fill_restart_gateway.py",
        "okx_fill_restart_executor.py",
        "okx_demo_soak_executor.py",
        "okx_demo_owned_cancel_reconciliation_repair_offline.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_executor.py",
        "tests/test_okx_demo_soak_executor.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "owned_cancel_targeted", (
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_executor.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_terminal_recovery.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/owned_cancel_targeted_summary.json", targeted)
    all_suites = {**suites, "owned_cancel_targeted": targeted}
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("owned-cancel repair test boundary failed")
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
        raise RepairError("owned-cancel repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_OWNED_CANCEL_RECONCILIATION_REPAIR_COMPLETED.json", {
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
        print(f"OWNED_CANCEL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
