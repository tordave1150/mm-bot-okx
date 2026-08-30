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
from typing import TYPE_CHECKING
import sys
import os
from dataclasses import dataclass, field
from decimal import Decimal

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import Config, load_config, _validate_config
from fill_tracker import Fill, FillTracker
from market_state import MarketState
from quote_engine import QuoteEngine
from regime_detector import RegimeDetector
from risk_manager import RiskManager
from backtest.matching_engine import CancellationReason, MatchingEngine
from market_spec import MarketSpec, OrderValidationError, validate_order

if TYPE_CHECKING:
    from dashboard_state import DashboardState

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

    # Fees
    total_fees: float = 0.0
    total_realized_pnl: float = 0.0
    gross_realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    funding_pnl: float = 0.0

    # Inventory breach counter (>80% of max_inventory)
    ticks_over_80pct_inventory: int = 0
    max_abs_inventory_base: float = 0.0
    ending_inventory_base: float = 0.0
    bid_fill_base_qty: float = 0.0
    ask_fill_base_qty: float = 0.0

    # Validation/margin evidence
    invalid_order_count: int = 0
    invalid_order_reasons: list[str] = field(default_factory=list)
    rejected_post_only_count: int = 0
    local_risk_rejection_count: int = 0
    local_quote_skip_count: int = 0
    post_only_clamp_count: int = 0
    quote_updates: int = 0
    maker_bid_fills: int = 0
    maker_ask_fills: int = 0
    taker_exit_fills: int = 0
    partial_fills: int = 0
    completed_round_trips: int = 0
    quote_log: list[dict] = field(default_factory=list)
    rejection_events: list[dict] = field(default_factory=list)
    max_margin_utilization: float = 0.0
    market_spec_fingerprint: str = ""
    initial_capital: float = 0.0

    # Ticks-per-day (set by runner for correct annualisation)
    ticks_per_day: int = 288

    # Post-kill deterioration: equity lost after first kill-switch trigger
    # Computed as initial_capital_at_trigger - final_equity (positive = loss)
    post_kill_equity_at_trigger: float = 0.0
    post_kill_final_equity: float = 0.0
    kill_post_flatten_equity: float = 0.0
    flatten_fees: float = 0.0
    flatten_slippage: float = 0.0
    terminal_fees: float = 0.0
    terminal_slippage: float = 0.0
    residual_inventory_base: float = 0.0
    order_reconciliation: dict = field(default_factory=dict)

    # Kill-switch trigger count (from kill_switch_events)
    @property
    def kill_switch_count(self) -> int:
        return len(self.kill_switch_events)

    @property
    def post_kill_deterioration(self) -> float:
        """Equity lost after first kill-switch trigger (positive = loss)."""
        if not self.kill_switch_events:
            return 0.0
        if self.post_kill_equity_at_trigger <= 0:
            return 0.0
        final = self.kill_post_flatten_equity or self.post_kill_equity_at_trigger
        loss = self.post_kill_equity_at_trigger - final
        return max(0.0, loss)

    def summary(self) -> str:
        """Human-readable one-line summary."""
        final_equity = self.equity_curve[-1] if self.equity_curve else 0.0
        max_dd = max(self.drawdown_curve) if self.drawdown_curve else 0.0
        return (
            f"Ticks={self.total_ticks} | "
            f"FinalEquity={final_equity:.2f} | "
            f"MaxDD={max_dd:.2%} | "
            f"Fees={self.total_fees:.4f} | "
            f"KillSwitch={self.kill_switch_count} | "
            f"BidFills={self.bid_fills} | AskFills={self.ask_fills}"
        )


# ── Default market info for BTC/USDT perp ────────────────────────────────────

_DEFAULT_MARKET_INFO: dict = {
    "tick_size": 0.1,
    "lot_size": 1.0,              # exchange contracts
    "base_step": 0.01,            # BTC base quantity
    "min_amount": 1.0,            # exchange contracts
    "min_notional": 5.0,
    "contract_size": 0.01,        # BTC per contract
    "ticks_per_day": 288,
}


def default_market_spec(market_info: dict | None = None) -> MarketSpec:
    """Build the frozen BTC/USDT linear-perpetual fixture used offline."""
    info = dict(_DEFAULT_MARKET_INFO)
    if market_info:
        info.update(market_info)
    existing = info.get("market_spec")
    if isinstance(existing, MarketSpec):
        return existing
    return MarketSpec(
        symbol="BTC/USDT:USDT",
        contract_size=Decimal(str(info["contract_size"])),
        amount_step=Decimal(str(info["lot_size"])),
        min_amount=Decimal(str(info.get("min_amount", info["lot_size"]))),
        min_notional=(
            Decimal(str(info["min_notional"]))
            if info.get("min_notional") is not None else None
        ),
        price_tick=Decimal(str(info["tick_size"])),
        amount_precision=None,
        price_precision=None,
        linear=True,
        inverse=False,
    )


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
    fill_mode : str
        Matching engine fill mode: "optimistic", "probabilistic", "conservative".
    """

    def __init__(
        self,
        config: Config | None = None,
        market_info: dict | None = None,
        fill_mode: str = "optimistic",
        fill_seed: int | None = None,
    ) -> None:
        self.cfg = config if config is not None else load_config()
        _validate_config(self.cfg)
        self.market_info = market_info if market_info is not None else dict(_DEFAULT_MARKET_INFO)
        self.market_info = dict(self.market_info)
        self.market_spec = default_market_spec(self.market_info)
        self.market_info["market_spec"] = self.market_spec
        self.market_info["base_step"] = float(
            self.market_spec.contracts_to_base(self.market_spec.amount_step)
        )
        self.fill_mode = fill_mode
        self.fill_seed = fill_seed

    def run(
        self,
        ticks: list[dict],
        dashboard_state: DashboardState | None = None,
    ) -> BacktestResult:
        """Run a full backtest over the given tick sequence.

        Parameters
        ----------
        ticks : list[dict]
            CCXT-format order book dicts (from any synthetic_data generator).
            Must have at least ``regime_slow_ema_span + rsi_period`` ticks
            to allow regime detector to warm up.
        dashboard_state : DashboardState | None
            Optional shared state object.  When provided the runner checks
            ``is_stop_requested()`` every tick and breaks cleanly if the
            dashboard user presses Stop.

        Returns
        -------
        BacktestResult
        """
        result = BacktestResult(
            equity_curve=[self.cfg.initial_capital],
            inventory_curve=[0.0],
            drawdown_curve=[0.0],
            initial_capital=self.cfg.initial_capital,
            market_spec_fingerprint=self.market_spec.fingerprint,
            ticks_per_day=int(self.market_info.get("ticks_per_day", 288)),
        )

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
        matching_engine = MatchingEngine(
            fill_mode=self.fill_mode,
            maker_fee_rate=self.cfg.maker_fee_rate,
            taker_fee_rate=self.cfg.taker_fee_rate,
            fill_seed=self.fill_seed,
            quantity_step=float(self.market_info["base_step"]),
            cancel_latency_ticks=int(self.market_info.get("cancel_latency_ticks", 0)),
        )

        prev_bid_order_id: str | None = None
        prev_ask_order_id: str | None = None
        iteration = 0
        funding_pnl = 0.0
        kill_handled = False
        last_tick: dict | None = None

        for tick in ticks:
            iteration += 1
            result.total_ticks += 1
            last_tick = tick

            # ── Dashboard stop check ──────────────────────────────────────
            if dashboard_state is not None and dashboard_state.is_stop_requested():
                logger.info(
                    "Backtest stopped from dashboard at tick %d / %d",
                    iteration, len(ticks),
                )
                break

            # ── Step 1: Refresh market state ──────────────────────────────
            ms.update_from_orderbook(tick)

            if ms.mid_price <= 0:
                continue  # skip ticks with no usable price

            # ── Step 2: Regime detection ──────────────────────────────────
            prices = ms.price_history_prices
            regime = regime_detector.detect(prices)
            ms.regime = regime

            # ── Step 3: Fill detection (matching engine, not exchange) ─────
            position_before_fills = fill_tracker.position
            new_fills = matching_engine.check_fills(tick, fill_tracker)
            result.fill_log.extend(new_fills)
            for f in new_fills:
                if f.side == "buy":
                    result.bid_fills += 1
                    result.maker_bid_fills += 1
                    result.bid_fill_base_qty += f.size
                else:
                    result.ask_fills += 1
                    result.maker_ask_fills += 1
                    result.ask_fill_base_qty += f.size
                if f.size < self.cfg.fixed_lot_size - 1e-12:
                    result.partial_fills += 1
            if (
                abs(position_before_fills) > 1e-12
                and abs(fill_tracker.position) <= 1e-12
                and new_fills
            ):
                result.completed_round_trips += 1

            # Positive funding means longs pay and shorts receive.
            funding_pnl -= (
                fill_tracker.position
                * ms.mid_price
                * self.cfg.funding_rate_per_day
                / result.ticks_per_day
            )

            # ── Step 4: P&L update ────────────────────────────────────────
            unrealized = fill_tracker.compute_unrealized_pnl(ms.mid_price)
            equity = self._equity(fill_tracker, unrealized, funding_pnl)
            risk_manager.update_pnl(
                realized_delta=0.0,
                unrealized=unrealized,
                current_equity=equity,
            )

            # ── Step 5: Risk checks ───────────────────────────────────────
            risk_result = risk_manager.check_all(
                inventory=fill_tracker.position,
                mid_price=ms.mid_price,
                avg_entry_price=fill_tracker.avg_entry_price,
                maintenance_margin=(
                    abs(fill_tracker.position)
                    * ms.mid_price
                    * self.cfg.maintenance_margin_rate
                ),
            )

            if risk_result.cancel_all:
                # Kill-switch triggered: cancel all orders
                pending_at_trigger = matching_engine.pending_count
                matching_engine.cancel_all(
                    reason=CancellationReason.CANCEL_RISK_HARD_KILL,
                    source_component="BacktestRunner",
                    source_event="risk_kill_switch",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version="market-maker-backtest-v1",
                    profile_fingerprint="shared-backtest-profile",
                    protocol_id="SHARED_BACKTEST_INFRASTRUCTURE",
                )
                prev_bid_order_id = None
                prev_ask_order_id = None

                # Only record the FIRST trigger event — not every subsequent tick
                # (RiskManager.kill_switch_active stays True until manually reset;
                #  each tick after first activation also returns cancel_all=True)
                if not kill_handled:
                    kill_handled = True
                    trigger_equity = risk_manager.current_equity
                    drawdown_loss = max(0.0, risk_manager.peak_equity - trigger_equity)
                    if max(0.0, -unrealized) >= 0.5 * drawdown_loss:
                        root_cause = "ADVERSE_SELECTION"
                    elif matching_engine.total_fees >= 0.5 * drawdown_loss:
                        root_cause = "FEE_ACCUMULATION"
                    else:
                        root_cause = "EXPECTED_ECONOMIC_LOSS"
                    result.kill_switch_events.append({
                        "tick": iteration,
                        "timestamp": tick.get("timestamp"),
                        "reason": risk_result.reason,
                        "equity": trigger_equity,
                        "drawdown": risk_manager.drawdown_pct,
                        "inventory": fill_tracker.position,
                        "regime": str(regime),
                        "capital": self.cfg.initial_capital,
                        "mid_price": ms.mid_price,
                        "best_bid": ms.best_bid,
                        "best_ask": ms.best_ask,
                        "open_orders": pending_at_trigger,
                        "recent_fills": [
                            {
                                "side": f.side, "price": f.price,
                                "size": f.size, "fee": f.fee,
                            }
                            for f in result.fill_log[-10:]
                        ],
                        "gross_realized_pnl": fill_tracker.gross_realized_pnl,
                        "net_realized_pnl": fill_tracker.realized_pnl,
                        "unrealized_pnl": unrealized,
                        "fees": matching_engine.total_fees,
                        "peak_equity": risk_manager.peak_equity,
                        "drawdown_usdt": drawdown_loss,
                        "drawdown_limit": self.cfg.max_drawdown_pct,
                        "trigger_condition": risk_result.reason,
                        "root_cause_category": root_cause,
                    })
                    result.post_kill_equity_at_trigger = trigger_equity
                    fee_before = matching_engine.flatten_fees
                    slip_before = matching_engine.flatten_slippage
                    flatten = matching_engine.execute_market_flatten(
                        tick,
                        fill_tracker,
                        slippage_bps=self.cfg.emergency_slippage_bps,
                        reason="kill",
                    )
                    if flatten is not None:
                        result.fill_log.append(flatten)
                        result.taker_exit_fills += 1
                    result.flatten_fees = matching_engine.flatten_fees - fee_before
                    result.flatten_slippage = matching_engine.flatten_slippage - slip_before
                    unrealized = fill_tracker.compute_unrealized_pnl(ms.mid_price)
                    equity = self._equity(fill_tracker, unrealized, funding_pnl)
                    risk_manager.update_pnl(unrealized=unrealized, current_equity=equity)
                    result.kill_post_flatten_equity = equity
                    result.kill_switch_events[0].update({
                        "post_flatten_equity": equity,
                        "post_flatten_inventory": fill_tracker.position,
                        "flatten_fees": result.flatten_fees,
                        "flatten_slippage": result.flatten_slippage,
                    })

                # Collect metrics and continue (don't return early — we want
                # the full equity curve for metrics)
                self._collect_tick_metrics(
                    result, risk_manager, fill_tracker, ms,
                    self._position_margin_utilization(
                        fill_tracker.position, ms.mid_price, risk_manager.current_equity
                    ),
                )
                continue

            # ── Step 6: Quote generation ──────────────────────────────────
            if not risk_result.allow_quoting:
                self._collect_tick_metrics(
                    result, risk_manager, fill_tracker, ms,
                    self._position_margin_utilization(
                        fill_tracker.position, ms.mid_price, risk_manager.current_equity
                    ),
                )
                continue

            quotes = quote_engine.generate(
                ms=ms,
                inventory=fill_tracker.position,
                market_info=self.market_info,
                spread_multiplier=regime_detector.get_spread_multiplier(),
                size_multiplier=regime_detector.get_size_multiplier(),
                remaining_drawdown_budget_usdt=max(
                    0.0,
                    risk_manager.peak_equity * self.cfg.max_drawdown_pct
                    - (risk_manager.peak_equity - risk_manager.current_equity),
                ),
            )
            if prev_bid_order_id:
                matching_engine.cancel_order(
                    prev_bid_order_id,
                    reason=CancellationReason.CANCEL_STRATEGY_REPLACE,
                    source_component="BacktestRunner",
                    source_event="quote_refresh_bid",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version="market-maker-backtest-v1",
                    profile_fingerprint="shared-backtest-profile",
                    protocol_id="SHARED_BACKTEST_INFRASTRUCTURE",
                )
            if prev_ask_order_id:
                matching_engine.cancel_order(
                    prev_ask_order_id,
                    reason=CancellationReason.CANCEL_STRATEGY_REPLACE,
                    source_component="BacktestRunner",
                    source_event="quote_refresh_ask",
                    timestamp_ms=int(tick.get("timestamp", 0)),
                    strategy_version="market-maker-backtest-v1",
                    profile_fingerprint="shared-backtest-profile",
                    protocol_id="SHARED_BACKTEST_INFRASTRUCTURE",
                )
            prev_bid_order_id = None
            prev_ask_order_id = None
            pending_existing_requirement = self._pending_opening_requirement(
                matching_engine
            )
            result.quote_updates += 1
            result.post_only_clamp_count += quotes.post_only_clamps

            bid_requirement = (
                0.0 if quotes.bid_reduce_only else
                self._opening_order_requirement(quotes.bid_price, quotes.bid_size)
            ) if quotes.bid_valid else 0.0
            ask_requirement = (
                0.0 if quotes.ask_reduce_only else
                self._opening_order_requirement(quotes.ask_price, quotes.ask_size)
            ) if quotes.ask_valid else 0.0
            risk_budget = max(
                0.0,
                max(0.0, risk_manager.current_equity) * self.cfg.max_margin_utilization
                - pending_existing_requirement,
            )

            # A flat account that cannot reserve both opening orders follows a
            # deterministic alternating-side policy instead of submitting a
            # known-invalid second order.
            if (
                abs(fill_tracker.position) <= 1e-12
                and quotes.bid_valid and quotes.ask_valid
                and bid_requirement + ask_requirement > risk_budget
            ):
                if iteration % 2:
                    quotes.ask_valid = False
                    quotes.ask_skip_reason = "LOCAL_RISK_REJECT:TWO_SIDED_MARGIN"
                else:
                    quotes.bid_valid = False
                    quotes.bid_skip_reason = "LOCAL_RISK_REJECT:TWO_SIDED_MARGIN"
                result.local_risk_rejection_count += 1

            if quotes.bid_valid and bid_requirement > risk_budget:
                quotes.bid_valid = False
                quotes.bid_skip_reason = "LOCAL_RISK_REJECT:MARGIN_CAP"
                result.local_risk_rejection_count += 1
            if quotes.ask_valid and ask_requirement > risk_budget:
                quotes.ask_valid = False
                quotes.ask_skip_reason = "LOCAL_RISK_REJECT:MARGIN_CAP"
                result.local_risk_rejection_count += 1

            if not quotes.bid_valid:
                result.local_quote_skip_count += 1
            if not quotes.ask_valid:
                result.local_quote_skip_count += 1

            result.quote_log.append({
                "timestamp": tick.get("timestamp"),
                "tick": iteration,
                "regime": str(regime),
                "capital": self.cfg.initial_capital,
                "mid_price": ms.mid_price,
                "best_bid": ms.best_bid,
                "best_ask": ms.best_ask,
                "reservation_price": quotes.reservation_price,
                "raw_bid_quote": quotes.raw_bid_price,
                "raw_ask_quote": quotes.raw_ask_price,
                "final_bid_quote": quotes.bid_price if quotes.bid_valid else None,
                "final_ask_quote": quotes.ask_price if quotes.ask_valid else None,
                "bid_reduce_only": quotes.bid_reduce_only,
                "ask_reduce_only": quotes.ask_reduce_only,
                "bid_skip_reason": quotes.bid_skip_reason,
                "ask_skip_reason": quotes.ask_skip_reason,
                "inventory_btc": fill_tracker.position,
                "inventory_lots": fill_tracker.position / self.cfg.fixed_lot_size,
                "spread_abs": 2.0 * quotes.half_spread,
                "spread_bps": 2.0 * quotes.half_spread / ms.mid_price * 10_000.0,
                "volatility_input": ms.volatility,
                "gamma": self.cfg.gamma,
                "kappa": self.cfg.k,
                "gross_realized_pnl": fill_tracker.gross_realized_pnl,
                "net_realized_pnl": fill_tracker.realized_pnl,
                "unrealized_pnl": unrealized,
                "fees": matching_engine.total_fees,
                "peak_equity": risk_manager.peak_equity,
                "current_equity": risk_manager.current_equity,
                "drawdown_usdt": risk_manager.peak_equity - risk_manager.current_equity,
                "drawdown_pct": risk_manager.drawdown_pct,
                "risk_budget": risk_budget,
                "post_only_clamps": quotes.post_only_clamps,
            })

            # ── Step 7: Order reconciliation (simplified) ─────────────────
            # Cancel previous resting orders then place fresh quotes
            # (v1: always replace; v2 could add price/size change threshold)
            if quotes.bid_valid:
                prev_bid_order_id = self._validate_and_place(
                    result, matching_engine, "buy", quotes.bid_price,
                    quotes.bid_size, fill_tracker.position,
                    risk_manager.current_equity, ms.best_bid, ms.best_ask,
                    quotes.bid_reduce_only,
                    self._pending_opening_requirement(matching_engine),
                )
            if quotes.ask_valid:
                prev_ask_order_id = self._validate_and_place(
                    result, matching_engine, "sell", quotes.ask_price,
                    quotes.ask_size, fill_tracker.position,
                    risk_manager.current_equity, ms.best_bid, ms.best_ask,
                    quotes.ask_reduce_only,
                    self._pending_opening_requirement(matching_engine),
                )

            current_margin = abs(fill_tracker.position) * ms.mid_price / self.cfg.leverage
            pending_requirement = self._pending_opening_requirement(matching_engine)
            margin_utilization = (
                current_margin + pending_requirement
            ) / risk_manager.current_equity if risk_manager.current_equity > 0 else float("inf")
            if result.quote_log:
                result.quote_log[-1]["margin_utilization"] = margin_utilization

            # ── Step 8: Collect metrics ───────────────────────────────────
            self._collect_tick_metrics(
                result, risk_manager, fill_tracker, ms, margin_utilization
            )

            # Track inventory breach (>80% of max_inventory)
            max_inv = self.cfg.max_inventory
            if max_inv > 0 and abs(fill_tracker.position) > 0.8 * max_inv:
                result.ticks_over_80pct_inventory += 1

        # ── Post-run stats ────────────────────────────────────────────────
        matching_engine.cancel_all(
            reason=CancellationReason.CANCEL_TERMINAL_CLEANUP,
            source_component="BacktestRunner",
            source_event="terminal_finalize",
            timestamp_ms=int(last_tick.get("timestamp", 0)) if last_tick else 0,
            strategy_version="market-maker-backtest-v1",
            profile_fingerprint="shared-backtest-profile",
            protocol_id="SHARED_BACKTEST_INFRASTRUCTURE",
        )
        if last_tick is not None and abs(fill_tracker.position) > 1e-12:
            fee_before = matching_engine.flatten_fees
            slip_before = matching_engine.flatten_slippage
            terminal_fill = matching_engine.execute_market_flatten(
                last_tick,
                fill_tracker,
                slippage_bps=self.cfg.terminal_slippage_bps,
                reason="terminal",
            )
            if terminal_fill is not None:
                result.fill_log.append(terminal_fill)
                result.taker_exit_fills += 1
            result.terminal_fees = matching_engine.flatten_fees - fee_before
            result.terminal_slippage = matching_engine.flatten_slippage - slip_before
            terminal_mid = result.mid_price_curve[-1] if result.mid_price_curve else 0.0
            unrealized = fill_tracker.compute_unrealized_pnl(terminal_mid)
            terminal_equity = self._equity(fill_tracker, unrealized, funding_pnl)
            risk_manager.update_pnl(unrealized=unrealized, current_equity=terminal_equity)
            result.equity_curve.append(terminal_equity)
            result.inventory_curve.append(fill_tracker.position)
            result.drawdown_curve.append(risk_manager.drawdown_pct)

        result.total_fees = matching_engine.total_fees
        result.total_realized_pnl = fill_tracker.realized_pnl
        result.gross_realized_pnl = fill_tracker.gross_realized_pnl
        result.unrealized_pnl = fill_tracker.compute_unrealized_pnl(
            result.mid_price_curve[-1] if result.mid_price_curve else 0.0
        )
        result.funding_pnl = funding_pnl
        result.ending_inventory_base = fill_tracker.position
        result.residual_inventory_base = abs(fill_tracker.position)
        result.post_kill_final_equity = result.equity_curve[-1]
        result.order_reconciliation = matching_engine.reconciliation()

        logger.info("Backtest complete: %s", result.summary())
        return result

    def _collect_tick_metrics(
        self,
        result: BacktestResult,
        risk_manager: RiskManager,
        fill_tracker: FillTracker,
        ms: MarketState,
        margin_utilization: float,
    ) -> None:
        """Append this tick's snapshot to all curve lists."""
        result.equity_curve.append(risk_manager.current_equity)
        result.inventory_curve.append(fill_tracker.position)
        result.drawdown_curve.append(risk_manager.drawdown_pct)
        result.mid_price_curve.append(ms.mid_price)
        result.max_abs_inventory_base = max(
            result.max_abs_inventory_base, abs(fill_tracker.position)
        )
        result.max_margin_utilization = max(
            result.max_margin_utilization, margin_utilization
        )

    def _equity(
        self,
        fill_tracker: FillTracker,
        unrealized: float,
        funding_pnl: float,
    ) -> float:
        return self.cfg.initial_capital + fill_tracker.realized_pnl + unrealized + funding_pnl

    def _position_margin_utilization(
        self,
        inventory_base: float,
        mid_price: float,
        equity: float,
    ) -> float:
        if equity <= 0:
            return float("inf")
        margin = abs(inventory_base) * mid_price / self.cfg.leverage
        return margin / equity

    def _opening_order_requirement(self, price: float, base_size: float) -> float:
        if price <= 0 or base_size <= 0:
            return 0.0
        notional = price * base_size
        return (
            notional / self.cfg.leverage
            + notional * self.cfg.maker_fee_rate
            + notional * (
                self.cfg.taker_fee_rate
                + self.cfg.emergency_slippage_bps / 10_000.0
            )
        )

    def _pending_opening_requirement(self, engine: MatchingEngine) -> float:
        return sum(
            self._opening_order_requirement(order.price, order.size)
            for order in engine.pending_orders
            if not order.reduce_only
        )

    def _validate_and_place(
        self,
        result: BacktestResult,
        matching_engine: MatchingEngine,
        side: str,
        price: float,
        base_size: float,
        current_inventory: float,
        available_equity: float,
        best_bid: float,
        best_ask: float,
        reduce_only: bool,
        reserved_margin: float,
    ) -> str | None:
        """Validate canonical base quantity, then place a simulated order."""
        try:
            contracts = self.market_spec.base_to_contracts(
                Decimal(str(base_size)), exact=True
            )
            validate_order(
                spec=self.market_spec,
                side=side,
                price=Decimal(str(price)),
                amount=contracts,
                best_bid=Decimal(str(best_bid)),
                best_ask=Decimal(str(best_ask)),
                current_inventory=Decimal(str(current_inventory)),
                max_inventory=Decimal(str(self.cfg.max_inventory)),
                available_equity=Decimal(str(max(available_equity, 0.0))),
                leverage=Decimal(str(self.cfg.leverage)),
                maker_fee_rate=Decimal(str(self.cfg.maker_fee_rate)),
                taker_fee_rate=Decimal(str(self.cfg.taker_fee_rate)),
                reduce_only=reduce_only,
                reserved_margin=Decimal(str(reserved_margin)),
                emergency_reserve=Decimal(str(
                    0.0 if reduce_only else price * base_size * (
                        self.cfg.taker_fee_rate
                        + self.cfg.emergency_slippage_bps / 10_000.0
                    )
                )),
            )
            projected = current_inventory + (base_size if side == "buy" else -base_size)
            proposed_requirement = (
                0.0 if reduce_only else self._opening_order_requirement(price, base_size)
            )
            utilization = (
                reserved_margin + proposed_requirement
            ) / available_equity if available_equity > 0 else float("inf")
            if utilization > self.cfg.max_margin_utilization:
                raise OrderValidationError(
                    f"Projected margin utilization {utilization:.6f} exceeds "
                    f"limit {self.cfg.max_margin_utilization:.6f}"
                )
        except (OrderValidationError, ValueError) as exc:
            message = str(exc)
            if "would cross" in message:
                result.rejected_post_only_count += 1
                category = "SIMULATOR_REJECT:POST_ONLY"
            elif "margin" in message.lower() or "equity" in message.lower():
                result.local_risk_rejection_count += 1
                category = "LOCAL_RISK_REJECT:MARGIN"
            else:
                result.invalid_order_count += 1
                if len(result.invalid_order_reasons) < 20:
                    result.invalid_order_reasons.append(message)
                category = "SIMULATOR_REJECT:INVALID"
            result.rejection_events.append({
                "side": side, "reduce_only": reduce_only,
                "size_base": base_size, "price": price,
                "current_inventory": current_inventory,
                "reserved_margin": reserved_margin,
                "category": category, "reason": message,
            })
            return None
        return matching_engine.place_order(
            side, price, base_size, reduce_only=reduce_only
        )


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
    parser.add_argument(
        "--fill-mode", type=str, default="optimistic",
        choices=["optimistic", "probabilistic", "conservative"],
        help="Matching engine fill mode (default: optimistic)",
    )
    parser.add_argument("--fill-seed", type=int, default=None, help="RNG seed for probabilistic fills")
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

    print(f"Running backtest on {len(ticks):,} ticks... (fill_mode={args.fill_mode}, fill_seed={args.fill_seed})")
    runner = BacktestRunner(cfg, fill_mode=args.fill_mode, fill_seed=args.fill_seed)
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
