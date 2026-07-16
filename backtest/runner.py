"""
backtest/runner.py — Offline backtest event loop.

Mirrors ``strategy.py::on_trading_iteration()`` steps 1-8, but:
  - Step 1: pulls next tick from a pre-generated synthetic tick list
  - Step 3: calls MatchingEngine.check_fills() instead of exchange detection
  - Steps 9-10: collects equity/inventory curve instead of web dashboard

Usage::

    from config import load_config
    from backtest.synthetic_data import generate_regime_switching_gbm
    from backtest.runner import BacktestRunner

    cfg = load_config()
    ticks = generate_regime_switching_gbm(vol_weekly=0.25, n_days=30, seed=42)
    runner = BacktestRunner(cfg)
    result = runner.run(ticks)
    print(result.summary())
"""

from __future__ import annotations

import logging
import sys
import os
import time
from dataclasses import dataclass, field

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config, load_config
from fill_tracker import Fill, FillTracker
from market_state import MarketState
from quote_engine import QuoteEngine
from regime_detector import RegimeDetector
from risk_manager import RiskManager
from backtest.matching_engine import MatchingEngine

logger = logging.getLogger(__name__)


# ── BacktestResult ────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """All outputs collected by the runner after a complete backtest run."""

    # Core curves (one value per tick)
    equity_curve: list[float] = field(default_factory=list)
    inventory_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    mid_price_curve: list[float] = field(default_factory=list)

    # Fill log
    fill_log: list[Fill] = field(default_factory=list)

    # Risk events
    kill_switch_events: list[dict] = field(default_factory=list)

    # Fill counters (for fill rate metric)
    bid_fills: int = 0
    ask_fills: int = 0
    total_ticks: int = 0

    # Inventory breach counter (>80% of max_inventory)
    ticks_over_80pct_inventory: int = 0

    # Kill-switch trigger count (from kill_switch_events)
    @property
    def kill_switch_count(self) -> int:
        return len(self.kill_switch_events)

    def summary(self) -> str:
        """Human-readable one-line summary."""
        final_equity = self.equity_curve[-1] if self.equity_curve else 0.0
        max_dd = max(self.drawdown_curve) if self.drawdown_curve else 0.0
        return (
            f"Ticks={self.total_ticks} | "
            f"FinalEquity={final_equity:.2f} | "
            f"MaxDD={max_dd:.2%} | "
            f"KillSwitch={self.kill_switch_count} | "
            f"BidFills={self.bid_fills} | AskFills={self.ask_fills}"
        )


# ── Default market info for BTC/USDT perp ────────────────────────────────────

_DEFAULT_MARKET_INFO: dict = {
    "tick_size": 0.1,
    "lot_size": 0.001,
    "min_notional": 5.0,
    "contract_size": 0.001,
}


# ── BacktestRunner ────────────────────────────────────────────────────────────

class BacktestRunner:
    """Offline event loop that replays synthetic ticks through production logic.

    The runner initialises fresh instances of all subsystems (MarketState,
    RegimeDetector, QuoteEngine, RiskManager, FillTracker, MatchingEngine)
    so each run is fully isolated.

    Parameters
    ----------
    config : Config
        Bot configuration.  Use ``load_config()`` for defaults or pass
        a custom Config with ``Config(...)`` overrides for scenario testing.
    market_info : dict | None
        Exchange market info dict (tick_size, lot_size, etc.).
        Defaults to ``_DEFAULT_MARKET_INFO`` (BTC/USDT perp).
    """

    def __init__(
        self,
        config: Config | None = None,
        market_info: dict | None = None,
    ) -> None:
        self.cfg = config if config is not None else load_config()
        self.market_info = market_info if market_info is not None else dict(_DEFAULT_MARKET_INFO)

    def run(self, ticks: list[dict]) -> BacktestResult:
        """Run a full backtest over the given tick sequence.

        Parameters
        ----------
        ticks : list[dict]
            CCXT-format order book dicts (from any synthetic_data generator).
            Must have at least ``regime_slow_ema_span + rsi_period`` ticks
            to allow regime detector to warm up.

        Returns
        -------
        BacktestResult
        """
        result = BacktestResult()

        # ── Initialise subsystems (fresh for each run) ────────────────────
        ms = MarketState()
        ms.configure(
            ema_span=self.cfg.ema_span,
            price_history_length=self.cfg.price_history_length,
            staleness_threshold_s=999_999.0,  # never stale in backtest
            latency_warning_ms=1e12,          # suppress latency warnings for backfilled timestamps
        )

        regime_detector = RegimeDetector(self.cfg)
        quote_engine = QuoteEngine(self.cfg)
        risk_manager = RiskManager(self.cfg)
        fill_tracker = FillTracker(self.cfg)
        matching_engine = MatchingEngine()

        prev_bid_order_id: str | None = None
        prev_ask_order_id: str | None = None
        prev_realized_pnl: float = 0.0
        iteration = 0

        for tick in ticks:
            iteration += 1
            result.total_ticks += 1

            # ── Step 1: Refresh market state ──────────────────────────────
            ms.update_from_orderbook(tick)

            if ms.mid_price <= 0:
                continue  # skip ticks with no usable price

            # ── Step 2: Regime detection ──────────────────────────────────
            prices = ms.price_history_prices
            regime = regime_detector.detect(prices)
            ms.regime = regime

            # ── Step 3: Fill detection (matching engine, not exchange) ─────
            new_fills = matching_engine.check_fills(tick, fill_tracker)
            result.fill_log.extend(new_fills)
            for f in new_fills:
                if f.side == "buy":
                    result.bid_fills += 1
                else:
                    result.ask_fills += 1

            # ── Step 4: P&L update ────────────────────────────────────────
            unrealized = fill_tracker.compute_unrealized_pnl(ms.mid_price)
            risk_manager.update_pnl(
                realized_delta=0.0,
                unrealized=unrealized,
                current_equity=(
                    self.cfg.initial_capital
                    + fill_tracker.realized_pnl
                    + unrealized
                ),
            )

            # ── Step 5: Risk checks ───────────────────────────────────────
            risk_result = risk_manager.check_all(
                inventory=fill_tracker.position,
                mid_price=ms.mid_price,
                avg_entry_price=fill_tracker.avg_entry_price,
            )

            if risk_result.cancel_all:
                # Kill-switch triggered: cancel all orders
                matching_engine.cancel_all()
                prev_bid_order_id = None
                prev_ask_order_id = None

                # Only record the FIRST trigger event — not every subsequent tick
                # (RiskManager.kill_switch_active stays True until manually reset;
                #  each tick after first activation also returns cancel_all=True)
                is_first_trigger = not any(
                    e.get("reason") == risk_result.reason
                    for e in result.kill_switch_events
                )
                if is_first_trigger or not result.kill_switch_events:
                    result.kill_switch_events.append({
                        "tick": iteration,
                        "reason": risk_result.reason,
                        "equity": risk_manager.current_equity,
                        "drawdown": risk_manager.drawdown_pct,
                        "inventory": fill_tracker.position,
                    })
                    logger.debug(
                        "Kill-switch at tick %d: %s", iteration, risk_result.reason
                    )

                # Collect metrics and continue (don't return early — we want
                # the full equity curve for metrics)
                self._collect_tick_metrics(result, risk_manager, fill_tracker, ms)
                continue

            # ── Step 6: Quote generation ──────────────────────────────────
            if not risk_result.allow_quoting:
                self._collect_tick_metrics(result, risk_manager, fill_tracker, ms)
                continue

            quotes = quote_engine.generate(
                ms=ms,
                inventory=fill_tracker.position,
                market_info=self.market_info,
                spread_multiplier=regime_detector.get_spread_multiplier(),
                size_multiplier=regime_detector.get_size_multiplier(),
            )

            # ── Step 7: Order reconciliation (simplified) ─────────────────
            # Cancel previous resting orders then place fresh quotes
            # (v1: always replace; v2 could add price/size change threshold)
            if prev_bid_order_id:
                matching_engine.cancel_order(prev_bid_order_id)
            if prev_ask_order_id:
                matching_engine.cancel_order(prev_ask_order_id)

            prev_bid_order_id = None
            prev_ask_order_id = None

            if quotes.bid_valid:
                prev_bid_order_id = matching_engine.place_order(
                    "buy", quotes.bid_price, quotes.bid_size
                )
            if quotes.ask_valid:
                prev_ask_order_id = matching_engine.place_order(
                    "sell", quotes.ask_price, quotes.ask_size
                )

            # ── Step 8: Collect metrics ───────────────────────────────────
            self._collect_tick_metrics(result, risk_manager, fill_tracker, ms)

            # Track inventory breach (>80% of max_inventory)
            max_inv = self.cfg.max_inventory
            if max_inv > 0 and abs(fill_tracker.position) > 0.8 * max_inv:
                result.ticks_over_80pct_inventory += 1

        # ── Post-run stats ────────────────────────────────────────────────
        result.bid_fills = matching_engine.total_bid_fills
        result.ask_fills = matching_engine.total_ask_fills

        logger.info("Backtest complete: %s", result.summary())
        return result

    def _collect_tick_metrics(
        self,
        result: BacktestResult,
        risk_manager: RiskManager,
        fill_tracker: FillTracker,
        ms: MarketState,
    ) -> None:
        """Append this tick's snapshot to all curve lists."""
        result.equity_curve.append(risk_manager.current_equity)
        result.inventory_curve.append(fill_tracker.position)
        result.drawdown_curve.append(risk_manager.drawdown_pct)
        result.mid_price_curve.append(ms.mid_price)


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli() -> None:
    """Run a single scenario from the command line for smoke testing.

    Usage::
        python -m backtest.runner --seed 42 --vol 0.25 --n-days 7
    """
    import argparse
    from backtest.synthetic_data import generate_regime_switching_gbm
    from backtest.metrics import compute_metrics

    parser = argparse.ArgumentParser(description="Run a single backtest scenario")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--vol", type=float, default=0.25, help="Weekly vol")
    parser.add_argument("--n-days", type=int, default=7)
    parser.add_argument("--jump-freq", type=float, default=0.2)
    parser.add_argument("--jump-size", type=float, default=0.02)
    parser.add_argument("--ticks-per-day", type=int, default=288)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config()

    print(f"Generating {args.n_days}-day GBM scenario (seed={args.seed}, vol_weekly={args.vol})")
    ticks = generate_regime_switching_gbm(
        vol_weekly=args.vol,
        n_days=args.n_days,
        jump_freq=args.jump_freq,
        jump_size=args.jump_size,
        ticks_per_day=args.ticks_per_day,
        seed=args.seed,
    )

    print(f"Running backtest on {len(ticks):,} ticks...")
    runner = BacktestRunner(cfg)
    result = runner.run(ticks)

    print("\n-- Result " + "-" * 35)
    print(result.summary())

    metrics = compute_metrics(result, cfg)
    print("\n-- Metrics " + "-" * 34)
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        elif isinstance(v, list):
            pass  # skip verbose lists
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
