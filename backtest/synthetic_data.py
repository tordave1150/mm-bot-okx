"""
backtest/synthetic_data.py — Synthetic price/order-book tick generators.

Three generation strategies, each returning ``list[dict]`` in CCXT order-book
format (compatible with ``MarketState.update_from_orderbook()``):

    {
        "bids": [[price, size], ...],   # descending price — best bid first
        "asks": [[price, size], ...],   # ascending price — best ask first
        "timestamp": int,               # milliseconds since epoch
    }

Each tick also carries a realistic synthetic spread (wider in high-vol
regimes) and order-book imbalance (biased toward trend direction).

Generators
----------
generate_regime_switching_gbm(...)
    GBM + Markov regime switching (range / trend-up / trend-down) + jumps.

generate_block_bootstrap(...)
    Resample blocks from embedded BTC historical log-return arrays.

generate_garch_path(...)
    Fit GARCH(1,1) to historical prices and simulate a new path.
"""

from __future__ import annotations

import math
import time
from typing import Literal

import numpy as np

# ── Optional dependency: arch (GARCH) ────────────────────────────────────────
try:
    from arch import arch_model
    _ARCH_AVAILABLE = True
except ImportError:
    _ARCH_AVAILABLE = False


# ── Embedded historical log-return block libraries ────────────────────────────
# Derived from well-known BTC/USDT daily closing prices.
# Each value is the daily log-return as a fraction (not percent).
# Resolution: daily.  Used by block-bootstrap generator.

_BLOCK_LIBRARIES: dict[str, np.ndarray] = {
    # BTC 2021 bull run (Jan–Nov 2021): strong upward trend interspersed with
    # corrections.  Approx daily returns drawn from that period.
    "BTC_2021_BULL": np.array([
        0.0523,  0.0210,  0.0387, -0.0215,  0.0502,  0.0321,  0.0189,
       -0.0312,  0.0450,  0.0267,  0.0612, -0.0498,  0.0378,  0.0231,
        0.0189, -0.0287,  0.0412,  0.0356, -0.0197,  0.0523,  0.0098,
       -0.0423,  0.0312,  0.0198,  0.0456, -0.0312,  0.0289,  0.0178,
       -0.0256,  0.0389,
    ]),
    # BTC 2022 bear market (Jan–Dec 2022): extended downtrend, high volatility.
    "BTC_2022_BEAR": np.array([
       -0.0387, -0.0523, -0.0198,  0.0212, -0.0412, -0.0267, -0.0189,
        0.0234, -0.0356, -0.0289, -0.0534,  0.0198, -0.0312, -0.0198,
       -0.0256,  0.0178, -0.0423, -0.0267,  0.0156, -0.0489, -0.0134,
        0.0289, -0.0378, -0.0234, -0.0512,  0.0234, -0.0289, -0.0156,
        0.0198, -0.0412,
    ]),
    # BTC crash events (May 2021 crash, Nov 2022 FTX, etc.): fat-tail drawdowns.
    "BTC_CRASH": np.array([
       -0.1523, -0.0812, -0.0623,  0.0312, -0.1234, -0.0789, -0.0456,
        0.0456, -0.0923, -0.0612,  0.0234, -0.1123, -0.0534, -0.0312,
        0.0289, -0.0823, -0.1234, -0.0567,  0.0445, -0.0923, -0.0712,
       -0.0534,  0.0612, -0.1023, -0.0789, -0.0456,  0.0389, -0.0912,
       -0.0678, -0.0234,
    ]),
}


# ── Spread / imbalance helpers ────────────────────────────────────────────────

def _build_tick(
    mid_price: float,
    vol_annual: float,
    imbalance: float = 0.0,
    ts_ms: int | None = None,
    depth_levels: int = 5,
    rng: np.random.Generator | None = None,
) -> dict:
    """Build a synthetic CCXT-format tick dict.

    Parameters
    ----------
    mid_price : float
    vol_annual : float
        Annualised volatility (used to widen spread in high-vol).
    imbalance : float
        Order book imbalance bias in [-1, 1].  Positive → bid-heavy ticks.
    ts_ms : int | None
        Millisecond timestamp; uses current wall time if None.
    depth_levels : int
        Number of price levels to generate per side.
    rng : np.random.Generator | None
        Random number generator for reproducibility.
    """
    if rng is None:
        rng = np.random.default_rng()
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)

    # Spread widens with volatility: base 0.01% + 2× vol-adjusted premium
    spread_bps = 1.0 + 200.0 * (vol_annual / math.sqrt(252))
    half_spread = mid_price * spread_bps / 20_000.0  # bps → fraction → half

    best_bid = mid_price - half_spread
    best_ask = mid_price + half_spread

    # Sizes — randomised around a base; imbalance tilts bid vs ask volumes
    base_size = 1.0 + rng.exponential(0.5)
    imb_factor = 1.0 + 0.5 * imbalance  # imbalance in (-1, 1)

    def _levels(start_price: float, direction: int, levels: int) -> list[list[float]]:
        """Generate `levels` depth levels stepping away from best price."""
        result = []
        for i in range(levels):
            price = round(start_price + direction * i * 2 * half_spread, 2)
            size = round(base_size * (1.0 + 0.3 * i) * rng.uniform(0.8, 1.2), 6)
            result.append([price, size])
        return result

    bid_size_base = base_size * imb_factor
    ask_size_base = base_size / max(imb_factor, 0.2)

    bids = [[round(best_bid - i * 2 * half_spread, 2),
             round(bid_size_base * (1.0 + 0.3 * i) * rng.uniform(0.8, 1.2), 6)]
            for i in range(depth_levels)]
    asks = [[round(best_ask + i * 2 * half_spread, 2),
             round(ask_size_base * (1.0 + 0.3 * i) * rng.uniform(0.8, 1.2), 6)]
            for i in range(depth_levels)]

    return {
        "bids": bids,
        "asks": asks,
        "timestamp": ts_ms,
    }


# ── Generator 1: Regime-Switching GBM ────────────────────────────────────────

_REGIMES = Literal["range", "trend_up", "trend_down"]

# Markov transition matrix [from_regime][to_regime]
# Rows: range, trend_up, trend_down
# Columns: range, trend_up, trend_down
_DEFAULT_TRANSITION = np.array([
    [0.90, 0.05, 0.05],  # from range
    [0.10, 0.85, 0.05],  # from trend_up
    [0.10, 0.05, 0.85],  # from trend_down
])


def generate_regime_switching_gbm(
    vol_weekly: float = 0.25,
    regime_duration_days: float = 3.0,
    jump_freq: float = 0.2,
    jump_size: float = 0.02,
    n_days: int = 30,
    ticks_per_day: int = 288,   # 5-minute ticks
    start_price: float = 50_000.0,
    seed: int | None = None,
) -> list[dict]:
    """Generate synthetic order book ticks via regime-switching GBM + jumps.

    Parameters
    ----------
    vol_weekly : float
        Weekly volatility (annualised = vol_weekly * sqrt(52)).
    regime_duration_days : float
        Average days spent in each regime before switching.
    jump_freq : float
        Expected number of jumps per day (Poisson rate).
    jump_size : float
        Log-normal jump magnitude (std of log-jump).
    n_days : int
        Number of simulated days.
    ticks_per_day : int
        Number of ticks per day (288 = 5-minute bars for 24h).
    start_price : float
        Starting mid price.
    seed : int | None
        Random seed for reproducibility.

    Returns
    -------
    list[dict]
        CCXT order book dicts compatible with ``MarketState.update_from_orderbook()``.
    """
    rng = np.random.default_rng(seed)

    vol_annual = vol_weekly * math.sqrt(52)
    dt = 1.0 / (252 * ticks_per_day)   # time step in years
    vol_dt = vol_annual * math.sqrt(dt)

    n_ticks = n_days * ticks_per_day
    start_ts_ms = int(time.time() * 1000) - n_ticks * 5 * 60 * 1000  # backfill

    # Regime drift parameters (per-tick log-drift)
    _regime_drift = {
        "range":      0.0,
        "trend_up":   vol_annual * dt * 2.0,    # mild positive drift
        "trend_down": -vol_annual * dt * 2.0,   # mild negative drift
    }
    _regime_vol_mult = {
        "range":      0.7,    # tighter vol in ranging market
        "trend_up":   1.0,
        "trend_down": 1.2,    # trending down is more volatile
    }
    _regime_imbalance = {
        "range":      0.0,
        "trend_up":   0.3,
        "trend_down": -0.3,
    }

    # Markov regime switch probability per tick
    # regime_duration_days → switch_prob per tick
    switch_prob_per_tick = 1.0 / (regime_duration_days * ticks_per_day)
    transition = np.array([
        [1 - switch_prob_per_tick, switch_prob_per_tick / 2, switch_prob_per_tick / 2],
        [switch_prob_per_tick, 1 - switch_prob_per_tick, 0.0],
        [switch_prob_per_tick, 0.0, 1 - switch_prob_per_tick],
    ])
    regimes_list = ["range", "trend_up", "trend_down"]
    current_regime_idx = 0  # start in range

    price = start_price
    ticks: list[dict] = []

    for i in range(n_ticks):
        # Regime transition (Markov)
        row = transition[current_regime_idx]
        current_regime_idx = rng.choice(3, p=row / row.sum())
        regime = regimes_list[current_regime_idx]

        drift = _regime_drift[regime]
        vol_factor = _regime_vol_mult[regime]
        imbalance = _regime_imbalance[regime] + rng.normal(0, 0.1)
        imbalance = float(np.clip(imbalance, -1.0, 1.0))

        # GBM step
        dW = rng.standard_normal()
        log_return = drift + vol_factor * vol_dt * dW

        # Poisson jump
        if rng.random() < jump_freq * dt * 252:  # per-tick jump probability
            jump_direction = rng.choice([-1, 1])
            jump = jump_direction * abs(rng.normal(0, jump_size))
            log_return += jump

        price = price * math.exp(log_return)
        price = max(price, 1.0)  # prevent negative/zero prices

        ts_ms = start_ts_ms + i * 5 * 60 * 1000
        effective_vol = vol_annual * _regime_vol_mult[regime]

        tick = _build_tick(
            mid_price=price,
            vol_annual=effective_vol,
            imbalance=imbalance,
            ts_ms=ts_ms,
            rng=rng,
        )
        ticks.append(tick)

    return ticks


# ── Generator 2: Block Bootstrap ─────────────────────────────────────────────

def generate_block_bootstrap(
    block_libraries: list[str] | None = None,
    block_size: int = 5,
    n_days: int = 30,
    ticks_per_day: int = 288,
    start_price: float = 50_000.0,
    seed: int | None = None,
) -> list[dict]:
    """Generate ticks by resampling blocks from historical log-return arrays.

    Parameters
    ----------
    block_libraries : list[str] | None
        Names of block libraries to draw from.  Choices:
        ``"BTC_2021_BULL"``, ``"BTC_2022_BEAR"``, ``"BTC_CRASH"``.
        Defaults to all three mixed equally.
    block_size : int
        Number of daily returns per resampled block.
    n_days : int
        Number of simulated days.
    ticks_per_day : int
        Number of ticks per day (returns are interpolated intraday).
    start_price : float
        Starting mid price.
    seed : int | None
        Random seed.

    Returns
    -------
    list[dict]
    """
    rng = np.random.default_rng(seed)

    if block_libraries is None:
        block_libraries = list(_BLOCK_LIBRARIES.keys())

    # Concatenate chosen libraries into a single return pool
    pool = np.concatenate([_BLOCK_LIBRARIES[name] for name in block_libraries])

    # Build daily return sequence by sampling blocks
    daily_returns: list[float] = []
    while len(daily_returns) < n_days:
        start_idx = rng.integers(0, max(1, len(pool) - block_size))
        block = pool[start_idx: start_idx + block_size]
        daily_returns.extend(block.tolist())

    daily_returns = daily_returns[:n_days]

    # Compute daily volatility for spread calculation
    daily_vol = float(np.std(daily_returns)) if len(daily_returns) > 1 else 0.02

    # Interpolate intraday ticks from daily returns
    n_ticks = n_days * ticks_per_day
    start_ts_ms = int(time.time() * 1000) - n_ticks * 5 * 60 * 1000

    price = start_price
    ticks: list[dict] = []
    tick_idx = 0

    for day_idx, daily_ret in enumerate(daily_returns):
        # Spread daily return across ticks (with intraday noise)
        intraday_drift = daily_ret / ticks_per_day
        intraday_vol = daily_vol / math.sqrt(252 * ticks_per_day)

        for t in range(ticks_per_day):
            noise = rng.normal(0, intraday_vol)
            price = price * math.exp(intraday_drift + noise)
            price = max(price, 1.0)

            # Imbalance follows sign of daily return
            imbalance = float(np.sign(daily_ret)) * 0.2 + rng.normal(0, 0.1)
            imbalance = float(np.clip(imbalance, -1.0, 1.0))

            ts_ms = start_ts_ms + tick_idx * 5 * 60 * 1000
            tick = _build_tick(
                mid_price=price,
                vol_annual=daily_vol * math.sqrt(252),
                imbalance=imbalance,
                ts_ms=ts_ms,
                rng=rng,
            )
            ticks.append(tick)
            tick_idx += 1

    return ticks[:n_ticks]


# ── Generator 3: GARCH(1,1) ──────────────────────────────────────────────────

def generate_garch_path(
    historical_prices: list[float],
    n_days: int = 30,
    ticks_per_day: int = 288,
    seed: int | None = None,
) -> list[dict]:
    """Fit GARCH(1,1) to historical prices and simulate a new path.

    Requires the ``arch`` package.  Falls back to regime-switching GBM
    with a vol estimate derived from the historical data if ``arch`` is
    not installed.

    Parameters
    ----------
    historical_prices : list[float]
        Historical price series (daily or sub-daily).
    n_days : int
        Number of days to simulate.
    ticks_per_day : int
        Number of ticks per simulated day.
    seed : int | None
        Random seed.

    Returns
    -------
    list[dict]
    """
    rng = np.random.default_rng(seed)

    prices_arr = np.array(historical_prices, dtype=float)
    if len(prices_arr) < 10:
        raise ValueError("Need at least 10 historical prices to fit GARCH(1,1)")

    log_returns = np.diff(np.log(prices_arr))
    mean_ret = float(np.mean(log_returns))
    daily_vol = float(np.std(log_returns))
    start_price = float(prices_arr[-1])

    if not _ARCH_AVAILABLE:
        # Graceful fallback: use historical vol estimate in regime-switching GBM
        weekly_vol = daily_vol * math.sqrt(5)
        return generate_regime_switching_gbm(
            vol_weekly=weekly_vol,
            n_days=n_days,
            ticks_per_day=ticks_per_day,
            start_price=start_price,
            seed=seed,
        )

    # Fit GARCH(1,1) — use percentage returns for numerical stability
    pct_returns = log_returns * 100.0
    am = arch_model(pct_returns, vol="Garch", p=1, q=1, mean="Zero", dist="normal")
    res = am.fit(disp="off", show_warning=False)

    # Simulate from fitted model
    n_total = n_days * ticks_per_day
    # arch simulation is at the same frequency as input data; we scale
    sim = res.model.simulate(res.params, n_total)
    simulated_pct_returns = sim["data"].values / 100.0  # back to log-returns

    start_ts_ms = int(time.time() * 1000) - n_total * 5 * 60 * 1000
    price = start_price
    ticks: list[dict] = []

    for i, lr in enumerate(simulated_pct_returns):
        price = price * math.exp(float(lr) + mean_ret / len(log_returns))
        price = max(price, 1.0)

        # Use simulated conditional vol for spread sizing
        cond_vol_pct = float(sim["volatility"].iloc[i]) / 100.0
        annual_vol = cond_vol_pct * math.sqrt(252 * ticks_per_day)

        imbalance = float(rng.normal(0, 0.15))
        imbalance = float(np.clip(imbalance, -1.0, 1.0))

        ts_ms = start_ts_ms + i * 5 * 60 * 1000
        tick = _build_tick(
            mid_price=price,
            vol_annual=annual_vol,
            imbalance=imbalance,
            ts_ms=ts_ms,
            rng=rng,
        )
        ticks.append(tick)

    return ticks


# ── Convenience: list available block libraries ───────────────────────────────

def available_block_libraries() -> list[str]:
    """Return the names of built-in block libraries."""
    return list(_BLOCK_LIBRARIES.keys())
