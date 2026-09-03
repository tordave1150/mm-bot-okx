from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from okx_demo_multi_session_campaign import (
    CampaignDecision,
    CampaignError,
    CampaignLimits,
    CampaignManifest,
    CampaignRegistry,
    SessionEvidence,
    seal_session_evidence,
    verify_registry,
)


SOURCE_SHA256 = "a" * 64
FORMAL_COMPLETED_SHA256 = (
    "f62a17a606b7c4826f862ee6ae0cab7ef35d224c9f08bfc13234aa188f12b11e"
)
SOAK_COMPLETED_SHA256 = (
    "e658a0aede428465d579434aa19a9e975f0564e6134b9577ee9f373a0eacd958"
)


def _manifest(campaign_id: str = "economic-campaign-fixture") -> CampaignManifest:
    return CampaignManifest(
        campaign_id=campaign_id,
        source_sha256=SOURCE_SHA256,
        created_at_ms=1,
        formal_predecessor="formal-package-20260810T123953Z",
        soak_predecessor="soak-package-20260811T140223Z",
        formal_completed_sha256=FORMAL_COMPLETED_SHA256,
        soak_completed_sha256=SOAK_COMPLETED_SHA256,
    )


def _causal(index: int, start: int) -> list[dict[str, object]]:
    return [
        {
            "trade_id": f"trade-{index}-bid",
            "fill_side": "buy",
            "fill_timestamp_ms": start + 100,
            "defense_timestamp_ms": start + 101,
            "reentry_timestamp_ms": start + 102,
            "workoff_timestamp_ms": start + 103,
            "maker_reentry_observed": True,
            "maker_workoff_observed": True,
            "immediate_taker_flatten": False,
            "inventory_before_btc": "0",
            "inventory_after_btc": "0.01",
        },
        {
            "trade_id": f"trade-{index}-ask",
            "fill_side": "sell",
            "fill_timestamp_ms": start + 200,
            "defense_timestamp_ms": start + 201,
            "reentry_timestamp_ms": start + 202,
            "workoff_timestamp_ms": start + 203,
            "maker_reentry_observed": True,
            "maker_workoff_observed": True,
            "immediate_taker_flatten": False,
            "inventory_before_btc": "0.01",
            "inventory_after_btc": "0",
        },
    ]


def _session_payload(
    index: int,
    *,
    bid_fills: int = 1,
    ask_fills: int = 1,
    normal_gross: str = "0.30",
    normal_fees: str = "0.10",
    special_gross: str = "0",
    special_fees: str = "0",
    special_fill_count: int | None = None,
    flatten_dispatches: int = 0,
    unclassified: int = 0,
    hard_kill: bool = False,
) -> dict[str, object]:
    start = 1_000_000 + index * 1_000_000
    fill_count = bid_fills + ask_fills
    gross = Decimal(normal_gross)
    fees = Decimal(normal_fees)
    normal_net = gross - fees
    special_gross_value = Decimal(special_gross)
    special_fees_value = Decimal(special_fees)
    special_net = special_gross_value - special_fees_value
    normal_creates = max(2, fill_count)
    if bid_fills == 1 and ask_fills == 1:
        causal = _causal(index, start)
        round_trips = 1
    else:
        causal = []
        for offset in range(fill_count):
            side = "buy" if offset < bid_fills else "sell"
            causal.append({
                "trade_id": f"trade-{index}-{offset}",
                "fill_side": side,
                "fill_timestamp_ms": start + 100 + offset * 10,
                "defense_timestamp_ms": start + 101 + offset * 10,
                "reentry_timestamp_ms": start + 102 + offset * 10,
                "workoff_timestamp_ms": start + 103 + offset * 10,
                "maker_reentry_observed": True,
                "maker_workoff_observed": True,
                "immediate_taker_flatten": False,
                "inventory_before_btc": "0",
                "inventory_after_btc": "0",
            })
        round_trips = min(bid_fills, ask_fills)
    return {
        "session_id": f"economic:economic-{index}:p0:fixture",
        "run_id": f"economic-{index}",
        "source_sha256": SOURCE_SHA256,
        "started_at_ms": start,
        "ended_at_ms": start + 60_000,
        "normal_creates": normal_creates,
        "normal_create_dispatches": normal_creates,
        "normal_create_acknowledgements": normal_creates,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 0,
        "reconciles": True,
        "normal_cancels": max(0, normal_creates - fill_count),
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": 0,
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01" if fill_count else "0",
        "maximum_drawdown_usdt": "37.50" if hard_kill else "0.25",
        "hard_kill_triggered": hard_kill,
        "normal_bid_fills": bid_fills,
        "normal_ask_fills": ask_fills,
        "normal_fifo_round_trips": round_trips,
        "realized_spread_pnl_usdt": str(gross - Decimal("0.10")),
        "inventory_pnl_usdt": "0.10",
        "normal_gross_pnl_usdt": str(gross),
        "normal_fees_usdt": str(fees),
        "normal_net_pnl_usdt": str(normal_net),
        "special_fill_count": (
            int(flatten_dispatches > 0)
            if special_fill_count is None else special_fill_count
        ),
        "special_gross_pnl_usdt": str(special_gross_value),
        "special_fees_usdt": str(special_fees_value),
        "special_net_pnl_usdt": str(special_net),
        "aggregate_gross_pnl_usdt": str(gross + special_gross_value),
        "aggregate_fees_usdt": str(fees + special_fees_value),
        "aggregate_net_pnl_usdt": str(normal_net + special_net),
        "flatten_dispatches": flatten_dispatches,
        "quote_mode_ticks": 10,
        "quote_mode_counters": {"BALANCED": 10 - unclassified},
        "unclassified_quote_mode_ticks": unclassified,
        "markouts_usdt": ["0.02"] * fill_count,
        "causal_reentry": causal,
        "fill_cursor_sha256": f"{index + 1:064x}",
        "final_position_btc": "0",
        "final_open_orders": 0,
        "terminal_account_snapshots": 2,
        "terminal_reconciled": True,
        "pending_intent": False,
        "ambiguous_intent": False,
        "safety_violations": [],
        "live_endpoint_attempts": 0,
        "live_orders": 0,
    }


def _session(index: int, **overrides: object) -> SessionEvidence:
    payload = _session_payload(index, **overrides)
    return SessionEvidence.from_dict(seal_session_evidence(payload))


def _multi_partial_fifo_audit() -> dict[str, object]:
    return {
        "normal_fifo_match_fragments": 5,
        "normal_fifo_completed_lots": 5,
        "normal_inventory_cycles": 1,
        "normal_matched_fragment_quantity_btc": "0.010",
        "normal_completed_lot_quantity_btc": "0.010",
        "open_fifo_lot_quantity_btc": "0",
        "normal_fifo_unique_completed_entry_fills": 5,
        "normal_fifo_unique_exit_fills": 1,
        "normal_fifo_evidence_trade_ids": 6,
        "normal_fifo_exit_fill_reuse_count": 4,
        "normal_fifo_multi_partial_completed_lots": 0,
        "normal_fifo_multi_lot_exit_fills": 1,
    }


def test_extended_fifo_audit_allows_one_exit_to_close_many_partial_lots() -> None:
    payload = _session_payload(90, bid_fills=1, ask_fills=5)
    payload["normal_fifo_round_trips"] = 5
    payload["accounting_audit"] = _multi_partial_fifo_audit()
    session = SessionEvidence.from_dict(seal_session_evidence(payload))
    session.validate(CampaignLimits())


def test_extended_fifo_audit_rejects_forged_exit_reuse_count() -> None:
    payload = _session_payload(91, bid_fills=1, ask_fills=5)
    payload["normal_fifo_round_trips"] = 5
    audit = _multi_partial_fifo_audit()
    audit["normal_fifo_exit_fill_reuse_count"] = 3
    payload["accounting_audit"] = audit
    with pytest.raises(CampaignError, match="identity/lot"):
        SessionEvidence.from_dict(seal_session_evidence(payload))


def test_twelve_balanced_profitable_sessions_pass_and_reverify(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    registry = CampaignRegistry.initialize(root, _manifest())
    for index in range(11):
        assert registry.register_session(_session(index)) is CampaignDecision.IN_PROGRESS
    assert registry.register_session(_session(11)) is (
        CampaignDecision.READY_FOR_PRODUCTION_READ_ONLY_SHADOW
    )
    verified = verify_registry(root)
    assert verified["terminal_decision"] == "READY_FOR_PRODUCTION_READ_ONLY_SHADOW"
    assert verified["aggregate"]["normal_fill_count"] == 24
    assert verified["aggregate"]["normal_fifo_round_trips"] == 12
    assert verified["aggregate"]["normal_net_pnl_usdt"] == "2.40"
    assert verified["aggregate"]["live_endpoint_attempts"] == 0


def test_create_counter_extensions_round_trip_losslessly_through_registry(
    tmp_path: Path,
) -> None:
    sealed = seal_session_evidence(_session_payload(0))
    session = SessionEvidence.from_dict(sealed)
    assert session.to_dict() == sealed

    registry = CampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    assert registry.register_session(session) is CampaignDecision.IN_PROGRESS
    reloaded = CampaignRegistry.load(tmp_path / "campaign")
    assert reloaded.sessions()[0].to_dict() == sealed
    aggregate = reloaded.aggregate()
    assert aggregate["normal_create_dispatches"] == 2
    assert aggregate["normal_create_acknowledgements"] == 2
    assert aggregate["normal_create_rejections"] == 0
    assert aggregate["normal_create_unresolved"] == 0
    assert aggregate["create_counter_reconciles"] is True


def test_safe_but_empty_campaign_is_insufficient_and_does_not_extend(
    tmp_path: Path,
) -> None:
    registry = CampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    decision = CampaignDecision.IN_PROGRESS
    for index in range(12):
        decision = registry.register_session(_session(
            index,
            bid_fills=0,
            ask_fills=0,
            normal_gross="0",
            normal_fees="0",
        ))
    assert decision is CampaignDecision.INSUFFICIENT_EVIDENCE
    with pytest.raises(CampaignError, match="terminal"):
        registry.register_session(_session(12))


@pytest.mark.parametrize(
    ("session_overrides", "reason"),
    [
        ({"normal_gross": "0.05", "normal_fees": "0.10"}, "NORMAL_NET_NOT_POSITIVE"),
        ({"unclassified": 1}, "UNCLASSIFIED_QUOTE_MODE"),
    ],
)
def test_full_sample_fails_economic_gates(
    tmp_path: Path, session_overrides: dict[str, object], reason: str
) -> None:
    registry = CampaignRegistry.initialize(tmp_path / reason, _manifest(
        f"economic-campaign-{reason.lower()}"
    ))
    for index in range(12):
        decision = registry.register_session(_session(index, **session_overrides))
    assert decision is CampaignDecision.NOT_READY
    terminal = registry.records()[-1]
    assert reason in terminal["payload"]["reasons"]


def test_special_flatten_rate_uses_session_count_not_special_pnl(tmp_path: Path) -> None:
    registry = CampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    for index in range(12):
        decision = registry.register_session(_session(
            index,
            flatten_dispatches=1 if index < 3 else 0,
            special_gross="1" if index < 3 else "0",
            special_fees="0" if index < 3 else "0",
        ))
    assert decision is CampaignDecision.NOT_READY
    assert "SPECIAL_FLATTEN_RATE" in registry.records()[-1]["payload"]["reasons"]
    assert registry.aggregate()["normal_net_pnl_usdt"] == "2.40"


def test_special_flatten_rate_counts_dispatch_sessions_not_special_fills(
    tmp_path: Path,
) -> None:
    """One dispatch may settle through several special trades.

    The campaign has two such sessions (1 and 4), even though its special
    trade-level fill count is four.  The exact 2/12 limit remains admissible;
    a third dispatch session remains fail-closed at terminal evaluation.
    """
    registry = CampaignRegistry.initialize(tmp_path / "two", _manifest())
    for index in range(12):
        decision = registry.register_session(_session(
            index,
            flatten_dispatches=1 if index in (0, 3) else 0,
            special_fill_count=1 if index == 0 else (3 if index == 3 else 0),
            special_gross="-0.10" if index in (0, 3) else "0",
            special_fees="0.01" if index in (0, 3) else "0",
        ))
    assert decision is CampaignDecision.READY_FOR_PRODUCTION_READ_ONLY_SHADOW
    aggregate = registry.aggregate()
    assert aggregate["special_flatten_sessions"] == 2
    assert aggregate["special_flatten_fraction"] == str(Decimal(2) / Decimal(12))
    assert aggregate["special_fees_usdt"] == "0.02"

    capped = CampaignRegistry.initialize(tmp_path / "three", _manifest())
    for index in range(12):
        decision = capped.register_session(_session(
            index,
            flatten_dispatches=1 if index in (0, 3, 7) else 0,
            special_fill_count=1 if index != 3 else 3,
        ))
    assert decision is CampaignDecision.NOT_READY
    assert "SPECIAL_FLATTEN_RATE" in capped.records()[-1]["payload"]["reasons"]


def test_hard_kill_and_aggregate_loss_fail_closed_early(tmp_path: Path) -> None:
    hard = CampaignRegistry.initialize(tmp_path / "hard", _manifest(
        "economic-campaign-hard"
    ))
    assert hard.register_session(_session(0, hard_kill=True)) is CampaignDecision.NOT_READY
    assert "UNSAFE_SESSION" in hard.records()[-1]["payload"]["reasons"]

    loss = CampaignRegistry.initialize(tmp_path / "loss", _manifest(
        "economic-campaign-loss"
    ))
    assert loss.register_session(_session(
        0, normal_gross="-37.40", normal_fees="0.10"
    )) is CampaignDecision.IN_PROGRESS
    assert loss.register_session(_session(
        1, normal_gross="-37.40", normal_fees="0.10"
    )) is CampaignDecision.NOT_READY
    assert "AGGREGATE_HARD_LOSS" in loss.records()[-1]["payload"]["reasons"]


def test_identity_source_overlap_and_budget_drift_are_refused_without_append(
    tmp_path: Path,
) -> None:
    registry = CampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    first = _session(0)
    registry.register_session(first)
    before = len(registry.records())
    with pytest.raises(CampaignError, match="reuse"):
        registry.register_session(first)
    assert len(registry.records()) == before

    overlap_payload = _session_payload(1)
    overlap_payload["started_at_ms"] = first.ended_at_ms - 1
    overlap_payload["ended_at_ms"] = first.ended_at_ms + 100
    with pytest.raises(CampaignError, match="overlap"):
        registry.register_session(SessionEvidence.from_dict(
            seal_session_evidence(overlap_payload)
        ))
    assert len(registry.records()) == before

    drift_payload = _session_payload(1)
    drift_payload["source_sha256"] = "b" * 64
    with pytest.raises(CampaignError, match="source binding"):
        registry.register_session(SessionEvidence.from_dict(
            seal_session_evidence(drift_payload)
        ))
    assert len(registry.records()) == before


def test_campaign_wall_budget_counts_inter_session_gaps_not_only_active_time(
    tmp_path: Path,
) -> None:
    registry = CampaignRegistry.initialize(tmp_path / "campaign", _manifest())
    first = _session(0)
    assert registry.register_session(first) is CampaignDecision.IN_PROGRESS
    late = _session_payload(1)
    late["started_at_ms"] = first.started_at_ms + 6 * 60 * 60 * 1000 + 1
    late["ended_at_ms"] = int(late["started_at_ms"]) + 60_000
    with pytest.raises(CampaignError, match="wall budget"):
        registry.register_session(
            SessionEvidence.from_dict(seal_session_evidence(late))
        )
    assert len(registry.sessions()) == 1


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"aggregate_net_pnl_usdt": "999"}, "aggregate net"),
        ({"final_position_btc": "0.01"}, "not flat"),
        ({"mutation_retries": 1}, "mutation_retries"),
        ({"order_amends": 1}, "order_amends"),
        ({"live_endpoint_attempts": 1}, "live_endpoint_attempts"),
        ({"normal_create_acknowledgements": 1}, "create counter extension"),
    ],
)
def test_session_accounting_terminal_and_mutation_boundaries_fail_closed(
    mutation: dict[str, object], match: str
) -> None:
    payload = _session_payload(0)
    payload.update(mutation)
    with pytest.raises(CampaignError, match=match):
        SessionEvidence.from_dict(seal_session_evidence(payload))


def test_causal_and_quote_reconciliation_are_mandatory() -> None:
    causal = _session_payload(0)
    causal["causal_reentry"] = causal["causal_reentry"][:-1]
    with pytest.raises(CampaignError, match="causal re-entry evidence count"):
        SessionEvidence.from_dict(seal_session_evidence(causal))

    quote = _session_payload(0)
    quote["quote_mode_counters"] = {"BALANCED": 8}
    with pytest.raises(CampaignError, match="quote-mode counters"):
        SessionEvidence.from_dict(seal_session_evidence(quote))

    taker = _session_payload(0)
    taker["causal_reentry"][0]["immediate_taker_flatten"] = True
    with pytest.raises(CampaignError, match="taker flatten"):
        SessionEvidence.from_dict(seal_session_evidence(taker))


def test_registry_truncation_hash_corruption_and_manifest_drift_are_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "campaign"
    registry = CampaignRegistry.initialize(root, _manifest())
    registry.register_session(_session(0))
    lines = registry.registry_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    row["payload"]["session"]["normal_creates"] = 59
    lines[-1] = json.dumps(row, sort_keys=True)
    registry.registry_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="record hash"):
        CampaignRegistry.load(root)

    reuse = tmp_path / "reuse"
    CampaignRegistry.initialize(reuse, _manifest("economic-campaign-reuse"))
    with pytest.raises(CampaignError, match="reuse"):
        CampaignRegistry.initialize(reuse, _manifest("economic-campaign-reuse"))
