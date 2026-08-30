from decimal import Decimal

import pytest

from okx_execution_safety import (
    CancelOutcome,
    ExchangeOrder,
    KillSwitchLatch,
    OrderIntent,
    SafetyContractError,
    evaluate_startup_reconciliation,
    replacement_is_safe,
)


def _intent(generation: int = 7) -> OrderIntent:
    return OrderIntent(
        session_id="demo-session",
        generation=generation,
        symbol="BTC/USDT:USDT",
        side="buy",
        price=Decimal("50000.0"),
        contracts=Decimal("1"),
    )


def test_client_order_identity_is_deterministic_and_generation_scoped() -> None:
    first = _intent()
    assert first.client_order_id == _intent().client_order_id
    assert first.client_order_id != _intent(8).client_order_id
    assert len(first.client_order_id) == 28
    assert "BTC" not in first.client_order_id
    assert "demo" not in first.client_order_id


@pytest.mark.parametrize("orders_ok,position_ok", [(False, True), (True, False)])
def test_incomplete_exchange_snapshot_fails_closed(orders_ok: bool, position_ok: bool) -> None:
    result = evaluate_startup_reconciliation(
        expected_client_order_ids=(),
        exchange_orders=(),
        local_position_base=Decimal("0"),
        exchange_position_base=Decimal("0"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=orders_ok,
        position_fetch_ok=position_ok,
    )
    assert result.allow_quoting is False
    assert result.reason == "exchange_snapshot_incomplete"


def test_unowned_exchange_order_blocks_quoting() -> None:
    result = evaluate_startup_reconciliation(
        expected_client_order_ids=(),
        exchange_orders=(ExchangeOrder("exchange-1", "foreign", "buy"),),
        local_position_base=Decimal("0"),
        exchange_position_base=Decimal("0"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=True,
        position_fetch_ok=True,
    )
    assert result.allow_quoting is False
    assert result.foreign_order_ids == ("exchange-1",)


def test_missing_expected_order_requires_trade_reconciliation() -> None:
    result = evaluate_startup_reconciliation(
        expected_client_order_ids=("expected-1",),
        exchange_orders=(),
        local_position_base=Decimal("0"),
        exchange_position_base=Decimal("0"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=True,
        position_fetch_ok=True,
    )
    assert result.allow_quoting is False
    assert result.missing_expected_client_ids == ("expected-1",)


def test_reconciled_snapshot_allows_quoting() -> None:
    result = evaluate_startup_reconciliation(
        expected_client_order_ids=("owned-bid",),
        exchange_orders=(ExchangeOrder("order-1", "owned-bid", "buy"),),
        local_position_base=Decimal("0.01"),
        exchange_position_base=Decimal("0.01"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=True,
        position_fetch_ok=True,
    )
    assert result.allow_quoting is True
    assert result.reason == "reconciled"


def test_position_reconciliation_one_base_step_boundary_is_closed() -> None:
    at_boundary = evaluate_startup_reconciliation(
        expected_client_order_ids=(),
        exchange_orders=(),
        local_position_base=Decimal("0"),
        exchange_position_base=Decimal("0.01"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=True,
        position_fetch_ok=True,
    )
    over_boundary = evaluate_startup_reconciliation(
        expected_client_order_ids=(),
        exchange_orders=(),
        local_position_base=Decimal("0"),
        exchange_position_base=Decimal("0.01000001"),
        base_step=Decimal("0.01"),
        orders_fetch_ok=True,
        position_fetch_ok=True,
    )
    assert at_boundary.allow_quoting is True
    assert over_boundary.allow_quoting is False
    assert over_boundary.reason == "position_mismatch_exceeds_base_step"


def test_replacement_requires_confirmed_cancellation() -> None:
    assert replacement_is_safe(CancelOutcome.CONFIRMED_GONE) is True
    assert replacement_is_safe(CancelOutcome.STILL_OPEN) is False
    assert replacement_is_safe(CancelOutcome.UNKNOWN) is False


def test_kill_switch_round_trip_and_release_guards() -> None:
    latch = KillSwitchLatch()
    activation_id = latch.activate("drawdown", now=123.0)
    restored = KillSwitchLatch.from_dict(latch.to_dict())
    assert restored.active is True
    with pytest.raises(SafetyContractError, match="acknowledgement"):
        restored.release(
            acknowledgement_id="wrong",
            exchange_position_base=Decimal("0"),
            base_step=Decimal("0.01"),
            exchange_open_order_count=0,
        )
    with pytest.raises(SafetyContractError, match="open position"):
        restored.release(
            acknowledgement_id=activation_id,
            exchange_position_base=Decimal("0.02"),
            base_step=Decimal("0.01"),
            exchange_open_order_count=0,
        )
    restored.release(
        acknowledgement_id=activation_id,
        exchange_position_base=Decimal("0.00"),
        base_step=Decimal("0.01"),
        exchange_open_order_count=0,
    )
    assert restored.active is False
