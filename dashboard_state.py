"""
dashboard_state.py — Thread-safe in-memory snapshot of bot state for the
HTML dashboard. Written once per trading iteration by the strategy thread,
read on every HTTP request by the web server thread.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from config import Config


class DashboardState:
    """Thread-safe snapshot store for the browser dashboard.

    The strategy thread calls ``update()`` and ``add_log()`` while the web
    server thread calls ``get_snapshot()`` to serve the latest data to the
    browser.  A single ``threading.Lock`` protects all shared state.
    """

    def __init__(self, config: Config, session_mode: str = "live"):
        self.cfg = config
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._iteration_count = 0
        self._log_lines: deque[dict] = deque(maxlen=config.dashboard_log_lines)
        self._snapshot: dict[str, Any] = {}
        self._session_mode: str = session_mode  # "live" or "backtest"
        self._stop_requested: bool = False

    # ── Stop / Resume control ───────────────────────────────────────────

    def request_stop(self) -> None:
        """Request the bot to pause quoting (called from web API)."""
        with self._lock:
            self._stop_requested = True

    def clear_stop(self) -> None:
        """Resume the bot after a stop request (called from web API)."""
        with self._lock:
            self._stop_requested = False

    def is_stop_requested(self) -> bool:
        """Check whether a stop has been requested (called from strategy loop)."""
        with self._lock:
            return self._stop_requested

    # ── Logging ────────────────────────────────────────────────────────────

    def add_log(self, message: str) -> None:
        """Append a timestamped message to the log ring buffer."""
        with self._lock:
            self._log_lines.append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "message": message,   # may contain [red]/[green]/[yellow]/[dim] tags
            })

    def update(self, **kw) -> None:
        """Called once per iteration from strategy.py in place of Dashboard.update()."""
        with self._lock:
            self._iteration_count += 1
            if kw.get("actions"):
                for a in kw["actions"]:
                    self._log_lines.append({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "message": a,
                    })
            self._snapshot = self._build_snapshot(kw)

    def get_snapshot(self) -> dict:
        """Return a read-only copy of the latest snapshot for the HTTP handler."""
        with self._lock:
            snap = dict(self._snapshot)
            snap["log"] = list(self._log_lines)
            snap["uptime_seconds"] = time.time() - self._start_time
            snap["iteration"] = self._iteration_count
            snap["control"] = {
                "session_mode": self._session_mode,
                "stop_requested": self._stop_requested,
            }
            return snap

    def _build_snapshot(self, kw: dict) -> dict:
        ms = kw.get("market_state")
        orders = kw.get("open_orders") or []
        fills = kw.get("recent_fills") or []

        market = None
        if ms and getattr(ms, "mid_price", 0) > 0:
            market = {
                "mid_price": ms.mid_price,
                "microprice": ms.microprice,
                "spread": ms.spread,
                "best_bid": ms.best_bid,
                "best_bid_size": ms.best_bid_size,
                "best_ask": ms.best_ask,
                "best_ask_size": ms.best_ask_size,
                "volatility": ms.volatility,
                "imbalance": ms.order_book_imbalance,
                "latency_ms": ms.latency_ms,
                "is_stale": ms.is_stale,
                "price_history": ms.price_history_prices[-200:],  # for the sparkline
            }

        inv = kw.get("inventory", 0.0)
        max_inv = self.cfg.max_inventory

        return {
            "symbol": self.cfg.symbol,
            "strategy_mode": self.cfg.strategy_mode,
            "regime": kw.get("regime", "range"),
            "market": market,
            "position": {
                "inventory": inv,
                "inventory_pct": (abs(inv) / max_inv * 100) if max_inv > 0 else 0,
                "avg_entry_price": kw.get("avg_entry_price", 0.0),
                "realized_pnl": kw.get("realized_pnl", 0.0),
                "unrealized_pnl": kw.get("unrealized_pnl", 0.0),
                "bid_vwap": kw.get("bid_vwap", 0.0),
                "ask_vwap": kw.get("ask_vwap", 0.0),
            },
            "orders": [
                {
                    "side": o.side,
                    "price": o.price,
                    "size": o.size,
                    "age_seconds": o.age_seconds,
                    "order_id": o.order_id,
                }
                for o in orders
            ],
            "fills": [
                {
                    "time": datetime.fromtimestamp(f.timestamp, tz=timezone.utc).strftime("%H:%M:%S"),
                    "side": f.side,
                    "price": f.price,
                    "size": f.size,
                    "fee": f.fee,
                }
                for f in fills[-self.cfg.dashboard_recent_fills:]
            ],
            "risk": {
                "drawdown_pct": kw.get("drawdown_pct", 0.0),
                "max_drawdown_pct": self.cfg.max_drawdown_pct,
                "kill_switch_active": kw.get("kill_switch_active", False),
                "kill_switch_reason": kw.get("kill_switch_reason", ""),
            },
        }
