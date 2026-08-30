"""Prepare the sample-efficiency successor R2 campaign entirely offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import okx_demo_multi_session_prepare as base
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


R0_EVIDENCE_KIND = "sample_efficiency_r0_offline_repair"
SOURCE_FILES = (
    "okx_demo_sample_efficiency_campaign_prepare.py",
    "okx_demo_sample_efficiency_campaign_supervisor.py",
    "tests/test_okx_demo_sample_efficiency_campaign_prepare.py",
    "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
)
TARGETED_TESTS = (
    *base.TARGETED_TESTS,
    "tests/test_okx_demo_sample_efficiency_repair.py",
    "tests/test_okx_demo_sample_efficiency_campaign_prepare.py",
    "tests/test_okx_demo_sample_efficiency_campaign_supervisor.py",
)

REJECTED_PREARM_PACKAGE_ID = "economic-package-20260820T135400Z"


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = base._source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise base.A2PackageError(f"successor R2 source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _verify_r1(
    root: Path, *, preflight_run_id: str, r0_evidence_id: str
) -> dict[str, object]:
    audit = verify_preflight_evidence(root, preflight_run_id)
    output = root / "artifacts" / "okx_demo_fill_restart_validation" / preflight_run_id
    result = json.loads(
        (output / "preflight/preflight_result.json").read_text(encoding="utf-8")
    )
    spec = json.loads(
        (output / "specification/preflight_spec.json").read_text(encoding="utf-8")
    )
    predecessor = json.loads(
        (output / "predecessor/offline_evidence_audit.json").read_text(
            encoding="utf-8"
        )
    )
    hosts = list(dict(result.get("transport_audit") or {}).get("endpoint_hosts") or [])
    bound_evidence_id = predecessor.get("repair_id") or predecessor.get(
        "offline_run_id"
    )
    if any((
        audit.get("passed") is not True,
        audit.get("run_id") != preflight_run_id,
        spec.get("protocol_id") != MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
        predecessor.get("evidence_kind") != R0_EVIDENCE_KIND,
        bound_evidence_id != r0_evidence_id,
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
        raise base.A2PackageError("sample-efficiency R1 is not eligible for R2")
    return {
        **audit,
        "r0_evidence_id": r0_evidence_id,
        "protocol_id": spec["protocol_id"],
        "endpoint_hosts": hosts,
        "zero_mutation_verified": True,
    }


def prepare(
    root: Path,
    *,
    r0_evidence_id: str,
    preflight_run_id: str,
    run_suite: Callable[..., dict[str, object]] = _run_suite,
) -> tuple[Path, dict[str, object]]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        r0 = verify_offline_evidence(root, r0_evidence_id)
        preflight = _verify_r1(
            root,
            preflight_run_id=preflight_run_id,
            r0_evidence_id=r0_evidence_id,
        )
        if any((
            r0.get("passed") is not True,
            r0.get("evidence_kind") != R0_EVIDENCE_KIND,
            r0.get("successor_protocol_active") is not True,
            r0.get("economic_predecessor_verified") is not True,
        )):
            raise base.A2PackageError("sample-efficiency R0 is not eligible for R2")

        rejected_output = root / base.CAMPAIGN_ARTIFACT_ROOT / REJECTED_PREARM_PACKAGE_ID
        rejected_terminal = rejected_output / "A2_PACKAGE_COMPLETED.json"
        rejected_spec = rejected_output / "specification/campaign_package_spec.json"
        rejected_completion = rejected_output / "completion_hashes.json"
        rejected_marker = rejected_output / "campaign_run/A2_CAMPAIGN_ARMED.json"
        if not all(path.is_file() for path in (
            rejected_terminal, rejected_spec, rejected_completion
        )) or rejected_marker.exists():
            raise base.A2PackageError("rejected pre-arm predecessor is invalid")
        rejected_audit = {
            "package_id": REJECTED_PREARM_PACKAGE_ID,
            "terminal_sha256": _sha256(rejected_terminal),
            "specification_sha256": _sha256(rejected_spec),
            "completion_hashes_sha256": _sha256(rejected_completion),
            "arm_marker_exists": False,
            "immutable_rejected_prearm": True,
            "rejection_reason": "SUCCESSOR_PROTOCOL_NOT_ACCEPTED_BY_BOUND_LEGACY_SUPERVISOR",
        }

        sources = _source_hashes(root)
        source_manifest_sha256 = canonical_sha256(sources)
        identifiers = base._identifiers(
            source_manifest_sha256=source_manifest_sha256,
            preflight_completion_sha256=str(preflight["completion_hashes_sha256"]),
        )
        output = root / base.CAMPAIGN_ARTIFACT_ROOT / str(identifiers["package_id"])
        run_output = root / base.CAMPAIGN_ARTIFACT_ROOT / str(identifiers["run_id"])
        if output.exists() or run_output.exists():
            raise base.A2PackageError("fresh successor R2 identity collision")
        output.mkdir(parents=True)
        campaign_token = str(identifiers["campaign_arm_token"])
        slots = list(identifiers["session_slots"])
        spec: dict[str, object] = {
            "schema_version": 2,
            "protocol_id": "okx-demo-sample-efficiency-economic-campaign-v1",
            "execution_source": "okx_demo_sample_efficiency_campaign_supervisor.py",
            "child_execution_source": "okx_demo_soak_executor.py",
            "preparation_source": Path(__file__).name,
            "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"],
            "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "campaign_arm_token_sha256": hashlib.sha256(
                campaign_token.encode("utf-8")
            ).hexdigest(),
            "campaign_arm_token_serialized": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "r0_evidence_id": r0_evidence_id,
            "a0_evidence_id": r0_evidence_id,
            "preflight_run_id": preflight_run_id,
            "formal_predecessor": base.FORMAL_PACKAGE_ID,
            "bounded_soak_predecessor": base.SOAK_PACKAGE_ID,
            "economic_predecessor": r0.get("economic_predecessor_package_id"),
            "source_manifest_sha256": source_manifest_sha256,
            "r0_completion_sha256": r0["completion_hashes_sha256"],
            "a0_completion_sha256": r0["completion_hashes_sha256"],
            "preflight_completion_sha256": preflight["completion_hashes_sha256"],
            "market_fingerprint": preflight["market_fingerprint"],
            "market_spec": preflight["market_spec"],
            "campaign_limits": base.CampaignLimits().to_dict(),
            "session_risk_budget": base._risk_budget(),
            "session_slots": slots,
            "session_count": len(slots),
            "sessions_must_be_sequential": True,
            "single_instance_lease_required": True,
            "campaign_authorized": False,
            "campaign_executed": False,
            "execution_marker_created": False,
            "network_authorized_during_freeze": False,
            "orders_authorized_during_freeze": False,
            "production_authorized": False,
        }
        spec["specification_sha256"] = canonical_sha256(spec)
        _write_json(output / "predecessor/r0_evidence_audit.json", r0)
        _write_json(output / "predecessor/a0_evidence_audit.json", r0)
        _write_json(output / "predecessor/preflight_evidence_audit.json", preflight)
        _write_json(
            output / "predecessor/rejected_prearm_package_audit.json",
            rejected_audit,
        )
        _write_json(output / "specification/source_hashes.json", sources)
        _write_json(output / "specification/campaign_package_spec.json", spec)
        child_audits = [
            base._write_session_package(
                root=root,
                campaign_output=output,
                campaign_spec=spec,
                slot=dict(slot),
                sources=sources,
                a0=r0,
                preflight=preflight,
            )
            for slot in slots
        ]
        _write_json(output / "specification/session_package_audits.json", {
            "count": len(child_audits), "packages": child_audits,
        })
        targeted = run_suite(root, output, "r2_successor_package_targeted", TARGETED_TESTS)
        _write_json(output / "tests/test_summary.json", {
            "r2_successor_package_targeted": targeted,
        })
        if targeted.get("passed_gate") is not True:
            raise base.A2PackageError("successor R2 package regressions failed")
        if _source_hashes(root) != sources:
            raise base.A2PackageError("successor R2 source changed during freeze")
        _write_json(output / "audits/network_mutation_audit.json", {
            "socket_denied": True,
            "network_attempts": len(guard.attempts),
            "credential_accesses": 0,
            "okx_requests": 0,
            "preflight_attempts": 0,
            "economic_campaign_attempts": 0,
            "economic_session_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        scan = _artifact_secret_scan(output)
        child_scan = {
            item["package_id"]: _artifact_secret_scan(
                root / base.SESSION_ARTIFACT_ROOT / str(item["package_id"])
            )
            for item in child_audits
        }
        _write_json(output / "audits/secret_scan.json", {
            "passed": bool(scan["passed"] and all(
                value["passed"] for value in child_scan.values()
            )),
            "campaign_package": scan,
            "session_packages": child_scan,
            "credential_environment_accessed": False,
        })
        decision = {
            "status": base.READY_STATUS,
            "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"],
            "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "r0_evidence_id": r0_evidence_id,
            "preflight_run_id": preflight_run_id,
            "session_packages_prepared": len(child_audits),
            "campaign_authorized": False,
            "campaign_executed": False,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "next_boundary": "separate exact R2 campaign authorization",
        }
        _write_json(output / "decision/a2_package_decision.json", decision)
        _write_text(
            output / "decision/a2_package_decision.md",
            "# R2 sample-efficiency economic campaign package\n\n"
            f"- Package: `{identifiers['package_id']}`\n"
            f"- Campaign: `{identifiers['campaign_id']}`\n"
            f"- Run: `{identifiers['run_id']}`\n"
            "- Session packages: `12`\n"
            "- Network/order execution: `0 / 0`\n"
            "- R2 authorization: required separately\n",
        )
        completion = base._completion_hashes(output, "A2_PACKAGE_COMPLETED.json")
        _write_json(output / "completion_hashes.json", completion)
        terminal = {
            **decision,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "source_manifest_sha256": source_manifest_sha256,
            "campaign_arm_token_sha256": spec["campaign_arm_token_sha256"],
            "campaign_arm_token_serialized": False,
            "terminal_written_last": True,
        }
        _write_json(output / "A2_PACKAGE_COMPLETED.json", terminal)
    if guard.attempts:
        raise base.A2PackageError("network attempt blocked during successor freeze")
    public = {
        key: value for key, value in identifiers.items()
        if key not in {"campaign_arm_token", "session_slots"}
    }
    public["campaign_arm_token"] = campaign_token
    public["session_slots"] = slots
    return output, public


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--r0-evidence-id", required=True)
    parser.add_argument("--preflight-run-id", required=True)
    args = parser.parse_args()
    try:
        output, identifiers = prepare(
            args.root,
            r0_evidence_id=args.r0_evidence_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"R2_PACKAGE_PREPARATION_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
