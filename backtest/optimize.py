"""
backtest/optimize.py — Optuna hyperparameter optimization for the MM bot.

Searches over the key Avellaneda-Stoikov and risk parameters using the
existing BacktestRunner, evaluating across multiple synthetic scenarios
(seeds) to find robust parameter sets.

Usage::

    python -m backtest.optimize                     # 100 trials, 3 seeds
    python -m backtest.optimize --n-trials 200      # more trials
    python -m backtest.optimize --n-seeds 5         # more scenarios
    python -m backtest.optimize --n-days 14         # longer scenarios
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
import time
from dataclasses import replace

import numpy as np
import optuna
from optuna.pruners import MedianPruner

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config, load_config
from backtest.runner import BacktestRunner
from backtest.synthetic_data import generate_regime_switching_gbm
from backtest.metrics import compute_metrics

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  Objective function
# ══════════════════════════════════════════════════════════════════════════════

def objective(
    trial: optuna.Trial,
    base_cfg: Config,
    seeds: list[int],
    n_days: int,
    vol_weekly: float,
    ticks_per_day: int,
) -> float:
    """Evaluate a single parameter configuration across multiple scenarios.

    The objective maximises a composite score:
        score = total_return - 2 * max_drawdown + 0.1 * sortino

    This balances profitability, risk control, and risk-adjusted returns.
    A penalty is added for kill-switch triggers to discourage fragile configs.
    """

    # ── Sample hyperparameters ───────────────────────────────────────────
    gamma = trial.suggest_float("gamma", 0.01, 1.0, log=True)
    k = trial.suggest_float("k", 0.5, 5.0)
    tau = trial.suggest_float("tau", 0.5, 2.0)

    max_inventory = trial.suggest_float("max_inventory", 0.01, 0.10)
    order_size_base = trial.suggest_float("order_size_base", 0.002, 0.03)
    max_drawdown_pct = trial.suggest_float("max_drawdown_pct", 0.03, 0.15)

    # Regime adjustments
    trend_spread_mult = trial.suggest_float("trend_spread_multiplier", 1.0, 3.0)
    trend_size_mult = trial.suggest_float("trend_size_multiplier", 0.2, 1.0)
    range_spread_mult = trial.suggest_float("range_spread_multiplier", 0.5, 1.2)

    # Skew factors
    imbalance_skew = trial.suggest_float("imbalance_skew_factor", 0.0, 2.0)
    inventory_skew = trial.suggest_float("inventory_skew_factor", 0.5, 3.0)

    # EMA span for volatility
    ema_span = trial.suggest_int("ema_span", 10, 50)

    # ── Build config with sampled params ─────────────────────────────────
    cfg = replace(
        base_cfg,
        gamma=gamma,
        k=k,
        tau=tau,
        max_inventory=max_inventory,
        order_size_base=order_size_base,
        max_drawdown_pct=max_drawdown_pct,
        trend_spread_multiplier=trend_spread_mult,
        trend_size_multiplier=trend_size_mult,
        range_spread_multiplier=range_spread_mult,
        imbalance_skew_factor=imbalance_skew,
        inventory_skew_factor=inventory_skew,
        ema_span=ema_span,
    )

    # ── Evaluate across multiple seeds ───────────────────────────────────
    scores: list[float] = []

    for i, seed in enumerate(seeds):
        ticks = generate_regime_switching_gbm(
            vol_weekly=vol_weekly,
            n_days=n_days,
            ticks_per_day=ticks_per_day,
            seed=seed,
        )

        runner = BacktestRunner(cfg)
        result = runner.run(ticks)
        metrics = compute_metrics(result, cfg)

        total_return = metrics.get("total_return_pct", -1.0)
        max_dd = metrics.get("max_drawdown", 1.0)
        sortino = metrics.get("sortino_ratio", 0.0)
        kill_count = metrics.get("kill_switch_count", 0)
        bid_fill_rate = metrics.get("bid_fill_rate", 0.0)
        ask_fill_rate = metrics.get("ask_fill_rate", 0.0)

        # Clamp extreme sortino values
        sortino = max(min(sortino, 10.0), -10.0)

        # Composite score
        score = (
            total_return                          # want positive returns
            - 2.0 * max_dd                        # penalise drawdown heavily
            + 0.1 * sortino                       # reward risk-adjusted returns
            + 0.5 * (bid_fill_rate + ask_fill_rate)  # reward getting fills
            - 0.1 * kill_count                    # penalise kill-switch triggers
        )
        scores.append(score)

        # Report intermediate value for pruning (average so far)
        trial.report(np.mean(scores), i)
        if trial.should_prune():
            raise optuna.TrialPruned()

    return float(np.mean(scores))


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optuna hyperparameter optimization for the MM bot backtest"
    )
    parser.add_argument("--n-trials", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--n-seeds", type=int, default=3, help="Number of random seeds per trial")
    parser.add_argument("--n-days", type=int, default=7, help="Days per scenario")
    parser.add_argument("--vol", type=float, default=0.25, help="Weekly volatility")
    parser.add_argument("--ticks-per-day", type=int, default=288, help="Ticks per day")
    parser.add_argument("--study-name", type=str, default="mm-bot-optuna", help="Optuna study name")
    parser.add_argument("--db", type=str, default=None, help="SQLite DB path for persistence (e.g. optuna_study.db)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Suppress noisy loggers
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    logging.getLogger("backtest").setLevel(logging.WARNING)

    base_cfg = load_config()
    seeds = list(range(42, 42 + args.n_seeds))

    # ── Create study ─────────────────────────────────────────────────────
    storage = f"sqlite:///{args.db}" if args.db else None
    study = optuna.create_study(
        study_name=args.study_name,
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=1),
    )

    print("=" * 60)
    print("  Optuna Hyperparameter Optimization")
    print(f"  Trials: {args.n_trials}  Seeds: {args.n_seeds}  Days: {args.n_days}  Vol: {args.vol}")
    print(f"  Study: {args.study_name}")
    print("=" * 60)
    print()

    t0 = time.time()

    study.optimize(
        lambda trial: objective(
            trial, base_cfg, seeds, args.n_days, args.vol, args.ticks_per_day,
        ),
        n_trials=args.n_trials,
        show_progress_bar=True,
    )

    elapsed = time.time() - t0

    # ── Results ──────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  OPTIMIZATION COMPLETE -- {elapsed:.1f}s ({elapsed/60:.1f}min)")
    print("=" * 60)
    print()

    best = study.best_trial
    print(f"  Best Trial: #{best.number}")
    print(f"  Best Score: {best.value:.6f}")
    print()
    print("  Best Parameters:")
    print("  " + "-" * 40)
    for k, v in sorted(best.params.items()):
        if isinstance(v, float):
            print(f"    {k:30s} = {v:.6f}")
        else:
            print(f"    {k:30s} = {v}")
    print()

    # ── Run final evaluation with best params ────────────────────────────
    print("  Validating best params on 5 held-out seeds...")
    val_seeds = list(range(100, 105))
    val_cfg = replace(base_cfg, **{
        k: v for k, v in best.params.items()
        if k != "ema_span"
    }, ema_span=best.params.get("ema_span", base_cfg.ema_span))

    val_results = []
    for seed in val_seeds:
        ticks = generate_regime_switching_gbm(
            vol_weekly=args.vol, n_days=args.n_days,
            ticks_per_day=args.ticks_per_day, seed=seed,
        )
        runner = BacktestRunner(val_cfg)
        result = runner.run(ticks)
        metrics = compute_metrics(result, val_cfg)
        val_results.append(metrics)

    avg_return = np.mean([m["total_return_pct"] for m in val_results])
    avg_dd = np.mean([m["max_drawdown"] for m in val_results])
    avg_sortino = np.mean([m["sortino_ratio"] for m in val_results])
    avg_kills = np.mean([m["kill_switch_count"] for m in val_results])
    avg_equity = np.mean([m["final_equity"] for m in val_results])

    print()
    print("  Validation Results (5 held-out seeds):")
    print("  " + "-" * 40)
    print(f"    Avg Return:      {avg_return:+.2%}")
    print(f"    Avg Max DD:      {avg_dd:.2%}")
    print(f"    Avg Sortino:     {avg_sortino:.4f}")
    print(f"    Avg Kill Count:  {avg_kills:.1f}")
    print(f"    Avg Final Equity:{avg_equity:,.2f}")
    print()

    # ── Top 5 trials ─────────────────────────────────────────────────────
    print("  Top 5 Trials:")
    print("  " + "-" * 56)
    print(f"  {'#':>4}  {'Score':>10}  {'gamma':>8}  {'k':>6}  {'max_inv':>8}  {'dd_lim':>7}")
    for t in sorted(study.trials, key=lambda t: t.value if t.value is not None else -999, reverse=True)[:5]:
        if t.value is None:
            continue
        p = t.params
        print(
            f"  {t.number:>4}  {t.value:>10.4f}  "
            f"{p.get('gamma', 0):>8.4f}  {p.get('k', 0):>6.2f}  "
            f"{p.get('max_inventory', 0):>8.4f}  {p.get('max_drawdown_pct', 0):>7.2%}"
        )
    print()


if __name__ == "__main__":
    main()
