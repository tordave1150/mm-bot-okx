# AGENTS.md — Avellaneda-Stoikov Market Maker Bot (Lumibot × OKX × CCXT)

## 0. Purpose of This Document
This is a build spec for an AI coding agent. It merges two source specs (a generic
market-maker prompt and a Lumibot/OKX/CCXT blueprint) into one implementation-ready
document. Follow the phased build order in Section 12. Do not skip the risk
management or kill-switch components — they are load-bearing, not optional polish.

---

## 1. Executive Summary

Build a market-making trading bot that:
- Quotes both sides of the book (bid + ask) on a single crypto perpetual/spot market on OKX.
- Uses the **Avellaneda-Stoikov (A-S) framework** as its primary pricing model, with a
  simpler volatility-based spread as a fallback/alternate strategy.
- Runs on the **Lumibot** strategy framework, connecting to OKX via **Ccxt/CCXT Pro**.
- Manages inventory risk, drawdown, and kill-switch conditions in real time.
- Exposes a live terminal dashboard for monitoring.

Out of scope for v1: multi-market quoting, cross-exchange arbitrage, ML-based
regime prediction (rule-based regime detection only).

---

## 2. Mathematical Framework (Quote Generation)

### 2.1 Reservation Price
```
reservation_price = S - q * gamma * sigma^2 * tau
```
- `S`: current mid-price
- `q`: current inventory (position size relative to target, can be negative)
- `gamma`: risk aversion parameter (config)
- `sigma`: market volatility, computed via EWMA of returns
- `tau`: time horizon parameter (default 1 for continuous perpetuals)

### 2.2 Optimal Half-Spread
```
half_spread = (1/gamma) * ln(1 + gamma/k) + (gamma * sigma^2 * tau) / 2
```
- `k`: order book liquidity/depth parameter, calibrated from book depth or fill data

### 2.3 Fallback: Volatility-Based Spread
Used when regime detection flags high uncertainty, or as a simpler mode for small
accounts:
```
half_spread = volatility_multiplier * sigma + base_spread_ticks
```

### 2.4 Final Quote Construction
```
bid_price = round_to_tick(reservation_price - half_spread)
ask_price = round_to_tick(reservation_price + half_spread)
```
Then apply inventory skewing, imbalance skewing, and size adjustment (Section 4.4-4.6)
before submitting.

---

## 3. Architecture & Class Structure

Implement as decoupled components, not one monolithic script.

| Class | Responsibility |
|---|---|
| `Config` | Dataclass holding all tunable parameters (capital, leverage, max inventory, risk thresholds, exchange settings, API keys via env/secrets — never hardcoded) |
| `MarketState` | Current bids/asks, mid price, microprice, EWMA volatility, order book imbalance, regime flag, price history buffer |
| `Quotes` | Computed bid/ask price + size pair, tick-rounded, ready to submit |
| `EMA` | Small utility for exponential moving averages (used for volatility, regime detection) |
| `MarketMakerBot(Strategy)` | Main class, inherits Lumibot's `Strategy`. Orchestrates state updates, quote generation, risk checks, order routing, dashboard |

### 3.1 Lumibot → OKX Broker Setup
OKX requires an explicit `password` (API passphrase) in addition to key/secret.

```python
from lumibot.brokers import Ccxt
from lumibot.strategies import Strategy

class AvellanedaMarketMaker(Strategy):
    def initialize(self):
        self.sleeptime = 0.5  # high-frequency loop; tune per rate limits
        self.market_state = MarketState()
        self.inventory_limit = self.config.max_inventory

    def on_trading_iteration(self):
        # 1. Refresh MarketState (WS preferred, REST fallback)
        # 2. Recompute regime + volatility
        # 3. Generate Quotes (A-S or fallback)
        # 4. Apply risk checks / kill-switch
        # 5. Reconcile live orders vs target quotes (amend/cancel/place)
        # 6. Update dashboard + logs
        pass
```

Credentials (API key, secret, passphrase) must be loaded from environment variables
or a secrets manager — do not commit them to the repo or config dataclass defaults.

---

## 4. Quote Generation Logic

4.1 Support **both** strategies behind a config flag: `strategy_mode: "avellaneda" | "volatility"`.

4.2 **Inventory skewing** — shift reservation price / spread as `q` approaches
`max_inventory` so the bot naturally quotes tighter/wider to pull inventory back
toward target.

4.3 **Imbalance skewing** — adjust bid/ask asymmetrically based on order book
imbalance (e.g., more bid-side volume → skew quotes down slightly).

4.4 **Size adjustment** — linearly (or otherwise) reduce order size as `|q|`
approaches `max_inventory`; stop quoting the side that would breach the limit.

4.5 **Tick rounding** — all final prices must be rounded to the exchange's tick
size for the configured symbol; never submit an unrounded price.

4.6 **Minimum notional** — validate every order against exchange minimum notional
before submission; skip/resize orders that would fall below it.

---

## 5. Market Data & Connectivity

- Primary: real-time order book via CCXT Pro WebSocket subscription.
- Fallback: REST polling when WebSocket data is stale or disconnected (define a
  staleness threshold, e.g. no update in 3s → fall back).
- Compute from raw book: mid price, microprice (size-weighted), EWMA volatility,
  order book imbalance.
- Maintain a rolling price history buffer for moving averages and regime detection.
- Track and log latency between data timestamp and local processing time; flag
  degraded quality if latency exceeds a configurable threshold.

---

## 6. Risk Management (must-have, not optional)

- **Inventory limit**: hard cap on position size; bot must stop quoting the
  breaching side at the limit.
- **Drawdown kill switch**: track running P&L; if drawdown exceeds a configured
  threshold, cancel all open orders and halt new quoting until manual reset.
- **Real-time P&L**: separate realized vs unrealized P&L, updated every iteration.
- **Liquidation risk**: for leveraged positions, monitor distance to estimated
  liquidation price; tighten inventory limits or halt as it approaches.
- **Rate limiting**: cap order amend/cancel frequency to stay within exchange
  rate limits and avoid unnecessary churn/fees.
- **Margin awareness**: account for leverage and margin requirements when sizing
  orders, not just raw capital.

All risk checks run every iteration, before order placement — never after.

---

## 7. Order Management

- **Selective replacement**: only amend/replace an order if price or size has
  moved beyond a configurable threshold (avoid churning on tiny fluctuations).
- **Minimum order lifetime**: don't cancel/replace an order younger than a
  configured minimum age, to reduce fee drag and rate-limit pressure.
- **Queue positioning**: config option to either join the best bid/ask or
  outbid/outask by one tick.
- **Order state tracking**: maintain a local order table (id, price, size, side,
  status, timestamps) reconciled against exchange state each iteration.
- **Cancellation error handling**: treat "order not found" on cancel as a
  non-fatal, already-filled/cancelled condition — log and continue, don't crash.

---

## 8. Fill Detection & Trade Tracking

- Primary: WebSocket account/position update events for instant fill detection.
- Secondary: periodic REST check against recent trade history (last ~100 trades)
  as a reconciliation safety net.
- Compute VWAP separately for filled bids and filled asks.
- Attribute P&L correctly to realized (on fills) vs unrealized (mark-to-market on
  open inventory).

---

## 9. Regime Detection

```python
def detect_regime() -> str:
    # Inputs: moving averages, RSI, price slope over recent window
    # Returns "trend" or "range"
```
- In `"trend"` regime: widen spreads and/or reduce size to avoid adverse selection.
- In `"range"` regime: tighten spreads to capture more flow.
- Regime flag feeds into `strategy_mode` fallback logic (Section 4.1) and skewing
  aggressiveness.

---

## 10. Dashboard & Monitoring

Live-updating terminal (or simple web) dashboard showing:
- Current market data (mid, spread, volatility, regime)
- Current position, inventory %, unrealized/realized P&L
- Open orders and their age
- Last N fills with VWAP stats
- Risk metrics: drawdown, distance to liquidation, kill-switch state
- Scrolling debug log pane

---

## 11. Capital Scaling

- Position sizing scales with total account capital (config-driven multiplier,
  not hardcoded).
- Small-capital accounts default to wider spreads, lower inventory limits, and
  lower quoting frequency to control fee drag.
- Drawdown thresholds scale with capital size rather than using a fixed dollar
  figure.

---

## 12. Build Order (Implementation Phases)

1. **Config + skeleton** — `Config` dataclass, `Strategy` subclass boilerplate,
   env-based credential loading. No trading logic yet.
2. **Market data pipeline** — WebSocket connection, `MarketState` updates, REST
   fallback, latency logging.
3. **Quote generation** — A-S formula + volatility fallback, tick rounding,
   inventory/imbalance skewing, size adjustment.
4. **Risk management** — inventory limits, drawdown kill switch, P&L tracking,
   liquidation distance. Do this before live order placement.
5. **Order management** — place/amend/cancel logic, minimum lifetime, rate
   limiting, order state table.
6. **Fill detection** — WebSocket fill events + REST reconciliation, VWAP,
   realized/unrealized P&L split.
7. **Dashboard** — terminal display of all of the above.
8. **Regime detection** — moving averages/RSI/slope, regime-based parameter
   adjustment.
9. **Resilience** — reconnection logic, REST fallback paths, silent-continue on
   individual API failures, state persistence across disconnects.
10. **Testing** — paper trading or minimum-size live testing before scaling capital.

---

## 13. Error Handling & Resilience Requirements

- Auto-retry with backoff on WebSocket disconnects.
- Fall back to REST automatically when WebSocket is degraded/down; resume
  WebSocket when it recovers.
- Individual API call failures should be logged and skipped, not crash the loop.
- Bot must persist enough state (open orders, inventory, P&L baseline) to resume
  cleanly after a restart or brief disconnect, without double-counting fills.

---

## 14. Acceptance Criteria

- [ ] Bot connects to OKX via Lumibot `Ccxt` broker with key/secret/passphrase from env.
- [ ] Quotes both sides using A-S formula; volatility fallback mode switchable via config.
- [ ] Inventory and imbalance skewing visibly shift quotes in backtest/paper logs.
- [ ] Kill switch halts quoting and cancels orders when drawdown threshold is breached (verified with a forced-loss test).
- [ ] Fills are detected within one iteration cycle and reflected in P&L.
- [ ] Dashboard updates live with no stale data beyond the configured staleness threshold.
- [ ] Bot survives a simulated WebSocket disconnect/reconnect without duplicate orders or lost state.
- [ ] All orders pass tick-size and minimum-notional validation before submission.

---

## 15. Explicit Non-Goals for This Handoff

- No spoofing, layering, or any order behavior intended to mislead other market
  participants about true supply/demand — quotes must reflect genuine resting
  intent to trade at the posted price/size.
- No cross-exchange or wash-trading logic.
- No bypassing OKX rate limits or terms of service.
