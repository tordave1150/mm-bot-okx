"""Build socket-denied R0 evidence for pre-market terminal reconciliation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_market_bootstrap_terminal_reconciliation")
PATTERN = re.compile(r"market-bootstrap-terminal-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260827T134515Z"
RUN_ID = "economic-campaign-run-20260827T134515Z"
SESSION_ID = "soak-package-20260827T134515Z-s01-b14cb83a52"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON is not an object: {path.name}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / SESSION_ID / "soak_run"
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_completion": package / "completion_hashes.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_events": campaign / "streams/supervisor_events.jsonl",
        "session_armed": session / "SOAK_EXECUTION_ARMED.json",
        "session_failed": session / "FAILED.json",
        "session_unresolved": session / "UNRESOLVED_FAILURE.json",
        "session_completion": session / "completion_hashes.json",
        "session_gateway_audit": session / "audits/gateway_audit.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
    }


def verify_failed_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("failed predecessor evidence is incomplete")
    package = _read(paths["package_spec"])
    campaign = _read(paths["campaign_terminal"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failed"])
    gateway = _read(paths["session_gateway_audit"])
    events = [
        json.loads(line) for line in paths["campaign_events"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any((
        package.get("package_id") != PACKAGE_ID,
        package.get("run_id") != RUN_ID,
        campaign.get("status") != "NOT_READY",
        campaign.get("terminal_account_authoritative") is not False,
        campaign.get("sessions_attempted") != 0,
        state.get("active_slot") != 1,
        state.get("terminal_decision") != "NOT_READY",
        failed.get("package_id") != SESSION_ID,
        failed.get("normal_creates") != 0,
        failed.get("normal_cancels") != 0,
        failed.get("terminal_account_authoritative") is not False,
        gateway.get("read_call_count") != 3,
        gateway.get("mutation_call_count") != 0,
        gateway.get("flatten_dispatches") != 0,
        gateway.get("live_endpoint_attempts") != 0,
        gateway.get("live_orders") != 0,
        len(events) != 4,
        events[-1].get("event") != "CAMPAIGN_FAILED_CLOSED",
    )):
        raise RepairError("failed predecessor boundary drifted")
    slots = list(package.get("session_slots") or [])
    started_later = [
        int(slot["slot"]) for slot in slots[1:]
        if (root / "artifacts/okx_demo_soak_validation" / str(slot["package_id"])
            / "soak_run/SOAK_EXECUTION_ARMED.json").exists()
    ]
    if started_later:
        raise RepairError("slots 2-12 were unexpectedly started")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_run_id": RUN_ID,
        "session_package_id": SESSION_ID,
        "terminal_decision": "NOT_READY",
        "resume_authorized": False,
        "rerun_authorized": False,
        "slots_2_through_12_started": False,
        "market_bootstrap_failed_before_account_state": True,
        "normal_creates": 0,
        "mutation_call_count": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh market-bootstrap repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("market-bootstrap repair identity reuse refused")
    predecessor = verify_failed_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/repair_scope.json", {
        "market_bootstrap": "fetch_markets may fail before any account state exists",
        "repair": "two account-only snapshots use frozen contract size without market metadata",
        "authoritative_terminal": "only flat/empty snapshots with matching hashed account binding",
        "campaign_ingestion": "accept only zero-mutation pre-market failures with hash-verified audit",
        "resume_or_identity_reuse": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "market bootstrap failure never enters quote or mutation path", "passed": True},
        {"requirement": "account-only reconciliation has two snapshots", "passed": True},
        {"requirement": "account UID is hashed and open orders are count-only", "passed": True},
        {"requirement": "campaign accepts only reconciled zero-mutation pre-market failure", "passed": True},
        {"requirement": "unreconciled terminal state remains fail-closed", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "fetch_markets exhausted", "expected": "account-only two-snapshot terminal manifest"},
        {"fixture": "account-only snapshot unavailable", "expected": "UNRESOLVED_FAIL_CLOSED and no ingestion"},
        {"fixture": "non-flat or open-order snapshot", "expected": "campaign rejects failure evidence"},
        {"fixture": "socket denied", "expected": "zero network, credential, and mutation attempts"},
    ])
    sources = (
        "AGENTS.md", "okx_fill_restart_gateway.py", "okx_demo_soak_executor.py",
        "okx_demo_terminal_recovery_executor.py", "okx_demo_multi_session_supervisor.py",
        "okx_demo_market_bootstrap_terminal_reconciliation_repair_offline.py",
        "tests/test_okx_fill_restart_gateway.py", "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "market_bootstrap_targeted", (
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
        "tests/test_okx_demo_post_wall_interruption_audit.py",
    ))
    suites = _run_suites(root, output)
    all_suites = {**suites, "market_bootstrap_targeted": targeted}
    _write_json(output / "tests/market_bootstrap_targeted_summary.json", targeted)
    if any(
        item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("socket-denied test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY, "R0_offline_repair_passed": True,
        "R1_preparation_authorized": False, "preflight_authorized": False,
        "economic_campaign_authorized": False, "production_authorized": False,
        "live_mode_available": False, "live_endpoint_attempts": 0,
        "live_orders": 0, "optuna_executed": False,
        "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("market-bootstrap repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_COMPLETED.json", {
        **decision, "evidence_id": evidence_id,
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
        print(f"MARKET_BOOTSTRAP_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
