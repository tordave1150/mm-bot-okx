"""
dashboard.py — Live terminal dashboard using the Rich library.

Displays market data, position, P&L, open orders, recent fills, risk
metrics, and a scrolling log pane.  Updated each iteration via
``Dashboard.update()``.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import Config
from fill_tracker import Fill
from order_manager import TrackedOrder

logger = logging.getLogger(__name__)


class Dashboard:
    """Rich-based live terminal dashboard for the market maker bot.

    Usage:
        dash = Dashboard(config)
        dash.start()
        # each iteration:
        dash.update(market_state=..., ...)
        # on shutdown:
        dash.stop()
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._live: Live | None = None
        self._start_time = time.time()
        self._log_lines: deque[str] = deque(maxlen=config.dashboard_log_lines)
        self._iteration_count: int = 0

    def start(self) -> None:
        """Start the Rich Live display."""
        self._start_time = time.time()
        self._live = Live(
            self._build_layout_placeholder(),
            refresh_per_second=self.cfg.dashboard_refresh_per_second,
            screen=False,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the Rich Live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def add_log(self, message: str) -> None:
        """Add a log line to the scrolling log pane."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_lines.append(f"[dim]{ts}[/dim] {message}")

    def update(
        self,
        market_state: Any = None,
        inventory: float = 0.0,
        avg_entry_price: float = 0.0,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        open_orders: list[TrackedOrder] | None = None,
        recent_fills: list[Fill] | None = None,
        drawdown_pct: float = 0.0,
        kill_switch_active: bool = False,
        kill_switch_reason: str = "",
        regime: str = "range",
        bid_vwap: float = 0.0,
        ask_vwap: float = 0.0,
        actions: list[str] | None = None,
    ) -> None:
        """Rebuild and refresh the dashboard with current data."""
        self._iteration_count += 1

        if actions:
            for a in actions:
                self.add_log(a)

        layout = self._build_layout(
            market_state=market_state,
            inventory=inventory,
            avg_entry_price=avg_entry_price,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            open_orders=open_orders or [],
            recent_fills=recent_fills or [],
            drawdown_pct=drawdown_pct,
            kill_switch_active=kill_switch_active,
            kill_switch_reason=kill_switch_reason,
            regime=regime,
            bid_vwap=bid_vwap,
            ask_vwap=ask_vwap,
        )

        if self._live:
            self._live.update(layout)

    # ── Layout construction ─────────────────────────────────────────────

    def _build_layout_placeholder(self) -> Panel:
        return Panel("Starting bot...", title="Avellaneda-Stoikov Market Maker")

    def _build_layout(self, **kw) -> Layout:
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="log", size=self.cfg.dashboard_log_lines + 2),
        )

        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        layout["left"].split_column(
            Layout(name="market", ratio=1),
            Layout(name="position", ratio=1),
        )

        layout["right"].split_column(
            Layout(name="orders", ratio=1),
            Layout(name="fills", ratio=1),
            Layout(name="risk", size=6),
        )

        # ── Header ──────────────────────────────────────────────────────
        uptime = time.time() - self._start_time
        hours, rem = divmod(int(uptime), 3600)
        mins, secs = divmod(rem, 60)
        header_text = Text()
        header_text.append("  A-S Market Maker", style="bold cyan")
        header_text.append(f"  │  {self.cfg.symbol}", style="white")
        header_text.append(f"  │  Mode: {self.cfg.strategy_mode}", style="yellow")
        header_text.append(f"  │  Uptime: {hours:02d}:{mins:02d}:{secs:02d}", style="green")
        header_text.append(f"  │  Iter: {self._iteration_count}", style="dim")
        layout["header"].update(Panel(header_text))

        # ── Market Data ─────────────────────────────────────────────────
        layout["market"].update(self._build_market_panel(kw.get("market_state"), kw["regime"]))

        # ── Position ────────────────────────────────────────────────────
        layout["position"].update(
            self._build_position_panel(
                kw["inventory"],
                kw["avg_entry_price"],
                kw["realized_pnl"],
                kw["unrealized_pnl"],
                kw["bid_vwap"],
                kw["ask_vwap"],
            )
        )

        # ── Orders ──────────────────────────────────────────────────────
        layout["orders"].update(self._build_orders_panel(kw["open_orders"]))

        # ── Fills ───────────────────────────────────────────────────────
        layout["fills"].update(self._build_fills_panel(kw["recent_fills"]))

        # ── Risk ────────────────────────────────────────────────────────
        layout["risk"].update(
            self._build_risk_panel(
                kw["drawdown_pct"],
                kw["kill_switch_active"],
                kw["kill_switch_reason"],
            )
        )

        # ── Log ─────────────────────────────────────────────────────────
        log_text = "\n".join(self._log_lines) if self._log_lines else "[dim]No log entries yet[/dim]"
        layout["log"].update(Panel(log_text, title="Log", border_style="dim"))

        return layout

    # ── Panel builders ──────────────────────────────────────────────────

    def _build_market_panel(self, ms: Any, regime: str) -> Panel:
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("Label", style="dim", ratio=1)
        table.add_column("Value", ratio=2)

        if ms and ms.mid_price > 0:
            table.add_row("Mid Price", f"[bold white]{ms.mid_price:,.2f}[/]")
            table.add_row("Microprice", f"{ms.microprice:,.2f}")
            table.add_row("Spread", f"{ms.spread:,.4f}")
            table.add_row("Best Bid", f"[green]{ms.best_bid:,.2f}[/] ({ms.best_bid_size:.4f})")
            table.add_row("Best Ask", f"[red]{ms.best_ask:,.2f}[/] ({ms.best_ask_size:.4f})")
            table.add_row("Volatility", f"{ms.volatility:.6f}")
            table.add_row("Imbalance", self._colorize_value(ms.order_book_imbalance, "{:.4f}"))
            table.add_row("Latency", f"{ms.latency_ms:.0f} ms")
            stale_str = "[red]STALE[/]" if ms.is_stale else "[green]LIVE[/]"
            table.add_row("Data Status", stale_str)
        else:
            table.add_row("Status", "[yellow]Waiting for data...[/]")

        regime_style = "red" if regime == "trend" else "green"
        table.add_row("Regime", f"[{regime_style}]{regime.upper()}[/]")

        return Panel(table, title="Market Data", border_style="blue")

    def _build_position_panel(
        self, inventory: float, avg_entry: float,
        realized: float, unrealized: float,
        bid_vwap: float, ask_vwap: float,
    ) -> Panel:
        table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        table.add_column("Label", style="dim", ratio=1)
        table.add_column("Value", ratio=2)

        inv_pct = (abs(inventory) / self.cfg.max_inventory * 100) if self.cfg.max_inventory > 0 else 0
        inv_color = "green" if inv_pct < 50 else ("yellow" if inv_pct < 80 else "red")

        table.add_row("Inventory", f"[{inv_color}]{inventory:+.6f}[/]")
        table.add_row("Inventory %", f"[{inv_color}]{inv_pct:.1f}%[/]")
        table.add_row("Avg Entry", f"{avg_entry:,.2f}" if avg_entry > 0 else "-")
        table.add_row("Realized P&L", self._colorize_pnl(realized))
        table.add_row("Unrealized P&L", self._colorize_pnl(unrealized))
        table.add_row("Total P&L", self._colorize_pnl(realized + unrealized))
        table.add_row("Bid VWAP", f"{bid_vwap:,.2f}" if bid_vwap > 0 else "-")
        table.add_row("Ask VWAP", f"{ask_vwap:,.2f}" if ask_vwap > 0 else "-")

        return Panel(table, title="Position & P&L", border_style="magenta")

    def _build_orders_panel(self, orders: list[TrackedOrder]) -> Panel:
        table = Table(expand=True, box=None, padding=(0, 1))
        table.add_column("Side", style="bold", width=6)
        table.add_column("Price", width=14)
        table.add_column("Size", width=12)
        table.add_column("Age", width=8)

        for o in orders:
            side_str = f"[green]BUY[/]" if o.side == "buy" else f"[red]SELL[/]"
            age_s = o.age_seconds
            if age_s < 60:
                age_str = f"{age_s:.0f}s"
            else:
                age_str = f"{age_s / 60:.1f}m"
            table.add_row(side_str, f"{o.price:,.2f}", f"{o.size:.6f}", age_str)

        if not orders:
            table.add_row("[dim]—[/]", "[dim]No open orders[/]", "", "")

        return Panel(table, title="Open Orders", border_style="cyan")

    def _build_fills_panel(self, fills: list[Fill]) -> Panel:
        table = Table(expand=True, box=None, padding=(0, 1))
        table.add_column("Time", width=10)
        table.add_column("Side", width=6)
        table.add_column("Price", width=14)
        table.add_column("Size", width=10)
        table.add_column("Fee", width=10)

        for f in fills[-self.cfg.dashboard_recent_fills:]:
            side_str = "[green]BUY[/]" if f.side == "buy" else "[red]SELL[/]"
            t = datetime.fromtimestamp(f.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
            table.add_row(t, side_str, f"{f.price:,.2f}", f"{f.size:.6f}", f"{f.fee:.6f}")

        if not fills:
            table.add_row("[dim]—[/]", "[dim]No fills yet[/]", "", "", "")

        return Panel(table, title="Recent Fills", border_style="yellow")

    def _build_risk_panel(
        self, drawdown: float, kill: bool, kill_reason: str
    ) -> Panel:
        parts: list[str] = []
        dd_color = "green" if drawdown < 0.02 else ("yellow" if drawdown < 0.04 else "red")
        parts.append(f"  Drawdown: [{dd_color}]{drawdown:.2%}[/] / {self.cfg.max_drawdown_pct:.2%}")

        if kill:
            parts.append(f"  Kill Switch: [bold red]🚨 ACTIVE — {kill_reason}[/]")
        else:
            parts.append("  Kill Switch: [green]OFF[/]")

        text = "\n".join(parts)
        border = "red" if kill else "green"
        return Panel(text, title="Risk", border_style=border)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _colorize_pnl(value: float) -> str:
        if value > 0:
            return f"[green]+{value:,.4f}[/]"
        elif value < 0:
            return f"[red]{value:,.4f}[/]"
        return f"[dim]{value:,.4f}[/]"

    @staticmethod
    def _colorize_value(value: float, fmt: str = "{:.4f}") -> str:
        if value > 0:
            return f"[green]{fmt.format(value)}[/]"
        elif value < 0:
            return f"[red]{fmt.format(value)}[/]"
        return f"[dim]{fmt.format(value)}[/]"
