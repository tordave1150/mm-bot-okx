"""Canonical, fail-closed metrics for offline backtests.

All returns, drawdowns, VaR, and CVaR values are fractions.  Currency values
are denominated in quote currency (USDT for the repository's default market),
and inventory values are base-asset quantities.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from config import Config


REQUIRED_METRIC_KEYS = frozenset({
    "initial_equity_usdt", "final_equity_usdt", "min_equity_usdt", "total_return",
    "max_drawdown", "sortino_ratio", "var_95", "var_99", "cvar_95",
    "cvar_99", "annual_volatility", "total_fees_usdt", "bid_fills",
    "ask_fills", "max_abs_inventory_base", "ending_inventory_base",
    "max_margin_utilization", "kill_switch_count",
    "post_kill_deterioration_usdt", "invalid_order_count",
    "gross_realized_pnl_usdt", "net_realized_pnl_usdt",
    "unrealized_pnl_usdt", "funding_pnl_usdt",
    "kill_trigger_equity_usdt", "kill_post_flatten_equity_usdt",
    "flatten_fees_usdt", "flatten_slippage_usdt",
    "residual_inventory_base", "post_kill_deterioration_pct",
    "maker_bid_fills", "maker_ask_fills", "taker_exit_fills",
    "completed_round_trips", "local_risk_rejection_count",
})


def compute_metrics(result: Any, config: Config) -> dict[str, Any]:
    """Compute optimizer-safe metrics; never manufacture a zeroed result."""
    equity = np.asarray(result.equity_curve, dtype=float)
    if len(equity) < 2:
        raise ValueError("Equity curve must contain initial equity and at least one observation")
    if not np.all(np.isfinite(equity)):
        raise ValueError("Equity curve contains NaN or infinity")
    if equity[0] <= 0:
        raise ValueError("Initial equity must be positive")

    denominators = equity[:-1]
    returns = np.divide(
        np.diff(equity),
        denominators,
        out=np.full_like(denominators, np.nan),
        where=denominators != 0,
    )
    finite_returns = returns[np.isfinite(returns)]
    ticks_per_day = int(result.ticks_per_day)
    if ticks_per_day <= 0:
        raise ValueError("ticks_per_day must be positive")
    annual_factor = ticks_per_day * 365

    initial = float(equity[0])
    final = float(equity[-1])
    total_return = (final - initial) / initial
    total_fees = float(result.total_fees)
    post_kill = float(result.post_kill_deterioration)

    metrics: dict[str, Any] = {
        "initial_equity": initial,
        "initial_equity_usdt": initial,
        "final_equity": final,
        "final_equity_usdt": final,
        "min_equity": float(np.min(equity)),
        "min_equity_usdt": float(np.min(equity)),
        "total_return": total_return,
        "total_return_pct": total_return,  # compatibility: historical name, fractional unit
        "max_drawdown": _max_drawdown(equity),
        "sortino_ratio": _sortino_ratio(finite_returns, annual_factor),
        "var_95": _var(finite_returns, 0.05),
        "var_99": _var(finite_returns, 0.01),
        "cvar_95": _cvar(finite_returns, 0.05),
        "cvar_99": _cvar(finite_returns, 0.01),
        "annual_volatility": (
            float(np.std(finite_returns)) * math.sqrt(annual_factor)
            if len(finite_returns) > 1 else 0.0
        ),
        "annual_vol": (
            float(np.std(finite_returns)) * math.sqrt(annual_factor)
            if len(finite_returns) > 1 else 0.0
        ),
        "total_fees_usdt": total_fees,
        "total_fees": total_fees,
        "gross_realized_pnl": float(result.gross_realized_pnl),
        "gross_realized_pnl_usdt": float(result.gross_realized_pnl),
        "net_realized_pnl": float(result.total_realized_pnl),
        "net_realized_pnl_usdt": float(result.total_realized_pnl),
        "total_realized_pnl": float(result.total_realized_pnl),
        "unrealized_pnl": float(result.unrealized_pnl),
        "unrealized_pnl_usdt": float(result.unrealized_pnl),
        "funding_pnl": float(result.funding_pnl),
        "funding_pnl_usdt": float(result.funding_pnl),
        "gross_pnl_before_fees": final - initial + total_fees,
        "gross_pnl": final - initial + total_fees,
        "total_ticks": int(result.total_ticks),
        "bid_fills": int(result.bid_fills),
        "ask_fills": int(result.ask_fills),
        "bid_fill_base_qty": float(result.bid_fill_base_qty),
        "ask_fill_base_qty": float(result.ask_fill_base_qty),
        "bid_fill_rate": result.bid_fills / max(result.total_ticks, 1),
        "ask_fill_rate": result.ask_fills / max(result.total_ticks, 1),
        "pct_time_over_80pct_inventory": (
            result.ticks_over_80pct_inventory / max(result.total_ticks, 1)
        ),
        "max_abs_inventory_base": float(result.max_abs_inventory_base),
        "max_abs_inventory_lots": (
            float(result.max_abs_inventory_base) / config.fixed_lot_size
        ),
        "ending_inventory_base": float(result.ending_inventory_base),
        "residual_inventory_base": float(result.residual_inventory_base),
        "max_margin_utilization": float(result.max_margin_utilization),
        "margin_utilization": float(result.max_margin_utilization),
        "invalid_order_count": int(result.invalid_order_count),
        "invalid_order_reasons": list(result.invalid_order_reasons),
        "rejected_post_only_count": int(result.rejected_post_only_count),
        "local_risk_rejection_count": int(result.local_risk_rejection_count),
        "local_quote_skip_count": int(result.local_quote_skip_count),
        "post_only_clamp_count": int(result.post_only_clamp_count),
        "quote_updates": int(result.quote_updates),
        "maker_bid_fills": int(result.maker_bid_fills),
        "maker_ask_fills": int(result.maker_ask_fills),
        "taker_exit_fills": int(result.taker_exit_fills),
        "partial_fills": int(result.partial_fills),
        "completed_round_trips": int(result.completed_round_trips),
        "kill_switch_count": int(result.kill_switch_count),
        "kill_switch_events": list(result.kill_switch_events),
        "kill_equity_at_trigger": float(result.post_kill_equity_at_trigger),
        "kill_trigger_equity_usdt": float(result.post_kill_equity_at_trigger),
        "kill_post_flatten_equity": float(result.kill_post_flatten_equity),
        "kill_post_flatten_equity_usdt": float(result.kill_post_flatten_equity),
        "post_kill_deterioration": post_kill,
        "post_kill_deterioration_usdt": post_kill,
        "post_kill_deterioration_fraction": post_kill / initial,
        "post_kill_deterioration_pct": post_kill / initial,
        "flatten_fees": float(result.flatten_fees),
        "flatten_fees_usdt": float(result.flatten_fees),
        "flatten_slippage": float(result.flatten_slippage),
        "flatten_slippage_usdt": float(result.flatten_slippage),
        "terminal_fees": float(result.terminal_fees),
        "terminal_slippage": float(result.terminal_slippage),
        "market_spec_fingerprint": result.market_spec_fingerprint,
    }
    validate_metric_schema(metrics)
    return metrics


def validate_metric_schema(metrics: dict[str, Any]) -> None:
    """Raise on missing or non-finite required metrics."""
    missing = sorted(REQUIRED_METRIC_KEYS.difference(metrics))
    if missing:
        raise KeyError(f"Missing required metrics: {', '.join(missing)}")
    bad = [
        key for key in REQUIRED_METRIC_KEYS
        if isinstance(metrics[key], (int, float)) and not math.isfinite(float(metrics[key]))
    ]
    if bad:
        raise ValueError(f"Non-finite required metrics: {', '.join(sorted(bad))}")


def format_metrics_table(metrics: dict[str, Any]) -> str:
    rows = (
        ("Total Return", "total_return", ".2%"),
        ("Final Equity", "final_equity", ".2f"),
        ("Max Drawdown", "max_drawdown", ".2%"),
        ("Sortino Ratio", "sortino_ratio", ".4f"),
        ("CVaR 95", "cvar_95", ".4f"),
        ("Fees (USDT)", "total_fees_usdt", ".4f"),
        ("Max Margin Utilization", "max_margin_utilization", ".2%"),
        ("Kill-Switch Triggers", "kill_switch_count", "d"),
    )
    lines = ["-- Backtest Metrics " + "-" * 27]
    for label, key, spec in rows:
        value = metrics[key]
        lines.append(f"  {label:<30} {int(value) if spec == 'd' else format(value, spec)}")
    return "\n".join(lines)


def _sortino_ratio(returns: np.ndarray, annual_factor: int) -> float:
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    downside_deviation = math.sqrt(float(np.mean(downside ** 2))) if len(downside) else 0.0
    if downside_deviation == 0:
        return 0.0
    return float(np.mean(returns) / downside_deviation * math.sqrt(annual_factor))


def _max_drawdown(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    drawdowns = np.divide(
        peaks - equity, peaks, out=np.full_like(equity, np.inf), where=peaks > 0
    )
    return float(np.max(drawdowns))


def _var(returns: np.ndarray, alpha: float) -> float:
    if len(returns) == 0:
        return 0.0
    return max(0.0, -float(np.percentile(returns, alpha * 100)))


def _cvar(returns: np.ndarray, alpha: float) -> float:
    if len(returns) == 0:
        return 0.0
    threshold = float(np.percentile(returns, alpha * 100))
    tail = returns[returns <= threshold]
    return max(0.0, -float(np.mean(tail))) if len(tail) else 0.0
