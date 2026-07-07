# Avellaneda-Stoikov Market Maker Bot

A market-making trading bot for OKX using the **Avellaneda-Stoikov** pricing framework, built on **Lumibot** with **CCXT/CCXT Pro** connectivity.

## Features

- **Dual strategy modes**: Avellaneda-Stoikov optimal quoting and volatility-based fallback
- **Real-time WebSocket data**: CCXT Pro order book streaming with automatic REST fallback
- **Inventory management**: Reservation price skewing, size reduction, hard limits
- **Order book imbalance skewing**: Asymmetric quote adjustment based on book pressure
- **Risk management**: Drawdown kill-switch, liquidation distance monitoring, rate limiting
- **Fill detection**: REST polling with VWAP tracking and proper P&L attribution
- **Regime detection**: EMA crossover, RSI, and price slope for trend/range classification
- **Live dashboard**: Rich terminal UI with market data, P&L, orders, fills, and risk metrics
- **Crash recovery**: JSON state persistence for clean restarts

## Quick Start

### 1. Install dependencies

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your OKX API credentials:

```env
OKX_API_KEY=your_api_key_here
OKX_SECRET=your_secret_here
OKX_PASSPHRASE=your_passphrase_here
OKX_SANDBOX=true
```

> **⚠️ Start with sandbox mode (`OKX_SANDBOX=true`)** to test before using real funds.

### 3. Run the bot

```bash
python main.py
```

## Architecture

```
main.py                  Entry point — broker + strategy + trader
├── config.py            Configuration dataclass + env loading
├── strategy.py          Lumibot Strategy subclass — main orchestrator
│   ├── market_state.py  Order book data, EMA, volatility, imbalance
│   ├── quote_engine.py  A-S and volatility quote generation + skewing
│   ├── risk_manager.py  Kill-switch, drawdown, liquidation checks
│   ├── order_manager.py Order lifecycle, rate limiting, reconciliation
│   ├── fill_tracker.py  Fill detection, VWAP, P&L attribution
│   ├── regime_detector.py  Trend/range classification
│   ├── dashboard.py     Rich terminal live dashboard
│   ├── state_persistence.py  JSON state save/load
│   └── utils.py         Tick rounding, min notional, helpers
```

## Configuration

All parameters are in `config.py`. Key settings:

| Parameter | Default | Description |
|---|---|---|
| `strategy_mode` | `"avellaneda"` | `"avellaneda"` or `"volatility"` |
| `gamma` | `0.1` | Risk aversion (higher → wider spreads) |
| `k` | `1.5` | Order book liquidity parameter |
| `max_inventory` | `0.01` | Max position size (base currency) |
| `max_drawdown_pct` | `0.05` | Kill-switch threshold (5%) |
| `leverage` | `1.0` | Leverage multiplier |
| `sleeptime` | `0.5` | Seconds between iterations |

## Trading Loop

Each iteration follows this sequence:

1. **Market data** — read WebSocket order book (REST fallback if stale)
2. **Regime detection** — classify as trend or range
3. **Fill detection** — check for new fills, update P&L
4. **Risk checks** — drawdown, liquidation, inventory limits
5. **Quote generation** — A-S or volatility model with skewing
6. **Order reconciliation** — place/amend/cancel as needed
7. **State persistence** — periodic JSON save
8. **Dashboard update** — refresh terminal display

## Safety

- Kill-switch halts all quoting when drawdown exceeds the threshold
- All orders are cancelled on shutdown (graceful or crash)
- State is persisted for crash recovery
- Rate limiting prevents exchange ban
- Never submit unrounded prices or sub-minimum orders

## Disclaimer

This bot is provided for educational and research purposes. Trading cryptocurrency carries significant risk. Always start with sandbox/demo mode and never risk more than you can afford to lose.
