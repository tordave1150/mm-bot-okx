"""Post-optimization comparison on a shared unseen scenario matrix."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from config import Config, load_config
from backtest.robust_optimize import (
    _build_scenario_matrix,
    _evaluate_candidate_across_capitals,
    check_capital_feasibility,
)


TUNABLE = {
    "gamma", "k", "tau", "trend_spread_multiplier", "trend_size_multiplier",
    "range_spread_multiplier", "imbalance_skew_factor", "inventory_skew_factor",
    "ema_span",
}
LEGACY_PARAMS = {
    "gamma": 0.29533864670021215,
    "k": 4.176599010180326,
    "tau": 1.7512045323642924,
    "trend_spread_multiplier": 1.5312376972972281,
    "trend_size_multiplier": 0.7967883994603725,
    "range_spread_multiplier": 1.1870480854924734,
    "imbalance_skew_factor": 0.08759538768149842,
    "inventory_skew_factor": 0.771210965163458,
    "ema_span": 16,
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", default=[], help="candidate_params.json path")
    parser.add_argument("--capitals", default="300,500,750,1000")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--ticks-per-day", type=int, default=288)
    parser.add_argument("--output-dir", default="artifacts/comparisons/latest")
    return parser.parse_args()


def _load_candidate(path: Path) -> tuple[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    params = payload.get("params", payload.get("strategy_parameters", {}))
    unknown = sorted(set(params).difference(TUNABLE))
    if unknown:
        raise ValueError(f"{path}: non-strategy parameters are not comparable: {unknown}")
    return path.parent.name or path.stem, params


def main() -> None:
    logging.disable(logging.CRITICAL)
    args = _args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    requested = [float(item) for item in args.capitals.split(",")]
    infeasible = {
        capital: reason for capital in requested
        if (reason := check_capital_feasibility(capital, 1.0, 0.01)) is not None
    }
    capitals = [capital for capital in requested if capital not in infeasible]
    if not capitals:
        raise SystemExit("No feasible capitals remain after preflight")

    candidates: list[tuple[str, dict[str, Any]]] = [
        ("current_defaults", {}), ("legacy_optuna", LEGACY_PARAMS)
    ]
    candidates.extend(_load_candidate(Path(item)) for item in args.candidate)

    seeds = list(range(900, 900 + args.seeds))
    scenarios = _build_scenario_matrix([], [], seeds, args.days, args.days, args.ticks_per_day)
    base = load_config(initial_capital=min(capitals), max_inventory_lots=1)
    reports: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    print("Candidate                  Pass       Median       P10      Worst    WorstDD      Fees")
    for label, params in candidates:
        cfg: Config = replace(base, **params)
        summary = _evaluate_candidate_across_capitals(
            cfg, capitals, scenarios, "holdout_seeds", "val_days",
            ["probabilistic", "conservative"], args.ticks_per_day,
        )
        report = {"candidate": label, "params": params, **summary}
        reports.append(report)
        for row in summary["scenario_results"]:
            rows.append({"candidate": label, **row})
        status = "PASS" if summary["pass_rate"] == 1.0 else "FAIL"
        print(
            f"{label:<26} {summary['pass_rate']:>6.1%} {summary['median_return']:>11.2%} "
            f"{summary['p10_return']:>9.2%} {summary['worst_return']:>9.2%} "
            f"{summary['worst_drawdown']:>10.2%} {summary['total_fees']:>9.2f}  {status}"
        )

    payload = {
        "seed_group": "comparison_unseen_900_plus",
        "seeds": seeds, "fill_modes": ["probabilistic", "conservative"],
        "feasible_capitals": capitals,
        "infeasible_capitals": {str(key): value for key, value in infeasible.items()},
        "candidates": reports,
    }
    (output / "comparison.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if rows:
        with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    print(f"Artifacts: {output.resolve()}")


if __name__ == "__main__":
    main()
