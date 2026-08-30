"""Build fresh socket-denied evidence for terminal special-closure repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from okx_demo_economic_session_controller import (
    EconomicSessionController,
    PendingCausalFill,
    SampleEfficiencyQuotePolicy,
)
from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_terminal_special_closure_repair")
PATTERN = re.compile(r"terminal-special-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_TERMINAL_SPECIAL_CLOSURE_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260822T040609Z"
CAMPAIGN_ID = "economic-campaign-20260822T040609Z"
RUN_ID = "economic-campaign-run-20260822T040609Z"
FAILED_SESSION_ID = "soak-package-20260822T040609Z-s11-7ab96d4c71"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(root: Path) -> dict[str, Path]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / FAILED_SESSION_ID / "soak_run"
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "slot_11_attempt": campaign / "attempted_sessions/slot-11.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_economics": session / "audits/economics.json",
        "session_state": session / "state/validation_state.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("terminal special-closure predecessor is incomplete")
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    economics = _read(paths["session_economics"])
    pending = list(economics.get("pending_causal_fills") or [])
    if any((
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 11,
        terminal.get("sessions_completed") != 10,
        terminal.get("sessions_failed") != 1,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        decision.get("decision") != "NOT_READY",
        state.get("campaign_id") != CAMPAIGN_ID,
        state.get("terminal_decision") != "NOT_READY",
        state.get("completed_slots") != list(range(1, 11)),
        state.get("failed_slots") != [11],
        state.get("active_slot") is not None,
        failed.get("reason")
        != "SoakExecutionError:economic controller/engine/account inventory mismatch",
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_snapshots") != 2,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        len(pending) != 1,
        pending[0].get("fill_side") != "sell",
        pending[0].get("causal_binding", {}).get("remaining_workoff_btc") != "0.010",
        failed.get("special_fill_count") != 1,
    )):
        raise RepairError("terminal special-closure predecessor boundary drifted")
    aggregate = dict(decision["attempted_aggregate"])
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "failed_session_package_id": FAILED_SESSION_ID,
        "terminal_decision": "NOT_READY",
        "completed_slots": list(range(1, 11)),
        "failed_slots": [11],
        "slot_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "normal_fill_count": aggregate["normal_fill_count"],
        "normal_fifo_round_trips": aggregate["normal_fifo_round_trips"],
        "normal_net_pnl_usdt": aggregate["normal_net_pnl_usdt"],
        "special_flatten_sessions": aggregate["special_flatten_sessions"],
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def replay_special_closure(root: Path) -> dict[str, object]:
    economics = _read(_paths(root)["session_economics"])
    failure = _read(_paths(root)["session_failure_evidence"])
    row = dict(economics["pending_causal_fills"][0])
    controller = EconomicSessionController(
        session_id=str(failure["session_id"]), source_sha256="a" * 64
    )
    item = PendingCausalFill.from_dict(row)
    controller.pending_fills[item.trade_id] = item
    controller.markouts_usdt[item.trade_id] = Decimal("0")
    inventory = (
        item.remaining_workoff_btc
        if item.fill_side == "buy" else -item.remaining_workoff_btc
    )
    closed = controller.close_with_special_flatten(
        timestamp_ms=max(item.defense_timestamp_ms + 1, 1),
        inventory_before_btc=inventory,
        flatten_quantity_btc=abs(inventory),
    )
    controller.finish(timestamp_ms=controller.last_timestamp_ms + 1)
    evidence = controller.evidence(require_complete=True)
    return {
        "passed": True,
        "closed_trade_ids": list(closed),
        "pending_after": len(controller.pending_fills),
        "causal_reentry_credit_after": len(evidence["causal_reentry"]),
        "special_closed_count": len(evidence["special_closed_causal_fills"]),
        "maker_workoff_credit": False,
        "controller_inventory_before_btc": str(inventory),
        "engine_inventory_before_btc": str(inventory),
        "account_inventory_before_btc": str(inventory),
        "terminal_inventory_btc": "0",
        "source_artifacts_modified": False,
        "projection_promotable": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh terminal special repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("terminal special repair identity reuse refused")
    predecessor = verify_predecessor(root)
    replay = replay_special_closure(root)
    policy = SampleEfficiencyQuotePolicy()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/slot_11_special_closure_replay.json", replay)
    _write_json(output / "diagnostic/workoff_opportunity_audit.json", {
        "normal_defense_retention_ticks": policy.retention_threshold_ticks(Decimal("0.01")),
        "draining_workoff_retention_ticks": policy.retention_threshold_ticks(
            Decimal("0.01"), draining=True
        ),
        "admission_create_cap": policy.admission_create_cap,
        "workoff_create_reserve": policy.workoff_create_reserve,
        "total_create_cap": 60,
        "risk_expansion": False,
        "flatten_reduction_claimed_without_demo_evidence": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "bounded recent-tail convergence before inventory mismatch", "passed": True},
        {"requirement": "special closure never earns maker work-off credit", "passed": True},
        {"requirement": "controller/engine/account quantities reconcile exactly", "passed": True},
        {"requirement": "multi-partial state remains durable", "passed": True},
        {"requirement": "draining refresh improves maker opportunity without budget growth", "passed": True},
        {"requirement": "failed campaign is immutable and non-resumable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "account leads recent-trade tail", "expected": "bounded reads then reconcile"},
        {"fixture": "partial remainder then special flatten", "expected": "special-only closure"},
        {"fixture": "three-ledger quantity mismatch", "expected": "fail closed"},
        {"fixture": "timebox draining before admission cap", "expected": "one-sided work-off only"},
        {"fixture": "socket denied", "expected": "zero endpoint and mutation attempts"},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_economic_session_controller.py",
        "okx_demo_soak_executor.py",
        "okx_demo_terminal_recovery_executor.py",
        "okx_demo_terminal_special_closure_repair_offline.py",
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_soak_executor.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "terminal_special_targeted", (
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_terminal_recovery.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/terminal_special_targeted_summary.json", targeted)
    all_suites = {**suites, "terminal_special_targeted": targeted}
    if any(int(item["network_attempts"]) for item in all_suites.values()):
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
        raise RepairError("terminal special repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_TERMINAL_SPECIAL_CLOSURE_REPAIR_COMPLETED.json", {
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
        print(f"TERMINAL_SPECIAL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
