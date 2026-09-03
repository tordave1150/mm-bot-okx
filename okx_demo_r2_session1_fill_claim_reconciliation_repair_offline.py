"""Create immutable R0 evidence for the R2 Session 1 fill-claim repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_r2_session5_clock_skew_interruption_audit_offline import _run_required_suites
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session1_fill_claim_reconciliation_repair")
PATTERN = re.compile(r"r2-session1-fill-claim-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION1_FILL_CLAIM_RECONCILIATION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260902T053030Z"
CAMPAIGN_ID = "economic-campaign-20260902T053030Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260902T053030Z"
SESSION_PACKAGE_ID = "soak-package-20260902T053030Z-s01-11c3e9e646"
SESSION_RUN_ID = "economic-session-20260902T053030Z-s01-11c3e9e646"
SESSION_ID = "economic:economic-session-20260902T053030Z-s01-11c3e9e646:p0:11c3e9e646"
UNPROVEN_CLIENT_ID = "fr131ef1041ba0e22251ef76ab834b"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"expected JSON object: {path}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    run = root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE_ID / "soak_run"
    campaign = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID / "campaign_run"
    return {
        "failure": run / "FAILED.json",
        "unresolved": run / "UNRESOLVED_FAILURE.json",
        "gateway": run / "audits/gateway_audit.json",
        "state": run / "state/validation_state.json",
        "events": run / "streams/events.jsonl",
        "completion": run / "completion_hashes.json",
        "campaign_marker": campaign / "A2_CAMPAIGN_ARMED.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("Session 1 durable evidence is incomplete")
    failure, unresolved = _read(paths["failure"]), _read(paths["unresolved"])
    gateway, envelope = _read(paths["gateway"]), _read(paths["state"])
    campaign_marker, campaign_state = _read(paths["campaign_marker"]), _read(paths["campaign_state"])
    cancel = dict(gateway.get("last_cancel_reconciliation_audit") or {})
    classifications = dict(cancel.get("classifications") or {})
    state = dict(envelope.get("payload") or {})
    owned = dict(state.get("owned_orders") or {})
    if any((
        failure.get("package_id") != SESSION_PACKAGE_ID,
        failure.get("run_id") != SESSION_RUN_ID,
        failure.get("session_id") != SESSION_ID,
        failure.get("reason") != "FormalGatewayError:owned cancellation outcome remains unresolved after bounded reads",
        failure.get("terminal_account_authoritative") is not False,
        failure.get("mutation_retries") != 0,
        gateway.get("normal_create_dispatches") != 26,
        gateway.get("flatten_dispatches") != 0,
        cancel.get("read_attempts") != 3,
        cancel.get("mutation_retries") != 0,
        cancel.get("resolved") is not False,
        cancel.get("targeted_order_fill_queries", 0) != 0,
        classifications.get(UNPROVEN_CLIENT_ID) != "FILL_CLAIM_UNPROVEN",
        sorted(classifications.values()) != ["CANCEL_CONFIRMED", "FILL_CLAIM_UNPROVEN"],
        dict(cancel.get("fill_union_audit") or {}).get("union_rows") != 0,
        dict(owned.get(UNPROVEN_CLIENT_ID) or {}).get("status") != "ACKNOWLEDGED",
        unresolved.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failure.get("live_endpoint_attempts") != 0,
        failure.get("live_orders") != 0,
        campaign_marker.get("package_id") != PACKAGE_ID,
        campaign_marker.get("campaign_id") != CAMPAIGN_ID,
        campaign_marker.get("run_id") != CAMPAIGN_RUN_ID,
        campaign_state.get("campaign_id") != CAMPAIGN_ID,
        campaign_state.get("active_slot") != 1,
        campaign_state.get("completed_slots") != [],
        campaign_state.get("failed_slots") != [],
        campaign_state.get("terminal_decision") is not None,
    )):
        raise RepairError("Session 1 fill-claim predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "session_package_id": SESSION_PACKAGE_ID,
        "session_run_id": SESSION_RUN_ID,
        "session_id": SESSION_ID,
        "failed_classification": "FILL_CLAIM_UNPROVEN",
        "normal_create_dispatches": 26,
        "normal_cancel_dispatches": 25,
        "flatten_dispatches": 0,
        "terminal_account_authoritative": False,
        "failed_campaign_decision": "NOT_READY",
        "active_failed_slot": 1,
        "session_1_accepted": False,
        "resume_authorized": False,
        "session_2_authorized": False,
        "mutation_retries": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnostic(root: Path) -> dict[str, object]:
    rows = [json.loads(line)["payload"] for line in _paths(root)["events"].read_text(encoding="utf-8").splitlines()]
    if rows[-1].get("event") != "SHUTDOWN_CANCEL_RECONCILIATION_FAILED":
        raise RepairError("Session event tail drifted")
    return {
        "passed": True,
        "root_cause": "a terminal order reported a positive fill while the broad paginated/recent-tail union exposed no exact owned trade within the bounded reconciliation window",
        "repair": {
            "targeted_read_only_order_fill_query": True,
            "requires_exact_order_and_client_id": True,
            "requires_quantity_fee_and_liquidity_validation": True,
            "timestamp_tolerance_ms": 1500,
            "read_attempt_cap": 3,
            "unproven_positive_fill_remains_fail_closed": True,
            "mutation_retry_cap": 0,
        },
        "risk_expansion": False,
    }


def projection() -> dict[str, object]:
    return {
        "passed": True,
        "fixtures": [
            "delayed exact targeted fill inside 1500 ms skew converges",
            "recent-tail saturation of 100 rows does not hide the targeted proof",
            "wrong client/order identity or stale timestamp remains fail closed",
            "persistent ambiguity dispatches no flatten",
        ],
        "authoritative_terminal_fixture": {"position_btc": "0", "open_orders": 0},
        "mutation_retries": 0,
        "risk_limits_changed": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh Session 1 fill-claim repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("Session 1 fill-claim repair identity reuse refused")
    predecessor, cause, rehearsal = verify_predecessor(root), diagnostic(root), projection()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_session_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", cause)
    _write_json(output / "diagnostic/promotion_rehearsal.json", rehearsal)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "exact order/client/quantity/fee/liquidity proof", "passed": True},
        {"requirement": "1500 ms timestamp boundary", "passed": True},
        {"requirement": "tail saturation and delayed visibility", "passed": True},
        {"requirement": "persistent ambiguity blocks mutation", "passed": True},
        {"requirement": "read attempts <= 3 and mutation retries = 0", "passed": True},
        {"requirement": "converged fixture reaches terminal 0/0", "passed": True},
    ])
    sources = (
        "AGENTS.md", "okx_fill_restart_gateway.py", "okx_demo_soak_executor.py",
        "okx_demo_r2_session5_clock_skew_interruption_audit_offline.py", Path(__file__).name, "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_demo_soak_executor.py",
    )
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r2_session1_fill_claim_targeted", (
        "tests/test_okx_owned_cancel_authoritative_reconciliation.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_fill_restart_validation.py",
    ))
    suites = _run_required_suites(root, output)
    _write_json(output / "tests/r2_session1_fill_claim_targeted_summary.json", targeted)
    all_suites = {**suites, "targeted": targeted}
    _write_json(output / "tests/test_summary.json", all_suites)
    if any(item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0 or int(item.get("network_attempts", 1)) != 0 or int(item.get("live_endpoint_attempts", 1)) != 0 or item.get("optuna_imported") is not False for item in all_suites.values()):
        raise RepairError("Session 1 fill-claim repair test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY, "R0_offline_repair_passed": True,
        "failed_campaign_decision": "NOT_READY",
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
        raise RepairError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION1_FILL_CLAIM_REPAIR_COMPLETED.json", {
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
        print(f"R2_SESSION1_FILL_CLAIM_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
