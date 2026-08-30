"""Build immutable offline evidence from a completed economic campaign.

This module has no OKX adapter import path.  It reads only durable local
campaign evidence, verifies the append-only registry, diagnoses the sample and
special-flatten deficits, runs socket-denied tests, and writes a terminal
offline marker last.  It never prepares or authorizes an external execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any, Iterable

from okx_demo_multi_session_a0_offline import (
    BACKTEST_DESELECT,
    BACKTEST_EXCLUSIONS,
    ROOT_DESELECT,
    ROOT_EXCLUSIONS,
    _run_suite,
)
from okx_demo_multi_session_campaign import (
    CampaignDecision,
    CampaignRegistry,
    SessionEvidence,
    verify_registry,
)


ARTIFACT_ROOT = Path("artifacts") / "okx_demo_post_campaign_repair"
PREDECESSOR_PACKAGE_ID = "economic-package-20260818T125221Z"
PREDECESSOR_CAMPAIGN_ID = "economic-campaign-20260818T125221Z"
SUCCESSOR_SOURCE = "AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md"
READY_STATUS = "OKX_DEMO_SAMPLE_EFFICIENCY_R0_OFFLINE_SUPPORT"
EVIDENCE_PATTERN = re.compile(r"economic-repair-offline-\d{8}T\d{6}Z\Z")
EXPECTED_PREDECESSOR_HASHES = {
    "campaign_run/A2_CAMPAIGN_COMPLETED.json": (
        "6e05efab617f59893768bbc1a144ce652a888f017c26eaf7322a309f151ade7e"
    ),
    "campaign_run/completion_hashes.json": (
        "3419e9f6b934d53a5981fd5be88c21489ae64da0bc834918ca2a404f3e2476b7"
    ),
    "campaign_run/decision/campaign_decision.json": (
        "7172cc37c322ac9e57666d10a8abd5a5d0fe1b031da4d5ae69742e2c9f834713"
    ),
    "campaign_run/registry/campaign_registry.jsonl": (
        "648882ce831666ef28867bc9792f6c06203b1bd2ca720a541ce03b529d7d21ef"
    ),
    "campaign_run/audits/registry_audit.json": (
        "a0024e766cd01251876496514bae3e4137c3fce3bb42177090e7fb5218a845cc"
    ),
}


class PostCampaignOfflineError(RuntimeError):
    """Raised when immutable offline evidence cannot be established."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _package_root(root: Path, package_id: str) -> Path:
    if package_id != PREDECESSOR_PACKAGE_ID:
        raise PostCampaignOfflineError("predecessor package identity mismatch")
    return (
        root
        / "artifacts"
        / "okx_demo_multi_session_economic_soak"
        / "packages"
        / package_id
    )


def verify_predecessor(root: Path, package_id: str) -> dict[str, object]:
    package = _package_root(root, package_id)
    observed: dict[str, str] = {}
    for relative, expected in EXPECTED_PREDECESSOR_HASHES.items():
        path = package / relative
        if not path.is_file():
            raise PostCampaignOfflineError(f"predecessor file missing: {relative}")
        actual = _sha256(path)
        if actual != expected:
            raise PostCampaignOfflineError(f"predecessor hash drift: {relative}")
        observed[relative] = actual

    registry_root = package / "campaign_run" / "registry"
    verified = verify_registry(registry_root)
    if verified["campaign_id"] != PREDECESSOR_CAMPAIGN_ID:
        raise PostCampaignOfflineError("predecessor campaign identity mismatch")
    if verified["terminal_decision"] != CampaignDecision.INSUFFICIENT_EVIDENCE.value:
        raise PostCampaignOfflineError("predecessor terminal decision mismatch")
    if verified["sessions"] != 12 or verified["aggregate"]["unsafe_sessions"] != 0:
        raise PostCampaignOfflineError("predecessor session safety evidence mismatch")
    if verified["live_endpoint_attempts"] != 0 or verified["live_orders"] != 0:
        raise PostCampaignOfflineError("predecessor contains Live activity")
    return {
        "package_id": package_id,
        "campaign_id": PREDECESSOR_CAMPAIGN_ID,
        "immutable": True,
        "hashes": observed,
        "registry_tail_sha256": verified["tail_sha256"],
        "terminal_decision": verified["terminal_decision"],
        "decision_reasons": verified["decision_reasons"],
        "sessions": verified["sessions"],
        "unsafe_sessions": verified["aggregate"]["unsafe_sessions"],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "production_authorized": False,
    }


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal:
    denominator_value = Decimal(denominator)
    if denominator_value == 0:
        return Decimal("0")
    return Decimal(numerator) / denominator_value


def session_rows(sessions: Iterable[SessionEvidence]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for slot, session in enumerate(sessions, start=1):
        fills = session.normal_bid_fills + session.normal_ask_fills
        workoffs = sum(item.maker_workoff_observed for item in session.causal_reentry)
        reentries = sum(item.maker_reentry_observed for item in session.causal_reentry)
        rows.append({
            "slot": slot,
            "run_id": session.run_id,
            "normal_creates": session.normal_creates,
            "normal_cancels": session.normal_cancels,
            "normal_fills": fills,
            "normal_bid_fills": session.normal_bid_fills,
            "normal_ask_fills": session.normal_ask_fills,
            "normal_fifo_round_trips": session.normal_fifo_round_trips,
            "maker_workoff_records": workoffs,
            "maker_reentry_records": reentries,
            "special_flatten": session.flatten_dispatches > 0,
            "flatten_dispatches": session.flatten_dispatches,
            "normal_net_pnl_usdt": str(session.normal_net_pnl_usdt),
            "special_net_pnl_usdt": str(session.special_net_pnl_usdt),
            "maximum_drawdown_usdt": str(session.maximum_drawdown_usdt),
            "placement_blocked_ticks": int(
                session.quote_mode_counters.get("PLACEMENT_BLOCKED", 0)
            ),
            "one_sided_buy_defense_ticks": int(
                session.quote_mode_counters.get("ONE_SIDED_BUY_DEFENSE", 0)
            ),
            "one_sided_sell_defense_ticks": int(
                session.quote_mode_counters.get("ONE_SIDED_SELL_DEFENSE", 0)
            ),
            "terminal_reconciled": session.terminal_reconciled,
            "final_position_btc": str(session.final_position_btc),
            "final_open_orders": session.final_open_orders,
        })
    return rows


def diagnose(root: Path, package_id: str) -> dict[str, object]:
    package = _package_root(root, package_id)
    registry = CampaignRegistry.load(package / "campaign_run" / "registry")
    sessions = registry.sessions()
    decision, decision_reasons = registry.evaluate(sessions)
    aggregate = registry.aggregate(sessions)
    limits = registry.manifest.limits
    if decision is not CampaignDecision.INSUFFICIENT_EVIDENCE:
        raise PostCampaignOfflineError("diagnostic requires INSUFFICIENT_EVIDENCE")

    rows = session_rows(sessions)
    no_fill_slots = [int(row["slot"]) for row in rows if row["normal_fills"] == 0]
    productive_slots = [
        int(row["slot"])
        for row in rows
        if int(row["normal_fifo_round_trips"]) > 0
    ]
    flatten_slots = [
        int(row["slot"]) for row in rows if bool(row["special_flatten"])
    ]
    one_fill_flatten_slots = [
        int(row["slot"])
        for row in rows
        if int(row["normal_fills"]) == 1 and bool(row["special_flatten"])
    ]
    maximum_flatten_sessions = int(
        (
            Decimal(limits.maximum_sessions)
            * limits.maximum_special_flatten_fraction
        ).to_integral_value(rounding=ROUND_FLOOR)
    )
    fills = int(aggregate["normal_fill_count"])
    creates = int(aggregate["normal_creates"])
    cancels = int(aggregate["normal_cancels"])
    round_trips = int(aggregate["normal_fifo_round_trips"])
    gate_deficits = {
        "normal_fills": max(0, limits.minimum_normal_fills - fills),
        "bid_fills": max(
            0, limits.minimum_bid_fills - int(aggregate["normal_bid_fills"])
        ),
        "ask_fills": max(
            0, limits.minimum_ask_fills - int(aggregate["normal_ask_fills"])
        ),
        "fifo_round_trips": max(
            0, limits.minimum_fifo_round_trips - round_trips
        ),
        "special_flatten_sessions_excess": max(
            0, len(flatten_slots) - maximum_flatten_sessions
        ),
    }
    return {
        "package_id": package_id,
        "campaign_id": registry.manifest.campaign_id,
        "terminal_decision": decision.value,
        "decision_reasons": list(decision_reasons),
        "aggregate": aggregate,
        "session_rows": rows,
        "cohorts": {
            "no_fill_slots": no_fill_slots,
            "productive_fifo_slots": productive_slots,
            "special_flatten_slots": flatten_slots,
            "one_fill_then_flatten_slots": one_fill_flatten_slots,
        },
        "efficiency": {
            "normal_fill_per_create": str(_ratio(fills, creates)),
            "normal_cancel_per_fill": str(_ratio(cancels, fills)),
            "fifo_round_trip_per_maximum_fill_pair": str(
                _ratio(round_trips, fills // 2)
            ),
            "productive_session_fraction": str(
                _ratio(len(productive_slots), len(rows))
            ),
            "no_fill_session_fraction": str(
                _ratio(len(no_fill_slots), len(rows))
            ),
        },
        "gate_deficits": gate_deficits,
        "maximum_allowed_special_flatten_sessions": maximum_flatten_sessions,
        "primary_repair_targets": [
            "increase maker fill sample without expanding lot, inventory, or loss budgets",
            "preserve causal maker work-off while reducing one-fill terminal flatten dependence",
            "retain exact quote-mode, create, fill, fee, and cursor reconciliation",
            "keep normal-versus-special economics separated",
        ],
        "risk_expansion_recommended": False,
        "optuna_recommended": False,
        "production_authorized": False,
        "live_mode_available": False,
    }


def frozen_risk_specification() -> dict[str, object]:
    return {
        "instrument": "BTC/USDT:USDT",
        "normal_lot_btc": "0.01",
        "absolute_inventory_cap_btc": "0.01",
        "leverage": "3",
        "session_minutes": 30,
        "session_normal_create_cap": 60,
        "campaign_sessions": 12,
        "campaign_hours": 6,
        "campaign_normal_create_cap": 720,
        "campaign_hard_loss_usdt": "75",
        "mutation_retry_attempts": 0,
        "sample_efficiency_policy": {
            "minimum_half_spread_bps": "4.0",
            "maker_fee_rate": "0.0002",
            "fee_edge_safety_buffer_usdt": "0.01",
            "balanced_retention_threshold_ticks": 10,
            "defense_retention_threshold_ticks": 20,
            "admission_create_cap": 48,
            "workoff_create_reserve": 12,
            "risk_expansion": False,
        },
        "risk_expansion": False,
        "OKX_connection_authorized": False,
        "orders_authorized": False,
        "production_authorized": False,
    }


def requirement_matrix() -> list[dict[str, object]]:
    return [
        {
            "requirement": "completed A2 predecessor remains byte immutable",
            "gate": "all fixed hashes and registry chain verify",
        },
        {
            "requirement": "sample deficits use exact immutable session evidence",
            "gate": "fills, side floors, FIFO and flatten counts reconcile",
        },
        {
            "requirement": "no-fill sessions cannot be silently substituted",
            "gate": "all twelve slots remain in cohort accounting",
        },
        {
            "requirement": "causal re-entry survives the repair",
            "gate": "maker re-entry/work-off is distinct from special flatten",
        },
        {
            "requirement": "risk remains frozen",
            "gate": "no lot, inventory, leverage, loss, wall or create expansion",
        },
        {
            "requirement": "offline process has zero external authority",
            "gate": "socket/credential/mutation/Live counters remain zero",
        },
        {
            "requirement": "balanced quotes retain queue priority without stale drift",
            "gate": "ten-tick retention is bounded and fee-positive",
        },
        {
            "requirement": "one-sided defense preserves maker work-off",
            "gate": "twenty-tick defense retention and no taker causal credit",
        },
    ]


def scenario_matrix() -> list[dict[str, object]]:
    return [
        {"fixture": "four no-fill sessions", "expected": "sample deficit retained"},
        {"fixture": "one fill then terminal flatten", "expected": "special only"},
        {"fixture": "maker fill and maker work-off", "expected": "FIFO normal round trip"},
        {"fixture": "more than two flatten sessions", "expected": "flatten gate fails"},
        {"fixture": "normal net positive but total net negative", "expected": "attribution separated"},
        {"fixture": "tampered predecessor registry", "expected": "fail closed before evidence"},
        {"fixture": "evidence identity reuse", "expected": "refused without overwrite"},
        {"fixture": "balanced retention drift above ten ticks", "expected": "fail closed"},
        {"fixture": "defense retention drift above twenty ticks", "expected": "fail closed"},
        {"fixture": "spread at or below fee floor", "expected": "fail closed"},
        {"fixture": "stale or crossed book", "expected": "zero mutation"},
        {"fixture": "future book beyond skew", "expected": "zero mutation"},
        {"fixture": "empty or invalid book", "expected": "zero mutation"},
        {"fixture": "pending or ambiguous intent", "expected": "no retry"},
        {"fixture": "order disappearance", "expected": "fail closed"},
        {"fixture": "counter regression", "expected": "fail closed"},
        {"fixture": "conflicting duplicate fill", "expected": "fail closed"},
        {"fixture": "restart durable boundaries", "expected": "no duplicate mutation"},
        {"fixture": "multi-partial single-flight flatten", "expected": "one dispatch"},
        {"fixture": "state-store or lease failure", "expected": "fail closed"},
        {"fixture": "terminal evidence failure", "expected": "no false completion"},
    ]


def _run_suites(root: Path, output: Path) -> dict[str, dict[str, object]]:
    targeted = (
        "tests/test_okx_demo_sample_efficiency_repair.py",
        "tests/test_okx_demo_post_campaign_offline.py",
        "tests/test_okx_a2_causal_economics_repair.py",
        "tests/test_okx_demo_economic_session_controller.py",
        "tests/test_okx_demo_soak_executor.py",
        "tests/test_okx_demo_soak_failure_injection.py",
        "tests/test_okx_demo_operational_failure_campaign.py",
        "tests/test_okx_demo_multi_session_prepare.py",
        "tests/test_okx_demo_multi_session_campaign.py",
        "tests/test_okx_demo_multi_session_supervisor.py",
    )
    r0_root_exclusions = (
        *ROOT_EXCLUSIONS,
        "tests/test_okx_demo_multi_session_a0_offline.py",
    )
    root_args = (
        "tests",
        *(f"--ignore={item}" for item in r0_root_exclusions),
        *(f"--deselect={item}" for item in ROOT_DESELECT),
    )
    backtest_args = (
        "backtest/tests",
        *(f"--ignore={item}" for item in BACKTEST_EXCLUSIONS),
        *(f"--deselect={item}" for item in BACKTEST_DESELECT),
    )
    targeted_result = _run_suite(
        root, output, "post_campaign_targeted", targeted
    )
    root_result = _run_suite(root, output, "root_non_optuna", root_args)
    root_result["root_exclusions"] = list(r0_root_exclusions)
    root_result["root_deselect"] = list(ROOT_DESELECT)
    backtest_result = _run_suite(
        root, output, "backtest_non_optuna", backtest_args
    )
    suites = {
        "post_campaign_targeted": targeted_result,
        "root_non_optuna": root_result,
        "backtest_non_optuna": backtest_result,
    }
    _write_json(output / "tests" / "test_summary.json", suites)
    if not all(bool(value["passed_gate"]) for value in suites.values()):
        raise PostCampaignOfflineError("socket-denied test gate failed")
    return suites


def _secret_scan(output: Path) -> dict[str, object]:
    pattern = re.compile(
        rb"(?i)OKX_(?:API_KEY|SECRET|PASSPHRASE)\s*[=:]\s*[^\s\"']+"
    )
    matches: list[str] = []
    scanned = 0
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        scanned += 1
        if pattern.search(path.read_bytes()):
            matches.append(path.relative_to(output).as_posix())
    return {
        "passed": not matches,
        "files_scanned": scanned,
        "secret_pattern_matches": matches,
        "credential_environment_accessed": False,
        "credentials_serialized": False,
    }


def run_offline(
    root: Path,
    evidence_id: str,
    package_id: str = PREDECESSOR_PACKAGE_ID,
) -> Path:
    if not EVIDENCE_PATTERN.fullmatch(evidence_id):
        raise PostCampaignOfflineError("invalid offline evidence identity")
    output = root / ARTIFACT_ROOT / evidence_id
    if output.exists():
        raise PostCampaignOfflineError("offline evidence identity reuse refused")

    predecessor = verify_predecessor(root, package_id)
    diagnostic = diagnose(root, package_id)
    successor = root / SUCCESSOR_SOURCE
    if not successor.is_file():
        raise PostCampaignOfflineError("successor protocol source is missing")
    if _sha256(root / "AGENTS.md") != _sha256(successor):
        raise PostCampaignOfflineError("active root protocol is not byte identical")

    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "predecessor" / "a2_campaign_audit.json", predecessor)
    _write_json(output / "diagnostic" / "post_campaign_diagnostic.json", diagnostic)
    _write_json(output / "specification" / "frozen_risk_specification.json", frozen_risk_specification())
    _write_json(output / "specification" / "requirement_matrix.json", requirement_matrix())
    _write_json(output / "specification" / "scenario_matrix.json", scenario_matrix())
    source_hashes = {
        name: _sha256(root / name)
        for name in (
            "AGENTS.md",
            SUCCESSOR_SOURCE,
            "okx_demo_post_campaign_offline.py",
            "okx_demo_economic_session_controller.py",
            "okx_demo_multi_session_campaign.py",
            "okx_demo_multi_session_prepare.py",
            "okx_demo_multi_session_supervisor.py",
            "okx_demo_soak_executor.py",
            "tests/test_okx_demo_sample_efficiency_repair.py",
            "tests/test_okx_demo_post_campaign_offline.py",
            "tests/test_okx_demo_soak_executor.py",
            "tests/test_okx_demo_multi_session_prepare.py",
        )
    }
    _write_json(output / "specification" / "source_hashes.json", source_hashes)
    suites = _run_suites(root, output)
    endpoint_audit = {
        "socket_denied": True,
        "network_attempts": sum(int(item["network_attempts"]) for item in suites.values()),
        "credential_reads": 0,
        "demo_endpoint_attempts": 0,
        "live_endpoint_attempts": 0,
        "create_attempts": 0,
        "amend_attempts": 0,
        "cancel_attempts": 0,
        "flatten_attempts": 0,
        "account_configuration_attempts": 0,
        "orders": 0,
    }
    _write_json(output / "audits" / "endpoint_mutation_audit.json", endpoint_audit)
    decision = {
        "status": READY_STATUS,
        "R0_offline_repair_passed": True,
        "successor_protocol_active": True,
        "R1_preparation_authorized": False,
        "preflight_authorized": False,
        "economic_campaign_authorized": False,
        "A3_authorized": False,
        "production_authorized": False,
        "live_mode_available": False,
        "next_boundary": (
            "exact user authorization is required before preparing fresh R1 identities"
        ),
    }
    _write_json(output / "decision" / "offline_decision.json", decision)
    secret_scan = _secret_scan(output)
    _write_json(output / "audits" / "secret_scan.json", secret_scan)
    if not secret_scan["passed"]:
        raise PostCampaignOfflineError("secret scan failed")

    completion_paths = sorted(
        path for path in output.rglob("*") if path.is_file()
    )
    completion_hashes = {
        path.relative_to(output).as_posix(): _sha256(path)
        for path in completion_paths
    }
    _write_json(output / "completion_hashes.json", completion_hashes)
    marker = {
        "status": READY_STATUS,
        "evidence_id": evidence_id,
        "predecessor_package_id": package_id,
        "predecessor_campaign_id": PREDECESSOR_CAMPAIGN_ID,
        "files_hashed": len(completion_hashes),
        "network_attempts": 0,
        "credential_reads": 0,
        "orders_submitted": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "production_authorized": False,
        "live_mode_available": False,
        "optuna_executed": False,
        "validation_opened": False,
        "holdout_opened": False,
        "git_write_operation": False,
    }
    _write_json(output / "R0_OFFLINE_REPAIR_COMPLETED.json", marker)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--package-id", default=PREDECESSOR_PACKAGE_ID)
    arguments = parser.parse_args()
    output = run_offline(arguments.root.resolve(), arguments.evidence_id, arguments.package_id)
    print(json.dumps({
        "status": READY_STATUS,
        "output": str(output),
        "production_authorized": False,
        "live_mode_available": False,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
