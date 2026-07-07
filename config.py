"""
config.py — Central configuration for the Avellaneda-Stoikov Market Maker Bot.

All tunable parameters live here as a frozen dataclass. Credentials are loaded
from environment variables via python-dotenv — never hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the market maker bot.

    Parameters are grouped by subsystem. Defaults are conservative and
    suitable for a ~$1,000 USDT sandbox account on OKX.
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
    gamma: float = 0.1                     # Risk aversion (higher → wider spread)
    k: float = 1.5                         # Order book liquidity/depth parameter
    tau: float = 1.0                       # Time horizon (1 for continuous perps)

    # ── Volatility-based fallback parameters ────────────────────────────
    volatility_multiplier: float = 2.0     # Multiplier on sigma for half-spread
    base_spread_ticks: float = 1.0         # Additive base spread in tick units

    # ── Inventory ───────────────────────────────────────────────────────
    max_inventory: float = 0.01            # Max absolute position size (in base)
    target_inventory: float = 0.0          # Desired neutral inventory level

    # ── Capital & sizing ────────────────────────────────────────────────
    initial_capital: float = 1000.0        # Starting capital in quote currency
    leverage: float = 1.0                  # Leverage multiplier (1 = no leverage)
    order_size_base: float = 0.001         # Base order size in base currency
    order_size_pct: float = 0.05           # Order size as % of capital (alt mode)
    use_pct_sizing: bool = False           # If True, use order_size_pct instead

    # ── Risk management ─────────────────────────────────────────────────
    max_drawdown_pct: float = 0.05         # 5% drawdown → kill switch
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
    trend_spread_multiplier: float = 1.5   # Widen spreads in trend regime
    trend_size_multiplier: float = 0.5     # Reduce size in trend regime
    range_spread_multiplier: float = 0.8   # Tighten spreads in range regime
    range_size_multiplier: float = 1.0     # Normal size in range regime

    # ── Dashboard ───────────────────────────────────────────────────────
    dashboard_refresh_per_second: int = 4
    dashboard_log_lines: int = 20
    dashboard_recent_fills: int = 10

    # ── Persistence ─────────────────────────────────────────────────────
    state_file: str = "bot_state.json"
    state_save_interval: int = 10          # Save state every N iterations

    # ── Strategy loop ───────────────────────────────────────────────────
    sleeptime: float = 0.5                 # Seconds between iterations

    # ── Imbalance skewing ───────────────────────────────────────────────
    imbalance_skew_factor: float = 0.5     # How aggressively to skew on imbalance
    inventory_skew_factor: float = 1.0     # How aggressively to skew on inventory


def load_config(**overrides) -> Config:
    """Load configuration from environment variables and optional overrides.

    Reads .env file from the current directory (or parent directories).
    Environment variables take precedence over defaults; explicit overrides
    take precedence over everything.
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
    return Config(**merged)
