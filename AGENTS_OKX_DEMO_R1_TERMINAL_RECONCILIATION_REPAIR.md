# AGENTS.md — OKX Demo R1 and Terminal Reconciliation Repair

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

Freeze package `formal-package-20260809T152113Z` and run
`formal-20260809T152113Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`;
- reason `FormalSafetyError:R1 checkpoint requires no orders and nonzero position`;
- exactly one execution marker and no rerun or resume;
- normal post-only creates `49`, authoritative cancel confirmations `48`,
  normal bid/ask fills `1 / 0`, and R1/R2 incomplete;
- one reduce-only flatten order filled in two partial fills;
- last authoritative position `0`, open orders `0`, and zero submissions after
  the fatal error;
- aggregate gross PnL `0.39002`, fees `0.45627931`, and net PnL `-0.06625931`;
- terminal report recovery required and no `COMPLETED.json`;
- execution marker SHA-256
  `1f085aac5729aa8fcccfdc9601cae2370794da3d98238110567288c58dd2f830`;
- `UNRESOLVED_FAILURE.json` SHA-256
  `c0771fe60e71f80e8b59b4efcd97ff2351caab6536ff3d3b33dd19a72161d379`;
- formal state SHA-256
  `a4d1dcf6833b46f87e88831de9dd5760f34f4f3e53c31f99447daf8bc9fc82f2`;
- validation state SHA-256
  `5bffd99213150ee96d00ff4675c57c30cef2b235a7b3c965d8183d8f448fe57d`;
- safety stream SHA-256
  `5bbf05e8585f6162fccf0642deb38be00a937630f6cd2210df7be766636c4703`.

Never edit, resume, recover in place, or rerun that package/run.

## 3. Repair objective

When a normal fill makes an acknowledged owned order disappear from the
authoritative open-order set, reconcile that identity as filled before any R1
checkpoint action. Missing ownership may be cleared only when same-side fill
counter deltas explain it exactly and no foreign or unresolved order exists.
Unexplained disappearance remains fail-closed.

Validate the controller and engine R1 checkpoint preconditions before either
durable checkpoint transition. A rejected controller transition must leave the
engine in its pre-checkpoint phase with no resume token or handoff. Emergency
cancel/flatten is a shutdown path and must never become part of R1 transition.

After a single reduce-only flatten with any number of partial fills, reconcile
the engine ledger, engine open-order view, controller closed ownership, and two
fresh authoritative flat/empty account snapshots. Historical closed orders may
remain in the validation audit history but must not be treated as open durable
state.

Account-only terminal failure reporting must use two post-shutdown flat/empty
snapshots, safely reconcile a stale halted controller from authoritative and
engine evidence, persist sanitized reconciliation evidence, cumulative gateway
audit, failure manifest, completion hashes, and `COMPLETED.json`. It must not
require a fresh market book. Ambiguous, non-flat, open-order, binding, ledger,
counter, or order-identity states remain unresolved and fail closed.

Preserve signed-age pre-arm behavior, R2 warmup/cumulative audit, fill-cursor
union, activity shutdown, fee attribution, single-flight flatten, and all
frozen risk budgets unchanged.

## 4. Mandatory offline gates

Use socket-denied fixtures for same-side filled-order ownership removal,
unexplained or wrong-side disappearance rejection, controller prevalidation
before engine checkpoint, rejected R1 atomicity, successful R1 handoff, strict
separation from flatten, one flatten with multiple partial fills, historical
closed-order retention with zero open orders, two post-flatten flat snapshots,
stale halted-controller terminal reconciliation, and failure manifest success.

Also cover binding mismatch, first/second non-flat account, authoritative open
orders, ledger non-flat, engine open orders, pending intent, counter regression,
foreign identity, more than one flatten, and terminal evidence write failure.
Every rejected fixture must prove zero new create, amend, cancel, flatten, Live,
account mutation, credential access, Optuna import, validation, holdout, or Git
write beyond the explicitly modeled pre-existing fixture history.

Run targeted tests, the successor-applicable root non-Optuna suite with exact
exclusions recorded, and the permitted backtest non-Optuna suite. Create fresh
non-overwriting repair evidence with predecessor/source hashes, test/network
audits, endpoint/mutation audit, secret scan, decision, completion manifest,
and a terminal offline marker written last.

## 5. Fresh boundary and reporting

Never reuse predecessor package, run, session, checkpoint, token, or artifact
directory. Passing offline repair authorizes no OKX request. Prepare a fresh
read-only preflight package/run/session/token offline and stop for separate
authorization. Passing preflight still authorizes no order.

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

This repair does not authorize Live production.
