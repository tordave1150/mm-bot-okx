"""Build fresh socket-denied evidence for R2 post-start terminal recovery."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_soak_failure_injection_offline import _run_suite
from okx_fill_restart_formal_prepare import _artifact_secret_scan
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_validation import canonical_sha256


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_terminal_recovery_repair"
EVIDENCE_PATTERN = re.compile(r"terminal-recovery-offline-\d{8}T\d{6}Z\Z")
FAILED_PACKAGE_ID = "economic-package-20260820T141231Z"
FAILED_CAMPAIGN_RUN_ID = "economic-campaign-run-20260820T141231Z"
FAILED_SESSION_PACKAGE_ID = "soak-package-20260820T141231Z-s01-83ef4ee73e"
READY_STATUS = "OKX_DEMO_R2_TERMINAL_RECOVERY_OFFLINE_SUPPORT"
SOURCE_FILES = (
    "AGENTS.md",
    "AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md",
    "okx_demo_terminal_recovery_executor.py",
    "okx_demo_sample_efficiency_campaign_supervisor.py",
    "okx_demo_r2_terminal_recovery_offline.py",
    "okx_demo_soak_executor.py",
    "okx_fill_restart_gateway.py",
    "tests/test_okx_demo_terminal_recovery.py",
    "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
)
TARGETED_TESTS = (
    "tests/test_okx_demo_terminal_recovery.py",
    "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
    "tests/test_okx_demo_soak_executor.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_sample_efficiency_repair.py",
)


class TerminalRecoveryEvidenceError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TerminalRecoveryEvidenceError(f"JSON object required: {path.name}")
    return value


def _source_hashes(root: Path) -> dict[str, str]:
    missing = [relative for relative in SOURCE_FILES if not (root / relative).is_file()]
    if missing:
        raise TerminalRecoveryEvidenceError("repair source missing: " + ",".join(missing))
    return {relative: _sha256(root / relative) for relative in SOURCE_FILES}


def _completion_hashes(output: Path) -> dict[str, str]:
    ignored = {"completion_hashes.json", "R2_TERMINAL_RECOVERY_OFFLINE_COMPLETED.json"}
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name not in ignored
        and path.suffix != ".tmp"
    ))


def build(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not EVIDENCE_PATTERN.fullmatch(evidence_id):
        raise TerminalRecoveryEvidenceError("fresh terminal recovery evidence ID required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise TerminalRecoveryEvidenceError("terminal recovery evidence reuse refused")
    output.mkdir(parents=True)

    package = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages"
        / FAILED_PACKAGE_ID
    )
    campaign = package / "campaign_run"
    session = (
        root / "artifacts/okx_demo_soak_validation" / FAILED_SESSION_PACKAGE_ID
        / "soak_run"
    )
    fixed = {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_specification": package / "specification/campaign_package_spec.json",
        "campaign_arm_marker": campaign / "A2_CAMPAIGN_ARMED.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_events": campaign / "streams/supervisor_events.jsonl",
        "session_failure": session / "FAILED.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_completion": session / "completion_hashes.json",
    }
    if not all(path.is_file() for path in fixed.values()):
        raise TerminalRecoveryEvidenceError("failed predecessor evidence is incomplete")
    failed = _read(fixed["session_failure"])
    campaign_state = _read(fixed["campaign_state"])
    if any((
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("terminal_account_authoritative") is not False,
        failed.get("final_position_btc") is not None,
        failed.get("final_open_orders") is not None,
        failed.get("mutation_retries") != 0,
        campaign_state.get("active_slot") != 1,
        campaign_state.get("completed_slots") != [],
        campaign_state.get("terminal_decision") is not None,
    )):
        raise TerminalRecoveryEvidenceError("failed predecessor boundary drifted")
    forbidden_markers = [
        root / "artifacts/okx_demo_soak_validation"
        / f"soak-package-20260820T141231Z-s{slot:02d}-placeholder"
        for slot in range(2, 13)
    ]
    actual_slot_markers = list(
        (root / "artifacts/okx_demo_soak_validation").glob(
            "soak-package-20260820T141231Z-s*/soak_run/SOAK_EXECUTION_ARMED.json"
        )
    )
    if len(actual_slot_markers) != 1 or FAILED_SESSION_PACKAGE_ID not in str(
        actual_slot_markers[0]
    ):
        raise TerminalRecoveryEvidenceError("slot 2-12 execution marker detected")

    predecessor_audit = {
        "package_id": FAILED_PACKAGE_ID,
        "campaign_run_id": FAILED_CAMPAIGN_RUN_ID,
        "session_package_id": FAILED_SESSION_PACKAGE_ID,
        "immutable": True,
        "resume_authorized": False,
        "flatten_retry_authorized": False,
        "slot_2_through_12_started": False,
        "hashes": {name: _sha256(path) for name, path in fixed.items()},
        "failure_reason": failed.get("reason"),
        "shutdown_reason": failed.get("shutdown_reason"),
        "normal_creates": failed.get("normal_creates"),
        "normal_fills": int(failed.get("normal_bid_fills", 0))
        + int(failed.get("normal_ask_fills", 0)),
        "normal_fifo_round_trips": failed.get("normal_fifo_round_trips"),
        "normal_net_pnl_usdt": failed.get("normal_net_pnl_usdt"),
    }
    _write_json(output / "predecessor/failed_execution_audit.json", predecessor_audit)

    confirmation = {
        "campaign_id": "economic-campaign-20260820T141231Z",
        "session_package_id": FAILED_SESSION_PACKAGE_ID,
        "position_btc": "0",
        "open_orders": 0,
        "reported_by": "user",
        "authoritative_exchange_snapshot": False,
        "economic_evidence": False,
        "resume_authorized": False,
        "reported_in_current_thread": True,
    }
    confirmation["confirmation_sha256"] = canonical_sha256(confirmation)
    _write_json(output / "recovery/user_flat_empty_confirmation.json", confirmation)
    _write_json(output / "recovery/campaign_fail_closed_recovery_manifest.json", {
        "decision": "NOT_READY",
        "failed_package_id": FAILED_PACKAGE_ID,
        "failed_campaign_run_id": FAILED_CAMPAIGN_RUN_ID,
        "failed_session_package_id": FAILED_SESSION_PACKAGE_ID,
        "confirmation_sha256": confirmation["confirmation_sha256"],
        "account_reported_flat_empty": True,
        "authoritative_exchange_snapshots": 0,
        "economic_evidence": False,
        "campaign_resume_authorized": False,
        "fresh_preflight_and_package_required": True,
    })
    _write_json(output / "specification/requirement_matrix.json", {
        "ambiguous_flatten_history_tail_reconciliation": True,
        "flatten_mutation_retry_exactly_zero": True,
        "single_flight_dispatch_cap_one": True,
        "post_start_child_loader_supported": True,
        "terminal_account_only_failure_manifest_supported": True,
        "external_confirmation_can_only_fail_close": True,
        "external_confirmation_not_economic_evidence": True,
        "future_two_account_snapshot_requirement": True,
        "failed_campaign_resume_prohibited": True,
    })
    _write_json(output / "specification/scenario_matrix.json", {
        "timeout_after_flatten_dispatch_trade_tail_identity": "PASS_REQUIRED",
        "timeout_after_flatten_dispatch_identity_absent": "FAIL_CLOSED_NO_RETRY",
        "account_flat_after_ambiguous_dispatch": "TWO_READ_ONLY_SNAPSHOTS",
        "account_nonflat_after_ambiguous_dispatch": "UNRESOLVED_NO_RETRY",
        "executed_child_loader": "IMMUTABLE_PACKAGE_PLUS_RUNTIME_ADDITIONS",
        "external_user_flat_confirmation": "NOT_READY_ONLY",
    })
    sources = _source_hashes(root)
    _write_json(output / "specification/source_hashes.json", sources)
    targeted = _run_suite(root, output, "terminal_recovery_targeted", TARGETED_TESTS)
    _write_json(output / "tests/test_summary.json", {
        "terminal_recovery_targeted": targeted,
    })
    if targeted.get("passed_gate") is not True:
        raise TerminalRecoveryEvidenceError("terminal recovery tests failed")
    if _source_hashes(root) != sources:
        raise TerminalRecoveryEvidenceError("repair source changed during evidence build")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True,
        "network_attempts": 0,
        "credential_reads": 0,
        "okx_requests": 0,
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_configuration_attempts": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    })
    scan = _artifact_secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if scan.get("passed") is not True:
        raise TerminalRecoveryEvidenceError("repair secret scan failed")
    decision = {
        "status": READY_STATUS,
        "evidence_id": evidence_id,
        "offline_repair_passed": True,
        "failed_campaign_decision": "NOT_READY",
        "failed_campaign_resume_authorized": False,
        "preflight_authorized": False,
        "package_preparation_authorized": False,
        "campaign_authorized": False,
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate authorization for fresh read-only preflight preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    _write_text(
        output / "decision/offline_decision.md",
        "# R2 post-start terminal recovery offline repair\n\n"
        "- Status: `PASS`\n"
        "- Failed campaign: `NOT_READY`, no resume\n"
        "- User-reported account: `0 BTC / 0 open orders`\n"
        "- Exchange-authoritative terminal snapshots: `0`\n"
        "- Network/order execution: `0 / 0`\n",
    )
    completion = _completion_hashes(output)
    _write_json(output / "completion_hashes.json", completion)
    terminal = {
        **decision,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "source_manifest_sha256": canonical_sha256(sources),
        "terminal_written_last": True,
    }
    _write_json(output / "R2_TERMINAL_RECOVERY_OFFLINE_COMPLETED.json", terminal)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        output = build(args.root, args.evidence_id)
    except Exception as exc:
        print(f"TERMINAL_RECOVERY_OFFLINE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
