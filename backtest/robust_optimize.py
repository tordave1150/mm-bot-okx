"""
backtest/robust_optimize.py — Survival-first robust Optuna optimization.

AGENTS.md §9 compliant:
  - fixed_lot_size=0.01, max_inventory_lots=1 are invariants (never optimized)
  - max_drawdown_pct and leverage are never optimized
  - capital is an experiment input, not a strategy parameter
  - Disjoint train/validation/holdout seed groups
  - Regime matrix: 7 scenario types
  - Promotion requires 100% validation AND 100% holdout pass rates
  - Persistent SQLite study with load_if_exists=True
  - All artifacts written to output_dir; config.py never modified automatically

Tunable parameters (AGENTS.md §8):
  gamma                    log-uniform 0.01-0.50
  k                        uniform     0.80-5.00
  tau                      uniform     0.50-1.50
  trend_spread_multiplier  uniform     1.20-3.50
  trend_size_multiplier    uniform     0.20-0.80
  range_spread_multiplier  uniform     0.70-1.30
  imbalance_skew_factor    uniform     0.00-0.80
  inventory_skew_factor    uniform     0.50-2.50
  ema_span                 integer     12-40

Usage::

    python -m backtest.robust_optimize --help
    python -m backtest.robust_optimize --n-trials 50 --output-dir artifacts/robust_optuna/smoke
    python -m backtest.robust_optimize \\
        --n-trials 500 --top-k 20 \\
        --capitals 300,500,750,1000 --leverage 1 --risk-dd 0.031 \\
        --train-days 7 --validation-days 30 \\
        --train-seeds 10 --validation-seeds 10 --holdout-seeds 20 \\
        --db artifacts/optuna/robust.db \\
        --output-dir artifacts/robust_optuna/main_run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import optuna
from optuna.pruners import MedianPruner

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config, load_config, _validate_config
from backtest.runner import BacktestRunner
from backtest.synthetic_data import generate_regime_switching_gbm, generate_block_bootstrap
from backtest.metrics import REQUIRED_METRIC_KEYS, compute_metrics, validate_metric_schema

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Seed groups — must be disjoint (AGENTS.md §9.1)
_TRAIN_SEED_START = 42
_VAL_SEED_START = 200
_HOLDOUT_SEED_START = 500

# Hard gate constants (AGENTS.md §10.1–§10.3)
_MAX_MARGIN_UTIL = 0.80
_NON_CRASH_MAX_DD = 0.05
_NON_CRASH_CVAR95_MAX = 0.08   # positive loss magnitude
_CRASH_MAX_DD = 0.15
_POST_KILL_PCT_MAX = 0.02      # 2% of initial capital

# Scoring weights (AGENTS.md §11)
_W_MEDIAN = 1.00
_W_P10 = 0.50
_W_WORST = 0.25
_W_DD = 2.00


# ── Capital Feasibility ───────────────────────────────────────────────────────

def check_capital_feasibility(
    capital: float,
    leverage: float,
    fixed_lot_size: float,
    btc_price: float = 50_000.0,
    margin_util_limit: float = _MAX_MARGIN_UTIL,
) -> str | None:
    """Return INFEASIBLE reason string or None if feasible.

    At 1x leverage with fixed_lot_size=0.01 and BTC at ~$50,000:
        notional = 50,000 * 0.01 = $500
        required_margin = $500 / leverage
        max_allowed_margin = capital * margin_util_limit

    For 300 USDT: max_allowed = $240, required = $500 → INFEASIBLE.
    For 500 USDT: max_allowed = $400, required = $500 → INFEASIBLE.
    For 750 USDT: max_allowed = $600, required = $500 → FEASIBLE.
    For 1000 USDT: max_allowed = $800, required = $500 → FEASIBLE.
    """
    notional = btc_price * fixed_lot_size
    required_margin = notional / max(leverage, 1.0)
    max_allowed = capital * margin_util_limit
    if required_margin > max_allowed:
        return (
            f"capital={capital} USDT insufficient: at BTC=${btc_price:,.0f}, "
            f"lot={fixed_lot_size}, {leverage}x leverage → notional=${notional:,.0f}, "
            f"margin=${required_margin:,.0f} > max_allowed=${max_allowed:,.0f} "
            f"({margin_util_limit:.0%} util limit)"
        )
    return None


# ── Regime Scenario Matrix ────────────────────────────────────────────────────

def _build_scenario_matrix(
    train_seeds: list[int],
    val_seeds: list[int],
    holdout_seeds: list[int],
    train_days: int,
    val_days: int,
    ticks_per_day: int,
) -> dict[str, list[dict]]:
    """Build the regime scenario matrix (AGENTS.md §9.2).

    Returns dict keyed by scenario name with list of tick-sequence generators.
    Each entry is a dict with: name, seeds, n_days, generator_fn, is_crash, ticks_per_day.
    """
    scenarios = []

    def _gbm(vol_weekly, jump_freq=0.2, jump_size=0.02):
        return dict(
            generator="gbm",
            vol_weekly=vol_weekly,
            jump_freq=jump_freq,
            jump_size=jump_size,
        )

    # AGENTS.md §9.2 regime matrix
    regime_matrix = [
        # Normal scenarios (non-crash)
        dict(name="quiet",          params=_gbm(0.12),            is_crash=False),
        dict(name="normal",         params=_gbm(0.25),            is_crash=False),
        dict(name="high_vol",       params=_gbm(0.40),            is_crash=False),
        dict(name="jump_high_vol",  params=_gbm(0.40, 1.0, 0.04), is_crash=False),
        # Crash scenarios
        dict(name="bear_bootstrap", params=dict(generator="bootstrap",
             block_libraries=["BTC_2022_BEAR"]), is_crash=True),
        dict(name="crash_bootstrap", params=dict(generator="bootstrap",
             block_libraries=["BTC_2022_BEAR", "BTC_CRASH"]), is_crash=True),
        dict(name="flash_crash",    params=_gbm(0.80, 3.0, 0.08), is_crash=True),
    ]

    for regime in regime_matrix:
        scenarios.append({
            "name": regime["name"],
            "is_crash": regime["is_crash"],
            "params": regime["params"],
            "train_seeds": train_seeds,
            "val_seeds": val_seeds,
            "holdout_seeds": holdout_seeds,
            "train_days": train_days,
            "val_days": val_days,
            "ticks_per_day": ticks_per_day,
        })

    return scenarios


def _generate_ticks(scenario_params: dict, seed: int, n_days: int, ticks_per_day: int) -> list[dict]:
    """Generate tick sequence for a scenario."""
    generator = scenario_params.get("generator", "gbm")

    if generator == "gbm":
        return generate_regime_switching_gbm(
            vol_weekly=scenario_params["vol_weekly"],
            n_days=n_days,
            ticks_per_day=ticks_per_day,
            seed=seed,
            jump_freq=scenario_params.get("jump_freq", 0.2),
            jump_size=scenario_params.get("jump_size", 0.02),
        )
    elif generator == "bootstrap":
        return generate_block_bootstrap(
            block_libraries=scenario_params["block_libraries"],
            n_days=n_days,
            ticks_per_day=ticks_per_day,
            seed=seed,
        )
    else:
        raise ValueError(f"Unknown generator: {generator}")


# ── Gate Checks ───────────────────────────────────────────────────────────────

def _check_hard_gates(metrics: dict, initial_capital: float) -> str | None:
    """Return failure reason string or None if all hard gates pass (AGENTS.md §10.1)."""
    try:
        validate_metric_schema(metrics)
    except (KeyError, ValueError) as exc:
        return str(exc)

    final_eq = metrics["final_equity_usdt"]
    if final_eq <= 0:
        return f"final_equity <= 0: {final_eq:.4f}"
    if metrics["min_equity_usdt"] <= 0:
        return f"equity <= 0 during run: {metrics['min_equity_usdt']:.4f}"

    total_ret = metrics["total_return"]
    if not math.isfinite(total_ret):
        return f"total_return_pct is NaN/inf: {total_ret}"

    max_dd = metrics["max_drawdown"]
    if not math.isfinite(max_dd):
        return f"max_drawdown is NaN/inf: {max_dd}"

    for key in ["cvar_95", "var_95", "sortino_ratio"]:
        v = metrics[key]
        if not math.isfinite(v):
            return f"{key} is NaN/inf: {v}"

    # Margin utilization gate
    margin_util = metrics["max_margin_utilization"]
    if margin_util > _MAX_MARGIN_UTIL:
        return f"margin_utilization {margin_util:.1%} > {_MAX_MARGIN_UTIL:.0%}"

    if metrics["invalid_order_count"]:
        return f"invalid orders rejected: {metrics['invalid_order_count']}"
    if metrics["max_abs_inventory_lots"] > 1.0 + 1e-9:
        return f"inventory hard limit breached: {metrics['max_abs_inventory_lots']:.6f} lots"
    if abs(metrics["ending_inventory_base"]) > 1e-9:
        return f"ending inventory not flattened: {metrics['ending_inventory_base']:.12f}"

    return None


def _check_non_crash_gates(metrics: dict, initial_capital: float) -> str | None:
    """Non-crash scenario gates (AGENTS.md §10.2)."""
    hard_fail = _check_hard_gates(metrics, initial_capital)
    if hard_fail:
        return hard_fail

    max_dd = metrics["max_drawdown"]
    if max_dd > _NON_CRASH_MAX_DD:
        return f"max_drawdown {max_dd:.2%} > {_NON_CRASH_MAX_DD:.2%}"

    cvar_95 = metrics["cvar_95"]
    if cvar_95 > _NON_CRASH_CVAR95_MAX:
        return f"cvar_95 {cvar_95:.4f} > {_NON_CRASH_CVAR95_MAX}"

    kill_count = metrics["kill_switch_count"]
    if kill_count > 0:
        return f"kill_switch triggered ({kill_count}x) in non-crash scenario"

    bid_fills = metrics.get("bid_fills", 0)
    ask_fills = metrics.get("ask_fills", 0)
    if bid_fills < 1 or ask_fills < 1:
        return f"insufficient two-sided fills: bid={bid_fills}, ask={ask_fills}"
    if metrics["completed_round_trips"] < 1:
        return "no completed maker-supported round trip"

    return None


def _check_crash_gates(metrics: dict, initial_capital: float) -> str | None:
    """Crash scenario gates (AGENTS.md §10.3)."""
    hard_fail = _check_hard_gates(metrics, initial_capital)
    if hard_fail:
        return hard_fail

    max_dd = metrics.get("max_drawdown", 1.0)
    if max_dd > _CRASH_MAX_DD:
        return f"max_drawdown {max_dd:.2%} > {_CRASH_MAX_DD:.2%}"

    kill_count = metrics["kill_switch_count"]
    if kill_count > 1:
        return (
            "kill-switch count must be at most 1 in crash scenario, got "
            f"{kill_count}"
        )
    if max_dd >= 0.031 and kill_count != 1:
        return "drawdown reached trigger without exactly one kill-switch activation"
    if kill_count == 1:
        events = metrics.get("kill_switch_events", [])
        if (
            not events
            or abs(float(events[0].get("post_flatten_inventory", math.inf))) > 1e-9
        ):
            return "kill-switch did not confirm flattened inventory"

        post_kill_pct = metrics.get("post_kill_deterioration_pct", 0.0)
        if post_kill_pct > _POST_KILL_PCT_MAX:
            return (
                f"post_kill_deterioration {post_kill_pct:.2%} "
                f"> {_POST_KILL_PCT_MAX:.2%}"
            )

    return None


def _check_scenario_gates(
    metrics: dict, is_crash: bool, initial_capital: float
) -> str | None:
    """Dispatch to correct gate checker."""
    if is_crash:
        return _check_crash_gates(metrics, initial_capital)
    return _check_non_crash_gates(metrics, initial_capital)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _compute_score(scenario_results: list[dict], initial_capital: float) -> float:
    """Composite robust score for train-feasible trials (AGENTS.md §11).

    score = median_return
           + 0.50 * p10_return
           + 0.25 * worst_return
           - 2.00 * worst_drawdown
           - normalized_fee_penalty
    """
    if not scenario_results:
        return -999.0

    returns = [r["total_return_pct"] for r in scenario_results]
    drawdowns = [r["max_drawdown"] for r in scenario_results]
    fees = [r.get("total_fees", 0.0) for r in scenario_results]

    median_return = float(np.median(returns))
    p10_return = float(np.percentile(returns, 10))
    worst_return = float(np.min(returns))
    worst_dd = float(np.max(drawdowns))
    mean_fees = float(np.mean(fees))

    fee_penalty = mean_fees / max(initial_capital, 1.0)

    score = (
        _W_MEDIAN * median_return
        + _W_P10 * p10_return
        + _W_WORST * worst_return
        - _W_DD * worst_dd
        - fee_penalty
    )
    return float(score)


# ── Objective Function ────────────────────────────────────────────────────────

def _build_trial_cfg(trial: optuna.Trial, base_cfg: Config) -> Config:
    """Sample hyperparameters and build a trial Config. Invariants are never optimized."""
    # AGENTS.md §7: fixed_lot_size=0.01, max_inventory_lots=1, never optimize
    # max_drawdown_pct or leverage
    assert base_cfg.fixed_lot_size == 0.01, "fixed_lot_size invariant violated"
    assert base_cfg.max_inventory_lots == 1, "max_inventory_lots must be 1 during search"

    gamma = trial.suggest_float("gamma", 0.01, 0.50, log=True)
    k = trial.suggest_float("k", 0.80, 5.00)
    tau = trial.suggest_float("tau", 0.50, 1.50)
    trend_spread_mult = trial.suggest_float("trend_spread_multiplier", 1.20, 3.50)
    trend_size_mult = trial.suggest_float("trend_size_multiplier", 0.20, 0.80)
    range_spread_mult = trial.suggest_float("range_spread_multiplier", 0.70, 1.30)
    imbalance_skew = trial.suggest_float("imbalance_skew_factor", 0.00, 0.80)
    inventory_skew = trial.suggest_float("inventory_skew_factor", 0.50, 2.50)
    ema_span = trial.suggest_int("ema_span", 12, 40)

    cfg = replace(
        base_cfg,
        gamma=gamma,
        k=k,
        tau=tau,
        trend_spread_multiplier=trend_spread_mult,
        trend_size_multiplier=trend_size_mult,
        range_spread_multiplier=range_spread_mult,
        imbalance_skew_factor=imbalance_skew,
        inventory_skew_factor=inventory_skew,
        ema_span=ema_span,
        # Fixed invariants (AGENTS.md §7)
        fixed_lot_size=0.01,
        max_inventory_lots=1,
        # max_drawdown_pct intentionally NOT overridden
    )
    _validate_config(cfg)
    return cfg


def _make_objective(
    base_cfg: Config,
    scenarios: list[dict],
    fill_mode_train: str,
    ticks_per_day: int,
):
    """Return the Optuna objective function (closure over base_cfg and scenarios)."""

    def objective(trial: optuna.Trial) -> float:
        try:
            cfg = _build_trial_cfg(trial, base_cfg)
        except Exception as e:
            trial.set_user_attr("failure", f"config_build: {e}")
            raise optuna.TrialPruned(f"Config build failed: {e}")

        all_results: list[dict] = []
        scenario_step = 0

        for scenario in scenarios:
            scenario_name = scenario["name"]
            is_crash = scenario["is_crash"]
            params = scenario["params"]
            seeds = scenario["train_seeds"]
            n_days = scenario["train_days"]
            run_cfg = replace(
                cfg, initial_capital=float(scenario.get("capital", cfg.initial_capital))
            )

            for seed in seeds:
                scenario_step += 1
                try:
                    ticks = _generate_ticks(params, seed, n_days, ticks_per_day)
                    runner = BacktestRunner(
                        run_cfg,
                        fill_mode=fill_mode_train,
                        fill_seed=seed,  # deterministic
                    )
                    result = runner.run(ticks)
                    metrics = compute_metrics(result, run_cfg)
                except Exception as e:
                    trial.set_user_attr("failure", f"{scenario_name}/{seed}: {e}")
                    raise optuna.TrialPruned(f"Runner exception: {e}")

                # Hard gates — prune immediately on failure (AGENTS.md §10)
                gate_fail = _check_scenario_gates(
                    metrics, is_crash, run_cfg.initial_capital
                )
                if gate_fail:
                    trial.set_user_attr("failure", f"{scenario_name}/{seed}: {gate_fail}")
                    raise optuna.TrialPruned(f"Hard gate fail: {gate_fail}")

                all_results.append({
                    "scenario": scenario_name,
                    "seed": seed,
                    "is_crash": is_crash,
                    "capital": run_cfg.initial_capital,
                    **{k: v for k, v in metrics.items() if not isinstance(v, list)},
                })

                # Intermediate reporting for pruner
                if all_results:
                    returns_so_far = [r["total_return_pct"] for r in all_results
                                      if not r["is_crash"]]
                    if returns_so_far:
                        trial.report(float(np.median(returns_so_far)), scenario_step)
                        if trial.should_prune():
                            raise optuna.TrialPruned()

        # ── Compute robust score from non-crash train scenarios ──────────────
        non_crash = [r for r in all_results if not r["is_crash"]]
        if not non_crash:
            raise optuna.TrialPruned("No non-crash scenarios completed")

        score = _compute_score(non_crash, cfg.initial_capital)
        return float(score)

    return objective


# ── Validation / Holdout Evaluation ──────────────────────────────────────────

def _evaluate_candidate(
    cfg: Config,
    scenarios: list[dict],
    seed_key: str,           # "val_seeds" or "holdout_seeds"
    n_days_key: str,         # "val_days" or "val_days" (holdout uses val_days length)
    fill_modes: list[str],
    ticks_per_day: int,
) -> dict[str, Any]:
    """Evaluate a config on val or holdout seeds across all scenarios and fill modes.

    Returns summary dict with pass_rate, per-scenario results, etc.
    """
    scenario_results = []
    passes = 0
    total = 0

    for scenario in scenarios:
        scenario_name = scenario["name"]
        is_crash = scenario["is_crash"]
        params = scenario["params"]
        seeds = scenario[seed_key]
        n_days = scenario[n_days_key]

        for fill_mode in fill_modes:
            for seed in seeds:
                fill_seeds = [seed, seed + 10_000] if fill_mode == "probabilistic" else [seed]
                ticks = _generate_ticks(params, seed, n_days, ticks_per_day)
                for fill_seed in fill_seeds:
                    total += 1
                    try:
                        runner = BacktestRunner(cfg, fill_mode=fill_mode, fill_seed=fill_seed)
                        result = runner.run(ticks)
                        metrics = compute_metrics(result, cfg)
                        gate_fail = _check_scenario_gates(metrics, is_crash, cfg.initial_capital)
                        passed = gate_fail is None
                        if passed:
                            passes += 1
                    except Exception:
                        gate_fail = f"exception: {traceback.format_exc()}"
                        passed = False
                        metrics = {}

                    scenario_results.append({
                        "scenario": scenario_name, "seed": seed,
                        "fill_seed": fill_seed, "fill_mode": fill_mode,
                        "is_crash": is_crash, "passed": passed,
                        "failure": gate_fail if not passed else None,
                        "total_return_pct": metrics.get("total_return_pct", float("nan")),
                        "max_drawdown": metrics.get("max_drawdown", float("nan")),
                        "cvar_95": metrics.get("cvar_95", float("nan")),
                        "total_fees": metrics.get("total_fees", 0.0),
                        "bid_fills": metrics.get("bid_fills", 0),
                        "ask_fills": metrics.get("ask_fills", 0),
                        "completed_round_trips": metrics.get("completed_round_trips", 0),
                        "kill_switch_count": metrics.get("kill_switch_count", 0),
                        "post_kill_deterioration_pct": metrics.get("post_kill_deterioration_pct", 0.0),
                        "max_margin_utilization": metrics.get("max_margin_utilization", float("nan")),
                        "invalid_order_count": metrics.get("invalid_order_count", 0),
                        "local_risk_rejection_count": metrics.get("local_risk_rejection_count", 0),
                        "ending_inventory_base": metrics.get("ending_inventory_base", float("nan")),
                    })

    pass_rate = passes / max(total, 1)

    returns = [r["total_return_pct"] for r in scenario_results
               if math.isfinite(r["total_return_pct"])]
    drawdowns = [r["max_drawdown"] for r in scenario_results
                 if math.isfinite(r["max_drawdown"])]

    return {
        "pass_rate": pass_rate,
        "passes": passes,
        "total": total,
        "scenario_results": scenario_results,
        "median_return": float(np.median(returns)) if returns else float("nan"),
        "p10_return": float(np.percentile(returns, 10)) if returns else float("nan"),
        "worst_return": float(np.min(returns)) if returns else float("nan"),
        "worst_drawdown": float(np.max(drawdowns)) if drawdowns else float("nan"),
    }


# ── Validation Re-ranking ─────────────────────────────────────────────────────

def _evaluate_candidate_across_capitals(
    cfg: Config,
    capitals: list[float],
    scenarios: list[dict],
    seed_key: str,
    n_days_key: str,
    fill_modes: list[str],
    ticks_per_day: int,
) -> dict[str, Any]:
    """Require one parameter set to pass independently at every capital."""
    combined: list[dict] = []
    passes = 0
    total = 0
    capital_summaries: dict[str, dict] = {}
    for capital in capitals:
        summary = _evaluate_candidate(
            cfg=replace(cfg, initial_capital=capital), scenarios=scenarios,
            seed_key=seed_key, n_days_key=n_days_key, fill_modes=fill_modes,
            ticks_per_day=ticks_per_day,
        )
        for row in summary["scenario_results"]:
            row["capital"] = capital
        combined.extend(summary["scenario_results"])
        passes += summary["passes"]
        total += summary["total"]
        capital_summaries[str(capital)] = {
            key: value for key, value in summary.items() if key != "scenario_results"
        }
    returns = [r["total_return_pct"] for r in combined if math.isfinite(r["total_return_pct"])]
    drawdowns = [r["max_drawdown"] for r in combined if math.isfinite(r["max_drawdown"])]
    normal = [r for r in combined if not r["is_crash"]]
    return {
        "pass_rate": passes / max(total, 1),
        "normal_pass_rate": sum(bool(r["passed"]) for r in normal) / max(len(normal), 1),
        "passes": passes, "total": total, "scenario_results": combined,
        "capital_summaries": capital_summaries,
        "median_return": float(np.median(returns)) if returns else float("nan"),
        "p10_return": float(np.percentile(returns, 10)) if returns else float("nan"),
        "worst_return": float(np.min(returns)) if returns else float("nan"),
        "worst_drawdown": float(np.max(drawdowns)) if drawdowns else float("nan"),
        "total_fees": float(sum(r.get("total_fees", 0.0) for r in combined)),
    }


def _validation_key(val_summary: dict) -> tuple:
    """Lexicographic ranking key for validation (AGENTS.md §11)."""
    # Lower is better for tuple sorting (we negate where higher is better)
    all_pass = 1 if val_summary["pass_rate"] == 1.0 else 0
    pass_rate = val_summary["pass_rate"]
    normal_pass_rate = val_summary.get("normal_pass_rate", 0.0)
    worst_dd = val_summary.get("worst_drawdown", 1.0)
    p10 = val_summary.get("p10_return", -1.0)
    median = val_summary.get("median_return", -1.0)
    fees = val_summary.get("total_fees", math.inf)

    return (
        -all_pass,          # 1. all pass (descending)
        -pass_rate,         # 2. pass rate (descending)
        -normal_pass_rate,
        worst_dd,           # 3. worst drawdown (ascending — lower is better)
        -p10,               # 4. p10 return (descending)
        -median,            # 5. median return (descending)
        fees,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Survival-first robust Optuna optimization (AGENTS.md §9)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--n-trials", type=int, default=50,
                        help="Number of Optuna trials")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Top-K train-feasible trials to validate")
    parser.add_argument("--capitals", type=str, default="300,500,750,1000",
                        help="Comma-separated capital sizes in USDT")
    parser.add_argument("--leverage", type=float, default=1.0,
                        help="Leverage (AGENTS.md §7: never optimize)")
    parser.add_argument("--risk-dd", type=float, default=0.031,
                        help="max_drawdown_pct kill-switch threshold (never optimized)")
    parser.add_argument("--train-days", type=int, default=7,
                        help="Days per train scenario")
    parser.add_argument("--validation-days", type=int, default=30,
                        help="Days per validation/holdout scenario")
    parser.add_argument("--train-seeds", type=int, default=5,
                        help="Number of train seeds per scenario")
    parser.add_argument("--validation-seeds", type=int, default=5,
                        help="Number of validation seeds per scenario")
    parser.add_argument("--holdout-seeds", type=int, default=10,
                        help="Number of holdout seeds per scenario")
    parser.add_argument("--ticks-per-day", type=int, default=288)
    parser.add_argument("--study-name", type=str, default="robust-mm-bot",
                        help="Optuna study name")
    parser.add_argument("--db", type=str, default=None,
                        help="SQLite DB path (e.g. artifacts/optuna/robust.db)")
    parser.add_argument("--output-dir", type=str, default="artifacts/robust_optuna/run",
                        help="Output directory for artifacts")
    parser.add_argument("--fill-mode-train", type=str, default="probabilistic",
                        choices=["optimistic", "probabilistic", "conservative"],
                        help="Fill mode for training (optimistic never used for promotion)")
    parser.add_argument("--btc-price", type=float, default=50_000.0,
                        help="BTC price for capital feasibility check")
    parser.add_argument("--no-holdout", action="store_true",
                        help="Skip holdout evaluation (smoke test mode)")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    if abs(args.risk_dd - 0.031) > 1e-12:
        raise SystemExit("Promotion protocol requires --risk-dd 0.031")
    if abs(args.leverage - 1.0) > 1e-12:
        raise SystemExit("Main protocol requires --leverage 1")
    if args.fill_mode_train == "optimistic":
        raise SystemExit("Optimistic fills are diagnostic-only and cannot train candidates")

    # ── Logging ──────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    logging.getLogger("backtest").setLevel(logging.WARNING)
    logging.getLogger("config").setLevel(logging.WARNING)

    # ── Output directory ──────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command_text = "python -m backtest.robust_optimize " + " ".join(sys.argv[1:])
    (output_dir / "commands.txt").write_text(command_text + "\n", encoding="utf-8")
    (output_dir / "test_results.txt").write_text(
        "Tests are run and recorded by the invoking verification workflow.\n",
        encoding="utf-8",
    )

    # ── Capital feasibility pre-check (AGENTS.md §9.3) ───────────────────────
    capitals = [float(c.strip()) for c in args.capitals.split(",")]
    feasible_capitals: list[float] = []
    infeasible_capitals: dict[float, str] = {}

    print("\n" + "=" * 70)
    print("  Robust Optuna Optimizer — AGENTS.md §9 Compliant")
    print("=" * 70)
    print(f"\n  Fixed invariants:")
    print(f"    fixed_lot_size    = 0.01")
    print(f"    max_inventory_lots = 1")
    print(f"    max_drawdown_pct  = {args.risk_dd:.3f} (never optimized)")
    print(f"    leverage          = {args.leverage} (never optimized)")
    print(f"\n  Capital feasibility check (BTC=${args.btc_price:,.0f}, {args.leverage}x leverage):")

    for cap in capitals:
        reason = check_capital_feasibility(
            capital=cap,
            leverage=args.leverage,
            fixed_lot_size=0.01,
            btc_price=args.btc_price,
        )
        if reason:
            infeasible_capitals[cap] = reason
            print(f"    {cap:>8.0f} USDT  →  INFEASIBLE: {reason}")
        else:
            feasible_capitals.append(cap)
            print(f"    {cap:>8.0f} USDT  →  FEASIBLE")

    if not feasible_capitals:
        print("\n  ❌ No feasible capital sizes — aborting.")
        report = {
            "status": "REJECTED",
            "reason": "All capitals INFEASIBLE",
            "infeasible_capitals": {str(k): v for k, v in infeasible_capitals.items()},
            "supported_capitals": [],
        }
        _write_json(output_dir / "report.json", report)
        return

    # Use the SMALLEST feasible capital for optimization (most conservative)
    base_capital = min(feasible_capitals)
    print(f"\n  Using capital={base_capital} USDT for optimization")

    # ── Load base config ──────────────────────────────────────────────────────
    base_cfg = load_config(
        initial_capital=base_capital,
        max_drawdown_pct=args.risk_dd,
        max_inventory_lots=1,
        leverage=args.leverage,
    )
    assert base_cfg.fixed_lot_size == 0.01, "fixed_lot_size invariant violated"

    # ── Seed groups ───────────────────────────────────────────────────────────
    train_seeds = list(range(_TRAIN_SEED_START, _TRAIN_SEED_START + args.train_seeds))
    val_seeds = list(range(_VAL_SEED_START, _VAL_SEED_START + args.validation_seeds))
    holdout_seeds = list(range(_HOLDOUT_SEED_START, _HOLDOUT_SEED_START + args.holdout_seeds))

    # Verify disjoint
    assert not (set(train_seeds) & set(val_seeds)), "Train/val seeds overlap!"
    assert not (set(train_seeds) & set(holdout_seeds)), "Train/holdout seeds overlap!"
    assert not (set(val_seeds) & set(holdout_seeds)), "Val/holdout seeds overlap!"

    print(f"\n  Seed groups:")
    print(f"    Train:    {train_seeds}")
    print(f"    Val:      {val_seeds}")
    print(f"    Holdout:  {holdout_seeds[:5]}{'...' if len(holdout_seeds) > 5 else ''}")

    # ── Build scenario matrix ─────────────────────────────────────────────────
    scenarios = _build_scenario_matrix(
        train_seeds=train_seeds,
        val_seeds=val_seeds,
        holdout_seeds=holdout_seeds,
        train_days=args.train_days,
        val_days=args.validation_days,
        ticks_per_day=args.ticks_per_day,
    )
    scenario_matrix_payload = {
        "train_seeds": train_seeds,
        "validation_seeds": val_seeds,
        "holdout_seeds": holdout_seeds,
        "scenarios": scenarios,
        "feasible_capitals": feasible_capitals,
        "fill_modes_validation_holdout": ["probabilistic", "conservative"],
        "probabilistic_fill_seed_offsets": [0, 10_000],
    }
    _write_json(output_dir / "scenario_matrix.json", scenario_matrix_payload)

    source_hasher = hashlib.sha256()
    for relative in (
        "config.py", "market_spec.py", "quote_engine.py", "risk_manager.py",
        "fill_tracker.py", "backtest/runner.py", "backtest/matching_engine.py",
        "backtest/metrics.py", "backtest/robust_optimize.py",
    ):
        source_hasher.update(relative.encode("utf-8"))
        source_hasher.update((Path(_PROJECT_ROOT) / relative).read_bytes())
    fingerprint_payload = {
        "source_sha256": source_hasher.hexdigest(),
        "market_spec": BacktestRunner(base_cfg).market_spec.to_dict(),
        "safety": {
            "fixed_lot_size": 0.01, "max_inventory_lots": 1,
            "max_drawdown_pct": args.risk_dd, "leverage": args.leverage,
            "max_margin_utilization": _MAX_MARGIN_UTIL,
        },
        "metric_schema": sorted(REQUIRED_METRIC_KEYS),
        "scenario_matrix": scenario_matrix_payload,
        "sampler_seed": 42,
    }
    encoded_fingerprint = json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    study_fingerprint = hashlib.sha256(encoded_fingerprint).hexdigest()
    _write_json(output_dir / "study_fingerprint.json", {
        "fingerprint": study_fingerprint, "payload": fingerprint_payload
    })

    print(f"\n  Regime matrix: {len(scenarios)} scenarios")
    for s in scenarios:
        crash_tag = " [CRASH]" if s["is_crash"] else ""
        print(f"    {s['name']}{crash_tag}")

    # ── Create Optuna study ───────────────────────────────────────────────────
    storage = f"sqlite:///{args.db}" if args.db else None
    if args.db:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    study = optuna.create_study(
        study_name=args.study_name,
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=2),
        sampler=optuna.samplers.TPESampler(seed=42),
    )
    existing_fingerprint = study.user_attrs.get("study_fingerprint")
    if study.trials and existing_fingerprint != study_fingerprint:
        raise RuntimeError(
            "Refusing to resume incompatible study: fingerprint mismatch"
        )
    study.set_user_attr("study_fingerprint", study_fingerprint)

    print(f"\n  Study: {args.study_name}")
    print(f"  Trials: {args.n_trials}")
    print(f"  Top-K: {args.top_k}")
    print(f"  Fill mode (train): {args.fill_mode_train}")
    print(f"  Train days: {args.train_days}  Val/Holdout days: {args.validation_days}")
    print()

    # ── Run optimization ──────────────────────────────────────────────────────
    objective_fn = _make_objective(
        base_cfg=base_cfg,
        scenarios=[
            {**scenario, "capital": capital}
            for capital in feasible_capitals
            for scenario in scenarios
        ],
        fill_mode_train=args.fill_mode_train,
        ticks_per_day=args.ticks_per_day,
    )

    t0 = time.time()
    study.optimize(
        objective_fn,
        n_trials=args.n_trials,
        show_progress_bar=True,
        catch=(Exception,),
    )
    elapsed_train = time.time() - t0

    print(f"\n  ✅ Training complete ({elapsed_train:.1f}s)")

    # ── Collect top-K feasible trials ─────────────────────────────────────────
    completed_trials = [
        t for t in study.trials
        if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None
    ]
    completed_trials.sort(key=lambda t: t.value, reverse=True)

    if not completed_trials:
        print("  ❌ No complete trials — aborting (all pruned).")
        failure_counts: dict[str, int] = {}
        for failed_trial in study.trials:
            reason = str(failed_trial.user_attrs.get("failure", failed_trial.state.name))
            failure_counts[reason] = failure_counts.get(reason, 0) + 1
        _write_json(output_dir / "candidate_params.json", {
            "status": "REJECTED", "params": {}, "reason": "No train-feasible trials"
        })
        _write_json(output_dir / "validation_candidates.json", [])
        _write_json(output_dir / "holdout_results.json", {
            "status": "NOT_RUN", "reason": "No train-feasible candidate"
        })
        (output_dir / "scenario_results.csv").write_text(
            "scenario,seed,fill_mode,capital,passed,failure\n", encoding="utf-8"
        )
        study.trials_dataframe().to_csv(output_dir / "trials.csv", index=False)
        _write_report(
            output_dir=output_dir,
            status="REJECTED",
            reason="No complete trials",
            infeasible_capitals=infeasible_capitals,
            feasible_capitals=feasible_capitals,
            args=args,
            base_cfg=base_cfg,
            train_summary={
                "n_trials": len(study.trials),
                "complete_trials": 0,
                "pruned_trials": sum(
                    t.state == optuna.trial.TrialState.PRUNED for t in study.trials
                ),
            },
            validation_summary={},
            holdout_summary={},
            best_params={},
            failure_counts=failure_counts,
        )
        return

    top_k = completed_trials[:args.top_k]
    print(f"\n  Top {len(top_k)} train-feasible trials:")
    print(f"  {'#':>5}  {'Score':>10}  {'gamma':>8}  {'k':>6}  {'tau':>6}  {'ema':>5}")
    for t in top_k[:5]:
        p = t.params
        print(f"  {t.number:>5}  {t.value:>10.4f}  "
              f"{p.get('gamma',0):>8.4f}  {p.get('k',0):>6.2f}  "
              f"{p.get('tau',0):>6.2f}  {p.get('ema_span',0):>5}")

    # ── Save trials CSV ───────────────────────────────────────────────────────
    try:
        df = study.trials_dataframe()
        df.to_csv(output_dir / "trials.csv", index=False)
        print(f"  ✅ Trials CSV saved: {output_dir / 'trials.csv'}")
    except Exception as e:
        print(f"  ⚠ Could not save trials CSV: {e}")

    # ── Validation: re-rank top-K candidates ─────────────────────────────────
    fill_modes_eval = ["probabilistic", "conservative"]
    if args.fill_mode_train == "optimistic":
        print("  ⚠ WARNING: fill_mode_train=optimistic — validation uses probabilistic+conservative")

    print(f"\n  Validating top-{len(top_k)} candidates on {val_seeds} seeds "
          f"× {len(fill_modes_eval)} fill modes × {len(scenarios)} scenarios...")

    validation_candidates: list[dict] = []
    t_val_start = time.time()

    for rank, trial in enumerate(top_k):
        params = trial.params
        # Build config from trial params (only override tunable params)
        try:
            trial_cfg = replace(
                base_cfg,
                gamma=params.get("gamma", base_cfg.gamma),
                k=params.get("k", base_cfg.k),
                tau=params.get("tau", base_cfg.tau),
                trend_spread_multiplier=params.get("trend_spread_multiplier", base_cfg.trend_spread_multiplier),
                trend_size_multiplier=params.get("trend_size_multiplier", base_cfg.trend_size_multiplier),
                range_spread_multiplier=params.get("range_spread_multiplier", base_cfg.range_spread_multiplier),
                imbalance_skew_factor=params.get("imbalance_skew_factor", base_cfg.imbalance_skew_factor),
                inventory_skew_factor=params.get("inventory_skew_factor", base_cfg.inventory_skew_factor),
                ema_span=params.get("ema_span", base_cfg.ema_span),
                fixed_lot_size=0.01,
                max_inventory_lots=1,
            )
        except Exception as e:
            print(f"    Trial #{trial.number}: config error — {e}")
            continue

        val_summary = _evaluate_candidate_across_capitals(
            cfg=trial_cfg,
            capitals=feasible_capitals,
            scenarios=scenarios,
            seed_key="val_seeds",
            n_days_key="val_days",
            fill_modes=fill_modes_eval,
            ticks_per_day=args.ticks_per_day,
        )
        val_summary["trial_number"] = trial.number
        val_summary["train_score"] = trial.value
        val_summary["params"] = params
        validation_candidates.append(val_summary)

        status = "✅ PASS" if val_summary["pass_rate"] == 1.0 else f"❌ {val_summary['passes']}/{val_summary['total']}"
        print(f"    [{rank+1:2d}/{len(top_k)}] Trial #{trial.number:4d}  "
              f"pass_rate={val_summary['pass_rate']:.0%}  "
              f"median_ret={val_summary.get('median_return', 0):+.2%}  "
              f"worst_dd={val_summary.get('worst_drawdown', 0):.2%}  "
              f"{status}")

    elapsed_val = time.time() - t_val_start
    print(f"  Validation complete ({elapsed_val:.1f}s)")

    # ── Re-rank by validation (AGENTS.md §11) ────────────────────────────────
    validation_candidates.sort(key=_validation_key)
    _write_json(output_dir / "validation_candidates.json", validation_candidates)

    best_val = validation_candidates[0] if validation_candidates else None
    selected_params = best_val["params"] if best_val else {}
    val_pass_rate = best_val["pass_rate"] if best_val else 0.0

    print(f"\n  Selected candidate: Trial #{best_val['trial_number'] if best_val else 'N/A'}")
    print(f"  Validation pass rate: {val_pass_rate:.0%}")

    # ── Holdout: evaluate exactly ONE candidate ───────────────────────────────
    holdout_summary: dict = {}

    if args.no_holdout:
        print("\n  ⚠ Holdout skipped (--no-holdout flag)")
        holdout_pass_rate = 0.0
        promotion_status = "REJECTED" if val_pass_rate < 1.0 else "REJECTED_NO_HOLDOUT"
    else:
        if not best_val:
            holdout_pass_rate = 0.0
            promotion_status = "REJECTED"
        else:
            try:
                holdout_cfg = replace(
                    base_cfg,
                    gamma=selected_params.get("gamma", base_cfg.gamma),
                    k=selected_params.get("k", base_cfg.k),
                    tau=selected_params.get("tau", base_cfg.tau),
                    trend_spread_multiplier=selected_params.get("trend_spread_multiplier", base_cfg.trend_spread_multiplier),
                    trend_size_multiplier=selected_params.get("trend_size_multiplier", base_cfg.trend_size_multiplier),
                    range_spread_multiplier=selected_params.get("range_spread_multiplier", base_cfg.range_spread_multiplier),
                    imbalance_skew_factor=selected_params.get("imbalance_skew_factor", base_cfg.imbalance_skew_factor),
                    inventory_skew_factor=selected_params.get("inventory_skew_factor", base_cfg.inventory_skew_factor),
                    ema_span=selected_params.get("ema_span", base_cfg.ema_span),
                    fixed_lot_size=0.01,
                    max_inventory_lots=1,
                )
            except Exception as e:
                print(f"  ❌ Holdout config build failed: {e}")
                holdout_pass_rate = 0.0
                promotion_status = "REJECTED"
                holdout_summary = {"error": str(e)}
            else:
                print(f"\n  Running holdout evaluation on {holdout_seeds} seeds...")
                holdout_summary = _evaluate_candidate_across_capitals(
                    cfg=holdout_cfg,
                    capitals=feasible_capitals,
                    scenarios=scenarios,
                    seed_key="holdout_seeds",
                    n_days_key="val_days",
                    fill_modes=fill_modes_eval,
                    ticks_per_day=args.ticks_per_day,
                )
                holdout_pass_rate = holdout_summary["pass_rate"]
                print(f"  Holdout pass rate: {holdout_pass_rate:.0%}")
                _write_json(output_dir / "holdout_results.json", holdout_summary)

                # Promotion gate (AGENTS.md §10.4)
                if val_pass_rate == 1.0 and holdout_pass_rate == 1.0:
                    promotion_status = "PROMOTED"
                else:
                    promotion_status = "REJECTED"

    # ── Save candidate params ─────────────────────────────────────────────────
    candidate = {
        "status": promotion_status,
        "trial_number": best_val["trial_number"] if best_val else None,
        "params": selected_params,
        "fixed_invariants": {
            "fixed_lot_size": 0.01,
            "max_inventory_lots": 1,
            "max_drawdown_pct": args.risk_dd,
            "leverage": args.leverage,
        },
        "capital": base_capital,
        "feasible_capitals": feasible_capitals,
        "infeasible_capitals": {str(k): v for k, v in infeasible_capitals.items()},
        "val_pass_rate": val_pass_rate,
        "holdout_pass_rate": holdout_pass_rate if not args.no_holdout else None,
    }
    _write_json(output_dir / "candidate_params.json", candidate)

    # ── Write scenario results CSV ────────────────────────────────────────────
    if holdout_summary.get("scenario_results"):
        _write_scenario_csv(
            output_dir / "scenario_results.csv",
            holdout_summary["scenario_results"]
        )
    elif validation_candidates and validation_candidates[0].get("scenario_results"):
        _write_scenario_csv(
            output_dir / "scenario_results.csv",
            validation_candidates[0]["scenario_results"]
        )

    # ── Failure analysis ──────────────────────────────────────────────────────
    pruned_trials = [
        t for t in study.trials
        if t.state == optuna.trial.TrialState.PRUNED
    ]
    failure_reasons: dict[str, int] = {}
    for t in pruned_trials:
        reason = t.user_attrs.get("failure", "unknown")
        # Simplify reason to first 50 chars
        key = str(reason)[:50]
        failure_reasons[key] = failure_reasons.get(key, 0) + 1

    # ── Write final report ────────────────────────────────────────────────────
    train_summary = {
        "n_trials": args.n_trials,
        "complete_trials": len(completed_trials),
        "pruned_trials": len(pruned_trials),
        "elapsed_s": elapsed_train,
        "best_train_score": completed_trials[0].value if completed_trials else None,
    }
    val_summary_out = {
        "candidates_evaluated": len(validation_candidates),
        "best_pass_rate": val_pass_rate,
        "elapsed_s": elapsed_val,
    }
    if val_pass_rate < 1.0:
        report_reason = (
            f"Validation gates failed: best pass rate {val_pass_rate:.2%}"
        )
    elif args.no_holdout:
        report_reason = "Holdout skipped by diagnostic smoke protocol"
    elif holdout_pass_rate < 1.0:
        report_reason = (
            f"Holdout gates failed: pass rate {holdout_pass_rate:.2%}"
        )
    else:
        report_reason = None

    _write_report(
        output_dir=output_dir,
        status=promotion_status,
        reason=report_reason,
        infeasible_capitals=infeasible_capitals,
        feasible_capitals=feasible_capitals,
        args=args,
        base_cfg=base_cfg,
        train_summary=train_summary,
        validation_summary=val_summary_out,
        holdout_summary={
            "pass_rate": holdout_pass_rate if not args.no_holdout else None,
            "passes": holdout_summary.get("passes", 0),
            "total": holdout_summary.get("total", 0),
        },
        best_params=selected_params,
        failure_counts=failure_reasons,
    )

    # ── Final console summary ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"  STATUS: {promotion_status}")
    print(f"  Trials complete: {len(completed_trials)}/{args.n_trials}")
    print(f"  Validation pass rate: {val_pass_rate:.0%}")
    if not args.no_holdout:
        print(f"  Holdout pass rate:    {holdout_pass_rate:.0%}")
    print(f"  Feasible capitals: {feasible_capitals}")
    print(f"  Infeasible capitals: {list(infeasible_capitals.keys())}")
    if promotion_status == "PROMOTED":
        print("\n  Best parameters (strategy params only):")
        for k, v in selected_params.items():
            if isinstance(v, float):
                print(f"    {k:35s} = {v:.6f}")
            else:
                print(f"    {k:35s} = {v}")
        print("\n  ⚠ config.py NOT modified. To apply, use candidate_params.json")
    else:
        print(f"\n  Most frequent failure reasons:")
        top_fails = sorted(failure_reasons.items(), key=lambda x: -x[1])[:5]
        for reason, count in top_fails:
            print(f"    [{count:4d}x] {reason}")
    print(f"\n  Artifacts: {output_dir}")
    print("=" * 70)

    # ── Config patch (only when PROMOTED) ─────────────────────────────────────
    if promotion_status == "PROMOTED" and selected_params:
        _write_config_patch(output_dir / "config_candidate.patch", selected_params, base_cfg)
        print(f"  ✅ Config patch: {output_dir / 'config_candidate.patch'}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: Any) -> None:
    def _default(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return str(obj)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=_default)


def _write_scenario_csv(path: Path, scenario_results: list[dict]) -> None:
    if not scenario_results:
        return
    fieldnames = list(scenario_results[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(scenario_results)


def _write_report(
    output_dir: Path,
    status: str,
    reason: str | None,
    infeasible_capitals: dict,
    feasible_capitals: list,
    args: argparse.Namespace,
    base_cfg: Config,
    train_summary: dict,
    validation_summary: dict,
    holdout_summary: dict,
    best_params: dict,
    failure_counts: dict,
) -> None:
    report = {
        "status": status,
        "reason": reason,
        "supported_capitals": feasible_capitals if status == "PROMOTED" else [],
        "infeasible_capitals": {str(k): v for k, v in infeasible_capitals.items()},
        "leverage": args.leverage,
        "fixed_lot_size": 0.01,
        "safety_parameters": {
            "max_drawdown_pct": args.risk_dd,
            "max_inventory_lots": 1,
        },
        "strategy_parameters": best_params,
        "train_summary": train_summary,
        "validation_summary": validation_summary,
        "holdout_summary": holdout_summary,
        "failure_counts": failure_counts,
        "simulator_changes": [
            "Canonical base-quantity model with explicit contract conversion",
            "Seeded probabilistic fills and residual partial-order accounting",
            "Emergency and terminal flatten with taker fees and slippage",
            "Equity, margin, inventory, fee, and tail metrics fail closed",
            "Universal parameter evaluation across all feasible capitals",
        ],
        "commands_run": [
            f"python -m backtest.robust_optimize --n-trials {args.n_trials} "
            f"--top-k {args.top_k} --capitals {args.capitals} "
            f"--leverage {args.leverage} --risk-dd {args.risk_dd} "
            f"--train-days {args.train_days} --validation-days {args.validation_days} "
            f"--train-seeds {args.train_seeds} --validation-seeds {args.validation_seeds} "
            f"--holdout-seeds {args.holdout_seeds}",
        ],
    }
    _write_json(output_dir / "report.json", report)

    # Markdown summary
    md_lines = [
        f"# Robust Optimize Report",
        f"",
        f"**Status:** `{status}`",
        f"",
        f"## Capital Feasibility",
        f"",
        f"| Capital | Status |",
        f"|---------|--------|",
    ]
    for cap in feasible_capitals:
        md_lines.append(f"| {cap:.0f} USDT | FEASIBLE |")
    for cap, reason in infeasible_capitals.items():
        md_lines.append(f"| {cap:.0f} USDT | INFEASIBLE: {reason} |")
    md_lines += [
        f"",
        f"## Fixed Invariants",
        f"- `fixed_lot_size = 0.01` (never changed)",
        f"- `max_inventory_lots = 1` (never raised during search)",
        f"- `max_drawdown_pct = {args.risk_dd}` (never optimized)",
        f"- `leverage = {args.leverage}` (never optimized)",
        f"",
        f"## Train Summary",
        f"- Trials requested: {args.n_trials}",
        f"- Complete: {train_summary.get('complete_trials', 'N/A')}",
        f"- Pruned: {train_summary.get('pruned_trials', 'N/A')}",
        f"",
        f"## Validation Summary",
        f"- Candidates evaluated: {validation_summary.get('candidates_evaluated', 'N/A')}",
        f"- Best pass rate: {validation_summary.get('best_pass_rate', 0):.0%}",
        f"",
        f"## Holdout Summary",
        f"- Pass rate: {holdout_summary.get('pass_rate', 'N/A')}",
        f"",
        f"## Strategy Parameters",
        f"",
    ]
    for k, v in best_params.items():
        if isinstance(v, float):
            md_lines.append(f"- `{k}` = `{v:.6f}`")
        else:
            md_lines.append(f"- `{k}` = `{v}`")

    with open(output_dir / "report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))


def _write_config_patch(path: Path, params: dict, base_cfg: Config) -> None:
    """Write a git-style patch showing what config.py changes to apply."""
    lines = [
        "# config_candidate.patch — Apply these strategy parameter changes to config.py",
        "# AGENTS.md §13: Only apply when status=PROMOTED",
        "# DO NOT change fixed_lot_size, max_inventory_lots, max_drawdown_pct, or leverage",
        "",
    ]
    tunable = [
        "gamma", "k", "tau",
        "trend_spread_multiplier", "trend_size_multiplier",
        "range_spread_multiplier", "imbalance_skew_factor",
        "inventory_skew_factor", "ema_span",
    ]
    for param in tunable:
        if param not in params:
            continue
        old_val = getattr(base_cfg, param, "N/A")
        new_val = params[param]
        if isinstance(new_val, float):
            lines.append(f"- {param}: {old_val} → {new_val:.6f}")
        else:
            lines.append(f"- {param}: {old_val} → {new_val}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
