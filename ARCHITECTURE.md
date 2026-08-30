# Architecture

## Canonical successor stack

```text
market_maker/
  ├─ model, inventory, quote, margin, accounting
  └─ frozen strategy profile used by offline research and OKX Demo controls

okx_demo_*.py
  ├─ profile, adapter, state, runtime, protocol
  ├─ campaign/session controller and supervisor
  └─ offline repairs, diagnostics, and evidence writers

okx_fill_restart_*.py
  ├─ formal safety specification and validation model
  ├─ gateway/executor and recovery handling
  └─ evidence hashing and audit utilities

tests/ and backtest/tests/
  └─ deterministic regression and permitted offline coverage
```

This is the only stack to use for R0 work. It is demo-oriented, fail-closed, and governed by `AGENTS.md`.

## Shared modules

`market_spec.py`, `fill_tracker.py`, `fill_classification.py`, and selected utility/accounting modules are used across research and runtime code. Do not relocate them based solely on the original/legacy classification.

## Legacy async stack

```text
main.py -> trading_bot.py -> config.py
strategy.py -> Lumibot lifecycle
```

`main.py` uses `TradingBot`; it does not use `strategy.py`. The Lumibot strategy and `.lumibot/` state are retained only for historical reference. Neither is a current authorized entry point.

## Operational boundaries

| Phase | State |
|---|---|
| R0 offline repair | Active and offline-only |
| R1 read-only demo preflight | Requires fresh identities and exact authorization |
| R2 economic campaign | Requires passing R1 and separate authorization |
| Production | Unauthorized |

The immutable predecessor evidence package remains outside all cleanup and refactoring work.
