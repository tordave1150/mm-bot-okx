# AGENTS.md — OKX Demo Bounded Soak and Failure-Injection Readiness

## 1. Activation and authority

This successor protocol is active only when these exact contents are installed
as root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation, deterministic socket-denied offline
failure-injection fixtures, fresh non-overwriting offline evidence, and support
code for a future fresh read-only OKX Demo preflight package. It authorizes no
OKX request, credential access, order, preflight execution, formal execution,
soak execution, execution marker, account-configuration change, or Live action.

A future read-only Demo preflight requires fresh package/run/session/token
identifiers prepared offline and separate exact user authorization. A later
bounded Demo soak requires a passing fresh preflight, a new soak
package/run/session/token, and another separate exact authorization.

Live endpoints, Live credentials/orders, production authorization, Git,
Optuna, validation, holdout, self-trade, and external endpoints remain
prohibited.

## 2. Immutable successful predecessor

Freeze package `formal-package-20260810T123953Z` and formal run
`formal-20260810T123953Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SUPPORT` and exactly one execution marker;
- R1/R2 complete across process generations `0, 1, 2`;
- normal post-only creates/cancels `44 / 42`;
- normal bid/ask fills `1 / 1` and one normal FIFO round trip;
- reduce-only flatten orders/fills `0 / 0`;
- final authoritative position/open orders `0 / 0`;
- normal and aggregate gross PnL `0.4960`, fees `0.2598552`, and
  net PnL `0.2361448`;
- Live endpoint attempts/orders `0 / 0`;
- execution marker SHA-256
  `2f28453da64775809c8b5485c38e40df1ae324e2f1f15c6917ed66bc652c3d10`;
- `COMPLETED.json` SHA-256
  `f62a17a606b7c4826f862ee6ae0cab7ef35d224c9f08bfc13234aa188f12b11e`;
- formal state SHA-256
  `9e11edb821fc396277eaf541011987123ae374fc8833fdcfb746692037baa1ab`;
- validation state SHA-256
  `a9dd54551e08f1b7b69783debe954b55a983b4bcf7685beeb5f5a773816b74bb`;
- cumulative gateway audit stream SHA-256
  `5de023e3e3a215d4d3d39ca309f02fb3ff1d403764681382130d4a5c86ad60c5`;
- formal completion manifest SHA-256
  `3277fe669bbe22cbb725d64fd6bc02b3aa2eb5198e7f1487186b5d060381ad37`.

Never edit, resume, rerun, recover in place, or use that successful package for
a soak. It is immutable evidence only.

## 3. Offline readiness objective

Define a bounded Demo soak protocol without starting it. The future soak must
use a fresh identity, finite wall-clock and activity budgets, fixed capital and
inventory limits, post-only normal orders, at most one unresolved reduce-only
flatten, and a terminal flat/empty account requirement. It must never reuse a
formal checkpoint or resume token.

Build deterministic fault injection around the real controller, validation
engine, gateway accounting, and durable artifact boundaries. Every injected
fault must have an explicit injection point, expected durable state, permitted
mutation delta, retry budget, and terminal decision. Unknown or ambiguous state
fails closed.

Transport failures before dispatch must produce no mutation. A timeout after
dispatch creates an ambiguous intent and must prohibit another create until
authoritative order/fill reconciliation. Rate limiting and bounded transient
read failures may retry reads only; mutation retry without authoritative
resolution is forbidden.

Duplicate, delayed, reordered, paginated-history, recent-tail, and
multi-partial fills must remain idempotent and reconcile inventory, fees,
ownership, and cursor state. Foreign orders, conflicting duplicate fills,
unexplained order disappearance, counter regression, or account mismatch fail
closed.

Stale, future-beyond-skew, crossed, empty, or invalid books block placement.
Clock, market, account, and fee gates must remain independent. A disconnect or
process restart must restore only hash-verified durable state and must not
duplicate an order or flatten.

A single-instance lease must prevent concurrent controllers. Evidence-write or
state-store failure must stop new mutation. Shutdown must cancel only owned
orders, use at most one single-flight reduce-only flatten when non-flat, support
multiple partial fills, obtain two fresh flat/empty account snapshots, and
write terminal evidence last.

Preserve the completed R1 ownership/checkpoint repair, signed-age behavior, R2
warmup/cumulative audit, fill-cursor union, activity shutdown, fee attribution,
and frozen risk budgets unchanged.

## 4. Mandatory socket-denied gates

Cover at least:

- timeout before create dispatch and timeout after ambiguous create dispatch;
- duplicate create acknowledgement and duplicate fill idempotency;
- delayed/reordered fills, paginated-history omission recovered by recent tail,
  and conflicting duplicate fill rejection;
- one flatten order with multiple partial fills and no second flatten;
- read timeout, bounded rate-limit backoff, retry exhaustion, and no mutation
  retry after ambiguous dispatch;
- stale, exact-boundary, future-within-skew, future-beyond-skew, crossed, empty,
  and invalid market books;
- process restart before dispatch, after dispatch, after fill, at R1, and at R2;
- single-instance lease collision and stale-lease recovery;
- hash-chain truncation/corruption and terminal evidence-write failure;
- foreign order, counter regression, binding mismatch, pending intent,
  controller/engine/account mismatch, non-flat terminal account, and terminal
  open orders;
- clean shutdown, owned-only cancel, multi-partial flatten reconciliation, two
  post-shutdown flat snapshots, and terminal completion success.

Every rejected fixture must prove zero new create, amend, cancel, flatten,
Live, account mutation, credential access, Optuna import, validation, holdout,
or Git write beyond explicitly modeled pre-existing history.

Run targeted tests, the successor-applicable root non-Optuna suite with exact
exclusions recorded, and the permitted backtest non-Optuna suite under socket
denial. Create fresh non-overwriting evidence containing predecessor/source
hashes, scenario matrix, test/network audit, endpoint/mutation audit, secret
scan, decision, completion manifest, and a terminal offline marker written
last.

## 5. Fresh boundary and reporting

Passing offline fixtures authorizes no OKX request and no soak. Do not prepare
or execute a preflight, formal run, or soak in this phase. The next phase may
prepare fresh read-only preflight identifiers entirely offline, then must stop
for separate exact authorization.

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

This protocol does not authorize Live production.

