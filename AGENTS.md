# AGENTS.md — Replace Terminal Dashboard with Localhost HTML Dashboard

## Objective

Replace the Rich terminal UI (`dashboard.py`) with a browser-based dashboard
served on `localhost` (default `http://127.0.0.1:8765`). The bot keeps
running exactly as before — same `on_trading_iteration` loop, same
subsystems (`MarketState`, `FillTracker`, `OrderManager`, `RiskManager`,
`RegimeDetector`) — only the *presentation layer* changes. The terminal
becomes a plain scrolling log (stdout + `bot.log`, already wired in
`main.py`); all live visuals move to the browser tab.

**Do not touch trading logic.** `strategy.py`'s steps 1–9 (market data,
regime, fills, P&L, risk, quotes, reconciliation, persistence) are
untouched. Only step 10 ("Dashboard update") and the `Dashboard` class
change.

---

## Constraints

- The web server must run **in the same process**, in a background thread,
  so it shares memory with the strategy (no IPC, no separate process, no
  database). Reuse the existing pattern already used for the WebSocket
  thread in `strategy.py` (`_start_ws` / daemon thread).
- The trading loop (`sleeptime = 0.5s`) must never block on a browser
  request. Use a thread-safe snapshot object — the web server thread reads
  the latest snapshot; the strategy thread writes it. No page render or
  HTTP request should ever be awaited from `on_trading_iteration`.
- No build step. No React/Vite/npm. One static `index.html` with inline
  `<style>` and `<script>`, served as a plain string or static file. This
  keeps the deliverable a single Python process + one HTML asset, matching
  the project's "run with `python main.py`" simplicity.
- Update frequency: poll every 500ms from the browser (matches
  `dashboard_refresh_per_second: int = 4` intent closely enough; no need
  for WebSockets/SSE — keep it simple, one `fetch()` interval).
- Dark theme by default (no light-mode toggle needed).
- `rich` dependency can be dropped from `requirements.txt` once the Rich
  `Live` UI is removed — logging still uses the stdlib `logging` module,
  untouched.
- The dashboard must degrade gracefully: if no browser tab is open, the bot
  keeps trading normally (the HTTP server sitting idle is harmless).

---

## New dependencies (`requirements.txt`)

Add:
```
fastapi>=0.110.0
uvicorn>=0.29.0
```
Remove:
```
rich>=13.0.0
```

---

## Architecture

```
main.py                    (unchanged, still the entry point)
├── config.py               + dashboard_host, dashboard_port fields
├── strategy.py              step 10 now calls DashboardState.update() instead of Dashboard.update()
│   ├── dashboard_state.py   NEW — thread-safe in-memory snapshot (replaces Rich-building logic in dashboard.py)
│   ├── web_server.py        NEW — FastAPI app + uvicorn runner in a background thread
│   └── static/index.html    NEW — single-page dashboard (HTML/CSS/JS, no build step)
```

`dashboard.py` is deleted. Its Rich-specific panel-building logic goes away;
its *data shape* (what fields go into the position panel, orders panel,
etc.) becomes the JSON schema returned by the web server.

---

## 1. `config.py` — add dashboard server settings

Add to the `Config` dataclass, under a new `# ── Dashboard ──` section
(replacing the three existing `dashboard_*` fields, which were Rich-specific
and can be dropped: `dashboard_refresh_per_second`, `dashboard_log_lines`):

```python
dashboard_host: str = "127.0.0.1"
dashboard_port: int = 8765
dashboard_recent_fills: int = 10      # keep — still used
dashboard_log_lines: int = 100        # keep — now just an in-memory ring buffer size
dashboard_open_browser: bool = True   # auto-open the tab on startup
```

---

## 2. `dashboard_state.py` (NEW) — thread-safe snapshot store

Replaces the data-holding responsibilities of `Dashboard`. No Rich imports.

```python
"""
dashboard_state.py — Thread-safe in-memory snapshot of bot state for the
HTML dashboard. Written once per trading iteration by the strategy thread,
read on every HTTP request by the web server thread.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import Config


class DashboardState:
    def __init__(self, config: Config):
        self.cfg = config
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._iteration_count = 0
        self._log_lines: deque[dict] = deque(maxlen=config.dashboard_log_lines)
        self._snapshot: dict[str, Any] = {}

    def add_log(self, message: str) -> None:
        with self._lock:
            self._log_lines.append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "message": message,   # may contain [red]/[green]/[yellow]/[dim] tags — see §4
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
        """Read-only copy for the HTTP handler. Never mutated by caller."""
        with self._lock:
            snap = dict(self._snapshot)
            snap["log"] = list(self._log_lines)
            snap["uptime_seconds"] = time.time() - self._start_time
            snap["iteration"] = self._iteration_count
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
                "price_history": ms.price_history_prices[-200:],  # for a sparkline
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
                    "side": o.side, "price": o.price, "size": o.size,
                    "age_seconds": o.age_seconds, "order_id": o.order_id,
                }
                for o in orders
            ],
            "fills": [
                {
                    "time": datetime.fromtimestamp(f.timestamp, tz=timezone.utc).strftime("%H:%M:%S"),
                    "side": f.side, "price": f.price, "size": f.size, "fee": f.fee,
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
```

**Field-for-field, this is the same data `Dashboard.update()` already
receives from `strategy.py`** — no changes needed to what `strategy.py`
passes in, only the class it's passed to.

---

## 3. `web_server.py` (NEW) — FastAPI app + background thread runner

```python
"""
web_server.py — Serves the HTML dashboard and a JSON state endpoint on
localhost. Runs in a daemon thread started from strategy.py.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from config import Config
from dashboard_state import DashboardState

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def build_app(state: DashboardState) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state", response_class=JSONResponse)
    def get_state() -> dict:
        return state.get_snapshot()

    return app


class WebServer:
    """Runs FastAPI/uvicorn in a background daemon thread."""

    def __init__(self, config: Config, state: DashboardState):
        self.cfg = config
        self.state = state
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    def start(self) -> None:
        app = build_app(self.state)
        uv_config = uvicorn.Config(
            app,
            host=self.cfg.dashboard_host,
            port=self.cfg.dashboard_port,
            log_level="warning",   # keep uvicorn quiet — bot.log stays the source of truth
        )
        self._server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._server.run, name="dashboard-http", daemon=True
        )
        self._thread.start()

        url = f"http://{self.cfg.dashboard_host}:{self.cfg.dashboard_port}"
        logger.info("Dashboard available at %s", url)
        if self.cfg.dashboard_open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
```

Note: `uvicorn.Server.run()` creates its own event loop — this is fine
inside a daemon thread and does not conflict with the WebSocket thread's
own `asyncio` loop in `strategy.py`, since each thread has its own loop.

---

## 4. `static/index.html` (NEW) — single-page dashboard

One file, dark theme, no build step, polls `/api/state` every 500ms.
Recreate the same six panels the Rich UI had, laid out as a CSS grid:

- **Header bar** — symbol, mode, uptime, iteration count, regime badge
- **Market Data panel** — mid/microprice/spread/best bid & ask/volatility/
  imbalance/latency/data-status pill (LIVE/STALE), plus a small inline SVG
  sparkline of `price_history` (last 200 points, no chart library needed —
  a `<polyline>` in an inline `<svg>` is enough)
- **Position & P&L panel** — inventory (with a colored progress bar for
  `inventory_pct`: green <50%, yellow <80%, red ≥80% — same thresholds as
  `dashboard.py`), avg entry, realized/unrealized/total P&L (green/red),
  bid/ask VWAP
- **Open Orders table** — side, price, size, age
- **Recent Fills table** — time, side, price, size, fee
- **Risk panel** — drawdown vs. limit (colored bar), kill-switch status
  banner (red pulsing banner when active, matching the 🚨 urgency of the
  old panel)
- **Log pane** — scrolling list of the last `dashboard_log_lines` entries,
  auto-scrolled to bottom, newest at bottom

**Handling the old Rich color tags** (`[green]...[/]`, `[red]...[/]`,
`[yellow]...[/]`, `[dim]...[/]`, `[bold red]...[/]`): since log messages
built elsewhere (e.g. `order_manager.py` reconciliation strings) are plain
text without tags, but `strategy.py` still appends a few tagged strings
(e.g. `"[red]RISK HALT: ...[/]"`, `"[yellow]No market data yet — waiting[/]"`),
write a small JS helper that converts `[color]text[/]` into
`<span class="color">text</span>` before inserting into the log pane. This
avoids having to touch every call site in `strategy.py` that still uses
Rich markup in log strings.

```javascript
function renderTag(s) {
  return s.replace(/\[(\/?)(\w+(?:\s+\w+)?)\]/g, (m, close, tag) =>
    close ? '</span>' : `<span class="tag-${tag.replace(/\s+/g, '-')}">`
  );
}
```
Define matching CSS classes: `.tag-red`, `.tag-green`, `.tag-yellow`,
`.tag-dim`, `.tag-bold-red`.

**Polling loop:**
```javascript
async function tick() {
  try {
    const res = await fetch('/api/state');
    const data = await res.json();
    render(data);
  } catch (e) {
    setConnectionBadge(false);  // show "disconnected" pill, keep retrying
  }
  setTimeout(tick, 500);
}
tick();
```

Keep everything else (exact colors, spacing, fonts) at the implementing
agent's discretion, but follow dark-theme conventions already used
elsewhere in this user's projects: near-black background (`#0d1117`-ish),
monospace font for numeric panels, minimal chrome, no emoji in labels
(numbers should read cleanly at a glance).

---

## 5. `strategy.py` changes

1. Replace the import:
   ```python
   from dashboard import Dashboard
   ```
   with:
   ```python
   from dashboard_state import DashboardState
   from web_server import WebServer
   ```

2. In `initialize()`, replace:
   ```python
   self.dashboard = Dashboard(self.cfg)
   ```
   with:
   ```python
   self.dashboard_state = DashboardState(self.cfg)
   self.web_server = WebServer(self.cfg, self.dashboard_state)
   ```
   and replace:
   ```python
   self.dashboard.start()
   self.dashboard.add_log(...)
   ```
   with:
   ```python
   self.web_server.start()
   self.dashboard_state.add_log(...)
   ```

3. Every other call site currently referencing `self.dashboard.add_log(...)`
   or `self.dashboard.update(...)` (in `on_trading_iteration` and
   `_update_dashboard`) is renamed to `self.dashboard_state.add_log(...)`
   / `self.dashboard_state.update(...)` — **same call signature, same
   keyword arguments**, no other changes needed in `_update_dashboard()`.

4. In `on_abrupt_closing()` (see truncated lines 205–289 — not shown in
   this handoff, implementing agent should locate the existing
   `self.dashboard.stop()` call and replace it with
   `self.web_server.stop()`).

---

## 6. Delete `dashboard.py`

Its responsibilities are fully absorbed by `dashboard_state.py` (data) +
`static/index.html` (presentation). Remove the file and its `rich` import
dependency.

---

## 7. `main.py` changes

None required for the trading loop. Optional: log the dashboard URL at
startup for visibility, e.g. right after `"Config loaded: ..."`:
```python
logger.info("Dashboard will be available at http://%s:%d",
            config.dashboard_host, config.dashboard_port)
```

---

## Acceptance checklist

- [ ] `python main.py` starts the bot **and** a browser tab opens
      automatically at `http://127.0.0.1:8765` showing the dashboard.
- [ ] All six panels (market, position, orders, fills, risk, log) update
      at least every 500ms while the bot is running, with no visible
      flicker or layout shift.
- [ ] Killing the browser tab and reopening it resumes live updates
      immediately (state is server-side, not per-connection).
- [ ] Kill-switch activation is visually unmistakable (red banner) within
      one polling cycle of the event.
- [ ] `Ctrl+C` shuts down cleanly — the `WebServer` thread exits without
      hanging the process (test against `main.py`'s existing
      `_shutdown` / `os._exit` handlers).
- [ ] Terminal output during a run is just plain `logging` lines (same as
      `bot.log`) — no Rich `Live` redraw artifacts.
- [ ] `requirements.txt` no longer lists `rich`; lists `fastapi` and
      `uvicorn`.
- [ ] No changes to trading logic in `quote_engine.py`, `risk_manager.py`,
      `order_manager.py`, `fill_tracker.py`, `regime_detector.py`,
      `market_state.py`, `state_persistence.py`, or `utils.py`.
