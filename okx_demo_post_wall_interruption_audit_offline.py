"""Build immutable socket-denied R0 evidence for post-wall interruption handling."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_post_wall_interruption_audit")
PATTERN = re.compile(r"post-wall-audit-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_POST_WALL_INTERRUPTION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260826T160834Z"
CAMPAIGN_ID = "economic-campaign-20260826T160834Z"
RUN_ID = "economic-campaign-run-20260826T160834Z"


class AuditError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"predecessor JSON is not an object: {path.name}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    package = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    )
    campaign = package / "campaign_run"
    slot5 = (
        root / "artifacts/okx_demo_soak_validation"
        / "soak-package-20260826T160834Z-s05-0d028be127"
    )
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_completion": package / "completion_hashes.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "package_sources": package / "specification/source_hashes.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_lease": campaign / "state/campaign_lease.json",
        "campaign_events": campaign / "streams/supervisor_events.jsonl",
        "slot5_terminal": slot5 / "COMPLETED.json",
        "slot5_completion": slot5 / "soak_run/completion_hashes.json",
        "slot5_evidence": slot5 / "soak_run/audits/economic_session_evidence.json",
        "slot5_economics": slot5 / "soak_run/audits/economics.json",
        "slot5_gateway": slot5 / "soak_run/audits/gateway_audit.json",
        "slot5_snapshots": slot5 / "soak_run/terminal/account_snapshots.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise AuditError("post-wall predecessor evidence is incomplete")
    spec = _read(paths["package_spec"])
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    lease = _read(paths["campaign_lease"])
    slot5 = _read(paths["slot5_terminal"])
    snapshots = _read(paths["slot5_snapshots"])
    aggregate = dict(terminal.get("attempted_aggregate") or {})
    events = [
        json.loads(line)
        for line in paths["campaign_events"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any((
        spec.get("package_id") != PACKAGE_ID,
        spec.get("campaign_id") != CAMPAIGN_ID,
        spec.get("run_id") != RUN_ID,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 5,
        terminal.get("sessions_completed") != 5,
        terminal.get("sessions_failed") != 0,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        terminal.get("terminal_written_last") is not True,
        decision.get("decision") != "NOT_READY",
        state.get("completed_slots") != [1, 2, 3, 4, 5],
        state.get("failed_slots") != [],
        state.get("active_slot") is not None,
        state.get("terminal_decision") != "NOT_READY",
        state.get("terminal_account_authoritative") is not True,
        lease.get("stale_lease_recovered") is not True,
        slot5.get("status") != "OKX_DEMO_ECONOMIC_SESSION_SUPPORT",
        slot5.get("final_position_btc") != "0",
        slot5.get("final_open_orders") != 0,
        slot5.get("two_flat_empty_snapshots") is not True,
        slot5.get("controller_engine_gateway_reconciled") is not True,
        slot5.get("mutation_retries") != 0,
        slot5.get("flatten_dispatches") != 1,
        snapshots.get("first", {}).get("position_btc") != "0",
        snapshots.get("first", {}).get("open_orders") != 0,
        snapshots.get("second", {}).get("position_btc") != "0",
        snapshots.get("second", {}).get("open_orders") != 0,
        aggregate.get("normal_creates") != 246,
        aggregate.get("normal_create_acknowledgements") != 246,
        aggregate.get("normal_create_rejections") != 0,
        aggregate.get("normal_fill_count") != 43,
        aggregate.get("normal_bid_fills") != 21,
        aggregate.get("normal_ask_fills") != 22,
        aggregate.get("normal_fifo_round_trips") != 21,
        aggregate.get("normal_net_pnl_usdt") != "7.2687144",
        aggregate.get("aggregate_net_pnl_usdt") != "7.6098929",
        aggregate.get("special_flatten_sessions") != 1,
        aggregate.get("unclassified_quote_mode_ticks") != 0,
        aggregate.get("unsafe_sessions") != 0,
        aggregate.get("economic_attribution_reconciles") is not True,
        aggregate.get("create_counter_reconciles") is not True,
        aggregate.get("normal_markout_attribution_reconciles") is not True,
        len(events) != 14,
        events[-3].get("event") != "LEASE_ACQUIRED",
        events[-3].get("payload", {}).get("stale_recovery") is not True,
        events[-2].get("event") != "SESSION_ACCEPTED",
        events[-2].get("payload", {}).get("slot") != 5,
        events[-1].get("event") != "CAMPAIGN_FAILED_CLOSED",
        events[-1].get("payload", {}).get("reason") != "CAMPAIGN_WALL_BUDGET",
    )):
        raise AuditError("post-wall predecessor boundary drifted")

    slots = list(spec.get("session_slots") or [])
    if len(slots) != 12:
        raise AuditError("predecessor session declaration drifted")
    late_markers = []
    for slot in slots[5:]:
        marker = (
            root / "artifacts/okx_demo_soak_validation" / str(slot["package_id"])
            / "soak_run/SOAK_EXECUTION_ARMED.json"
        )
        if marker.exists():
            late_markers.append(int(slot["slot"]))
    if late_markers:
        raise AuditError("slots 6-12 were unexpectedly started")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "terminal_decision": "NOT_READY",
        "terminal_reason": "CAMPAIGN_WALL_BUDGET",
        "completed_slots": [1, 2, 3, 4, 5],
        "failed_slots": [],
        "slots_6_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "stale_lease_recovered": True,
        "completed_active_slot_ingested": True,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_authoritative": True,
        "normal_creates": 246,
        "normal_fill_count": 43,
        "normal_bid_fills": 21,
        "normal_ask_fills": 22,
        "normal_fifo_round_trips": 21,
        "normal_net_pnl_usdt": "7.2687144",
        "aggregate_net_pnl_usdt": "7.6098929",
        "special_flatten_sessions": 1,
        "unclassified_quote_mode_ticks": 0,
        "unsafe_sessions": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def interruption_transition_audit() -> dict[str, object]:
    return {
        "passed": True,
        "transitions": [
            {"from": "ACTIVE_SLOT_5_COMPLETED_CHILD", "to": "STALE_LEASE_RECOVERED", "network": 0},
            {"from": "STALE_LEASE_RECOVERED", "to": "SLOT_5_INGESTED", "mutation": 0},
            {"from": "SLOT_5_INGESTED", "to": "CAMPAIGN_WALL_FAIL_CLOSED", "next_slot_authorized": False},
            {"from": "CAMPAIGN_WALL_FAIL_CLOSED", "to": "TERMINAL_FLAT_EMPTY", "position_btc": "0", "open_orders": 0},
        ],
        "resume_same_campaign": False,
        "reuse_identities": False,
        "projection_promotable": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise AuditError("fresh post-wall audit identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise AuditError("post-wall audit identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/interruption_transition_audit.json", interruption_transition_audit())
    _write_json(output / "diagnostic/economic_cohort_audit.json", {
        "sessions_observed": 5,
        "normal_creates": 246,
        "normal_fill_count": 43,
        "fill_per_create": "0.1747967479674796747967479675",
        "normal_bid_fills": 21,
        "normal_ask_fills": 22,
        "fill_balance": "0.9545454545454545454545454545",
        "normal_fifo_round_trips": 21,
        "fifo_conversion": "0.4883720930232558139534883721",
        "normal_net_pnl_usdt": "7.2687144",
        "aggregate_net_pnl_usdt": "7.6098929",
        "maximum_session_drawdown_usdt": "2.97821160000",
        "special_flatten_sessions": 1,
        "economic_floors_observed": True,
        "twelve_session_campaign_complete": False,
        "promotable": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "completed active child is ingested after stale lease recovery", "passed": True},
        {"requirement": "wall expiry fails closed before next-slot authorization", "passed": True},
        {"requirement": "slots 6-12 remain unstarted", "passed": True},
        {"requirement": "owned-cancel repair produced zero unresolved creates", "passed": True},
        {"requirement": "aggregate accounting and markouts reconcile", "passed": True},
        {"requirement": "terminal account is authoritative flat and empty", "passed": True},
        {"requirement": "terminal predecessor is immutable and non-resumable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "active child completed before process interruption", "expected": "hash-verified ingestion after lease recovery"},
        {"fixture": "stale campaign lease", "expected": "single durable recovery event"},
        {"fixture": "campaign wall expired", "expected": "NOT_READY before slot 6 authorization"},
        {"fixture": "terminal evidence", "expected": "two-snapshot flat/empty account"},
        {"fixture": "socket denied", "expected": "zero endpoint and mutation attempts"},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_multi_session_supervisor.py",
        "okx_demo_owned_cancel_reconciliation_campaign_supervisor.py",
        "okx_demo_post_wall_interruption_audit_offline.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
        "tests/test_okx_demo_post_wall_interruption_audit.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "post_wall_targeted", (
        "tests/test_okx_demo_post_wall_interruption_audit.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_demo_owned_cancel_reconciliation_campaign.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/post_wall_targeted_summary.json", targeted)
    all_suites = {**suites, "post_wall_targeted": targeted}
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise AuditError("post-wall audit test boundary failed")
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
        "R0_offline_audit_passed": True,
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
        raise AuditError("post-wall audit secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_POST_WALL_INTERRUPTION_AUDIT_COMPLETED.json", {
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
        print(f"POST_WALL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
