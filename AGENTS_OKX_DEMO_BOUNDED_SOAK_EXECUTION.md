# AGENTS.md — OKX Demo Bounded Soak Execution Preparation

## 1. Activation and authority

This successor protocol is active only when these exact contents are installed
as root `AGENTS.md`. Preserve this source copy byte-for-byte.

When active, it authorizes implementation and socket-denied testing of a
source-bound bounded-soak executor, fresh non-overwriting offline evidence, and
creation of one fresh executable soak package/run/session/token entirely
offline. It authorizes no OKX request, credential access, order, preflight,
formal run, soak execution, execution marker, account-configuration change, or
Live action.

Executing the fresh soak package requires separate exact user authorization
quoting its package, run, session, and arm token. Live endpoints, production,
Git, Optuna, validation, holdout, self-trade, and external endpoints remain
prohibited.

## 2. Immutable predecessors

Freeze successful formal package `formal-package-20260810T123953Z` exactly as
bound by the prior offline evidence. Never edit, resume, or rerun it.

Freeze `soak-package-20260810T161759Z` as preparation-only and never execute,
edit, arm, or reuse it. Its fixed hashes are:

- `SOAK_PACKAGE_COMPLETED.json`:
  `11d7bc47ced19caaa033936f30b5b2d5ed51870d2fa757b90bf054a96763feb7`;
- `completion_hashes.json`:
  `56f6a4cc97f9665b34a9aa22f9fcc011af61689551aaddef5a6d93520a85c4b9`;
- `specification/soak_package_spec.json`:
  `da56a237e5073332f3caaee0c6688ee3d09c5431150e842ccac9f8872300306a`;
- `run/soak_run_manifest.json`:
  `ffb006aed256480e277489ba7c39ec33f11999cbd96b8cefff9d394966fdbe01`.

The fresh executable package must bind the passing preflight
`preflight-20260810T131800Z` and offline evidence
`soak-failure-offline-20260810T130849Z` without mutating them.

## 3. Executor objective

Implement a deterministic source-bound executor with describe/start commands.
Package loading must verify completion hashes, source hashes, specification,
identity, preflight/evidence bindings, and zero prior execution markers.

Start must require the exact session arm token, create exactly one durable
execution marker before any authenticated transport, acquire a single-instance
lease, and prove two fresh flat/empty Demo account snapshots before placement.
It must reject Live transport, withdrawal permission, account drift, foreign
orders, stale or invalid books, source drift, token mismatch, or package reuse.

Normal orders are post-only, fixed-lot, write-ahead, and owned by deterministic
client IDs. At most one bid and one ask may be open. Every create is ambiguous
after dispatch until authoritative acknowledgement. Mutation retries are
forbidden. Cancels apply only to owned IDs and require authoritative
confirmation.

The run is bounded to 30 minutes and 60 normal creates with maximum inventory
`0.01 BTC`, soft/hard loss guards `22.50 / 37.50 USDT`, and no risk-budget
expansion. Shutdown cancels owned orders and, only if non-flat, submits at most
one single-flight reduce-only flatten supporting multiple partial fills.

Terminal close requires controller, engine, gateway, and two fresh account
snapshots to reconcile flat/empty; persists sanitized streams, mutation audit,
secret scan, decision, completion hashes, and `COMPLETED.json` last. Any
ambiguity or evidence-write failure remains unresolved and fails closed.

## 4. Mandatory offline gates

Use socket denial and blank credentials for package/source verification, bad
token, reused marker, lease collision, pre-arm non-flat/open/foreign state,
write-ahead failure, timeout before/after dispatch, duplicate acknowledgement,
mutation retry refusal, owned-only cancel, stale/future/crossed book, activity
and wall deadline, soft/hard guard, single-flight multi-partial flatten,
terminal snapshot mismatch, evidence-write failure, secret scan, and successful
flat completion.

Run targeted executor tests and successor-applicable non-Optuna regressions.
Create a fresh executable package only after all gates pass. Package freeze must
have zero network, credential, order, preflight, formal, soak, Live, account
mutation, Optuna, validation, holdout, and Git activity.

## 5. Boundary and reporting

Stop after reporting the fresh executable package/run/session/token. Do not
connect OKX or start the soak without separate exact authorization.

Always report `production_authorized: false`, `live_mode_available: false`,
`live_endpoint_attempts: 0`, `live_orders: 0`, `optuna_executed: false`,
`validation_opened: false`, `holdout_opened: false`, and
`git_write_operation: false`.

This protocol does not authorize Live production.

