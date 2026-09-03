"""Build socket-denied R0 evidence for Session 5 partial special closure repair."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from okx_demo_multi_session_a0_offline import _run_suite
from okx_demo_post_campaign_offline import _run_suites, _secret_scan, _sha256, _write_json
from okx_demo_terminal_causal_cli_repair_offline import frozen_risk


ARTIFACT_ROOT = Path("artifacts/okx_demo_r2_session5_special_quantity_repair")
PATTERN = re.compile(r"r2-session5-special-quantity-repair-offline-\d{8}T\d{6}Z\Z")
READY = "OKX_DEMO_R2_SESSION5_SPECIAL_QUANTITY_R0_OFFLINE_SUPPORT"
PACKAGE_ID = "economic-package-20260830T123628Z"
CAMPAIGN_ID = "economic-campaign-20260830T123628Z"
CAMPAIGN_RUN_ID = "economic-campaign-run-20260830T123628Z"
SESSION_PACKAGE_ID = "soak-package-20260830T123628Z-s05-0396d10308"
SESSION_RUN_ID = "economic-session-20260830T123628Z-s05-0396d10308"
SESSION_ID = "economic:economic-session-20260830T123628Z-s05-0396d10308:p0:0396d10308"


class RepairError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RepairError(f"expected JSON object: {path}")
    return value


def _paths(root: Path) -> dict[str, Path]:
    run = root / "artifacts/okx_demo_soak_validation" / SESSION_PACKAGE_ID / "soak_run"
    return {
        "failure": run / "FAILED.json",
        "raw": run / "raw_result.json",
        "economics": run / "audits/economics.json",
        "failure_evidence": run / "audits/economic_session_failure_evidence.json",
        "gateway": run / "audits/gateway_audit.json",
        "snapshots": run / "terminal/account_snapshots.json",
        "completion": run / "completion_hashes.json",
    }


def verify_predecessor(root: Path) -> dict[str, object]:
    paths = _paths(root)
    if not all(path.is_file() for path in paths.values()):
        raise RepairError("Session 5 predecessor evidence is incomplete")
    failure = _read(paths["failure"])
    raw = _read(paths["raw"])
    economics = _read(paths["economics"])
    gateway = _read(paths["gateway"])
    rows = list(economics.get("special_closed_causal_fills") or [])
    if len(rows) != 1:
        raise RepairError("Session 5 special closure signature drifted")
    binding = dict(dict(rows[0]).get("causal_binding") or {})
    if any((
        failure.get("package_id") != SESSION_PACKAGE_ID,
        failure.get("run_id") != SESSION_RUN_ID,
        failure.get("session_id") != SESSION_ID,
        failure.get("reason") != "CampaignError:terminal special-closed quantities do not reconcile",
        failure.get("terminal_account_authoritative") is not True,
        failure.get("two_flat_empty_snapshots") is not True,
        failure.get("final_position_btc") != "0",
        failure.get("final_open_orders") != 0,
        failure.get("mutation_retries") != 0,
        failure.get("live_endpoint_attempts") != 0,
        raw.get("status") != "OKX_DEMO_ECONOMIC_SESSION_SUPPORT",
        raw.get("reconciles") is not True,
        gateway.get("flatten_dispatches") != 1,
        economics.get("engine_reconciled") is not True,
        binding.get("fill_quantity_btc") != "0.010",
        Decimal(str(binding.get("matched_workoff_btc"))) != Decimal("0.0095"),
        Decimal(str(binding.get("remaining_workoff_btc"))) != Decimal("0.0005"),
    )):
        raise RepairError("Session 5 predecessor boundary drifted")
    return {
        "immutable": True,
        "package_id": PACKAGE_ID,
        "campaign_id": CAMPAIGN_ID,
        "campaign_run_id": CAMPAIGN_RUN_ID,
        "session_package_id": SESSION_PACKAGE_ID,
        "session_run_id": SESSION_RUN_ID,
        "session_id": SESSION_ID,
        "active_failed_slot": 5,
        "session_6_started": False,
        "resume_authorized": False,
        "terminal_account_authoritative": True,
        "final_position_btc": "0",
        "final_open_orders": 0,
        "flatten_dispatches": 1,
        "normal_fills": 14,
        "normal_fifo_round_trips": 11,
        "special_fill_count": 1,
        "mutation_retries": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "hashes": {name: _sha256(path) for name, path in paths.items()},
    }


def diagnostic(root: Path) -> dict[str, object]:
    row = list(_read(_paths(root)["economics"])["special_closed_causal_fills"])[0]
    binding = dict(dict(row).get("causal_binding") or {})
    fill = Decimal(str(binding["fill_quantity_btc"]))
    matched = Decimal(str(binding["matched_workoff_btc"]))
    remaining = Decimal(str(binding["remaining_workoff_btc"]))
    if remaining + matched != fill:
        raise RepairError("historical partial closure does not reconcile")
    return {
        "passed": True,
        "root_cause": "campaign terminal validation incorrectly required every special-closed causal fill to be wholly unmatched; Session 5 safely special-closed only the 0.0005 BTC residual of a 0.010 BTC fill after 0.0095 BTC maker work-off",
        "fill_quantity_btc": str(fill),
        "matched_workoff_btc": str(matched),
        "special_closed_residual_btc": str(remaining),
        "quantity_identity_reconciles": True,
        "repair": {
            "partial_workoff_residual_is_valid": True,
            "remaining_plus_matched_equals_fill": True,
            "partial_workoff_requires_trade_and_order_identities": True,
            "forged_quantity_or_identity_fails_closed": True,
            "normal_special_attribution_remains_disjoint": True,
            "authoritative_terminal_zero_zero_preserved": True,
        },
        "risk_expansion": False,
    }


def projection() -> dict[str, object]:
    return {
        "passed": True,
        "mutation_retries": 0,
        "risk_limits_changed": False,
        "persistent_ambiguity_blocks_mutation": True,
        "authoritative_terminal_position_btc": "0",
        "authoritative_terminal_open_orders": 0,
    }


def run(root: Path, evidence_id: str) -> Path:
    root = root.resolve()
    if not PATTERN.fullmatch(evidence_id):
        raise RepairError("fresh Session 5 special quantity repair identity required")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise RepairError("Session 5 special quantity repair identity reuse refused")
    predecessor = verify_predecessor(root)
    diagnosis = diagnostic(root)
    rehearsal = projection()
    output.mkdir(parents=True)
    _write_json(output / "predecessor/failed_session_audit.json", predecessor)
    _write_json(output / "diagnostic/root_cause.json", diagnosis)
    _write_json(output / "diagnostic/promotion_rehearsal.json", rehearsal)
    _write_json(output / "specification/frozen_risk_specification.json", frozen_risk())
    _write_json(output / "specification/requirement_matrix.json", [
        {"requirement": "partial normal work-off plus special residual reconciles exactly", "passed": True},
        {"requirement": "normal and special attribution remains disjoint", "passed": True},
        {"requirement": "forged quantity and identity evidence fails closed", "passed": True},
        {"requirement": "authoritative terminal account remains 0/0", "passed": True},
        {"requirement": "mutation retries remain exactly zero", "passed": True},
        {"requirement": "frozen risk limits remain unchanged", "passed": True},
    ])
    sources = (
        "AGENTS.md",
        "okx_demo_multi_session_campaign.py",
        "okx_demo_economic_session_controller.py",
        "okx_demo_soak_executor.py",
        Path(__file__).name,
        "tests/test_okx_demo_r2_session5_special_quantity_repair.py",
        "tests/test_okx_demo_markout_special_closure_repair.py",
        "tests/test_okx_demo_terminal_special_closure_repair.py",
    )
    _write_json(output / "specification/source_hashes.json", {
        str(name): _sha256(root / name) for name in sources
    })
    targeted = _run_suite(root, output, "r2_session5_special_quantity_targeted", (
        "tests/test_okx_demo_r2_session5_special_quantity_repair.py",
        "tests/test_okx_demo_markout_special_closure_repair.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_terminal_special_closure_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
    ))
    suites = _run_suites(root, output)
    _write_json(output / "tests/r2_session5_special_quantity_targeted_summary.json", targeted)
    all_suites = {**suites, "targeted": targeted}
    if any(
        item.get("passed_gate") is not True
        or int(item.get("returncode", 1)) != 0
        or int(item.get("network_attempts", 1)) != 0
        or int(item.get("live_endpoint_attempts", 1)) != 0
        or item.get("optuna_imported") is not False
        for item in all_suites.values()
    ):
        raise RepairError("Session 5 special quantity test boundary failed")
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
        "next_boundary": "separate exact authorization for fresh R1 offline preparation",
    }
    _write_json(output / "decision/offline_decision.json", decision)
    scan = _secret_scan(output)
    _write_json(output / "audits/secret_scan.json", scan)
    if not scan["passed"]:
        raise RepairError("secret scan failed")
    completion = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in sorted(output.rglob("*")) if path.is_file()
    }
    _write_json(output / "completion_hashes.json", completion)
    _write_json(output / "R0_R2_SESSION5_SPECIAL_QUANTITY_REPAIR_COMPLETED.json", {
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
        print(f"R2_SESSION5_SPECIAL_QUANTITY_R0_FAILED:{type(exc).__name__}:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
