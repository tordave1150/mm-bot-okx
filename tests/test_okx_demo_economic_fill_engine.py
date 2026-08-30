from decimal import Decimal
from pathlib import Path

from okx_demo_economic_fill_engine import EconomicSessionEngine
from okx_fill_restart_validation import (
    FillRestartEngine,
    HashChainStateStore,
    OwnedOrder,
    make_fixture_binding,
    make_snapshot,
)


def _order(side: str, suffix: str) -> OwnedOrder:
    return OwnedOrder(
        client_order_id=f"client-{side}-{suffix}",
        order_id=f"order-{side}-{suffix}",
        side=side,
        quantity_btc=Decimal("0.01"),
        remaining_btc=Decimal("0.01"),
        reduce_only=False,
        post_only_acknowledged=True,
    )


def _pages(order: OwnedOrder) -> list[dict[str, object]]:
    return [{
        "page_index": 0,
        "cursor": "",
        "next_cursor": "",
        "trades": [{
            "trade_id": f"trade-{order.side}",
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "timestamp_ms": 1,
            "side": order.side,
            "price": "65000",
            "quantity_btc": "0.01",
            "fee_cost": "0.001",
            "fee_currency": "USDT",
            "liquidity": "maker",
            "reduce_only": False,
        }],
    }]


def _reconciled_snapshot(engine: FillRestartEngine):
    return make_snapshot(
        engine.state.binding,
        sequence=2,
        position_btc="0.01",
        average_entry_price="65000",
        run_fees_usdt="0.001",
    )


def test_formal_engine_keeps_r1_latch_while_economic_engine_releases_only_after_reconciliation(
    tmp_path: Path,
) -> None:
    formal = FillRestartEngine.create(
        store=HashChainStateStore(tmp_path / "formal.json"),
        binding=make_fixture_binding("formal-fixture"),
    )
    formal_buy = _order("buy", "formal")
    formal.record_owned_order_ack(formal_buy)
    formal.ingest_fill_pages(_pages(formal_buy))
    formal.confirm_cancellations(
        confirmed_client_ids=(formal_buy.client_order_id,),
        snapshot=_reconciled_snapshot(formal),
    )
    assert formal.state.placement_halted_for_fill is True
    assert formal.state.can_submit is False

    economic = EconomicSessionEngine.create(
        store=HashChainStateStore(tmp_path / "economic.json"),
        binding=make_fixture_binding("economic-fixture"),
    )
    economic_buy = _order("buy", "economic")
    economic.record_owned_order_ack(economic_buy)
    economic.ingest_fill_pages(_pages(economic_buy))
    assert economic.state.can_submit is False
    economic.confirm_cancellations(
        confirmed_client_ids=(economic_buy.client_order_id,),
        snapshot=_reconciled_snapshot(economic),
    )
    assert economic.state.placement_halted_for_fill is False
    assert economic.state.can_submit is True
    economic.record_owned_order_ack(_order("sell", "reentry"))
    assert economic.state.normal_acknowledgements == 2


def test_two_filled_orders_release_latch_only_after_exact_flat_snapshot(
    tmp_path: Path,
) -> None:
    engine = EconomicSessionEngine.create(
        store=HashChainStateStore(tmp_path / "two-sided.json"),
        binding=make_fixture_binding("two-sided-economic-fixture"),
    )
    buy = _order("buy", "two-sided")
    sell = _order("sell", "two-sided")
    engine.record_owned_order_ack(buy)
    engine.record_owned_order_ack(sell)
    rows = _pages(buy)[0]["trades"] + _pages(sell)[0]["trades"]
    rows[1] = {**rows[1], "timestamp_ms": 2, "price": "65001"}
    engine.ingest_fill_pages([{
        "page_index": 0,
        "cursor": "",
        "next_cursor": "",
        "trades": rows,
    }])
    assert engine.state.ledger.inventory_btc == 0
    assert not engine.state.open_orders
    assert engine.state.can_submit is False
    engine.confirm_fill_reconciliation(snapshot=make_snapshot(
        engine.state.binding,
        sequence=3,
        position_btc="0",
        average_entry_price="0",
        run_fees_usdt="0.002",
    ))
    assert engine.state.placement_halted_for_fill is False
    assert engine.state.can_submit is True
