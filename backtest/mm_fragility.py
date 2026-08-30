"""Required Market Maker smoke stress diagnostics."""

from __future__ import annotations

from statistics import mean


def added_cost_expectancy(
    round_trip_pnls: list[float],
    *,
    added_cost_per_round_trip_usdt: float,
) -> float:
    if added_cost_per_round_trip_usdt < 0:
        raise ValueError("added cost must be non-negative")
    return (
        mean(value - added_cost_per_round_trip_usdt for value in round_trip_pnls)
        if round_trip_pnls else 0.0
    )


def markout_materially_negative(
    average_markout_usdt: float, *, tolerance_usdt: float = -0.05
) -> bool:
    return average_markout_usdt < tolerance_usdt
