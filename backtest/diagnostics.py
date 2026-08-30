"""Deterministic structural diagnostics required before an optimizer smoke run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from backtest.metrics import compute_metrics
from backtest.runner import BacktestRunner
from backtest.synthetic_data import generate_regime_switching_gbm
from config import Config


def capital_feasibility(
    capital: float,
    *,
    btc_price: float = 50_000.0,
    lot_size_btc: float = 0.01,
    leverage: float = 1.0,
    margin_cap: float = 0.80,
    maker_fee_rate: float = 0.0002,
    taker_fee_rate: float = 0.0005,
    emergency_slippage_bps: float = 10.0,
) -> dict[str, Any]:
    notional = btc_price * lot_size_btc
    position_margin = notional / leverage
    fee_reserve = notional * maker_fee_rate
    flatten_reserve = notional * (
        taker_fee_rate + emergency_slippage_bps / 10_000.0
    )
    one_order = position_margin + fee_reserve + flatten_reserve
    two_order = 2.0 * position_margin + 2.0 * fee_reserve + flatten_reserve
    budget = capital * margin_cap
    if one_order > budget:
        classification = "MATHEMATICALLY_INFEASIBLE"
        reason = "One opening lot plus fee/flatten reserve exceeds margin budget"
    elif two_order > budget:
        classification = "ONE_SIDED_FEASIBLE"
        reason = "One opening order fits; simultaneous bid and ask reservations do not"
    else:
        classification = "TWO_SIDED_FEASIBLE"
        reason = "Simultaneous opening bid and ask reservations fit preflight"
    return {
        "capital": capital,
        "btc_price": btc_price,
        "lot_size_btc": lot_size_btc,
        "order_notional": notional,
        "leverage": leverage,
        "margin_cap": margin_cap,
        "margin_budget": budget,
        "current_position_margin": 0.0,
        "one_order_margin": one_order,
        "two_order_worst_case_margin": two_order,
        "fee_reserve": fee_reserve,
        "flatten_reserve": flatten_reserve,
        "peak_projected_margin_utilization_one_order": one_order / capital,
        "peak_projected_margin_utilization_two_orders": two_order / capital,
        "reduce_only_risk": "releases exposure; no opening margin reserved",
        "classification": classification,
        "reason": reason,
    }


def _book_ticks(
    prices: np.ndarray,
    *,
    spread: float = 10.0,
    start_timestamp_ms: int = 1_700_000_000_000,
) -> list[dict]:
    ticks: list[dict] = []
    for index, price in enumerate(prices):
        half = spread / 2.0
        ticks.append({
            "bids": [[round(float(price - half), 2), 1.0]],
            "asks": [[round(float(price + half), 2), 1.0]],
            "timestamp": start_timestamp_ms + index * 300_000,
        })
    return ticks


def targeted_scenarios(ticks_per_day: int, days: int) -> list[dict[str, Any]]:
    count = ticks_per_day * days
    index = np.arange(count, dtype=float)
    base = 50_000.0

    def gbm(name: str, vol: float, seed: int, **extra: Any) -> dict[str, Any]:
        return {
            "name": name,
            "seed": seed,
            "ticks": generate_regime_switching_gbm(
                vol_weekly=vol, n_days=days, ticks_per_day=ticks_per_day,
                seed=seed, **extra,
            ),
            "market_info": {"ticks_per_day": ticks_per_day},
        }

    scenarios = [
        gbm("low_vol_normal", 0.10, 42),
        gbm("medium_vol_normal", 0.25, 43),
        gbm("high_vol_non_crash", 0.40, 44),
        {"name": "uptrend", "seed": 45,
         "ticks": _book_ticks(base * np.exp(0.08 * index / max(count - 1, 1))),
         "market_info": {"ticks_per_day": ticks_per_day}},
        {"name": "downtrend", "seed": 46,
         "ticks": _book_ticks(base * np.exp(-0.08 * index / max(count - 1, 1))),
         "market_info": {"ticks_per_day": ticks_per_day}},
        {"name": "mean_reverting", "seed": 47,
         "ticks": _book_ticks(base + 500.0 * np.sin(index / 8.0)),
         "market_info": {"ticks_per_day": ticks_per_day}},
        {"name": "thin_liquidity", "seed": 48,
         "ticks": _book_ticks(base + 300.0 * np.sin(index / 5.0), spread=40.0),
         "market_info": {"ticks_per_day": ticks_per_day}},
        gbm("partial_fill_heavy", 0.20, 49),
        {"name": "delayed_cancel_ack", "seed": 50,
         "ticks": _book_ticks(base + 250.0 * np.sin(index / 3.0)),
         "market_info": {"ticks_per_day": ticks_per_day, "cancel_latency_ticks": 2}},
        {"name": "stale_quote_move", "seed": 51,
         "ticks": _book_ticks(base + np.where(index < count / 2, 0.0, -1_500.0)),
         "market_info": {"ticks_per_day": ticks_per_day, "cancel_latency_ticks": 2}},
        {"name": "gap_non_crash", "seed": 52,
         "ticks": _book_ticks(base + np.where(index < count / 2, 0.0, -1_000.0)),
         "market_info": {"ticks_per_day": ticks_per_day}},
        {"name": "crash", "seed": 53,
         "ticks": _book_ticks(base * np.where(index < count / 2, 1.0, 0.82)),
         "market_info": {"ticks_per_day": ticks_per_day}},
        {"name": "terminal_flatten", "seed": 54,
         "ticks": _book_ticks(base + 200.0 * np.sin(index / 4.0)),
         "market_info": {"ticks_per_day": ticks_per_day}},
    ]
    return scenarios


def _stable_result_payload(result: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    scalar_metrics = {
        key: value for key, value in metrics.items()
        if not isinstance(value, list)
    }
    return {
        "metrics": scalar_metrics,
        "fills": [asdict(fill) for fill in result.fill_log],
        "equity_curve": result.equity_curve,
        "inventory_curve": result.inventory_curve,
        "drawdown_curve": result.drawdown_curve,
        "mid_price_curve": result.mid_price_curve,
        "quote_log": result.quote_log,
        "kill_switch_events": result.kill_switch_events,
        "rejection_events": result.rejection_events,
    }


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _checks(result: Any, metrics: dict[str, Any], cfg: Config) -> list[str]:
    failures: list[str] = []
    if metrics["invalid_order_count"] != 0:
        failures.append(f"invalid_order_count={metrics['invalid_order_count']}")
    if metrics["rejected_post_only_count"] != 0:
        failures.append(
            f"avoidable_post_only_rejections={metrics['rejected_post_only_count']}"
        )
    if metrics["max_abs_inventory_lots"] > 1.0 + 1e-9:
        failures.append("inventory_limit_breach")
    if metrics["max_margin_utilization"] > cfg.max_margin_utilization + 1e-9:
        failures.append("margin_cap_breach")
    expected_equity = (
        cfg.initial_capital + metrics["net_realized_pnl_usdt"]
        + metrics["unrealized_pnl_usdt"] + metrics["funding_pnl_usdt"]
    )
    if not math.isclose(
        metrics["final_equity_usdt"], expected_equity, rel_tol=0.0, abs_tol=1e-8
    ):
        failures.append("accounting_identity")
    for quote in result.quote_log:
        bid = quote["final_bid_quote"]
        ask = quote["final_ask_quote"]
        if bid is not None and bid >= quote["best_ask"]:
            failures.append("submitted_crossing_bid")
            break
        if ask is not None and ask <= quote["best_bid"]:
            failures.append("submitted_crossing_ask")
            break
    for event in result.kill_switch_events:
        if event.get("root_cause_category") in {None, "UNKNOWN"}:
            failures.append("unclassified_kill_switch")
        if abs(float(event.get("post_flatten_inventory", math.inf))) > 1e-9:
            failures.append("kill_not_flat")
    return sorted(set(failures))


def _plot_diagnostics(output: Path, label: str, result: Any, cfg: Config) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    plot_dir = output / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(result.equity_curve))

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(x, result.equity_curve, label="equity")
    ax.axhline(cfg.initial_capital * (1 - cfg.max_drawdown_pct), linestyle="--",
               label="drawdown threshold")
    for event in result.kill_switch_events:
        ax.axvline(event["tick"], color="red", linestyle=":", label="kill")
    ax.set(xlabel="observation", ylabel="USDT")
    ax.legend()
    fig.tight_layout()
    fig.savefig(plot_dir / f"{label}-equity-drawdown.png", dpi=130)
    plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(10, 4))
    inv = np.asarray(result.inventory_curve)
    ax1.plot(inv, label="inventory BTC")
    ax1.plot(inv / cfg.fixed_lot_size, label="inventory lots")
    ax1.axhline(1, linestyle="--")
    ax1.axhline(-1, linestyle="--")
    ax1.set(xlabel="observation", ylabel="BTC / lots")
    ax1.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(plot_dir / f"{label}-inventory.png", dpi=130)
    plt.close(fig)

    if result.quote_log:
        qx = np.asarray([row["tick"] for row in result.quote_log])
        fig, ax = plt.subplots(figsize=(10, 4))
        for key, name in (
            ("mid_price", "mid"), ("best_bid", "best bid"),
            ("best_ask", "best ask"), ("reservation_price", "reservation"),
            ("final_bid_quote", "quote bid"), ("final_ask_quote", "quote ask"),
        ):
            values = np.asarray([
                np.nan if row[key] is None else row[key] for row in result.quote_log
            ], dtype=float)
            ax.plot(qx, values, label=name, linewidth=0.9)
        ax.set(xlabel="tick", ylabel="USDT/BTC")
        ax.legend(ncol=3)
        fig.tight_layout()
        fig.savefig(plot_dir / f"{label}-market-quotes.png", dpi=130)
        plt.close(fig)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax1.plot(qx, [row["margin_utilization"] for row in result.quote_log],
                 label="margin utilization")
        ax1.axhline(cfg.max_margin_utilization, linestyle="--", label="margin cap")
        ax1.legend()
        ax2.plot(qx, [row["net_realized_pnl"] for row in result.quote_log],
                 label="net realized")
        ax2.plot(qx, [row["unrealized_pnl"] for row in result.quote_log],
                 label="unrealized")
        ax2.plot(qx, [row["fees"] for row in result.quote_log], label="fees")
        ax2.set(xlabel="tick", ylabel="USDT")
        ax2.legend()
        fig.tight_layout()
        fig.savefig(plot_dir / f"{label}-margin-pnl-costs.png", dpi=130)
        plt.close(fig)


def run_diagnostics(output: Path, ticks_per_day: int, days: int) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    capitals = [300.0, 500.0, 750.0, 1000.0]
    feasibility = [capital_feasibility(capital) for capital in capitals]
    with (output / "capital_feasibility.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(feasibility[0]))
        writer.writeheader()
        writer.writerows(feasibility)
    (output / "capital_feasibility.json").write_text(
        json.dumps(feasibility, indent=2), encoding="utf-8"
    )

    eligible = [
        row["capital"] for row in feasibility
        if row["classification"] != "MATHEMATICALLY_INFEASIBLE"
    ]
    results: list[dict[str, Any]] = []
    quote_dir = output / "quote_logs"
    quote_dir.mkdir()
    for scenario in targeted_scenarios(ticks_per_day, days):
        ticks_hash = _hash_payload(scenario["ticks"])
        for capital in eligible:
            cfg = Config(initial_capital=capital)
            market_info = scenario["market_info"]
            runner = BacktestRunner(
                cfg, market_info=market_info,
                fill_mode="probabilistic", fill_seed=scenario["seed"],
            )
            first = runner.run(scenario["ticks"])
            first_metrics = compute_metrics(first, cfg)
            second = BacktestRunner(
                cfg, market_info=market_info,
                fill_mode="probabilistic", fill_seed=scenario["seed"],
            ).run(scenario["ticks"])
            second_metrics = compute_metrics(second, cfg)
            first_payload = _stable_result_payload(first, first_metrics)
            second_payload = _stable_result_payload(second, second_metrics)
            result_hash = _hash_payload(first_payload)
            rerun_hash = _hash_payload(second_payload)
            failures = _checks(first, first_metrics, cfg)
            if result_hash != rerun_hash:
                failures.append("non_deterministic_rerun")
            label = f"{scenario['name']}-c{int(capital)}-s{scenario['seed']}"
            with (quote_dir / f"{label}.jsonl").open("w", encoding="utf-8") as handle:
                for row in first.quote_log:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
            if capital == min(eligible):
                _plot_diagnostics(output, label, first, cfg)
            results.append({
                "scenario": scenario["name"], "seed": scenario["seed"],
                "capital": capital, "market_hash": ticks_hash,
                "result_hash": result_hash, "rerun_hash": rerun_hash,
                "passed": not failures, "failures": failures,
                "kill_root_causes": [
                    event["root_cause_category"]
                    for event in first.kill_switch_events
                ],
                "first_failure": (
                    first.kill_switch_events[0] if first.kill_switch_events
                    else (first.rejection_events[0] if first.rejection_events else None)
                ),
                "metrics": first_metrics,
            })

    report = {
        "status": "PASS" if all(row["passed"] for row in results) else "FAIL",
        "funding_assumption": "fixed zero; non-promotable unresolved limitation",
        "capital_feasibility": feasibility,
        "targeted_results": results,
    }
    (output / "targeted_report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    lines = [
        "# Targeted Structural Diagnostics", "",
        f"Status: **{report['status']}**", "",
        "| Scenario | Capital | Result | Kill cause |",
        "|---|---:|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['scenario']} | {row['capital']:.0f} | "
            f"{'PASS' if row['passed'] else '; '.join(row['failures'])} | "
            f"{', '.join(row['kill_root_causes']) or 'none'} |"
        )
    (output / "targeted_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ticks-per-day", type=int, default=96)
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    report = run_diagnostics(
        Path(args.output_dir), args.ticks_per_day, args.days
    )
    print(json.dumps({
        "status": report["status"],
        "runs": len(report["targeted_results"]),
        "passed": sum(row["passed"] for row in report["targeted_results"]),
    }, indent=2))


if __name__ == "__main__":
    main()
