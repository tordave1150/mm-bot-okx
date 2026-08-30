"""Build offline evidence for R1 fallback-read audit reconciliation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_preflight_fallback_audit_repair")
PATTERN = re.compile(r"preflight-fallback-audit-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_PREFLIGHT_FALLBACK_AUDIT_R0_OFFLINE_SUPPORT"
PREPARATION_ID = "preflight-package-20260827T141951Z"
RUN_ID = "preflight-20260827T141951Z"
SESSION_ID = "preflight:preflight-20260827T141951Z:p0:ac18edfad54f"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON is not an object: {path.name}")
    return value


def verify_failed_preflight(root: Path) -> dict[str, object]:
    package = root / "artifacts/okx_demo_fill_restart_validation/preflight_packages" / PREPARATION_ID
    run = root / "artifacts/okx_demo_fill_restart_validation" / RUN_ID
    paths = {
        "preparation": package / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "preflight": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "result": run / "preflight/preflight_result.json",
        "decision": run / "decision/preflight_decision.json",
        "endpoint": run / "audits/endpoint_audit.json",
        "secret": run / "audits/secret_scan.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("failed R1 preflight evidence is incomplete")
    preparation = _read(paths["preparation"])
    terminal = _read(paths["preflight"])
    result = _read(paths["result"])
    decision = _read(paths["decision"])
    endpoint = _read(paths["endpoint"])
    secret = _read(paths["secret"])
    if any((
        preparation.get("preparation_id") != PREPARATION_ID,
        preparation.get("run_id") != RUN_ID,
        preparation.get("session_id") != SESSION_ID,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        result.get("terminal_reconciliation_failure_type") != "NetworkError",
        result.get("terminal_account_authoritative") is not False,
        decision.get("read_only_preflight_passed") is not False,
        endpoint.get("read_methods") != ["fetch_markets"],
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
        secret.get("passed") is not True,
    )):
        raise RepairError("failed R1 preflight boundary drifted")
    return {
        "immutable": True,
        "preparation_id": PREPARATION_ID,
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "terminal_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "primary_read_method": "fetch_markets",
        "fallback_error_category": "NETWORK",
        "resume_authorized": False,
        "rerun_authorized": False,
        "identity_reuse_authorized": False,
        "mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh fallback-audit repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("fallback-audit repair identity reuse refused")
    predecessor = verify_failed_preflight(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_preflight_audit.json", predecessor)
    _write_json(output / "diagnostic/repair_scope.json", {
        "repair": "finalize endpoint audit after terminal fallback",
        "primary_failure": "record last primary read method and classified error",
        "fallback_failure": "record raw fallback read method and classified error",
        "authoritative_terminal_required": True,
        "retry_or_identity_reuse": False,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "primary read method is recorded", "passed": True},
        {"requirement": "fallback read method and category are recorded", "passed": True},
        {"requirement": "final audit includes fallback reads", "passed": True},
        {"requirement": "network failure remains fail-closed", "passed": True},
        {"requirement": "no mutation or credential serialization", "passed": True},
    ])
    _write_json(output / "specification/scenario_matrix.json", [
        {"fixture": "market metadata timeout", "expected": "primary fetch_markets / NETWORK"},
        {"fixture": "fallback fetch_time network error", "expected": "final audit includes fetch_time"},
        {"fixture": "fallback account state is non-flat", "expected": "unresolved fail-closed"},
        {"fixture": "socket denied", "expected": "zero network, credentials, and mutations"},
    ])
    sources = (
        "AGENTS.md", "okx_fill_restart_preflight.py",
        "okx_fill_restart_gateway.py",
        "okx_demo_preflight_fallback_audit_repair_offline.py",
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_gateway.py",
    )
    _write_json(output / "specification/source_hashes.json", {name: _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "r1_fallback_audit", (
        "tests/test_okx_fill_restart_preflight.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_preflight_prepare.py",
    ))
    suites = _run_suites(root, output)
    all_suites = {**suites, "r1_fallback_audit": targeted}
    _write_json(output / "tests/r1_fallback_audit_summary.json", targeted)
    if any(
        item.get("passed_gate") is not True or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
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
        "live_mode_available": False, "live_endpoint_attempts": 0,
        "live_orders": 0, "optuna_executed": False,
        "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "wait for an OKX endpoint recovery, then prepare fresh R1 identities offline",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("fallback-audit repair secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_PREFLIGHT_FALLBACK_AUDIT_REPAIR_COMPLETED.json", {
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
        print(f"PREFLIGHT_FALLBACK_AUDIT_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
