"""Freeze one successor formal OKX Demo package entirely offline.

The package binds the completed fill-cursor repair and the separately armed
read-only preflight.  It never reads credentials, constructs an exchange,
creates a formal execution marker, or submits/amends/cancels an order.
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
from okx_fill_restart_offline import ARTIFACT_ROOT, _json, _sha256, _write_json, _write_text
from okx_fill_restart_preflight import verify_offline_evidence
from okx_fill_restart_validation import canonical_sha256


REPAIR_ID = "repair-offline-20260805T155548Z"
PREFLIGHT_RUN_ID = "preflight-20260805T161046Z"
FAILED_PACKAGE_ID = "formal-package-20260805T151737Z"
FAILED_FORMAL_RUN_ID = "formal-20260805T151737Z"
SUCCESSOR_SOURCE_FILES = (
    "okx_fill_cursor_formal_prepare.py",
    "tests/test_okx_fill_cursor_formal_prepare.py",
)


class SuccessorFormalPackageError(RuntimeError):
    pass


def _run_successor_suite(root: Path, output: Path) -> dict[str, object]:
    tests = output / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    audit_path = tests / "successor_formal_targeted_network_audit.json"
    base_temp = Path(tempfile.mkdtemp(prefix="okx-formal-"))
    arguments = (
        "tests/test_okx_fill_cursor_formal_prepare.py",
        "tests/test_okx_fill_restart_formal.py",
        "tests/test_okx_fill_restart_gateway.py",
        "tests/test_okx_fill_restart_executor.py",
    )
    command = [
        sys.executable, "-m", "pytest", *arguments, "-q",
        "-p", "no:cacheprovider", "-p", "okx_offline_network_guard",
        "--basetemp", str(base_temp),
    ]
    environment = dict(os.environ)
    for variable in (
        "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
        "OKX_API_SECRET", "OKX_API_PASSPHRASE",
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
    _write_text(tests / "successor_formal_targeted.txt", completed.stdout)
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
        "command_scope": list(arguments),
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


def successor_formal_source_hashes(root: Path) -> dict[str, str]:
    hashes = formal_source_hashes(root)
    for relative in SUCCESSOR_SOURCE_FILES:
        path = root / relative
        if not path.is_file():
            raise SuccessorFormalPackageError(
                f"successor formal source is missing: {relative}"
            )
        hashes[relative] = _sha256(path)
    return dict(sorted(hashes.items()))


def _failed_template(root: Path) -> dict[str, object]:
    package = root / ARTIFACT_ROOT / FAILED_PACKAGE_ID
    manifest_path = package / "formal_run" / "formal_completion_hashes.json"
    spec_path = package / "specification" / "formal_package_spec.json"
    if not manifest_path.is_file() or not spec_path.is_file():
        raise SuccessorFormalPackageError("failed formal template is incomplete")
    manifest = _json(manifest_path)
    expected = manifest.get("specification/formal_package_spec.json")
    if not isinstance(expected, str) or _sha256(spec_path) != expected:
        raise SuccessorFormalPackageError("failed formal template hash mismatch")
    template = _json(spec_path)
    if (
        template.get("package_id") != FAILED_PACKAGE_ID
        or template.get("formal_run_id") != FAILED_FORMAL_RUN_ID
    ):
        raise SuccessorFormalPackageError("failed formal template identity drift")
    return template


def _repair_test_contract(root: Path, repair_id: str) -> dict[str, object]:
    path = (
        root / "artifacts" / "okx_demo_fill_cursor_repair" / repair_id
        / "tests" / "test_summary.json"
    )
    summary = _json(path)
    required = ("repair_targeted", "root_non_optuna", "backtest_non_optuna")
    if any(
        not isinstance(summary.get(name), dict)
        or summary[name].get("passed_gate") is not True
        or summary[name].get("network_attempts") != 0
        or summary[name].get("live_endpoint_attempts") != 0
        or summary[name].get("optuna_imported") is not False
        for name in required
    ):
        raise SuccessorFormalPackageError("carried repair regression gate failed")
    return {
        "repair_test_summary_sha256": _sha256(path),
        "repair_targeted_passed": summary["repair_targeted"]["passed"],
        "root_non_optuna_passed": summary["root_non_optuna"]["passed"],
        "backtest_non_optuna_passed": summary["backtest_non_optuna"]["passed"],
        "network_attempts": 0,
        "live_endpoint_attempts": 0,
        "optuna_imported": False,
    }


def build_successor_formal_spec(
    *,
    root: Path,
    package_id: str,
    formal_run_id: str,
    repair_audit: dict[str, object],
    preflight_audit: dict[str, object],
    hashes: dict[str, str],
) -> tuple[dict[str, object], FormalRunSpecification]:
    if (
        not package_id.startswith("formal-package-")
        or any(character.isspace() for character in package_id)
        or package_id == FAILED_PACKAGE_ID
    ):
        raise SuccessorFormalPackageError("successor formal package ID is invalid")
    if (
        not formal_run_id.startswith("formal-")
        or any(character.isspace() for character in formal_run_id)
        or formal_run_id == FAILED_FORMAL_RUN_ID
    ):
        raise SuccessorFormalPackageError("successor formal run ID is invalid")
    if repair_audit.get("evidence_kind") != "fill_cursor_repair":
        raise SuccessorFormalPackageError("fill-cursor repair evidence is required")
    if not repair_audit.get("passed") or not preflight_audit.get("passed"):
        raise SuccessorFormalPackageError("repair or preflight evidence is not passed")

    template = _failed_template(root)
    profile = load_promoted_profile(root)
    if template.get("profile_binding_sha256") != profile.binding_sha256:
        raise SuccessorFormalPackageError("promoted profile binding drift")
    source_manifest_sha256 = canonical_sha256(hashes)
    ccxt_contract = _installed_formal_ccxt_contract()

    risk_budget = copy.deepcopy(template["risk_budget"])
    if risk_budget != {
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
    }:
        raise SuccessorFormalPackageError("frozen risk budget drift")

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
    runtime["fill_pagination"] = {
        "ccxt_paginate": True,
        "maximum_calls_per_snapshot": 3,
        "page_size": 100,
        "recent_tail_query": True,
        "recent_tail_limit": 100,
        "query_order": ["paginated_history", "recent_tail"],
        "union_identity": "trade_id",
        "filter_before_union": "timestamp_ms >= formal_trade_since_ms",
        "overlap_field_equality_required": [
            "order_id", "client_id", "timestamp", "side", "price",
            "amount", "fee", "fee_currency", "maker_taker",
        ],
        "identical_overlap_policy": "deduplicate_once",
        "conflicting_duplicate_policy": "fail_closed",
        "stable_sort": ["timestamp_ms", "trade_id"],
        "same_timestamp_deduplication": True,
        "late_fill_cursor_behavior": "monotonic_fail_closed",
    }
    runtime["flatten_partial_fill_contract"] = {
        "maximum_flatten_creates": 1,
        "positive_partial_fill_count": "one_or_more",
        "all_fills_map_to_persisted_special_order": True,
        "exact_sum_to_known_position": True,
        "duplicate_and_reordered_partial_fills": "deduplicate_by_trade_id",
        "overfill_or_incomplete_fee": "fail_closed",
        "excluded_from_normal_activity_fifo_and_economics": True,
        "included_in_aggregate_account_reconciliation": True,
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

    fixture_contract = _repair_test_contract(root, str(repair_audit["repair_id"]))
    base: dict[str, object] = {
        "schema_version": 2,
        "protocol_id": FORMAL_PROTOCOL_ID,
        "phase": "SUCCESSOR_FORMAL_PACKAGE_FREEZE_OFFLINE",
        "package_id": package_id,
        "formal_run_id": formal_run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "immutable_failed_predecessor": {
            "package_id": FAILED_PACKAGE_ID,
            "formal_run_id": FAILED_FORMAL_RUN_ID,
            "status": "OKX_DEMO_FILL_RESTART_RECONCILIATION_FAILED",
            "resume_or_rerun_allowed": False,
            "fixed_hashes_checked": repair_audit["failed_formal_fixed_hashes_checked"],
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
            "carried_repair_offline_gates": fixture_contract,
            "successor_targeted_scope": [
                "tests/test_okx_fill_cursor_formal_prepare.py",
                "tests/test_okx_fill_restart_formal.py",
                "tests/test_okx_fill_restart_gateway.py",
                "tests/test_okx_fill_restart_executor.py",
            ],
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
        preflight_completion_sha256=str(preflight_audit["completion_hashes_sha256"]),
        preflight_decision_sha256=str(preflight_audit["decision_sha256"]),
        ccxt_source_sha256=str(ccxt_contract["okx_source_sha256"]),
    )
    formal.validate()
    spec = dict(base)
    spec["package_specification_sha256"] = package_specification_sha256
    spec["package_hash_contract"] = (
        "SHA-256 of the canonical package payload before package hash fields"
    )
    spec["formal_controller_specification"] = formal.to_dict()
    return spec, formal


def _fresh_identifiers(now: datetime | None = None) -> tuple[str, str]:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    return f"formal-package-{stamp}", f"formal-{stamp}"


def prepare_successor_formal_package(
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
        hashes = successor_formal_source_hashes(root)
        package_id, formal_run_id = _fresh_identifiers()
        output = root / ARTIFACT_ROOT / package_id
        if output.exists():
            raise SuccessorFormalPackageError("successor package ID collision")
        if any(
            _json(path).get("formal_run_id") == formal_run_id
            for path in (root / ARTIFACT_ROOT).glob(
                "formal-package-*/run/formal_run_manifest.json"
            )
        ):
            raise SuccessorFormalPackageError("successor formal run ID reuse refused")
        spec, formal = build_successor_formal_spec(
            root=root,
            package_id=package_id,
            formal_run_id=formal_run_id,
            repair_audit=repair,
            preflight_audit=preflight,
            hashes=hashes,
        )
        output.mkdir(parents=True)
        _write_json(output / "predecessor" / "repair_evidence_audit.json", repair)
        _write_json(output / "predecessor" / "preflight_evidence_audit.json", preflight)
        _write_json(output / "specification" / "source_hashes.json", hashes)
        _write_json(output / "specification" / "formal_package_spec.json", spec)
        _write_json(
            output / "specification" / "formal_controller_spec.json",
            formal.to_dict(),
        )
        _write_json(output / "run" / "formal_run_manifest.json", {
            "package_id": package_id,
            "formal_run_id": formal_run_id,
            "session_id": formal.session_id,
            "package_specification_sha256": formal.package_specification_sha256,
            "source_manifest_sha256": formal.source_manifest_sha256,
            "formal_execution_armed": False,
            "execution_marker_created": False,
            "arm_token_sha256": hashlib.sha256(
                formal.expected_arm_token.encode("utf-8")
            ).hexdigest(),
            "arm_token_serialized": False,
        })

        targeted = _run_successor_suite(root, output)
        _write_json(output / "tests" / "test_summary.json", {
            "successor_formal_targeted": targeted,
            "carried_repair_gates": spec["fixture_manifest"],
        })
        if not targeted["passed_gate"]:
            raise SuccessorFormalPackageError("successor targeted regression failed")
        post_hashes = successor_formal_source_hashes(root)
        if post_hashes != hashes:
            raise SuccessorFormalPackageError("formal source changed during package freeze")
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
            raise SuccessorFormalPackageError("secret pattern found in formal package")
        decision = {
            "status": FORMAL_PACKAGE_STATUS,
            "successor_fill_cursor_repair_bound": True,
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
            "# Successor formal OKX Demo package\n\n"
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
            "arm_token_sha256": hashlib.sha256(
                formal.expected_arm_token.encode("utf-8")
            ).hexdigest(),
            "arm_token_serialized": False,
        }
        _write_json(output / "FORMAL_PACKAGE_COMPLETED.json", terminal)
    if guard.attempts:
        raise SuccessorFormalPackageError("network attempt blocked during package freeze")
    return output, formal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-id", default=REPAIR_ID)
    parser.add_argument("--preflight-run-id", default=PREFLIGHT_RUN_ID)
    args = parser.parse_args()
    try:
        output, formal = prepare_successor_formal_package(
            args.root,
            repair_id=args.repair_id,
            preflight_run_id=args.preflight_run_id,
        )
    except Exception as exc:
        print(f"SUCCESSOR_FORMAL_PACKAGE_FAILED:{type(exc).__name__}:{exc}")
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
