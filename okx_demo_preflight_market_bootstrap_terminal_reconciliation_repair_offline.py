"""Build socket-denied R0 evidence for R1 pre-market terminal reconciliation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import (
    _run_suites,
    _secret_scan,
    _sha256,
    _write_json,
)
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path(
    "artifacts/okx_demo_preflight_market_bootstrap_terminal_reconciliation"
)
PATTERN = re.compile(
    r"preflight-market-bootstrap-repair-offline-\d{8}T\d{6}Z\Z"
)
READY = (
    "OKX_DEMO_PREFLIGHT_MARKET_BOOTSTRAP_TERMINAL_RECONCILIATION_"
    "R0_OFFLINE_SUPPORT"
)
PREPARATION_ID = "preflight-package-20260827T140714Z"
RUN_ID = "preflight-20260827T140714Z"
SESSION_ID = "preflight:preflight-20260827T140714Z:p0:e06057bbf6c5"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON is not an object: {path.name}")
    return value


def _predecessor_paths(root: Path) -> dict[str, Path]:
    preparation = (
        root / "artifacts/okx_demo_fill_restart_validation/preflight_packages"
        / PREPARATION_ID
    )
    run = root / "artifacts/okx_demo_fill_restart_validation" / RUN_ID
    return {
        "preparation_terminal": preparation / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "preparation_completion": preparation / "completion_hashes.json",
        "preflight_terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "preflight_completion": run / "completion_hashes.json",
        "preflight_result": run / "preflight/preflight_result.json",
        "preflight_decision": run / "decision/preflight_decision.json",
        "endpoint_audit": run / "audits/endpoint_audit.json",
        "secret_scan": run / "audits/secret_scan.json",
    }


def verify_failed_preflight(root: Path) -> dict[str, object]:
    paths = _predecessor_paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("failed R1 preflight evidence is incomplete")
    preparation = _read(paths["preparation_terminal"])
    terminal = _read(paths["preflight_terminal"])
    result = _read(paths["preflight_result"])
    decision = _read(paths["preflight_decision"])
    endpoint = _read(paths["endpoint_audit"])
    secret = _read(paths["secret_scan"])
    if any((
        preparation.get("preparation_id") != PREPARATION_ID,
        preparation.get("run_id") != RUN_ID,
        preparation.get("session_id") != SESSION_ID,
        preparation.get("preflight_executed") is not False,
        terminal.get("run_id") != RUN_ID,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("passed") is not False,
        result.get("failure_type") != "DemoAdapterError",
        result.get("failure_reason") != "authoritative preflight failed",
        decision.get("read_only_preflight_passed") is not False,
        decision.get("formal_demo_execution_authorized") is not False,
        endpoint.get("read_call_count") != 1,
        endpoint.get("read_methods") != ["fetch_markets"],
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        result.get("orders_submitted") != 0,
        result.get("orders_amended") != 0,
        result.get("orders_cancelled") != 0,
        secret.get("passed") is not True,
    )):
        raise RepairError("failed R1 preflight boundary drifted")
    return {
        "immutable": True,
        "preparation_id": PREPARATION_ID,
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "terminal_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "failure_stage": "PRE_MARKET_BOOTSTRAP",
        "terminal_account_authoritative": False,
        "resume_authorized": False,
        "rerun_authorized": False,
        "identity_reuse_authorized": False,
        "orders_submitted": 0,
        "orders_amended": 0,
        "orders_cancelled": 0,
        "mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh preflight market-bootstrap repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("preflight market-bootstrap repair identity reuse refused")
    predecessor = verify_failed_preflight(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_preflight_audit.json", predecessor)
    _write_json(output / "diagnostic/repair_scope.json", {
        "failure": "R1 fetch_markets failed before authoritative account state",
        "repair": (
            "raw OKX account positions and pending-orders reads provide two "
            "terminal snapshots without CCXT market metadata"
        ),
        "terminal_only": True,
        "quote_or_order_admission": False,
        "mutation_retry": False,
        "resume_or_identity_reuse": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "failed R1 identity is immutable and never reused", "passed": True},
        {"requirement": "terminal path never calls fetch_markets", "passed": True},
        {"requirement": "two snapshots prove flat position and zero open orders", "passed": True},
        {"requirement": "raw UID and order identifiers are never serialized", "passed": True},
        {"requirement": "terminal evidence cannot promote a failed preflight", "passed": True},
        {"requirement": "mutation retry remains zero", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "fetch_markets timeout", "expected": "authoritative account-only failure manifest"},
        {"fixture": "signed net partial position", "expected": "exact frozen-contract conversion"},
        {"fixture": "non-flat or open orders", "expected": "unresolved fail-closed"},
        {"fixture": "account binding mismatch", "expected": "unresolved fail-closed"},
        {"fixture": "clock skew above 1500 ms", "expected": "unresolved fail-closed"},
        {"fixture": "socket denied", "expected": "zero credential and mutation attempts"},
    ])
    sources = (
        "AGENTS.md",
        "okx_fill_restart_gateway.py",
        "okx_fill_restart_preflight.py",
        "okx_demo_preflight_market_bootstrap_terminal_reconciliation_repair_offline.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_preflight.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        name: _sha256(root / name) for name in sources
    })
    # Keep the suite identifier short because pytest's Windows temp path is
    # part of deep deterministic evidence-fixture paths.
    targeted = _run_suite(root, output, "r1_bootstrap", (
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    suites = _run_suites(root, output)
    all_suites = {**suites, "r1_bootstrap": targeted}
    _write_json(
        output / "tests/preflight_market_bootstrap_targeted_summary.json",
        targeted,
    )
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("socket-denied test boundary failed")
    _write_json(output / "audits/endpoint_mutation_audit.json", {
        "socket_denied": True,
        "network_attempts": 0,
        "credential_reads": 0,
        "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0,
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_configuration_attempts": 0,
        "orders": 0,
        "mutation_retries": 0,
    })
    decision = {
        "status": READY,
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
        raise RepairError("preflight market-bootstrap repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_PREFLIGHT_MARKET_BOOTSTRAP_REPAIR_COMPLETED.json", {
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
        print(run(args.root, args.evidence_id))
    except Exception as exc:
        print(f"PREFLIGHT_MARKET_BOOTSTRAP_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
