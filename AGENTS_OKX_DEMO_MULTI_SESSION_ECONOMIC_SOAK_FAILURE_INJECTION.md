# AGENTS.md — OKX Demo Multi-Session Economic Soak and Operational Failure Injection

## 1. Activation and authority

This successor protocol is active only when these exact contents are installed
as root `AGENTS.md` after separate exact user authorization. Preserve this
source copy byte-for-byte. Merely creating or reading this source file does not
activate it and does not authorize replacing root `AGENTS.md`.

When active, phase A0 authorizes only project-local implementation,
deterministic socket-denied tests, audits, fresh non-overwriting offline
evidence, and offline preparation of one fresh read-only OKX Demo preflight
identity after all A0 gates pass. It authorizes no OKX request, credential
access, order, preflight execution, economic-soak execution, failure-injection
execution, execution marker, account-configuration change, or Live action.

Approval boundaries are independent and fail closed:

- A0 `OFFLINE_BUILD`: implementation, socket-denied tests, audits, evidence,
  and later fresh preflight identifiers prepared offline;
- A1 `READ_ONLY_DEMO_PREFLIGHT`: fresh preparation/run/session/arm token and
  separate exact user authorization; reads only and zero order mutation;
- A2 `MULTI_SESSION_ECONOMIC_SOAK`: passing A1 evidence, fresh campaign/package/
  run/session/arm-token identities, and separate exact user authorization;
- A3 `OPERATIONAL_FAILURE_INJECTION`: fresh identities and separate exact user
  authorization distinct from A2, even if A2 passes;
- A4 `PRODUCTION_READ_ONLY_SHADOW`: outside this protocol and goal; it requires
  a successor protocol, goal, risk review, and exact user authorization.

No approval crosses phases, authorizes a later generation, expands a risk
budget, or permits identifier reuse. A repair invalidates any unexecuted
downstream package and requires fresh source-bound identities.

Live endpoints, Live credentials/orders, withdrawals, account or leverage
changes, self-trade, credential serialization, risk expansion, Git operations,
Optuna, validation, holdout, and unauthorized external endpoints are prohibited.

## 2. Immutable predecessors

Freeze successful formal package/run `formal-package-20260810T123953Z` /
`formal-20260810T123953Z` as immutable evidence. Preserve the predecessor hashes
already fixed by the bounded-soak readiness protocol, including execution marker
`2f28453da64775809c8b5485c38e40df1ae324e2f1f15c6917ed66bc652c3d10`,
`COMPLETED.json`
`f62a17a606b7c4826f862ee6ae0cab7ef35d224c9f08bfc13234aa188f12b11e`,
and completion manifest
`3277fe669bbe22cbb725d64fd6bc02b3aa2eb5198e7f1487186b5d060381ad37`.

Freeze successful bounded-soak package/run `soak-package-20260811T140223Z` /
`soak-20260811T140223Z` as immutable operational-safety evidence with:

- package `COMPLETED.json` SHA-256
  `e658a0aede428465d579434aa19a9e975f0564e6134b9577ee9f373a0eacd958`;
- package `SOAK_PACKAGE_COMPLETED.json` SHA-256
  `29e1743b978abaa5d7551e7515978f540029944e85961d2cf2ca0d488c2d1e56`;
- run completion hashes SHA-256
  `56e01765779d4eec5b75431f38ed52b7032da56552464b9a05d7a9c002c669c4`;
- run decision SHA-256
  `4fdd42ac418f597a62ee070483d534491aa3a4fba5f17f8612119e36b8bb215b`;
- final authoritative position/open orders `0 / 0`, two fresh flat/empty
  snapshots, and Live attempts/orders `0 / 0`;
- normal creates/cancels `2 / 1`, normal bid/ask fills `1 / 0`, normal FIFO
  round trips `0`, normal net PnL `-0.1283324 USDT`;
- aggregate gross PnL `0.0700`, fees `0.4491984`, and net PnL
  `-0.3791984 USDT`; one single-flight reduce-only flatten.

This soak proves bounded operational closure only. It is not economic promotion
evidence because normal economics were negative, fills were one-sided, and no
normal maker FIFO round trip completed.

Never edit, rerun, resume in place, recover in place, or reuse any prior formal,
preflight, soak, repair, campaign, session, checkpoint, or arm-token identity.
All other prior packages remain historical and non-reusable.

## 3. Goal and decision states

Prove multi-session economic durability and fail-closed operational recovery on
OKX Demo before proposing production read-only shadow. This protocol can never
produce production authorization.

The only campaign decisions are:

- `READY_FOR_PRODUCTION_READ_ONLY_SHADOW` after every economic and operational
  gate passes with authoritative evidence;
- `INSUFFICIENT_EVIDENCE` when the finite economic campaign ends without the
  activity or fill sample floor and without a safety failure;
- `NOT_READY` for a failed safety, accounting, economic, binding, or operational
  gate.

`READY_FOR_PRODUCTION_READ_ONLY_SHADOW` authorizes no production connection,
credential access, account mutation, or order. It is a recommendation boundary
only.

## 4. Frozen economic session risk budget

Every A2 session must be source-bound, sequential, and independently durable:

- OKX Demo simulated trading only; verify sandbox mode, simulated-trading
  header, expected hostname, API permissions, market, account mode, fee tier,
  and clock before any mutation;
- instrument `BTC/USDT:USDT`, linear swap, isolated margin, net position mode,
  leverage `3x`, modeled capital `750 USDT`;
- fixed normal lot and absolute inventory cap `0.01 BTC`;
- at most one owned normal bid and one owned normal ask; normal creates are
  post-only; cancels are owned-only; amend and self-trade counts remain zero;
- wall-clock limit `30 minutes`, normal-create limit `60`, observation interval
  at least `2000 ms`, maximum book age `1000 ms`, maximum absolute clock skew
  `1500 ms`;
- read retry attempts at most `3`; mutation retry attempts exactly `0`;
- soft/hard session drawdown guards `22.50 / 37.50 USDT`;
- at most one unresolved single-flight reduce-only flatten, supporting multiple
  partial fills without a second flatten;
- shutdown cancels only owned orders, reconciles controller/engine/gateway/
  account, and records two fresh flat/empty account snapshots before terminal
  evidence is written last.

The aggregate A2 campaign is limited to `12` non-overlapping sessions,
`6 hours`, `720` normal creates, and an aggregate hard-loss stop of `75 USDT`.
No failed, incomplete, or ambiguous session may be replaced silently or omitted
from campaign accounting.

Fail closed immediately on hard kill, foreign order, source/hash/permission/
account/fee drift, unexplained order disappearance, pending or ambiguous
mutation without authoritative resolution, fill conflict, counter regression,
durable-state or evidence-write failure, lease conflict, component/account
mismatch, or terminal non-flat position/open orders.

## 5. Economic evidence and pass gates

Use actual maker fees and immutable fill identities. Attribute normal maker
economics separately from emergency or shutdown flatten economics. For each
session and the campaign aggregate record at least:

- normal and special fills by side, order, liquidity, fee currency, fee cost,
  price, amount, timestamp, and durable cursor source;
- realized spread, inventory PnL, fee PnL, gross/net PnL, peak equity, drawdown,
  markouts, time-to-reentry, inventory holding time, and FIFO round trips;
- quote-mode and activity counters, blocked-placement reasons, kill decisions,
  create/cancel/flatten counts, retry counts, and terminal reconciliation;
- causal chain from maker fill through inventory defense and maker work-off or
  re-entry. Immediate taker flatten is permitted only for bounded shutdown or
  emergency risk closure and cannot satisfy causal re-entry.

An economic pass requires all of:

- exactly `12` terminally reconciled sessions unless a hard stop ends the
  campaign with `NOT_READY`;
- at least `24` normal maker fills, including at least `8` bid and `8` ask
  fills, with `min(bid,ask) / max(bid,ask) >= 0.60`;
- at least `8` normal FIFO maker round trips;
- aggregate normal net PnL after actual maker fees strictly greater than zero;
- special flatten in at most `20%` of sessions and all special economics
  excluded from normal strategy ranking;
- `unclassified_quote_mode_ticks == 0`, exact activity/order/fill/fee/cursor
  counter reconciliation, and explicit causal re-entry evidence;
- every session ends with authoritative position/open orders `0 / 0`, two
  terminal flat/empty snapshots, and Live attempts/orders `0 / 0`.

If the twelfth safe session completes without the activity/fill/round-trip
sample floor, decide `INSUFFICIENT_EVIDENCE`. Do not extend, substitute sessions,
relax gates, or rank by special-flatten PnL without a successor campaign and
separate authorization.

## 6. Operational failure-injection campaign

A3 is separate from A2 and limited to `10` scenario runs, `10 minutes` and `4`
normal creates per run, `40` normal creates aggregate, and at most one unresolved
single-flight flatten per run.

Cover at least:

- timeout before dispatch and timeout after ambiguous dispatch;
- bounded read timeout/rate-limit backoff and retry exhaustion;
- duplicate acknowledgement and duplicate-fill idempotency;
- delayed, reordered, paginated-history, recent-tail, multi-partial, and
  conflicting duplicate fills;
- process restart before dispatch and after intent, acknowledgement, fill,
  cancel, R1, and R2 durable boundaries;
- single-instance lease collision and verified stale-lease recovery;
- state-store failure, hash-chain truncation/corruption, source/binding drift,
  and terminal evidence-write failure;
- stale, exact-boundary, future-within-skew, future-beyond-skew, crossed, empty,
  and invalid books;
- foreign order, order disappearance, account mismatch, counter regression,
  pending intent, controller/engine/gateway/account mismatch, non-flat terminal
  account, and terminal open orders;
- clean shutdown, owned-only cancel, one multi-partial flatten, two terminal
  account snapshots, terminal account-only failure reporting, and successful
  terminal completion.

A failure before dispatch must produce no mutation. A timeout after dispatch
creates one ambiguous intent and blocks every new create until authoritative
order/fill reconciliation. Reads alone may retry within budget. Mutation retry
is forbidden. Unknown state fails closed. Every scenario must end flat/empty
with durable evidence or remain explicitly unresolved without duplicate
mutation.

## 7. Mandatory A0 implementation and socket-denied gates

Implement, without activating A1-A3:

- a source-bound campaign manifest and append-only session registry with unique
  identities, aggregate wall/activity/loss budgets, and no session omission;
- a campaign economic aggregator using durable per-session fill/cursor data,
  normal-versus-special attribution, Decimal arithmetic, FIFO round trips,
  drawdown, markouts, fill balance, causal re-entry, and exact counter audits;
- a campaign supervisor that refuses concurrency, identifier reuse, budget
  regression, evidence gaps, predecessor/source drift, and unsafe terminal state;
- failure-injection adapters around the real executor, controller, validation
  engine, gateway, state store, lease, and evidence boundaries;
- terminal decision and completion manifests whose hashes bind every included
  session/scenario and whose terminal marker is written last.

Run targeted tests, successor-applicable root non-Optuna tests with exact
exclusions recorded, and permitted backtest non-Optuna tests under socket denial
and blank credentials. Rejected fixtures must prove zero new create, amend,
cancel, flatten, Live, account mutation, credential access, Optuna import,
validation, holdout, and Git write beyond explicitly modeled history.

A0 evidence must be fresh and non-overwriting and contain predecessor/source
hashes, frozen risk specification, requirement/scenario matrices, test and
socket audit, endpoint/mutation audit, secret scan, decision, completion hashes,
and a terminal offline marker written last.

Only after A0 passes may one fresh read-only A1 preflight identity be prepared
entirely offline. Stop and report its preparation ID, evidence ID, run ID,
session ID, and exact arm token for separate user authorization. Do not connect
to OKX or create an execution marker while preparing it.

## 8. Reporting invariants

Always report:

- `production_authorized: false`
- `live_mode_available: false`
- `live_endpoint_attempts: 0`
- `live_orders: 0`
- `optuna_executed: false`
- `validation_opened: false`
- `holdout_opened: false`
- `git_write_operation: false`

This protocol does not authorize Live production.
