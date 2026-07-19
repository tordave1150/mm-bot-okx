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
        schema_version: int = 1,
    ) -> None:
        """Persist current state to disk."""
        state = {
            "schema_version": schema_version,
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
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Atomic rename (as atomic as the OS allows)
            os.replace(tmp_path, self.filepath)

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
            with open(self.filepath, "r", encoding="utf-8") as f:
                state = json.load(f)

            age = time.time() - state.get("timestamp", 0)
            logger.info(
                "Loaded state from %s (schema=%d, iter=%d, age=%.0fs)",
                self.filepath,
                state.get("schema_version", 0),
                state.get("iteration", 0),
                age,
            )
            return state

        except json.JSONDecodeError as e:
            logger.error(
                "Corrupted state file %s: %s — starting fresh",
                self.filepath, e,
            )
            # Rename corrupted file for forensics
            try:
                corrupt_path = self.filepath + ".corrupt"
                os.replace(self.filepath, corrupt_path)
                logger.info("Moved corrupted state to %s", corrupt_path)
            except Exception:
                pass
            return None
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
