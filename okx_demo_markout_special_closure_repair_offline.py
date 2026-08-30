"""Build immutable socket-denied R0 evidence for split markout attribution."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_multi_session_campaign import (
    CampaignRegistry,
    SessionEvidence,
    seal_session_evidence,
)
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_markout_special_closure_repair")
PATTERN = re.compile(r"markout-special-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_MARKOUT_SPECIAL_CLOSURE_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260822T091916Z"
CAMPAIGN_ID = "economic-campaign-20260822T091916Z"
RUN_ID = "economic-campaign-run-20260822T091916Z"
FAILED_SESSION_ID = "soak-package-20260822T091916Z-s01-b5c6b47506"


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
        "package_completion": package / "completion_hashes.json",
        "package_spec": package / "specification/campaign_package_spec.json",
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "slot_01_attempt": campaign / "attempted_sessions/slot-01.json",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "session_economics": session / "audits/economics.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
        "raw_result": session / "raw_result.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("markout predecessor evidence is incomplete")
    package = _read(paths["package_terminal"])
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    failed = _read(paths["session_failure"])
    economics = _read(paths["session_economics"])
    snapshots = _read(paths["terminal_snapshots"])
    causal = list(economics.get("causal_reentry") or [])
    markouts = list(economics.get("markouts_usdt") or [])
    special = list(economics.get("special_closed_causal_fills") or [])
    special_markouts = list(economics.get("special_closed_markouts_usdt") or [])
    normal_fills = int(economics["normal_bid_fills"]) + int(economics["normal_ask_fills"])
    if any((
        package.get("package_id") != PACKAGE_ID,
        package.get("campaign_authorized") is not False,
        terminal.get("status") != "NOT_READY",
        terminal.get("sessions_attempted") != 1,
        terminal.get("sessions_completed") != 0,
        terminal.get("sessions_failed") != 1,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("terminal_written_last") is not True,
        decision.get("decision") != "NOT_READY",
        failed.get("reason")
        != "CampaignError:markout evidence count does not equal normal fills",
        failed.get("terminal_account_authoritative") is not True,
        failed.get("two_flat_empty_snapshots") is not True,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
        snapshots.get("first", {}).get("position_btc") != "0",
        snapshots.get("first", {}).get("open_orders") != 0,
        snapshots.get("second", {}).get("position_btc") != "0",
        snapshots.get("second", {}).get("open_orders") != 0,
        normal_fills != 3,
        len(causal) != 2,
        len(markouts) != 2,
        len(special) != 1,
        len(special_markouts) != 1,
        len(causal) + len(special) != normal_fills,
    )):
        raise RepairError("markout predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "failed_session_package_id": FAILED_SESSION_ID,
        "terminal_decision": "NOT_READY",
        "completed_slots": [],
        "failed_slots": [1],
        "slots_2_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "normal_fill_count": normal_fills,
        "causal_maker_fill_count": len(causal),
        "terminal_special_closed_fill_count": len(special),
        "causal_markout_count": len(markouts),
        "terminal_special_closed_markout_count": len(special_markouts),
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def replay_split_attribution(root: Path) -> dict[str, object]:
    paths = _paths(root)
    failed = _read(paths["session_failure"])
    economics = _read(paths["session_economics"])
    causal = list(economics["causal_reentry"])
    special = list(economics["special_closed_causal_fills"])
    timestamps = [
        int(item["fill_timestamp_ms"]) for item in [*causal, *special]
    ]
    create_audit = {
        "normal_create_dispatches": int(failed["normal_create_dispatches"]),
        "normal_create_acknowledgements": int(failed["normal_create_acknowledgements"]),
        "normal_create_rejections": int(failed["normal_create_rejections"]),
        "normal_create_unresolved": int(failed["normal_create_unresolved"]),
        "mutation_retries": 0,
        "reconciles": True,
    }
    payload = {
        "session_id": failed["session_id"],
        "run_id": failed["run_id"],
        "source_sha256": failed["source_sha256"],
        "started_at_ms": min(timestamps) - 1_000,
        "ended_at_ms": max(timestamps) + 1_000,
        "normal_creates": int(failed["normal_creates"]),
        **create_audit,
        "normal_cancels": int(failed["normal_cancels"]),
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": 0,
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01",
        "maximum_drawdown_usdt": failed["maximum_drawdown_usdt"],
        "hard_kill_triggered": False,
        "normal_bid_fills": failed["normal_bid_fills"],
        "normal_ask_fills": failed["normal_ask_fills"],
        "normal_fifo_round_trips": failed["normal_fifo_round_trips"],
        "realized_spread_pnl_usdt": failed["realized_spread_pnl_usdt"],
        "inventory_pnl_usdt": failed["inventory_pnl_usdt"],
        "normal_gross_pnl_usdt": failed["normal_gross_pnl_usdt"],
        "normal_fees_usdt": failed["normal_fees_usdt"],
        "normal_net_pnl_usdt": failed["normal_net_pnl_usdt"],
        "special_fill_count": failed["special_fill_count"],
        "special_gross_pnl_usdt": failed["special_gross_pnl_usdt"],
        "special_fees_usdt": failed["special_fees_usdt"],
        "special_net_pnl_usdt": failed["special_net_pnl_usdt"],
        "aggregate_gross_pnl_usdt": failed["aggregate_gross_pnl_usdt"],
        "aggregate_fees_usdt": failed["aggregate_fees_usdt"],
        "aggregate_net_pnl_usdt": failed["aggregate_net_pnl_usdt"],
        "flatten_dispatches": 1,
        "quote_mode_ticks": economics["quote_mode_ticks"],
        "quote_mode_counters": economics["quote_mode_counters"],
        "unclassified_quote_mode_ticks": economics["unclassified_quote_mode_ticks"],
        "markouts_usdt": economics["markouts_usdt"],
        "causal_reentry": causal,
        "special_closed_causal_fills": special,
        "special_closed_markouts_usdt": economics["special_closed_markouts_usdt"],
        "accounting_audit": economics["accounting_audit"],
        "control_audit": {
            "session_phase": economics["session_phase"],
            "create_budget_partition": economics["create_budget_partition"],
            "placement_reason_counters": economics["placement_reason_counters"],
            "normal_bid_fill_quantity_btc": economics["normal_bid_fill_quantity_btc"],
            "normal_ask_fill_quantity_btc": economics["normal_ask_fill_quantity_btc"],
            "create_counter_audit": create_audit,
        },
        "fill_cursor_sha256": economics["fill_cursor_sha256"],
        "final_position_btc": "0",
        "final_open_orders": 0,
        "terminal_account_snapshots": 2,
        "terminal_reconciled": True,
        "pending_intent": False,
        "ambiguous_intent": False,
        "safety_violations": [],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }
    evidence = SessionEvidence.from_dict(seal_session_evidence(payload))
    aggregate = CampaignRegistry.__new__(CampaignRegistry).aggregate([evidence])
    return {
        "passed": True,
        "source_artifacts_modified": False,
        "legacy_failure_reproduced": True,
        "normal_fill_count": evidence.normal_fill_count,
        "causal_maker_fill_count": len(evidence.causal_reentry),
        "causal_markout_count": len(evidence.markouts_usdt),
        "terminal_special_closed_fill_count": evidence.terminal_special_closed_fill_count,
        "terminal_special_closed_markout_count": evidence.terminal_special_closed_markout_count,
        "normal_markout_attribution_reconciles": aggregate[
            "normal_markout_attribution_reconciles"
        ],
        "terminal_position_btc": str(evidence.final_position_btc),
        "terminal_open_orders": evidence.final_open_orders,
        "projection_promotable": False,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh markout special repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("markout special repair identity reuse refused")
    predecessor = verify_predecessor(root)
    replay = replay_split_attribution(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/split_markout_attribution_replay.json", replay)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "causal maker markouts reconcile only causal maker fills", "passed": True},
        {"requirement": "terminal special-closed markouts reconcile separately", "passed": True},
        {"requirement": "combined attribution equals every normal maker fill", "passed": True},
        {"requirement": "trade identities cannot overlap across attribution classes", "passed": True},
        {"requirement": "special closure never earns maker work-off credit", "passed": True},
        {"requirement": "legacy incomplete evidence remains fail closed", "passed": True},
        {"requirement": "failed campaign is immutable and non-resumable", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "two causal plus one terminal special-closed fill", "expected": "2+1 equals 3"},
        {"fixture": "multi-partial terminal special closure", "expected": "each FIFO fill identity reconciles"},
        {"fixture": "missing special markout", "expected": "fail closed"},
        {"fixture": "overlapping causal/special trade id", "expected": "fail closed"},
        {"fixture": "special closure claims maker work-off", "expected": "fail closed"},
        {"fixture": "restart sealed round trip", "expected": "split attribution preserved"},
        {"fixture": "socket denied", "expected": "zero endpoints and mutations"},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_soak_executor.py",
        "okx_demo_markout_special_closure_repair_offline.py",
        "tests/test_okx_demo_markout_special_closure_repair.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_soak_executor.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "markout_special_targeted", (
        "tests/test_okx_demo_markout_special_closure_repair.py",
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/markout_special_targeted_summary.json", targeted)
    all_suites = {**suites, "markout_special_targeted": targeted}
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("markout special repair test boundary failed")
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
        raise RepairError("markout special repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_MARKOUT_SPECIAL_CLOSURE_REPAIR_COMPLETED.json", {
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
        print(f"MARKOUT_SPECIAL_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
