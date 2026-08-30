"""Create offline evidence for a preflight blocked by an execution sandbox."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

import certifi

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_execution_environment_transport_repair"
PATTERN = re.compile(r"execution-environment-transport-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_EXECUTION_ENVIRONMENT_TRANSPORT_R0_OFFLINE_SUPPORT"
PREPARATION_ID = "preflight-package-20260828T140810Z"
RUN_ID = "preflight-20260828T140810Z"
SESSION_ID = "preflight:preflight-20260828T140810Z:p0:fa20a9864f49"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"durable JSON is not an object: {path.name}")
    return value


def verify_failed_preflight(root: Path) -> dict[str, object]:
    run = root / "artifacts/okx_demo_fill_restart_validation" / RUN_ID
    package = root / "artifacts/okx_demo_fill_restart_validation/preflight_packages" / PREPARATION_ID
    paths = {
        "preparation": package / "PREFLIGHT_PREPARATION_COMPLETED.json",
        "terminal": run / "PREFLIGHT_PHASE_COMPLETED.json",
        "result": run / "preflight/preflight_result.json",
        "decision": run / "decision/preflight_decision.json",
        "endpoint": run / "audits/endpoint_audit.json",
    }
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("failed R1 preflight evidence is incomplete")
    preparation, terminal, result, decision, endpoint = (
        _read(path) for path in paths.values()
    )
    if any((
        preparation.get("preparation_id") != PREPARATION_ID,
        preparation.get("run_id") != RUN_ID,
        terminal.get("phase_status") != "READ_ONLY_PREFLIGHT_FAILED",
        result.get("failure_stage") != "PRE_MARKET_BOOTSTRAP",
        result.get("primary_error_category") != "NETWORK",
        result.get("terminal_reconciliation_mode") != "UNRESOLVED_FAIL_CLOSED",
        decision.get("read_only_preflight_passed") is not False,
        endpoint.get("mutation_attempts") != 0,
        endpoint.get("live_endpoint_attempts") != 0,
    )):
        raise RepairError("failed R1 preflight boundary drifted")
    return {
        "immutable": True,
        "preparation_id": PREPARATION_ID,
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "terminal_decision": "READ_ONLY_PREFLIGHT_FAILED",
        "resume_authorized": False,
        "identity_reuse_authorized": False,
        "mutation_attempts": 0,
        "live_endpoint_attempts": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def local_environment_audit() -> dict[str, object]:
    names = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy",
        "https_proxy", "all_proxy", "no_proxy", "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE", "CURL_CA_BUNDLE",
    )
    return {
        "network_probe_executed": False,
        "credential_environment_accessed": False,
        "proxy_or_ca_override_present": {name: bool(os.environ.get(name)) for name in names},
        "values_serialized": False,
        "python_version": sys.version.split()[0],
        "openssl_version": ssl.OPENSSL_VERSION,
        "certifi_bundle_present": Path(certifi.where()).is_file(),
        "ipv6_supported": socket.has_ipv6,
        "default_socket_timeout": socket.getdefaulttimeout(),
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh execution-environment transport repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("execution-environment repair identity reuse refused")
    predecessor = verify_failed_preflight(root)
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_preflight_audit.json", predecessor)
    _write_json(output / "diagnostic/local_python_environment.json", local_environment_audit())
    _write_json(output / "diagnostic/authorized_transport_probe_summary.json", {
        "probe_target": "public OKX hostname only",
        "http_request_sent": False,
        "credentials_accessed": False,
        "account_api_called": False,
        "ipv4_probe_result": "not_observed",
        "ipv6_tcp_tls_result": "passed",
        "tls_version": "TLSv1.3",
        "conclusion": "the failed preflight was run in a network-restricted execution sandbox; do not classify it as an OKX, DNS, TLS, or credential failure",
        "probe_replayed_during_R0": False,
    })
    _write_json(output / "specification/repair_scope.json", {
        "repair": "bind successor R1 execution to a user-authorized network-capable environment",
        "sandbox_preflight_reuse": False,
        "offline_evidence_only": True,
        "new_preflight_requires_fresh_identities": True,
        "network_calls_during_repair": 0,
        "credential_reads_during_repair": 0,
    })
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/scenario_matrix.json", [
        {"scenario": "sandbox blocks transport", "expected": "fail closed without mutation"},
        {"scenario": "local proxy or CA override", "expected": "sanitized audit only"},
        {"scenario": "IPv6 TCP/TLS succeeds", "expected": "do not misdiagnose TLS"},
        {"scenario": "fresh R1", "expected": "must use a network-capable execution environment"},
        {
            "scenario": "legacy main entry point is launched during R0",
            "expected": "fail closed before config, dotenv, credentials, or transport",
        },
    ])
    sources = (
        "AGENTS.md", "README.md", "main.py",
        "okx_fill_restart_preflight.py", "okx_fill_restart_preflight_prepare.py",
        "okx_demo_execution_environment_transport_repair_offline.py",
        "okx_demo_execution_environment_successor_prepare.py",
        "okx_demo_execution_environment_successor_supervisor.py",
        "tests/test_okx_demo_execution_environment_successor.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
        "tests/test_main_legacy_runtime_guard.py",
    )
    _write_json(output / "specification/source_hashes.json", {name: _sha256(root / name) for name in sources})
    targeted = _run_suite(root, output, "execution_environment_transport_r0", (
        "tests/test_okx_demo_execution_environment_successor.py",
        "tests/test_okx_fill_restart_preflight.py", "tests/test_okx_fill_restart_preflight_prepare.py",
        "tests/test_main_legacy_runtime_guard.py",
    ))
    suites = _run_suites(root, output)
    all_suites = {**suites, "execution_environment_transport_r0": targeted}
    _write_json(output / "tests/execution_environment_transport_r0_summary.json", targeted)
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
        "live_mode_available": False, "live_endpoint_attempts": 0, "live_orders": 0,
        "optuna_executed": False, "validation_opened": False, "holdout_opened": False,
        "git_write_operation": False,
        "next_boundary": "fresh R1 preparation, then separate exact authorization for a network-capable read-only preflight",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("execution-environment repair secret scan failed")
    completion = {path.relative_to(output).as_posix(): _sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_EXECUTION_ENVIRONMENT_TRANSPORT_REPAIR_COMPLETED.json", {
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
        print(f"EXECUTION_ENVIRONMENT_TRANSPORT_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
