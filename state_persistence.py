"""
state_persistence.py — JSON-based state save/load for crash recovery.

Persists enough state (open order IDs, inventory, P&L baseline, known
fill IDs) to resume cleanly after a restart without double-counting
fills or losing track of open orders.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class StatePersistence:
    """Save and restore bot state to/from a JSON file.

    Usage:
        sp = StatePersistence("bot_state.json")

        # Each iteration (throttled):
        sp.maybe_save(iteration, interval=10, ...)

        # On startup:
        state = sp.load()
        if state:
            # Restore from state dict
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._last_save_iter: int = 0

    def save(
        self,
        open_order_ids: list[str],
        inventory: float,
        avg_entry_price: float,
        realized_pnl: float,
        peak_equity: float,
        known_fill_ids: list[str],
        iteration: int = 0,
    ) -> None:
        """Persist current state to disk."""
        state = {
            "timestamp": time.time(),
            "iteration": iteration,
            "open_order_ids": open_order_ids,
            "inventory": inventory,
            "avg_entry_price": avg_entry_price,
            "realized_pnl": realized_pnl,
            "peak_equity": peak_equity,
            "known_fill_ids": known_fill_ids[-500:],  # Cap to avoid bloat
        }

        try:
            tmp_path = self.filepath + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            # Atomic rename (as atomic as the OS allows)
            if os.path.exists(self.filepath):
                os.replace(tmp_path, self.filepath)
            else:
                os.rename(tmp_path, self.filepath)

            logger.debug("State saved (iter=%d)", iteration)

        except Exception:
            logger.exception("Failed to save state to %s", self.filepath)

    def maybe_save(self, iteration: int, interval: int, **kwargs) -> None:
        """Save state every *interval* iterations."""
        if iteration - self._last_save_iter >= interval:
            self.save(iteration=iteration, **kwargs)
            self._last_save_iter = iteration

    def load(self) -> dict[str, Any] | None:
        """Load state from disk.  Returns None if no state file exists."""
        if not os.path.exists(self.filepath):
            logger.info("No state file found at %s — starting fresh", self.filepath)
            return None

        try:
            with open(self.filepath, "r") as f:
                state = json.load(f)

            age = time.time() - state.get("timestamp", 0)
            logger.info(
                "Loaded state from %s (iter=%d, age=%.0fs)",
                self.filepath,
                state.get("iteration", 0),
                age,
            )
            return state

        except Exception:
            logger.exception("Failed to load state from %s", self.filepath)
            return None

    def clear(self) -> None:
        """Delete the state file."""
        try:
            if os.path.exists(self.filepath):
                os.remove(self.filepath)
                logger.info("State file %s deleted", self.filepath)
        except Exception:
            logger.exception("Failed to delete state file %s", self.filepath)
