"""Explicit, unit-safe margin components and pre-quote admission."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Literal


UNIT_CONTRACT = {
    "instrument": "synthetic BTC/USDT linear perpetual research fixture",
    "price_unit": "USDT per BTC",
    "quantity_input_unit": "BTC base quantity",
    "internal_quantity_unit": "BTC base quantity",
    "lot_size_btc": 0.01,
    "contract_multiplier_btc": 1.0,
    "exchange_boundary_contract_size_btc": 0.01,
    "minimum_quantity_btc": 0.01,
    "quantity_step_btc": 0.01,
    "notional_unit": "USDT",
    "equity_unit": "USDT",
    "margin_unit": "USDT",
    "leverage_unit": "dimensionless ratio",
    "fee_unit": "USDT",
    "inventory_unit": "BTC base quantity",
    "base_quantity_identity": (
        "base_quantity_btc = order_quantity_input * contract_multiplier_btc"
    ),
    "notional_identity": (
        "order_notional_usdt = base_quantity_btc * order_price_usdt_per_btc"
    ),
    "initial_margin_identity": (
        "initial_margin_usdt = order_notional_usdt / leverage"
    ),
    "conversion_boundary": (
        "offline quantities remain BTC; MarketSpec.base_to_contracts converts "
        "0.01 BTC to one 0.01-BTC linear contract exactly once"
    ),
}


@dataclass(frozen=True)
class OrderExposure:
    side: Literal["buy", "sell"]
    price_usdt_per_btc: float
    quantity_btc: float
    order_id: str | None = None
    reduce_only: bool = False

    @property
    def notional_usdt(self) -> float:
        return self.price_usdt_per_btc * self.quantity_btc


@dataclass(frozen=True)
class MarginComponents:
    current_equity_usdt: float
    leverage: float
    position_margin_usdt: float
    active_bid_order_reserve_usdt: float
    active_ask_order_reserve_usdt: float
    proposed_bid_order_reserve_usdt: float
    proposed_ask_order_reserve_usdt: float
    fee_reserve_usdt: float
    liquidation_reserve_usdt: float
    emergency_exit_reserve_usdt: float
    other_buffer_usdt: float
    reserve_model: str = "GROSS_CONSERVATIVE"

    @property
    def pending_order_reserve_usdt(self) -> float:
        return (
            self.active_bid_order_reserve_usdt
            + self.active_ask_order_reserve_usdt
            + self.proposed_bid_order_reserve_usdt
            + self.proposed_ask_order_reserve_usdt
        )

    @property
    def total_modeled_margin_usdt(self) -> float:
        return (
            self.position_margin_usdt
            + self.pending_order_reserve_usdt
            + self.fee_reserve_usdt
            + self.liquidation_reserve_usdt
            + self.emergency_exit_reserve_usdt
            + self.other_buffer_usdt
        )

    @property
    def margin_utilization(self) -> float:
        if self.current_equity_usdt <= 0:
            return math.inf
        return self.total_modeled_margin_usdt / self.current_equity_usdt

    def to_dict(self) -> dict[str, float | str]:
        payload = asdict(self)
        payload["pending_order_reserve_usdt"] = self.pending_order_reserve_usdt
        payload["total_modeled_margin_usdt"] = self.total_modeled_margin_usdt
        payload["margin_utilization"] = self.margin_utilization
        return payload


@dataclass(frozen=True)
class SideAdmission:
    side: Literal["buy", "sell"]
    allowed: bool
    reason: str
    projected_utilization: float
    inventory_impact: str
    components: MarginComponents


def _validate_inputs(
    *,
    inventory_btc: float,
    mid_price_usdt_per_btc: float,
    current_equity_usdt: float,
    leverage: float,
    exposures: Iterable[OrderExposure],
) -> list[OrderExposure]:
    values = (
        inventory_btc, mid_price_usdt_per_btc, current_equity_usdt, leverage
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("margin state must be finite")
    if mid_price_usdt_per_btc <= 0 or leverage <= 0:
        raise ValueError("mid price and leverage must be positive")
    normalized = list(exposures)
    for exposure in normalized:
        if (
            exposure.side not in {"buy", "sell"}
            or not math.isfinite(exposure.price_usdt_per_btc)
            or not math.isfinite(exposure.quantity_btc)
            or exposure.price_usdt_per_btc <= 0
            or exposure.quantity_btc < 0
        ):
            raise ValueError("invalid order exposure")
    return normalized


def margin_components(
    *,
    inventory_btc: float,
    mid_price_usdt_per_btc: float,
    current_equity_usdt: float,
    leverage: float,
    active_orders: Iterable[OrderExposure] = (),
    proposed_orders: Iterable[OrderExposure] = (),
    maker_fee_rate: float = 0.0,
    liquidation_reserve_usdt: float = 0.0,
    emergency_exit_reserve_usdt: float = 0.0,
    other_buffer_usdt: float = 0.0,
    reserve_model: Literal["GROSS_CONSERVATIVE", "NETTED_DIAGNOSTIC"] = (
        "GROSS_CONSERVATIVE"
    ),
) -> MarginComponents:
    """Return individually reconciled position, order, fee, and buffer margin."""
    active = _validate_inputs(
        inventory_btc=inventory_btc,
        mid_price_usdt_per_btc=mid_price_usdt_per_btc,
        current_equity_usdt=current_equity_usdt,
        leverage=leverage,
        exposures=active_orders,
    )
    proposed = _validate_inputs(
        inventory_btc=inventory_btc,
        mid_price_usdt_per_btc=mid_price_usdt_per_btc,
        current_equity_usdt=current_equity_usdt,
        leverage=leverage,
        exposures=proposed_orders,
    )
    buffers = (
        maker_fee_rate,
        liquidation_reserve_usdt,
        emergency_exit_reserve_usdt,
        other_buffer_usdt,
    )
    if not all(math.isfinite(value) and value >= 0 for value in buffers):
        raise ValueError("fees and buffers must be finite and non-negative")

    def reserve(orders: list[OrderExposure], side: str) -> float:
        return sum(
            order.notional_usdt / leverage
            for order in orders if order.side == side and not order.reduce_only
        )

    active_bid = reserve(active, "buy")
    active_ask = reserve(active, "sell")
    proposed_bid = reserve(proposed, "buy")
    proposed_ask = reserve(proposed, "sell")
    if reserve_model == "NETTED_DIAGNOSTIC":
        bid_total = active_bid + proposed_bid
        ask_total = active_ask + proposed_ask
        if bid_total <= ask_total:
            active_bid = proposed_bid = 0.0
        else:
            active_ask = proposed_ask = 0.0
    elif reserve_model != "GROSS_CONSERVATIVE":
        raise ValueError("unknown reserve model")
    fee_reserve = sum(
        order.notional_usdt * maker_fee_rate
        for order in active + proposed if not order.reduce_only
    )
    components = MarginComponents(
        current_equity_usdt=current_equity_usdt,
        leverage=leverage,
        position_margin_usdt=(
            abs(inventory_btc) * mid_price_usdt_per_btc / leverage
        ),
        active_bid_order_reserve_usdt=active_bid,
        active_ask_order_reserve_usdt=active_ask,
        proposed_bid_order_reserve_usdt=proposed_bid,
        proposed_ask_order_reserve_usdt=proposed_ask,
        fee_reserve_usdt=fee_reserve,
        liquidation_reserve_usdt=liquidation_reserve_usdt,
        emergency_exit_reserve_usdt=emergency_exit_reserve_usdt,
        other_buffer_usdt=other_buffer_usdt,
        reserve_model=reserve_model,
    )
    numeric = [
        value for value in components.to_dict().values()
        if isinstance(value, (int, float))
    ]
    if current_equity_usdt > 0 and not all(
        math.isfinite(value) and value >= 0 for value in numeric
    ):
        raise ValueError("margin components must be finite and non-negative")
    return components


def _inventory_impact(side: str, inventory_btc: float) -> str:
    if inventory_btc > 0:
        return "REDUCING" if side == "sell" else "INCREASING"
    if inventory_btc < 0:
        return "REDUCING" if side == "buy" else "INCREASING"
    return "OPENING"


def admit_proposed_orders(
    *,
    inventory_btc: float,
    mid_price_usdt_per_btc: float,
    current_equity_usdt: float,
    leverage: float,
    maximum_margin_utilization: float,
    active_orders: Iterable[OrderExposure],
    proposed_orders: Iterable[OrderExposure],
    maker_fee_rate: float = 0.0,
) -> list[SideAdmission]:
    """Apply gross pre-quote admission and deterministic limited-margin policy."""
    proposals = list(proposed_orders)
    if not 0 < maximum_margin_utilization <= 1:
        raise ValueError("maximum margin utilization must be in (0, 1]")
    if current_equity_usdt <= 0:
        return [
            SideAdmission(
                order.side, False, "SUPPRESS_EQUITY_NONPOSITIVE", math.inf,
                _inventory_impact(order.side, inventory_btc),
                margin_components(
                    inventory_btc=inventory_btc,
                    mid_price_usdt_per_btc=mid_price_usdt_per_btc,
                    current_equity_usdt=current_equity_usdt,
                    leverage=leverage,
                ),
            )
            for order in proposals
        ]
    active = list(active_orders)
    try:
        combined = margin_components(
            inventory_btc=inventory_btc,
            mid_price_usdt_per_btc=mid_price_usdt_per_btc,
            current_equity_usdt=current_equity_usdt,
            leverage=leverage,
            active_orders=active,
            proposed_orders=proposals,
            maker_fee_rate=maker_fee_rate,
        )
    except ValueError:
        return [
            SideAdmission(
                order.side, False, "SUPPRESS_MARGIN_STATE_UNKNOWN", math.inf,
                _inventory_impact(order.side, inventory_btc),
                margin_components(
                    inventory_btc=0.0,
                    mid_price_usdt_per_btc=1.0,
                    current_equity_usdt=max(current_equity_usdt, 0.0),
                    leverage=1.0,
                ),
            )
            for order in proposals
        ]
    if combined.margin_utilization <= maximum_margin_utilization + 1e-12:
        return [
            SideAdmission(
                order.side, True, "ADMIT_WITHIN_MARGIN",
                combined.margin_utilization,
                _inventory_impact(order.side, inventory_btc), combined,
            )
            for order in proposals
        ]

    individual: dict[str, MarginComponents] = {
        order.side: margin_components(
            inventory_btc=inventory_btc,
            mid_price_usdt_per_btc=mid_price_usdt_per_btc,
            current_equity_usdt=current_equity_usdt,
            leverage=leverage,
            active_orders=active,
            proposed_orders=[order],
            maker_fee_rate=maker_fee_rate,
        )
        for order in proposals
    }
    feasible = [
        order for order in proposals
        if individual[order.side].margin_utilization
        <= maximum_margin_utilization + 1e-12
    ]
    chosen: str | None = None
    if len(feasible) == 1:
        chosen = feasible[0].side
    elif len(feasible) > 1:
        reducing = [
            order for order in feasible
            if _inventory_impact(order.side, inventory_btc) == "REDUCING"
        ]
        if reducing:
            chosen = reducing[0].side
        else:
            chosen = min(
                feasible,
                key=lambda order: (
                    individual[order.side].margin_utilization,
                    0 if order.side == "buy" else 1,
                ),
            ).side

    position_only = combined.position_margin_usdt / current_equity_usdt
    decisions = []
    for order in proposals:
        components = individual[order.side]
        allowed = order.side == chosen
        if allowed:
            reason = "ADMIT_LIMITED_MARGIN_POLICY"
        elif position_only > maximum_margin_utilization + 1e-12:
            reason = "SUPPRESS_POSITION_MARGIN"
        elif feasible:
            reason = "SUPPRESS_PENDING_ORDER_RESERVE"
        else:
            reason = "SUPPRESS_INSUFFICIENT_MARGIN"
        decisions.append(SideAdmission(
            order.side,
            allowed,
            reason,
            components.margin_utilization,
            _inventory_impact(order.side, inventory_btc),
            components,
        ))
    return decisions
