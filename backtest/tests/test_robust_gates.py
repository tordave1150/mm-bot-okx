from __future__ import annotations

from copy import deepcopy

from backtest.metrics import REQUIRED_METRIC_KEYS
from backtest.robust_optimize import _check_crash_gates


def _valid_crash_metrics() -> dict:
    metrics = {key: 0.0 for key in REQUIRED_METRIC_KEYS}
    metrics.update({
        "initial_equity_usdt": 1000.0,
        "final_equity_usdt": 990.0,
        "min_equity_usdt": 980.0,
        "max_margin_utilization": 0.5,
        "invalid_order_count": 0,
        "max_abs_inventory_lots": 1.0,
        "ending_inventory_base": 0.0,
        "max_drawdown": 0.02,
        "kill_switch_count": 0,
        "kill_switch_events": [],
        "post_kill_deterioration_pct": 0.0,
    })
    return metrics


def test_crash_without_strategy_drawdown_does_not_require_kill() -> None:
    assert _check_crash_gates(_valid_crash_metrics(), 1000.0) is None


def test_crash_trigger_crossing_without_kill_fails_closed() -> None:
    metrics = _valid_crash_metrics()
    metrics["max_drawdown"] = 0.04
    assert "without exactly one kill-switch" in _check_crash_gates(
        metrics, 1000.0
    )


def test_crash_kill_requires_flatten_confirmation() -> None:
    metrics = deepcopy(_valid_crash_metrics())
    metrics["max_drawdown"] = 0.04
    metrics["kill_switch_count"] = 1
    assert "flattened inventory" in _check_crash_gates(metrics, 1000.0)

    metrics["kill_switch_events"] = [{"post_flatten_inventory": 0.0}]
    assert _check_crash_gates(metrics, 1000.0) is None
