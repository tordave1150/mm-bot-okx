"""
config.py — Central configuration for the Avellaneda-Stoikov Market Maker Bot.

All tunable parameters live here as a frozen dataclass. Credentials are loaded
from environment variables via python-dotenv — never hardcoded.

Required baseline (AGENTS.md §7):
    initial_capital = 300.0 USDT
    fixed_lot_size  = 0.01
    max_inventory   = fixed_lot_size * max_inventory_lots
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv


class ConfigValidationError(Exception):
    """Raised when configuration values violate safety invariants."""


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the market maker bot.

    Parameters are grouped by subsystem. Defaults are conservative and
    suitable for a ~$300 USDT sandbox account on OKX.
    """

    # ── Exchange ────────────────────────────────────────────────────────
    exchange_name: str = "okx"
    symbol: str = "BTC/USDT:USDT"          # OKX perpetual swap
    sandbox: bool = True                    # True → OKX demo trading
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""               # OKX-specific passphrase

    # ── Strategy mode ───────────────────────────────────────────────────
    strategy_mode: Literal["avellaneda", "volatility"] = "avellaneda"

    # ── Avellaneda-Stoikov parameters ───────────────────────────────────
    gamma: float = 0.034                   # Risk aversion (higher → wider spread)
    k: float = 2.586                       # Order book liquidity/depth parameter
    tau: float = 1.0                       # Time horizon (1 for continuous perps)

    # ── Volatility-based fallback parameters ────────────────────────────
    volatility_multiplier: float = 2.0     # Multiplier on sigma for half-spread
    base_spread_ticks: float = 1.0         # Additive base spread in tick units

    # ── Capital & sizing ────────────────────────────────────────────────
    initial_capital: float = 300.0         # Starting capital in quote currency (USDT)
    leverage: float = 1.0                  # Leverage multiplier (1 = no leverage)
    fixed_lot_size: float = 0.01           # Fixed order size in base currency — MUST remain 0.01

    # ── Inventory ───────────────────────────────────────────────────────
    # max_inventory is computed as fixed_lot_size * max_inventory_lots.
    # Inventory limits are expressed in discrete lots, not floating-point amounts.
    max_inventory_lots: int = 1            # Conservative default; 2 only after validation
    target_inventory: float = 0.0          # Desired neutral inventory level

    # ── Fee rates ───────────────────────────────────────────────────────
    maker_fee_rate: float = 0.0002         # OKX maker fee (0.02%)
    taker_fee_rate: float = 0.0005         # OKX taker fee (0.05%)

    # ── Risk management ─────────────────────────────────────────────────
    max_drawdown_pct: float = 0.031        # ~3.1% drawdown → kill switch
    liquidation_distance_pct: float = 0.10 # Warn/halt if within 10% of liq price
    max_orders_per_second: int = 5         # Rate limit on order actions

    # ── Order management ────────────────────────────────────────────────
    price_change_threshold: float = 0.0001 # Min price change ratio to re-quote
    size_change_threshold: float = 0.10    # Min size change ratio to re-quote
    min_order_lifetime_s: float = 1.0      # Seconds before an order can be replaced
    queue_positioning: Literal["join", "improve"] = "join"

    # ── Market data ─────────────────────────────────────────────────────
    ws_staleness_threshold_s: float = 3.0  # Seconds before WS data is "stale"
    latency_warning_ms: float = 500.0      # Log warning if latency exceeds this
    price_history_length: int = 500        # Rolling price buffer size
    ema_span: int = 20                     # EWMA span for volatility calculation

    # ── Regime detection ────────────────────────────────────────────────
    regime_fast_ema_span: int = 20
    regime_slow_ema_span: int = 50
    regime_rsi_period: int = 14
    regime_rsi_overbought: float = 70.0
    regime_rsi_oversold: float = 30.0
    regime_slope_window: int = 20
    regime_slope_threshold: float = 0.001  # Absolute slope threshold for "trend"
    regime_ema_divergence_threshold: float = 0.001

    # ── Regime-based adjustments ────────────────────────────────────────
    trend_spread_multiplier: float = 2.27  # Widen spreads in trend regime
    trend_size_multiplier: float = 0.5     # Reduce size in trend regime
    range_spread_multiplier: float = 0.8   # Tighten spreads in range regime
    range_size_multiplier: float = 1.0     # Normal size in range regime

    # ── Dashboard ───────────────────────────────────────────────────────
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    dashboard_recent_fills: int = 10      # keep — still used
    dashboard_log_lines: int = 100        # keep — now just an in-memory ring buffer size
    dashboard_open_browser: bool = True   # auto-open the tab on startup

    # ── Persistence ─────────────────────────────────────────────────────
    state_file: str = "bot_state.json"
    state_save_interval: int = 10          # Save state every N iterations
    state_schema_version: int = 1          # Schema version for state recovery

    # ── Strategy loop ───────────────────────────────────────────────────
    sleeptime: float = 0.5                 # Seconds between iterations

    # ── Imbalance skewing ───────────────────────────────────────────────
    imbalance_skew_factor: float = 0.5     # How aggressively to skew on imbalance
    inventory_skew_factor: float = 1.0     # How aggressively to skew on inventory

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = "INFO"                # Console + file log level
    log_dir: str = "."                     # Directory for log files
    log_to_file: bool = True               # Enable file logging
    log_rotation_mb: int = 10              # Max size per log file in MB
    log_backup_count: int = 7              # Number of rotated log backups

    # ── Computed properties ─────────────────────────────────────────────

    @property
    def max_inventory(self) -> float:
        """Max absolute position size, derived from fixed_lot_size * max_inventory_lots."""
        return self.fixed_lot_size * self.max_inventory_lots


def _validate_config(cfg: Config) -> None:
    """Enforce safety invariants on a Config instance.

    Raises ConfigValidationError if any invariant is violated.
    """
    errors: list[str] = []

    # Fixed lot must be exactly 0.01
    if cfg.fixed_lot_size != 0.01:
        errors.append(
            f"fixed_lot_size must be exactly 0.01, got {cfg.fixed_lot_size}"
        )

    # Initial capital must be positive
    if cfg.initial_capital <= 0:
        errors.append(
            f"initial_capital must be > 0, got {cfg.initial_capital}"
        )

    # max_inventory_lots must be a positive integer
    if not isinstance(cfg.max_inventory_lots, int) or cfg.max_inventory_lots < 1:
        errors.append(
            f"max_inventory_lots must be an integer >= 1, got {cfg.max_inventory_lots}"
        )
    if cfg.max_inventory_lots > 10:
        errors.append(
            f"max_inventory_lots must be <= 10 (hard limit), got {cfg.max_inventory_lots}"
        )

    # Sandbox must be True
    if not cfg.sandbox:
        errors.append(
            "sandbox must be True — live trading is not permitted by AGENTS.md"
        )

    # Leverage must be >= 1
    if cfg.leverage < 1.0:
        errors.append(
            f"leverage must be >= 1.0, got {cfg.leverage}"
        )

    # Drawdown threshold sanity
    if not (0.0 < cfg.max_drawdown_pct <= 1.0):
        errors.append(
            f"max_drawdown_pct must be in (0, 1], got {cfg.max_drawdown_pct}"
        )

    # Fee rates must be non-negative
    if cfg.maker_fee_rate < 0 or cfg.taker_fee_rate < 0:
        errors.append(
            f"Fee rates must be >= 0, got maker={cfg.maker_fee_rate}, "
            f"taker={cfg.taker_fee_rate}"
        )

    if errors:
        msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ConfigValidationError(msg)


def load_config(**overrides) -> Config:
    """Load configuration from environment variables and optional overrides.

    Reads .env file from the current directory (or parent directories).
    Environment variables take precedence over defaults; explicit overrides
    take precedence over everything.

    Raises ConfigValidationError if the resulting config violates safety invariants.
    """
    load_dotenv()

    env_values: dict = {}

    # ── Credentials from env ────────────────────────────────────────────
    api_key = os.environ.get("OKX_API_KEY", "")
    if api_key and api_key != "your_api_key_here":
        env_values["api_key"] = api_key

    api_secret = os.environ.get("OKX_SECRET", "")
    if api_secret and api_secret != "your_secret_here":
        env_values["api_secret"] = api_secret

    api_passphrase = os.environ.get("OKX_PASSPHRASE", "")
    if api_passphrase and api_passphrase != "your_passphrase_here":
        env_values["api_passphrase"] = api_passphrase

    sandbox_str = os.environ.get("OKX_SANDBOX", "true")
    env_values["sandbox"] = sandbox_str.lower() in ("true", "1", "yes")

    # ── Merge: defaults ← env ← overrides ──────────────────────────────
    merged = {**env_values, **overrides}
    cfg = Config(**merged)

    # ── Validate invariants ─────────────────────────────────────────────
    _validate_config(cfg)

    return cfg
