"""Prepare a fresh post-terminal-recovery R2 campaign entirely offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import okx_demo_multi_session_prepare as base
import okx_demo_sample_efficiency_campaign_prepare as previous
from okx_demo_soak_failure_injection_offline import _run_suite
from okx_fill_restart_formal_prepare import (
    _OfflineSocketGuard,
    _artifact_secret_scan,
    verify_preflight_evidence,
)
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_preflight import (
    MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
    verify_offline_evidence,
)
from okx_fill_restart_validation import canonical_sha256


EVIDENCE_KIND = "r2_post_start_terminal_recovery_offline_repair"
PROTOCOL_ID = "okx-demo-terminal-recovery-economic-campaign-v1"
EXECUTION_SOURCE = "okx_demo_terminal_recovery_campaign_supervisor.py"
SOURCE_FILES = (
    "okx_demo_terminal_recovery_campaign_prepare.py",
    EXECUTION_SOURCE,
    "okx_demo_terminal_recovery_session_start.py",
    "tests/test_okx_demo_terminal_recovery_campaign.py",
)
TARGETED_TESTS = (
    *previous.TARGETED_TESTS,
    "tests/test_okx_demo_terminal_recovery.py",
    "tests/test_okx_demo_terminal_recovery_campaign.py",
)


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = previous._source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise base.A2PackageError(f"terminal-recovery R2 source missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _verify_r1(
    root: Path, *, preflight_run_id: str, evidence_id: str
) -> dict[str, object]:
    audit = verify_preflight_evidence(root, preflight_run_id)
    output = root / "artifacts/okx_demo_fill_restart_validation" / preflight_run_id
    result = json.loads((output / "preflight/preflight_result.json").read_text(encoding="utf-8"))
    spec = json.loads((output / "specification/preflight_spec.json").read_text(encoding="utf-8"))
    predecessor = json.loads((output / "predecessor/offline_evidence_audit.json").read_text(encoding="utf-8"))
    hosts = list(dict(result.get("transport_audit") or {}).get("endpoint_hosts") or [])
    bound_id = predecessor.get("repair_id") or predecessor.get("offline_run_id")
    if any((
        audit.get("passed") is not True,
        audit.get("run_id") != preflight_run_id,
        spec.get("protocol_id") != MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
        predecessor.get("evidence_kind") != EVIDENCE_KIND,
        bound_id != evidence_id,
        hosts != ["www.okx.com"],
        result.get("passed") is not True,
        result.get("snapshots_consistent") is not True,
        result.get("state_resolved") is not True,
        result.get("mutation_attempts") != 0,
        result.get("orders_submitted") != 0,
        result.get("orders_amended") != 0,
        result.get("orders_cancelled") != 0,
        result.get("live_endpoint_attempts") != 0,
        result.get("live_orders") != 0,
        result.get("initial_snapshot", {}).get("position_btc") != 0.0,
        result.get("initial_snapshot", {}).get("open_orders") != 0,
        result.get("verified_snapshot", {}).get("position_btc") != 0.0,
        result.get("verified_snapshot", {}).get("open_orders") != 0,
    )):
        raise base.A2PackageError("terminal-recovery R1 is not eligible for R2")
    return {
        **audit,
        "r0_evidence_id": evidence_id,
        "repair_evidence_id": evidence_id,
        "protocol_id": spec["protocol_id"],
        "endpoint_hosts": hosts,
        "zero_mutation_verified": True,
    }


def _failed_predecessor_audit(root: Path, evidence_id: str) -> dict[str, object]:
    evidence_root = root / "artifacts/okx_demo_terminal_recovery_repair" / evidence_id
    frozen = json.loads((evidence_root / "predecessor/failed_execution_audit.json").read_text(encoding="utf-8"))
    package_id = str(frozen["package_id"])
    session_id = str(frozen["session_package_id"])
    package = root / base.CAMPAIGN_ARTIFACT_ROOT / package_id
    session = root / base.SESSION_ARTIFACT_ROOT / session_id
    paths = {
        "package_terminal": package / "A2_PACKAGE_COMPLETED.json",
        "package_specification": package / "specification/campaign_package_spec.json",
        "campaign_arm_marker": package / "campaign_run/A2_CAMPAIGN_ARMED.json",
        "campaign_state": package / "campaign_run/state/supervisor_state.json",
        "campaign_events": package / "campaign_run/streams/supervisor_events.jsonl",
        "session_failure": session / "soak_run/FAILED.json",
        "session_failure_evidence": session / "soak_run/audits/economic_session_failure_evidence.json",
        "session_completion": session / "soak_run/completion_hashes.json",
    }
    expected = dict(frozen.get("hashes") or {})
    failures = [name for name, path in paths.items() if expected.get(name) != _sha256(path)]
    if any((
        failures,
        frozen.get("immutable") is not True,
        frozen.get("resume_authorized") is not False,
        frozen.get("flatten_retry_authorized") is not False,
        frozen.get("slot_2_through_12_started") is not False,
    )):
        raise base.A2PackageError("failed post-start predecessor binding drifted")
    return {
        "package_id": package_id,
        "campaign_run_id": frozen["campaign_run_id"],
        "session_package_id": session_id,
        "hashes": expected,
        "immutable_failed_post_start": True,
        "campaign_decision": "NOT_READY",
        "arm_marker_exists": True,
        "resume_authorized": False,
        "flatten_retry_authorized": False,
        "slot_2_through_12_started": False,
    }


def _write_terminal_recovery_session_package(**kwargs: object) -> dict[str, object]:
    audit = base._write_session_package(**kwargs)
    root = Path(kwargs["root"])
    output = root / base.SESSION_ARTIFACT_ROOT / str(audit["package_id"])
    spec_path = output / "specification/soak_package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["execution_source"] = "okx_demo_terminal_recovery_session_start.py"
    spec["ambiguous_flatten_mutation_retry_attempts"] = 0
    spec["two_snapshot_terminal_reconciliation_required"] = True
    canonical = dict(spec)
    canonical.pop("specification_sha256", None)
    spec["specification_sha256"] = canonical_sha256(canonical)
    _write_json(spec_path, spec)
    completion = base._completion_hashes(output, "SOAK_PACKAGE_COMPLETED.json")
    _write_json(output / "completion_hashes.json", completion)
    terminal_path = output / "SOAK_PACKAGE_COMPLETED.json"
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["completion_files_checked"] = len(completion)
    terminal["completion_hashes_sha256"] = _sha256(output / "completion_hashes.json")
    _write_json(terminal_path, terminal)
    return {
        **audit,
        "completion_hashes_sha256": terminal["completion_hashes_sha256"],
        "terminal_sha256": _sha256(terminal_path),
    }


def prepare(
    root: Path,
    *,
    evidence_id: str,
    preflight_run_id: str,
    run_suite: Callable[..., dict[str, object]] = _run_suite,
) -> tuple[Path, dict[str, object]]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        repair = verify_offline_evidence(root, evidence_id)
        preflight = _verify_r1(root, preflight_run_id=preflight_run_id, evidence_id=evidence_id)
        if any((
            repair.get("passed") is not True,
            repair.get("evidence_kind") != EVIDENCE_KIND,
            repair.get("failed_campaign_decision") != "NOT_READY",
            repair.get("account_confirmation_is_exchange_authoritative") is not False,
        )):
            raise base.A2PackageError("terminal-recovery evidence is not eligible for R2")
        failed_audit = _failed_predecessor_audit(root, evidence_id)
        sources = _source_hashes(root)
        source_manifest_sha256 = canonical_sha256(sources)
        identifiers = base._identifiers(
            source_manifest_sha256=source_manifest_sha256,
            preflight_completion_sha256=str(preflight["completion_hashes_sha256"]),
        )
        output = root / base.CAMPAIGN_ARTIFACT_ROOT / str(identifiers["package_id"])
        run_output = root / base.CAMPAIGN_ARTIFACT_ROOT / str(identifiers["run_id"])
        if output.exists() or run_output.exists():
            raise base.A2PackageError("fresh terminal-recovery R2 identity collision")
        output.mkdir(parents=True)
        token = str(identifiers["campaign_arm_token"])
        slots = list(identifiers["session_slots"])
        spec: dict[str, object] = {
            "schema_version": 3,
            "protocol_id": PROTOCOL_ID,
            "execution_source": EXECUTION_SOURCE,
            "child_execution_source": "okx_demo_terminal_recovery_session_start.py",
            "preparation_source": Path(__file__).name,
            "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"],
            "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "campaign_arm_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "campaign_arm_token_serialized": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "r0_evidence_id": evidence_id,
            "a0_evidence_id": evidence_id,
            "repair_evidence_id": evidence_id,
            "preflight_run_id": preflight_run_id,
            "failed_predecessor_package_id": failed_audit["package_id"],
            "failed_predecessor_campaign_run_id": failed_audit["campaign_run_id"],
            "failed_predecessor_session_package_id": failed_audit["session_package_id"],
            "source_manifest_sha256": source_manifest_sha256,
            "r0_completion_sha256": repair["completion_hashes_sha256"],
            "preflight_completion_sha256": preflight["completion_hashes_sha256"],
            "market_fingerprint": preflight["market_fingerprint"],
            "market_spec": preflight["market_spec"],
            "campaign_limits": base.CampaignLimits().to_dict(),
            "session_risk_budget": base._risk_budget(),
            "session_slots": slots,
            "session_count": len(slots),
            "sessions_must_be_sequential": True,
            "single_instance_lease_required": True,
            "two_snapshot_terminal_reconciliation_required": True,
            "mutation_retry_attempts": 0,
            "campaign_authorized": False,
            "campaign_executed": False,
            "execution_marker_created": False,
            "network_authorized_during_freeze": False,
            "orders_authorized_during_freeze": False,
            "production_authorized": False,
        }
        spec["specification_sha256"] = canonical_sha256(spec)
        _write_json(output / "predecessor/r0_evidence_audit.json", repair)
        _write_json(output / "predecessor/preflight_evidence_audit.json", preflight)
        _write_json(output / "predecessor/failed_post_start_package_audit.json", failed_audit)
        _write_json(output / "specification/source_hashes.json", sources)
        _write_json(output / "specification/campaign_package_spec.json", spec)
        child_audits = [
            _write_terminal_recovery_session_package(
                root=root, campaign_output=output, campaign_spec=spec,
                slot=dict(slot), sources=sources, a0=repair, preflight=preflight,
            ) for slot in slots
        ]
        _write_json(output / "specification/session_package_audits.json", {"count": len(child_audits), "packages": child_audits})
        targeted = run_suite(root, output, "r2_tr_pkg", TARGETED_TESTS)
        _write_json(output / "tests/test_summary.json", {"r2_tr_pkg": targeted})
        if targeted.get("passed_gate") is not True:
            raise base.A2PackageError("terminal-recovery R2 package regressions failed")
        if _source_hashes(root) != sources:
            raise base.A2PackageError("terminal-recovery R2 source changed during freeze")
        _write_json(output / "audits/network_mutation_audit.json", {
            "socket_denied": True, "network_attempts": len(guard.attempts),
            "credential_accesses": 0, "okx_requests": 0, "preflight_attempts": 0,
            "economic_campaign_attempts": 0, "economic_session_attempts": 0,
            "orders_submitted": 0, "orders_amended": 0, "orders_cancelled": 0,
            "flatten_dispatches": 0, "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0, "live_orders": 0,
        })
        scan = _artifact_secret_scan(output)
        child_scan = {item["package_id"]: _artifact_secret_scan(root / base.SESSION_ARTIFACT_ROOT / str(item["package_id"])) for item in child_audits}
        _write_json(output / "audits/secret_scan.json", {
            "passed": bool(scan["passed"] and all(v["passed"] for v in child_scan.values())),
            "campaign_package": scan, "session_packages": child_scan,
            "credential_environment_accessed": False,
        })
        decision = {
            "status": base.READY_STATUS, "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"], "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "repair_evidence_id": evidence_id, "preflight_run_id": preflight_run_id,
            "session_packages_prepared": len(child_audits), "campaign_authorized": False,
            "campaign_executed": False, "network_attempts": 0,
            "orders_submitted": 0, "orders_amended": 0, "orders_cancelled": 0,
            "production_authorized": False, "live_mode_available": False,
            "live_endpoint_attempts": 0, "live_orders": 0, "optuna_executed": False,
            "validation_opened": False, "holdout_opened": False,
            "git_write_operation": False,
            "next_boundary": "separate exact R2 campaign authorization",
        }
        _write_json(output / "decision/a2_package_decision.json", decision)
        _write_text(output / "decision/a2_package_decision.md", f"# R2 terminal-recovery campaign package\n\n- Package: `{identifiers['package_id']}`\n- Sessions: `12`\n- Network/order execution: `0 / 0`\n- Exact R2 authorization required separately.\n")
        completion = base._completion_hashes(output, "A2_PACKAGE_COMPLETED.json")
        _write_json(output / "completion_hashes.json", completion)
        terminal = {
            **decision, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": source_manifest_sha256,
            "campaign_arm_token_sha256": spec["campaign_arm_token_sha256"],
            "campaign_arm_token_serialized": False, "terminal_written_last": True,
        }
        _write_json(output / "A2_PACKAGE_COMPLETED.json", terminal)
    if guard.attempts:
        raise base.A2PackageError("network attempt blocked during terminal-recovery freeze")
    public = {k: v for k, v in identifiers.items() if k not in {"campaign_arm_token", "session_slots"}}
    public["campaign_arm_token"] = token
    public["session_slots"] = slots
    return output, public


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-evidence-id", required=True)
    parser.add_argument("--preflight-run-id", required=True)
    args = parser.parse_args()
    try:
        output, identifiers = prepare(args.root, evidence_id=args.repair_evidence_id, preflight_run_id=args.preflight_run_id)
    except Exception as exc:
        print(f"R2_PACKAGE_PREPARATION_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
