from decimal import Decimal

import pytest

from okx_demo_soak_failure_injection import (
    AccountPoint,
    BoundedSoakFaultHarness,
    FillRow,
    FixtureDecision,
    SoakFixtureError,
    scenario_matrix,
)


def _fill(
    trade_id: str,
    client_id: str,
    *,
    side: str = "buy",
    quantity: str = "0.01",
    timestamp: int = 1,
) -> FillRow:
    return FillRow(
        trade_id=trade_id,
        client_order_id=client_id,
        side=side,
        quantity_btc=Decimal(quantity),
        price_usdt=Decimal("65000"),
        fee_usdt=Decimal("0.13"),
        timestamp_ms=timestamp,
    )


def test_timeout_boundaries_never_retry_ambiguous_mutation() -> None:
    harness = BoundedSoakFaultHarness()
    baseline = harness.snapshot()
    harness.begin_create("before")
    assert harness.timeout_before_dispatch() is FixtureDecision.CONTINUE
    assert harness.snapshot() == baseline

    harness.begin_create("after")
    harness.dispatch_create()
    after_dispatch = harness.snapshot()
    assert harness.timeout_after_dispatch() is FixtureDecision.PLACEMENT_BLOCKED
    assert harness.placement_allowed is False
    with pytest.raises(SoakFixtureError, match="not permitted"):
        harness.begin_create("duplicate")
    assert harness.snapshot() == after_dispatch
    assert harness.reconcile_dispatched_absent() is FixtureDecision.PLACEMENT_BLOCKED
    assert harness.reconcile_dispatched_absent() is FixtureDecision.CONTINUE
    assert harness.snapshot() == after_dispatch


def test_duplicate_ack_and_fill_are_idempotent_but_conflicts_fail_closed() -> None:
    harness = BoundedSoakFaultHarness()
    harness.begin_create("owned")
    harness.dispatch_create()
    harness.acknowledge_create("owned", "order-1")
    baseline = harness.snapshot()
    harness.acknowledge_create("owned", "order-1")
    assert harness.snapshot() == baseline
    fill = _fill("trade-1", "owned")
    assert harness.ingest_fill(fill) is True
    position = harness.controller_position_btc
    assert harness.ingest_fill(fill) is False
    assert harness.controller_position_btc == position

    conflicting = _fill("trade-1", "owned", quantity="0.005")
    with pytest.raises(SoakFixtureError, match="conflicting"):
        harness.ingest_fill(conflicting)
    assert harness.halted_reason == "CONFLICTING_DUPLICATE_FILL"
    assert harness.snapshot() == baseline


def test_explicit_rejected_ack_clears_only_the_bound_ambiguous_intent() -> None:
    harness = BoundedSoakFaultHarness()
    harness.begin_create("rejected")
    harness.dispatch_create()
    harness.acknowledge_rejected_create(
        "rejected", classification="REJECTED_TERMINAL_ABSENT"
    )
    assert harness.ambiguous_intent == ""
    assert harness.open_owned == {}
    assert harness.normal_creates == 1
    with pytest.raises(SoakFixtureError, match="not authoritative"):
        harness.acknowledge_rejected_create(
            "wrong", classification="REJECTED_TERMINAL_ABSENT"
        )


def test_fill_union_recovers_tail_omission_and_orders_delayed_rows() -> None:
    older = _fill("older", "a", timestamp=100)
    recovered = _fill("recovered", "b", timestamp=200)
    duplicate = _fill("older", "a", timestamp=100)
    union = BoundedSoakFaultHarness.union_fills(
        (recovered, older), (duplicate,)
    )
    assert [item.trade_id for item in union] == ["older", "recovered"]
    conflict = _fill("older", "a", quantity="0.005", timestamp=100)
    with pytest.raises(SoakFixtureError, match="union conflict"):
        BoundedSoakFaultHarness.union_fills((older,), (conflict,))


def test_multi_partial_flatten_is_single_flight_and_idempotent() -> None:
    harness = BoundedSoakFaultHarness(
        controller_position_btc=Decimal("0.01"),
        engine_position_btc=Decimal("0.01"),
    )
    harness.start_flatten()
    dispatch = harness.snapshot()
    with pytest.raises(SoakFixtureError, match="single-flight"):
        harness.start_flatten()
    harness.flatten_partial("part-1", Decimal("0.004"))
    harness.flatten_partial("part-1", Decimal("0.004"))
    harness.flatten_partial("part-2", Decimal("0.006"))
    assert harness.controller_position_btc == 0
    assert harness.engine_position_btc == 0
    assert harness.snapshot() == dispatch
    assert harness.flatten_dispatches == 1


def test_read_backoff_is_bounded_and_never_mutates() -> None:
    harness = BoundedSoakFaultHarness()
    baseline = harness.snapshot()
    assert harness.inject_read_failure(1, 3) is FixtureDecision.RETRY_READ_ONLY
    assert harness.inject_read_failure(2, 3) is FixtureDecision.RETRY_READ_ONLY
    assert harness.inject_read_failure(3, 3) is FixtureDecision.FAIL_CLOSED
    assert harness.halted_reason == "READ_RETRY_EXHAUSTED"
    assert harness.snapshot() == baseline


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, FixtureDecision.CONTINUE),
        (1_000, FixtureDecision.CONTINUE),
        (-1_500, FixtureDecision.CONTINUE),
        (-1_499, FixtureDecision.CONTINUE),
        (1_001, FixtureDecision.PLACEMENT_BLOCKED),
        (-1_501, FixtureDecision.PLACEMENT_BLOCKED),
    ],
)
def test_signed_book_boundaries(age: int, expected: FixtureDecision) -> None:
    assert BoundedSoakFaultHarness.classify_book(
        bid="64999.9",
        ask="65000.1",
        signed_age_ms=age,
        maximum_age_ms=1_000,
        maximum_clock_skew_ms=1_500,
    ) is expected


@pytest.mark.parametrize(
    ("bid", "ask", "age"),
    [
        ("65000", "65000", 0),
        ("65001", "65000", 0),
        ("0", "65000", 0),
        ("nan", "65000", 0),
        ("64999", "65000", "bad"),
    ],
)
def test_crossed_empty_and_invalid_books_fail_closed(
    bid: str, ask: str, age: object
) -> None:
    assert BoundedSoakFaultHarness.classify_book(
        bid=bid,
        ask=ask,
        signed_age_ms=age,
        maximum_age_ms=1_000,
        maximum_clock_skew_ms=1_500,
    ) is FixtureDecision.FAIL_CLOSED


def test_single_instance_collision_and_hash_mismatch_fail_without_mutation() -> None:
    harness = BoundedSoakFaultHarness()
    harness.acquire_lease("p0", now_ms=1, ttl_ms=100)
    baseline = harness.snapshot()
    decision = harness.restart(
        "p1", now_ms=50, ttl_ms=100, expected_durable_hash=harness.durable_hash
    )
    assert decision is FixtureDecision.FAIL_CLOSED
    assert harness.halted_reason == "SINGLE_INSTANCE_COLLISION"
    assert harness.snapshot() == baseline

    harness = BoundedSoakFaultHarness()
    assert harness.restart(
        "p1", now_ms=200, ttl_ms=100, expected_durable_hash="wrong"
    ) is FixtureDecision.FAIL_CLOSED
    assert harness.halted_reason == "DURABLE_HASH_MISMATCH"
    assert harness.snapshot().total_order_mutations == 0


def test_restart_preserves_ambiguous_intent_and_blocks_placement() -> None:
    harness = BoundedSoakFaultHarness()
    harness.begin_create("ambiguous")
    harness.dispatch_create()
    expected = harness.durable_hash
    assert harness.restart(
        "p1", now_ms=200, ttl_ms=100, expected_durable_hash=expected
    ) is FixtureDecision.PLACEMENT_BLOCKED
    assert harness.ambiguous_intent == "ambiguous"
    assert harness.snapshot().normal_creates == 1


def test_foreign_cancel_fill_and_terminal_mismatch_fail_closed_without_delta() -> None:
    harness = BoundedSoakFaultHarness()
    baseline = harness.snapshot()
    with pytest.raises(SoakFixtureError, match="not owned"):
        harness.cancel_owned("foreign")
    assert harness.snapshot() == baseline

    harness = BoundedSoakFaultHarness()
    with pytest.raises(SoakFixtureError, match="does not belong"):
        harness.ingest_fill(_fill("foreign-fill", "foreign"))
    assert harness.snapshot().total_order_mutations == 0

    harness = BoundedSoakFaultHarness()
    assert harness.terminal_reconcile((
        AccountPoint(harness.binding, Decimal("0.01")),
        AccountPoint(harness.binding, Decimal("0")),
    )) is FixtureDecision.FAIL_CLOSED
    assert harness.snapshot().total_order_mutations == 0


def test_terminal_pair_and_evidence_write_boundary() -> None:
    points = (
        AccountPoint("offline-fixture-binding", Decimal("0")),
        AccountPoint("offline-fixture-binding", Decimal("0")),
    )
    harness = BoundedSoakFaultHarness()
    baseline = harness.snapshot()
    assert harness.terminal_reconcile(
        points, evidence_write_succeeds=False
    ) is FixtureDecision.FAIL_CLOSED
    assert harness.halted_reason == "TERMINAL_EVIDENCE_WRITE_FAILED"
    assert harness.snapshot() == baseline

    harness = BoundedSoakFaultHarness()
    assert harness.terminal_reconcile(points) is FixtureDecision.TERMINAL_COMPLETE
    assert harness.terminal_complete is True
    assert harness.snapshot() == baseline


def test_scenario_matrix_is_bounded_and_offline_only() -> None:
    matrix = scenario_matrix()
    assert len(matrix) >= 12
    assert len({row["scenario"] for row in matrix}) == len(matrix)
    assert all(row["network_allowed"] is False for row in matrix)
    assert all(row["live_allowed"] is False for row in matrix)
    assert all(
        row["account_configuration_mutation_allowed"] is False for row in matrix
    )
    assert max(int(row["maximum_order_mutation_delta"]) for row in matrix) == 1
