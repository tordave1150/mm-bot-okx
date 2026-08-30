# AGENTS.md — OKX Demo Fill Cursor Recovery Repair

## 1. Activation and scope

This successor protocol is dormant until the user explicitly installs these
exact contents as the root `AGENTS.md`.  Its preserved source copy is
`AGENTS_OKX_DEMO_FILL_CURSOR_RECOVERY_REPAIR.md`.

When active, it authorizes implementation, socket-denied offline fixtures, a
separately armed read-only OKX Demo preflight, and at most one separately armed
successor OKX Demo fill/restart run.  Offline repair does not authorize network
access or orders.  Preflight and formal execution each require a fresh explicit
instruction and exact session-scoped arm token.

Live endpoints, Live credentials/orders, production capital/defaults, Git,
Optuna, validation, holdout, external endpoints during offline work, self-trade,
and account-configuration mutation remain prohibited.

## 2. Immutable failed predecessor

Freeze the completed predecessor read-only:

- package `formal-package-20260805T151737Z`;
- formal run `formal-20260805T151737Z`;
- status `OKX_DEMO_FILL_RESTART_RECONCILIATION_FAILED`;
- exactly one formal execution marker;
- R1/R2 incomplete;
- terminal position `0` and open orders `0`;
- predecessor `COMPLETED.json` SHA-256
  `9f88df950a1771ca484c389542d50b00ecf486d7d75770f6ed8cbca4e5b898d4`;
- formal completion manifest SHA-256
  `55e28a0584f0bacc6925aa1d3be048e5e663b868ddc867fb2a0e29d810053892`;
- formal decision SHA-256
  `3bac052811f91b4db5bef181aa9565a2fa5d4a9a29de9a7b983e04375baf64c9`;
- operation reconstruction SHA-256
  `dd3fac15198763b82982e2c51973dfa8f6e94b9118fdc5bc4bfae8e94727e225`.

Never edit, overwrite, repair in place, resume, or rerun that package/run.  It
proved the defect: the frozen CCXT paginated `since` query omitted a fresh owned
maker fill that was visible in the non-paginated recent tail.

## 3. Repair objective

Replace the single-source fill read with a bounded authoritative union:

1. read paginated fill history from the frozen run start;
2. immediately read a non-paginated recent tail;
3. filter both to timestamps at or after the run start;
4. union by immutable trade ID;
5. require identical order ID, client ID, timestamp, side, price, amount, fee,
   fee currency, and maker/taker classification for overlap;
6. deduplicate identical overlap and fail closed on conflicting overlap;
7. sort stably by timestamp and trade ID before the persistent cursor;
8. retain same-timestamp, late-fill, and reordered-page guarantees.

One reduce-only flatten order may have any positive number of partial fill
records whose exact quantities sum to the known position.  All partial fills
must map to the one persisted special order; overfill, unknown identity,
duplicate conflict, a second flatten create, or incomplete fee data fails
closed.  Special fills remain excluded from normal activity, FIFO round trips,
and normal economics while remaining in aggregate account reconciliation.

## 4. Mandatory offline gates

Before any new preflight, require socket-denied fixtures for:

- history omits fresh fill while recent tail contains it;
- identical history/tail overlap deduplicates once;
- conflicting duplicate identity fails closed;
- old recent-tail rows are excluded by the run boundary;
- stable same-timestamp ordering and late-fill cursor behavior;
- position-before-trade becomes reconcilable without a new normal order;
- one flatten create produces multiple partial fills and ends flat;
- duplicate/reordered partial fills do not double count;
- partial quantities exceeding the special order fail closed;
- no automatic create retry, second flatten, Live attempt, credential leak,
  Optuna import, validation, holdout, or Git write.

Run targeted tests, the complete root non-Optuna suite, and the permitted
backtest non-Optuna suite.  Create fresh non-overwriting repair evidence with
source hashes, predecessor hashes, test/network audits, secret scan, decision,
completion hashes, and a terminal offline marker written last.

## 5. Fresh identifier boundary

Never reuse any predecessor package, run, session, checkpoint, arm token, or
artifact directory, including `preflight-20260805T-arm03`,
`formal-package-20260805T151737Z`, or `formal-20260805T151737Z`.

A passing offline repair authorizes no OKX request.  The next boundary is a
fresh read-only OKX Demo preflight bound to the repaired source and a new
offline repair ID.  Passing preflight authorizes no orders.  Any successor
formal run requires another fresh package/run/session and explicit arm token.

## 6. Successor formal safety

Retain the predecessor Demo-only transport proof, post-only normal orders,
write-ahead client identities, cancel-before-replace/restart, ambiguous-create
resolution without retry, `0.01 BTC` maximum inventory, `750 USDT` capital
copy, leverage `3`, 120-minute/120-create cap, one owned bid/ask, one
single-flight reduce-only flatten, terminal flat/empty requirement, and actual
fee/accounting reconciliation.

The successor formal run must still prove an owned bid maker fill, an owned ask
maker fill, one normal FIFO round trip, R1 after an observable nonzero fill,
and R2 kill-latch restart.  No organic fill remains a valid activity-insufficient
result and cannot be repaired with a profile change or second formal matrix.

## 7. Reporting boundary

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

Even successor support would not authorize Live production.
