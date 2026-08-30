# AGENTS.md — OKX Demo Future-Dated Book Timestamp Repair

## 1. Activation and authority

This successor protocol becomes active only when these exact contents are
installed as root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation, socket-denied offline fixtures,
fresh non-overwriting repair evidence, and preparation of one fresh read-only
OKX Demo preflight package entirely offline. It authorizes no OKX request,
credential use, order, execution marker, or account-configuration change.

A future read-only Demo preflight requires a fresh exact session arm token and
separate explicit user authorization. Any later formal Demo run requires a
passing fresh preflight, a new package/run/session/token, and another separate
explicit authorization.

Live endpoints, Live credentials/orders, production authorization, Git,
Optuna, validation, holdout, self-trade, and external endpoints during offline
work remain prohibited.

## 2. Immutable failed predecessor

Freeze package `formal-package-20260806T152904Z` and run
`formal-20260806T152904Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`;
- exactly one execution marker and no rerun;
- zero create, amend, cancel, flatten, normal fills, special fills, and fees;
- R1/R2 incomplete;
- terminal position `0` and open orders `0` from two read-only snapshots;
- `COMPLETED.json` SHA-256
  `4b894d665a7ab3f3495a771704fa943817a16ae68bfe6a33a5fe07c9cb0136cd`;
- formal completion manifest SHA-256
  `9ba3903d459226a19f4d495e82086af7eddb2a069579886c6eeb94f6441770b3`;
- `RAW_COMPLETED.json` SHA-256
  `6734270ace8927e47958ab72a2018ad77ca69409ff0fe6cc6fc873385ba8c31e`;
- `UNRESOLVED_FAILURE.json` SHA-256
  `602178fbd823d2a2bb2ffbce020b62bea9fe5375dcfc47e431ec4300e9f3d919`;
- staleness reconciliation SHA-256
  `3e70d0a422d6275af2510020507adc28b7748aeaa9ebf5403b6295d90c47fe6d`.

Never edit, resume, recover in place, or rerun that package/run. It proved that
the frozen formal gateway rejected a valid OKX book solely because its
timestamp was tens of milliseconds ahead of local receive time, despite
account clock skew remaining within the frozen 1,500 ms budget.

## 3. Repair objective

Use signed book age `local_receive_ms - exchange_book_timestamp_ms`.

- Accept zero or positive age only through the frozen market-staleness limit.
- Accept negative age only when its magnitude is no greater than the frozen
  clock-skew budget supplied explicitly by the caller.
- Accept both exact boundaries.
- Fail closed beyond either boundary, for nonpositive/missing timestamps,
  empty/crossed/nonpositive books, nonpositive budgets, or non-integer values.
- Never hide a stale positive book behind the future-timestamp allowance.
- Preserve monotonic warmup, account clock-skew, Demo transport, and placement
  blocking guarantees.

All formal collection and warmup call sites must pass both frozen budgets.
Preflight and formal runtime semantics must reconcile exactly.

## 4. Mandatory offline gates

Use socket-denied fixtures for age `0`, positive staleness boundary, one unit
beyond positive staleness, negative age within budget, exact negative boundary,
one unit beyond the negative boundary, invalid/missing timestamp, empty book,
crossed/nonpositive spread, invalid budgets, and unchanged monotonic warmup.

Prove every rejected/unknown fixture performs zero create, amend, cancel,
flatten, Live attempt, account mutation, credential access, Optuna import,
validation, holdout, or Git write. Run targeted tests, the successor-applicable
root non-Optuna suite with exact exclusions recorded, and the permitted
backtest non-Optuna suite.

Create fresh non-overwriting repair evidence with predecessor/source hashes,
test/network audits, endpoint/mutation audit, secret scan, decision, completion
manifest, and a terminal offline marker written last.

## 5. Fresh boundary

Never reuse any predecessor package, run, session, checkpoint, arm token, or
artifact directory. Passing offline repair authorizes no OKX request. Prepare a
fresh read-only preflight package/run/session/token offline and stop for a
separate exact authorization. Passing that preflight still authorizes no order.

## 6. Reporting

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

This repair does not authorize Live production.
