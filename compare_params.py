"""
compare_params.py — Compare parameter sets to find which config keeps the portfolio alive.

Run with:
    python compare_params.py

Runs backtest on same scenario (seed=42, vol=0.25, n_days=30) across different configs.
"""

from __future__ import annotations

import sys
import os
from dataclasses import replace

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import logging
logging.disable(logging.CRITICAL)  # Silence noisy logs

from config import load_config
from backtest.runner import BacktestRunner
from backtest.synthetic_data import generate_regime_switching_gbm
from backtest.metrics import compute_metrics

# ── Scenario settings ────────────────────────────────────────────────
SEEDS    = [42, 43, 44, 45, 46]  # Run on 5 seeds and average results
VOL      = 0.25
N_DAYS   = 30
FILL_MODE = "probabilistic"

# ── กำหนดชุด parameter ที่ต้องการทดสอบ ──────────────────────────────────────
PARAM_SETS = {
    "Default (current config.py)": {},  # Use defaults from config.py

    "Best Trial #201 (Optuna winner)": {
        "gamma": 0.29533864670021215,
        "k": 4.176599010180326,
        "tau": 1.7512045323642924,
        "max_inventory_lots": 1,
        "max_drawdown_pct": 0.047074361080890616,
        "trend_spread_multiplier": 1.5312376972972281,
        "trend_size_multiplier": 0.7967883994603725,
        "range_spread_multiplier": 1.1870480854924734,
        "imbalance_skew_factor": 0.08759538768149842,
        "inventory_skew_factor": 0.771210965163458,
        "ema_span": 16,
    },

    # ── Test each parameter individually ─────────────────────────────────────

    "gamma=0.295 only": {
        "gamma": 0.295,
    },

    "k=4.18 only": {
        "k": 4.18,
    },

    "dd_lim=4.7% only": {
        "max_drawdown_pct": 0.047,
    },

    "ema_span=16 only": {
        "ema_span": 16,
    },

    "Regime multipliers from Best Trial": {
        "trend_spread_multiplier": 1.531,
        "trend_size_multiplier": 0.797,
        "range_spread_multiplier": 1.187,
    },

    "Skew factors from Best Trial": {
        "imbalance_skew_factor": 0.088,
        "inventory_skew_factor": 0.771,
    },
}


def run_scenario(overrides: dict, seed: int) -> dict:
    """Run backtest for one config + one seed, return metrics."""
    base_cfg = load_config()
    cfg = replace(base_cfg, **overrides)

    ticks = generate_regime_switching_gbm(
        vol_weekly=VOL,
        n_days=N_DAYS,
        seed=seed,
    )

    runner = BacktestRunner(cfg, fill_mode=FILL_MODE)
    result = runner.run(ticks)
    return compute_metrics(result, cfg)


def run_average(overrides: dict) -> dict:
    """Run backtest across all SEEDS and return averaged metrics."""
    all_ret, all_dd, all_eq, all_kill = [], [], [], []
    for seed in SEEDS:
        m = run_scenario(overrides, seed)
        all_ret.append(m.get("total_return_pct", 0))
        all_dd.append(m.get("max_drawdown", 1))
        all_eq.append(m.get("final_equity", 0))
        all_kill.append(m.get("kill_switch_count", 0))
    return {
        "avg_return": sum(all_ret) / len(all_ret),
        "avg_dd": sum(all_dd) / len(all_dd),
        "avg_equity": sum(all_eq) / len(all_eq),
        "avg_kill": sum(all_kill) / len(all_kill),
        "worst_return": min(all_ret),
        "survived_count": sum(1 for r in all_ret if r > 0),
    }


def survived(avg_return: float, survived_count: int) -> str:
    """Determine if the portfolio is reliable."""
    if survived_count == len(SEEDS):
        return f"GOOD  ({survived_count}/{len(SEEDS)} profitable)"
    if survived_count >= len(SEEDS) // 2:
        return f"MIXED ({survived_count}/{len(SEEDS)} profitable)"
    return f"POOR  ({survived_count}/{len(SEEDS)} profitable)"


def main():
    print(f"\n{'='*84}")
    print(f"  Parameter Comparison (avg of {len(SEEDS)} seeds)  |  vol={VOL}  days={N_DAYS}  fill={FILL_MODE}")
    print(f"  Seeds: {SEEDS}")
    print(f"{'='*84}")
    print(f"  {'Parameter Set':<38} {'AvgRet':>8} {'AvgDD':>7} {'AvgEq':>8} {'Kill':>5} {'Worst':>8}  {'Result'}")
    print(f"  {'-'*38} {'-'*8} {'-'*7} {'-'*8} {'-'*5} {'-'*8}  {'-'*22}")

    for label, overrides in PARAM_SETS.items():
        try:
            r = run_average(overrides)
            ret   = r["avg_return"] * 100
            dd    = r["avg_dd"] * 100
            eq    = r["avg_equity"]
            kill  = r["avg_kill"]
            worst = r["worst_return"] * 100
            surv  = r["survived_count"]
            status = survived(ret, surv)
            print(f"  {label:<38} {ret:>+7.2f}% {dd:>6.1f}% {eq:>8,.1f} {kill:>5.1f} {worst:>+7.2f}%  {status}")
        except Exception as e:
            print(f"  {label:<38}  ERROR: {e}")

    print(f"{'='*84}")
    print(f"\n  Column guide:")
    print(f"    AvgRet  -> avg return across {len(SEEDS)} seeds (higher is better)")
    print(f"    AvgDD   -> avg max drawdown (lower is better)")
    print(f"    AvgEq   -> avg ending equity (started at 300 USDT)")
    print(f"    Kill    -> avg kill switch activations")
    print(f"    Worst   -> worst single-seed return (robustness check)")
    print(f"    Result  -> how many seeds ended profitably")
    print()


if __name__ == "__main__":
    main()
