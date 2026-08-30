"""Freeze the activity-budget successor formal OKX Demo package offline.

This phase verifies the completed shutdown repair and its separately armed
read-only preflight, runs socket-denied regressions, and writes a fresh,
non-overwriting package.  It never reads credentials, constructs an exchange,
creates an execution marker, or submits/amends/cancels an order.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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


REPAIR_ID = "shutdown-repair-offline-20260806T142614Z"
PREFLIGHT_RUN_ID = "preflight-20260806T144814Z"
FAILED_PACKAGE_ID = "formal-package-20260805T162735Z"
FAILED_FORMAL_RUN_ID = "formal-20260805T162735Z"
REPAIR_ARTIFACT_ROOT = Path("artifacts") / "okx_demo_activity_budget_shutdown_repair"
SUCCESSOR_SOURCE_FILES = (
    "okx_activity_budget_formal_prepare.py",
    "tests/test_okx_activity_budget_shutdown_repair.py",
)
TARGETED_TESTS = (
    "tests/test_okx_activity_budget_shutdown_repair.py",
    "tests/test_okx_fill_restart_formal.py",
    "tests/test_okx_fill_restart_gateway.py",
    "tests/test_okx_fill_restart_executor.py",
    "tests/test_okx_fill_restart_validation.py",
)


class ActivityBudgetFormalPackageError(RuntimeError):
    pass


def _fresh_identifiers(now: datetime | None = None) -> tuple[str, str]:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    return f"formal-package-{stamp}", f"formal-{stamp}"


def _source_hashes(root: Path) -> dict[str, str]:
    hashes = formal_source_hashes(root)
    for relative in SUCCESSOR_SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise ActivityBudgetFormalPackageError(
                f"successor formal source is missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _predecessor_template(root: Path) -> dict[str, object]:
    package = root / ARTIFACT_ROOT / FAILED_PACKAGE_ID
    terminal_path = package / "COMPLETED.json"
    manifest_path = package / "formal_run" / "formal_completion_hashes.json"
    spec_path = package / "specification" / "formal_package_spec.json"
    if not all(path.is_file() for path in (terminal_path, manifest_path, spec_path)):
        raise ActivityBudgetFormalPackageError("failed predecessor is incomplete")
    terminal = _json(terminal_path)
    if (
        terminal.get("status") != "OKX_DEMO_FILL_RESTART_SAFETY_FAILED"
        or terminal.get("formal_run_id") != FAILED_FORMAL_RUN_ID
        or terminal.get("formal_execution_marker_count") != 1
    ):
        raise ActivityBudgetFormalPackageError("failed predecessor terminal drift")
    manifest = _json(manifest_path)
    expected = manifest.get("specification/formal_package_spec.json")
    if not isinstance(expected, str) or _sha256(spec_path) != expected:
        raise ActivityBudgetFormalPackageError("failed predecessor template hash mismatch")
    template = _json(spec_path)
    if (
        template.get("package_id") != FAILED_PACKAGE_ID
        or template.get("formal_run_id") != FAILED_FORMAL_RUN_ID
    ):
        raise ActivityBudgetFormalPackageError("failed predecessor identity drift")
    return template


def _repair_test_contract(root: Path, repair_id: str) -> dict[str, object]:
    path = REPAIR_ARTIFACT_ROOT / repair_id / "tests" / "test_summary.json"
    full_path = root / path
    summary = _json(full_path)
    required = (
        "shutdown_targeted",
        "root_successor_non_optuna",
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
        raise ActivityBudgetFormalPackageError("carried shutdown repair gate failed")
    return {
        "repair_test_summary_sha256": _sha256(full_path),
        "shutdown_targeted_passed": summary["shutdown_targeted"]["passed"],
        "root_successor_non_optuna_passed": summary[
            "root_successor_non_optuna"
        ]["passed"],
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
        raise ActivityBudgetFormalPackageError("formal package ID is invalid")
    if not formal_run_id.startswith("formal-") or any(
        character.isspace() for character in formal_run_id
    ):
        raise ActivityBudgetFormalPackageError("formal run ID is invalid")
    if package_id == FAILED_PACKAGE_ID or formal_run_id == FAILED_FORMAL_RUN_ID:
        raise ActivityBudgetFormalPackageError("failed predecessor ID reuse refused")
    if (
        repair_audit.get("evidence_kind") != "activity_budget_shutdown_repair"
        or repair_audit.get("formal_predecessor_package_id") != FAILED_PACKAGE_ID
        or repair_audit.get("formal_predecessor_run_id") != FAILED_FORMAL_RUN_ID
        or repair_audit.get("formal_predecessor_verified") is not True
        or repair_audit.get("passed") is not True
        or preflight_audit.get("passed") is not True
    ):
        raise ActivityBudgetFormalPackageError(
            "activity-budget repair or preflight evidence is invalid"
        )

    template = _predecessor_template(root)
    profile = load_promoted_profile(root)
    if template.get("profile_binding_sha256") != profile.binding_sha256:
        raise ActivityBudgetFormalPackageError("promoted profile binding drift")
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
        raise ActivityBudgetFormalPackageError("frozen risk budget drift")

    runtime = copy.deepcopy(template["runtime_configuration"])
    runtime.update({
        "formal_run_id": formal_run_id,
        "package_id": package_id,
        "source_manifest_sha256": source_manifest_sha256,
        "market_fingerprint": preflight_audit["market_fingerprint"],
        "profile_binding_sha256": profile.binding_sha256,
        "formal_execution_armed": False,
        "network_allowed_during_package_freeze": False,
        "orders_allowed_during_package_freeze": False,
    })
    runtime["activity_budget_shutdown_contract"] = {
        "terminal_reasons": [
            "NORMAL_CREATE_BUDGET_EXHAUSTED",
            "FORMAL_DEADLINE_REACHED",
        ],
        "ordered_flow": [
            "stop_normal_placement_and_persist_reason",
            "cancel_authoritative_owned_set",
            "reconcile_fills_fees_position_balance_cursor_orders",
            "require_authoritative_position_equals_ledger_inventory",
            "single_flight_reduce_only_flatten_when_nonzero",
            "accept_deduplicated_exact_partial_fill_sum",
            "require_authoritative_and_ledger_flat_and_owned_orders_empty",
            "close_activity_insufficient",
        ],
        "safe_terminal_status": "OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT",
        "mismatch_or_unresolved_outcome": "fail_closed_safety_or_reconciliation",
        "maximum_flatten_creates": 1,
    }
    runtime_configuration_sha256 = canonical_sha256(runtime)

    artifact_contract = copy.deepcopy(template["artifact_contract"])
    artifact_contract.update({
        "package_directory": package_id,
        "formal_run_id": formal_run_id,
        "formal_execution_marker_created_by_package_freeze": False,
        "maximum_formal_execution_markers": 1,
    })
    endpoint_contract = copy.deepcopy(template["endpoint_contract"])
    endpoint_contract["hostname"] = ccxt_contract["rest_host"]
    endpoint_contract["request_paths"] = {
        "private": ccxt_contract["private_paths"],
        "public": ccxt_contract["public_paths"],
    }
    endpoint_contract["fallback_to_live"] = False
    endpoint_contract["live_endpoint_attempt_budget"] = 0

    fixture_contract = _repair_test_contract(
        root, str(repair_audit["repair_id"])
    )
    base: dict[str, object] = {
        "schema_version": 3,
        "protocol_id": FORMAL_PROTOCOL_ID,
        "phase": "ACTIVITY_BUDGET_SUCCESSOR_FORMAL_PACKAGE_FREEZE_OFFLINE",
        "package_id": package_id,
        "formal_run_id": formal_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "immutable_failed_predecessor": {
            "package_id": FAILED_PACKAGE_ID,
            "formal_run_id": FAILED_FORMAL_RUN_ID,
            "status": "OKX_DEMO_FILL_RESTART_SAFETY_FAILED",
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
        "restart_contract": copy.deepcopy(template["restart_contract"]),
        "fixture_manifest": {
            "carried_activity_budget_repair_gates": fixture_contract,
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


def _run_targeted_suite(root: Path, output: Path) -> dict[str, object]:
    tests = output / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    audit_path = tests / "activity_formal_targeted_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix="okx-activity-formal-"))
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TARGETED_TESTS,
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "okx_offline_network_guard",
        "--basetemp",
        str(base_temp),
    ]
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY",
        "OKX_SECRET",
        "OKX_PASSPHRASE",
        "OKX_API_SECRET",
        "OKX_API_PASSPHRASE",
    ):
        environment[variable] = ""
    environment["OKX_EXECUTION_MODE"] = "OFFLINE_FIXTURE"
    environment["OKX_OFFLINE_NETWORK_AUDIT"] = str(audit_path.resolve())
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    _write_text(tests / "activity_formal_targeted.txt", completed.stdout)
    matches = re.findall(r"(\d+) passed", completed.stdout)
    audit = _json(audit_path) if audit_path.is_file() else {
        "network_attempts": -1,
        "live_endpoint_attempts": -1,
        "optuna_imported": True,
    }
    result = {
        "returncode": completed.returncode,
        "passed": int(matches[-1]) if matches else 0,
        "network_attempts": int(audit.get("network_attempts", -1)),
        "live_endpoint_attempts": int(audit.get("live_endpoint_attempts", -1)),
        "optuna_imported": bool(audit.get("optuna_imported", True)),
        "command_scope": list(TARGETED_TESTS),
        "basetemp_outside_project": True,
    }
    result["passed_gate"] = all((
        result["returncode"] == 0,
        result["passed"] > 0,
        result["network_attempts"] == 0,
        result["live_endpoint_attempts"] == 0,
        result["optuna_imported"] is False,
    ))
    return result


def prepare_activity_budget_formal_package(
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
            raise ActivityBudgetFormalPackageError(
                "preflight is not bound to the selected shutdown repair"
            )
        hashes = _source_hashes(root)
        package_id, formal_run_id = _fresh_identifiers()
        output = root / ARTIFACT_ROOT / package_id
        if output.exists():
            raise ActivityBudgetFormalPackageError("formal package ID collision")
        if any(
            _json(path).get("formal_run_id") == formal_run_id
            for path in (root / ARTIFACT_ROOT).glob(
                "formal-package-*/run/formal_run_manifest.json"
            )
        ):
            raise ActivityBudgetFormalPackageError("formal run ID reuse refused")
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

        targeted = _run_targeted_suite(root, output)
        _write_json(output / "tests" / "test_summary.json", {
            "activity_formal_targeted": targeted,
            "carried_activity_budget_repair_gates": spec["fixture_manifest"],
        })
        if not targeted["passed_gate"]:
            raise ActivityBudgetFormalPackageError(
                "activity-budget successor regression failed"
            )
        if _source_hashes(root) != hashes:
            raise ActivityBudgetFormalPackageError(
                "formal source changed during package freeze"
            )
        _write_json(output / "audits" / "network_mutation_audit.json", {
            "socket_denied": True,
            "network_attempts": len(guard.attempts),
            "preflight_attempts": 0,
            "orders_submitted": 0,
            "orders_amended": 0,
            "orders_cancelled": 0,
            "account_configuration_mutations": 0,
            "formal_execution_markers": 0,
            "live_endpoint_attempts": 0,
            "live_orders": 0,
        })
        secret_scan = _artifact_secret_scan(output)
        _write_json(output / "audits" / "secret_scan.json", secret_scan)
        if not secret_scan["passed"]:
            raise ActivityBudgetFormalPackageError(
                "secret pattern found in formal package"
            )
        decision = {
            "status": FORMAL_PACKAGE_STATUS,
            "activity_budget_shutdown_repair_bound": True,
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
            "# Activity-budget successor formal OKX Demo package\n\n"
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
            "completion_hashes_sha256": _sha256(
                output / "completion_hashes.json"
            ),
            "package_specification_sha256": formal.package_specification_sha256,
            "source_manifest_sha256": formal.source_manifest_sha256,
            "arm_token_sha256": token_hash,
            "arm_token_serialized": False,
        }
        _write_json(output / "FORMAL_PACKAGE_COMPLETED.json", terminal)
    if guard.attempts:
        raise ActivityBudgetFormalPackageError(
            "network attempt blocked during package freeze"
        )
    loaded = load_frozen_package(root, formal.package_id)
    if loaded.spec != formal:
        raise ActivityBudgetFormalPackageError(
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
        output, formal = prepare_activity_budget_formal_package(
            args.root,
            repair_id=args.repair_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"ACTIVITY_FORMAL_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
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
