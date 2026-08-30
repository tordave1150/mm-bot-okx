from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import okx_demo_multi_session_prepare as prepare_module
from okx_demo_multi_session_campaign import CampaignDecision, seal_session_evidence
from okx_demo_multi_session_prepare import prepare
from okx_demo_multi_session_supervisor import (
    CampaignSupervisor,
    CampaignSupervisorError,
    _attempted_aggregate,
    load_campaign_package,
    verify_campaign_run,
)
from okx_demo_soak_executor import (
    SoakExecutionError,
    _verify_campaign_authorization,
    load_package,
    start,
)
from okx_fill_restart_offline import _sha256, _write_json
from okx_fill_restart_validation import canonical_sha256


def test_attempted_aggregate_derives_total_components_from_normal_and_special() -> None:
    completed = {
        "normal_create_dispatches": 145,
        "normal_create_acknowledgements": 145,
        "normal_bid_fills": 1,
        "normal_ask_fills": 1,
        "realized_spread_pnl_usdt": "0.2660",
        "inventory_pnl_usdt": "0.0000",
        "normal_gross_pnl_usdt": "0.2660",
        "normal_fees_usdt": "0.2551084",
        "normal_net_pnl_usdt": "0.0108916",
        "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0",
        "special_net_pnl_usdt": "0",
        "aggregate_net_pnl_usdt": "0.0108916",
    }
    failed = {
        "normal_create_dispatches": 23,
        "normal_create_acknowledgements": 23,
        "normal_bid_fills": 1,
        "normal_ask_fills": 1,
        "realized_spread_pnl_usdt": "0.3300",
        "inventory_pnl_usdt": "0.0000",
        "normal_gross_pnl_usdt": "0.3300",
        "normal_fees_usdt": "0.2549248",
        "normal_net_pnl_usdt": "0.0750752",
        "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0",
        "special_net_pnl_usdt": "0",
        "aggregate_gross_pnl_usdt": "0.3300",
        "aggregate_fees_usdt": "0.2549248",
        "aggregate_net_pnl_usdt": "0.0750752",
        "maximum_drawdown_usdt": "0.35442940000",
        "unsafe_sessions": 1,
    }
    result = _attempted_aggregate(
        completed, failed, attempted_session_count=4
    )
    assert result["normal_gross_pnl_usdt"] == "0.5960"
    assert result["normal_fees_usdt"] == "0.5100332"
    assert result["normal_net_pnl_usdt"] == "0.0859668"
    assert result["aggregate_gross_pnl_usdt"] == "0.5960"
    assert result["aggregate_fees_usdt"] == "0.5100332"
    assert result["aggregate_net_pnl_usdt"] == "0.0859668"
    assert result["economic_attribution_reconciles"] is True
    assert result["create_counter_reconciles"] is True
    assert result["attempted_session_count"] == 4


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = tmp_path / "fixture.py"
    fixture.write_text("# source fixture\n", encoding="utf-8")
    sources = {"fixture.py": _sha(fixture)}
    a0 = {
        "passed": True,
        "evidence_kind": "multi_session_a0_offline_build",
        "evidence_id": "multi-session-a0-offline-fixture",
        "completion_hashes_sha256": "a" * 64,
        "terminal_sha256": "b" * 64,
        "decision_sha256": "c" * 64,
        "formal_predecessor_verified": True,
        "soak_predecessor_verified": True,
        "failed_a2_predecessor_verified": True,
    }
    a1 = {
        "passed": True,
        "run_id": "preflight-fixture",
        "completion_hashes_sha256": "d" * 64,
        "terminal_sha256": "e" * 64,
        "decision_sha256": "f" * 64,
        "market_fingerprint": "1" * 64,
        "market_spec": {"symbol": "BTC/USDT:USDT", "linear": True},
        "endpoint_hosts": ["www.okx.com"],
    }
    monkeypatch.setattr(
        prepare_module, "verify_offline_evidence", lambda root, evidence_id: a0
    )
    monkeypatch.setattr(
        prepare_module,
        "_verify_a1",
        lambda root, preflight_run_id, a0_evidence_id: a1,
    )
    monkeypatch.setattr(prepare_module, "_source_hashes", lambda root: sources)
    output, identifiers = prepare(
        tmp_path,
        a0_evidence_id="multi-session-a0-offline-fixture",
        preflight_run_id="preflight-fixture",
        run_suite=lambda *args: {"passed_gate": True, "passed": 1},
    )
    return load_campaign_package(tmp_path, str(identifiers["package_id"])), identifiers


def _session(package, slot: int, armed_at_ms: int) -> dict[str, object]:
    identity = package.slots[slot - 1]
    start = armed_at_ms + slot * 1_000_000
    causal = []
    for offset, side in enumerate(("buy", "sell"), start=1):
        fill = start + offset * 100
        causal.append({
            "trade_id": f"trade-{slot}-{side}",
            "fill_side": side,
            "fill_timestamp_ms": fill,
            "defense_timestamp_ms": fill + 1,
            "reentry_timestamp_ms": fill + 2,
            "workoff_timestamp_ms": fill + 3,
            "maker_reentry_observed": True,
            "maker_workoff_observed": True,
            "immediate_taker_flatten": False,
            "inventory_before_btc": "0" if side == "buy" else "0.01",
            "inventory_after_btc": "0.01" if side == "buy" else "0",
        })
    return seal_session_evidence({
        "session_id": identity["session_id"],
        "run_id": identity["run_id"],
        "source_sha256": package.spec["source_manifest_sha256"],
        "started_at_ms": start,
        "ended_at_ms": start + 60_000,
        "normal_creates": 2,
        "normal_create_dispatches": 2,
        "normal_create_acknowledgements": 2,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 0,
        "reconciles": True,
        "normal_cancels": 0,
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": 0,
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01",
        "maximum_drawdown_usdt": "0.25",
        "hard_kill_triggered": False,
        "normal_bid_fills": 1,
        "normal_ask_fills": 1,
        "normal_fifo_round_trips": 1,
        "realized_spread_pnl_usdt": "0.20",
        "inventory_pnl_usdt": "0.10",
        "normal_gross_pnl_usdt": "0.30",
        "normal_fees_usdt": "0.10",
        "normal_net_pnl_usdt": "0.20",
        "special_fill_count": 0,
        "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0",
        "special_net_pnl_usdt": "0",
        "aggregate_gross_pnl_usdt": "0.30",
        "aggregate_fees_usdt": "0.10",
        "aggregate_net_pnl_usdt": "0.20",
        "flatten_dispatches": 0,
        "quote_mode_ticks": 10,
        "quote_mode_counters": {"BALANCED": 10},
        "unclassified_quote_mode_ticks": 0,
        "markouts_usdt": ["0.02", "0.02"],
        "causal_reentry": causal,
        "fill_cursor_sha256": f"{slot:064x}",
        "final_position_btc": "0",
        "final_open_orders": 0,
        "terminal_account_snapshots": 2,
        "terminal_reconciled": True,
        "pending_intent": False,
        "ambiguous_intent": False,
        "safety_violations": [],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    })


def test_arm_is_exact_non_reusable_and_serializes_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    with pytest.raises(CampaignSupervisorError, match="token"):
        CampaignSupervisor.arm(
            package, campaign_arm_token="wrong", now_ms=1_000_000
        )
    assert not (package.output / "campaign_run").exists()
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=1_000_000,
    )
    marker = supervisor.marker_path.read_text(encoding="utf-8")
    assert str(identifiers["campaign_arm_token"]) not in marker
    with pytest.raises(CampaignSupervisorError, match="reuse"):
        CampaignSupervisor.arm(
            package,
            campaign_arm_token=str(identifiers["campaign_arm_token"]),
            now_ms=1_000_001,
        )


def test_lease_collision_stale_recovery_and_single_active_session_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=1_000_000,
    )
    supervisor.acquire_lease(owner="owner-a", now_ms=1_000_001, ttl_ms=100)
    with pytest.raises(CampaignSupervisorError, match="collision"):
        supervisor.acquire_lease(owner="owner-b", now_ms=1_000_050, ttl_ms=100)
    supervisor.acquire_lease(owner="owner-b", now_ms=1_000_102, ttl_ms=21_900_000)
    slot = supervisor.authorize_next_session(owner="owner-b", now_ms=1_000_103)
    with pytest.raises(CampaignSupervisorError, match="active session"):
        supervisor.authorize_next_session(owner="owner-b", now_ms=1_000_104)
    child = load_package(tmp_path, str(slot["package_id"]))
    gate = _verify_campaign_authorization(child)
    assert gate["campaign_slot"] == 1
    assert gate["live_authorized"] is False
    assert gate["production_authorized"] is False
    resumed = CampaignSupervisor.load(package)
    with pytest.raises(CampaignSupervisorError, match="active session"):
        resumed.authorize_next_session(owner="owner-b", now_ms=1_000_105)


def test_economic_child_refuses_start_without_supervisor_gate_before_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, _ = _prepared(tmp_path, monkeypatch)
    slot = package.slots[0]
    child = load_package(tmp_path, str(slot["package_id"]))
    with pytest.raises(SoakExecutionError, match="campaign authorization"):
        start(child, child.expected_arm_token)
    assert not (child.output / "soak_run/SOAK_EXECUTION_ARMED.json").exists()


def test_twelve_sessions_are_sequential_and_terminal_marker_is_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(
        owner="owner", now_ms=armed_at + 1, ttl_ms=21_900_000
    )
    decision = CampaignDecision.IN_PROGRESS
    for slot in range(1, 13):
        authorized = supervisor.authorize_next_session(
            owner="owner", now_ms=armed_at + slot * 1_000_000 - 10
        )
        assert authorized["slot"] == slot
        decision = supervisor.accept_session(
            owner="owner",
            now_ms=armed_at + slot * 1_000_000 + 60_001,
            sealed_session=_session(package, slot, armed_at),
        )
    assert decision is CampaignDecision.READY_FOR_PRODUCTION_READ_ONLY_SHADOW
    verified = verify_campaign_run(package)
    terminal = supervisor.run / "A2_CAMPAIGN_COMPLETED.json"
    manifest = supervisor.run / "completion_hashes.json"
    assert verified["sessions_completed"] == 12
    assert verified["terminal_decision"] == (
        "READY_FOR_PRODUCTION_READ_ONLY_SHADOW"
    )
    assert verified["registry"]["aggregate"]["campaign_wall_ms"] == 11_060_000
    assert terminal.is_file()
    assert terminal.stat().st_mtime_ns >= manifest.stat().st_mtime_ns


def test_conflicting_session_evidence_fails_closed_without_accepting_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(owner="owner", now_ms=armed_at + 1, ttl_ms=1_000_000)
    supervisor.authorize_next_session(owner="owner", now_ms=armed_at + 2)
    bad = dict(_session(package, 1, armed_at))
    bad["run_id"] = "economic-conflict"
    with pytest.raises(CampaignSupervisorError, match="fail closed"):
        supervisor.accept_session(
            owner="owner", now_ms=armed_at + 3, sealed_session=bad
        )
    verified = verify_campaign_run(package)
    assert verified["sessions_completed"] == 0
    assert verified["terminal_decision"] == "NOT_READY"
    assert verified["registry"]["aggregate"]["normal_creates"] == 0


def test_missing_child_terminal_artifact_fails_campaign_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(owner="owner", now_ms=armed_at + 1, ttl_ms=1_000_000)
    supervisor.authorize_next_session(owner="owner", now_ms=armed_at + 2)
    with pytest.raises(CampaignSupervisorError, match="fail closed"):
        supervisor.accept_active_session_artifact(
            owner="owner", now_ms=armed_at + 3
        )
    verified = verify_campaign_run(package)
    assert verified["terminal_decision"] == "NOT_READY"
    assert verified["sessions_completed"] == 0
    terminal = (
        package.output / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
    ).read_text(encoding="utf-8")
    assert '"terminal_account_status": "UNRESOLVED_FAIL_CLOSED"' in terminal
    assert '"final_position_btc": null' in terminal


def test_supervisor_accounts_verified_failed_child_instead_of_zeroing_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package,
        campaign_arm_token=str(identifiers["campaign_arm_token"]),
        now_ms=armed_at,
    )
    supervisor.acquire_lease(
        owner="owner", now_ms=armed_at + 1, ttl_ms=1_000_000
    )
    slot = supervisor.authorize_next_session(
        owner="owner", now_ms=armed_at + 2
    )
    child = package.session_output(slot)
    evidence = {
        "status": "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        "evidence_kind": "ATTEMPTED_ECONOMIC_SESSION_FAILURE",
        "package_id": slot["package_id"],
        "run_id": slot["run_id"],
        "session_id": slot["session_id"],
        "source_sha256": package.spec["source_manifest_sha256"],
        "normal_creates": 60,
        "normal_create_dispatches": 60,
        "normal_create_acknowledgements": 59,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 1,
        "mutation_retries": 0,
        "normal_cancels": 50,
        "normal_bid_fills": 6,
        "normal_ask_fills": 13,
        "normal_fifo_round_trips": 8,
        "realized_spread_pnl_usdt": "1.0",
        "inventory_pnl_usdt": "-2.0",
        "normal_gross_pnl_usdt": "-1.0",
        "normal_fees_usdt": "0.8",
        "normal_net_pnl_usdt": "-1.8",
        "special_fill_count": 1,
        "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0.1",
        "special_net_pnl_usdt": "-0.1",
        "aggregate_gross_pnl_usdt": "-1.0",
        "aggregate_fees_usdt": "0.9",
        "aggregate_net_pnl_usdt": "-1.9",
        "maximum_drawdown_usdt": "2.0",
        "markouts_usdt": [],
        "causal_reentry": [],
        "unclassified_quote_mode_ticks": 0,
        "two_flat_empty_snapshots": True,
        "terminal_account_authoritative": True,
        "final_position_btc": "0",
        "final_open_orders": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    evidence_path = (
        child / "soak_run/audits/economic_session_failure_evidence.json"
    )
    snapshots_path = child / "soak_run/terminal/account_snapshots.json"
    _write_json(evidence_path, evidence)
    _write_json(snapshots_path, {
        "first": {"position_btc": "0", "open_orders": 0},
        "second": {"position_btc": "0", "open_orders": 0},
    })
    completion = {
        "soak_run/audits/economic_session_failure_evidence.json": _sha256(
            evidence_path
        ),
        "soak_run/terminal/account_snapshots.json": _sha256(snapshots_path),
    }
    completion_path = child / "soak_run/completion_hashes.json"
    _write_json(completion_path, completion)
    failed = {
        "status": "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        "package_id": slot["package_id"],
        "run_id": slot["run_id"],
        "session_id": slot["session_id"],
        "source_sha256": package.spec["source_manifest_sha256"],
        "normal_creates": 60,
        "normal_create_dispatches": 60,
        "normal_create_acknowledgements": 59,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 1,
        "mutation_retries": 0,
        "normal_cancels": 50,
        "normal_bid_fills": 6,
        "normal_ask_fills": 13,
        "normal_fifo_round_trips": 8,
        "realized_spread_pnl_usdt": "1.0",
        "inventory_pnl_usdt": "-2.0",
        "normal_gross_pnl_usdt": "-1.0",
        "normal_fees_usdt": "0.8",
        "normal_net_pnl_usdt": "-1.8",
        "special_fill_count": 1,
        "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0.1",
        "special_net_pnl_usdt": "-0.1",
        "aggregate_gross_pnl_usdt": "-1.0",
        "aggregate_fees_usdt": "0.9",
        "aggregate_net_pnl_usdt": "-1.9",
        "maximum_drawdown_usdt": "2.0",
        "markouts_usdt": [],
        "causal_reentry": [],
        "unclassified_quote_mode_ticks": 0,
        "two_flat_empty_snapshots": True,
        "terminal_account_authoritative": True,
        "final_position_btc": "0",
        "final_open_orders": 0,
        "live_endpoint_attempts": 0,
        "live_orders": 0,
        "terminal_written_last": True,
        "completion_hashes_sha256": _sha256(completion_path),
    }
    _write_json(child / "soak_run/FAILED.json", failed)
    assert supervisor.accept_active_session_artifact(
        owner="owner", now_ms=armed_at + 3
    ) is CampaignDecision.NOT_READY
    verified = verify_campaign_run(package)
    assert verified["sessions_completed"] == 0
    assert verified["sessions_failed"] == 1
    assert verified["sessions_attempted"] == 1
    terminal = json.loads((
        package.output / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
    ).read_text(encoding="utf-8"))
    assert terminal["terminal_account_authoritative"] is True
    assert terminal["final_position_btc"] == "0"
    assert terminal["attempted_aggregate"]["normal_creates"] == 60
    assert terminal["attempted_aggregate"]["normal_fill_count"] == 19
    assert terminal["attempted_aggregate"]["normal_fees_usdt"] == "0.8"
    assert terminal["attempted_aggregate"]["realized_spread_pnl_usdt"] == "1.0"
    assert terminal["attempted_aggregate"]["economic_attribution_reconciles"] is True
    assert terminal["attempted_aggregate"]["create_counter_reconciles"] is True


def test_supervisor_ingests_reconciled_pre_market_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, identifiers = _prepared(tmp_path, monkeypatch)
    armed_at = 1_000_000
    supervisor = CampaignSupervisor.arm(
        package, campaign_arm_token=str(identifiers["campaign_arm_token"]), now_ms=armed_at
    )
    supervisor.acquire_lease(owner="owner", now_ms=armed_at + 1, ttl_ms=1_000_000)
    slot = supervisor.authorize_next_session(owner="owner", now_ms=armed_at + 2)
    child = package.session_output(slot)
    accounting = {
        "realized_spread_pnl_usdt": "0", "inventory_pnl_usdt": "0",
        "normal_gross_pnl_usdt": "0", "normal_fees_usdt": "0",
        "normal_net_pnl_usdt": "0", "special_gross_pnl_usdt": "0",
        "special_fees_usdt": "0", "special_net_pnl_usdt": "0",
        "aggregate_gross_pnl_usdt": "0", "aggregate_fees_usdt": "0",
        "aggregate_net_pnl_usdt": "0", "maximum_drawdown_usdt": "0",
    }
    shared = {
        "status": "OKX_DEMO_ECONOMIC_SESSION_FAILED",
        "package_id": slot["package_id"], "run_id": slot["run_id"],
        "session_id": slot["session_id"],
        "source_sha256": package.spec["source_manifest_sha256"],
        "failure_stage": "PRE_MARKET_BOOTSTRAP",
        "market_bootstrap_completed": False,
        "terminal_reconciliation_mode": "ACCOUNT_ONLY_TWO_SNAPSHOT",
        "normal_creates": 0, "normal_create_dispatches": 0,
        "normal_create_acknowledgements": 0, "normal_create_rejections": 0,
        "normal_create_unresolved": 0, "mutation_retries": 0,
        "normal_cancels": 0, "normal_bid_fills": 0, "normal_ask_fills": 0,
        "normal_fifo_round_trips": 0, "special_fill_count": 0,
        "markouts_usdt": [], "causal_reentry": [],
        "unclassified_quote_mode_ticks": 0, "two_flat_empty_snapshots": True,
        "terminal_account_authoritative": True, "final_position_btc": "0",
        "final_open_orders": 0, "live_endpoint_attempts": 0, "live_orders": 0,
        **accounting,
    }
    evidence = {**shared, "evidence_kind": "ATTEMPTED_ECONOMIC_SESSION_FAILURE"}
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    evidence_path = child / "soak_run/audits/economic_session_failure_evidence.json"
    snapshots_path = child / "soak_run/terminal/account_snapshots.json"
    audit_path = child / "soak_run/audits/gateway_audit.json"
    _write_json(evidence_path, evidence)
    _write_json(snapshots_path, {
        "first": {"position_btc": "0", "open_orders": 0},
        "second": {"position_btc": "0", "open_orders": 0},
    })
    _write_json(audit_path, {
        "mutation_call_count": 0, "flatten_dispatches": 0,
        "live_endpoint_attempts": 0, "live_orders": 0,
    })
    completion = {
        "soak_run/audits/economic_session_failure_evidence.json": _sha256(evidence_path),
        "soak_run/terminal/account_snapshots.json": _sha256(snapshots_path),
        "soak_run/audits/gateway_audit.json": _sha256(audit_path),
    }
    completion_path = child / "soak_run/completion_hashes.json"
    _write_json(completion_path, completion)
    _write_json(child / "soak_run/FAILED.json", {
        **shared, "terminal_written_last": True,
        "completion_hashes_sha256": _sha256(completion_path),
    })

    assert supervisor.accept_active_session_artifact(
        owner="owner", now_ms=armed_at + 3
    ) is CampaignDecision.NOT_READY
    terminal = json.loads((
        package.output / "campaign_run/A2_CAMPAIGN_COMPLETED.json"
    ).read_text(encoding="utf-8"))
    assert terminal["terminal_account_authoritative"] is True
    assert terminal["final_position_btc"] == "0"
    assert terminal["attempted_aggregate"]["normal_creates"] == 0
