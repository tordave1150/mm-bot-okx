"""Inventory-aware side suppression and quote-size control."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryControl:
    bid_size_btc: float
    ask_size_btc: float
    bid_suppressed: bool
    ask_suppressed: bool
    utilization: float


def inventory_control(
    *,
    inventory_btc: float,
    fixed_lot_size_btc: float,
    maximum_inventory_btc: float,
) -> InventoryControl:
    if fixed_lot_size_btc <= 0 or maximum_inventory_btc <= 0:
        raise ValueError("inventory and lot limits must be positive")
    utilization = min(1.0, abs(inventory_btc) / maximum_inventory_btc)
    bid_suppressed = inventory_btc >= maximum_inventory_btc - 1e-12
    ask_suppressed = inventory_btc <= -maximum_inventory_btc + 1e-12
    risk_increasing_scale = max(0.0, 1.0 - utilization)
    bid_size = (
        0.0 if bid_suppressed else
        fixed_lot_size_btc * (
            risk_increasing_scale if inventory_btc > 0 else 1.0
        )
    )
    ask_size = (
        0.0 if ask_suppressed else
        fixed_lot_size_btc * (
            risk_increasing_scale if inventory_btc < 0 else 1.0
        )
    )
    return InventoryControl(
        bid_size_btc=bid_size,
        ask_size_btc=ask_size,
        bid_suppressed=bid_suppressed,
        ask_suppressed=ask_suppressed,
        utilization=utilization,
    )
