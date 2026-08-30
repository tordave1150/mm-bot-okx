"""Deterministic contract tests for shared order-lifecycle reconciliation."""

from __future__ import annotations

import pytest

from backtest.matching_engine import CancellationReason, MatchingEngine
from config import Config
from fill_tracker import FillTracker


TS = 1_800_000_000_000


def _tracker() -> FillTracker:
    return FillTracker(Config())


def _tick(bid: float = 99.0, ask: float = 101.0, timestamp: int = TS) -> dict:
    return {"bids": [[bid, 1.0]], "asks": [[ask, 1.0]], "timestamp": timestamp}


def _context(reason: CancellationReason) -> dict:
    return {
        "reason": reason,
        "source_component": "test_funnel_repair",
        "source_event": "deterministic_fixture",
        "timestamp_ms": TS,
        "strategy_version": "repair-test-v1",
        "profile_fingerprint": "market-maker-test-profile",
        "protocol_id": "infrastructure-only-repair-test",
    }


def _assert_exact(engine: MatchingEngine, created: int = 1) -> dict:
    result = engine.reconciliation()
    assert result["passive_orders_created"] == created
    assert result["unclassified_order_removals"] == 0
    assert result["zero_terminal_reason_orders"] == 0
    assert result["multiple_terminal_reason_orders"] == 0
    assert result["duplicate_terminal_transitions"] == 0
    assert result["order_count_reconciliation"] is True
    assert result["order_quantity_reconciliation"] is True
    assert result["funnel_accounting_reconciliation"] is True
    return result


@pytest.mark.parametrize(
    "reason",
    [
        CancellationReason.CANCEL_MAXIMUM_ORDER_AGE,
        CancellationReason.CANCEL_REQUOTE_PRICE_CHANGE,
        CancellationReason.CANCEL_REQUOTE_SIZE_CHANGE,
        CancellationReason.CANCEL_STRATEGY_REPLACE,
        CancellationReason.CANCEL_INVENTORY_REBALANCE,
        CancellationReason.CANCEL_RISK_SOFT_STOP,
        CancellationReason.CANCEL_RISK_HARD_KILL,
        CancellationReason.CANCEL_TERMINAL_CLEANUP,
        CancellationReason.CANCEL_EMERGENCY_CLEANUP,
        CancellationReason.CANCEL_SHUTDOWN,
    ],
)
def test_representative_cancellation_reasons_reconcile(reason):
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 100.0, 0.01)
    assert engine.cancel_order(order_id, **_context(reason)) is True
    result = _assert_exact(engine)
    assert result["fully_cancelled_orders"] == 1
    terminal = [
        event for event in engine.lifecycle_events
        if event["event"] == "order_terminal"
    ]
    assert len(terminal) == 1
    assert terminal[0]["reason"] == reason.value


def test_cancel_all_requires_explicit_reason():
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    with pytest.raises(TypeError):
        engine.cancel_all(
            source_component="test",
            source_event="missing_reason",
            timestamp_ms=TS,
            strategy_version="test-v1",
            profile_fingerprint="market-maker-test-profile",
            protocol_id="FUNNEL_REPAIR_TEST",
        )


def test_unknown_reason_rejected_immediately():
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    with pytest.raises(ValueError, match="unknown cancellation reason"):
        engine.cancel_all(
            reason="CANCEL_OTHER",
            source_component="test",
            source_event="unknown_reason",
            timestamp_ms=TS,
            strategy_version="test-v1",
            profile_fingerprint="market-maker-test-profile",
            protocol_id="FUNNEL_REPAIR_TEST",
        )


def test_unknown_error_is_retained_but_fails_integrity():
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    engine.cancel_all(**_context(CancellationReason.CANCEL_UNKNOWN_ERROR))
    result = engine.reconciliation()
    assert result["unclassified_order_removals"] == 1
    assert result["funnel_accounting_reconciliation"] is False


def test_bulk_cancel_emits_one_terminal_per_order_and_aggregate():
    engine = MatchingEngine()
    ids = [
        engine.place_order("buy", 100.0, 0.01),
        engine.place_order("sell", 102.0, 0.02),
    ]
    affected = engine.cancel_all(
        **_context(CancellationReason.CANCEL_RISK_SOFT_STOP)
    )
    assert affected == tuple(ids)
    terminal = [
        event for event in engine.lifecycle_events
        if event["event"] == "order_terminal"
    ]
    bulk = [
        event for event in engine.lifecycle_events
        if event["event"] == "bulk_cancel"
    ]
    assert [event["order_id"] for event in terminal] == ids
    assert len(bulk) == 1
    assert bulk[0]["affected_order_ids"] == ids
    _assert_exact(engine, created=2)


def test_duplicate_bulk_cancel_does_not_duplicate_terminal_outcome():
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    kwargs = _context(CancellationReason.CANCEL_TERMINAL_CLEANUP)
    assert len(engine.cancel_all(**kwargs)) == 1
    assert engine.cancel_all(**kwargs) == ()
    terminal = [
        event for event in engine.lifecycle_events
        if event["event"] == "order_terminal"
    ]
    assert len(terminal) == 1
    _assert_exact(engine)


def test_already_filled_order_is_not_bulk_cancelled():
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 100.0, 0.01)
    assert engine.check_fills(_tick(98.0, 100.0), _tracker())
    affected = engine.cancel_all(
        **_context(CancellationReason.CANCEL_TERMINAL_CLEANUP)
    )
    assert order_id not in affected
    result = _assert_exact(engine)
    assert result["fully_filled_orders"] == 1
    assert result["fully_cancelled_orders"] == 0


def test_cancel_request_then_fill_has_one_filled_terminal_outcome():
    engine = MatchingEngine(cancel_latency_ticks=2)
    order_id = engine.place_order("buy", 100.0, 0.01)
    engine.cancel_order(
        order_id, **_context(CancellationReason.CANCEL_REQUOTE_PRICE_CHANGE)
    )
    fills = engine.check_fills(_tick(98.0, 99.0), _tracker())
    assert len(fills) == 1
    result = _assert_exact(engine)
    assert result["fully_filled_orders"] == 1
    assert any(
        event["event"] == "cancel_requested"
        for event in engine.lifecycle_events
    )


def test_fill_then_later_cancel_attempt_retains_one_terminal_outcome():
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 100.0, 0.01)
    engine.check_fills(_tick(98.0, 99.0), _tracker())
    assert engine.cancel_order(
        order_id, **_context(CancellationReason.CANCEL_TERMINAL_CLEANUP)
    ) is False
    assert any(
        event["event"] == "cancel_attempt_ignored"
        for event in engine.lifecycle_events
    )
    _assert_exact(engine)


def test_partial_fill_residual_cancellation_reconciles_quantity(monkeypatch):
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 100.0, 0.02)
    monkeypatch.setattr(
        engine, "_check_single_fill", lambda *_: (100.0, 0.01, True)
    )
    engine.check_fills(_tick(), _tracker())
    engine.cancel_order(
        order_id, **_context(CancellationReason.CANCEL_REQUOTE_SIZE_CHANGE)
    )
    result = _assert_exact(engine)
    assert result["partially_filled_orders"] == 1
    assert result["filled_quantity"] == pytest.approx(0.01)
    assert result["cancelled_quantity"] == pytest.approx(0.01)


def test_partial_fill_residual_open_at_terminal_reconciles(monkeypatch):
    engine = MatchingEngine()
    engine.place_order("sell", 102.0, 0.02)
    monkeypatch.setattr(
        engine, "_check_single_fill", lambda *_: (102.0, 0.01, True)
    )
    engine.check_fills(_tick(), _tracker())
    engine.finalize_open_orders(
        timestamp_ms=TS,
        source_component="test_funnel_repair",
        source_event="terminal_open",
        strategy_version="repair-test-v1",
        profile_fingerprint="market-maker-test-profile",
        protocol_id="FUNNEL_REPAIR_TEST",
    )
    result = _assert_exact(engine)
    assert result["partially_filled_orders"] == 1
    assert result["open_orders_at_terminal"] == 1
    assert result["open_quantity_at_terminal"] == pytest.approx(0.01)


def test_rejection_before_activation_reconciles():
    engine = MatchingEngine()
    engine.record_rejected_before_activation(
        side="buy",
        price=100.0,
        size=0.01,
        timestamp_ms=TS,
        rejection_reason="POST_ONLY_WOULD_CROSS",
    )
    result = _assert_exact(engine)
    assert result["rejected_before_activation"] == 1
    assert result["rejected_quantity"] == pytest.approx(0.01)


def test_expiration_reconciles():
    engine = MatchingEngine()
    order_id = engine.place_order("buy", 100.0, 0.01)
    assert engine.expire_order(
        order_id,
        timestamp_ms=TS,
        source_component="test_funnel_repair",
        source_event="protocol_expiry",
    )
    result = _assert_exact(engine)
    assert result["expired_orders"] == 1


def test_live_order_fails_until_explicit_terminal_classification():
    engine = MatchingEngine()
    engine.place_order("buy", 100.0, 0.01)
    before = engine.reconciliation()
    assert before["zero_terminal_reason_orders"] == 1
    assert before["funnel_accounting_reconciliation"] is False
    engine.finalize_open_orders(
        timestamp_ms=TS,
        source_component="test_funnel_repair",
        source_event="terminal_open",
        strategy_version="repair-test-v1",
        profile_fingerprint="market-maker-test-profile",
        protocol_id="FUNNEL_REPAIR_TEST",
    )
    after = _assert_exact(engine)
    assert after["open_orders_at_terminal"] == 1
