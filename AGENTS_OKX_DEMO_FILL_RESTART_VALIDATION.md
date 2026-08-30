# AGENTS.md - OKX Demo Fill and Restart Validation

## 1. Activation and authority

This protocol is active only when these exact contents are installed as the
root `AGENTS.md` by explicit user instruction. The user authorized this
successor on 2026-08-03. Preserve an identical copy as
`AGENTS_OKX_DEMO_FILL_RESTART_VALIDATION.md`.

When active, it governs the whole project folder and authorizes implementation,
offline testing, read-only OKX Demo preflight, and one separately armed OKX
Demo fill-and-restart validation run. Activation alone does not arm network
access or orders. Preflight and formal execution each require explicit user
instruction and a session-scoped arm token.

It does not authorize Live endpoints, Live credentials, Live orders,
production capital/defaults, Git operations, Optuna, validation, holdout,
market manipulation, self-trading, or mutation/rerun of v1.3-v1.6 or prior
OKX Demo evidence.

## 2. Immutable predecessor evidence

Freeze these read-only inputs:

- status `MM_V1_6_ECONOMIC_AND_STRESS_SUPPORT`;
- profile `FEE_AWARE_SPREAD_6` / `mm-v1-6-profile-02`;
- v1.6 canonical specification SHA-256
  `e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f`;
- prior Demo run `okx-demo-20260802T172200Z` and status
  `OKX_DEMO_EXECUTION_SAFETY_SUPPORT`;
- prior canonical Demo specification SHA-256
  `10063903656d0bde6ea6259134bbe1e19409a1babb10c682ebd644080123508e`;
- prior specification file SHA-256
  `cf0985d36983fbf5776f9becb75103c305380a39cce9adcebfaf6a2d9ff28534`;
- prior completion-manifest SHA-256
  `ef673741c4bacb9aab7bb49175d435e070975040e628a3146831f893869a14e7`;
- prior decision SHA-256
  `ca7658a21b766300f2bda98d54d45bbae004ba33a7d86fa187617ca83ebbe42b`;
- prior `COMPLETED.json` SHA-256
  `288889525e85c3579f5bbec50581b4f124259e73c813fab494b0f6b9d368afa1`.

Verify all covered v1.6 and prior Demo hashes read-only before implementation or
network access. The root authority transition is the only allowed predecessor
source difference. Never edit a v1.6 covered source, any v1.3-v1.6 artifact, or
an existing `artifacts/okx_demo_execution_safety/` directory. Optuna remains
unexecuted.

## 3. Objective

The predecessor proved six post-only acknowledgements and six confirmed
cancellations but had zero fills. Close only that gap by proving with actual
OKX Demo fills:

- owned normal maker-fill classification and deduplication;
- inventory, average entry, gross/net realized PnL, and actual fee accounting;
- safe process restart after a real fill with nonzero position;
- persistent controlled kill-latch restart, blocking, and safe release;
- terminal flat position and zero owned orders after cancellation or a required
  reduce-only flatten.

Profitability is not a gate. Safety and reconciliation override activity.

## 4. Mandatory separation

Use exactly `OFFLINE_FIXTURE`, `OKX_DEMO`, and unavailable/rejected `LIVE`.
Never infer mode from credentials or fall back to Live. Every authenticated
request must prove CCXT sandbox mode and `x-simulated-trading: 1` immediately
before dispatch. Freeze CCXT version/source hash, host, method/path, and
non-secret request fields. Never log headers, signatures, credentials,
passphrases, or raw account IDs.

Credentials must be complete, runtime-injected, Demo-only, non-withdrawal, and
absent from source/state/logs/streams/artifacts. Any transport, identity, or
permission uncertainty halts with zero new orders.

## 5. Frozen runtime binding

Create a fresh non-overwriting hashed manifest binding all predecessor hashes;
the exact profile/fingerprint/overlay/units; `BTC/USDT:USDT` linear USDT swap;
market fingerprint; lot and maximum inventory `0.01 BTC`; isolated net mode;
leverage `3`; capital copy `750 USDT`; margin cap `0.80`; fee, drawdown,
re-entry, exit, age, requote, and staleness semantics; runtime/configuration
hashes; installed CCXT contract; fixture/run/restart IDs; risk budget; and
artifact contract.

Missing, extra, renamed, or unit-mismatched fields fail closed. Never change
the frozen profile, narrow spread, enlarge lot, raise leverage/inventory, or
bypass controls to obtain fills.

## 6. Read-only preflight

Preflight may perform public and authenticated reads only. It must not create,
amend, cancel, or simulate an order; change leverage/position/account mode; or
activate/release a kill state.

Require two consistent snapshots proving Demo transport, complete non-withdraw
credentials, flat position, zero open orders, no unresolved state, net mode,
leverage `3`, complete account/order/trade/fee data, exact market metadata,
healthy clock, fresh monotonic book, sufficient one/two-sided margin capacity,
and reconciled predecessor/current hashes. Failure writes
`orders_submitted: 0` and stops. Passing does not authorize formal orders.

## 7. Conduct and hard risk budget

Before the first order freeze a complete specification within:

- at most 120 minutes wall time and 120 normal creates;
- at most one owned bid and one owned ask;
- absolute position at most `0.01 BTC`;
- capital `750 USDT`, leverage `3`;
- soft guard `22.50 USDT` (3%) and hard kill `37.50 USDT` (5%);
- one unresolved emergency-flatten attempt;
- exact promoted spread, size, rest, requote, age, inventory, and defensive
  behavior;
- frozen observation tick and order-rate ceiling.

Every normal price/size comes from the promoted engine and requires post-only
acknowledgement. Never cross, use artificial fill-seeking prices, a second
account, self-trade, layer, spoof, wash trade, or generate evidence volume.
No organic fill is a valid `ACTIVITY_INSUFFICIENT` result and cannot be repaired
by profile changes or another formal matrix.

After a fill, stop exposure, cancel the authoritative owned set, and reconcile
before restart or any placement.

## 8. Fill, state, and accounting safety

Retain all predecessor controls and require write-ahead generation/client ID
before create; ambiguous-create resolution with no automatic retry;
authoritative cancel before replace/restart; exact mapping of every trade to an
owned normal or special reduce-only order; maker proof for normal fills;
paginated monotonic fill cursor with stable same-timestamp deduplication; exact
partial/late fill and trade/position ordering reconciliation; and atomic
persistence of orders, inventory, average entry, gross PnL, actual fees, net
PnL, equity, drawdown, cursor, kill, flatten, and restart state.

Authoritative position is final but never hides an unexplained accounting
mismatch. Every incomplete/unknown snapshot proves zero submissions. Special
fills remain excluded from normal fills, FIFO round trips, and economics.

## 9. Mandatory restart checkpoints

The formal run may span processes but remains one run ID and one execution.
Resume is legal only after a durable `RESTART_REQUIRED` checkpoint.

### R1 - after actual fill

On the first owned normal maker fill leaving nonzero position: stop placement;
confirm cancellation of all owned orders; reconcile trade/fee/position/balance;
persist a hash-chained handoff containing run/process generation, cursor,
position, entry, PnL, fees, and source/spec hashes; exit with
`RESTART_REQUIRED_AFTER_FILL`; resume the same run using a checkpoint-bound
token; load market metadata before state; and prove two authoritative exact
snapshots before quoting.

If both sides fill before an observable nonzero checkpoint, do not manufacture
another fill; restart evidence fails.

### R2 - kill-latch restart

After a normal maker round trip while flat/empty: persist a dedicated validation
kill latch and activation ID; exit with
`RESTART_REQUIRED_AFTER_KILL_LATCH`; resume the same run; prove the latch
blocks all submissions; take two flat/empty snapshots; release only with an
explicit checkpoint acknowledgement matching the ID; and persist release.

Missing, reused, stale, corrupt, wrong-run, or wrong-generation resume tokens
halt. A new run ID or second execution marker is not a resume.

## 10. Market data and emergency safety

Stale, empty, crossed, delayed, duplicate, future-dated, or non-monotonic data
cancels owned orders and halts. Cover WebSocket disconnect/reorder/duplicate
and REST failure offline. REST fallback preserves the original timestamp.

At deadline/failure, cancel authoritative owned orders and reconcile. If
nonzero, activate kill and use the persisted single-flight reduce-only market
flatten, submitting once unless absence is proven under the frozen policy.
Poll to a bounded authoritative result. Fatal errors attempt cancellation and
persistence and return non-success. Never complete with owned orders, position
beyond one base step, unknown flatten, or unresolved checkpoint.

## 11. Required offline fixtures

Test every predecessor fixture plus paginated/same-timestamp/reordered fills;
partial/late fill and both trade-position arrival orders; maker/taker or fee
mismatch; crash before create and after accepted create; restart before/after
partial/full/offset fill with long and short positions; checkpoint with live,
foreign, or unknown orders; missing/corrupt/stale/reused/wrong resume tokens;
source/spec/market/account/profile drift; kill persistence/block/bad ack/release;
flat/partial deadline and ambiguous/partial/delayed flatten; supervisor crash
after execution marker; artifact failure, truncation, hash-chain break; and
report recovery without another order run.

Every unknown or failed fixture proves zero new submissions.

## 12. Formal gates

Require all offline and non-Optuna regressions; zero-mutation preflight; exactly
one execution marker and no run-ID reuse; at least one actual owned bid maker
fill, one actual owned ask maker fill, and one normal FIFO maker round trip; R1
nonzero restart; R2 kill restart; exact order/fill/fee/position/balance/
accounting reconciliation; zero duplicate/foreign/unknown/unowned/stale/
overfill events; zero ambiguous retries or orders during unknown state; final
flat within one base step and zero owned orders; actual fees/net economics;
all hashes reconciled; and zero secret leakage, Live attempts/orders, or real
capital.

Missing organic fills or either restart checkpoint means activity insufficient.

## 13. Closed status and next boundary

Use exactly one:

- `OKX_DEMO_FILL_RESTART_EVIDENCE_FAILED`
- `OKX_DEMO_FILL_RESTART_RUNTIME_BINDING_FAILED`
- `OKX_DEMO_FILL_RESTART_RECONCILIATION_FAILED`
- `OKX_DEMO_FILL_RESTART_SAFETY_FAILED`
- `OKX_DEMO_FILL_RESTART_ACTIVITY_INSUFFICIENT`
- `OKX_DEMO_FILL_RESTART_SUPPORT`

Write `COMPLETED.json` last. Always report
`production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`,
`optuna_executed: false`, `validation_opened: false`,
`holdout_opened: false`, and `git_write_operation: false`.

Even support does not authorize Live. Live needs another explicit protocol with
real-money loss budget, rollback, monitoring/alert ownership, credential
separation, timebox, and explicit Live authorization.

## 14. Required outputs

Create fresh non-overwriting artifacts under
`artifacts/okx_demo_fill_restart_validation/`: predecessor hash/transition
audit; initial/final readiness; zero-mutation preflight; frozen promotion,
runtime, CCXT, endpoint, source, fixture, run, restart, risk, and artifact
manifests; append-only hash-chained market/order/trade/fill/position/balance/
fee/accounting/defensive/safety/supervisor streams; R1/R2 handoff/resume/
generation/reconciliation audits; classification/FIFO/staleness/cancel/restart/
kill/flatten audits; raw result and recovery inputs; secret scan, endpoint
audit, test counts, final JSON/Markdown decision, completion hashes, and
`COMPLETED.json`.

Never overwrite prior artifacts to repair reporting. Recover only from durable
streams. After the first formal marker, never run another Demo order matrix
under this protocol.

