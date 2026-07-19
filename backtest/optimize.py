"""
backtest/optimize.py — Optuna hyperparameter optimization for the MM bot.

AGENTS.md §13 compliant:
  - fixed_lot_size=0.01 is NEVER optimized (invariant check enforced)
  - max_inventory is lot-based: trial.suggest_int("max_inventory_lots", 1, 3)
  - Robust objective: median_net_return - 2.5*worst_drawdown - ...
  - Default fill_mode is "probabilistic" (not "optimistic")
  - Train/validation/holdout seed splits
  - Artifacts written to artifacts/optuna/ — NEVER auto-applied to config

Usage::

    python -m backtest.optimize                              # 100 trials, 5 seeds
    python -m backtest.optimize --n-trials 300 --n-seeds 10  # full study
    python -m backtest.optimize --n-days 14 --fill-mode conservative
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

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
#  Objective function — AGENTS.md §13 compliant
# ══════════════════════════════════════════════════════════════════════════════

def objective(
    trial: optuna.Trial,
    base_cfg: Config,
    seeds: list[int],
    n_days: int,
    vol_weekly: float,
    ticks_per_day: int,
    fill_mode: str,
) -> float:
    """Evaluate a single parameter configuration across multiple seeds.

    Robust composite score (AGENTS.md §13.4):
        score = median_net_return
                - 2.5 * worst_max_drawdown
                - 1.5 * max(0, -worst_seed_return)
                + 0.10 * clipped_median_sortino
                - fee_penalty
                - kill_switch_penalty
    """

    # ── Invariant: fixed_lot_size must be 0.01 ───────────────────────────
    assert base_cfg.fixed_lot_size == 0.01, (
        f"fixed_lot_size must be 0.01, got {base_cfg.fixed_lot_size}"
    )

    # ── Sample hyperparameters ───────────────────────────────────────────
    # NEVER suggest fixed_lot_size or order_size_base
    gamma = trial.suggest_float("gamma", 0.01, 1.0, log=True)
    k = trial.suggest_float("k", 0.5, 5.0)
    tau = trial.suggest_float("tau", 0.5, 2.0)

    # Lot-based inventory (AGENTS.md §13.2)
    max_inventory_lots = trial.suggest_int("max_inventory_lots", 1, 3)

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
        max_inventory_lots=max_inventory_lots,
        max_drawdown_pct=max_drawdown_pct,
        trend_spread_multiplier=trend_spread_mult,
        trend_size_multiplier=trend_size_mult,
        range_spread_multiplier=range_spread_mult,
        imbalance_skew_factor=imbalance_skew,
        inventory_skew_factor=inventory_skew,
        ema_span=ema_span,
    )

    # ── Evaluate across multiple seeds ───────────────────────────────────
    all_returns: list[float] = []
    all_max_dd: list[float] = []
    all_sortino: list[float] = []
    all_fees: list[float] = []
    all_kill_counts: list[int] = []
    all_bid_fills: list[int] = []
    all_ask_fills: list[int] = []

    for i, seed in enumerate(seeds):
        ticks = generate_regime_switching_gbm(
            vol_weekly=vol_weekly,
            n_days=n_days,
            ticks_per_day=ticks_per_day,
            seed=seed,
        )

        runner = BacktestRunner(cfg, fill_mode=fill_mode)
        result = runner.run(ticks)
        metrics = compute_metrics(result, cfg)

        net_return = metrics.get("total_return_pct", -1.0)
        max_dd = metrics.get("max_drawdown", 1.0)
        sortino = metrics.get("sortino_ratio", 0.0)
        kill_count = metrics.get("kill_switch_count", 0)
        total_fees = metrics.get("total_fees", 0.0)

        # Hard constraints — prune obviously bad trials (AGENTS.md §13.4)
        final_equity = metrics.get("final_equity", 0.0)
        if final_equity <= 0:
            raise optuna.TrialPruned("Final equity <= 0")
        if kill_count > 5:
            raise optuna.TrialPruned(f"Excessive kill-switch count: {kill_count}")

        bid_fills = metrics.get("bid_fills", 0)
        ask_fills = metrics.get("ask_fills", 0)
        if bid_fills + ask_fills < 5:
            raise optuna.TrialPruned("Insufficient total fills (<5)")

        all_returns.append(net_return)
        all_max_dd.append(max_dd)
        all_sortino.append(max(min(sortino, 10.0), -10.0))
        all_fees.append(total_fees)
        all_kill_counts.append(kill_count)
        all_bid_fills.append(bid_fills)
        all_ask_fills.append(ask_fills)

        # Report intermediate value for pruning (median so far)
        trial.report(float(np.median(all_returns)), i)
        if trial.should_prune():
            raise optuna.TrialPruned()

    # ── Robust composite score (AGENTS.md §13.4) ────────────────────────
    median_return = float(np.median(all_returns))
    worst_return = float(np.min(all_returns))
    worst_dd = float(np.max(all_max_dd))
    median_sortino = float(np.median(all_sortino))
    total_kills = sum(all_kill_counts)
    mean_fees = float(np.mean(all_fees))

    # Fee penalty relative to initial capital
    fee_penalty = mean_fees / max(cfg.initial_capital, 1.0)

    # Two-sided quoting check: penalize heavily one-sided bots
    min_bid = min(all_bid_fills) if all_bid_fills else 0
    min_ask = min(all_ask_fills) if all_ask_fills else 0
    two_sided_penalty = 0.0
    if min_bid < 2 or min_ask < 2:
        two_sided_penalty = 0.5  # Heavy penalty for not quoting both sides

    score = (
        median_return
        - 2.5 * worst_dd
        - 1.5 * max(0.0, -worst_return)
        + 0.10 * min(median_sortino, 5.0)  # clipped
        - fee_penalty
        - 0.1 * total_kills
        - two_sided_penalty
    )

    return float(score)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optuna hyperparameter optimization for the MM bot backtest"
    )
    parser.add_argument("--n-trials", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--n-seeds", type=int, default=5, help="Number of random seeds per trial")
    parser.add_argument("--n-days", type=int, default=7, help="Days per scenario")
    parser.add_argument("--vol", type=float, default=0.25, help="Weekly volatility")
    parser.add_argument("--ticks-per-day", type=int, default=288, help="Ticks per day")
    parser.add_argument(
        "--fill-mode", type=str, default="probabilistic",
        choices=["optimistic", "probabilistic", "conservative"],
        help="Matching engine fill mode (default: probabilistic)",
    )
    parser.add_argument("--study-name", type=str, default="mm-bot-300usdt-fixed-lot", help="Optuna study name")
    parser.add_argument("--db", type=str, default=None, help="SQLite DB path (e.g. optuna_300usdt.db)")
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

    # ── Seed splits (AGENTS.md §13.5) ────────────────────────────────────
    # Train seeds: used during optimization
    # Validation seeds: used for final validation
    train_seeds = list(range(42, 42 + args.n_seeds))
    val_seeds = list(range(200, 200 + args.n_seeds))
    holdout_seeds = list(range(500, 500 + max(5, args.n_seeds)))

    # ── Create study ─────────────────────────────────────────────────────
    storage = f"sqlite:///{args.db}" if args.db else None
    study = optuna.create_study(
        study_name=args.study_name,
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=1),
        sampler=optuna.samplers.TPESampler(seed=42),  # Reproducible
    )

    print("=" * 60)
    print("  Optuna Hyperparameter Optimization")
    print(f"  Trials: {args.n_trials}  Seeds: {args.n_seeds}  Days: {args.n_days}")
    print(f"  Vol: {args.vol}  Fill Mode: {args.fill_mode}")
    print(f"  Capital: {base_cfg.initial_capital}  Lot: {base_cfg.fixed_lot_size}")
    print(f"  Study: {args.study_name}")
    print("=" * 60)
    print()

    t0 = time.time()

    study.optimize(
        lambda trial: objective(
            trial, base_cfg, train_seeds, args.n_days, args.vol,
            args.ticks_per_day, args.fill_mode,
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
    for param_key, v in sorted(best.params.items()):
        if isinstance(v, float):
            print(f"    {param_key:30s} = {v:.6f}")
        else:
            print(f"    {param_key:30s} = {v}")
    print()

    # ── Validation on held-out seeds ─────────────────────────────────────
    print(f"  Validating best params on {len(val_seeds)} held-out seeds...")

    # Build validation config — only set params that exist in Config
    valid_params = {}
    for param_key, v in best.params.items():
        if hasattr(base_cfg, param_key):
            valid_params[param_key] = v
    val_cfg = replace(base_cfg, **valid_params)

    val_results = []
    for seed in val_seeds:
        ticks = generate_regime_switching_gbm(
            vol_weekly=args.vol, n_days=args.n_days,
            ticks_per_day=args.ticks_per_day, seed=seed,
        )
        runner = BacktestRunner(val_cfg, fill_mode=args.fill_mode)
        result = runner.run(ticks)
        metrics = compute_metrics(result, val_cfg)
        val_results.append(metrics)

    avg_return = np.mean([m["total_return_pct"] for m in val_results])
    avg_dd = np.mean([m["max_drawdown"] for m in val_results])
    avg_sortino = np.mean([m["sortino_ratio"] for m in val_results])
    avg_kills = np.mean([m["kill_switch_count"] for m in val_results])
    avg_equity = np.mean([m["final_equity"] for m in val_results])
    avg_fees = np.mean([m.get("total_fees", 0.0) for m in val_results])

    print()
    print(f"  Validation Results ({len(val_seeds)} held-out seeds):")
    print("  " + "-" * 40)
    print(f"    Avg Return:      {avg_return:+.2%}")
    print(f"    Avg Max DD:      {avg_dd:.2%}")
    print(f"    Avg Sortino:     {avg_sortino:.4f}")
    print(f"    Avg Kill Count:  {avg_kills:.1f}")
    print(f"    Avg Final Equity:{avg_equity:,.2f}")
    print(f"    Avg Fees:        {avg_fees:.4f}")
    print()

    # ── Write artifacts (NEVER modify config.py) ─────────────────────────
    artifacts_dir = os.path.join(_PROJECT_ROOT, "artifacts", "optuna")
    os.makedirs(artifacts_dir, exist_ok=True)

    # Best params JSON
    params_path = os.path.join(artifacts_dir, "best_params_300usdt.json")
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump({
            "trial_number": best.number,
            "score": best.value,
            "params": best.params,
            "fixed_lot_size": 0.01,
            "initial_capital": 300.0,
            "fill_mode": args.fill_mode,
            "n_trials": args.n_trials,
            "n_seeds": args.n_seeds,
            "n_days": args.n_days,
        }, f, indent=2)
    print(f"  ✅ Best params saved to: {params_path}")

    # Validation report JSON
    val_path = os.path.join(artifacts_dir, "validation_report.json")
    with open(val_path, "w", encoding="utf-8") as f:
        json.dump({
            "validation_seeds": val_seeds,
            "avg_return": float(avg_return),
            "avg_max_drawdown": float(avg_dd),
            "avg_sortino": float(avg_sortino),
            "avg_kill_count": float(avg_kills),
            "avg_final_equity": float(avg_equity),
            "avg_fees": float(avg_fees),
        }, f, indent=2)
    print(f"  ✅ Validation report saved to: {val_path}")

    # Trials CSV
    try:
        trials_df = study.trials_dataframe()
        csv_path = os.path.join(artifacts_dir, "trials.csv")
        trials_df.to_csv(csv_path, index=False)
        print(f"  ✅ All trials saved to: {csv_path}")
    except Exception as e:
        print(f"  ⚠ Could not save trials CSV: {e}")

    print()
    print("  ⚠ These parameters are NOT auto-applied to config.py.")
    print("  To apply, manually update config.py with the best params above.")
    print()

    # ── Top 5 trials ─────────────────────────────────────────────────────
    print("  Top 5 Trials:")
    print("  " + "-" * 56)
    print(f"  {'#':>4}  {'Score':>10}  {'gamma':>8}  {'k':>6}  {'inv_lots':>8}  {'dd_lim':>7}")
    for t in sorted(study.trials, key=lambda t: t.value if t.value is not None else -999, reverse=True)[:5]:
        if t.value is None:
            continue
        p = t.params
        print(
            f"  {t.number:>4}  {t.value:>10.4f}  "
            f"{p.get('gamma', 0):>8.4f}  {p.get('k', 0):>6.2f}  "
            f"{p.get('max_inventory_lots', 0):>8}  {p.get('max_drawdown_pct', 0):>7.2%}"
        )
    print()


if __name__ == "__main__":
    main()
