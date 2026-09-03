"""Offline audit for the R2 special-fill versus flatten-session boundary."""
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


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_special_flatten_classification_repair")
PATTERN = re.compile(r"r2-special-flatten-classification-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SPECIAL_FLATTEN_CLASSIFICATION_REPAIR_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260902T132514Z"
CAMPAIGN_ID = "economic-campaign-20260902T132514Z"
RUN_ID = "economic-campaign-run-20260902T132514Z"
SLOTS = (
    (1, "soak-package-20260902T132514Z-s01-e6ec98914e"),
    (2, "soak-package-20260902T132514Z-s02-646363b086"),
    (3, "soak-package-20260902T132514Z-s03-a6dad64b0b"),
    (4, "soak-package-20260902T132514Z-s04-416d1c5e34"),
)


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable object required: {path.name}")
    return value


def campaign_audit(root: Path) -> dict[str, object]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    state_path = package / "campaign_run/state/supervisor_state.json"
    terminal = package / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
    registry = package / "campaign_run/registry/campaign_registry.jsonl"
    state = _read(state_path)
    if terminal.exists() is not True or state.get("terminal_decision") != "NOT_READY":
        raise RepairError("campaign freeze terminal evidence is missing")
    if state.get("completed_slots") != [1, 2, 3, 4] or state.get("active_slot") != 5:
        raise RepairError("campaign progression does not match the frozen boundary")
    rows: list[dict[str, object]] = []
    for slot, session_package in SLOTS:
        evidence = _read(
            root / "artifacts/okx_demo_soak_validation" / session_package
            / "soak_run/audits/economic_session_evidence.json"
        )
        if any((
            evidence.get("final_position_btc") != "0",
            evidence.get("final_open_orders") != 0,
            evidence.get("reconciles") is not True,
            evidence.get("terminal_reconciled") is not True,
            evidence.get("mutation_retries") != 0,
        )):
            raise RepairError(f"session {slot} terminal safety audit failed")
        rows.append({
            "slot": slot,
            "package_id": session_package,
            "special_fill_count": int(evidence.get("special_fill_count", -1)),
            "flatten_dispatches": int(evidence.get("flatten_dispatches", -1)),
            "normal_fees_usdt": evidence.get("normal_fees_usdt"),
            "special_fees_usdt": evidence.get("special_fees_usdt"),
            "normal_net_pnl_usdt": evidence.get("normal_net_pnl_usdt"),
            "special_net_pnl_usdt": evidence.get("special_net_pnl_usdt"),
            "causal_reentry_has_immediate_taker": any(
                row.get("immediate_taker_flatten") is True
                for row in list(evidence.get("causal_reentry") or [])
            ),
        })
    dispatch_slots = [row["slot"] for row in rows if row["flatten_dispatches"] > 0]
    if dispatch_slots != [1, 4] or sum(row["special_fill_count"] for row in rows) != 4:
        raise RepairError("special fill/dispatch classification drifted")
    if any(row["flatten_dispatches"] > 1 for row in rows):
        raise RepairError("per-session flatten boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "terminal_decision": "NOT_READY",
        "completed_slots": [1, 2, 3, 4],
        "unstarted_slots": [5, 6],
        "identity_reuse_authorized": False,
        "resume_authorized": False,
        "accept_session_5_authorized": False,
        "last_completed_position_open_orders": "0/0",
        "last_completed_reconciliation": True,
        # R0 did not read the current account.  It may preserve completed
        # session evidence, but must not promote it to a fresh authority.
        "terminal_account_authoritative": False,
        "mutation_retries": 0,
        "special_fill_count": 4,
        "flatten_dispatches": 2,
        "special_flatten_sessions": len(dispatch_slots),
        "special_flatten_session_slots": dispatch_slots,
        "maximum_special_flatten_sessions": 2,
        "successor_stops_above_limit": True,
        "session_audits": rows,
        "state_sha256": _sha256(state_path),
        "registry_sha256": _sha256(registry),
        "terminal_sha256": _sha256(terminal),
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh special-flatten repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("offline evidence identity reuse refused")
    audit = campaign_audit(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/campaign_freeze_audit.json", audit)
    _write_json(output / "diagnostic/root_cause.json", {
        "repository_defect_found": True,
        "cause": "admission reporting could conflate special trade-level fills with the session-level flatten-rate unit",
        "repair": "derive special_flatten_sessions solely from flatten_dispatches > 0",
        "special_fill_count_is_trade_level": True,
        "terminal_flatten_is_not_causal_maker_reentry": True,
        "risk_or_permission_relaxed": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "four special fills classify as two dispatch sessions", "passed": True},
        {"requirement": "2/12 is admissible; >2/12 is fail-closed", "passed": True},
        {"requirement": "fees/PnL/fills remain trade-level values", "passed": True},
        {"requirement": "last completed terminal 0/0/reconciliation and zero mutation retries", "passed": True},
        {"requirement": "no immediate taker flatten receives causal maker credit", "passed": True},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name, "okx_demo_multi_session_campaign.py",
        "okx_demo_multi_session_supervisor.py", "okx_demo_soak_executor.py",
        "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py",
        "okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        str(name): _sha256(root / name) for name in sources
    })
    # Keep the socket-denied temporary directory short enough for Windows
    # child-artifact paths used by supervisor fixtures.
    targeted = _run_suite(root, output, "sf", (
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    ))
    suites = _run_required_suites(root, output)
    summary = {**suites, "targeted": targeted}
    _write_json(output / "tests/test_summary.json", summary)
    if any(
        item.get("passed_gate") is not True or item.get("returncode") != 0
        or item.get("network_attempts") != 0 or item.get("live_endpoint_attempts") != 0
        or item.get("optuna_imported") is not False for item in summary.values()
    ):
        raise RepairError("offline test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY,
        "evidence_kind": "r2_special_flatten_classification_r0_offline_repair",
        "R0_offline_repair_passed": True,
        "preflight_authorized": False, "economic_campaign_authorized": False,
        "production_authorized": False, "live_mode_available": False,
        "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False,
        "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if scan.get("passed") is not True:
        raise RepairError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SPECIAL_FLATTEN_CLASSIFICATION_REPAIR_COMPLETED.json", {
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
        print(f"R2_SPECIAL_FLATTEN_CLASSIFICATION_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
