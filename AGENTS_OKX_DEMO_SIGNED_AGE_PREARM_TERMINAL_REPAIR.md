# AGENTS.md — OKX Demo Signed-Age Pre-Arm and Terminal Reporting Repair

## 1. Activation and authority

This successor protocol is active only when these exact contents are installed
as root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation, socket-denied offline fixtures,
fresh non-overwriting repair evidence, and preparation of one fresh read-only
OKX Demo preflight package entirely offline. It authorizes no OKX request,
credential use, order, execution marker, preflight execution, formal execution,
or account-configuration change.

A future read-only Demo preflight requires a fresh exact arm token and separate
explicit authorization. A later formal run requires a passing fresh preflight,
a new package/run/session/token, and another separate explicit authorization.

Live endpoints, Live credentials/orders, production authorization, Git,
Optuna, validation, holdout, self-trade, and external endpoints during offline
work remain prohibited.

## 2. Immutable failed predecessor

Freeze package `formal-package-20260807T151814Z` and run
`formal-20260807T151814Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`;
- reason `FormalSafetyError:MARKET_DATA_STALE`;
- exactly one execution marker and no rerun;
- R1/R2 incomplete, normal creates `0`, flatten `0`, and no order stream;
- durable position `0`, owned orders `0`, pending intents `0`, and external
  order submissions `0`;
- Live endpoint attempts/orders `0 / 0`;
- terminal report recovery required because market freshness incorrectly gated
  failure reporting;
- execution marker SHA-256
  `187dc225d96eb2c7ebd346a93e69a06818a4bd966ad27fa725cd4b8644f5d97b`;
- `UNRESOLVED_FAILURE.json` SHA-256
  `7ba7fe37218fcc22e90334f5db7c519cf4e082fab7e748741b3ae83a3aec4ba4`;
- formal state SHA-256
  `e74c802d987eae927732127c9ba9d8a60337e18c7212f00fe4d13402b8eb8676`;
- validation state SHA-256
  `3a8c0cc7c2e4443ea0393469c7cce75ce1ee894a8f1d0bb999cdcbba22855fbe`;
- safety stream SHA-256
  `5bbf05e8585f6162fccf0642deb38be00a937630f6cd2210df7be766636c4703`.

Never edit, resume, recover in place, or rerun that package/run.

## 3. Repair objective

Make formal observation signed-age semantics exactly match the gateway and
preflight: accept age from negative frozen clock-skew boundary through positive
frozen staleness boundary, inclusive; fail closed beyond either boundary and
for invalid budgets or values. Never use future allowance to hide positive
staleness.

Before controller arm only, retry a specialized positive-stale book rejection
for a bounded number of read-only attempts. Persist signed age, budgets, attempt,
and rejection reason before every retry. Empty, crossed, invalid, or unknown
books fail immediately. Every retry must prove zero create, amend, cancel,
flatten, Live, or account mutation. No retry is allowed after arm.

Failure reporting must accept two authoritative flat/empty account snapshots
without requiring a market book when ledger position, owned orders, pending
intents, and transport counters reconcile. Persist sanitized account-only
terminal evidence and cumulative gateway audit. Market freshness may block
placement but must not prevent a safe terminal failure manifest.

Preserve the R2 warmup and cumulative generation-audit repair unchanged.

## 4. Mandatory offline gates

Use socket-denied fixtures for signed ages `0`, positive boundary, positive one
past boundary, negative within budget, exact negative boundary, negative one
past boundary, invalid values/budgets, stale-then-fresh pre-arm retry, bounded
stale exhaustion, invalid/crossed immediate failure, no retry after arm, zero
mutation on every rejected attempt, account-only terminal success, account
snapshot mismatch, non-flat account, ledger mismatch, cumulative audit
preservation, and unchanged R1/R2/fill-cursor/future-book safety.

Run targeted tests, the successor-applicable root non-Optuna suite with exact
exclusions recorded, and the permitted backtest non-Optuna suite. Create fresh
non-overwriting evidence with predecessor/source hashes, network/mutation audit,
secret scan, decision, completion manifest, and terminal marker written last.

## 5. Fresh boundary and reporting

Never reuse predecessor package, run, session, checkpoint, token, or artifact
directory. Passing offline repair authorizes no OKX request. Prepare a fresh
read-only preflight package/run/session/token offline and stop for separate
authorization.

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

This repair does not authorize Live production.
