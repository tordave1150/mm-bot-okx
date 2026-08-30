# AGENTS.md — OKX Demo Execution Safety Before Production

## 1. Activation and authority

This is a candidate successor protocol. It is not active while the root
`AGENTS.md` remains the MM v1.6 protocol. Activate it only by an explicit user
instruction that replaces the root protocol after the frozen v1.6 hashes have
been verified read-only.

When active, this protocol governs the entire project folder. Work only inside
the project folder. It authorizes implementation and testing of an opt-in OKX
demo adapter. It does not authorize live orders, live credentials, production
capital, production-default changes, Git operations, Optuna, validation,
holdout selection, or mutation/rerun of v1.3-v1.6 evidence.

## 2. Immutable research input

- MM v1.6 status must remain `MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT`.
- Supported profile must remain `FEE_AWARE_SPREAD_6` / `mm-v1-6-profile-02`.
- Frozen specification SHA-256 must remain
  `e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f`.
- Optuna must remain unexecuted in this process.

Verify all covered hashes read-only before implementation. Do not edit a v1.6
covered source file or any prior artifact directory.

## 3. Objective

Close every blocker emitted by `okx_production_readiness.py` in the offline and
OKX demo paths, while keeping live trading structurally impossible. Produce
evidence that the supported v1.6 profile is mapped exactly into the runtime
quote engine and that order, fill, position, state, and emergency behavior are
safe under failures and restarts.

## 4. Mandatory separation

Use three explicit execution modes:

1. `OFFLINE_FIXTURE`: no network and no credentials.
2. `OKX_DEMO`: OKX demo endpoint only, explicitly armed per process.
3. `LIVE`: unavailable and rejected by configuration in this protocol.

Never infer a mode from the presence of credentials. Never fall back from demo
to live. Log endpoint identity without logging secrets. Credentials must be
injected at runtime, checked for key/secret/passphrase completeness, never
written to artifacts, and limited to the minimum demo-account permissions.

## 5. Runtime profile promotion contract

Create a non-overwriting, hashed promotion manifest binding all of these:

- `FEE_AWARE_SPREAD_6` and `mm-v1-6-profile-02`;
- the v1.6 specification hash;
- every quote/economic/defensive parameter and unit;
- symbol, contract type, capital copy, lot, inventory, leverage, margin cap,
  fee assumptions, drawdown guard, re-entry, and special-exit semantics;
- exact runtime source hashes and runtime configuration hash.

Add equivalence fixtures proving that the runtime quote controls match the
frozen profile. A profile name alone is insufficient. Any missing, extra, or
unit-mismatched field must fail closed.

## 6. Order lifecycle safety

Before any demo order is allowed:

- assign each intent a deterministic, session-scoped client order identity;
- resolve an ambiguous create result by client identity before any retry;
- never replace until cancellation is confirmed by an authoritative snapshot;
- reject duplicate same-side orders and foreign/unowned orders;
- reconcile open orders, recent trades, position, balance, account mode,
  margin mode, leverage, contract specification, and clock health;
- halt on any incomplete or unknown exchange snapshot;
- cancel from the authoritative owned exchange order set, not only local state;
- require post-only acknowledgement for normal maker orders;
- keep emergency/special exits reduce-only taker orders excluded from normal
  activity and FIFO evidence.

The exchange account mode and OKX request fields must be frozen from the
installed CCXT version and demo metadata before formal execution. Do not rely
on remembered API details.

## 7. Fill and state safety

Persist atomically and bind to environment/account/symbol/market fingerprint:

- session and client-order generations;
- authoritative owned open orders;
- a monotonic fill/trade cursor plus deduplication identity;
- inventory, average entry, gross/net realized PnL, and fees;
- equity high-water mark and drawdown state;
- kill-switch latch, activation identity, reason, and flatten state.

Load market metadata before validating restored state. An empty exchange
position snapshot must explicitly reconcile local inventory to zero. A state
write failure must cancel quotes and halt. A kill switch must survive restart
and may be released only with explicit acknowledgement, zero owned orders, and
position within one base step of zero.

## 8. Market-data and emergency safety

- Stale, crossed, empty, delayed, or non-monotonic market data cancels orders
  and halts new placement.
- REST fallback must not make an old snapshot fresh.
- Maintenance margin and available equity must come from authoritative demo
  account state on every decision cycle.
- Emergency flatten must be a single-flight state machine: submit once,
  reconcile outcome, then retry only under a frozen bounded policy.
- Fatal loop errors must produce a non-success supervisor signal after safe
  cancellation and state persistence are attempted.

## 9. Required offline failure fixtures

Test at minimum:

- create accepted but response lost;
- cancel timed out and order remains open;
- cancel accepted but response lost;
- fetch-open-orders, trades, balance, or position unavailable;
- duplicate, foreign, missing, partially filled, and late-filled orders;
- restart before/after fill and before/after kill activation;
- corrupt, stale, wrong-account, wrong-symbol, and wrong-market state;
- empty position list, long, short, and one-step rounding boundaries;
- WebSocket disconnect/reorder/duplicate plus REST failure;
- stale book, crossed book, clock skew, rate limiting, and partial outage;
- flatten response lost, partial flatten, and delayed position update;
- persistence failure and supervisor restart.

Every unknown state must prove zero new quote submissions.

## 10. Demo execution and gates

Freeze the complete demo specification, source hashes, fixture IDs, demo run
IDs, and artifact contract before the first formal demo order. Refuse run-ID
reuse. Execute the frozen demo matrix once. Do not rerun simulation or demo
orders to repair reporting; recover reports from written streams.

Required gates:

- all offline fixtures and full regression suites pass;
- zero live endpoint attempts and zero secrets in logs/artifacts;
- zero duplicate or unowned orders, ambiguous retries, or overfills;
- exact order/fill/fee/position/accounting reconciliation;
- zero unknown account, order, margin, market, or position states while quoting;
- zero stale-data placements;
- kill switch and single-flight flatten pass every restart fixture;
- runtime/profile equivalence and source hashes reconcile;
- demo normal activity retains at least 80% of its frozen offline control;
- demo round-trip economics are reported after actual demo fees, but no
  profitability result may override an execution-safety failure.

## 11. Closed status and next boundary

Use exactly one status:

- `OKX_DEMO_EVIDENCE_FAILED`
- `OKX_DEMO_RUNTIME_BINDING_FAILED`
- `OKX_DEMO_RECONCILIATION_FAILED`
- `OKX_DEMO_SAFETY_FAILED`
- `OKX_DEMO_ACTIVITY_INSUFFICIENT`
- `OKX_DEMO_EXECUTION_SAFETY_SUPPORT`

Write `COMPLETED.json` last. Even on full support, report
`production_authorized: false`, `live_orders: 0`, and `optuna_executed: false`.
Limited live deployment requires a separate user-approved protocol with a
capital-loss budget, rollback procedure, monitoring/alert ownership, and
explicit live authorization.

## 12. Required outputs

Create fresh non-overwriting artifacts under
`artifacts/okx_demo_execution_safety/` containing:

- initial and final readiness audits;
- frozen profile-promotion manifest and hashes;
- offline failure-fixture manifests and results;
- demo specification, source hashes, run manifest, and raw event streams;
- order, fill, position, fee, accounting, staleness, restart, kill-switch, and
  emergency-flatten audits;
- secret scan, endpoint audit, test counts, final JSON/Markdown decision, and
  completion hash manifests.
