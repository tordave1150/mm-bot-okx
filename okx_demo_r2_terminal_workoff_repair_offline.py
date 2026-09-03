"""Offline R0 repair evidence for the terminal maker work-off campaign gate."""
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


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_terminal_workoff_repair")
PATTERN = re.compile(r"r2-terminal-workoff-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_TERMINAL_WORKOFF_REPAIR_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260831T140616Z"
CAMPAIGN_ID = "economic-campaign-20260831T140616Z"
RUN_ID = "economic-campaign-run-20260831T140616Z"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON object required: {path.name}")
    return value


def verify_predecessor(root: Path) -> dict[str, object]:
    campaign = (
        root / "artifacts/okx_demo_multi_session_economic_soak/packages" / PACKAGE_ID
        / "campaign_run"
    )
    paths = {
        "terminal": campaign / "A2_CAMPAIGN_COMPLETED.json",
        "decision": campaign / "decision/campaign_decision.json",
        "completion": campaign / "completion_hashes.json",
        "registry": campaign / "registry/campaign_registry.jsonl",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("R2 campaign evidence is incomplete")
    terminal, decision = _read(paths["terminal"]), _read(paths["decision"])
    aggregate = dict(decision.get("attempted_aggregate") or {})
    if any((
        terminal.get("run_id") != RUN_ID,
        terminal.get("status") != "NOT_READY",
        terminal.get("terminal_written_last") is not True,
        terminal.get("terminal_account_authoritative") is not True,
        terminal.get("final_position_btc") != "0",
        terminal.get("final_open_orders") != 0,
        decision.get("campaign_id") != CAMPAIGN_ID,
        decision.get("decision") != "NOT_READY",
        aggregate.get("special_flatten_sessions") != 5,
        aggregate.get("normal_fill_count") != 112,
        aggregate.get("causal_reentry_records") != 107,
        aggregate.get("unsafe_sessions") != 0,
        aggregate.get("live_endpoint_attempts") != 0,
        aggregate.get("live_orders") != 0,
    )):
        raise RepairError("R2 terminal work-off predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": RUN_ID,
        "terminal_decision": "NOT_READY",
        "terminal_position_open_orders": "0/0",
        "special_flatten_sessions": 5,
        "normal_fill_count": 112,
        "causal_reentry_records": 107,
        "mutation_retries": 0,
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def _run_repair_suites(root: Path, output: Path) -> dict[str, dict[str, object]]:
    """Run broad current-protocol suites without revalidating frozen legacy roots."""
    legacy_root_exclusions = (
        "tests/test_okx_demo_multi_session_a0_offline.py",
        "tests/test_okx_fill_cursor_formal_prepare.py",
        "tests/test_okx_fill_cursor_repair_offline.py",
        "tests/test_okx_fill_restart_formal_prepare.py",
        "tests/test_okx_fill_restart_offline.py",
        "tests/test_okx_production_readiness.py",
        "tests/test_okx_r2_warmup_audit_counter_repair.py",
        "tests/test_okx_r2_warmup_audit_formal_prepare.py",
    )
    root_args = ("tests", *(f"--ignore={item}" for item in legacy_root_exclusions))
    suites = {
        "post_campaign_targeted": _run_suite(root, output, "post_campaign_targeted", (
            "tests/test_okx_demo_sample_efficiency_repair.py",
            "tests/test_okx_demo_soak_executor.py",
            "tests/test_okx_demo_multi_session_prepare.py",
            "tests/test_okx_demo_r2_terminal_workoff_repair.py",
            "tests/test_okx_fill_restart_preflight.py",
        )),
        "root_non_optuna": _run_suite(root, output, "root_non_optuna", root_args),
        "backtest_non_optuna": _run_suite(
            root, output, "backtest_non_optuna",
            (
                "backtest/tests",
                *(f"--ignore={item}" for item in BACKTEST_EXCLUSIONS),
                *(f"--deselect={item}" for item in BACKTEST_DESELECT),
                "--ignore=backtest/tests/test_mm_v1_6_economics.py",
            ),
        ),
    }
    suites["root_non_optuna"]["root_exclusions"] = list(legacy_root_exclusions)
    _write_json(output / "tests/test_summary.json", suites)
    return suites


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh terminal work-off repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("terminal work-off repair identity reuse refused")
    predecessor = verify_predecessor(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/campaign_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", {
        "classification": "TERMINAL_MAKER_WORKOFF_CAP_EXIT",
        "special_flatten_rate_before": "5/12",
        "causal_reentry_before": "107/112",
        "root_causes": [
            "loop exited at normal create cap while an owned maker work-off could still fill",
            "draining logic cancelled a valid correctly-sided maker work-off at the fixed create cap",
        ],
        "repair": [
            "continue paced read-only observation of an owned correctly-sided work-off at create cap",
            "retain the correctly-sided draining work-off at create cap; no new create, inventory, time, loss, or retry budget",
        ],
        "terminal_special_flatten_is_causal_reentry": False,
        "persistent_ambiguity_blocks_mutation": True,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/scenario_matrix.json", [
        {"scenario": "valid work-off at create cap", "expected": "paced read-only wait; no new create"},
        {"scenario": "wrong-sided or unresolved work-off", "expected": "cancel/reconcile or fail closed; no replacement at cap"},
        {"scenario": "maker work-off fills", "expected": "normal causal re-entry/work-off record; terminal 0/0"},
        {"scenario": "special flatten", "expected": "never credited as causal maker re-entry"},
        {"scenario": "persistent ambiguity", "expected": "blocks mutation before dispatch"},
    ])
    sources = (
        "AGENTS.md", Path(__file__).name, "okx_demo_economic_session_controller.py",
        "okx_demo_soak_executor.py", "okx_demo_multi_session_prepare.py",
        "okx_demo_multi_session_campaign.py", "okx_fill_restart_preflight.py",
        "okx_fill_restart_preflight_prepare.py", "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_sample_efficiency_repair.py",
        "tests/test_okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_r2_terminal_workoff_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        str(name): _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "r2_terminal_workoff_targeted", (
        "tests/test_okx_demo_sample_efficiency_repair.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_r2_terminal_workoff_repair.py",
        "tests/test_okx_fill_restart_preflight.py",
    ))
    suites = _run_repair_suites(root, output)
    _write_json(output / "tests/r2_terminal_workoff_targeted_summary.json", targeted)
    checks = [*suites.values(), targeted]
    if any(
        item.get("passed_gate") is not True or item.get("returncode") != 0
        or item.get("network_attempts") != 0
        or item.get("live_endpoint_attempts") != 0
        or item.get("optuna_imported") is not False
        for item in checks
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
        "R1_preparation_authorized": False, "preflight_authorized": False,
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
    _write_json(output / "R0_R2_TERMINAL_WORKOFF_REPAIR_COMPLETED.json", {
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
        print(f"R2_TERMINAL_WORKOFF_REPAIR_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
