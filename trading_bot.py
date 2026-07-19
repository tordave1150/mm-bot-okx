"""
trading_bot.py — Avellaneda-Stoikov Market Maker: plain async trading service.

Replaces the Lumibot Strategy subclass with an explicit async lifecycle:
    1. initialize()  — load exchange, market spec, restore state, start dashboard
    2. run()         — main async loop with WebSocket + trading iterations
    3. shutdown()    — cancel orders, persist state, close connections

Orchestrates the full trading loop each iteration:
    1. Refresh market state (WebSocket primary, REST fallback)
    2. Detect regime + update volatility
    3. Detect fills + update P&L
    4. Run risk checks (kill-switch, drawdown, liquidation)
    5. Generate quotes (A-S or volatility fallback)
    6. Reconcile orders (place/amend/cancel)
    7. Persist state
    8. Update dashboard
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from typing import Any

from config import Config
from dashboard_state import DashboardState
from web_server import WebServer
from fill_tracker import FillTracker
from market_state import MarketState
from order_manager import OrderManager
from quote_engine import QuoteEngine
from regime_detector import RegimeDetector
from risk_manager import RiskManager
from state_persistence import StatePersistence
from utils import fetch_market_info

logger = logging.getLogger(__name__)


class TradingBot:
    """Avellaneda-Stoikov market-making bot on OKX via CCXT/CCXT Pro.

    A plain Python async service — no framework dependency.
    """

    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.stop_event = threading.Event()  # Cross-thread signal for shutdown

        # ── Subsystem instances (created in initialize) ─────────────────
        self.market_state = MarketState()
        self.quote_engine = QuoteEngine(config)
        self.risk_manager = RiskManager(config)
        self.order_manager = OrderManager(config)
        self.fill_tracker = FillTracker(config)
        self.regime_detector = RegimeDetector(config)
        self.state_persistence = StatePersistence(config.state_file)
        self.dashboard_state = DashboardState(config)
        self.web_server = WebServer(config, self.dashboard_state)

        # ── Exchange handle ─────────────────────────────────────────────
        self._exchange: Any = None
        self._market_info: dict = {}
        self._iteration: int = 0

        # ── WebSocket background thread ─────────────────────────────────
        self._ws_thread: threading.Thread | None = None
        self._ws_stop_event = threading.Event()
        self._ws_orderbook: dict | None = None
        self._ws_lock = threading.Lock()
        self._ws_connected: bool = False
        self._ws_reconnect_delay: float = 1.0

    # ── Main lifecycle ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Full bot lifecycle: initialize → loop → shutdown."""
        try:
            self._initialize()

            while not self.stop_event.is_set():
                self._trading_iteration()
                # Async sleep with stop check
                for _ in range(int(self.cfg.sleeptime * 10)):
                    if self.stop_event.is_set():
                        break
                    await asyncio.sleep(0.1)
        except Exception:
            logger.exception("Unhandled exception in trading loop")
        finally:
            self._shutdown()

    # ── Initialize ──────────────────────────────────────────────────────

    def _initialize(self) -> None:
        """Set up all subsystems. Called once at startup."""
        self.market_state.configure(
            ema_span=self.cfg.ema_span,
            price_history_length=self.cfg.price_history_length,
            staleness_threshold_s=self.cfg.ws_staleness_threshold_s,
            latency_warning_ms=self.cfg.latency_warning_ms,
        )

        # ── Restore state if available ──────────────────────────────────
        self._restore_state()

        # ── Start dashboard ─────────────────────────────────────────────
        self.web_server.start()
        self.dashboard_state.add_log(
            f"Bot initialized — {self.cfg.strategy_mode} mode, "
            f"symbol={self.cfg.symbol}, sandbox={self.cfg.sandbox}"
        )

        logger.info("TradingBot initialized: capital=%.1f, lot=%.4f, max_inv_lots=%d",
                     self.cfg.initial_capital, self.cfg.fixed_lot_size,
                     self.cfg.max_inventory_lots)

    # ── Trading iteration ───────────────────────────────────────────────

    def _trading_iteration(self) -> None:
        """Main trading loop — called every sleeptime seconds."""
        self._iteration += 1

        # ── 0a. Dashboard stop check (pause, not kill) ──────────────────
        if self.dashboard_state.is_stop_requested():
            if self._exchange:
                try:
                    self.order_manager.cancel_all(self._exchange, self.cfg.symbol)
                except Exception:
                    logger.exception("Error cancelling orders during stop")
            self.dashboard_state.add_log(
                "[yellow]⏸ Quoting paused — stopped from dashboard[/]"
            )
            self._update_dashboard([])
            return

        # ── 0b. Ensure exchange handle ──────────────────────────────────
        if self._exchange is None:
            self._init_exchange()
            if self._exchange is None:
                self.dashboard_state.add_log("[red]No exchange connection — skipping[/]")
                return

        actions: list[str] = []

        # ── 1. Refresh market state ─────────────────────────────────────
        self._update_market_data()

        if self.market_state.mid_price <= 0:
            self.dashboard_state.add_log("[yellow]No market data yet — waiting[/]")
            self._update_dashboard(actions)
            return

        # ── 2. Regime detection ─────────────────────────────────────────
        prices = self.market_state.price_history_prices
        regime = self.regime_detector.detect(prices)
        self.market_state.regime = regime

        # ── 3. Fill detection ───────────────────────────────────────────
        new_fills = self.fill_tracker.detect_fills(self._exchange, self.cfg.symbol)
        for fill in new_fills:
            actions.append(
                f"Fill: {fill.side} {fill.size:.6f} @ {fill.price:,.2f}"
            )

        # ── 4. P&L update ──────────────────────────────────────────────
        unrealized = self.fill_tracker.compute_unrealized_pnl(
            self.market_state.mid_price
        )

        self.risk_manager.update_pnl(
            realized_delta=0.0,  # FillTracker tracks realized P&L internally
            unrealized=unrealized,
            current_equity=(
                self.cfg.initial_capital
                + self.fill_tracker.realized_pnl
                + unrealized
            ),
        )

        # ── 5. Risk checks ─────────────────────────────────────────────
        risk_result = self.risk_manager.check_all(
            inventory=self.fill_tracker.position,
            mid_price=self.market_state.mid_price,
            avg_entry_price=self.fill_tracker.avg_entry_price,
        )

        if risk_result.cancel_all:
            cancel_actions = self.order_manager.cancel_all(
                self._exchange, self.cfg.symbol
            )
            actions.extend(cancel_actions)
            actions.append(f"[red]RISK HALT: {risk_result.reason}[/]")
            self._update_dashboard(actions)
            self._save_state()
            return

        # ── 6. Quote generation ─────────────────────────────────────────
        quotes = self.quote_engine.generate(
            ms=self.market_state,
            inventory=self.fill_tracker.position,
            market_info=self._market_info,
            spread_multiplier=self.regime_detector.get_spread_multiplier(),
            size_multiplier=self.regime_detector.get_size_multiplier(),
        )

        if not risk_result.allow_quoting:
            self.dashboard_state.add_log(f"[yellow]Quoting paused: {risk_result.reason}[/]")
            self._update_dashboard(actions)
            return

        # ── 7. Order reconciliation ─────────────────────────────────────
        order_actions = self.order_manager.reconcile(
            quotes, self._exchange, self.cfg.symbol, self._market_info
        )
        actions.extend(order_actions)

        # ── 8. Cleanup old closed orders ────────────────────────────────
        self.order_manager.cleanup_closed()

        # ── 9. State persistence ────────────────────────────────────────
        self._save_state_periodic()

        # ── 10. Dashboard update ────────────────────────────────────────
        self._update_dashboard(actions)

    # ── Shutdown ────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        """Graceful shutdown: cancel orders, save state, close connections."""
        logger.warning("Shutting down — cancelling all orders")
        self.dashboard_state.add_log("[red]Shutting down — cancelling all orders[/]")

        if self._exchange:
            try:
                self.order_manager.cancel_all(self._exchange, self.cfg.symbol)
            except Exception:
                logger.exception("Error cancelling orders on shutdown")

            # Close the exchange session
            try:
                self._exchange.close()
            except Exception:
                logger.exception("Error closing exchange session")

        self._save_state()
        self._stop_ws()
        self.web_server.stop()
        logger.info("Shutdown complete")

    # ── Exchange initialisation ─────────────────────────────────────────

    def _init_exchange(self) -> None:
        """Create a CCXT exchange instance and load market info."""
        try:
            import ccxt

            exchange_class = getattr(ccxt, self.cfg.exchange_name)
            self._exchange = exchange_class({
                "apiKey": self.cfg.api_key,
                "secret": self.cfg.api_secret,
                "password": self.cfg.api_passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })

            if self.cfg.sandbox:
                self._exchange.set_sandbox_mode(True)
                logger.info("Sandbox mode enabled on exchange")

            self._market_info = fetch_market_info(
                self._exchange, self.cfg.symbol
            )
            self.dashboard_state.add_log(
                f"Exchange connected: {self.cfg.exchange_name} "
                f"sandbox={self.cfg.sandbox} "
                f"(tick={self._market_info['tick_size']}, "
                f"lot={self._market_info['lot_size']})"
            )
            logger.info(
                "Exchange base URL: %s",
                getattr(self._exchange, 'urls', {}).get('api', 'unknown')
            )
            # Start WebSocket thread
            self._start_ws()

        except Exception:
            logger.exception("Failed to initialise exchange")
            self._exchange = None

    # ── WebSocket Background Thread ─────────────────────────────────────

    def _start_ws(self) -> None:
        """Start the WebSocket order book streaming thread."""
        if self._ws_thread and self._ws_thread.is_alive():
            return

        self._ws_stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._ws_loop,
            name="ws-orderbook",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("WebSocket thread started for %s", self.cfg.symbol)

    def _stop_ws(self) -> None:
        """Signal the WebSocket thread to stop."""
        self._ws_stop_event.set()
        if self._ws_thread:
            self._ws_thread.join(timeout=5)
            self._ws_thread = None

    def _ws_loop(self) -> None:
        """Background thread: stream order book via CCXT Pro WebSocket."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._ws_stream())
        except Exception:
            logger.exception("WebSocket loop crashed")
        finally:
            loop.close()

    async def _ws_stream(self) -> None:
        """Async WebSocket streaming loop with reconnection + jitter."""
        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            logger.warning("ccxt.pro not available — WebSocket disabled, using REST only")
            return

        while not self._ws_stop_event.is_set():
            exchange = None
            try:
                exchange_class = getattr(ccxtpro, self.cfg.exchange_name)
                exchange = exchange_class({
                    "apiKey": self.cfg.api_key,
                    "secret": self.cfg.api_secret,
                    "password": self.cfg.api_passphrase,
                    "enableRateLimit": True,
                    "options": {"defaultType": "swap"},
                })

                if self.cfg.sandbox:
                    exchange.set_sandbox_mode(True)

                self._ws_connected = True
                self._ws_reconnect_delay = 1.0  # Reset backoff on success
                logger.info("WebSocket connected to %s", self.cfg.exchange_name)

                while not self._ws_stop_event.is_set():
                    orderbook = await exchange.watch_order_book(self.cfg.symbol)
                    with self._ws_lock:
                        self._ws_orderbook = orderbook

            except Exception as e:
                self._ws_connected = False
                # Add jitter to backoff (AGENTS.md §8)
                jitter = random.uniform(0, self._ws_reconnect_delay * 0.3)
                delay = self._ws_reconnect_delay + jitter
                logger.warning(
                    "WebSocket error (reconnecting in %.1fs): %s",
                    delay, e,
                )
                await asyncio.sleep(delay)
                # Exponential backoff: 1s → 2s → 4s → … → 30s max
                self._ws_reconnect_delay = min(
                    self._ws_reconnect_delay * 2, 30.0
                )
            finally:
                if exchange:
                    try:
                        await exchange.close()
                    except Exception:
                        logger.debug("Error closing WS exchange session", exc_info=True)

    # ── Market Data Update ──────────────────────────────────────────────

    def _update_market_data(self) -> None:
        """Pull latest data from WebSocket (or REST fallback)."""
        # Try WebSocket data first
        ws_data = None
        with self._ws_lock:
            if self._ws_orderbook is not None:
                ws_data = self._ws_orderbook
                self._ws_orderbook = None  # Consume it

        if ws_data is not None:
            self.market_state.update_from_orderbook(ws_data)
            return

        # If WS data is stale or unavailable, fall back to REST
        if self.market_state.is_stale and self._exchange:
            try:
                ob = self._exchange.fetch_order_book(self.cfg.symbol)
                self.market_state.update_from_orderbook(ob)
                if not self._ws_connected:
                    logger.debug("Using REST fallback for market data")
            except Exception:
                logger.exception("REST order book fetch failed")

    # ── State Management ────────────────────────────────────────────────

    def _restore_state(self) -> None:
        """Restore state from disk on startup."""
        state = self.state_persistence.load()
        if state is None:
            return

        # Check schema version
        schema_version = state.get("schema_version", 0)
        if schema_version != self.cfg.state_schema_version:
            logger.warning(
                "State schema version mismatch: file=%d, expected=%d — starting fresh",
                schema_version, self.cfg.state_schema_version,
            )
            return

        # Restore via public method on FillTracker
        self.fill_tracker.restore_state(
            known_fill_ids=set(state.get("known_fill_ids", [])),
            realized_pnl=state.get("realized_pnl", 0.0),
            position=state.get("inventory", 0.0),
            avg_entry_price=state.get("avg_entry_price", 0.0),
        )

        # Restore risk manager peak equity
        self.risk_manager.peak_equity = state.get("peak_equity", self.cfg.initial_capital)
        self.risk_manager.realized_pnl = state.get("realized_pnl", 0.0)

        self.dashboard_state.add_log(
            f"State restored: pos={state.get('inventory', 0):.6f}, "
            f"realized_pnl={state.get('realized_pnl', 0):.4f}"
        )

    def _save_state(self) -> None:
        """Save full state now."""
        self.state_persistence.save(
            open_order_ids=[o.order_id for o in self.order_manager.open_orders],
            inventory=self.fill_tracker.position,
            avg_entry_price=self.fill_tracker.avg_entry_price,
            realized_pnl=self.fill_tracker.realized_pnl,
            peak_equity=self.risk_manager.peak_equity,
            known_fill_ids=list(self.fill_tracker.known_fill_ids),
            iteration=self._iteration,
            schema_version=self.cfg.state_schema_version,
        )

    def _save_state_periodic(self) -> None:
        """Save state every N iterations."""
        self.state_persistence.maybe_save(
            iteration=self._iteration,
            interval=self.cfg.state_save_interval,
            open_order_ids=[o.order_id for o in self.order_manager.open_orders],
            inventory=self.fill_tracker.position,
            avg_entry_price=self.fill_tracker.avg_entry_price,
            realized_pnl=self.fill_tracker.realized_pnl,
            peak_equity=self.risk_manager.peak_equity,
            known_fill_ids=list(self.fill_tracker.known_fill_ids),
            schema_version=self.cfg.state_schema_version,
        )

    # ── Dashboard ───────────────────────────────────────────────────────

    def _update_dashboard(self, actions: list[str]) -> None:
        """Push latest data to the browser dashboard state snapshot."""
        unrealized = self.fill_tracker.compute_unrealized_pnl(
            self.market_state.mid_price
        )
        self.dashboard_state.update(
            market_state=self.market_state,
            inventory=self.fill_tracker.position,
            avg_entry_price=self.fill_tracker.avg_entry_price,
            realized_pnl=self.fill_tracker.realized_pnl,
            unrealized_pnl=unrealized,
            open_orders=self.order_manager.open_orders,
            recent_fills=self.fill_tracker.get_recent_fills(
                self.cfg.dashboard_recent_fills
            ),
            drawdown_pct=self.risk_manager.drawdown_pct,
            kill_switch_active=self.risk_manager.kill_switch_active,
            kill_switch_reason=self.risk_manager.kill_switch_reason,
            regime=self.market_state.regime,
            bid_vwap=self.fill_tracker.bid_vwap,
            ask_vwap=self.fill_tracker.ask_vwap,
            actions=actions,
        )
