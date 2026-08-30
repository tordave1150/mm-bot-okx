"""Market-maker attribution and concentration diagnostics."""

from __future__ import annotations

import math
from statistics import mean
from typing import Any


def favorable_markout(
    *, side: str, fill_price: float, future_mid: float
) -> float:
    if side == "buy":
        return future_mid - fill_price
    if side == "sell":
        return fill_price - future_mid
    raise ValueError(f"unknown side: {side!r}")


def top_profit_removal(values: list[float], fraction: float = 0.10) -> float:
    if not values:
        return 0.0
    if not 0 < fraction < 1:
        raise ValueError("removal fraction must be in (0, 1)")
    remove_count = max(1, math.ceil(len(values) * fraction))
    remaining = sorted(values, reverse=True)[remove_count:]
    return sum(remaining)


def conservative_objective(metrics: dict[str, float]) -> float:
    required = (
        "net_pnl_usdt",
        "average_spread_capture_usdt",
        "profit_factor_clipped",
        "after_top_10pct_removal_usdt",
        "quote_uptime",
        "max_drawdown",
        "inventory_variance",
        "adverse_markout_loss_usdt",
        "terminal_liquidation_cost_usdt",
        "quote_churn",
        "cancel_to_fill_ratio",
    )
    missing = [key for key in required if key not in metrics]
    if missing:
        raise ValueError(f"missing objective metrics: {missing}")
    if not all(math.isfinite(float(metrics[key])) for key in required):
        raise ValueError("objective metrics must be finite")
    normalized = {
        "net": max(-1.0, min(1.0, metrics["net_pnl_usdt"] / 10.0)),
        "spread": max(
            -1.0, min(1.0, metrics["average_spread_capture_usdt"] / 0.25)
        ),
        "pf": max(0.0, min(1.0, metrics["profit_factor_clipped"] / 2.0)),
        "concentration": max(
            -1.0,
            min(1.0, metrics["after_top_10pct_removal_usdt"] / 10.0),
        ),
        "uptime": max(0.0, min(1.0, metrics["quote_uptime"])),
        "drawdown": max(0.0, min(1.0, metrics["max_drawdown"] / 0.025)),
        "inventory": max(0.0, min(1.0, metrics["inventory_variance"] / 0.0001)),
        "markout": max(
            0.0, min(1.0, metrics["adverse_markout_loss_usdt"] / 5.0)
        ),
        "terminal": max(
            0.0, min(1.0, metrics["terminal_liquidation_cost_usdt"] / 5.0)
        ),
        "churn": max(0.0, min(1.0, metrics["quote_churn"])),
        "cancel_fill": max(
            0.0, min(1.0, metrics["cancel_to_fill_ratio"] / 10.0)
        ),
    }
    return (
        0.20 * normalized["net"]
        + 0.15 * normalized["spread"]
        + 0.10 * normalized["pf"]
        + 0.10 * normalized["concentration"]
        + 0.10 * normalized["uptime"]
        - 0.10 * normalized["drawdown"]
        - 0.05 * normalized["inventory"]
        - 0.05 * normalized["markout"]
        - 0.05 * normalized["terminal"]
        - 0.05 * normalized["churn"]
        - 0.05 * normalized["cancel_fill"]
    )


def population_variance(values: list[float]) -> float:
    if not values:
        return 0.0
    center = mean(values)
    return mean((value - center) ** 2 for value in values)
