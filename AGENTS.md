# AGENTS.md — OKX Demo Economic Sample-Efficiency and Maker Work-Off Repair

## 1. Activation boundary

This successor source is inactive unless these exact contents are installed as
root `AGENTS.md` after separate exact user authorization. Creating, inspecting,
testing, or hashing this source does not activate it. Preserve this source copy
byte-for-byte and never replace root `AGENTS.md` implicitly.

Before activation, only project-local offline analysis, implementation,
deterministic fixtures, socket-denied tests, audits, and fresh non-overwriting
offline evidence are allowed. Do not connect to OKX, read credentials, prepare
or execute preflight, create/amend/cancel/flatten an order, change account
configuration, or create an external execution marker.

After activation, phase boundaries remain independent and fail closed:

- R0 `OFFLINE_REPAIR`: implementation, fixtures, audits, and offline evidence;
- R1 `READ_ONLY_DEMO_PREFLIGHT`: fresh identities and exact authorization;
- R2 `SUCCESSOR_ECONOMIC_CAMPAIGN`: passing R1 evidence, fresh identities, and
  separate exact authorization;
- R3 `OPERATIONAL_FAILURE_INJECTION`: outside R2 and requires its own fresh
  identities and exact authorization;
- production and production read-only shadow remain unauthorized.

No approval crosses phases or authorizes identifier reuse. Live endpoints,
Live credentials or orders, withdrawals, leverage/account changes, self-trade,
mutation retries, Git operations, Optuna, validation, and holdout are prohibited.

## 2. Immutable predecessor

Freeze A2 package/campaign/run:

- package `economic-package-20260818T125221Z`;
- campaign `economic-campaign-20260818T125221Z`;
- run `economic-campaign-run-20260818T125221Z`;
- terminal decision `INSUFFICIENT_EVIDENCE`;
- `A2_CAMPAIGN_COMPLETED.json` SHA-256
  `6e05efab617f59893768bbc1a144ce652a888f017c26eaf7322a309f151ade7e`;
- `completion_hashes.json` SHA-256
  `3419e9f6b934d53a5981fd5be88c21489ae64da0bc834918ca2a404f3e2476b7`;
- `campaign_decision.json` SHA-256
  `7172cc37c322ac9e57666d10a8abd5a5d0fe1b031da4d5ae69742e2c9f834713`;
- `campaign_registry.jsonl` SHA-256
  `648882ce831666ef28867bc9792f6c06203b1bd2ca720a541ce03b529d7d21ef`;
- registry tail
  `90916abd153f7d31096b559ce871656ad1d8eab98fe3033c90115414dd7ff138`.

The predecessor completed 12 safe terminal sessions with position/open orders
`0 / 0`, Live attempts/orders `0 / 0`, normal creates/acks `599 / 599`, normal
maker fills `15` (`7` bid, `8` ask), FIFO maker round trips `5`, normal net PnL
`+0.3052058 USDT`, and special flatten in `5 / 12` sessions. Aggregate net PnL
including special economics was `-2.044501705 USDT`.

Never edit, rerun, extend, resume, substitute a session, recover in place, or
reuse any identity from this predecessor.

## 3. Repair goal

Increase safe maker sample efficiency and FIFO maker work-off evidence without
weakening accounting, causal re-entry, or risk boundaries. The repair must
address all observed deficits:

- normal fill deficit `9` against the floor of `24`;
- bid fill deficit `1` against the floor of `8`;
- FIFO maker round-trip deficit `3` against the floor of `8`;
- special-flatten excess `3` because at most `2 / 12` sessions may flatten;
- four no-fill sessions and five one-fill terminal-flatten sessions.

Normal PnL was positive and fill balance passed. Do not widen risk or optimize
special-flatten PnL. Special economics remain excluded from normal ranking.

## 4. Frozen risk boundary

Do not increase any predecessor economic risk:

- instrument `BTC/USDT:USDT`, isolated linear swap, net position mode, `3x`;
- modeled capital `750 USDT`;
- normal lot and absolute inventory cap `0.01 BTC`;
- at most one owned bid and one owned ask, post-only create, owned-only cancel;
- session limits `30 minutes` and `60` normal creates;
- campaign limits `12` sessions, `6 hours`, `720` creates, hard loss `75 USDT`;
- soft/hard session drawdown guards `22.50 / 37.50 USDT`;
- observation interval at least `2000 ms`, book age at most `1000 ms`, absolute
  clock skew at most `1500 ms`;
- read retries at most `3`, mutation retries exactly `0`;
- at most one unresolved single-flight reduce-only flatten.

## 5. Mandatory R0 offline work

Implement and test offline:

- immutable post-campaign cohort diagnostics for no-fill, productive FIFO, and
  special-flatten sessions;
- exact fill-per-create, cancel-per-fill, FIFO conversion, fill-side, fee,
  normal/special PnL, drawdown, and quote-mode reconciliation;
- deterministic balanced-quote activity fixtures that improve maker sample
  opportunity without changing the frozen create or inventory budgets;
- deterministic one-sided defense fixtures that preserve causal maker re-entry
  and work-off, never count immediate taker flatten as causal re-entry, and
  reduce dependence on terminal flatten;
- exact admission/work-off reserve accounting and zero unclassified quote-mode
  ticks on every continuation path;
- fail-closed stale/crossed/future/empty book, pending intent, order
  disappearance, counter regression, fill conflict, restart, multi-partial
  flatten, state-store, lease, and terminal evidence fixtures;
- successor gate projections that never relax the existing economic floors.

Run targeted, successor-applicable root non-Optuna, and permitted backtest
non-Optuna tests with blank credentials and socket denial. Rejected fixtures
must prove zero create, amend, cancel, flatten, Live, account mutation,
credential access, validation, holdout, Optuna import, and Git write.

Fresh R0 evidence must include predecessor/source hashes, the diagnostic,
frozen risk specification, requirement/scenario matrices, test/socket audits,
endpoint/mutation audit, secret scan, decision, completion hashes, and a terminal
offline marker written last.

## 6. Promotion boundary

R0 may recommend a fresh read-only R1 preflight only after all offline gates
pass. Preparing or running R1 and preparing or running R2 are separate acts.
Each requires fresh package/run/session/token identities and exact user
authorization. This protocol cannot authorize production.

Always report:

- `production_authorized: false`
- `live_mode_available: false`
- `live_endpoint_attempts: 0`
- `live_orders: 0`
- `optuna_executed: false`
- `validation_opened: false`
- `holdout_opened: false`
- `git_write_operation: false`

