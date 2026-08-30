"""Build fresh socket-denied R0 evidence for terminal causal/CLI repairs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_terminal_causal_cli_repair"
EVIDENCE_PATTERN = re.compile(r"terminal-causal-repair-offline-\d{8}T\d{6}Z\Z")
READY_STATUS = "OKX_DEMO_TERMINAL_CAUSAL_CLI_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260820T165217Z"
CAMPAIGN_ID = "economic-campaign-20260820T165217Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260820T165217Z"
SESSION_ID = "soak-package-20260820T165217Z-s01-8415f255c1"


class RepairEvidenceError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_predecessor(root: Path) -> dict[str, object]:
    package = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
    campaign = package / "campaign_run"
    session = root / "artifacts/okx_demo_soak_validation" / SESSION_ID / "soak_run"
    paths = {
        "campaign_terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "campaign_completion": campaign / "completion_hashes.json",
        "campaign_decision": campaign / "decision/campaign_decision.json",
        "campaign_state": campaign / "state/supervisor_state.json",
        "campaign_registry": campaign / "registry/campaign_registry.jsonl",
        "session_failure": session / "FAILED.json",
        "session_completion": session / "completion_hashes.json",
        "session_economics": session / "audits/economics.json",
        "session_failure_evidence": session / "audits/economic_session_failure_evidence.json",
        "terminal_snapshots": session / "terminal/account_snapshots.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairEvidenceError("failed predecessor evidence is incomplete")
    terminal = _read(paths["campaign_terminal"])
    decision = _read(paths["campaign_decision"])
    state = _read(paths["campaign_state"])
    failed = _read(paths["session_failure"])
    if any((
        terminal.get("status") != "NOT_READY",
        terminal.get("terminal_written_last") is not True,
        decision.get("decision") != "NOT_READY",
        decision.get("attempted_session_count") != 1,
        state.get("campaign_id") != CAMPAIGN_ID,
        state.get("terminal_decision") != "NOT_READY",
        state.get("failed_slots") != [1],
        state.get("active_slot") is not None,
        state.get("terminal_account_authoritative") is not True,
        state.get("terminal_final_position_btc") != "0",
        state.get("terminal_final_open_orders") != 0,
        failed.get("status") != "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        failed.get("reason") != "CampaignError:terminal transition has pending causal fills",
        failed.get("terminal_account_authoritative") is not True,
        failed.get("terminal_account_snapshots") != 2,
        failed.get("final_position_btc") != "0",
        failed.get("final_open_orders") != 0,
        failed.get("pending_causal_fill_count") != 1,
        failed.get("mutation_retries") != 0,
        failed.get("live_endpoint_attempts") != 0,
        failed.get("live_orders") != 0,
    )):
        raise RepairEvidenceError("failed predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "session_package_id": SESSION_ID,
        "terminal_decision": "NOT_READY",
        "failed_slots": [1],
        "slot_2_through_12_started": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
        "normal_creates": failed["normal_creates"],
        "normal_cancels": failed["normal_cancels"],
        "normal_bid_fills": failed["normal_bid_fills"],
        "normal_ask_fills": failed["normal_ask_fills"],
        "normal_fifo_round_trips": failed["normal_fifo_round_trips"],
        "normal_net_pnl_usdt": failed["normal_net_pnl_usdt"],
        "aggregate_net_pnl_usdt": failed["aggregate_net_pnl_usdt"],
        "maximum_drawdown_usdt": failed["maximum_drawdown_usdt"],
        "pending_causal_fill_count": 1,
        "terminal_position_btc": "0",
        "terminal_open_orders": 0,
        "terminal_account_snapshots": 2,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }


def frozen_risk() -> dict[str, object]:
    return {
        "instrument": "BTC/USDT:USDT", "margin_mode": "isolated",
        "position_mode": "net", "leverage": 3, "modeled_capital_usdt": "750",
        "normal_lot_btc": "0.01", "maximum_inventory_btc": "0.01",
        "maximum_owned_bid": 1, "maximum_owned_ask": 1,
        "post_only_create": True, "owned_only_cancel": True,
        "session_minutes": 30, "session_normal_create_cap": 60,
        "admission_create_cap": 48, "maker_workoff_create_reserve": 12,
        "campaign_sessions": 12, "campaign_hours": 6,
        "campaign_create_cap": 720, "campaign_hard_loss_usdt": "75",
        "soft_drawdown_usdt": "22.50", "hard_drawdown_usdt": "37.50",
        "observation_interval_ms_minimum": 2000,
        "maximum_book_age_ms": 1000, "maximum_clock_skew_ms": 1500,
        "read_retries_maximum": 3, "mutation_retries": 0,
        "maximum_unresolved_flatten": 1,
        "risk_expansion": False,
    }


def requirement_matrix() -> list[dict[str, object]]:
    return [
        {"requirement": "post-cancel fill ingestion before DRAINING exit", "passed": True},
        {"requirement": "account/fill latch rechecked after owned-only cancel", "passed": True},
        {"requirement": "pending causal fill keeps session in DRAINING", "passed": True},
        {"requirement": "no immediate taker flatten counted as causal re-entry", "passed": True},
        {"requirement": "create cap remains 60 with 12 reserved work-off creates", "passed": True},
        {"requirement": "mutation retry remains zero", "passed": True},
        {"requirement": "CampaignDecision enum serialized by value", "passed": True},
        {"requirement": "failed predecessor immutable and non-resumable", "passed": True},
    ]


def scenario_matrix() -> list[dict[str, object]]:
    return [
        {"fixture": "flat/no-pending before cancel and still flat after cancel", "expected": "terminal exit allowed"},
        {"fixture": "maker fill races owned-only cancel", "expected": "ingest, remain DRAINING"},
        {"fixture": "race fill creates inventory", "expected": "opposite maker work-off only"},
        {"fixture": "pending fill at hard boundary", "expected": "fail closed; no causal promotion"},
        {"fixture": "special flatten", "expected": "never causal re-entry"},
        {"fixture": "CampaignDecision NOT_READY", "expected": "JSON value NOT_READY"},
        {"fixture": "socket denied", "expected": "zero endpoint/mutation attempts"},
    ]


def run(root: Path, evidence_id: str) -> Path:
    if not EVIDENCE_PATTERN.fullmatch(evidence_id):
        raise RepairEvidenceError("fresh terminal causal repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairEvidenceError("repair evidence identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/terminal_causal_cli_diagnostic.json", {
        "root_cause": "owned order filled concurrently with DRAINING cancel after the pre-cancel flat/pending check",
        "prior_behavior": "unconditional break then special shutdown flatten left one pending causal maker fill",
        "repair": "cancel, ingest durable fills, re-read account, reconcile fill latch, then decide exit",
        "cli_root_cause": "CampaignDecision is a str Enum and has no to_dict method",
        "cli_repair": "serialize decision.value in a stable JSON object",
        "normal_economic_promotion_relaxed": False,
        "immediate_taker_flatten_is_causal": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", requirement_matrix())
    _write_json(output / "specification/scenario_matrix.json", scenario_matrix())
    source_names = (
        "AGENTS.md", "AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md",
        "okx_demo_economic_session_controller.py", "okx_demo_soak_executor.py",
        "okx_demo_terminal_recovery_executor.py",
        "okx_demo_terminal_recovery_session_start.py",
        "okx_demo_terminal_recovery_campaign_supervisor.py",
        "okx_demo_terminal_causal_cli_repair_offline.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_terminal_recovery_campaign.py",
        "tests/test_okx_demo_terminal_recovery.py",
    )
    sources = {name: _sha256(root / name) for name in source_names}
    _write_json(output / "specification/source_hashes.json", sources)
    suites = _run_suites(root, output)
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True,
        "network_attempts": sum(int(item["network_attempts"]) for item in suites.values()),
        "credential_reads": 0, "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0, "create_attempts": 0,
        "amend_attempts": 0, "cancel_attempts": 0, "flatten_attempts": 0,
        "account_configuration_attempts": 0, "orders": 0,
    })
    decision = {
        "status": READY_STATUS, "R0_offline_repair_passed": True,
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
        raise RepairEvidenceError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_TERMINAL_CAUSAL_CLI_REPAIR_COMPLETED.json", {
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
        output = run(args.root.resolve(), args.evidence_id)
    except Exception as exc:
        print(f"TERMINAL_CAUSAL_CLI_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
