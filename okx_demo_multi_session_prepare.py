"""Freeze a source-bound A2 economic campaign and its twelve session packages.

This module is deliberately offline-only.  It verifies immutable A0/A1
evidence, prepares non-overwriting identities and artifacts, and never loads
credentials or dispatches an OKX request.  A later A2 authorization is still
required before the campaign supervisor may arm any session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_soak_failure_injection_offline import _run_suite
from okx_fill_restart_formal_prepare import (
    _OfflineSocketGuard,
    _artifact_secret_scan,
    verify_preflight_evidence,
)
from okx_fill_restart_offline import _sha256, _write_json, _write_text
from okx_fill_restart_preflight import (
    MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
    preflight_source_hashes,
    verify_offline_evidence,
)
from okx_fill_restart_validation import canonical_sha256


CAMPAIGN_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_multi_session_economic_soak" / "packages"
)
SESSION_ARTIFACT_ROOT = Path("artifacts") / "okx_demo_soak_validation"
READY_STATUS = "A2_MULTI_SESSION_ECONOMIC_PACKAGE_FROZEN_OFFLINE"
SESSION_READY_STATUS = "BOUNDED_DEMO_SOAK_PACKAGE_FROZEN_OFFLINE"
R2_ADMISSIBLE_R0_EVIDENCE_KINDS = frozenset({
    "multi_session_a0_offline_build",
    "r2_terminal_workoff_v2_r0_offline_repair",
    "r2_session5_clock_skew_interruption_audit_r0_offline",
    "r1_network_transport_audit_r0_offline",
    "r1_clock_skew_r0_offline_repair",
    "r2_bootstrap_exposure_admission_r0_offline_repair",
    "r2_special_flatten_classification_r0_offline_repair",
    "r1_special_flatten_admission_r0_offline_repair",
})
FORMAL_PACKAGE_ID = "formal-package-20260810T123953Z"
FORMAL_COMPLETED_SHA256 = (
    "f62a17a606b7c4826f862ee6ae0cab7ef35d224c9f08bfc13234aa188f12b11e"
)
SOAK_PACKAGE_ID = "soak-package-20260811T140223Z"
SOAK_COMPLETED_SHA256 = (
    "e658a0aede428465d579434aa19a9e975f0564e6134b9577ee9f373a0eacd958"
)
SOURCE_FILES = (
    "okx_demo_multi_session_prepare.py",
    "okx_demo_multi_session_supervisor.py",
    "okx_demo_multi_session_campaign.py",
    "okx_demo_economic_session_controller.py",
    "okx_demo_economic_fill_engine.py",
    "okx_demo_soak_executor.py",
    "tests/test_okx_demo_multi_session_prepare.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_multi_session_campaign.py",
    "tests/test_okx_demo_soak_executor.py",
)
TARGETED_TESTS = (
    "tests/test_okx_demo_multi_session_prepare.py",
    "tests/test_okx_demo_multi_session_supervisor.py",
    "tests/test_okx_demo_multi_session_campaign.py",
    "tests/test_okx_demo_economic_session_controller.py",
    "tests/test_okx_demo_economic_fill_engine.py",
    "tests/test_okx_demo_soak_executor.py",
)


class A2PackageError(RuntimeError):
    pass


def _completion_hashes(output: Path, *terminal_names: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", *terminal_names}
    return dict(sorted(
        (path.relative_to(output).as_posix(), _sha256(path))
        for path in output.rglob("*")
        if (
            path.is_file()
            and not path.is_symlink()
            and path.name not in ignored
            and path.suffix != ".tmp"
        )
    ))


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = preflight_source_hashes(root)
    for relative in SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise A2PackageError(f"A2 source is missing: {relative}")
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _identifiers(
    *,
    source_manifest_sha256: str,
    preflight_completion_sha256: str,
    now: datetime | None = None,
) -> dict[str, object]:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise A2PackageError("identifier timestamp must be timezone aware")
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_id = f"economic-package-{stamp}"
    campaign_id = f"economic-campaign-{stamp}"
    run_id = f"economic-campaign-run-{stamp}"
    nonce = hashlib.sha256(
        (
            f"{package_id}|{campaign_id}|{run_id}|{source_manifest_sha256}|"
            f"{preflight_completion_sha256}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    campaign_session_id = f"economic-campaign:{run_id}:p0:{nonce}"
    slots: list[dict[str, object]] = []
    for index in range(1, CampaignLimits().maximum_sessions + 1):
        slot_nonce = hashlib.sha256(
            f"{campaign_id}|{index}|{source_manifest_sha256}".encode("utf-8")
        ).hexdigest()[:10]
        session_run_id = f"economic-session-{stamp}-s{index:02d}-{slot_nonce}"
        session_id = f"economic:{session_run_id}:p0:{slot_nonce}"
        session_package_id = f"soak-package-{stamp}-s{index:02d}-{slot_nonce}"
        session_token = f"OKX_DEMO:{session_id}"
        slots.append({
            "slot": index,
            "package_id": session_package_id,
            "run_id": session_run_id,
            "session_id": session_id,
            "arm_token_sha256": hashlib.sha256(
                session_token.encode("utf-8")
            ).hexdigest(),
            "arm_token_serialized": False,
        })
    return {
        "package_id": package_id,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "campaign_session_id": campaign_session_id,
        "campaign_arm_token": f"OKX_DEMO:{campaign_session_id}",
        "session_slots": slots,
    }


def _verify_a1(
    root: Path, *, preflight_run_id: str, a0_evidence_id: str
) -> dict[str, object]:
    audit = verify_preflight_evidence(root, preflight_run_id)
    output = (
        root / "artifacts" / "okx_demo_fill_restart_validation" / preflight_run_id
    )
    result = json.loads(
        (output / "preflight" / "preflight_result.json").read_text(encoding="utf-8")
    )
    spec = json.loads(
        (output / "specification" / "preflight_spec.json").read_text(
            encoding="utf-8"
        )
    )
    predecessor = json.loads(
        (output / "predecessor" / "offline_evidence_audit.json").read_text(
            encoding="utf-8"
        )
    )
    hosts = list(dict(result.get("transport_audit") or {}).get("endpoint_hosts") or [])
    if any((
        audit.get("passed") is not True,
        audit.get("run_id") != preflight_run_id,
        spec.get("protocol_id") != MULTI_SESSION_A1_PREFLIGHT_PROTOCOL_ID,
        predecessor.get("evidence_kind") not in R2_ADMISSIBLE_R0_EVIDENCE_KINDS,
        _predecessor_r0_evidence_id(predecessor) != a0_evidence_id,
        hosts != ["www.okx.com"],
        result.get("mutation_attempts") != 0,
        result.get("orders_submitted") != 0,
        result.get("orders_amended") != 0,
        result.get("orders_cancelled") != 0,
        result.get("live_endpoint_attempts") != 0,
        result.get("live_orders") != 0,
    )):
        raise A2PackageError("A1 preflight is not eligible for A2")
    return {
        **audit,
        "a0_evidence_id": a0_evidence_id,
        "protocol_id": spec["protocol_id"],
        "endpoint_hosts": hosts,
        "zero_mutation_verified": True,
    }


def _predecessor_r0_evidence_id(predecessor: dict[str, object]) -> object:
    """Resolve the sole canonical predecessor identity for an admitted kind."""
    if predecessor.get("evidence_kind") == "multi_session_a0_offline_build":
        return predecessor.get("evidence_id")
    if predecessor.get("evidence_kind") in {
        "r2_terminal_workoff_v2_r0_offline_repair",
        "r2_session5_clock_skew_interruption_audit_r0_offline",
        "r1_clock_skew_r0_offline_repair",
        "r2_bootstrap_exposure_admission_r0_offline_repair",
        "r2_special_flatten_classification_r0_offline_repair",
        "r1_special_flatten_admission_r0_offline_repair",
    }:
        return predecessor.get("offline_run_id")
    return None


def _admissible_r0_for_r2(a0: dict[str, object]) -> bool:
    """Accept only source-specific R0 evidence with its matching fail-closed gate."""
    if a0.get("passed") is not True:
        return False
    evidence_kind = a0.get("evidence_kind")
    if evidence_kind == "multi_session_a0_offline_build":
        return all((
            a0.get("formal_predecessor_verified") is True,
            a0.get("soak_predecessor_verified") is True,
            a0.get("failed_a2_predecessor_verified") is True,
        ))
    if evidence_kind == "r2_terminal_workoff_v2_r0_offline_repair":
        return all((
            a0.get("failed_campaign_decision") == "NOT_READY",
            a0.get("terminal_account_authoritative") is True,
            a0.get("resume_authorized") is False,
        ))
    if evidence_kind == "r2_session5_clock_skew_interruption_audit_r0_offline":
        return all((
            a0.get("failed_campaign_decision") == "NOT_READY",
            a0.get("terminal_account_authoritative") is True,
            a0.get("resume_authorized") is False,
            a0.get("accept_authorized") is False,
            a0.get("failed_session_slot") == 5,
        ))
    if evidence_kind == "r1_clock_skew_r0_offline_repair":
        return all((
            a0.get("failed_campaign_decision") == "NOT_READY",
            a0.get("terminal_account_authoritative") is False,
            a0.get("resume_authorized") is False,
            isinstance(a0.get("failed_preparation_id"), str),
            isinstance(a0.get("failed_run_id"), str),
            isinstance(a0.get("failed_session_id"), str),
        ))
    if evidence_kind == "r2_bootstrap_exposure_admission_r0_offline_repair":
        return all((
            a0.get("r1_preflight_passed") is True,
            a0.get("r1_mutation_attempts") == 0,
            a0.get("r1_live_endpoint_attempts") == 0,
            a0.get("failed_R1_identity_reusable") is False,
            a0.get("resume_authorized") is False,
        ))
    if evidence_kind == "r2_special_flatten_classification_r0_offline_repair":
        return all((
            a0.get("failed_campaign_decision") == "NOT_READY",
            a0.get("terminal_account_authoritative") is False,
            a0.get("special_flatten_sessions") == 2,
            a0.get("resume_authorized") is False,
            a0.get("identity_reuse_authorized") is False,
        ))
    if evidence_kind == "r1_special_flatten_admission_r0_offline_repair":
        return all((
            a0.get("failed_preflight_decision") == "READ_ONLY_PREFLIGHT_FAILED",
            a0.get("failed_identity_reusable") is False,
            a0.get("rerun_authorized") is False,
            a0.get("resume_authorized") is False,
        ))
    return False


def _risk_budget() -> dict[str, object]:
    return {
        "capital_usdt": "750",
        "leverage": 3,
        "maximum_inventory_btc": "0.01",
        "maximum_owned_bid": 1,
        "maximum_owned_ask": 1,
        "maximum_unresolved_flatten": 1,
        "soft_guard_usdt": "22.50",
        "hard_kill_usdt": "37.50",
        "session_wall_minutes": 30,
        "shutdown_reconciliation_reserve_seconds": 60,
        "session_normal_create_cap": 60,
        "admission_create_cap": 48,
        "maker_workoff_create_reserve": 12,
        "economic_repair_version": "r0-terminal-workoff-v2",
        "maker_fee_rate": "0.0002",
        "minimum_half_spread_bps": "4.0",
        "fee_edge_safety_buffer_usdt": "0.01",
        "quote_retention_threshold_ticks": 2,
        "balanced_quote_retention_threshold_ticks": 10,
        "defense_quote_retention_threshold_ticks": 20,
        "draining_workoff_max_quote_observations": 6,
        "draining_workoff_max_refreshes": 3,
        "minimum_fill_balance": "0.60",
        "observation_interval_ms": 2_000,
        "maximum_market_age_ms": 1_000,
        "maximum_clock_skew_ms": 1_500,
        "read_retry_attempts": 3,
        "ambiguous_mutation_retry_attempts": 0,
        "terminal_account_snapshots": 2,
    }


def _write_session_package(
    *,
    root: Path,
    campaign_output: Path,
    campaign_spec: dict[str, object],
    slot: dict[str, object],
    sources: dict[str, str],
    a0: dict[str, object],
    preflight: dict[str, object],
) -> dict[str, object]:
    output = root / SESSION_ARTIFACT_ROOT / str(slot["package_id"])
    if output.exists():
        raise A2PackageError("fresh A2 session package identity collision")
    output.mkdir(parents=True)
    spec: dict[str, object] = {
        "schema_version": 1,
        "protocol_id": "okx-demo-multi-session-economic-soak-v1",
        "execution_source": "okx_demo_soak_executor.py",
        "execution_source_bound": True,
        "package_id": slot["package_id"],
        "run_id": slot["run_id"],
        "session_id": slot["session_id"],
        "campaign_package_id": campaign_spec["package_id"],
        "campaign_id": campaign_spec["campaign_id"],
        "campaign_slot": slot["slot"],
        "campaign_authorization_required": True,
        "created_at_utc": campaign_spec["created_at_utc"],
        "a0_evidence_id": campaign_spec["a0_evidence_id"],
        "preflight_run_id": campaign_spec["preflight_run_id"],
        "successful_formal_predecessor": FORMAL_PACKAGE_ID,
        "successful_soak_predecessor": SOAK_PACKAGE_ID,
        "source_manifest_sha256": campaign_spec["source_manifest_sha256"],
        "a0_completion_sha256": a0["completion_hashes_sha256"],
        "preflight_completion_sha256": preflight["completion_hashes_sha256"],
        "market_fingerprint": preflight["market_fingerprint"],
        "market_spec": preflight["market_spec"],
        "risk_budget": _risk_budget(),
        "normal_orders": "POST_ONLY_ONLY",
        "shutdown_flatten": "SINGLE_FLIGHT_REDUCE_ONLY",
        "required_terminal_state": "TWO_FLAT_EMPTY_ACCOUNT_SNAPSHOTS",
        "soak_authorized": False,
        "soak_executed": False,
        "execution_marker_created": False,
        "network_authorized_during_freeze": False,
        "orders_authorized_during_freeze": False,
        "production_authorized": False,
    }
    spec["specification_sha256"] = canonical_sha256(spec)
    _write_json(output / "predecessor/a0_evidence_audit.json", a0)
    _write_json(output / "predecessor/preflight_evidence_audit.json", preflight)
    _write_json(output / "specification/source_hashes.json", sources)
    _write_json(output / "specification/soak_package_spec.json", spec)
    _write_json(output / "run/soak_run_manifest.json", {
        **slot,
        "campaign_package_id": campaign_spec["package_id"],
        "campaign_authorized": False,
        "session_authorized": False,
        "soak_executed": False,
        "execution_marker_created": False,
        "network_attempts": 0,
        "orders_submitted": 0,
        "orders_amended": 0,
        "orders_cancelled": 0,
    })
    completion = _completion_hashes(output, "SOAK_PACKAGE_COMPLETED.json")
    _write_json(output / "completion_hashes.json", completion)
    terminal = {
        "status": SESSION_READY_STATUS,
        "package_id": slot["package_id"],
        "run_id": slot["run_id"],
        "session_id": slot["session_id"],
        "campaign_package_id": campaign_spec["package_id"],
        "campaign_slot": slot["slot"],
        "campaign_authorized": False,
        "soak_executed": False,
        "execution_marker_created": False,
        "network_attempts": 0,
        "orders_submitted": 0,
        "orders_amended": 0,
        "orders_cancelled": 0,
        "completion_files_checked": len(completion),
        "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
        "production_authorized": False,
    }
    _write_json(output / "SOAK_PACKAGE_COMPLETED.json", terminal)
    return {
        **slot,
        "relative_path": output.relative_to(root).as_posix(),
        "completion_hashes_sha256": terminal["completion_hashes_sha256"],
        "terminal_sha256": _sha256(output / "SOAK_PACKAGE_COMPLETED.json"),
    }


def prepare(
    root: Path,
    *,
    a0_evidence_id: str,
    preflight_run_id: str,
    run_suite: Callable[..., dict[str, object]] = _run_suite,
) -> tuple[Path, dict[str, object]]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        a0 = verify_offline_evidence(root, a0_evidence_id)
        preflight = _verify_a1(
            root,
            preflight_run_id=preflight_run_id,
            a0_evidence_id=a0_evidence_id,
        )
        if not _admissible_r0_for_r2(a0):
            raise A2PackageError("A0 evidence is not eligible for A2")
        sources = _source_hashes(root)
        source_manifest_sha256 = canonical_sha256(sources)
        identifiers = _identifiers(
            source_manifest_sha256=source_manifest_sha256,
            preflight_completion_sha256=str(preflight["completion_hashes_sha256"]),
        )
        output = root / CAMPAIGN_ARTIFACT_ROOT / str(identifiers["package_id"])
        run_output = root / CAMPAIGN_ARTIFACT_ROOT / str(identifiers["run_id"])
        if output.exists() or run_output.exists():
            raise A2PackageError("fresh A2 campaign identity collision")
        output.mkdir(parents=True)
        campaign_token = str(identifiers["campaign_arm_token"])
        slots = list(identifiers["session_slots"])
        spec: dict[str, object] = {
            "schema_version": 1,
            "protocol_id": "okx-demo-multi-session-economic-campaign-v1",
            "execution_source": "okx_demo_multi_session_supervisor.py",
            "child_execution_source": "okx_demo_soak_executor.py",
            "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"],
            "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "campaign_arm_token_sha256": hashlib.sha256(
                campaign_token.encode("utf-8")
            ).hexdigest(),
            "campaign_arm_token_serialized": False,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "a0_evidence_id": a0_evidence_id,
            "preflight_run_id": preflight_run_id,
            "formal_predecessor": FORMAL_PACKAGE_ID,
            "bounded_soak_predecessor": SOAK_PACKAGE_ID,
            "formal_completed_sha256": FORMAL_COMPLETED_SHA256,
            "bounded_soak_completed_sha256": SOAK_COMPLETED_SHA256,
            "source_manifest_sha256": source_manifest_sha256,
            "a0_completion_sha256": a0["completion_hashes_sha256"],
            "preflight_completion_sha256": preflight["completion_hashes_sha256"],
            "market_fingerprint": preflight["market_fingerprint"],
            "market_spec": preflight["market_spec"],
            "campaign_limits": CampaignLimits().to_dict(),
            "session_risk_budget": _risk_budget(),
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
        _write_json(output / "predecessor/a0_evidence_audit.json", a0)
        _write_json(output / "predecessor/preflight_evidence_audit.json", preflight)
        _write_json(output / "specification/source_hashes.json", sources)
        _write_json(output / "specification/campaign_package_spec.json", spec)
        child_audits = [
            _write_session_package(
                root=root,
                campaign_output=output,
                campaign_spec=spec,
                slot=dict(slot),
                sources=sources,
                a0=a0,
                preflight=preflight,
            )
            for slot in slots
        ]
        _write_json(output / "specification/session_package_audits.json", {
            "count": len(child_audits),
            "packages": child_audits,
        })
        targeted = run_suite(root, output, "a2_package_targeted", TARGETED_TESTS)
        _write_json(output / "tests/test_summary.json", {
            "a2_package_targeted": targeted,
        })
        if targeted.get("passed_gate") is not True:
            raise A2PackageError("A2 package targeted regressions failed")
        if _source_hashes(root) != sources:
            raise A2PackageError("A2 source changed during package freeze")
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
                root / SESSION_ARTIFACT_ROOT / str(item["package_id"])
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
            "status": READY_STATUS,
            "package_id": identifiers["package_id"],
            "campaign_id": identifiers["campaign_id"],
            "run_id": identifiers["run_id"],
            "campaign_session_id": identifiers["campaign_session_id"],
            "a0_evidence_id": a0_evidence_id,
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
            "next_boundary": "separate exact A2 campaign authorization",
        }
        _write_json(output / "decision/a2_package_decision.json", decision)
        _write_text(
            output / "decision/a2_package_decision.md",
            "# A2 multi-session economic campaign package\n\n"
            f"- Package: `{identifiers['package_id']}`\n"
            f"- Campaign: `{identifiers['campaign_id']}`\n"
            f"- Run: `{identifiers['run_id']}`\n"
            "- Session packages: `12`\n"
            "- Network/order execution: `0 / 0`\n"
            "- A2 authorization: required separately\n",
        )
        completion = _completion_hashes(output, "A2_PACKAGE_COMPLETED.json")
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
        raise A2PackageError("network attempt blocked during A2 package freeze")
    public = {
        key: value
        for key, value in identifiers.items()
        if key not in {"campaign_arm_token", "session_slots"}
    }
    public["campaign_arm_token"] = campaign_token
    public["session_slots"] = slots
    return output, public


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--a0-evidence-id", required=True)
    parser.add_argument("--preflight-run-id", required=True)
    args = parser.parse_args()
    try:
        output, identifiers = prepare(
            args.root,
            a0_evidence_id=args.a0_evidence_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"A2_PACKAGE_PREPARATION_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({"output": str(output), **identifiers}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
