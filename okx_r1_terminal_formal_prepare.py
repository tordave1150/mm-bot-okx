"""Freeze one R1/terminal-reconciliation successor formal package offline."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from okx_demo_profile import load_promoted_profile
from okx_fill_restart_executor import load_frozen_package
from okx_fill_restart_formal import FORMAL_PROTOCOL_ID, FormalRunSpecification
from okx_fill_restart_formal_prepare import (
    FORMAL_PACKAGE_STATUS,
    _OfflineSocketGuard,
    _artifact_secret_scan,
    _formal_completion_hashes,
    _installed_formal_ccxt_contract,
    formal_source_hashes,
    verify_preflight_evidence,
)
from okx_fill_restart_offline import (
    ARTIFACT_ROOT,
    _json,
    _sha256,
    _write_json,
    _write_text,
)
from okx_fill_restart_preflight import verify_offline_evidence
from okx_fill_restart_validation import canonical_sha256
from okx_r1_terminal_reconciliation_repair_offline import (
    ROOT_DESELECT,
    _run_suite,
)


REPAIR_ID = "r1-terminal-repair-offline-20260809T161631Z"
PREFLIGHT_RUN_ID = "preflight-20260809T161723Z"
FAILED_PACKAGE_ID = "formal-package-20260809T152113Z"
FAILED_FORMAL_RUN_ID = "formal-20260809T152113Z"
REPAIR_ARTIFACT_ROOT = (
    Path("artifacts") / "okx_demo_r1_terminal_reconciliation_repair"
)
SUCCESSOR_SOURCE_FILES = (
    "okx_r1_terminal_formal_prepare.py",
)
TARGETED_TESTS = (
    "tests/test_okx_r1_terminal_reconciliation_repair.py",
    "tests/test_okx_signed_age_prearm_terminal_repair.py",
    "tests/test_okx_r2_warmup_audit_counter_repair.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_validation.py",
    "tests/test_okx_future_book_timestamp_repair.py",
    "tests/test_okx_fill_restart_preflight.py",
    "tests/test_okx_fill_restart_preflight_prepare.py",
    *(f"--deselect={node}" for node in ROOT_DESELECT),
)
PREDECESSOR_HASHES = {
    "formal_run/FORMAL_EXECUTION_ARMED.json": (
        "1f085aac5729aa8fcccfdc9601cae2370794da3d98238110567288c58dd2f830"
    ),
    "formal_run/UNRESOLVED_FAILURE.json": (
        "c0771fe60e71f80e8b59b4efcd97ff2351caab6536ff3d3b33dd19a72161d379"
    ),
    "formal_run/state/formal_state.json": (
        "a4d1dcf6833b46f87e88831de9dd5760f34f4f3e53c31f99447daf8bc9fc82f2"
    ),
    "formal_run/state/validation_state.json": (
        "5bffd99213150ee96d00ff4675c57c30cef2b235a7b3c965d8183d8f448fe57d"
    ),
    "formal_run/streams/safety.jsonl": (
        "5bbf05e8585f6162fccf0642deb38be00a937630f6cd2210df7be766636c4703"
    ),
}


class R1TerminalFormalPackageError(RuntimeError):
    pass


def _fresh_identifiers(now: datetime | None = None) -> tuple[str, str]:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise R1TerminalFormalPackageError(
            "formal identifier timestamp must be timezone aware"
        )
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"formal-package-{stamp}", f"formal-{stamp}"


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = formal_source_hashes(root)
    for relative in SUCCESSOR_SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise R1TerminalFormalPackageError(
                f"R1/terminal successor formal source is missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _predecessor_template(root: Path) -> dict[str, object]:
    package = root / ARTIFACT_ROOT / FAILED_PACKAGE_ID
    failures = [
        relative for relative, expected in PREDECESSOR_HASHES.items()
        if not (package / relative).is_file()
        or _sha256(package / relative) != expected
    ]
    unresolved = _json(package / "formal_run" / "UNRESOLVED_FAILURE.json")
    spec_path = package / "specification" / "formal_package_spec.json"
    if not spec_path.is_file():
        failures.append("specification/formal_package_spec.json")
    if any((
        unresolved.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
        unresolved.get("reason") != (
            "FormalSafetyError:R1 checkpoint requires no orders and nonzero position"
        ),
        unresolved.get("formal_execution_marker_count") != 1,
        unresolved.get("report_recovery_required") is not True,
        unresolved.get("second_order_run_allowed") is not False,
    )):
        failures.append("formal_predecessor_contract")
    if failures:
        raise R1TerminalFormalPackageError(
            "R1/terminal failed predecessor drift: " + ",".join(failures[:8])
        )
    template = _json(spec_path)
    if (
        template.get("package_id") != FAILED_PACKAGE_ID
        or template.get("formal_run_id") != FAILED_FORMAL_RUN_ID
    ):
        raise R1TerminalFormalPackageError(
            "failed predecessor template identity drift"
        )
    return template


def _repair_test_contract(root: Path, repair_id: str) -> dict[str, object]:
    path = REPAIR_ARTIFACT_ROOT / repair_id / "tests" / "test_summary.json"
    full_path = root / path
    summary = _json(full_path)
    required = (
        "r1_terminal_repair_targeted", "root_non_optuna",
        "backtest_non_optuna",
    )
    if any(
        not isinstance(summary.get(name), dict)
        or summary[name].get("passed_gate") is not True
        or summary[name].get("network_attempts") != 0
        or summary[name].get("live_endpoint_attempts") != 0
        or summary[name].get("optuna_imported") is not False
        for name in required
    ):
        raise R1TerminalFormalPackageError("carried R1/terminal repair gate failed")
    return {
        "repair_test_summary_sha256": _sha256(full_path),
        "r1_terminal_repair_targeted_passed": summary[
            "r1_terminal_repair_targeted"
        ]["passed"],
        "root_non_optuna_passed": summary["root_non_optuna"]["passed"],
        "backtest_non_optuna_passed": summary["backtest_non_optuna"]["passed"],
        "network_attempts": 0,
        "live_endpoint_attempts": 0,
        "optuna_imported": False,
    }


def _build_spec(
    *,
    root: Path,
    package_id: str,
    formal_run_id: str,
    repair_audit: dict[str, object],
    preflight_audit: dict[str, object],
    hashes: dict[str, str],
) -> tuple[dict[str, object], FormalRunSpecification]:
    if not package_id.startswith("formal-package-") or any(
        character.isspace() for character in package_id
    ):
        raise R1TerminalFormalPackageError("formal package ID is invalid")
    if not formal_run_id.startswith("formal-") or any(
        character.isspace() for character in formal_run_id
    ):
        raise R1TerminalFormalPackageError("formal run ID is invalid")
    if package_id == FAILED_PACKAGE_ID or formal_run_id == FAILED_FORMAL_RUN_ID:
        raise R1TerminalFormalPackageError("failed formal predecessor ID reuse refused")
    if any((
        repair_audit.get("evidence_kind")
        != "r1_terminal_reconciliation_repair",
        repair_audit.get("formal_predecessor_package_id") != FAILED_PACKAGE_ID,
        repair_audit.get("formal_predecessor_run_id") != FAILED_FORMAL_RUN_ID,
        repair_audit.get("formal_predecessor_verified") is not True,
        repair_audit.get("passed") is not True,
        preflight_audit.get("passed") is not True,
        preflight_audit.get("run_id") != PREFLIGHT_RUN_ID,
    )):
        raise R1TerminalFormalPackageError(
            "R1/terminal repair or preflight evidence is invalid"
        )

    template = _predecessor_template(root)
    profile = load_promoted_profile(root)
    if template.get("profile_binding_sha256") != profile.binding_sha256:
        raise R1TerminalFormalPackageError("promoted profile binding drift")
    source_manifest_sha256 = canonical_sha256(hashes)
    ccxt_contract = _installed_formal_ccxt_contract()
    risk_budget = copy.deepcopy(template["risk_budget"])
    expected_risk = {
        "capital_usdt": "750",
        "hard_kill_usdt": "37.50",
        "leverage": 3,
        "maximum_inventory_btc": "0.01",
        "maximum_normal_creates": 120,
        "maximum_owned_ask": 1,
        "maximum_owned_bid": 1,
        "maximum_unresolved_flatten": 1,
        "maximum_wall_minutes": 120,
        "soft_guard_usdt": "22.50",
    }
    if risk_budget != expected_risk:
        raise R1TerminalFormalPackageError("frozen risk budget drift")

    runtime = copy.deepcopy(template["runtime_configuration"])
    if not isinstance(runtime.get("r2_warmup_audit_contract"), dict):
        raise R1TerminalFormalPackageError("carried R2 warmup contract is missing")
    if not isinstance(runtime.get("signed_age_prearm_terminal_contract"), dict):
        raise R1TerminalFormalPackageError("carried signed-age contract is missing")
    runtime.update({
        "formal_run_id": formal_run_id,
        "package_id": package_id,
        "source_manifest_sha256": source_manifest_sha256,
        "market_fingerprint": preflight_audit["market_fingerprint"],
        "profile_binding_sha256": profile.binding_sha256,
        "formal_execution_armed": False,
        "network_allowed_during_package_freeze": False,
        "orders_allowed_during_package_freeze": False,
        "maximum_market_age_ms": 1_000,
        "maximum_clock_skew_ms": 1_500,
    })
    runtime["r1_terminal_reconciliation_contract"] = {
        "filled_order_removed_before_r1_checkpoint": True,
        "missing_identity_requires_exact_same_side_fill_delta": True,
        "unexplained_or_wrong_side_disappearance_fail_closed": True,
        "controller_and_engine_prevalidated_before_checkpoint": True,
        "rejected_r1_transition_is_atomic": True,
        "emergency_flatten_separate_from_r1_transition": True,
        "maximum_flatten_orders": 1,
        "multi_partial_flatten_supported": True,
        "controller_engine_account_terminal_reconciliation": True,
        "historical_closed_orders_not_open_durable_state": True,
        "post_shutdown_flat_account_snapshots_required": 2,
        "account_only_terminal_reporting_without_market_book": True,
        "ambiguous_terminal_state": "unresolved_fail_closed",
    }
    runtime_configuration_sha256 = canonical_sha256(runtime)

    artifact_contract = copy.deepcopy(template["artifact_contract"])
    artifact_contract.update({
        "package_directory": package_id,
        "formal_run_id": formal_run_id,
        "formal_execution_marker_created_by_package_freeze": False,
        "maximum_formal_execution_markers": 1,
        "gateway_audit_stream": "formal_run/streams/gateway_audit.jsonl",
        "terminal_account_only_evidence": (
            "formal_run/terminal/account_only_snapshots.json"
        ),
        "terminal_durable_reconciliation_evidence": (
            "formal_run/terminal/durable_reconciliation.json"
        ),
    })
    endpoint_contract = copy.deepcopy(template["endpoint_contract"])
    endpoint_contract["hostname"] = ccxt_contract["rest_host"]
    endpoint_contract["request_paths"] = {
        "private": ccxt_contract["private_paths"],
        "public": ccxt_contract["public_paths"],
    }
    endpoint_contract["fallback_to_live"] = False
    endpoint_contract["live_endpoint_attempt_budget"] = 0
    restart_contract = copy.deepcopy(template["restart_contract"])
    restart_contract["r1_controller_engine_prevalidation"] = True
    restart_contract["r1_transition_includes_flatten"] = False
    restart_contract["r2_quote_engine_warmup_before_probe"] = True
    restart_contract["cumulative_gateway_audit_required"] = True
    restart_contract["terminal_account_only_reporting"] = True
    fixture_contract = _repair_test_contract(root, str(repair_audit["repair_id"]))

    base: dict[str, object] = {
        "schema_version": 7,
        "protocol_id": FORMAL_PROTOCOL_ID,
        "phase": "R1_TERMINAL_RECONCILIATION_SUCCESSOR_FORMAL_PACKAGE_FREEZE_OFFLINE",
        "package_id": package_id,
        "formal_run_id": formal_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "immutable_failed_predecessor": {
            "package_id": FAILED_PACKAGE_ID,
            "formal_run_id": FAILED_FORMAL_RUN_ID,
            "status": "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
            "reason": (
                "FormalSafetyError:R1 checkpoint requires no orders and nonzero position"
            ),
            "resume_or_rerun_allowed": False,
            "fixed_hashes_checked": repair_audit[
                "formal_predecessor_fixed_hashes_checked"
            ],
        },
        "repair_evidence_audit": repair_audit,
        "offline_evidence_audit": repair_audit,
        "preflight_evidence_audit": preflight_audit,
        "profile_binding": profile.binding_payload,
        "profile_binding_sha256": profile.binding_sha256,
        "runtime_configuration": runtime,
        "runtime_configuration_sha256": runtime_configuration_sha256,
        "source_hashes": hashes,
        "source_manifest_sha256": source_manifest_sha256,
        "market_spec": preflight_audit["market_spec"],
        "ccxt_contract": ccxt_contract,
        "endpoint_contract": endpoint_contract,
        "risk_budget": risk_budget,
        "restart_contract": restart_contract,
        "fixture_manifest": {
            "carried_r1_terminal_repair_gates": fixture_contract,
            "successor_targeted_scope": list(TARGETED_TESTS),
        },
        "artifact_contract": artifact_contract,
        "conduct_contract": copy.deepcopy(template["conduct_contract"]),
        "package_boundary": {
            "network_attempts": 0,
            "preflight_executed_in_this_task": False,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "formal_execution_armed": False,
            "formal_arm_token_serialized": False,
            "execution_marker_created": False,
            "account_configuration_mutations": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
        },
    }
    package_specification_sha256 = canonical_sha256(base)
    formal = FormalRunSpecification(
        formal_run_id=formal_run_id,
        package_id=package_id,
        package_specification_sha256=package_specification_sha256,
        source_manifest_sha256=source_manifest_sha256,
        runtime_configuration_sha256=runtime_configuration_sha256,
        profile_binding_sha256=profile.binding_sha256,
        market_fingerprint=str(preflight_audit["market_fingerprint"]),
        offline_completion_sha256=str(repair_audit["completion_hashes_sha256"]),
        preflight_completion_sha256=str(
            preflight_audit["completion_hashes_sha256"]
        ),
        preflight_decision_sha256=str(preflight_audit["decision_sha256"]),
        ccxt_source_sha256=str(ccxt_contract["okx_source_sha256"]),
    )
    formal.validate()
    spec = dict(base)
    spec["package_specification_sha256"] = package_specification_sha256
    spec["package_hash_contract"] = (
        "SHA-256 of canonical package payload before package hash fields"
    )
    spec["formal_controller_specification"] = formal.to_dict()
    return spec, formal


def prepare_r1_terminal_formal_package(
    root: Path,
    *,
    repair_id: str = REPAIR_ID,
    preflight_run_id: str = PREFLIGHT_RUN_ID,
) -> tuple[Path, FormalRunSpecification]:
    root = root.resolve()
    guard = _OfflineSocketGuard()
    with guard:
        repair = verify_offline_evidence(root, repair_id)
        preflight = verify_preflight_evidence(root, preflight_run_id)
        preflight_repair = _json(
            root / ARTIFACT_ROOT / preflight_run_id
            / "predecessor" / "offline_evidence_audit.json"
        )
        if (
            preflight_repair.get("repair_id") != repair_id
            or preflight_repair.get("completion_hashes_sha256")
            != repair.get("completion_hashes_sha256")
        ):
            raise R1TerminalFormalPackageError(
                "preflight is not bound to the selected R1/terminal repair"
            )
        hashes = _source_hashes(root)
        package_id, formal_run_id = _fresh_identifiers()
        output = root / ARTIFACT_ROOT / package_id
        if output.exists():
            raise R1TerminalFormalPackageError("formal package ID collision")
        if any(
            _json(path).get("formal_run_id") == formal_run_id
            for path in (root / ARTIFACT_ROOT).glob(
                "formal-package-*/run/formal_run_manifest.json"
            )
        ):
            raise R1TerminalFormalPackageError("formal run ID reuse refused")
        spec, formal = _build_spec(
            root=root,
            package_id=package_id,
            formal_run_id=formal_run_id,
            repair_audit=repair,
            preflight_audit=preflight,
            hashes=hashes,
        )
        output.mkdir(parents=True)
        _write_json(output / "predecessor" / "repair_evidence_audit.json", repair)
        _write_json(
            output / "predecessor" / "preflight_evidence_audit.json", preflight
        )
        _write_json(output / "specification" / "source_hashes.json", hashes)
        _write_json(output / "specification" / "formal_package_spec.json", spec)
        _write_json(
            output / "specification" / "formal_controller_spec.json",
            formal.to_dict(),
        )
        token_hash = hashlib.sha256(
            formal.expected_arm_token.encode("utf-8")
        ).hexdigest()
        _write_json(output / "run" / "formal_run_manifest.json", {
            "package_id": package_id,
            "formal_run_id": formal_run_id,
            "session_id": formal.session_id,
            "package_specification_sha256": formal.package_specification_sha256,
            "source_manifest_sha256": formal.source_manifest_sha256,
            "formal_execution_armed": False,
            "execution_marker_created": False,
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
        })
        targeted = _run_suite(
            root, output, "r1_terminal_formal_targeted", TARGETED_TESTS
        )
        _write_json(output / "tests" / "test_summary.json", {
            "r1_terminal_formal_targeted": targeted,
            "carried_r1_terminal_repair_gates": spec["fixture_manifest"],
        })
        if not targeted["passed_gate"]:
            raise R1TerminalFormalPackageError(
                "R1/terminal successor formal regression failed"
            )
        if _source_hashes(root) != hashes:
            raise R1TerminalFormalPackageError(
                "formal source changed during package freeze"
            )
        _write_json(output / "audits" / "network_mutation_audit.json", {
            "socket_denied": True,
            "network_attempts": len(guard.attempts),
            "preflight_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "formal_execution_markers": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        secret_scan = _artifact_secret_scan(output)
        _write_json(output / "audits" / "secret_scan.json", secret_scan)
        if not secret_scan["passed"]:
            raise R1TerminalFormalPackageError(
                "secret pattern found in formal package"
            )
        decision = {
            "status": FORMAL_PACKAGE_STATUS,
            "r1_terminal_reconciliation_repair_bound": True,
            "package_id": package_id,
            "formal_run_id": formal_run_id,
            "session_id": formal.session_id,
            "formal_execution_armed": False,
            "execution_marker_created": False,
            "preflight_executed_in_this_task": False,
            "network_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "flatten_dispatches": 0,
            "account_configuration_mutations": 0,
            "production_authorized": False,
            "live_mode_available": False,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
            "optuna_executed": False,
            "validation_opened": False,
            "holdout_opened": False,
            "git_write_operation": False,
            "next_boundary": (
                "separate explicit user instruction quoting this package, formal "
                "run, session, and exact arm token"
            ),
        }
        _write_json(output / "decision" / "formal_package_decision.json", decision)
        _write_text(
            output / "decision" / "formal_package_decision.md",
            "# R1/terminal successor formal OKX Demo package\n\n"
            f"- Package: `{package_id}`\n"
            f"- Formal run: `{formal_run_id}`\n"
            f"- Session: `{formal.session_id}`\n"
            "- Formal execution armed: `false`\n"
            "- Network attempts: `0`\n"
            "- Orders submitted/amended/cancelled: `0 / 0 / 0`\n\n"
            "A separate exact user authorization is required before Demo orders.\n",
        )
        completion = _formal_completion_hashes(output)
        _write_json(output / "completion_hashes.json", completion)
        terminal = {
            **decision,
            "phase_status": FORMAL_PACKAGE_STATUS,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "completion_files_checked": len(completion),
            "completion_hashes_sha256": _sha256(output / "completion_hashes.json"),
            "package_specification_sha256": formal.package_specification_sha256,
            "source_manifest_sha256": formal.source_manifest_sha256,
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
        }
        _write_json(output / "FORMAL_PACKAGE_COMPLETED.json", terminal)
    if guard.attempts:
        raise R1TerminalFormalPackageError(
            "network attempt blocked during package freeze"
        )
    loaded = load_frozen_package(root, formal.package_id)
    if loaded.spec != formal:
        raise R1TerminalFormalPackageError(
            "frozen package controller verification failed"
        )
    return output, formal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id", default=REPAIR_ID)
    parser.add_argument("--preflight-run-id", default=PREFLIGHT_RUN_ID)
    args = parser.parse_args()
    try:
        output, formal = prepare_r1_terminal_formal_package(
            args.root,
            repair_id=args.repair_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"R1_TERMINAL_FORMAL_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
        return 1
    print(json.dumps({
        "output": str(output),
        "package_id": formal.package_id,
        "formal_run_id": formal.formal_run_id,
        "session_id": formal.session_id,
        "arm_token": formal.expected_arm_token,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
