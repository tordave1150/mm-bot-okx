"""
backtest/metrics.py — Risk metrics computed from BacktestResult.

Uses ``empyrical`` for standard quant finance metrics (Sortino, MaxDD, VaR)
and adds bot-specific metrics (kill-switch count, fill rate, inventory breach).

Usage::

    from backtest.runner import BacktestResult
    from backtest.metrics import compute_metrics
    from config import Config

    metrics = compute_metrics(result, config)
    # metrics is a dict with all computed values
"""

from __future__ import annotations

import logging
import sys
import os
import math
from typing import Any

import numpy as np

# ── Ensure project root importable ────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config

# empyrical intentionally not used — pure numpy implementation for portability

logger = logging.getLogger(__name__)

# Default annualisation factor: 288 ticks/day × 365 days
# Can be overridden per-result if ticks_per_day is known
_DEFAULT_TICKS_PER_DAY = 288
_DEFAULT_ANNUAL_FACTOR = _DEFAULT_TICKS_PER_DAY * 365


# ── Public API ────────────────────────────────────────────────────────────────

def compute_metrics(result: Any, config: Config) -> dict[str, Any]:
    """Compute all risk metrics from a BacktestResult.

    Parameters
    ----------
    result : BacktestResult
        Output from ``BacktestRunner.run()``.
    config : Config
        Bot config (used for max_inventory threshold).

    Returns
    -------
    dict
        Keys: sortino_ratio, max_drawdown, var_95, var_99, cvar_95, cvar_99,
              kill_switch_count, bid_fill_rate, ask_fill_rate,
              pct_time_over_80pct_inventory, final_equity,
              total_return_pct, annual_vol.
    """
    equity_curve = np.array(result.equity_curve, dtype=float)
    n_ticks = result.total_ticks

    if len(equity_curve) < 2:
        logger.warning("Equity curve too short (%d points) — returning zeroed metrics", len(equity_curve))
        return _zeroed_metrics()

    # Determine annualization factor dynamically
    ticks_per_day = getattr(result, 'ticks_per_day', _DEFAULT_TICKS_PER_DAY)
    annual_factor = ticks_per_day * 365

    # ── Returns series (tick-by-tick) ─────────────────────────────────────
    returns = np.diff(equity_curve) / np.where(equity_curve[:-1] != 0, equity_curve[:-1], 1.0)
    returns = returns[np.isfinite(returns)]  # remove any inf/nan

    # ── Sortino Ratio ─────────────────────────────────────────────────────
    sortino = _sortino_ratio(returns, annual_factor=annual_factor)

    # ── Max Drawdown ──────────────────────────────────────────────────────
    max_dd = _max_drawdown(equity_curve)

    # ── VaR (Value at Risk) — loss quantile ───────────────────────────────
    # VaR is expressed as a *positive* number (the loss magnitude).
    # e.g., var_95 = 0.02 means 5% of tick-returns are worse than -2%.
    var_95 = _var(returns, 0.05)    # 95% confidence → 5th percentile
    var_99 = _var(returns, 0.01)    # 99% confidence → 1st percentile

    # ── CVaR / Expected Shortfall ─────────────────────────────────────────
    cvar_95 = _cvar(returns, 0.05)
    cvar_99 = _cvar(returns, 0.01)

    # ── Annualised Volatility ─────────────────────────────────────────────
    annual_vol = float(np.std(returns)) * math.sqrt(annual_factor) if len(returns) > 1 else 0.0

    # ── Total Return ──────────────────────────────────────────────────────
    initial = equity_curve[0]
    final = equity_curve[-1]
    total_return_pct = (final - initial) / initial if initial != 0 else 0.0

    # ── Bot-specific metrics ──────────────────────────────────────────────
    fill_base = max(n_ticks, 1)

    bid_fill_rate = result.bid_fills / fill_base
    ask_fill_rate = result.ask_fills / fill_base
    pct_time_over_80 = result.ticks_over_80pct_inventory / fill_base

    # ── Fee and P&L metrics ───────────────────────────────────────
    total_fees = getattr(result, 'total_fees', 0.0)
    total_realized_pnl = getattr(result, 'total_realized_pnl', 0.0)
    gross_pnl = total_return_pct * initial if initial != 0 else 0.0
    net_pnl_after_fees = total_realized_pnl

    return {
        # Standard risk metrics
        "sortino_ratio": sortino,
        "max_drawdown": max_dd,
        "var_95": var_95,
        "var_99": var_99,
        "cvar_95": cvar_95,
        "cvar_99": cvar_99,
        "annual_vol": annual_vol,
        # Return summary
        "final_equity": final,
        "total_return_pct": total_return_pct,
        # Bot-specific
        "kill_switch_count": result.kill_switch_count,
        "bid_fill_rate": bid_fill_rate,
        "ask_fill_rate": ask_fill_rate,
        "pct_time_over_80pct_inventory": pct_time_over_80,
        # Raw counts
        "total_ticks": n_ticks,
        "bid_fills": result.bid_fills,
        "ask_fills": result.ask_fills,
        "kill_switch_events": result.kill_switch_events,
    }


def format_metrics_table(metrics: dict[str, Any]) -> str:
    """Return a human-readable table string of the metrics dict."""
    lines = ["── Backtest Metrics ────────────────────────────"]
    scalar_keys = [
        ("sortino_ratio",              "Sortino Ratio",               ".4f"),
        ("max_drawdown",               "Max Drawdown",                ".2%"),
        ("var_95",                     "VaR (95%)",                   ".4f"),
        ("var_99",                     "VaR (99%)",                   ".4f"),
        ("cvar_95",                    "CVaR / ES (95%)",             ".4f"),
        ("cvar_99",                    "CVaR / ES (99%)",             ".4f"),
        ("annual_vol",                 "Annual Volatility",           ".2%"),
        ("total_return_pct",           "Total Return",                ".2%"),
        ("final_equity",               "Final Equity",                ".2f"),
        ("kill_switch_count",          "Kill-Switch Triggers",        "d"),
        ("bid_fill_rate",              "Bid Fill Rate",               ".4%"),
        ("ask_fill_rate",              "Ask Fill Rate",               ".4%"),
        ("pct_time_over_80pct_inventory", "Time >80% Inventory",     ".2%"),
    ]
    for key, label, fmt in scalar_keys:
        val = metrics.get(key, 0)
        if fmt == "d":
            lines.append(f"  {label:<30} {int(val)}")
        elif fmt.endswith("%"):
            lines.append(f"  {label:<30} {val:{fmt}}")
        else:
            lines.append(f"  {label:<30} {val:{fmt}}")
    lines.append("─" * 48)
    return "\n".join(lines)


# ── Private helpers ───────────────────────────────────────────────────────────

def _sortino_ratio(returns: np.ndarray, target: float = 0.0, annual_factor: int = _DEFAULT_ANNUAL_FACTOR) -> float:
    """Sortino ratio (pure numpy)."""
    if len(returns) < 2:
        return 0.0

    downside = returns[returns < target] - target
    downside_std = math.sqrt(float(np.mean(downside ** 2))) if len(downside) > 0 else 0.0
    mean_ret = float(np.mean(returns))
    if downside_std == 0:
        return 0.0
    return float(mean_ret / downside_std * math.sqrt(annual_factor))


def _max_drawdown(equity_curve: np.ndarray) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction (pure numpy)."""
    if len(equity_curve) < 2:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _var(returns: np.ndarray, alpha: float) -> float:
    """Value at Risk at confidence level (1-alpha).

    Returns a positive number representing the magnitude of loss at the
    given tail quantile.  e.g., VaR(95%) = 0.02 means the worst 5% of
    returns exceed a -2% loss.
    """
    if len(returns) == 0:
        return 0.0
    quantile = float(np.percentile(returns, alpha * 100))
    return -min(quantile, 0.0)   # convert loss to positive


def _cvar(returns: np.ndarray, alpha: float) -> float:
    """Expected Shortfall (CVaR) at confidence level (1-alpha).

    Average loss in the worst (alpha * 100)% of cases.
    Returns a positive number.
    """
    if len(returns) == 0:
        return 0.0
    threshold = float(np.percentile(returns, alpha * 100))
    tail = returns[returns <= threshold]
    if len(tail) == 0:
        return 0.0
    return -float(np.mean(tail))


def _zeroed_metrics() -> dict[str, Any]:
    return {
        "sortino_ratio": 0.0,
        "max_drawdown": 0.0,
        "var_95": 0.0,
        "var_99": 0.0,
        "cvar_95": 0.0,
        "cvar_99": 0.0,
        "annual_vol": 0.0,
        "final_equity": 0.0,
        "total_return_pct": 0.0,
        "kill_switch_count": 0,
        "bid_fill_rate": 0.0,
        "ask_fill_rate": 0.0,
        "pct_time_over_80pct_inventory": 0.0,
        "total_ticks": 0,
        "bid_fills": 0,
        "ask_fills": 0,
        "kill_switch_events": [],
    }
