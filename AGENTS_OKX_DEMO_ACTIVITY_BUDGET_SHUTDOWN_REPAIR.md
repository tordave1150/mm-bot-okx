# AGENTS.md — OKX Demo Activity-Budget Shutdown Repair

## 1. Activation and authority

This protocol is dormant until the user explicitly installs these exact
contents as the root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation and socket-denied offline evidence,
then a separately armed fresh read-only OKX Demo preflight and at most one
separately armed successor formal run. Activation or offline support alone
authorizes no network request or order.

Live endpoints, Live credentials/orders, production authorization, account
configuration mutation, Git, Optuna, validation, holdout, self-trade, and
external endpoints during offline work remain prohibited.

## 2. Immutable predecessor

Freeze package `formal-package-20260805T162735Z` and run
`formal-20260805T162735Z` read-only with:

- status `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`;
- exactly one execution marker;
- R1 completed and R2 incomplete;
- one normal bid maker fill, zero normal ask fills, and zero FIFO round trips;
- one special single-flight reduce-only flatten;
- final position `0` and open orders `0`;
- `COMPLETED.json` SHA-256
  `cb04e253dacffeb96ec03bfcea098abb4df89d9dca43aa5f9f7fa12978f06b08`;
- formal completion manifest SHA-256
  `aeefd22b2a2efa8793894cdfa9c1f9755df738a0f67aceca960a6c2fbf61585c`;
- formal decision SHA-256
  `8897e06a53a5a534f96e810519d619f9e75aa5b21a1019610c6ea6ee1b8dc3d6`;
- `RAW_COMPLETED.json` SHA-256
  `12b85adc0b32854c556a1f9caa2872bb32486a99d338d48424b054e9f9958ad4`.

Never edit, resume, recover in place, or rerun that package/run. Its terminal
account is safe; its defect is activity-budget shutdown ordering.

## 3. Repair objective and exact flow

For `NORMAL_CREATE_BUDGET_EXHAUSTED` and `FORMAL_DEADLINE_REACHED`, require:

1. stop all normal placement and persist the terminal reason;
2. cancel the complete authoritative owned set;
3. reconcile fills, fees, position, balance, cursor, and owned orders;
4. require authoritative position to equal ledger inventory;
5. when nonzero, dispatch the one persisted reduce-only flatten identity;
6. accept one or more deduplicated partial special fills whose sum is exact;
7. reconcile authoritative and ledger position to zero and orders to empty;
8. only then close as `OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT`.

Any unknown order, position mismatch, incomplete fee, overfill, duplicate
conflict, ambiguous unresolved flatten, second flatten create, or non-flat
terminal result closes as safety/reconciliation failure. Never hide it behind
activity insufficient.

## 4. Mandatory offline gates

Use socket-denied fixtures for long and short inventory, already-flat closure,
owned-order cancellation before flatten, position/accounting mismatch, partial
and multi-partial flatten, duplicate/reordered special fills, overfill, delayed
position update, ambiguous response, single-flight persistence, and reporting
after safe terminal reconciliation.

Carry the completed fill-cursor repair gates read-only. Run the targeted
shutdown suite, all successor-applicable root non-Optuna tests with exact
generation-locked exclusions recorded, and the permitted backtest non-Optuna
suite. Every failed/unknown fixture proves zero new submissions.

Create fresh non-overwriting evidence with predecessor/source hashes, test and
network audits, endpoint/mutation audit, secret scan, decision, completion
manifest, and terminal offline marker written last.

## 5. Fresh boundary

Never reuse any predecessor package/run/session/checkpoint/token or artifact
directory. Offline support authorizes no OKX request. A future read-only Demo
preflight requires new preparation/run/session/arm identifiers and explicit
authorization. A passing preflight authorizes no orders. Any future formal run
requires another fresh package/run/session/token and explicit authorization.

## 6. Reporting

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

Even successor support does not authorize Live production.
