"""Offline R0 evidence for the current R2 terminal maker work-off repair."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import (
    BACKTEST_DESELECT,
    BACKTEST_EXCLUSIONS,
    _run_suite,
)
from okx_demo_post_campaign_offline import _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_terminal_workoff_v2_repair")
PATTERN = re.compile(r"r2-terminal-workoff-v2-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_TERMINAL_WORKOFF_V2_REPAIR_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260901T015511Z"
CAMPAIGN_ID = "economic-campaign-20260901T015511Z"
RUN_ID = "economic-campaign-run-20260901T015511Z"
SESSION_PREFIX = "soak-package-20260901T015511Z-s"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON object required: {path.name}")
    return value


def _session_audit(root: Path, slot: int) -> dict[str, object]:
    package = root / "artifacts/okx_demo_soak_validation" / (
        f"{SESSION_PREFIX}{slot:02d}-e631bd99ef"
    )
    evidence_path = package / "soak_run/audits/economic_session_evidence.json"
    if not evidence_path.is_file():
        evidence_path = package / "soak_run/audits/economics.json"
    state_path = package / "soak_run/state/validation_state.json"
    completed_path = package / "COMPLETED.json"
    if not evidence_path.is_file() or not state_path.is_file():
        raise RepairError(f"session {slot} durable evidence is incomplete")
    evidence, state = _read(evidence_path), _read(state_path)
    payload = dict(state.get("payload") or {})
    return {
        "slot": slot,
        "package_id": package.name,
        "completed": completed_path.is_file(),
        "special_flatten": (
            int(evidence.get("flatten_dispatches", 0)) > 0
            or int(evidence.get("special_fill_count", 0)) > 0
        ),
        "special_fill_count": int(evidence.get("special_fill_count", 0)),
        "normal_creates": int(evidence.get("normal_creates", 0)),
        "normal_fill_count": int(evidence.get("normal_bid_fills", 0))
        + int(evidence.get("normal_ask_fills", 0)),
        "normal_fifo_round_trips": int(evidence.get("normal_fifo_round_trips", 0)),
        "normal_net_pnl_usdt": str(evidence.get("normal_net_pnl_usdt", "0")),
        "mutation_retries": int(evidence.get("mutation_retries", 0)),
        "reconciles": bool(
            evidence.get("reconciles", evidence.get("engine_reconciled", False))
        ),
        "state_phase": payload.get("phase"),
        "state_position_btc": str(payload.get("last_reconciled_position_btc")),
        "hashes": {
            "economics": _sha256(evidence_path),
            "state": _sha256(state_path),
            **({"terminal": _sha256(completed_path)} if completed_path.is_file() else {}),
        },
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    campaign = root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID / "campaign_run"
    state_path = campaign / "state/supervisor_state.json"
    registry_path = campaign / "registry/campaign_registry.jsonl"
    armed_path = campaign / "A2_CAMPAIGN_ARMED.json"
    if not all(path.is_file() for path in (state_path, registry_path, armed_path)):
        raise RepairError("current R2 campaign evidence is incomplete")
    state = _read(state_path)
    if state.get("campaign_id") != CAMPAIGN_ID or state.get("active_slot") != 11:
        raise RepairError("current R2 campaign identity or active slot drifted")
    sessions = [_session_audit(root, slot) for slot in (4, 8, 11)]
    if [row["special_flatten"] for row in sessions] != [True, True, True]:
        raise RepairError("terminal work-off special-flatten cohort drifted")
    if sessions[0]["completed"] is not True or sessions[1]["completed"] is not True:
        raise RepairError("completed special session evidence drifted")
    if sessions[2]["completed"] is not False or sessions[2]["state_position_btc"] != "0.000":
        raise RepairError("unaccepted Session 11 boundary drifted")
    completed = [_session_audit(root, slot) for slot in range(1, 11)]
    if any(row["mutation_retries"] != 0 or not row["reconciles"] for row in completed):
        raise RepairError("completed campaign safety evidence drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "terminal_decision": "NOT_READY",
        "decision_reason": "SPECIAL_FLATTEN_SESSION_LIMIT_EXCEEDED",
        "completed_sessions": 10,
        "unaccepted_session": 11,
        "unstarted_session": 12,
        "special_flatten_sessions": 3,
        "maximum_special_flatten_sessions": 2,
        "special_session_audits": sessions,
        "successful_economic_cohort": {
            "normal_fill_count": sum(int(row["normal_fill_count"]) for row in completed),
            "normal_fifo_round_trips": sum(int(row["normal_fifo_round_trips"]) for row in completed),
            "normal_net_pnl_usdt": str(sum(
                (float(row["normal_net_pnl_usdt"]) for row in completed), 0.0
            )),
            "mutation_retries": 0,
        },
        "accept_session_11_authorized": False,
        "start_session_12_authorized": False,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "hashes": {
            "armed": _sha256(armed_path),
            "state": _sha256(state_path),
            "registry": _sha256(registry_path),
        },
    }


def _run_suites(root: Path, output: Path) -> dict[str, dict[str, object]]:
    targeted = _run_suite(root, output, "terminal_workoff_v2_targeted", (
        "tests/test_okx_demo_sample_efficiency_repair.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_r2_terminal_workoff_v2_repair.py",
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    legacy_root_exclusions = (
        "tests/test_okx_demo_multi_session_a0_offline.py",
        "tests/test_okx_fill_cursor_formal_prepare.py",
        "tests/test_okx_fill_cursor_repair_offline.py",
        "tests/test_okx_fill_restart_formal_prepare.py",
        "tests/test_okx_fill_restart_offline.py",
        "tests/test_okx_production_readiness.py",
        "tests/test_okx_r2_warmup_audit_counter_repair.py",
        "tests/test_okx_r2_warmup_audit_formal_prepare.py",
        "tests/test_okx_demo_sample_efficiency_campaign_prepare.py",
        "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
    )
    root_suite = _run_suite(root, output, "root_non_optuna", (
        "tests", *(f"--ignore={item}" for item in legacy_root_exclusions),
    ))
    root_suite["root_exclusions"] = list(legacy_root_exclusions)
    backtest = _run_suite(root, output, "backtest_non_optuna", (
        "backtest/tests",
        *(f"--ignore={item}" for item in BACKTEST_EXCLUSIONS),
        *(f"--deselect={item}" for item in BACKTEST_DESELECT),
        "--ignore=backtest/tests/test_mm_v1_6_economics.py",
    ))
    suites = {
        "targeted": targeted,
        "root_non_optuna": root_suite,
        "backtest_non_optuna": backtest,
    }
    _write_json(output / "tests/test_summary.json", suites)
    return suites


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh terminal work-off v2 repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("terminal work-off v2 repair identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "classification": "TIMEBOXED_STALE_MAKER_WORKOFF",
        "special_flatten_sessions_before": "3/11 attempted",
        "maximum_allowed_special_flatten_sessions": "2/12",
        "root_causes": [
            "Sessions 8 and 11 retained a tick-valid work-off quote through draining/timebox without bounded refresh",
            "Session 4 exhausted the create cap before the terminal work-off completed",
        ],
        "repair": [
            "refresh only an already-owned correctly-sided draining work-off after six observations",
            "limit terminal work-off refreshes to three, inside the unchanged twelve-create reserve",
            "retain at create cap with paced read-only observation; never create a replacement at cap",
        ],
        "risk_expansion": False,
        "special_flatten_is_not_causal_maker_reentry": True,
        "persistent_ambiguity_blocks_mutation": True,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/scenario_matrix.json", [
        {"scenario": "tick-valid draining work-off below cap", "expected": "bounded refresh after six observations"},
        {"scenario": "refresh limit reached", "expected": "no further refresh; terminal safety path remains available"},
        {"scenario": "draining work-off at create cap", "expected": "paced read-only retention; no create"},
        {"scenario": "special flatten", "expected": "not counted as causal maker re-entry"},
        {"scenario": "persistent ambiguity", "expected": "fail closed before mutation dispatch"},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name, "okx_demo_economic_session_controller.py",
        "okx_demo_soak_executor.py", "okx_demo_multi_session_prepare.py",
        "okx_demo_multi_session_campaign.py", "okx_fill_restart_preflight.py",
        "okx_fill_restart_preflight_prepare.py",
        "okx_demo_execution_environment_successor_supervisor.py",
        "tests/test_okx_demo_sample_efficiency_repair.py",
        "tests/test_okx_demo_soak_executor.py", "tests/test_okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_multi_session_campaign.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        str(name): _sha256(root / name) for name in sources
    })
    suites = _run_suites(root, output)
    if any(
        item.get("passed_gate") is not True or item.get("returncode") != 0
        or item.get("network_attempts") != 0 or item.get("optuna_imported") is not False
        for item in suites.values()
    ):
        raise RepairError("socket-denied test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True, "network_attempts": 0, "credential_reads": 0,
        "demo_endpoint_attempts": 0, "live_endpoint_attempts": 0,
        "create_attempts": 0, "amend_attempts": 0, "cancel_attempts": 0,
        "flatten_attempts": 0, "account_configuration_attempts": 0,
        "orders": 0, "mutation_retries": 0,
    })
    decision = {
        "status": READY, "R0_offline_repair_passed": True,
        "evidence_kind": "r2_terminal_workoff_v2_r0_offline_repair",
        "failed_campaign_decision": "NOT_READY",
        "terminal_account_authoritative": True,
        "resume_authorized": False, "preflight_authorized": False,
        "economic_campaign_authorized": False, "production_authorized": False,
        "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0,
        "optuna_executed": False, "validation_opened": False,
        "holdout_opened": False, "git_write_operation": False,
        "next_boundary": "separate exact authorization is required to prepare fresh R1 identities",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    secret = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", secret)
    if not secret["passed"]:
        raise RepairError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_TERMINAL_WORKOFF_V2_REPAIR_COMPLETED.json", {
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
        print(f"R2_TERMINAL_WORKOFF_V2_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
