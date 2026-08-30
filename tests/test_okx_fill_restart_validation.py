import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

import okx_fill_restart_validation as validation
from okx_fill_restart_validation import (
    AuthoritativeSnapshot,
    FillDedupCursor,
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    PersistenceFailure,
    RiskBudget,
    RunPhase,
    RuntimeBinding,
    ValidationSafetyError,
    make_fixture_binding,
    make_snapshot,
)


def _order(
    side: str,
    *,
    suffix: str = "1",
    quantity: str = "0.01",
    reduce_only: bool = False,
) -> OwnedOrder:
    return OwnedOrder(
        client_order_id=f"client-{side}-{suffix}",
        order_id=f"order-{side}-{suffix}",
        side=side,
        quantity_btc=Decimal(quantity),
        remaining_btc=Decimal(quantity),
        reduce_only=reduce_only,
        post_only_acknowledged=not reduce_only,
    )


def _fill(
    order: OwnedOrder,
    *,
    trade_id: str,
    timestamp_ms: int,
    price: str,
    quantity: str | None = None,
    fee: str = "0.10",
    fee_currency: str = "USDT",
    liquidity: str | None = None,
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "timestamp_ms": timestamp_ms,
        "side": order.side,
        "price": price,
        "quantity_btc": quantity or str(order.quantity_btc),
        "fee_cost": fee,
        "fee_currency": fee_currency,
        "liquidity": liquidity or ("taker" if order.reduce_only else "maker"),
        "reduce_only": order.reduce_only,
    }


def _pages(*fills: dict[str, object]) -> list[dict[str, object]]:
    return [{
        "page_index": 0,
        "cursor": "",
        "next_cursor": "",
        "trades": list(fills),
    }]


def _open(order: OwnedOrder) -> dict[str, str]:
    return {
        "client_order_id": order.client_order_id,
        "order_id": order.order_id,
        "side": order.side,
    }


def _new_engine(tmp_path: Path, run_id: str = "offline-run") -> FillRestartEngine:
    return FillRestartEngine.create(
        store=HashChainStateStore(tmp_path / f"{run_id}.json"),
        binding=make_fixture_binding(run_id),
    )


def _snapshot_pair(
    engine: FillRestartEngine,
    *,
    position: str,
    average: str,
    fees: str,
) -> tuple[AuthoritativeSnapshot, AuthoritativeSnapshot]:
    return (
        make_snapshot(
            engine.state.binding,
            sequence=100,
            position_btc=position,
            average_entry_price=average,
            run_fees_usdt=fees,
        ),
        make_snapshot(
            engine.state.binding,
            sequence=101,
            position_btc=position,
            average_entry_price=average,
            run_fees_usdt=fees,
        ),
    )


def _complete_r1(
    tmp_path: Path,
    *,
    entry_side: str = "buy",
    run_id: str = "r1-fixture",
) -> FillRestartEngine:
    engine = _new_engine(tmp_path, run_id)
    entry = _order(entry_side)
    entry_price = "50000"
    position = "0.01" if entry_side == "buy" else "-0.01"
    engine.record_owned_order_ack(entry)
    engine.ingest_fill_pages(_pages(_fill(
        entry,
        trade_id="entry-fill",
        timestamp_ms=1000,
        price=entry_price,
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc=position,
        average_entry_price=entry_price,
        run_fees_usdt="0.10",
    ))
    directive = engine.checkpoint_after_actual_fill()
    return FillRestartEngine.resume(
        store=engine.store,
        expected_binding=engine.state.binding,
        resume_token=directive.resume_token,
        market_metadata_loaded=True,
        snapshots=_snapshot_pair(
            engine, position=position, average=entry_price, fees="0.10"
        ),
    )


def test_live_mode_and_wider_risk_budget_are_structurally_rejected() -> None:
    fixture = make_fixture_binding()
    value = fixture.to_dict()
    value["execution_mode"] = "LIVE"
    with pytest.raises(ValidationSafetyError, match="LIVE"):
        RuntimeBinding.from_dict(value)
    with pytest.raises(ValidationSafetyError, match="inventory"):
        RiskBudget(maximum_inventory_btc=Decimal("0.02")).validate()
    with pytest.raises(ValidationSafetyError, match="leverage"):
        RiskBudget(leverage=4).validate()


def test_binding_rejects_profile_spec_symbol_and_source_drift() -> None:
    original = make_fixture_binding().to_dict()
    cases = {
        "profile_id": "other",
        "profile_name": "other",
        "symbol": "ETH/USDT:USDT",
        "v16_specification_sha256": "d" * 64,
        "prior_demo_specification_sha256": "e" * 64,
        "source_manifest_sha256": "not-a-hash",
    }
    for field, replacement in cases.items():
        value = dict(original)
        value[field] = replacement
        with pytest.raises(ValidationSafetyError):
            RuntimeBinding.from_dict(value)


def test_paginated_same_timestamp_reordered_duplicate_and_late_fills() -> None:
    bid = _order("buy")
    a = _fill(bid, trade_id="a", timestamp_ms=100, price="50000", quantity="0.002")
    b = _fill(bid, trade_id="b", timestamp_ms=100, price="50000", quantity="0.002")
    c = _fill(bid, trade_id="c", timestamp_ms=101, price="50000", quantity="0.002")
    cursor = FillDedupCursor()
    selected = cursor.select_new_pages([
        {"page_index": 0, "cursor": "", "next_cursor": "next", "trades": [c, b]},
        {"page_index": 1, "cursor": "next", "next_cursor": "", "trades": [a, b]},
    ])
    assert [row.trade_id for row in selected] == ["a", "b", "c"]
    assert cursor.watermark_ms == 101
    assert cursor.select_new_pages(_pages(c, b, a)) == []
    late = _fill(bid, trade_id="late", timestamp_ms=99, price="50000", quantity="0.002")
    assert [row.trade_id for row in cursor.select_new_pages(_pages(late))] == ["late"]
    assert cursor.watermark_ms == 101
    assert cursor.late_fill_count == 1


def test_fill_cursor_rejects_broken_page_chain_and_conflicting_duplicate() -> None:
    order = _order("buy")
    fill = _fill(order, trade_id="same", timestamp_ms=100, price="50000")
    cursor = FillDedupCursor()
    with pytest.raises(ValidationSafetyError, match="cursor"):
        cursor.select_new_pages([
            {"page_index": 0, "cursor": "wrong", "next_cursor": "", "trades": []}
        ])
    cursor.select_new_pages(_pages(fill))
    changed = dict(fill)
    changed["price"] = "50001"
    with pytest.raises(ValidationSafetyError, match="identity changed"):
        cursor.select_new_pages(_pages(changed))


@pytest.mark.parametrize("entry_side", ["buy", "sell"])
def test_r1_restart_restores_real_fill_position_fees_and_generation(
    tmp_path: Path, entry_side: str
) -> None:
    engine = _complete_r1(tmp_path, entry_side=entry_side, run_id=f"r1-{entry_side}")
    expected = Decimal("0.01") if entry_side == "buy" else Decimal("-0.01")
    assert engine.state.phase is RunPhase.RUNNING_AFTER_R1
    assert engine.state.r1_completed is True
    assert engine.state.process_generation == 1
    assert engine.state.ledger.inventory_btc == expected
    assert engine.state.ledger.total_fees_usdt == Decimal("0.10")
    assert engine.state.can_submit is True
    assert engine.state.external_network_attempts == 0
    assert engine.state.external_order_submissions == 0


def test_full_r1_r2_kill_restart_round_trip_and_safe_release(tmp_path: Path) -> None:
    engine = _complete_r1(tmp_path, run_id="full-r1-r2")
    exit_order = _order("sell", suffix="2")
    engine.record_owned_order_ack(exit_order)
    engine.ingest_fill_pages(_pages(_fill(
        exit_order,
        trade_id="exit-fill",
        timestamp_ms=2000,
        price="50010",
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=10,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.20",
    ))
    assert len(engine.state.ledger.normal_round_trips) == 1
    assert engine.state.ledger.gross_realized_pnl_usdt == Decimal("0.10")
    assert engine.state.ledger.net_realized_pnl_usdt == Decimal("-0.10")

    directive = engine.checkpoint_kill_latch()
    activation_id = engine.state.kill_latch.activation_id
    engine = FillRestartEngine.resume(
        store=engine.store,
        expected_binding=engine.state.binding,
        resume_token=directive.resume_token,
        market_metadata_loaded=True,
        snapshots=_snapshot_pair(engine, position="0", average="0", fees="0.20"),
    )
    assert engine.state.phase is RunPhase.KILL_LATCH_BLOCKED
    assert engine.state.kill_latch.active is True
    assert engine.state.can_submit is False
    with pytest.raises(ValidationSafetyError, match="acknowledgement"):
        engine.release_kill_latch(
            acknowledgement_id="wrong",
            snapshots=_snapshot_pair(engine, position="0", average="0", fees="0.20"),
        )
    engine.release_kill_latch(
        acknowledgement_id=activation_id,
        snapshots=_snapshot_pair(engine, position="0", average="0", fees="0.20"),
    )
    assert engine.state.r2_completed is True
    assert engine.state.kill_latch.active is False
    assert engine.state.can_submit is True
    assert engine.state.external_network_attempts == 0
    assert engine.state.external_preflight_attempts == 0
    assert engine.state.external_order_submissions == 0


def test_partial_then_late_fill_after_confirmed_cancel_reconciles(tmp_path: Path) -> None:
    engine = _new_engine(tmp_path, "partial-late")
    order = _order("buy")
    engine.record_owned_order_ack(order)
    engine.ingest_fill_pages(_pages(_fill(
        order, trade_id="partial", timestamp_ms=200, price="50000", quantity="0.005", fee="0.05"
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc="0.005",
        average_entry_price="50000",
        run_fees_usdt="0.05",
        open_orders=(_open(order),),
    ))
    engine.confirm_cancellations(
        confirmed_client_ids=(order.client_order_id,),
        snapshot=make_snapshot(
            engine.state.binding,
            sequence=2,
            position_btc="0.005",
            average_entry_price="50000",
            run_fees_usdt="0.05",
        ),
    )
    engine.ingest_fill_pages(_pages(_fill(
        order, trade_id="late", timestamp_ms=199, price="50000", quantity="0.005", fee="0.05"
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=3,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    ))
    assert engine.state.fill_cursor.late_fill_count == 1
    assert engine.state.ledger.inventory_btc == Decimal("0.01")
    assert engine.state.owned_orders[order.client_order_id].status == "FILLED"


def test_trade_before_position_lag_blocks_then_reconciles(tmp_path: Path) -> None:
    engine = _new_engine(tmp_path, "trade-first")
    order = _order("buy")
    engine.record_owned_order_ack(order)
    engine.ingest_fill_pages(_pages(_fill(
        order, trade_id="fill", timestamp_ms=100, price="50000"
    )))
    accepted = engine.record_authoritative_snapshot(
        make_snapshot(
            engine.state.binding,
            sequence=1,
            position_btc="0",
            average_entry_price="0",
            run_fees_usdt="0.10",
        ),
        allow_position_lag=True,
    )
    assert accepted is False
    assert engine.state.can_submit is False
    assert engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=2,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    )) is True


def test_position_before_trade_blocks_then_reconciles(tmp_path: Path) -> None:
    engine = _new_engine(tmp_path, "position-first")
    order = _order("buy")
    engine.record_owned_order_ack(order)
    engine.observe_position_before_trade(make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0",
        trades_complete=False,
    ))
    assert engine.state.can_submit is False
    engine.ingest_fill_pages(_pages(_fill(
        order, trade_id="fill", timestamp_ms=100, price="50000"
    )))
    assert engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=2,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    )) is True


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({"liquidity": "taker"}, "maker"),
        ({"fee_currency": "EUR"}, "fee currency"),
        ({"fee_cost": "1000"}, "fee"),
        ({"order_id": "foreign"}, "owned order"),
    ],
)
def test_maker_fee_and_order_mismatch_fail_closed_with_zero_submissions(
    tmp_path: Path, mutation: dict[str, object], match: str
) -> None:
    engine = _new_engine(tmp_path, "bad-" + next(iter(mutation)))
    order = _order("buy")
    engine.record_owned_order_ack(order)
    fill = _fill(order, trade_id="bad", timestamp_ms=100, price="50000")
    fill.update(mutation)
    with pytest.raises(ValidationSafetyError, match="FILL_RECONCILIATION_FAILED") as caught:
        engine.ingest_fill_pages(_pages(fill))
    assert caught.value.__cause__ is not None
    assert match in str(caught.value.__cause__)
    assert engine.state.phase is RunPhase.HALTED
    assert engine.state.can_submit is False
    assert engine.state.external_order_submissions == 0


@pytest.mark.parametrize(
    "flag",
    [
        "orders_complete", "trades_complete", "position_complete",
        "balance_complete", "fee_complete",
    ],
)
def test_every_incomplete_snapshot_halts_with_zero_submissions(
    tmp_path: Path, flag: str
) -> None:
    engine = _new_engine(tmp_path, "incomplete-" + flag)
    snapshot = make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc="0",
        **{flag: False},
    )
    with pytest.raises(ValidationSafetyError, match="SNAPSHOT"):
        engine.record_authoritative_snapshot(snapshot)
    assert engine.state.can_submit is False
    assert engine.state.external_order_submissions == 0


def test_foreign_duplicate_and_missing_orders_fail_closed(tmp_path: Path) -> None:
    foreign = _new_engine(tmp_path, "foreign")
    with pytest.raises(ValidationSafetyError):
        foreign.record_authoritative_snapshot(make_snapshot(
            foreign.state.binding,
            sequence=1,
            position_btc="0",
            open_orders=({
                "client_order_id": "foreign", "order_id": "x", "side": "buy"
            },),
        ))
    assert foreign.state.external_order_submissions == 0

    duplicate = _new_engine(tmp_path, "duplicate")
    bid = _order("buy", suffix="a")
    duplicate.record_owned_order_ack(bid)
    with pytest.raises(ValidationSafetyError, match="ORDER_ACK_REJECTED"):
        duplicate.record_owned_order_ack(_order("buy", suffix="b"))
    assert duplicate.state.can_submit is False

    missing = _new_engine(tmp_path, "missing")
    order = _order("sell")
    missing.record_owned_order_ack(order)
    with pytest.raises(ValidationSafetyError):
        missing.record_authoritative_snapshot(make_snapshot(
            missing.state.binding, sequence=1, position_btc="0"
        ))
    assert missing.state.can_submit is False


def test_restart_requires_metadata_exact_pair_and_correct_token(tmp_path: Path) -> None:
    engine = _new_engine(tmp_path, "restart-guards")
    order = _order("buy")
    engine.record_owned_order_ack(order)
    engine.ingest_fill_pages(_pages(_fill(
        order, trade_id="fill", timestamp_ms=1, price="50000"
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    ))
    directive = engine.checkpoint_after_actual_fill()
    pair = _snapshot_pair(engine, position="0.01", average="50000", fees="0.10")
    with pytest.raises(ValidationSafetyError, match="metadata"):
        FillRestartEngine.resume(
            store=engine.store,
            expected_binding=engine.state.binding,
            resume_token=directive.resume_token,
            market_metadata_loaded=False,
            snapshots=pair,
        )
    with pytest.raises(ValidationSafetyError, match="token"):
        FillRestartEngine.resume(
            store=engine.store,
            expected_binding=engine.state.binding,
            resume_token="wrong",
            market_metadata_loaded=True,
            snapshots=pair,
        )
    inconsistent = (pair[0], make_snapshot(
        engine.state.binding,
        sequence=102,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.10",
    ))
    with pytest.raises(ValidationSafetyError, match="position"):
        FillRestartEngine.resume(
            store=engine.store,
            expected_binding=engine.state.binding,
            resume_token=directive.resume_token,
            market_metadata_loaded=True,
            snapshots=inconsistent,
        )


def test_resume_token_is_single_use_and_binding_drift_fails(tmp_path: Path) -> None:
    original = _new_engine(tmp_path, "token-single-use")
    order = _order("buy")
    original.record_owned_order_ack(order)
    original.ingest_fill_pages(_pages(_fill(
        order, trade_id="fill", timestamp_ms=1, price="50000"
    )))
    original.record_authoritative_snapshot(make_snapshot(
        original.state.binding,
        sequence=1,
        position_btc="0.01",
        average_entry_price="50000",
        run_fees_usdt="0.10",
    ))
    directive = original.checkpoint_after_actual_fill()
    pair = _snapshot_pair(original, position="0.01", average="50000", fees="0.10")
    resumed = FillRestartEngine.resume(
        store=original.store,
        expected_binding=original.state.binding,
        resume_token=directive.resume_token,
        market_metadata_loaded=True,
        snapshots=pair,
    )
    with pytest.raises(ValidationSafetyError, match="already used"):
        FillRestartEngine.resume(
            store=resumed.store,
            expected_binding=resumed.state.binding,
            resume_token=directive.resume_token,
            market_metadata_loaded=True,
            snapshots=pair,
        )
    drift = resumed.state.binding.to_dict()
    drift["market_fingerprint"] = "wrong-market"
    with pytest.raises(ValidationSafetyError, match="binding drift"):
        resumed.store.load(expected_binding=RuntimeBinding.from_dict(drift))


def test_checkpoint_refuses_live_owned_orders_or_flat_fill(tmp_path: Path) -> None:
    engine = _new_engine(tmp_path, "checkpoint-guards")
    order = _order("buy", quantity="0.005")
    engine.record_owned_order_ack(order)
    engine.ingest_fill_pages(_pages(_fill(
        order,
        trade_id="partial",
        timestamp_ms=1,
        price="50000",
        quantity="0.002",
        fee="0.02",
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=1,
        position_btc="0.002",
        average_entry_price="50000",
        run_fees_usdt="0.02",
        open_orders=(_open(order),),
    ))
    with pytest.raises(ValidationSafetyError, match="zero owned"):
        engine.checkpoint_after_actual_fill()


def test_special_reduce_only_fill_is_accounted_but_not_normal_fifo(tmp_path: Path) -> None:
    engine = _complete_r1(tmp_path, run_id="special-fill")
    special = _order("sell", suffix="flatten", reduce_only=True)
    engine.record_special_order_ack(special)
    engine.ingest_fill_pages(_pages(_fill(
        special,
        trade_id="special",
        timestamp_ms=2000,
        price="49990",
        fee="0.25",
    )))
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=10,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.35",
    ))
    assert engine.state.ledger.special_fill_count == 1
    assert engine.state.ledger.normal_ask_fills == 0
    assert engine.state.ledger.normal_round_trips == []
    assert engine.state.ledger.normal_fifo_lots == []
    assert engine.state.ledger.normal_gross_realized_pnl_usdt == Decimal("0")
    assert engine.state.ledger.normal_fees_usdt == Decimal("0.10")
    assert engine.state.ledger.normal_net_realized_pnl_usdt == Decimal("-0.10")
    assert engine.state.ledger.special_gross_realized_pnl_usdt == Decimal("-0.10")
    assert engine.state.ledger.special_fees_usdt == Decimal("0.25")
    assert engine.state.ledger.special_net_realized_pnl_usdt == Decimal("-0.35")
    assert engine.state.ledger.gross_realized_pnl_usdt == Decimal("-0.10")
    assert engine.state.ledger.total_fees_usdt == Decimal("0.35")
    assert engine.state.ledger.net_realized_pnl_usdt == Decimal("-0.45")
    restored = type(engine.state.ledger).from_dict(engine.state.ledger.to_dict())
    assert restored.to_dict() == engine.state.ledger.to_dict()


def test_one_flatten_order_accepts_many_partial_fills_without_resubmission(
    tmp_path: Path,
) -> None:
    engine = _complete_r1(tmp_path, run_id="multi-partial-flatten")
    special = _order("sell", suffix="flatten-parts", reduce_only=True)
    engine.record_special_order_ack(special)
    parts = tuple(
        _fill(
            special,
            trade_id=f"special-part-{index:02d}",
            timestamp_ms=2_000 + index,
            price="49990",
            quantity="0.001",
            fee="0.025",
        )
        for index in range(10)
    )

    assert engine.ingest_fill_pages(_pages(*parts)) == 10
    engine.record_authoritative_snapshot(make_snapshot(
        engine.state.binding,
        sequence=10,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.35",
    ))

    ledger = engine.state.ledger
    assert ledger.inventory_btc == 0
    assert ledger.normal_bid_fills == 1
    assert ledger.normal_ask_fills == 0
    assert ledger.special_fill_count == 10
    assert ledger.normal_round_trips == []
    assert ledger.normal_fifo_lots == []
    assert ledger.normal_fees_usdt == Decimal("0.10")
    assert ledger.special_fees_usdt == Decimal("0.250")
    assert ledger.total_fees_usdt == Decimal("0.350")
    assert ledger.special_gross_realized_pnl_usdt == Decimal("-0.100")
    assert engine.state.owned_orders[special.client_order_id].status == "FILLED"
    assert engine.state.owned_orders[special.client_order_id].remaining_btc == 0


def test_hash_chain_round_trip_and_journal_only_recovery(tmp_path: Path) -> None:
    store = HashChainStateStore(tmp_path / "state.json")
    engine = FillRestartEngine.create(store=store, binding=make_fixture_binding("recover"))
    engine.record_owned_order_ack(_order("buy"))
    store.state_path.unlink()
    with pytest.raises(PersistenceFailure, match="missing"):
        store.load()
    recovered = store.load(allow_journal_recovery=True)
    assert recovered is not None
    assert recovered.binding.run_id == "recover"
    store.recover_snapshot_from_journal()
    assert store.load() is not None


@pytest.mark.parametrize("damage", ["truncate", "hash", "snapshot"])
def test_corrupt_truncated_and_hash_broken_state_fail_closed(
    tmp_path: Path, damage: str
) -> None:
    store = HashChainStateStore(tmp_path / f"{damage}.json")
    FillRestartEngine.create(store=store, binding=make_fixture_binding(damage))
    if damage == "truncate":
        raw = store.journal_path.read_text(encoding="utf-8")
        store.journal_path.write_text(raw[:-1], encoding="utf-8")
    elif damage == "hash":
        rows = store.journal_path.read_text(encoding="utf-8").splitlines()
        value = json.loads(rows[0])
        value["previous_hash"] = "bad"
        store.journal_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    else:
        value = json.loads(store.state_path.read_text(encoding="utf-8"))
        value["record_hash"] = "bad"
        store.state_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(PersistenceFailure):
        store.load()


def test_snapshot_replace_failure_is_durable_but_runtime_halts(
    tmp_path: Path, monkeypatch
) -> None:
    store = HashChainStateStore(tmp_path / "state.json")
    engine = FillRestartEngine.create(store=store, binding=make_fixture_binding("persist"))
    original_replace = os.replace

    def fail_replace(source, target):
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(validation.os, "replace", fail_replace)
    with pytest.raises(PersistenceFailure):
        engine.record_owned_order_ack(_order("buy"))
    assert engine.state.phase is RunPhase.HALTED
    assert engine.state.can_submit is False
    assert engine.state.external_order_submissions == 0
    monkeypatch.setattr(validation.os, "replace", original_replace)
    recovered = store.load(allow_journal_recovery=True)
    assert recovered is not None
    assert recovered.owned_orders["client-buy-1"].order_id == "order-buy-1"
    assert recovered.phase is RunPhase.HALTED
    assert recovered.can_submit is False


def test_run_id_reuse_is_refused(tmp_path: Path) -> None:
    store = HashChainStateStore(tmp_path / "state.json")
    FillRestartEngine.create(store=store, binding=make_fixture_binding("same"))
    with pytest.raises(PersistenceFailure, match="reuse"):
        FillRestartEngine.create(store=store, binding=make_fixture_binding("same"))
