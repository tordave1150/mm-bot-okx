# AGENTS.md — OKX Demo R2 Warmup and Cumulative Audit Repair

## 1. Activation and authority

This successor protocol is active only when these exact contents are installed
as root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation, socket-denied offline fixtures,
fresh non-overwriting repair evidence, and preparation of one fresh read-only
OKX Demo preflight package entirely offline. It authorizes no OKX request,
credential use, order, execution marker, preflight execution, formal execution,
or account-configuration change.

A future read-only Demo preflight requires a fresh exact session arm token and
separate explicit user authorization. Any later formal Demo run requires a
passing fresh preflight, a new package/run/session/token, and another separate
explicit authorization.

Live endpoints, Live credentials/orders, production authorization, Git,
Optuna, validation, holdout, self-trade, and external endpoints during offline
work remain prohibited.

## 2. Immutable failed predecessor

Freeze package `formal-package-20260807T130847Z` and run
`formal-20260807T130847Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`;
- exactly one execution marker and no rerun;
- R1 complete and R2 incomplete;
- one normal bid fill, one normal ask fill, and one normal FIFO round trip;
- 37 normal create acknowledgements, 35 authoritative cancel confirmations,
  zero amend, zero flatten, and zero special fills;
- gross/fee/net economics `0.5810 / 0.2607478 / 0.3202522` USDT;
- terminal position `0` and open orders `0`;
- persisted R2 kill latch active and no `R2_resume.json`;
- `COMPLETED.json` SHA-256
  `468f93c2234d93546178185287f9a7d59973a03294912de64969c488dda56c87`;
- formal completion manifest SHA-256
  `ee8f542d46527ec1f9596a925c6865c61f17023d397a63605bde3195ab5231d6`;
- `RAW_COMPLETED.json` SHA-256
  `e85004baaf169e82e0ffd0fd091799911306a2bd2a9dd03b21871eb1d48f3bf3`;
- R2 handoff SHA-256
  `6f211c26c71b4217428d71f752294ace3ca3bbce010c83a3dd1868711eca6439`;
- execution marker SHA-256
  `6bdf321c21ff4a91d4ca9fef6823e0c266527465c2431dd77e86307836d2fc26`.

Never edit, resume, recover in place, or rerun that package/run.

## 3. Repair objective

After R2 restart and two authoritative flat/empty snapshots, warm the newly
constructed frozen quote engine with the same bounded monotonic warmup used by
normal execution before constructing the kill-latch quote probe. Warmup and
probe construction are read/compute only and must dispatch zero create, amend,
cancel, flatten, Live, or account mutation. A missing/denied quote, invalid
state, non-monotonic/stale book, or any mutation drift must fail closed while
the persisted kill latch remains active.

Persist a hash-chained process-generation gateway audit at every restart
handoff and terminal boundary. Terminal endpoint, cancellation, flatten, and
Live counters must be cumulative across all process generations, with
contiguous generations, monotonic same-generation snapshots, allow-listed
mutation methods, and exact reconciliation to durable order/fill events. Never
report only the final process-local counters as whole-run totals.

## 4. Mandatory offline gates

Use socket-denied fixtures for R2 warmup-before-probe ordering, cold-engine
quote denial, warmup failure, mutation drift during warmup/probe, persisted kill
blocking, explicit acknowledgement matching, cumulative generation sums,
same-generation monotonicity, missing generation, truncated/hash-corrupt audit
stream, mutation allow-list, terminal create/cancel/flatten reconciliation, and
unchanged R1/R2/future-book/fill-cursor safety behavior.

Every rejected/unknown fixture must prove zero create, amend, cancel, flatten,
Live attempt, account mutation, credential access, Optuna import, validation,
holdout, or Git write. Run targeted tests, the successor-applicable root
non-Optuna suite with exact exclusions recorded, and the permitted backtest
non-Optuna suite.

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
