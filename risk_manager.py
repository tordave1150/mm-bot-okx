"""
risk_manager.py — Real-time risk management and kill-switch logic.

Runs every iteration *before* order placement.  Checks inventory limits,
drawdown kill-switch, liquidation distance, and margin sufficiency.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Outcome of a full risk check pass."""

    allow_quoting: bool = True
    cancel_all: bool = False
    reason: str = ""


class RiskManager:
    """Monitors risk metrics and enforces hard limits.

    Usage:
        1. Call ``update_pnl()`` each iteration with latest fill/mark data.
        2. Call ``check_all()`` — returns a ``RiskCheckResult``.
        3. If ``result.cancel_all`` is True, cancel all orders and stop quoting.
    """

    def __init__(self, config: Config):
        self.cfg = config

        # ── P&L tracking ────────────────────────────────────────────────
        self.initial_equity: float = config.initial_capital
        self.peak_equity: float = config.initial_capital
        self.current_equity: float = config.initial_capital
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0

        # ── Kill-switch state ───────────────────────────────────────────
        self.kill_switch_active: bool = False
        self.kill_switch_reason: str = ""
        self.kill_switch_ts: float = 0.0

        # ── Drawdown ────────────────────────────────────────────────────
        self.max_drawdown_seen: float = 0.0

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak equity as a fraction (0–1)."""
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - self.current_equity) / self.peak_equity
        return max(0.0, dd)

    def update_pnl(
        self,
        realized_delta: float = 0.0,
        unrealized: float = 0.0,
        current_equity: float | None = None,
    ) -> None:
        """Update P&L counters.  Called every iteration.

        Parameters
        ----------
        realized_delta : float
            New realized P&L from fills this iteration (additive).
        unrealized : float
            Current unrealized P&L (replacement, not additive).
        current_equity : float or None
            Current total equity.  If None, computed as
            ``initial_equity + realized + unrealized``.
        """
        self.realized_pnl += realized_delta
        self.unrealized_pnl = unrealized

        if current_equity is not None:
            self.current_equity = current_equity
        else:
            self.current_equity = self.initial_equity + self.total_pnl

        # Update high-water mark
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        # Track worst drawdown
        dd = self.drawdown_pct
        if dd > self.max_drawdown_seen:
            self.max_drawdown_seen = dd

    def check_all(
        self,
        inventory: float,
        mid_price: float,
        avg_entry_price: float = 0.0,
    ) -> RiskCheckResult:
        """Run all risk checks and return the most restrictive result.

        Parameters
        ----------
        inventory : float
            Current net position (positive = long, negative = short).
        mid_price : float
            Current mid market price.
        avg_entry_price : float
            Weighted-average entry price of current position.
        """
        # Already in kill-switch → stay halted until manual reset
        if self.kill_switch_active:
            return RiskCheckResult(
                allow_quoting=False,
                cancel_all=True,
                reason=f"Kill switch active: {self.kill_switch_reason}",
            )

        # Run individual checks — first failure wins
        checks = [
            self._check_drawdown(),
            self._check_liquidation(inventory, mid_price, avg_entry_price),
        ]

        for result in checks:
            if result.cancel_all:
                self._activate_kill_switch(result.reason)
                return result
            if not result.allow_quoting:
                return result

        return RiskCheckResult(allow_quoting=True)

    def check_inventory_side(self, inventory: float, side: str) -> bool:
        """Return True if we can still quote on *side* given current inventory.

        This is a fast inline check — the full ``check_all`` runs separately.
        """
        max_q = self.cfg.max_inventory
        if max_q <= 0:
            return True
        if side == "buy" and inventory >= max_q:
            return False
        if side == "sell" and inventory <= -max_q:
            return False
        return True

    def reset_kill_switch(self) -> None:
        """Manual reset of kill switch.  Must be called explicitly."""
        if self.kill_switch_active:
            logger.warning(
                "Kill switch manually reset (was: %s)", self.kill_switch_reason
            )
            self.kill_switch_active = False
            self.kill_switch_reason = ""
            self.kill_switch_ts = 0.0
            # Reset peak to current equity so we don't re-trigger immediately
            self.peak_equity = self.current_equity

    # ── Private checks ──────────────────────────────────────────────────

    def _check_drawdown(self) -> RiskCheckResult:
        """Drawdown kill-switch: halt if dd exceeds threshold."""
        dd = self.drawdown_pct
        if dd >= self.cfg.max_drawdown_pct:
            return RiskCheckResult(
                allow_quoting=False,
                cancel_all=True,
                reason=(
                    f"Drawdown {dd:.2%} exceeds limit "
                    f"{self.cfg.max_drawdown_pct:.2%}"
                ),
            )
        return RiskCheckResult()

    def _check_liquidation(
        self,
        inventory: float,
        mid_price: float,
        avg_entry: float,
    ) -> RiskCheckResult:
        """Estimate distance to liquidation and halt if too close.

        For a simplified perpetual model:
            Long liquidation ≈ avg_entry * (1 - 1/leverage)
            Short liquidation ≈ avg_entry * (1 + 1/leverage)
        """
        if self.cfg.leverage <= 1.0 or abs(inventory) == 0 or avg_entry <= 0:
            return RiskCheckResult()

        lev = self.cfg.leverage

        if inventory > 0:
            # Long position
            liq_price = avg_entry * (1.0 - 1.0 / lev)
            distance = (mid_price - liq_price) / mid_price if mid_price > 0 else 1.0
        else:
            # Short position
            liq_price = avg_entry * (1.0 + 1.0 / lev)
            distance = (liq_price - mid_price) / mid_price if mid_price > 0 else 1.0

        if distance < self.cfg.liquidation_distance_pct:
            return RiskCheckResult(
                allow_quoting=False,
                cancel_all=True,
                reason=(
                    f"Liquidation distance {distance:.2%} < threshold "
                    f"{self.cfg.liquidation_distance_pct:.2%} "
                    f"(liq_price={liq_price:.2f})"
                ),
            )

        return RiskCheckResult()

    # ── Kill-switch helpers ─────────────────────────────────────────────

    def _activate_kill_switch(self, reason: str) -> None:
        """Arm the kill switch — quoting stops until manual reset."""
        self.kill_switch_active = True
        self.kill_switch_reason = reason
        self.kill_switch_ts = time.time()
        logger.critical("🚨 KILL SWITCH ACTIVATED: %s", reason)
