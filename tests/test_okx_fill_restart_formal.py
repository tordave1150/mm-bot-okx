from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from okx_fill_restart_formal import (
    FormalActionType,
    FormalOrchestrator,
    FormalPersistenceError,
    FormalSafetyError,
    FormalStage,
    FormalStateStore,
    make_fixture_observation,
    make_fixture_quote,
    make_fixture_spec,
)


def _pair(spec, start: int, **values):
    return (
        make_fixture_observation(
            spec,
            sequence=start,
            observed_at_ms=start * 1_000,
            market_timestamp_ms=start * 1_000,
            **values,
        ),
        make_fixture_observation(
            spec,
            sequence=start + 1,
            observed_at_ms=(start + 1) * 1_000,
            market_timestamp_ms=(start + 1) * 1_000,
            **values,
        ),
    )


def _armed(tmp_path: Path):
    spec = make_fixture_spec()
    store = FormalStateStore(tmp_path / "formal_state.json")
    controller = FormalOrchestrator.prepare(spec, store)
    controller.arm_and_start(
        arm_token=spec.expected_arm_token,
        snapshots=_pair(spec, 1),
        now_ms=100,
    )
    return spec, store, controller


def _fill_values(*, side: str, round_trip: bool = False):
    if side == "buy":
        return {
            "position_btc": "0.01",
            "average_entry_usdt": "50000",
            "normal_bid_fills_total": 1,
            "normal_ask_fills_total": 0,
            "normal_fifo_round_trips_total": 0,
            "fill_cursor_timestamp_ms": 3_000,
            "fill_cursor_trade_id": "trade-bid-1",
            "fill_deduplication_sha256": "1" * 64,
            "gross_realized_pnl_usdt": "0",
            "actual_fees_usdt": "0.01",
            "net_realized_pnl_usdt": "-0.01",
        }
    assert round_trip
    return {
        "position_btc": "0",
        "average_entry_usdt": "0",
        "normal_bid_fills_total": 1,
        "normal_ask_fills_total": 1,
        "normal_fifo_round_trips_total": 1,
        "fill_cursor_timestamp_ms": 7_000,
        "fill_cursor_trade_id": "trade-ask-1",
        "fill_deduplication_sha256": "2" * 64,
        "gross_realized_pnl_usdt": "1.00",
        "actual_fees_usdt": "0.02",
        "net_realized_pnl_usdt": "0.98",
    }


def test_prepare_is_offline_not_armed_and_run_reuse_is_refused(tmp_path: Path) -> None:
    spec = make_fixture_spec()
    store = FormalStateStore(tmp_path / "formal_state.json")
    controller = FormalOrchestrator.prepare(spec, store)
    assert controller.state.stage is FormalStage.NOT_ARMED
    assert controller.state.normal_create_count == 0
    assert controller.state.pending_intents == {}
    assert controller.state.owned_orders == {}
    with pytest.raises(FormalPersistenceError, match="already exists"):
        FormalOrchestrator.prepare(spec, store)


def test_spec_and_arm_fail_closed_on_drift_or_bad_snapshot(tmp_path: Path) -> None:
    spec = make_fixture_spec()
    with pytest.raises(FormalSafetyError, match="LIVE"):
        replace(spec, live_mode_available=True).validate()
    with pytest.raises(FormalSafetyError, match="capital or leverage"):
        replace(spec, leverage=4).validate()
    controller = FormalOrchestrator.prepare(
        spec, FormalStateStore(tmp_path / "state.json")
    )
    with pytest.raises(FormalSafetyError, match="arm token"):
        controller.arm_and_start(
            arm_token="wrong", snapshots=_pair(spec, 1), now_ms=100
        )
    bad = _pair(spec, 1, sandbox_mode=False)
    with pytest.raises(FormalSafetyError, match="DEMO_TRANSPORT_UNPROVEN"):
        controller.arm_and_start(
            arm_token=spec.expected_arm_token, snapshots=bad, now_ms=100
        )
    assert controller.state.stage is FormalStage.NOT_ARMED


def test_restart_consistency_excludes_moving_market_and_equity() -> None:
    spec = make_fixture_spec()
    first = make_fixture_observation(
        spec,
        sequence=1,
        observed_at_ms=1_000,
        market_timestamp_ms=1_000,
        best_bid=Decimal("49999"),
        best_ask=Decimal("50001"),
        equity_usdt=Decimal("750"),
        available_equity_usdt=Decimal("700"),
    )
    second = replace(
        first,
        sequence=2,
        observed_at_ms=2_000,
        market_timestamp_ms=2_000,
        best_bid=Decimal("50009"),
        best_ask=Decimal("50011"),
        equity_usdt=Decimal("751"),
        available_equity_usdt=Decimal("701"),
    )
    assert first.consistency_key == second.consistency_key
    assert first.safety_failure(spec) == ""
    assert second.safety_failure(spec) == ""


def test_write_ahead_identity_rate_cap_and_ambiguous_resolution(tmp_path: Path) -> None:
    spec, _, controller = _armed(tmp_path)
    observation = _pair(spec, 1)[1]
    quote = replace(make_fixture_quote(spec, observation), ask_suppressed=True)
    actions = controller.plan_quotes(observation=observation, quote=quote, now_ms=3_000)
    assert [item.action for item in actions] == [FormalActionType.PLACE_POST_ONLY]
    client_id = str(actions[0].payload["client_order_id"])
    assert client_id in controller.state.pending_intents
    assert controller.state.normal_create_count == 0
    controller.record_create_dispatch(client_order_id=client_id, now_ms=3_000)
    assert controller.state.normal_create_count == 1
    resolution = controller.record_ambiguous_create(client_order_id=client_id)
    assert resolution[0].action is FormalActionType.RESOLVE_AMBIGUOUS_CREATE
    assert resolution[0].payload["automatic_retry_allowed"] is False
    with pytest.raises(FormalSafetyError, match="already awaiting"):
        controller.record_ambiguous_create(client_order_id=client_id)
    controller.resolve_ambiguous_create(
        client_order_id=client_id, authoritative_status="absent"
    )
    later = controller.plan_quotes(
        observation=observation, quote=quote, now_ms=5_000
    )
    assert later[0].payload["client_order_id"] != client_id


def test_undispatched_intent_can_be_abandoned_but_dispatched_intent_cannot(
    tmp_path: Path,
) -> None:
    spec, _, controller = _armed(tmp_path)
    observation = _pair(spec, 1)[1]
    actions = controller.plan_quotes(
        observation=observation,
        quote=make_fixture_quote(spec, observation),
        now_ms=3_000,
    )
    first, second = actions
    controller.record_create_dispatch(
        client_order_id=str(first.payload["client_order_id"]), now_ms=3_000
    )
    with pytest.raises(FormalSafetyError, match="cannot be abandoned"):
        controller.abandon_undispatched_intent(
            client_order_id=str(first.payload["client_order_id"]),
            reason="fixture",
        )
    controller.abandon_undispatched_intent(
        client_order_id=str(second.payload["client_order_id"]),
        reason="fill observed before second dispatch",
    )
    assert str(second.payload["client_order_id"]) not in controller.state.pending_intents


def test_ambiguous_create_can_resolve_closed_without_retry(tmp_path: Path) -> None:
    spec, _, controller = _armed(tmp_path)
    observation = _pair(spec, 1)[1]
    quote = replace(make_fixture_quote(spec, observation), ask_suppressed=True)
    action = controller.plan_quotes(
        observation=observation, quote=quote, now_ms=3_000
    )[0]
    client_id = str(action.payload["client_order_id"])
    controller.record_create_dispatch(client_order_id=client_id, now_ms=3_000)
    controller.record_ambiguous_create(client_order_id=client_id)
    controller.resolve_ambiguous_create(
        client_order_id=client_id,
        authoritative_status="closed",
        post_only_confirmed=True,
    )
    assert controller.state.pending_intents == {}
    assert controller.state.owned_orders == {}
    assert controller.state.normal_create_count == 1


def test_missing_order_incomplete_or_foreign_snapshot_halts_without_create(
    tmp_path: Path,
) -> None:
    spec, _, controller = _armed(tmp_path)
    observation = _pair(spec, 1)[1]
    quote = replace(make_fixture_quote(spec, observation), ask_suppressed=True)
    action = controller.plan_quotes(observation=observation, quote=quote, now_ms=3_000)[0]
    client_id = str(action.payload["client_order_id"])
    controller.record_create_dispatch(client_order_id=client_id, now_ms=3_000)
    controller.record_post_only_ack(
        client_order_id=client_id,
        side="buy",
        post_only_confirmed=True,
        now_ms=3_000,
    )
    result = controller.observe(
        make_fixture_observation(
            spec,
            sequence=3,
            observed_at_ms=3_000,
            market_timestamp_ms=3_000,
        ),
        now_ms=4_000,
    )
    assert result[-1].action is FormalActionType.HALT
    assert controller.state.halted_reason == "MISSING_EXPECTED_ORDER_WITHOUT_FILL"
    assert controller.state.normal_create_count == 1

    for index, override in enumerate((
        {"trades_complete": False},
        {"foreign_order_count": 1},
        {"best_ask": Decimal("49998")},
        {"clock_skew_ms": 2_000},
    ), start=10):
        spec2, _, candidate = _armed(tmp_path / str(index))
        result = candidate.observe(
            make_fixture_observation(
                spec2,
                sequence=3,
                observed_at_ms=3_000,
                market_timestamp_ms=3_000,
                **override,
            ),
            now_ms=4_000,
        )
        assert result[-1].action is FormalActionType.HALT
        assert candidate.state.normal_create_count == 0


def test_happy_path_requires_r1_r2_and_exact_accounting(tmp_path: Path) -> None:
    spec, store, controller = _armed(tmp_path)
    initial = _pair(spec, 1)[1]
    places = controller.plan_quotes(
        observation=initial,
        quote=make_fixture_quote(spec, initial),
        now_ms=3_000,
    )
    assert [item.payload["side"] for item in places] == ["buy", "sell"]
    order_ids = {}
    for index, action in enumerate(places):
        side = str(action.payload["side"])
        order_id = str(action.payload["client_order_id"])
        order_ids[side] = order_id
        dispatched_at = 3_000 + index * 2_000
        controller.record_create_dispatch(
            client_order_id=order_id, now_ms=dispatched_at
        )
        controller.record_post_only_ack(
            client_order_id=order_id,
            side=side,
            post_only_confirmed=True,
            now_ms=dispatched_at,
        )

    bid_fill = make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        open_owned_orders=((order_ids["sell"], "sell"),),
        **_fill_values(side="buy"),
    )
    assert controller.observe(bid_fill, now_ms=7_000)[0].action is FormalActionType.CANCEL_ALL_OWNED
    controller.record_authoritative_cancellation(remaining_open_client_ids=[])
    reconciled_r1 = replace(
        bid_fill,
        sequence=4,
        observed_at_ms=4_000,
        market_timestamp_ms=4_000,
        open_owned_orders=(),
    )
    assert controller.observe(reconciled_r1, now_ms=8_000)[0].action is FormalActionType.PERSIST_R1_CHECKPOINT
    assert controller.persist_r1_checkpoint("r1-checkpoint")[0].action is FormalActionType.EXIT_RESTART_R1

    restored = FormalOrchestrator(
        spec=spec, store=store, state=store.load(spec)
    )
    r1_pair = _pair(spec, 5, **_fill_values(side="buy"))
    restored.resume_r1(checkpoint_id="r1-checkpoint", snapshots=r1_pair)
    offset = restored.plan_quotes(
        observation=r1_pair[1],
        quote=make_fixture_quote(spec, r1_pair[1]),
        now_ms=10_000,
    )
    assert len(offset) == 1 and offset[0].payload["side"] == "sell"
    sell_id = str(offset[0].payload["client_order_id"])
    restored.record_create_dispatch(client_order_id=sell_id, now_ms=10_000)
    restored.record_post_only_ack(
        client_order_id=sell_id,
        side="sell",
        post_only_confirmed=True,
        now_ms=10_000,
    )
    ask_fill = make_fixture_observation(
        spec,
        sequence=7,
        observed_at_ms=7_000,
        market_timestamp_ms=7_000,
        **_fill_values(side="sell", round_trip=True),
    )
    assert restored.observe(ask_fill, now_ms=11_000)[0].action is FormalActionType.ACTIVATE_VALIDATION_KILL
    restored.record_authoritative_cancellation(remaining_open_client_ids=[])
    post_fill = replace(
        ask_fill, sequence=8, observed_at_ms=8_000, market_timestamp_ms=8_000
    )
    assert restored.observe(post_fill, now_ms=12_000)[0].action is FormalActionType.ACTIVATE_VALIDATION_KILL
    r2_actions = restored.activate_r2_kill()
    assert [item.action for item in r2_actions] == [
        FormalActionType.PERSIST_R2_CHECKPOINT,
        FormalActionType.EXIT_RESTART_R2,
    ]
    activation = restored.state.validation_kill_id

    restored = FormalOrchestrator(spec=spec, store=store, state=store.load(spec))
    r2_pair = _pair(spec, 9, **_fill_values(side="sell", round_trip=True))
    restored.resume_r2(checkpoint_id=activation, snapshots=r2_pair)
    with pytest.raises(FormalSafetyError, match="blocked"):
        restored.plan_quotes(
            observation=r2_pair[1],
            quote=make_fixture_quote(spec, r2_pair[1]),
            now_ms=15_000,
        )
    release_pair = _pair(spec, 11, **_fill_values(side="sell", round_trip=True))
    restored.release_r2_kill(
        acknowledgement_id=activation, snapshots=release_pair
    )
    final_pair = _pair(spec, 13, **_fill_values(side="sell", round_trip=True))
    assert restored.finalize(final_pair)[0].action is FormalActionType.FINALIZE
    assert restored.state.stage is FormalStage.COMPLETE
    assert restored.state.process_generation == 2
    assert restored.state.position_btc == 0
    assert restored.state.actual_fees_usdt == Decimal("0.02")
    assert restored.state.net_realized_pnl_usdt == Decimal("0.98")


def test_both_sides_before_r1_and_bad_accounting_fail_closed(tmp_path: Path) -> None:
    spec, _, controller = _armed(tmp_path)
    both = make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        position_btc="0.01",
        average_entry_usdt="50000",
        normal_bid_fills_total=1,
        normal_ask_fills_total=1,
        fill_cursor_timestamp_ms=3_000,
        fill_cursor_trade_id="both",
        fill_deduplication_sha256="3" * 64,
    )
    controller.observe(both, now_ms=4_000)
    assert controller.state.halted_reason == "BOTH_SIDES_FILLED_BEFORE_R1_CHECKPOINT"

    bad = replace(both, net_realized_pnl_usdt=Decimal("1"))
    assert bad.safety_failure(spec) == "NET_ACCOUNTING_MISMATCH"


def test_deadline_flatten_is_single_flight_and_halts(tmp_path: Path) -> None:
    spec, _, controller = _armed(tmp_path)
    position = make_fixture_observation(
        spec,
        sequence=3,
        observed_at_ms=3_000,
        market_timestamp_ms=3_000,
        position_btc="-0.01",
        average_entry_usdt="50000",
    )
    actions = controller.observe(position, now_ms=controller.state.deadline_at_ms)
    assert [item.action for item in actions] == [
        FormalActionType.FLATTEN_REDUCE_ONLY,
        FormalActionType.HALT,
    ]
    assert actions[0].payload["side"] == "buy"
    assert controller.state.flatten_submission_count == 1


def test_state_store_detects_truncation_and_hash_break(tmp_path: Path) -> None:
    spec, store, controller = _armed(tmp_path)
    loaded = store.load(spec)
    assert loaded is not None and loaded.stage is FormalStage.SEEK_R1_FILL
    raw = store.journal_path.read_text(encoding="utf-8")
    store.journal_path.write_text(raw.rstrip("\n"), encoding="utf-8")
    with pytest.raises(FormalPersistenceError, match="truncated"):
        store.load(spec)

    store2 = FormalStateStore(tmp_path / "broken.json")
    FormalOrchestrator.prepare(spec, store2)
    content = store2.journal_path.read_text(encoding="utf-8")
    store2.journal_path.write_text(
        content.replace('"record_hash":"', '"record_hash":"f', 1),
        encoding="utf-8",
    )
    with pytest.raises(FormalPersistenceError, match="record hash"):
        store2.load(spec)
