from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from okx_demo_multi_session_campaign import (
    CampaignError,
    CampaignRegistry,
    SessionEvidence,
    seal_session_evidence,
)


def _causal(trade_id: str, side: str, timestamp_ms: int) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "fill_side": side,
        "fill_timestamp_ms": timestamp_ms,
        "defense_timestamp_ms": timestamp_ms + 1,
        "reentry_timestamp_ms": timestamp_ms + 2,
        "workoff_timestamp_ms": timestamp_ms + 3,
        "maker_reentry_observed": True,
        "maker_workoff_observed": True,
        "immediate_taker_flatten": False,
        "inventory_before_btc": "0",
        "inventory_after_btc": "0",
    }


def _special_closed(
    trade_id: str, timestamp_ms: int, quantity: str = "0.010"
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "fill_side": "sell",
        "fill_timestamp_ms": timestamp_ms,
        "defense_timestamp_ms": timestamp_ms + 1,
        "reentry_timestamp_ms": timestamp_ms + 2,
        "workoff_timestamp_ms": 0,
        "maker_reentry_observed": True,
        "maker_workoff_observed": False,
        "immediate_taker_flatten": True,
        "inventory_before_btc": "0",
        "inventory_after_btc": f"-{quantity}",
        "causal_binding": {
            "fill_order_id": f"order-{trade_id}",
            "fill_quantity_btc": quantity,
            "fill_price_usdt": "79000",
            "remaining_workoff_btc": quantity,
            "reentry_client_order_id": f"client-{trade_id}",
            "reentry_side": "buy",
            "reentry_quantity_btc": quantity,
            "workoff_trade_ids": [],
            "workoff_order_ids": [],
            "matched_workoff_btc": "0",
        },
    }


def _payload(*, two_partial_special_closed: bool = False) -> dict[str, object]:
    special = (
        [_special_closed("special-a", 1_000_300, "0.005"),
         _special_closed("special-b", 1_000_400, "0.005")]
        if two_partial_special_closed
        else [_special_closed("special-a", 1_000_300)]
    )
    normal_ask_fills = 1 + len(special)
    return {
        "session_id": "economic:economic-markout-fixture:p0:fixture",
        "run_id": "economic-markout-fixture",
        "source_sha256": "a" * 64,
        "started_at_ms": 1_000_000,
        "ended_at_ms": 1_060_000,
        "normal_creates": 10,
        "normal_create_dispatches": 10,
        "normal_create_acknowledgements": 10,
        "normal_create_rejections": 0,
        "normal_create_unresolved": 0,
        "reconciles": True,
        "normal_cancels": 6,
        "order_amends": 0,
        "self_trades": 0,
        "read_retries": 0,
        "mutation_retries": 0,
        "maximum_owned_bid_observed": 1,
        "maximum_owned_ask_observed": 1,
        "maximum_inventory_btc_observed": "0.01",
        "maximum_drawdown_usdt": "0.50",
        "hard_kill_triggered": False,
        "normal_bid_fills": 1,
        "normal_ask_fills": normal_ask_fills,
        "normal_fifo_round_trips": 1,
        "realized_spread_pnl_usdt": "0.80",
        "inventory_pnl_usdt": "0.20",
        "normal_gross_pnl_usdt": "1.00",
        "normal_fees_usdt": "0.20",
        "normal_net_pnl_usdt": "0.80",
        "special_fill_count": 1,
        "special_gross_pnl_usdt": "0.10",
        "special_fees_usdt": "0.05",
        "special_net_pnl_usdt": "0.05",
        "aggregate_gross_pnl_usdt": "1.10",
        "aggregate_fees_usdt": "0.25",
        "aggregate_net_pnl_usdt": "0.85",
        "flatten_dispatches": 1,
        "quote_mode_ticks": 10,
        "quote_mode_counters": {"BALANCED": 10},
        "unclassified_quote_mode_ticks": 0,
        "markouts_usdt": ["0.02", "0.03"],
        "causal_reentry": [
            _causal("maker-a", "sell", 1_000_100),
            _causal("maker-b", "buy", 1_000_200),
        ],
        "special_closed_causal_fills": special,
        "special_closed_markouts_usdt": ["0.01"] * len(special),
        "fill_cursor_sha256": "b" * 64,
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


def _session(payload: dict[str, object] | None = None) -> SessionEvidence:
    return SessionEvidence.from_dict(seal_session_evidence(payload or _payload()))


def test_terminal_special_closed_fill_is_excluded_from_causal_markout_floor() -> None:
    session = _session()
    assert session.normal_fill_count == 3
    assert len(session.markouts_usdt) == 2
    assert len(session.causal_reentry) == 2
    assert session.terminal_special_closed_fill_count == 1
    assert session.terminal_special_closed_markout_count == 1


def test_aggregate_reports_exact_split_markout_reconciliation(tmp_path) -> None:
    aggregate = CampaignRegistry.__new__(CampaignRegistry).aggregate([_session()])
    assert aggregate["normal_fill_count"] == 3
    assert aggregate["markout_count"] == 2
    assert aggregate["terminal_special_closed_fill_count"] == 1
    assert aggregate["terminal_special_closed_markout_count"] == 1
    assert aggregate["normal_markout_attribution_reconciles"] is True


def test_multi_partial_terminal_closure_reconciles_each_fifo_fill_identity() -> None:
    session = _session(_payload(two_partial_special_closed=True))
    assert session.normal_fill_count == 4
    assert session.terminal_special_closed_fill_count == 2
    assert session.terminal_special_closed_markout_count == 2


def test_partially_worked_off_fill_can_special_close_only_its_residual() -> None:
    payload = deepcopy(_payload())
    binding = payload["special_closed_causal_fills"][0]["causal_binding"]  # type: ignore[index]
    binding["fill_quantity_btc"] = "0.010"
    binding["remaining_workoff_btc"] = "0.0005"
    binding["matched_workoff_btc"] = "0.0095"
    binding["workoff_trade_ids"] = ["maker-workoff-partial"]
    binding["workoff_order_ids"] = ["order-workoff-partial"]
    session = _session(payload)
    closed = session.extension_fields["special_closed_causal_fills"][0]
    assert closed["causal_binding"]["remaining_workoff_btc"] == "0.0005"
    assert closed["causal_binding"]["matched_workoff_btc"] == "0.0095"


@pytest.mark.parametrize(
    ("remaining", "matched", "trade_ids", "order_ids"),
    (
        ("0.0005", "0.0094", ["trade-partial"], ["order-partial"]),
        ("0.0005", "0.0095", [], ["order-partial"]),
        ("0.0005", "0.0095", ["trade-partial"], []),
        ("0.010", "0", ["forged-trade"], ["forged-order"]),
    ),
)
def test_invalid_partial_special_closure_quantities_fail_closed(
    remaining: str,
    matched: str,
    trade_ids: list[str],
    order_ids: list[str],
) -> None:
    payload = deepcopy(_payload())
    binding = payload["special_closed_causal_fills"][0]["causal_binding"]  # type: ignore[index]
    binding["remaining_workoff_btc"] = remaining
    binding["matched_workoff_btc"] = matched
    binding["workoff_trade_ids"] = trade_ids
    binding["workoff_order_ids"] = order_ids
    with pytest.raises(CampaignError, match="quantities do not reconcile"):
        _session(payload)


def test_terminal_special_closure_survives_sealed_round_trip() -> None:
    original = _session()
    restored = SessionEvidence.from_dict(original.to_dict())
    assert restored.extension_fields == original.extension_fields
    assert restored.terminal_special_closed_fill_count == 1


def test_missing_special_closed_markout_fails_closed() -> None:
    payload = _payload()
    payload["special_closed_markouts_usdt"] = []
    with pytest.raises(CampaignError, match="markouts are incomplete"):
        _session(payload)


def test_overlapping_causal_and_special_trade_identity_fails_closed() -> None:
    payload = _payload()
    payload["special_closed_causal_fills"][0]["trade_id"] = "maker-a"  # type: ignore[index]
    with pytest.raises(CampaignError, match="trade identity is duplicated"):
        _session(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (("immediate_taker_flatten", False), ("maker_workoff_observed", True)),
)
def test_special_closed_fill_cannot_claim_maker_workoff_credit(
    field: str, value: object
) -> None:
    payload = deepcopy(_payload())
    payload["special_closed_causal_fills"][0][field] = value  # type: ignore[index]
    with pytest.raises(CampaignError, match="attribution is invalid"):
        _session(payload)


def test_legacy_evidence_cannot_silently_omit_a_normal_markout() -> None:
    payload = _payload()
    payload.pop("special_closed_causal_fills")
    payload.pop("special_closed_markouts_usdt")
    with pytest.raises(CampaignError, match="markout evidence count"):
        _session(payload)
