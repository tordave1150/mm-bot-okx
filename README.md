# bot-trade — OKX Demo Market-Maker Research

## Current status

This repository is in **R0 offline repair** for an OKX Demo market-maker. It is not approved for R1 preflight, an economic campaign, Live access, or production.

```text
production_authorized: false
live_mode_available: false
live_endpoint_attempts: 0
live_orders: 0
optuna_executed: false
validation_opened: false
holdout_opened: false
git_write_operation: false
```

Read [AGENTS.md](AGENTS.md) before any execution. Its active phase and risk rules override this README.

## Canonical implementation

The canonical implementation is the controlled successor stack:

```text
market_maker/                 frozen market-maker model and accounting
okx_demo_*.py                 demo campaign, controller, protocol, and repairs
okx_fill_restart_*.py         recovery, gateway, formal safety, and evidence tools
tests/test_okx_*.py           successor regression tests
backtest/mm_*.py              permitted offline market-maker tests
```

The design target is a passive BTC/USDT:USDT maker strategy with a frozen risk boundary. The current repair focuses on increasing normal maker fills and FIFO maker work-off while reducing terminal special flatten dependence, without widening economic risk.

For the component map and legacy inventory, see [ARCHITECTURE.md](ARCHITECTURE.md) and [REPO_CLASSIFICATION_MANIFEST.md](REPO_CLASSIFICATION_MANIFEST.md).

## R0-only workflow

Allowed work is local/offline implementation, deterministic fixtures, audits, and permitted tests with blank credentials and socket denial. A fresh R0 run must produce non-overwriting evidence and write its terminal marker last.

Before running any permitted tests:

1. Create an environment from `requirements-offline-test.txt`.
2. Keep credentials blank and deny sockets.
3. Run only the successor-applicable targeted, root non-Optuna, and permitted backtest non-Optuna scopes defined by the active protocol.
4. Confirm endpoint/mutation, secret, test, and socket audits are clean.

Do not run `main.py`, connect to OKX, run Optuna, open validation/holdout workflows, or use a prior package/run/session identity.

## Dependency sets

| File | Purpose | Current phase |
|---|---|---|
| `requirements-runtime.txt` | Original async runtime dependencies | Legacy; not an authorized execution path |
| `requirements-offline-test.txt` | R0 local test/backtest dependencies | Permitted only within active R0 rules |
| `requirements-research-disabled.txt` | Optuna research dependencies | Installed only with separate authorization; do not execute/import now |

`requirements.txt` intentionally points to the offline-test set rather than installing Optuna by default.

## Legacy components

`main.py` and `trading_bot.py` form an older CCXT async runtime. `strategy.py` is an older Lumibot implementation. They are retained for reference while the successor stack is the canonical research and safety path; neither is a current authorized entry point.

`main.py` now fails closed before loading configuration or dotenv. The retained
`bot_state.json` belongs to this legacy runtime and contains stale non-zero
inventory; do not reset, consume, or treat it as current account truth without
a separately authorized reconciliation.

## Evidence retention

Do not modify or remove the immutable predecessor package:

```text
artifacts/okx_demo_multi_session_economic_soak/packages/economic-package-20260818T125221Z/
```

Historical artifacts are not runtime dependencies, but must be retained or externally archived with a manifest and verified hashes before cleanup. Do not bulk-delete `artifacts/`.

## Next boundary

After fresh R0 evidence passes every mandatory gate, obtain separate exact user authorization before preparing fresh R1 identities. R1 preparation, R1 preflight, and R2 economic campaign are separate phases; none authorizes production.
