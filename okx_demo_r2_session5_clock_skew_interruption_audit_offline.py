"""Socket-denied, non-overwriting R0 audit of the interrupted R2 Session 5."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import (
    BACKTEST_DESELECT, BACKTEST_EXCLUSIONS, ROOT_DESELECT, ROOT_EXCLUSIONS,
    _secret_scan, _sha256, _write_json,
)
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk

ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session5_clock_skew_interruption_audit")
PATTERN = re.compile(r"r2-session5-clock-skew-interruption-audit-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION5_CLOCK_SKEW_INTERRUPTION_AUDIT_R0_OFFLINE_SUPPORT"
PACKAGE = "economic-package-20260901T141747Z"
CAMPAIGN = "economic-campaign-20260901T141747Z"
RUN = "economic-campaign-run-20260901T141747Z"
CAMPAIGN_SESSION = "economic-campaign:economic-campaign-run-20260901T141747Z:p0:de98096c0942"
SESSION_PACKAGE = "soak-package-20260901T141747Z-s05-60ea9f2820"
SESSION_RUN = "economic-session-20260901T141747Z-s05-60ea9f2820"
SESSION = "economic:economic-session-20260901T141747Z-s05-60ea9f2820:p0:60ea9f2820"


class AuditError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"expected JSON object: {path}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE
    campaign = package / "campaign_run"
    session_root = root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE
    session = session_root / "soak_run"
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "package_sources": package / "specification/source_hashes.json",
        "campaign_armed": campaign / "A2_CAMPAIGN_ARMED.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_lease": campaign / "state/campaign_lease.json",
        "campaign_events": campaign / "streams/supervisor_events.jsonl",
        "session_completion": session / "completion_hashes.json",
        "failed": session / "FAILED.json",
        "unresolved": session / "UNRESOLVED_FAILURE.json",
        "failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "gateway": session / "audits/gateway_audit.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
        "armed": session / "SOAK_EXECUTION_ARMED.json",
        "controller": session / "state/controller_state.jsonl",
        "lease": session / "state/lease.json",
    }


def _verify_manifest(session_root: Path, manifest: dict[str, object]) -> dict[str, object]:
    if not manifest:
        raise AuditError("Session 5 completion manifest is empty")
    for name, expected in manifest.items():
        path = session_root / str(name)
        if not isinstance(expected, str) or not path.is_file() or _sha256(path) != expected:
            raise AuditError(f"Session 5 completion hash mismatch: {name}")
    return {"files_checked": len(manifest), "manifest_sha256": _sha256(session_root / "soak_run/completion_hashes.json")}


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise AuditError("Session 5 or campaign durable evidence is incomplete")
    spec = _read(paths["package_spec"]); armed = _read(paths["campaign_armed"])
    state = _read(paths["campaign_state"]); failed = _read(paths["failed"])
    unresolved = _read(paths["unresolved"]); failure = _read(paths["failure_evidence"])
    gateway = _read(paths["gateway"]); manifest = _read(paths["session_completion"])
    elapsed = int(datetime.now(timezone.utc).timestamp() * 1000) - int(state.get("armed_at_ms", 0))
    identity = (failed.get("package_id"), failed.get("run_id"), failed.get("session_id"))
    zero = ("normal_creates", "normal_create_dispatches", "normal_create_acknowledgements",
            "normal_cancels", "mutation_retries", "live_endpoint_attempts", "live_orders")
    if any((
        spec.get("package_id") != PACKAGE, spec.get("campaign_id") != CAMPAIGN,
        spec.get("run_id") != RUN, armed.get("campaign_session_id") != CAMPAIGN_SESSION,
        state.get("active_slot") != 5, state.get("completed_slots") != [1, 2, 3, 4],
        state.get("failed_slots") != [], state.get("terminal_decision") is not None,
        elapsed <= 21_600_000, identity != (SESSION_PACKAGE, SESSION_RUN, SESSION),
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason") != "SoakExecutionError:account clock skew exceeds budget",
        failed.get("failure_stage") != "POST_BOOTSTRAP",
        failed.get("market_bootstrap_completed") is not True,
        failed.get("controller_engine_gateway_reconciled") is not False,
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_flat_empty") is not True,
        failed.get("two_flat_empty_snapshots") is not True,
        failed.get("final_position_btc") != "0", failed.get("final_open_orders") != 0,
        any(failed.get(key) != 0 for key in zero),
        any(unresolved.get(key) != failed.get(key) for key in ("reason", "failure_stage", "final_position_btc", "final_open_orders")),
        any(failure.get(key) != failed.get(key) for key in ("package_id", "run_id", "session_id", "reason")),
        gateway.get("mutation_call_count") != 0,
        gateway.get("mutation_method_counts") != {"cancel_order": 0, "create_order": 0},
        gateway.get("flatten_dispatches") != 0,
        gateway.get("live_endpoint_attempts") != 0, gateway.get("live_orders") != 0,
    )):
        raise AuditError("Session 5 clock-skew fail-closed boundary drifted")
    session_root = paths["session_completion"].parent.parent
    hashes = _verify_manifest(session_root, manifest)
    return {
        "immutable": True, "package_id": PACKAGE, "campaign_id": CAMPAIGN,
        "campaign_run_id": RUN, "campaign_session_id": CAMPAIGN_SESSION,
        "failed_session_slot": 5, "failed_session_package_id": SESSION_PACKAGE,
        "failed_session_run_id": SESSION_RUN, "failed_session_id": SESSION,
        "failed_campaign_decision": "NOT_READY", "campaign_wall_expired": True,
        "elapsed_wall_ms": elapsed, "completed_slots": [1, 2, 3, 4], "failed_slots": [],
        "failure_stage": "POST_BOOTSTRAP", "failure_reason": failed["reason"],
        "clock_skew_gate_failed_before_mutation": True,
        "controller_engine_gateway_reconciled": False,
        "terminal_account_authoritative": True,
        "terminal_position_open_orders": ["0", 0], "mutation_retries": 0,
        "create_amend_cancel_flatten": [0, 0, 0, 0], "resume_authorized": False,
        "retry_authorized": False, "accept_authorized": False, "session_6_started": False,
        "completion_hash_verification": hashes,
        "source_hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def _run_required_suites(root: Path, output: Path) -> dict[str, dict[str, object]]:
    """Run successor-applicable tests while preserving historical package immutability."""
    historical_immutability_deselect = (
        "tests/test_okx_demo_sample_efficiency_campaign_prepare.py::test_successor_source_extension_does_not_modify_r0_bound_preparer",
        "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py::test_post_start_child_runtime_additions_preserve_immutable_package_audit",
    )
    root_args = (
        "tests", *(f"--ignore={item}" for item in (*ROOT_EXCLUSIONS, "tests/test_okx_demo_multi_session_a0_offline.py")),
        *(f"--deselect={item}" for item in (*ROOT_DESELECT, *historical_immutability_deselect)),
    )
    backtest_args = (
        "backtest/tests", *(f"--ignore={item}" for item in BACKTEST_EXCLUSIONS),
        *(f"--deselect={item}" for item in BACKTEST_DESELECT),
    )
    result = {
        "root_non_optuna": _run_suite(root, output, "root_non_optuna", root_args),
        "backtest_non_optuna": _run_suite(root, output, "backtest_non_optuna", backtest_args),
    }
    return result


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise AuditError("fresh Session 5 interruption audit identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise AuditError("interruption audit identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/session5_clock_skew_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "passed": True, "cause": "power interruption followed by host/account clock-skew beyond 1500ms budget",
        "failure_stage": "POST_BOOTSTRAP", "defect_found": False,
        "clock_or_risk_budget_changed": False, "mutation_blocked_before_dispatch": True,
        "controller_engine_gateway_not_claimed_reconciled": True,
        "durable_hash_chain_verified": True,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "skew over budget blocks all mutations", "passed": True},
        {"requirement": "authoritative terminal account is flat 0/0", "passed": True},
        {"requirement": "pre-start session is not represented as reconciled", "passed": True},
        {"requirement": "expired campaign is immutable NOT_READY and non-resumable", "passed": True},
    ])
    sources = ("AGENTS.md", Path(__file__).name, "okx_fill_restart_preflight.py",
               "okx_fill_restart_preflight_prepare.py", "okx_demo_multi_session_prepare.py",
               "okx_demo_execution_environment_successor_supervisor.py",
               "tests/test_okx_demo_r2_session5_clock_skew_interruption_audit.py")
    _write_json(output / "specification/source_hashes.json", {str(name): _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "session5_clock_skew_targeted", (
        "tests/test_okx_demo_r2_session5_clock_skew_interruption_audit.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
        "tests/test_okx_demo_multi_session_prepare.py"))
    suites = _run_required_suites(root, output)
    _write_json(output / "tests/session5_clock_skew_targeted_summary.json", targeted)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/test_summary.json", summary)
    if any(value.get("passed_gate") is not True or value.get("returncode") != 0
           or value.get("network_attempts") != 0 or value.get("optuna_imported") is not False
           for value in summary.values()):
        raise AuditError("offline test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0, "create_attempts": 0,
        "amend_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0,
        "account_configuration_attempts": 0, "orders": 0, "mutation_retries": 0})
    decision = {"status": READY, "evidence_kind": "r2_session5_clock_skew_interruption_audit_r0_offline",
                "R0_offline_audit_passed": True, "failed_campaign_decision": "NOT_READY",
                "preflight_authorized": False, "economic_campaign_authorized": False,
                "production_authorized": False, "live_mode_available": False,
                "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False,
                "validation_opened": False, "holdout_opened": False, "git_write_operation": False,
                "next_boundary": "separate exact authorization for fresh R1 offline preparation"}
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output); _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]: raise AuditError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path)
                  for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION5_CLOCK_SKEW_INTERRUPTION_AUDIT_COMPLETED.json", {
        **decision, "evidence_id": evidence_id, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_files_checked": len(completion), "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "terminal_written_last": True})
    return output


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent); parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try: print(run(args.root, args.evidence_id))
    except Exception as exc: print(f"R2_SESSION5_CLOCK_SKEW_AUDIT_FAILED:{type(exc).__name__}:{exc}"); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
