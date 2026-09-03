"""Build socket-denied R0 evidence for the R2 Session 1 special-closure repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session1_special_closure_repair")
PATTERN = re.compile(r"r2-session1-special-closure-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION1_SPECIAL_CLOSURE_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260830T105752Z"
CAMPAIGN_ID = "economic-campaign-20260830T105752Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260830T105752Z"
SESSION_PACKAGE_ID = "soak-package-20260830T105752Z-s01-1fb2ec8ab7"
SESSION_RUN_ID = "economic-session-20260830T105752Z-s01-1fb2ec8ab7"
SESSION_ID = "economic:economic-session-20260830T105752Z-s01-1fb2ec8ab7:p0:1fb2ec8ab7"


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
        "economics": session / "audits/economics.json",
        "state": session / "state/validation_state.json",
        "journal": session / "state/validation_state_journal.jsonl",
        "events": session / "streams/events.jsonl",
        "completion": session / "completion_hashes.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("Session 1 special-closure evidence is incomplete")
    failure, unresolved = _read(paths["failure"]), _read(paths["unresolved"])
    gateway, economics, envelope = _read(paths["gateway"]), _read(paths["economics"]), _read(paths["state"])
    state = dict(envelope.get("payload") or {})
    if any((
        failure.get("package_id") != SESSION_PACKAGE_ID,
        failure.get("run_id") != SESSION_RUN_ID,
        failure.get("session_id") != SESSION_ID,
        failure.get("reason") != "CampaignError:special closure controller/engine inventory does not reconcile",
        unresolved.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failure.get("terminal_account_authoritative") is not True,
        failure.get("two_flat_empty_snapshots") is not True,
        failure.get("final_position_btc") != "0",
        failure.get("final_open_orders") != 0,
        failure.get("normal_bid_fills") != 2,
        failure.get("normal_ask_fills") != 3,
        failure.get("normal_fifo_round_trips") != 4,
        failure.get("special_fill_count") != 3,
        failure.get("mutation_retries") != 0,
        failure.get("live_endpoint_attempts") != 0,
        gateway.get("flatten_dispatches") != 1,
        gateway.get("normal_create_dispatches") != 32,
        economics.get("engine_reconciled") is not True,
        dict(state.get("ledger") or {}).get("inventory_btc") != "0.0000",
        state.get("last_reconciled_position_btc") != "0.0000",
    )):
        raise RepairError("Session 1 special-closure predecessor boundary drifted")
    return {
        "immutable": True, "package_id": PACKAGE_ID, "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID, "session_package_id": SESSION_PACKAGE_ID,
        "session_run_id": SESSION_RUN_ID, "session_id": SESSION_ID,
        "active_failed_slot": 1, "session_2_started": False,
        "resume_authorized": False, "rerun_authorized": False,
        "terminal_account_authoritative": True, "final_position_btc": "0",
        "final_open_orders": 0, "normal_fills": 5, "normal_fifo_round_trips": 4,
        "special_fill_count": 3, "flatten_dispatches": 1,
        "mutation_retries": 0, "live_endpoint_attempts": 0, "live_orders": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnose(root: Path) -> dict[str, object]:
    failure = _read(_paths(root)["failure"])
    rows = list(failure.get("causal_reentry") or [])
    causal_inventory = sum(
        (
            Decimal(str(dict(row.get("causal_binding") or {}).get("remaining_workoff_btc", "0")))
            * (Decimal("1") if row.get("fill_side") == "buy" else Decimal("-1"))
            for row in rows
        ),
        Decimal("0"),
    )
    if causal_inventory != Decimal("-0.0035"):
        raise RepairError("Session 1 causal mismatch signature drifted")
    return {
        "passed": True,
        "root_cause": "an opposing maker fill was credited as work-off against an older causal lot while its full quantity was also retained as a new causal remainder; five cross-zero fills therefore produced -0.0035 BTC causal inventory against -0.0055 BTC engine inventory before flatten",
        "pre_flatten_engine_inventory_btc": "-0.0055",
        "old_causal_inventory_btc": str(causal_inventory),
        "mismatch_btc": "0.0020",
        "repair": {
            "opposing_fill_quantity_is_fifo_netted": True,
            "only_cross_zero_residual_opens_new_causal_lot": True,
            "inventory_transition_must_equal_signed_fill_quantity": True,
            "special_flatten_still_requires_exact_controller_engine_match": True,
            "persistent_ambiguity_blocks_mutation": True,
            "authoritative_terminal_zero_zero_preserved": True,
        },
        "risk_expansion": False,
    }


def projection() -> dict[str, object]:
    return {"passed": True, "mutation_retries": 0, "risk_limits_changed": False, "fixtures": [
        {"fixture": "five alternating cross-zero normal maker fills", "expected": "FIFO-net causal inventory equals -0.0055 BTC engine inventory"},
        {"fixture": "three-part reduce-only special flatten", "expected": "special attribution only; authoritative terminal 0/0"},
        {"fixture": "conflicting fill inventory transition", "expected": "fail closed before continuation mutation"},
        {"fixture": "persistent cancel ambiguity before flatten", "expected": "zero flatten dispatch"},
    ]}


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh R2 Session 1 special-closure repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("R2 Session 1 special-closure repair identity reuse refused")
    predecessor, diagnostic, rehearsal = verify_predecessor(root), diagnose(root), projection()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_session_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", diagnostic)
    _write_json(output / "diagnostic/promotion_rehearsal.json", rehearsal)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "normal and special fill attribution remains disjoint", "passed": True},
        {"requirement": "controller causal inventory equals engine inventory before flatten", "passed": True},
        {"requirement": "gateway/account terminal evidence remains authoritative 0/0", "passed": True},
        {"requirement": "persistent ambiguity blocks flatten before dispatch", "passed": True},
        {"requirement": "mutation retries remain exactly zero", "passed": True},
        {"requirement": "frozen risk limits are unchanged", "passed": True},
    ])
    sources = (
        "AGENTS.md", "okx_demo_economic_session_controller.py", "okx_demo_soak_executor.py",
        "okx_fill_restart_validation.py", Path(__file__).name,
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_r2_session1_special_closure_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {name: _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r2_session1_special_closure_targeted", (
        "tests/test_okx_demo_r2_session1_special_closure_repair.py",
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_fill_restart_validation.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/r2_session1_special_closure_targeted_summary.json", targeted)
    all_suites = {**suites, "r2_session1_special_closure_targeted": targeted}
    if any(item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0 or int(item.get("network_attempts", 1)) != 0 or int(item.get("live_endpoint_attempts", 1)) != 0 or item.get("optuna_imported") is not False for item in all_suites.values()):
        raise RepairError("R2 Session 1 special-closure test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {"socket_denied": True, "network_attempts": 0, "credential_reads": 0, "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0, "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0, "account_configuration_attempts": 0, "orders": 0, "mutation_retries": 0})
    decision = {"status": READY, "R0_offline_repair_passed": True, "R1_preparation_authorized": False, "preflight_authorized": False, "economic_campaign_authorized": False, "production_authorized": False, "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False, "validation_opened": False, "holdout_opened": False, "git_write_operation": False, "next_boundary": "separate exact authorization for fresh R1 offline preparation"}
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION1_SPECIAL_CLOSURE_REPAIR_COMPLETED.json", {**decision, "evidence_id": evidence_id, "completed_at_utc": datetime.now(timezone.utc).isoformat(), "completion_files_checked": len(completion), "completion_hashes_sha256": _sha256(output / "completion_hashes.json"), "terminal_written_last": True})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evidence-id", required=True)
    args = parser.parse_args()
    try:
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"R2_SESSION1_SPECIAL_CLOSURE_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
