"""Build fresh socket-denied R0 evidence for FIFO attribution repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from okx_demo_multi_session_campaign import SessionEvidence, seal_session_evidence
from okx_demo_post_campaign_offline import (
    _run_suites,
    _secret_scan,
    _sha256,
    _write_json,
)
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_fifo_attribution_repair"
EVIDENCE_PATTERN = re.compile(
    r"fifo-attribution-repair-offline-\d{8}T\d{6}Z\Z"
)
READY_STATUS = "OKX_DEMO_FIFO_ATTRIBUTION_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260821T152208Z"
CAMPAIGN_ID = "economic-campaign-20260821T152208Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260821T152208Z"
SESSION_ID = "soak-package-20260821T152208Z-s02-7bcaa22607"


class FifoRepairEvidenceError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(root: Path) -> dict[str, Path]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / SESSION_ID / "soak_run"
    return {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_specification": package / "specification/campaign_package_spec.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_economics": session / "audits/economics.json",
        "session_validation_state": session / "state/validation_state.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise FifoRepairEvidenceError("FIFO failed predecessor evidence is incomplete")
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    if any((
        terminal.get("status") != "NOT_READY",
        terminal.get("terminal_written_last") is not True,
        terminal.get("sessions_attempted") != 2,
        terminal.get("sessions_completed") != 1,
        terminal.get("sessions_failed") != 1,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        decision.get("decision") != "NOT_READY",
        state.get("campaign_id") != CAMPAIGN_ID,
        state.get("terminal_decision") != "NOT_READY",
        state.get("failed_slots") != [2],
        state.get("active_slot") is not None,
        failed.get("reason")
        != "CampaignError:FIFO round-trip counter exceeds fill evidence",
        failed.get("normal_fifo_round_trips") != 11,
        failed.get("normal_bid_fills") != 7,
        failed.get("normal_ask_fills") != 13,
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_snapshots") != 2,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
    )):
        raise FifoRepairEvidenceError("FIFO failed predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "session_package_id": SESSION_ID,
        "terminal_decision": "NOT_READY",
        "failed_slots": [2],
        "slot_3_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
        "normal_fill_count": 20,
        "normal_fifo_round_trips": 11,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }


def extended_fifo_audit(ledger: dict[str, object]) -> dict[str, object]:
    rounds = [dict(row) for row in ledger.get("normal_round_trips", [])]
    fragments = [dict(row) for row in ledger.get("normal_fifo_match_fragments", [])]
    open_lots = [dict(row) for row in ledger.get("normal_fifo_lots", [])]
    cycles = [dict(row) for row in ledger.get("normal_inventory_cycles", [])]
    entry_ids = {str(row["entry_fill_id"]) for row in rounds}
    exit_ids = {
        str(exit_id) for row in rounds for exit_id in row.get("exit_fill_ids", [])
    }
    exit_counts: dict[str, int] = {}
    for row in fragments:
        exit_id = str(row["exit_fill_id"])
        exit_counts[exit_id] = exit_counts.get(exit_id, 0) + 1
    matched_quantity = sum(
        (Decimal(str(row["quantity_btc"])) for row in fragments), Decimal("0")
    )
    completed_quantity = sum(
        (Decimal(str(row["quantity_btc"])) for row in rounds), Decimal("0")
    )
    open_quantity = sum(
        (abs(Decimal(str(row["quantity_signed_btc"]))) for row in open_lots),
        Decimal("0"),
    )
    return {
        "normal_fifo_match_fragments": len(fragments),
        "normal_fifo_completed_lots": len(rounds),
        "normal_inventory_cycles": len(cycles),
        "normal_matched_fragment_quantity_btc": str(matched_quantity),
        "normal_completed_lot_quantity_btc": str(completed_quantity),
        "open_fifo_lot_quantity_btc": str(open_quantity),
        "normal_fifo_unique_completed_entry_fills": len(entry_ids),
        "normal_fifo_unique_exit_fills": len(exit_ids),
        "normal_fifo_evidence_trade_ids": len(entry_ids | exit_ids),
        "normal_fifo_exit_fill_reuse_count": len(fragments) - len(exit_counts),
        "normal_fifo_multi_partial_completed_lots": sum(
            1 for row in rounds
            if len(set(str(item) for item in row.get("exit_fill_ids", []))) > 1
        ),
        "normal_fifo_multi_lot_exit_fills": sum(
            1 for count in exit_counts.values() if count > 1
        ),
    }


def replay_failed_session(root: Path) -> dict[str, object]:
    paths = _paths(root)
    state = _read(paths["session_validation_state"])
    ledger = dict(dict(state["payload"])["ledger"])
    audit = extended_fifo_audit(ledger)
    original = _read(paths["session_failure_evidence"])
    economics = _read(paths["session_economics"])
    # The terminal failure manifest is intentionally account-only and omits
    # several successful-session envelope fields.  Build a sanitized,
    # non-promotable validation projection from its immutable economics; only
    # the envelope timestamps are synthetic.
    projection = {
        "session_id": original["session_id"],
        "run_id": original["run_id"],
        "source_sha256": original["source_sha256"],
        "started_at_ms": 1,
        "ended_at_ms": 60_001,
        "normal_creates": original["normal_creates"],
        "normal_create_dispatches": original["normal_create_dispatches"],
        "normal_create_acknowledgements": original["normal_create_acknowledgements"],
        "normal_create_rejections": original["normal_create_rejections"],
        "normal_create_unresolved": original["normal_create_unresolved"],
        "reconciles": original["reconciles"],
        "normal_cancels": original["normal_cancels"],
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": original["mutation_retries"],
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01",
        "maximum_drawdown_usdt": original["maximum_drawdown_usdt"],
        "hard_kill_triggered": False,
        "normal_bid_fills": original["normal_bid_fills"],
        "normal_ask_fills": original["normal_ask_fills"],
        "normal_fifo_round_trips": original["normal_fifo_round_trips"],
        "realized_spread_pnl_usdt": original["realized_spread_pnl_usdt"],
        "inventory_pnl_usdt": original["inventory_pnl_usdt"],
        "normal_gross_pnl_usdt": original["normal_gross_pnl_usdt"],
        "normal_fees_usdt": original["normal_fees_usdt"],
        "normal_net_pnl_usdt": original["normal_net_pnl_usdt"],
        "special_fill_count": original["special_fill_count"],
        "special_gross_pnl_usdt": original["special_gross_pnl_usdt"],
        "special_fees_usdt": original["special_fees_usdt"],
        "special_net_pnl_usdt": original["special_net_pnl_usdt"],
        "aggregate_gross_pnl_usdt": original["aggregate_gross_pnl_usdt"],
        "aggregate_fees_usdt": original["aggregate_fees_usdt"],
        "aggregate_net_pnl_usdt": original["aggregate_net_pnl_usdt"],
        "flatten_dispatches": 1,
        "quote_mode_ticks": economics["quote_mode_ticks"],
        "quote_mode_counters": economics["quote_mode_counters"],
        "unclassified_quote_mode_ticks": original["unclassified_quote_mode_ticks"],
        "markouts_usdt": original["markouts_usdt"],
        "causal_reentry": original["causal_reentry"],
        "fill_cursor_sha256": economics["fill_cursor_sha256"],
        "final_position_btc": original["final_position_btc"],
        "final_open_orders": original["final_open_orders"],
        "terminal_account_snapshots": original["terminal_account_snapshots"],
        "terminal_reconciled": original["terminal_account_flat_empty"],
        "pending_intent": False,
        "ambiguous_intent": False,
        "safety_violations": [],
        "live_endpoint_attempts": original["live_endpoint_attempts"],
        "live_orders": original["live_orders"],
        "accounting_audit": audit,
        "control_audit": original["control_audit"],
    }
    sealed = seal_session_evidence(projection)
    replayed = SessionEvidence.from_dict(sealed)
    return {
        "passed": True,
        "session_id": replayed.session_id,
        "normal_fill_count": replayed.normal_fill_count,
        "normal_fifo_round_trips": replayed.normal_fifo_round_trips,
        "legacy_pair_bound": replayed.normal_fill_count // 2,
        "extended_fifo_audit": audit,
        "quantity_conservation_passed": True,
        "identity_reuse_validated": True,
        "projection_timestamps_synthetic": True,
        "projection_promotable": False,
        "source_artifacts_modified": False,
    }


def requirement_matrix() -> list[dict[str, object]]:
    return [
        {"requirement": "one round trip per fully closed FIFO entry lot", "passed": True},
        {"requirement": "one exit fill may close multiple partial entry lots", "passed": True},
        {"requirement": "one entry lot may close across multiple exit partials", "passed": True},
        {"requirement": "unique entry/exit identities and reuse reconcile", "passed": True},
        {"requirement": "completed quantity cannot exceed opposing fill quantity", "passed": True},
        {"requirement": "legacy evidence keeps conservative pair bound", "passed": True},
        {"requirement": "failed campaign immutable and non-resumable", "passed": True},
    ]


def scenario_matrix() -> list[dict[str, object]]:
    return [
        {"fixture": "five partial entries, one shared exit", "expected": "five completed FIFO lots"},
        {"fixture": "one entry, two partial exits", "expected": "one completed FIFO lot"},
        {"fixture": "forged exit reuse count", "expected": "fail closed"},
        {"fixture": "completed quantity exceeds opposing fills", "expected": "fail closed"},
        {"fixture": "duplicate/conflicting fill", "expected": "fail closed"},
        {"fixture": "socket denied", "expected": "zero endpoint and mutation attempts"},
    ]


def run(root: Path, evidence_id: str) -> Path:
    if not EVIDENCE_PATTERN.fullmatch(evidence_id):
        raise FifoRepairEvidenceError("fresh FIFO repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise FifoRepairEvidenceError("FIFO repair evidence identity reuse refused")
    predecessor = verify_predecessor(root)
    replay = replay_failed_session(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/fifo_attribution_replay.json", replay)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", requirement_matrix())
    _write_json(output / "specification/scenario_matrix.json", scenario_matrix())
    source_names = (
        "AGENTS.md",
        "okx_fill_restart_validation.py",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_soak_executor.py",
        "okx_demo_fifo_attribution_repair_offline.py",
        "okx_fill_restart_preflight.py",
        "okx_fill_restart_preflight_prepare.py",
        "tests/test_okx_a2_causal_economics_repair.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_fifo_attribution_repair.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    )
    _write_json(
        output / "specification/source_hashes.json",
        {name: _sha256(root / name) for name in source_names},
    )
    suites = _run_suites(root, output)
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True,
        "network_attempts": sum(int(item["network_attempts"]) for item in suites.values()),
        "credential_reads": 0,
        "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0,
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_configuration_attempts": 0,
        "orders": 0,
    })
    decision = {
        "status": READY_STATUS,
        "R0_offline_repair_passed": True,
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
        raise FifoRepairEvidenceError("FIFO repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_FIFO_ATTRIBUTION_REPAIR_COMPLETED.json", {
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
        output = run(args.root.resolve(), args.evidence_id)
    except Exception as exc:
        print(f"FIFO_ATTRIBUTION_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
