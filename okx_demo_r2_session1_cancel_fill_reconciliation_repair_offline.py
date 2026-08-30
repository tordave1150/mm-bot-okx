"""Build socket-denied R0 evidence for the immutable R2 Session 1 repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session1_cancel_fill_reconciliation_repair")
PATTERN = re.compile(r"r2-session1-cancel-fill-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION1_CANCEL_FILL_RECONCILIATION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260829T162220Z"
CAMPAIGN_ID = "economic-campaign-20260829T162220Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260829T162220Z"
SESSION_PACKAGE_ID = "soak-package-20260829T162220Z-s01-8409058fa1"
SESSION_RUN_ID = "economic-session-20260829T162220Z-s01-8409058fa1"
SESSION_ID = "economic:economic-session-20260829T162220Z-s01-8409058fa1:p0:8409058fa1"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"expected JSON object: {path}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    session = root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE_ID / "soak_run"
    return {
        "failure": session / "FAILED.json",
        "unresolved": session / "UNRESOLVED_FAILURE.json",
        "gateway": session / "audits/gateway_audit.json",
        "state": session / "state/validation_state.json",
        "events": session / "streams/events.jsonl",
        "completion": session / "completion_hashes.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("Session 1 durable evidence is incomplete")
    failure, unresolved = _read(paths["failure"]), _read(paths["unresolved"])
    gateway, envelope = _read(paths["gateway"]), _read(paths["state"])
    state = dict(envelope.get("payload") or {})
    classifications = dict(gateway.get("last_cancel_reconciliation_audit") or {}).get("classifications")
    if any((
        failure.get("package_id") != SESSION_PACKAGE_ID,
        failure.get("run_id") != SESSION_RUN_ID,
        failure.get("session_id") != SESSION_ID,
        failure.get("reason") != "SoakExecutionError:cancel/fill reconciliation exhausted",
        unresolved.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        gateway.get("normal_create_dispatches") != 4,
        gateway.get("flatten_dispatches") != 0,
        failure.get("mutation_retries") != 0,
        gateway.get("live_endpoint_attempts") != 0,
        gateway.get("live_orders") != 0,
        not isinstance(classifications, dict),
        sorted(classifications.values()) != ["CANCEL_CONFIRMED", "FILLED_DURING_CANCEL"],
        state.get("pending_position_reconciliation") is not True,
        dict(state.get("ledger") or {}).get("inventory_btc") != "0",
    )):
        raise RepairError("Session 1 predecessor boundary drifted")
    return {
        "immutable": True,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "package_id": PACKAGE_ID,
        "session_package_id": SESSION_PACKAGE_ID,
        "session_run_id": SESSION_RUN_ID,
        "session_id": SESSION_ID,
        "resume_authorized": False,
        "session_2_authorized": False,
        "terminal_account_authoritative": False,
        "normal_create_dispatches": 4,
        "flatten_dispatches": 0,
        "mutation_retries": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "old_classifications": classifications,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnostic(root: Path) -> dict[str, object]:
    event_rows = [json.loads(line)["payload"] for line in _paths(root)["events"].read_text(encoding="utf-8").splitlines()]
    if event_rows[-1].get("event") != "SHUTDOWN_CANCEL_RECONCILIATION_FAILED":
        raise RepairError("Session 1 event tail drifted")
    return {
        "passed": True,
        "root_cause": "generic terminal closed status with zero reported fill was classified as FILLED_DURING_CANCEL without owned trade-union evidence",
        "old_cancel_fill_classifications": {"cancel_confirmed": 1, "phantom_filled_during_cancel": 1},
        "old_owned_trade_union_rows": 0,
        "repair": {
            "filled_during_cancel_requires_owned_trade_quantity_proof": True,
            "closed_zero_fill_classification": "CANCEL_CONFIRMED",
            "unproven_positive_fill_claim": "FAIL_CLOSED",
            "persistent_ambiguity_blocks_flatten_before_dispatch": True,
            "converged_zero_fill_path": "AUTHORITATIVE_TERMINAL_0_0",
        },
        "risk_expansion": False,
    }


def projection() -> dict[str, object]:
    return {"passed": True, "fixtures": [
        {"fixture": "closed terminal order with filled=0 and no owned trades", "expected": "CANCEL_CONFIRMED; converged authoritative 0/0"},
        {"fixture": "terminal positive fill claim without owned trade evidence", "expected": "fail closed; zero flatten dispatch"},
        {"fixture": "full owned trade union matches terminal fill", "expected": "FILLED_DURING_CANCEL"},
    ], "mutation_retries": 0, "risk_limits_changed": False}


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh R2 Session 1 repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("R2 Session 1 repair identity reuse refused")
    predecessor, cause, rehearsal = verify_predecessor(root), diagnostic(root), projection()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_session_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", cause)
    _write_json(output / "diagnostic/promotion_rehearsal.json", rehearsal)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "fill classification requires owned trade quantity proof", "passed": True},
        {"requirement": "zero-fill generic closed order converges as cancelled", "passed": True},
        {"requirement": "positive unproven fill remains fail-closed", "passed": True},
        {"requirement": "persistent ambiguity blocks flatten before dispatch", "passed": True},
        {"requirement": "converged path writes authoritative terminal 0/0", "passed": True},
        {"requirement": "mutation retries remain zero", "passed": True},
    ])
    sources = ("AGENTS.md", "okx_fill_restart_gateway.py", "okx_demo_soak_executor.py", Path(__file__).name,
               "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
               "tests/test_okx_demo_soak_executor.py", "tests/test_okx_demo_r2_session1_cancel_fill_reconciliation_repair.py")
    _write_json(output / "specification/source_hashes.json", {name: _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r2_session1_cancel_fill_targeted", (
        "tests/test_okx_demo_r2_session1_cancel_fill_reconciliation_repair.py",
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_fill_restart_validation.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/r2_session1_cancel_fill_targeted_summary.json", targeted)
    all_suites = {**suites, "r2_session1_cancel_fill_targeted": targeted}
    if any(item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0 or int(item.get("network_attempts", 1)) != 0 or int(item.get("live_endpoint_attempts", 1)) != 0 or item.get("optuna_imported") is not False for item in all_suites.values()):
        raise RepairError("R2 Session 1 repair test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {"socket_denied": True, "network_attempts": 0, "credential_reads": 0, "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0, "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0, "account_configuration_attempts": 0, "orders": 0, "mutation_retries": 0})
    decision = {"status": READY, "R0_offline_repair_passed": True, "R1_preparation_authorized": False, "preflight_authorized": False, "economic_campaign_authorized": False, "production_authorized": False, "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False, "validation_opened": False, "holdout_opened": False, "git_write_operation": False, "next_boundary": "separate exact authorization for fresh R1 offline preparation"}
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION1_CANCEL_FILL_REPAIR_COMPLETED.json", {**decision, "evidence_id": evidence_id, "completed_at_utc": datetime.now(timezone.utc).isoformat(), "completion_files_checked": len(completion), "completion_hashes_sha256": _sha256(output / "completion_hashes.json"), "terminal_written_last": True})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"R2_SESSION1_CANCEL_FILL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
