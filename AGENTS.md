# AGENTS.md — OKX Market-Making Bot Audit and Refactor Agent

## 1. Mission

Act as a **Senior Python Quant Developer, Algorithmic Trading Engineer, Reliability Engineer, and Code Auditor**.

Work directly on this repository:

- Repository: `https://github.com/tordave1150/mm-bot-okx`
- System: OKX perpetual-swap market-making bot
- Strategy family: Avellaneda–Stoikov with volatility and market-regime adjustments
- Exchange connectivity: CCXT / CCXT Pro
- Existing framework dependency: Lumibot
- Backtesting: custom synthetic-data runner and matching engine
- Optimization: Optuna

Your job is to **audit, simplify, correct, test, and document the actual codebase**. Do not stop after giving advice. Implement the changes that can be implemented safely, run the required checks, and report exact results.

The priorities, in order, are:

1. Trading safety
2. Accounting correctness
3. Backtest realism
4. Live/backtest parity
5. Reliability and recoverability
6. Testability
7. Simplicity
8. Performance
9. Return optimization

A prettier backtest result is never more important than realistic risk and execution assumptions.

---

## 2. Non-Negotiable Safety Invariants

These rules override all other goals.

### 2.1 Environment and execution

- Keep `OKX_SANDBOX=true`.
- Never change sandbox/demo mode to live mode.
- Never deploy the bot.
- Never submit a real-money order.
- Never use, display, log, commit, or echo real API credentials.
- Do not run an order-sending integration test unless it is explicitly guarded as OKX Demo and uses credentials already supplied through the environment.
- Unit and normal integration tests must not require network access, money, or API keys.

### 2.2 Capital and lot size

The required baseline configuration is:

```text
initial_capital = 300.0 USDT
fixed_lot_size = 0.01
```

Rules:

- The fixed lot must remain exactly `0.01` throughout configuration, live logic, backtests, and every Optuna trial.
- Optuna must not suggest or mutate the fixed lot.
- Never silently reduce the lot.
- Never automatically increase leverage to make an order pass.
- If `0.01` is infeasible for the instrument, available equity, or selected leverage, reject the configuration with an actionable error.
- Report the estimated minimum required capital or minimum required leverage, but leave the decision to the user.
- Do not assume `0.01` means `0.01 BTC`. Verify OKX and CCXT contract semantics first.

### 2.3 Claims and reporting

- Never claim profitability based on synthetic backtesting.
- Never claim a command passed unless it was actually run successfully.
- Never hide exceptions or failed checks.
- Never fabricate command output, exchange metadata, fill data, test results, or performance metrics.
- If a task remains incomplete, state the exact blocker and affected files.
- Do not overwrite production configuration automatically with Optuna results.

---

## 3. Current Repository Baseline to Verify

Treat the following as observations from the repository snapshot inspected on 2026-07-18. Re-check them against the working tree before editing because the repository may have changed.

### 3.1 Current architecture observations

- `main.py` creates a Lumibot `Ccxt` broker and `Trader`.
- `main.py` monkey-patches private broker methods:
  - `_pull_positions`
  - `_get_balances_at_broker`
- `strategy.py` subclasses `lumibot.strategies.Strategy` while also coordinating custom CCXT/CCXT Pro market data, orders, fills, persistence, risk, and dashboard behavior.
- Shutdown handling uses `os._exit(...)` and swallows some cleanup exceptions.
- Logging uses an unbounded `logging.FileHandler("bot.log")`.
- `.lumibot/` appears to contain generated runtime state.

### 3.2 Current configuration observations

Verify that the current defaults still include values similar to:

```text
initial_capital = 1000.0
order_size_base = 0.01
max_inventory = 0.078
```

These defaults are not aligned with the required 300 USDT baseline and lot-based inventory limits.

### 3.3 Current backtest observations

- `backtest/matching_engine.py` describes an optimistic fill model.
- A resting order fills fully when market price touches/crosses the limit.
- Fill price is the submitted limit price.
- Fees are set to zero.
- No realistic queue position, partial fill, latency, slippage, or adverse-selection model is present in the baseline.
- The matching engine directly calls a private fill-tracker method.

### 3.4 Current Optuna observations

Verify that `backtest/optimize.py` still:

- suggests `order_size_base`,
- suggests arbitrary floating-point `max_inventory`,
- rewards fill rate directly,
- optimizes mainly on mean synthetic-seed score,
- uses only a small default seed count,
- imports `optuna` while `requirements.txt` may not declare it.

These behaviors must be corrected.

---

## 4. Required Working Method

Follow this sequence. Do not start with a broad rewrite.

### Phase 0 — Preserve evidence

Before modifying code:

1. Record the active branch and commit hash.
2. Run and save the current status:

```bash
git status --short
git rev-parse --show-toplevel
git rev-parse HEAD
python --version
```

3. Inventory files and dependencies.
4. Run available tests and smoke commands to establish the baseline.
5. Record real failures before attempting fixes.

Do not erase pre-existing user changes.

### Phase 1 — Repository-wide audit

Audit every Python file and classify it as one of:

- Production-critical
- Shared strategy/domain logic
- Exchange integration
- Backtest-only
- Optimization-only
- Dashboard-only
- Test-only
- Generated/runtime state
- Unused
- Duplicate
- Safe to remove
- Must be refactored before removal

Produce findings under:

- Critical
- High
- Medium
- Low

Look specifically for:

- logic errors,
- incorrect contract sizing,
- margin/accounting errors,
- live-trading risks,
- backtest biases,
- look-ahead bias,
- unused imports/functions/classes,
- unused dependencies,
- duplicate responsibilities,
- duplicate formulas,
- circular imports,
- import-time side effects,
- private API usage,
- monkey-patching,
- broad `except Exception`,
- exceptions swallowed with `pass`,
- global mutable state,
- blocking calls inside async loops,
- race conditions,
- thread-safety issues,
- resource leaks,
- stale WebSocket handling,
- non-atomic persistence,
- malformed README commands,
- platform-specific hardcoded paths,
- missing tests.

Use static analysis plus repository searches. Do not delete anything until import and runtime usage have been checked.

### Phase 2 — Decide whether Lumibot remains

Do not remove Lumibot merely because it is large. Decide from architecture, ownership, and runtime risk.

#### Remove Lumibot when all are true

- It is mainly a lifecycle wrapper.
- Raw/custom CCXT already owns market data, orders, fills, balances, or positions.
- Keeping both creates conflicting sources of truth.
- The required lifecycle can be implemented more clearly with a small async application service.

If removing Lumibot:

- Convert `AvellanedaMarketMaker` from a Lumibot `Strategy` subclass into a plain Python service/class.
- Replace Lumibot lifecycle methods with an explicit async application lifecycle.
- Use CCXT Pro for WebSocket data.
- Use CCXT REST only as a controlled fallback.
- Add reconnect with bounded exponential backoff and jitter.
- Add graceful handling for `SIGINT` and `SIGTERM`.
- Stop generating new quotes before shutdown.
- Cancel all open bot-owned orders.
- Persist final local metadata.
- Close WebSocket and REST exchange sessions.
- Join/stop dashboard threads or tasks.
- Remove private monkey-patches.
- Remove generated `.lumibot/` state if no longer required.
- Remove the dependency from production requirements.
- Update README and run commands.

#### Keep Lumibot only when justified

If retaining it:

- Document the exact responsibilities Lumibot owns.
- Use only public Lumibot APIs.
- Remove private monkey-patches.
- Do not allow Lumibot and the custom CCXT layer to both own balances, positions, orders, or fills.
- Add integration tests proving the chosen ownership model works in OKX Demo.

The final report must state the decision and technical evidence.

---

## 5. Target Responsibility Model

Maintain one clear owner per responsibility.

| Responsibility | Required owner |
|---|---|
| Exchange metadata | `OKXExchangeGateway` or equivalent |
| WebSocket market data | Exchange gateway / market-data adapter |
| REST fallback | Exchange gateway |
| Order submission/amend/cancel | Execution/order manager through exchange gateway |
| Open orders and exchange positions | Exchange as source of truth |
| Strategy decisions | Pure `StrategyCore` |
| Quote calculations | `QuoteEngine` called by `StrategyCore` |
| Risk gating | `RiskManager` |
| Fill normalization | Public fill/accounting service |
| Position, average entry, fees, P&L | Accounting/portfolio component |
| Local continuity metadata | State persistence component |
| Backtest fills | Backtest execution simulator only |
| Dashboard | Read-mostly presentation/control adapter |
| Optimization | Backtest-only orchestration |

Recommended pure interface:

```python
class StrategyCore:
    def on_market_update(self, state: StrategyState) -> DesiredQuotes:
        ...
```

`StrategyCore` must not:

- call an exchange,
- read environment variables,
- start threads,
- sleep,
- write files,
- mutate dashboard state,
- depend on Lumibot,
- depend on CCXT response dictionaries directly.

Live trading and backtesting must call the same strategy decision logic.

Avoid an unnecessary full repository reorganization. Prefer small modules with explicit ownership over a cosmetic folder rewrite.

---

## 6. Contract, Quantity, and Margin Semantics

Before submitting any order, load and validate current OKX/CCXT market metadata for `BTC/USDT:USDT` or the configured symbol.

Inspect at minimum:

- `contractSize`
- linear vs inverse contract
- CCXT amount unit
- minimum amount
- minimum notional
- lot size / amount step
- price tick size
- amount precision
- price precision
- leverage bounds
- margin mode
- position mode
- isolated vs cross behavior
- reduce-only behavior

Create a typed market specification, for example:

```python
@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    contract_size: Decimal
    amount_step: Decimal
    min_amount: Decimal
    min_notional: Decimal | None
    price_tick: Decimal
    amount_precision: int | None
    price_precision: int | None
    linear: bool
    inverse: bool
```

Create explicit conversion utilities between:

- requested fixed lot,
- base-asset quantity,
- contract quantity,
- quote notional,
- required initial margin,
- estimated maker/taker fee.

Use `Decimal` or carefully controlled integer step arithmetic for exchange-facing rounding. Do not use unconstrained binary floating-point rounding for prices and contract quantities.

Before every order, validate:

```text
required_margin + estimated_fees + safety_buffer <= available_equity
```

Also validate:

- price is positive,
- amount is positive,
- price conforms to tick size,
- amount conforms to amount step,
- minimum amount is met,
- minimum notional is met,
- bid is below ask,
- quote does not unintentionally cross the book,
- potential post-fill inventory stays inside the limit,
- reduce-only intent is correct,
- margin mode and position mode match configuration.

If a configuration is infeasible, raise a clear domain-specific error. Do not silently modify sizing or leverage.

---

## 7. Configuration Rules for 300 USDT

Establish one source of truth for configuration.

Required baseline:

```python
initial_capital = 300.0
fixed_lot_size = 0.01
max_inventory_lots = 1  # conservative default; 2 only after validation
max_inventory = fixed_lot_size * max_inventory_lots
sandbox = True
```

Use names that distinguish:

- base quantity,
- contract quantity,
- number of lots,
- quote notional.

Do not leave ambiguous names such as `order_size_base` unless its unit is fully defined and tested.

Configuration validation must enforce:

- `initial_capital > 0`
- `fixed_lot_size == 0.01`
- `max_inventory_lots` is an integer
- `1 <= max_inventory_lots <= user-defined hard limit`
- `max_inventory == fixed_lot_size * max_inventory_lots`
- target inventory is within limits
- drawdown threshold is in a sensible range
- leverage is at least 1
- leverage does not exceed a user-defined hard limit
- sandbox is true
- bid is below ask
- sizing is feasible for market metadata and equity

A frozen dataclass is acceptable only if overrides consistently use `dataclasses.replace` and do not create avoidable complexity. Otherwise use an immutable validated model or a simpler validated dataclass.

Credentials must remain environment-only.

---

## 8. Live Trading Lifecycle and Reliability

The live application must have an explicit lifecycle:

1. Load and validate configuration.
2. Create exchange sessions.
3. Load market metadata.
4. Verify demo/sandbox mode.
5. Verify margin/position settings.
6. Fetch exchange positions, open orders, and recent fills.
7. Reconcile local metadata against exchange truth.
8. Start WebSocket feed.
9. Start dashboard only after state is initialized.
10. Enter the trading loop.
11. On stop: disable quoting, cancel bot-owned orders, reconcile, persist, close resources.

### WebSocket requirements

- Detect stale data.
- Reconnect with exponential backoff and jitter.
- Bound the maximum retry delay.
- Reset backoff after a stable connection.
- Use REST fallback only when clearly marked and rate-limited.
- Do not place quotes from stale or structurally invalid market data.
- Record reconnect and fallback events.

### Async requirements

- Do not call blocking REST, file, browser, or thread operations directly in an async hot path.
- Use an executor or async-compatible API when required.
- Protect shared state with a clear concurrency model.
- Avoid mixing an unmanaged event loop with ad-hoc background threads.

### Shutdown requirements

- Prefer normal cancellation and cleanup.
- Do not use `os._exit` except as a documented final-resort watchdog after graceful shutdown has failed.
- Do not swallow cleanup exceptions. Log them and continue remaining cleanup steps.
- Ensure exchange sessions are closed even if one cleanup step fails.

---

## 9. State Persistence and Recovery

Exchange state is authoritative for:

- positions,
- open orders,
- fills/trades,
- balances/equity.

Local state is only for:

- metadata,
- continuity,
- dashboard history,
- strategy warm-up data when safe,
- last processed fill identifiers,
- schema-versioned recovery hints.

On restart:

1. Fetch positions from OKX.
2. Fetch open orders.
3. Fetch recent fills/trades.
4. Load local state.
5. Validate schema version.
6. Reconcile differences.
7. Log discrepancies.
8. Prefer exchange truth.
9. Prevent duplicate fill processing.

Write state atomically:

1. Write to a temporary file in the same directory.
2. Flush.
3. `fsync` when appropriate.
4. Atomically replace the target.
5. Handle corrupted JSON safely.
6. Keep a schema version.

Do not write state every tick unless required. Persist on meaningful changes and/or a debounced interval.

---

## 10. Logging and Audit Trail

Do not remove logging. Trading software requires an audit trail.

Required events include:

- startup and configuration validation,
- exchange metadata validation,
- order submitted,
- order amended,
- order cancelled,
- order rejected,
- fill received,
- duplicate fill ignored,
- position changed,
- realized P&L,
- unrealized P&L,
- fees and rebates,
- insufficient margin,
- inventory-limit rejection,
- kill-switch activation,
- WebSocket reconnect,
- stale data,
- REST fallback,
- state reconciliation,
- shutdown and cancel-all result,
- unexpected exception.

Replace unbounded file logging with:

- console: `INFO`
- rotating application log: `INFO`
- rotating error log: `ERROR`
- optional structured trade audit: JSON Lines
- backtest/Optuna default: `WARNING`
- debug enabled through environment/config

Use `RotatingFileHandler` or `TimedRotatingFileHandler` with:

- UTF-8,
- configurable maximum size or daily rotation,
- configurable backup count, normally 7–30,
- safe directory creation,
- no API secrets, passphrases, tokens, or credential-like values.

Add redaction for known credential keys and URL query secrets.

Avoid per-tick log spam.

Suggested structured fill event:

```json
{
  "timestamp": "...",
  "event": "fill",
  "symbol": "BTC/USDT:USDT",
  "side": "buy",
  "price": 0,
  "contracts": 0,
  "base_quantity": 0,
  "notional": 0,
  "fee": 0,
  "position_after": 0,
  "equity_after": 0
}
```

Recommended config fields:

```text
log_level
log_dir
log_to_file
log_rotation_mb
log_backup_count
trade_audit_enabled
```

---

## 11. Backtest Engine Requirements

The baseline optimistic engine is diagnostic only. It is not acceptable as the default optimization environment.

### 11.1 Fill model modes

Implement explicit modes:

- `optimistic`
- `probabilistic`
- `conservative`

Optimization must default to `probabilistic` or `conservative`, never `optimistic`.

### 11.2 Fees

Support configurable:

```text
maker_fee_rate
taker_fee_rate
```

- Passive fills use maker fees.
- Marketable/aggressive actions use taker fees.
- Negative maker fees may represent rebates when configured.
- Fees must affect realized P&L, cash, equity, final balance, and optimization score.

### 11.3 Queue, partial fills, and latency

Support at minimum:

- queue-ahead estimate,
- fill probability,
- partial fills,
- maximum fill quantity per tick,
- order age,
- placement latency,
- amend latency,
- cancellation latency,
- minimum quote life,
- stale-quote exposure.

Fill probability may use only contemporaneously available data such as:

- price touch/cross,
- distance from mid,
- spread,
- visible/synthetic depth,
- traded volume,
- order-book imbalance,
- queue ahead,
- order age,
- historical volatility available at that tick.

Never use future observations to decide a current fill.

### 11.4 Slippage and adverse selection

Apply slippage to:

- aggressive fills,
- forced exits,
- liquidation approximations,
- marketable limit orders where appropriate.

Measure adverse selection after configurable horizons such as 1, 5, and 10 ticks.

### 11.5 Cash and margin accounting

Track at minimum:

- cash balance,
- position,
- average entry,
- realized P&L,
- unrealized P&L,
- total fees,
- rebates,
- equity,
- used margin,
- free margin,
- notional exposure,
- maintenance margin estimate.

Reject orders with insufficient margin.

Liquidation approximation must account for:

- direction,
- leverage,
- maintenance margin,
- fees,
- unrealized P&L.

### 11.6 End-of-test inventory

Support:

- `mark_to_market`
- `force_close`
- `both`

Report mark-to-market equity and forced-close equity after fee/slippage. Do not make an unclosed inventory position look like safely realizable profit.

### 11.7 Metrics

Add or verify:

- gross P&L,
- net P&L after fees,
- total fees,
- maker rebates,
- total return,
- maximum drawdown,
- Sortino/downside deviation,
- spread captured,
- adverse selection at multiple horizons,
- average holding time,
- inventory turnover,
- average order age,
- fill-to-cancel ratio,
- quote-to-fill ratio,
- margin violations,
- kill-switch count,
- probability-of-ruin proxy,
- final inventory,
- forced-close cost.

---

## 12. Production/Backtest Parity and Look-Ahead Controls

Live and backtest code must share:

- `StrategyCore`,
- quote calculations,
- risk validation where applicable,
- regime logic,
- volatility calculations,
- configuration validation,
- accounting formulas where practical.

Do not duplicate Avellaneda–Stoikov formulas, inventory skew, imbalance skew, volatility, or regime multipliers between live and backtest modules.

Audit look-ahead bias in:

- volatility estimation,
- EMA,
- RSI,
- trend slope,
- regime classification,
- synthetic data generation,
- fill simulation,
- performance metrics,
- end-of-test handling.

At tick `t`, every decision must use only information available at or before tick `t`.

Tests must demonstrate the absence of future leakage for representative features and fill decisions.

---

## 13. Optuna Rules for 300 USDT and Fixed Lot 0.01

### 13.1 Fixed values

The optimization base configuration must set:

```python
initial_capital = 300.0
fixed_lot_size = 0.01
```

The objective must contain an invariant check equivalent to:

```python
assert cfg.fixed_lot_size == 0.01
```

Do not call:

```python
trial.suggest_float("order_size_base", ...)
trial.suggest_float("fixed_lot_size", ...)
```

### 13.2 Inventory search space

Optimize inventory as discrete lots:

```python
max_inventory_lots = trial.suggest_int("max_inventory_lots", 1, 3)
max_inventory = max_inventory_lots * 0.01
```

Prune or reject trials whose lot count is not feasible under market metadata, equity, leverage, fees, and safety buffer.

### 13.3 Search-space discipline

Optimize a limited, justified subset in each study, such as:

- `gamma`
- `k`
- `tau`
- `max_inventory_lots`
- `max_drawdown_pct`
- trend spread multiplier
- trend size multiplier
- range spread multiplier
- imbalance skew factor
- inventory skew factor
- EMA span
- quote refresh threshold
- minimum quote life
- cancel/replace threshold

Do not optimize too many correlated parameters in one study.

### 13.4 Robust objective

Do not maximize mean return or fill rate alone.

Evaluate:

- median net return,
- worst-seed return,
- worst maximum drawdown,
- downside deviation,
- fees,
- adverse selection,
- inventory risk,
- kill-switch count,
- minimum fill count,
- two-sided quoting behavior,
- margin violations,
- probability-of-ruin proxy.

A suitable form is:

```python
score = (
    median_net_return
    - 2.5 * worst_max_drawdown
    - 1.5 * max(0.0, -worst_seed_return)
    + 0.10 * clipped_median_sortino
    - fee_penalty
    - adverse_selection_penalty
    - inventory_penalty
    - kill_switch_penalty
)
```

Direct fill-rate rewards require penalties for toxic fills, fees, and one-sided behavior.

Hard constraints:

- prune on any unhandled margin violation,
- reject final equity `<= 0`,
- reject excessive kill-switch count,
- reject insufficient fills,
- reject insufficient two-sided quoting,
- raise if fixed lot differs from `0.01`.

### 13.5 Validation design

Separate:

1. Train scenarios
2. Validation scenarios
3. Holdout stress scenarios

Seeds must not overlap across groups.

Cover at minimum:

- low-volatility range,
- normal range,
- uptrend,
- downtrend,
- high volatility,
- flash crash,
- rapid rebound,
- spread widening,
- low liquidity,
- high adverse selection,
- data gaps,
- latency spike.

Recommended full-study defaults:

```text
n_trials >= 300
train_seeds >= 10
validation_seeds >= 10
holdout_seeds >= 10
```

Allow smaller CLI values for smoke tests.

Use reproducible seeded samplers and SQLite persistence.

Recommended command:

```bash
python -m backtest.optimize \
  --n-trials 300 \
  --n-seeds 10 \
  --n-days 30 \
  --db optuna_300usdt.db \
  --study-name mm-bot-300usdt-fixed-lot
```

Write artifacts without modifying production config:

```text
artifacts/optuna/best_params_300usdt.json
artifacts/optuna/trials.csv
artifacts/optuna/validation_report.json
```

A separate explicit command must validate/apply parameters. Never auto-apply them.

---

## 14. Historical Data Support

Synthetic data is acceptable for controlled stress testing, not profitability validation.

Introduce a data-source interface similar to:

```python
class MarketDataSource(Protocol):
    def __iter__(self) -> Iterator[MarketTick]:
        ...
```

Support adapters for:

- `SyntheticDataSource`
- `HistoricalTradeDataSource`
- `HistoricalOrderBookDataSource`
- optional `HistoricalOHLCVDataSource` for baseline-only tests

Do not combine download logic with the backtest core.

Validate cached data:

- UTC timestamps,
- sorted timestamps,
- no invalid duplicates,
- no negative price or size,
- valid bid/ask relationship,
- gap detection,
- no future leakage.

Document that candle-only data cannot accurately reproduce market-making queue and fill behavior.

---

## 15. Dependency and Packaging Policy

Audit every dependency before changing it.

Verify actual usage of at least:

- `lumibot`
- `ccxt`
- `python-dotenv`
- `fastapi`
- `uvicorn`
- `numpy`
- `arch`
- `scipy`
- `PyYAML`
- `pytest`
- `optuna`

Separate dependency groups using either:

- `requirements.txt`
- `requirements-dev.txt`
- `requirements-backtest.txt`

or preferably a clear `pyproject.toml` with optional groups:

```toml
[project.optional-dependencies]
dev = [...]
backtest = [...]
```

Production dependencies must not include test/optimization packages unless runtime genuinely requires them.

Do not remove a dependency until repository imports and runtime paths prove it is unused.

Target Python 3.11 and 3.12 compatibility.

Add or update:

- `pyproject.toml`
- `.pre-commit-config.yaml`
- `.github/workflows/tests.yml`

CI must run at minimum:

```bash
ruff check .
pytest -q
```

and a small deterministic backtest smoke test.

---

## 16. File-Specific Instructions

### `main.py`

- Remove private monkey-patches.
- Replace unbounded logging.
- Replace forced exits with graceful lifecycle handling.
- Keep the entry point thin.
- Do not contain strategy formulas.
- Enforce sandbox guard before exchange initialization.

### `strategy.py`

- Separate orchestration from pure decision logic.
- Remove framework inheritance if Lumibot is removed.
- Avoid direct dashboard, persistence, exchange, and formula ownership in one class.
- Preserve behavior only when it is correct and safe.

### `config.py`

- Change baseline to 300 USDT and fixed lot 0.01.
- Replace arbitrary max inventory with discrete lot count.
- Add validation and units.
- Add logging, fee, latency, and fill-model configuration.
- Keep credentials environment-only.

### `market_state.py`

- Normalize exchange data into typed internal state.
- Track timestamp, receive time, age, and data source.
- Reject crossed/empty/invalid books.
- Avoid exchange-dictionary leakage into strategy logic.

### `quote_engine.py`

- Remain pure and deterministic.
- Use explicit units.
- Ensure bid `<` ask after rounding.
- Ensure rounding cannot create crossed quotes.
- Share this logic with backtest.

### `regime_detector.py`

- Use only past/current observations.
- Handle warm-up periods explicitly.
- Test EMA, RSI, slope, and threshold boundaries.

### `risk_manager.py`

- Make risk checks explicit and side-effect-light.
- Validate equity, drawdown, margin, inventory, stale data, and liquidation distance.
- Return structured decisions/reasons.
- Ensure kill-switch behavior is idempotent.

### `order_manager.py`

- Own order reconciliation only.
- Enforce minimum quote life and rate limits.
- Track bot-owned order IDs.
- Handle partial fills and cancel/replace races.
- Use public exchange-gateway methods.

### `fill_tracker.py`

- Expose a public fill-ingestion method.
- Make duplicate-fill handling idempotent.
- Correct average entry through scale-in, scale-out, close, and position flip.
- Include fees in realized P&L and equity.
- Avoid private method calls from backtest.

### `state_persistence.py`

- Use atomic writes and schema versions.
- Recover safely from corrupt state.
- Treat exchange truth as authoritative.
- Debounce writes.

### `dashboard_state.py`, `web_server.py`, and `static/`

- Keep dashboard presentation separate from trading decisions.
- Dashboard failures must not crash or block the trading loop.
- Stop/resume controls must use thread-safe/application-safe commands.
- Never expose credentials or secret-bearing exception strings.

### `backtest/matching_engine.py`

- Preserve `optimistic` only as an explicitly selected diagnostic mode.
- Add fee, queue, latency, partial-fill, probabilistic/conservative behavior.
- Do not directly call private production methods.
- Make deterministic behavior seed-controlled.

### `backtest/runner.py`

- Use the same `StrategyCore` as live trading.
- Implement cash, margin, fee, and end-position accounting.
- Do not manually duplicate production formulas.
- Allow historical data sources.

### `backtest/metrics.py`

- Calculate gross/net results correctly.
- Make fee/rebate and forced-close effects visible.
- Avoid invalid annualization for very short or irregular test periods.
- Guard zero-variance and empty-series cases.

### `backtest/optimize.py`

- Remove lot size from search space.
- Use discrete inventory lots.
- Add fixed-lot assertions and margin feasibility constraints.
- Add reproducible sampler, persistence, train/validation/holdout reporting, and artifact output.
- Do not write best parameters into production config.

### `backtest/synthetic_data.py`

- Keep seeded reproducibility.
- Validate generated books.
- Document model limitations.
- Ensure generated future values cannot leak into current features or fills.

### `backtest/agent_loop.py`

- Do not create a loop that optimizes until a favorable result appears.
- Use fixed acceptance criteria and a bounded run count.
- Record failed criteria and seeds.
- Avoid selection bias from repeated retries.

### `.gitignore`

Include runtime and generated artifacts such as:

```gitignore
*.log
logs/
bot_state.json
optuna*.db
artifacts/
__pycache__/
.pytest_cache/
.lumibot/
```

Do not ignore required sample configuration or test fixtures.

### `README.md`

- Correct malformed shell commands.
- Provide Windows and POSIX commands that actually work.
- Explain demo-only startup.
- Explain units and fixed lot semantics.
- Explain backtest limitations.
- Explain Optuna validation and non-auto-application.
- Remove unsupported claims.

---

## 17. Required Tests

Add or update tests for the following.

### Unit tests

- Avellaneda reservation price
- Optimal spread
- Inventory skew
- Order-book imbalance skew
- Regime multipliers
- Fixed lot remains 0.01
- Maximum inventory is an exact multiple of 0.01
- Contract-to-base conversion
- Base-to-contract conversion
- Notional calculation
- Required-margin calculation
- Estimated fee calculation
- Insufficient-margin rejection
- Tick rounding
- Lot/amount-step rounding
- Bid/ask non-crossing after rounding
- Maker fee
- Taker fee
- Maker rebate
- Partial fill
- Queue behavior
- Placement latency
- Amend latency
- Cancellation latency
- Realized P&L
- Unrealized P&L
- Average entry after scale-in
- Average entry after scale-out
- Position close
- Position flip
- Drawdown
- Kill-switch idempotency
- State recovery
- Corrupt state handling
- Duplicate fill handling
- Log redaction

### Backtest tests

- No future leakage
- Deterministic output with the same seed
- Different seed creates a different path
- Fees reduce equity correctly
- Conservative results are no better than optimistic results under controlled identical conditions
- Fixed lot 0.01 cannot be changed by Optuna
- Margin violations are rejected/pruned
- End position is force-closed correctly
- Mark-to-market and force-close results are both reported
- Optuna study resumes from SQLite
- Train, validation, and holdout seeds do not overlap
- Final results include fees and inventory
- Partial fills update remaining quantity correctly
- Cancellation latency can result in a late fill

### Integration tests with mocks/fakes

- WebSocket reconnect
- Stale WebSocket detection
- REST fallback
- Duplicate fills
- Partial fills
- Rejected orders
- Rate limits
- Timeouts
- Cancel-all
- Graceful shutdown
- Exchange/local reconciliation
- Exchange session closure

Tests must not require real credentials or real money.

---

## 18. Code Quality Rules

- Python 3.11/3.12 compatible.
- Add type hints to core/domain code.
- Use `ruff` for linting and formatting, or `ruff` plus `black` consistently.
- Use `mypy` for suitable core modules if introduced.
- Prefer domain-specific exceptions.
- Avoid broad exception catches. When a boundary requires one, log context and preserve the cause.
- Never use `except Exception: pass` for meaningful operations.
- Do not monkey-patch private attributes.
- Avoid `os._exit` except documented last resort.
- Avoid hardcoded Windows paths.
- Keep CLI commands cross-platform.
- Keep business logic separate from I/O.
- Keep one source of truth for config and accounting.
- Prefer small, reviewable changes.
- Do not overengineer abstractions that have only one trivial implementation.
- Do not change formulas merely to improve backtest performance.

---

## 19. Required Validation Commands

Run at minimum:

```bash
ruff check .
pytest -q
python -m backtest.runner --seed 42 --vol 0.25 --n-days 3
python -m backtest.optimize \
  --n-trials 10 \
  --n-seeds 3 \
  --n-days 3 \
  --db optuna_smoke.db \
  --study-name smoke-fixed-lot
```

Also verify study resume by running the same Optuna command again or by a dedicated resume test.

Where formatting is configured, also run the formatter check, for example:

```bash
ruff format --check .
```

Where type checking is configured:

```bash
mypy <configured-core-paths>
```

Do not run the live bot as a substitute for tests.

If a command fails:

- include the exact command,
- include the relevant real error output,
- explain the root cause,
- identify what remains incomplete.

---

## 20. Acceptance Checks

Before declaring completion, confirm with evidence that:

- Tests pass, or remaining failures are explicitly reported.
- Fixed lot is exactly `0.01` in every Optuna trial.
- Initial capital is `300.0` USDT in the 300-USDT study.
- Contract/lot conversion is validated from market metadata.
- Fees affect backtest equity and objective.
- Margin violations are not ignored.
- Optimistic fill mode is not the optimization default.
- Optuna persistence and resume work.
- Validation seeds differ from train seeds.
- Holdout results are reported separately.
- Logs redact credentials.
- Log files rotate.
- Shutdown attempts cancel bot-owned open orders.
- Exchange sessions close normally.
- Local state does not override exchange positions/orders.
- README commands are syntactically valid.
- No real-money setting or credential was introduced.

---

## 21. Final Deliverables

Return results in this exact order:

1. Executive audit summary
2. Issues by Critical / High / Medium / Low
3. Lumibot decision and evidence
4. Architecture before and after
5. Files changed
6. Files removed and proof they were safe to remove
7. Dependencies added, removed, or moved between groups
8. Contract-size and fixed-lot implementation
9. 300-USDT margin feasibility method
10. Backtest bias corrections
11. Optuna search space and objective
12. Logging and redaction policy
13. State recovery behavior
14. Test and command results
15. Installation and run commands
16. Remaining limitations
17. Per-file diff summary

For every validation command, report:

```text
Command:
Exit code:
Result:
Important output:
```

Do not state “all tests pass” without real output.

---

## 22. Definition of Done

The task is complete only when:

- the codebase has been audited,
- critical and high-risk defects have been addressed or clearly blocked,
- Lumibot ownership has been resolved,
- fixed lot 0.01 is enforced,
- 300-USDT configuration is validated,
- contract semantics are explicit,
- margin and fee accounting are implemented,
- backtest realism is materially improved,
- live and backtest use shared strategy logic,
- Optuna cannot alter lot size,
- optimization uses robust validation,
- logging rotates and redacts secrets,
- recovery reconciles against exchange truth,
- required tests and smoke checks have been run,
- README and dependency definitions match the actual code,
- no real-money action has been taken.

Safety and realism are more important than reported return.
