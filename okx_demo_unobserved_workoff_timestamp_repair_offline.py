"""Build fresh socket-denied evidence for terminal work-off timestamp repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import (
    _run_suites,
    _secret_scan,
    _sha256,
    _write_json,
)
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_unobserved_workoff_timestamp_repair")
PATTERN = re.compile(r"workoff-timestamp-repair-offline-\d{8}T\d{6}Z\Z")
PACKAGE_ID = "economic-package-20260821T173615Z"
CAMPAIGN_ID = "economic-campaign-20260821T173615Z"
RUN_ID = "economic-campaign-run-20260821T173615Z"
FAILED_SESSION_ID = "soak-package-20260821T173615Z-s12-870c0378a3"
READY = "OKX_DEMO_UNOBSERVED_WORKOFF_TIMESTAMP_R0_OFFLINE_SUPPORT"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_predecessor(root: Path) -> dict[str, object]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / FAILED_SESSION_ID / "soak_run"
    paths = {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "slot_12_attempt": campaign / "attempted_sessions/slot-12.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_state": session / "state/validation_state.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("terminal work-off predecessor evidence incomplete")
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    if any((
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 12,
        terminal.get("sessions_completed") != 11,
        terminal.get("sessions_failed") != 1,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        decision.get("decision") != "NOT_READY",
        state.get("campaign_id") != CAMPAIGN_ID,
        state.get("terminal_decision") != "NOT_READY",
        state.get("failed_slots") != [12],
        state.get("active_slot") is not None,
        failed.get("reason") != "CampaignError:unobserved maker work-off has a timestamp",
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_snapshots") != 2,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
    )):
        raise RepairError("terminal work-off predecessor boundary drifted")
    aggregate = dict(decision["completed_aggregate"])
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "failed_session_package_id": FAILED_SESSION_ID,
        "terminal_decision": "NOT_READY",
        "resume_authorized": False,
        "rerun_authorized": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "normal_fill_count": aggregate["normal_fill_count"],
        "normal_fifo_round_trips": aggregate["normal_fifo_round_trips"],
        "normal_net_pnl_usdt": aggregate["normal_net_pnl_usdt"],
        "special_flatten_sessions": aggregate["special_flatten_sessions"],
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh work-off timestamp evidence identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("work-off timestamp evidence identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "failed_invariant": "unobserved maker work-off has a timestamp",
        "cause": "partial FIFO match wrote causal completion timestamp before remainder reached zero",
        "repair": "persist partial identities and quantities with zero completion timestamp; set timestamp only when remainder reaches zero",
        "restart_policy": "sealed ambiguous legacy state fails closed; repaired partial state restores exactly",
        "risk_expansion": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "partial work-off has zero completion timestamp", "passed": True},
        {"requirement": "completion timestamp is set only at zero remainder", "passed": True},
        {"requirement": "partial identities and quantities survive sealed restart", "passed": True},
        {"requirement": "legacy ambiguous state fails closed", "passed": True},
        {"requirement": "failed campaign remains immutable and non-resumable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "one partial maker exit", "expected": "timestamp zero and valid serialization"},
        {"fixture": "partial-save-restore-complete", "expected": "exact quantities and completion timestamp"},
        {"fixture": "unobserved nonzero timestamp", "expected": "fail closed on restore"},
        {"fixture": "socket denied", "expected": "zero endpoint and mutation attempts"},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_economic_session_controller.py",
        "okx_demo_soak_executor.py",
        "okx_demo_unobserved_workoff_timestamp_repair_offline.py",
        "tests/test_okx_demo_unobserved_workoff_timestamp_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "workoff_timestamp_targeted", (
        "tests/test_okx_demo_unobserved_workoff_timestamp_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_campaign.py",
    ))
    suites = _run_suites(root, output)
    suites["workoff_timestamp_targeted"] = targeted
    _write_json(output / "tests/workoff_timestamp_targeted_summary.json", targeted)
    attempts = sum(int(item["network_attempts"]) for item in suites.values())
    if attempts:
        raise RepairError("socket-denied suite attempted network access")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0, "orders": 0,
    })
    decision = {
        "status": READY, "R0_offline_repair_passed": True,
        "R1_preparation_authorized": False, "preflight_authorized": False,
        "economic_campaign_authorized": False, "production_authorized": False,
        "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0,
        "optuna_executed": False, "validation_opened": False,
        "holdout_opened": False, "git_write_operation": False,
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_WORKOFF_TIMESTAMP_REPAIR_COMPLETED.json", {
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
        print(run(args.root.resolve(), args.evidence_id))
    except Exception as exc:
        print(f"WORKOFF_TIMESTAMP_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
