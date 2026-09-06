"""Fail-closed generator for R2 Successor Economic Campaign Preparation.

Implements all phases and rules of:
EXECUTION_R2_SUCCESSOR_ECONOMIC_CAMPAIGN.md
- Binds prerequisite R0 qualification closure and R1 read-only preflight runs
- Verifies candidate fingerprint integrity
- Generates fresh R2 campaign identities and 12-session schedule
- Freezes risk specification and qualification floors
- Strictly offline under socket denial: zero network, zero credentials, zero order mutations
- Enforces hard stop before R2 execution.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from market_spec import MarketSpec
from okx_demo_multi_session_campaign import CampaignLimits
from okx_demo_profile import load_promoted_profile
from okx_demo_staged_validation import compute_candidate_fingerprint
from okx_fill_restart_preflight_prepare import _OfflineSocketGuard

ROOT = Path(__file__).resolve().parent

CANONICAL_R0_CLOSURE_REF = "r0-closure-20260904T121733Z"
CANONICAL_R1_RUN_ID = "r1-preflight-run-20260904T121733Z"
EXPECTED_CANDIDATE_FINGERPRINT = "ef993bc42ffbb19cf1bfc94d3dcca32cd19909c9b0d9da73ab0a01ab0088ffb8"


def canonical_sha256(content: bytes | str) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_exact_fresh_r0_readiness_evidence(r0_data: Mapping[str, Any]) -> bool:
    """Accept a fresh repair only when every safety field is present and exact."""
    # A repair evidence record is admissible only when it binds a concrete
    # repaired predecessor and proves that the repair itself made no external
    # calls. The successor's R1 marker is separately required below to refer
    # to this exact evidence ID; never use the historical run named inside a
    # repair record as an implicit successor authorization.
    offline_repair = r0_data.get("offline_verification")
    repair_results = r0_data.get("repair_results")
    repair_boundaries = r0_data.get("boundaries")
    if isinstance(offline_repair, Mapping) and isinstance(repair_results, Mapping) and isinstance(repair_boundaries, Mapping):
        required_repair_zeros = (
            "credentials_read", "network_attempts", "orders_or_mutations",
            "mutation_retries", "live_endpoint_attempts",
        )
        return (
            r0_data.get("status") == "R0_OFFLINE_REPAIR_PASSED"
            and r0_data.get("decision") == "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"
            and isinstance(r0_data.get("repaired_r0_evidence_id"), str)
            and bool(r0_data.get("repaired_r0_evidence_id"))
            and isinstance(r0_data.get("candidate_fingerprint"), str)
            and len(str(r0_data.get("candidate_fingerprint"))) == 64
            and repair_results.get("accepts_exact_post_clearance_zero_zero_schema") is True
            and repair_results.get("rejects_missing_or_nonzero_clearance_fields") is True
            and repair_results.get("rejects_stale_r1_candidate_fingerprint") is True
            and all(offline_repair.get(field) == 0 for field in required_repair_zeros)
            and repair_boundaries.get("r1_prepared") is False
            and repair_boundaries.get("r2_prepared") is False
            and repair_boundaries.get("r2_executed") is False
            and repair_boundaries.get("r3_prepared_or_run") is False
            and repair_boundaries.get("live_or_production") is False
        )

    # Post-clearance evidence is deliberately a separate schema: it records a
    # read-only external 0/0 admission result, while its own offline boundary
    # proves that the R0 evidence-writing phase made no remote calls.
    clearance = r0_data.get("clearance_observation")
    boundary = r0_data.get("offline_boundary")
    failed_identity = r0_data.get("failed_identity_disposition")
    if isinstance(clearance, Mapping) and isinstance(boundary, Mapping) and isinstance(failed_identity, Mapping):
        required_boundary_zeros = (
            "credentials_read", "network_attempts", "orders_or_mutations",
            "mutation_retries", "live_endpoint_attempts",
        )
        return (
            r0_data.get("status") == "R0_OFFLINE_READINESS_PASSED"
            and r0_data.get("decision") == "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"
            and clearance.get("execution_mode") == "OKX_DEMO"
            and clearance.get("instrument") == "BTC-USDT-SWAP"
            and clearance.get("nonzero_position_count") == 0
            and clearance.get("open_order_count") == 0
            and clearance.get("clearance_passed") is True
            and all(boundary.get(field) == 0 for field in required_boundary_zeros)
            and boundary.get("r1_prepared_or_run") is False
            and boundary.get("r2_or_r3_prepared_or_run") is False
            and failed_identity.get("reuse_authorized") is False
            and failed_identity.get("retry_authorized") is False
            and failed_identity.get("resume_authorized") is False
        )

    offline = r0_data.get("offline_verification")
    boundaries = r0_data.get("boundaries")
    if not isinstance(offline, Mapping) or not isinstance(boundaries, Mapping):
        return False
    zero_fields = ("orders_created", "orders_cancelled", "flatten_attempts", "mutation_retries")
    return (
        r0_data.get("status") in {"R0_OFFLINE_FRESH_CHAIN_READINESS_PASSED", "R0_OFFLINE_REPAIR_PASSED"}
        and r0_data.get("decision") == "FRESH_R1_PREPARATION_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION"
        and offline.get("network_access") is False
        and offline.get("order_mutations") == 0
        and all(offline.get(field) == 0 for field in zero_fields)
        and boundaries.get("r1_prepared") is False
        and boundaries.get("r2_prepared") is False
        and boundaries.get("r2_executed") is False
    )


def write_json_atomic(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(".tmp")
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temp_target.write_text(data, encoding="utf-8")
    temp_target.replace(target)


def generate_completion_hashes(directory: Path, marker_filename: str) -> dict[str, str]:
    ignored = {"completion_hashes.json", marker_filename}
    hashes: dict[str, str] = {}
    for item in sorted(directory.rglob("*")):
        if item.is_file() and item.name not in ignored and not item.name.endswith(".tmp"):
            rel = item.relative_to(directory).as_posix()
            hashes[rel] = hash_file(item)
    return hashes


def build_r2_campaign_preparation_package(
    root: Path,
    stamp: str = "20260904T124817Z",
    r0_closure_ref: str = CANONICAL_R0_CLOSURE_REF,
    r1_run_id: str = CANONICAL_R1_RUN_ID,
    r0_evidence_id: str | None = None,
    r0_evidence_dir: Path | None = None,
) -> Path:
    # 1. Verify candidate fingerprint
    fingerprint_result = compute_candidate_fingerprint(root)
    actual_fingerprint = fingerprint_result.get("candidate_fingerprint", "")
    if actual_fingerprint != EXPECTED_CANDIDATE_FINGERPRINT:
        raise ValueError(
            f"Candidate fingerprint mismatch! Expected {EXPECTED_CANDIDATE_FINGERPRINT}, found {actual_fingerprint}"
        )

    # 2. Verify exact R0 prerequisite.  Fresh repair/audit evidence is accepted
    # only when the caller binds its immutable evidence ID explicitly.
    if r0_evidence_dir is None:
        r0_closure_dir = root / "artifacts" / "r0_offline_qualification_closure" / r0_closure_ref
        r0_marker = r0_closure_dir / "R0_CLOSURE_COMPLETED.json"
        expected_r0_ref = r0_closure_ref
    else:
        r0_closure_dir = r0_evidence_dir
        markers = sorted(r0_closure_dir.glob("*COMPLETED.json"))
        if len(markers) != 1:
            raise FileNotFoundError("Fresh R0 evidence must contain exactly one terminal marker")
        r0_marker = markers[0]
        expected_r0_ref = r0_evidence_id or ""
        if not expected_r0_ref:
            raise ValueError("Fresh R0 evidence ID is required")
    if not r0_marker.is_file():
        raise FileNotFoundError(f"Prerequisite R0 closure marker missing: {r0_marker}")
    r0_data = json.loads(r0_marker.read_text(encoding="utf-8"))
    recorded_r0_ref = (
        r0_data.get("closure_package_id")
        or r0_data.get("audit_id")
        or r0_data.get("repair_id")
        or r0_data.get("evidence_id")
    )
    if recorded_r0_ref != expected_r0_ref:
        raise ValueError("R0 evidence identity mismatch")
    if r0_evidence_dir is None:
        if r0_data.get("status") != "R0_OFFLINE_QUALIFICATION_PASSED":
            raise ValueError(f"R0 closure status invalid: {r0_data.get('status')}")
    else:
        is_legacy_expiry_audit = r0_data.get("decision") == "R2_CAMPAIGN_EXPIRED_NOT_READY"
        is_fresh_readiness_evidence = _is_exact_fresh_r0_readiness_evidence(r0_data)
        if not (is_legacy_expiry_audit or is_fresh_readiness_evidence):
            raise ValueError("Fresh R0 evidence is not eligible for successor preparation")

    # 3. Verify R1 preflight prerequisite
    r1_run_dir = root / "artifacts" / "r1_read_only_preflight_runs" / r1_run_id
    r1_marker = r1_run_dir / "R1_PREFLIGHT_PASSED.json"
    if not r1_marker.is_file():
        raise FileNotFoundError(f"Prerequisite R1 preflight marker missing: {r1_marker}")
    r1_data = json.loads(r1_marker.read_text(encoding="utf-8"))
    if r1_data.get("status") != "R1_PREFLIGHT_PASSED":
        raise ValueError(f"R1 preflight status invalid: {r1_data.get('status')}")
    r1_hashes_path = r1_run_dir / "completion_hashes.json"
    if not r1_hashes_path.is_file():
        raise FileNotFoundError("Prerequisite R1 completion hashes missing")
    r1_hashes = json.loads(r1_hashes_path.read_text(encoding="utf-8"))
    for relative_name, expected_hash in r1_hashes.items():
        artifact = r1_run_dir / relative_name
        if not artifact.is_file() or hash_file(artifact) != expected_hash:
            raise ValueError(f"R1 prerequisite hash verification failed: {relative_name}")
    if r1_data.get("candidate_fingerprint") != actual_fingerprint:
        raise ValueError("R1 candidate fingerprint mismatch")
    if r1_data.get("r0_evidence_id", r1_data.get("r0_closure_ref")) != expected_r0_ref:
        raise ValueError("R1 predecessor R0 evidence mismatch")

    # 4. Prepare target directory
    prep_dir = root / "artifacts" / "r2_economic_campaign_preparation" / f"r2-prep-{stamp}"
    prep_dir.mkdir(parents=True, exist_ok=True)

    package_id = f"r2-package-{stamp}"
    campaign_id = f"r2-campaign-{stamp}"
    run_id = f"r2-campaign-run-{stamp}"
    campaign_nonce = hashlib.sha256(f"{package_id}|{campaign_id}|{actual_fingerprint}".encode()).hexdigest()[:12]
    campaign_session_id = f"r2-campaign-session-{stamp}:p0:{campaign_nonce}"
    campaign_arm_token = f"OKX_DEMO:{campaign_session_id}"

    profile = load_promoted_profile(root)
    limits = CampaignLimits()

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. candidate_identity.json
    candidate_identity = {
        "candidate_fingerprint": actual_fingerprint,
        "limits_fingerprint": fingerprint_result.get("limits_fingerprint", ""),
        "policy_fingerprint": fingerprint_result.get("policy_fingerprint", ""),
        "profile_id": profile.profile_id,
        "profile_fingerprint": profile.profile_fingerprint,
        "specification_sha256": profile.specification_sha256,
        "r0_closure_ref": expected_r0_ref,
        "r0_evidence_id": expected_r0_ref,
        "r1_run_id": r1_run_id,
        "created_at_utc": now_iso,
    }
    write_json_atomic(prep_dir / "candidate_identity.json", candidate_identity)

    # 2. prerequisite_evidence_manifest.json
    prerequisite_evidence = {
        "r0_closure": {
            "closure_package_id": expected_r0_ref,
            "status": r0_data.get("status"),
            "completion_hashes_sha256": r0_data.get("completion_hashes_sha256"),
            "qualification_state": r0_data.get("qualification_state"),
        },
        "r1_preflight": {
            "run_id": r1_run_id,
            "status": r1_data.get("status"),
            "completion_hashes_sha256": r1_data.get("completion_hashes_sha256"),
            "r1_package_id": r1_data.get("r1_package_id"),
        },
        "candidate_fingerprint": actual_fingerprint,
        "prerequisites_satisfied": True,
    }
    write_json_atomic(prep_dir / "prerequisite_evidence_manifest.json", prerequisite_evidence)

    # 3. r2_identity.json
    r2_identity = {
        "package_id": package_id,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "campaign_session_id": campaign_session_id,
        "expected_campaign_arm_token": campaign_arm_token,
        "symbol": "BTC/USDT:USDT",
        "execution_mode": "OKX_DEMO",
        "transport_environment": "OKX_DEMO_SANDBOX",
        "total_sessions": limits.maximum_sessions,
        "created_at_utc": now_iso,
    }
    write_json_atomic(prep_dir / "r2_identity.json", r2_identity)

    # 4. r2_authorization_contract.json
    r2_auth = {
        "r2_prepared": True,
        "r2_authorized": False,
        "r2_executed": False,
        "expected_campaign_arm_token": campaign_arm_token,
        "arm_token_provided": False,
        "authorization_boundary": (
            "Separate explicit manual user command required to authorize R2 campaign execution. "
            "Zero network, zero credentials, zero mutations permitted until explicit authorization."
        ),
        "terminal_action": "STOP_BEFORE_CAMPAIGN_START",
        "next_status": "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION",
    }
    write_json_atomic(prep_dir / "r2_authorization_contract.json", r2_auth)

    # 5. frozen_risk_specification.json
    strategy_dict = dataclasses.asdict(profile.strategy)
    frozen_risk = {
        "frozen_strategy_controls_10": {
            "fixed_lot_size_btc": strategy_dict["fixed_lot_size_btc"],
            "maximum_inventory_lots": strategy_dict["maximum_inventory_lots"],
            "maximum_order_age_ticks": strategy_dict["maximum_order_age_ticks"],
            "minimum_order_lifetime_ticks": strategy_dict["minimum_order_lifetime_ticks"],
            "requote_threshold_ticks": strategy_dict["requote_threshold_ticks"],
            "time_horizon_ticks": strategy_dict["time_horizon_ticks"],
            "risk_aversion_gamma": strategy_dict["risk_aversion_gamma"],
            "inventory_skew_strength": strategy_dict["inventory_skew_strength"],
            "imbalance_skew_strength": strategy_dict["imbalance_skew_strength"],
            "volatility_cap": strategy_dict["volatility_cap"],
        },
        "frozen_risk_boundary": {
            "aggregate_hard_loss_usdt": str(limits.aggregate_hard_loss_usdt),
            "session_hard_drawdown_usdt": str(limits.session_hard_drawdown_usdt),
            "session_soft_drawdown_usdt": str(limits.session_soft_drawdown_usdt),
            "maximum_inventory_btc": str(limits.maximum_inventory_btc),
            "maximum_owned_bid": limits.maximum_owned_bid,
            "maximum_owned_ask": limits.maximum_owned_ask,
            "maximum_unresolved_flatten": limits.maximum_unresolved_flatten,
            "hard_kill_drawdown_pct": strategy_dict["hard_kill_drawdown_pct"],
            "soft_session_loss_pct": strategy_dict["soft_session_loss_pct"],
            "maximum_margin_utilization": strategy_dict["maximum_margin_utilization"],
        },
        "campaign_limits": limits.to_dict(),
        "market_spec": {
            "symbol": "BTC/USDT:USDT",
            "contract_size": "0.01",
            "amount_step": "1",
            "min_amount": "1",
            "price_tick": "0.1",
            "linear": True,
            "inverse": False,
            "leverage": 3,
            "margin_mode": "isolated",
            "position_mode": "net_mode",
        },
    }
    write_json_atomic(prep_dir / "frozen_risk_specification.json", frozen_risk)

    # 6. session_schedule.json (12 sequential session slots)
    session_slots: list[dict[str, Any]] = []
    for idx in range(1, limits.maximum_sessions + 1):
        slot_str = f"s{idx:02d}"
        s_nonce = hashlib.sha256(f"{campaign_id}|{slot_str}|{actual_fingerprint}".encode()).hexdigest()[:8]
        s_id = f"r2-session-{stamp}-{slot_str}:p0:{s_nonce}"
        session_slots.append({
            "slot_index": idx,
            "slot_name": slot_str,
            "session_id": s_id,
            "expected_session_arm_token": f"OKX_DEMO:{s_id}",
            "timeout_budget_ms": limits.maximum_session_wall_ms,
            "max_normal_creates": limits.maximum_session_normal_creates,
            "soft_loss_usdt": str(limits.session_soft_drawdown_usdt),
            "hard_loss_usdt": str(limits.session_hard_drawdown_usdt),
        })
    schedule = {
        "campaign_id": campaign_id,
        "total_slots": len(session_slots),
        "maximum_campaign_wall_ms": limits.maximum_campaign_wall_ms,
        "session_slots": session_slots,
    }
    write_json_atomic(prep_dir / "session_schedule.json", schedule)

    # 7. endpoint_permissions.json
    endpoint_permissions = {
        "allowed_trading_endpoints": {
            "fetch_markets": "Market enumeration & contract verification",
            "fetch_market_info": "Contract specification validation",
            "fetch_time": "Clock skew budget tracking",
            "fetch_balance": "Account equity & margin observation",
            "fetch_positions": "Authoritative position observation",
            "fetch_open_orders": "Authoritative open orders observation",
            "fetch_my_trades": "Authoritative fill cursor observation",
            "fetch_leverage": "Leverage configuration verification",
            "fetch_trading_fee": "Fee schedule validation",
            "create_order": "Post-only maker order placement during active quoting",
            "cancel_order": "Order cancellation during cancel-replace cycle",
            "cancel_all_owned": "Owned orders cancellation on session shutdown",
            "submit_emergency_flatten": "Reduce-only market order on hard drawdown breach only",
        },
        "prohibited_endpoints": {
            "set_position_mode": "PROHIBITED: Account configuration mutation",
            "set_leverage": "PROHIBITED: Leverage mutation",
            "transfer": "PROHIBITED: Asset transfer",
            "withdrawal": "PROHIBITED: Asset withdrawal",
            "live_endpoints": "PROHIBITED: Real-money production transport",
        },
        "policy": "DENY_UNKNOWN",
    }
    write_json_atomic(prep_dir / "endpoint_permissions.json", endpoint_permissions)

    # 8. qualification_expectations.json
    qualification_expectations = {
        "minimum_normal_fills": limits.minimum_normal_fills,
        "minimum_bid_fills": limits.minimum_bid_fills,
        "minimum_ask_fills": limits.minimum_ask_fills,
        "minimum_fifo_round_trips": limits.minimum_fifo_round_trips,
        "minimum_fill_balance": str(limits.minimum_fill_balance),
        "maximum_special_flatten_sessions": 2,
        "maximum_special_flatten_rate": "16.67%",
        "maximum_emergency_flattens": 0,
        "positive_net_pnl_required": True,
        "target_qualification_decision": "R2_CAMPAIGN_QUALIFICATION_PASSED",
    }
    write_json_atomic(prep_dir / "qualification_expectations.json", qualification_expectations)

    # 9. failure_matrix.json
    failure_matrix = {
        "failure_scenarios": [
            {"condition": "Live transport requested", "action": "FAIL_CLOSED_IMMEDIATE", "error": "DemoAdapterError"},
            {"condition": "Missing/blank credentials", "action": "FAIL_CLOSED_PRE_CONNECTION", "error": "DemoAdapterError"},
            {"condition": "Arm token mismatch", "action": "FAIL_CLOSED_PRE_ARM", "error": "CampaignError"},
            {"condition": "Clock skew > 1500ms", "action": "QUOTE_HALT_FAIL_CLOSED", "error": "ClockSkewBudgetError"},
            {"condition": "Unowned position at startup", "action": "FAIL_CLOSED_RECONCILIATION", "error": "DemoAdapterError"},
            {"condition": "Foreign order detected", "action": "FAIL_CLOSED_RECONCILIATION", "error": "DemoAdapterError"},
            {"condition": "Session soft loss breach (>22.50 USDT)", "action": "ENTER_WORKOFF_ONLY", "error": "DrawdownAlert"},
            {"condition": "Session hard loss breach (>37.50 USDT)", "action": "EMERGENCY_FLATTEN_AND_STOP", "error": "SessionHardLossError"},
            {"condition": "Aggregate hard loss breach (>75.00 USDT)", "action": "PERMANENT_CAMPAIGN_SHUTDOWN", "error": "AggregateHardLossError"},
            {"condition": "Mutation call network failure", "action": "FAIL_CLOSED_ZERO_RETRIES", "error": "MutationFailure"},
        ]
    }
    write_json_atomic(prep_dir / "failure_matrix.json", failure_matrix)

    # 10. credential_contract.json
    credential_contract = {
        "required_credential_names": ["OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"],
        "credentials_loaded": False,
        "credentials_read": 0,
        "credentials_serialized": False,
        "handling_rule": (
            "Never log, print, store, hash, or serialize credential values. "
            "Load in memory only upon authorized session execution."
        ),
    }
    write_json_atomic(prep_dir / "credential_contract.json", credential_contract)

    # 11. demo_transport_contract.json
    demo_transport = {
        "execution_mode": "OKX_DEMO",
        "sandbox_mode_required": True,
        "simulated_trading_header": "x-simulated-trading",
        "simulated_trading_header_value": "1",
        "live_mode_available": False,
        "transport_policy": "Fail-closed if sandboxMode is not True or simulated-trading header != '1'",
    }
    write_json_atomic(prep_dir / "demo_transport_contract.json", demo_transport)

    # 12. expected_evidence_schema.json
    expected_evidence_schema = {
        "campaign_artifacts": [
            "campaign_identity.json",
            "campaign_summary.json",
            "session_manifest.json",
            "qualification_metrics.json",
            "qualification_decision.json",
            "completion_hashes.json",
            "R2_CAMPAIGN_COMPLETED.json",
        ],
        "session_artifacts": [
            "session_identity.json",
            "session_summary.json",
            "fills.json",
            "round_trips.json",
            "reconciliation_audit.json",
            "completion_hashes.json",
            "SESSION_COMPLETED.json",
        ],
        "strictly_prohibited_fields": [
            "apiKey",
            "secret",
            "password",
            "passphrase",
            "raw_secret",
            "account_uid_raw",
        ],
    }
    write_json_atomic(prep_dir / "expected_evidence_schema.json", expected_evidence_schema)

    # 13. completion_hashes.json
    completion_hashes = generate_completion_hashes(prep_dir, "R2_PREPARATION_COMPLETED.json")
    write_json_atomic(prep_dir / "completion_hashes.json", completion_hashes)

    # 14. R2_PREPARATION_COMPLETED.json (terminal marker written last)
    r2_prep_completed = {
        "status": "R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION",
        "preparation_package_id": package_id,
        "r2_campaign_id": campaign_id,
        "r2_run_id": run_id,
        "r0_closure_ref": expected_r0_ref,
        "r0_evidence_id": expected_r0_ref,
        "r1_run_id": r1_run_id,
        "candidate_fingerprint": actual_fingerprint,
        "timestamp_utc": stamp,
        "files_verified": len(completion_hashes),
        "completion_hashes_sha256": canonical_sha256(json.dumps(completion_hashes, sort_keys=True)),
        "r2_authorized": False,
        "r2_executed": False,
        "sessions_executed": 0,
        "credential_reads": 0,
        "network_attempts": 0,
        "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0,
        "create_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_mutation_attempts": 0,
        "git_write_operation": False,
        "production_authorized": False,
    }
    write_json_atomic(prep_dir / "R2_PREPARATION_COMPLETED.json", r2_prep_completed)

    return prep_dir


def main() -> None:
    print("=== Initiating R2 Successor Economic Campaign Preparation ===")
    guard = _OfflineSocketGuard()
    with guard:
        stamp = "20260904T124817Z"
        print(f"Timestamp: {stamp}")
        print("Verifying candidate source fingerprint and prerequisite evidence...")
        prep_dir = build_r2_campaign_preparation_package(
            root=ROOT,
            stamp=stamp,
            r0_closure_ref=CANONICAL_R0_CLOSURE_REF,
            r1_run_id=CANONICAL_R1_RUN_ID,
        )
        print(f"R2 Campaign Preparation Package written to: {prep_dir}")

    assert guard.attempts == [], "Socket attempts detected during R2 preparation!"
    print("=== R2 Campaign Prepared Successfully (Offline / Zero Network) ===")
    print("Status: R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION")
    print("Hard stop enforced: zero network, zero credentials, zero order mutations, R2 unexecuted.")


if __name__ == "__main__":
    main()
