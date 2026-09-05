# CODEX EXECUTION BRIEF — Terminal Work-off & Staged Validation Pipeline

## 0. Authority and precedence

This document is an execution brief for the `tordave1150/mm-bot-okx` repository.

Before making any change, read the repository's current `AGENTS.md` and follow it as the highest-priority repo-local operating rule. If this document conflicts with `AGENTS.md`, stop the conflicting action and follow `AGENTS.md`.

This task is intended for the current **offline repair phase only** unless a later, explicit authorization changes the active phase.

### Hard prohibitions for this task

Do **not**:

- connect to OKX or any external trading endpoint;
- read or validate API credentials, secrets, passphrases, or environment variables containing credentials;
- execute OKX Demo R1/R2 traffic;
- create, amend, cancel, or flatten any real or demo exchange order;
- change account configuration, leverage, margin mode, or position mode;
- run production or legacy live entry points such as `main.py` unless the current `AGENTS.md` explicitly authorizes it;
- run Optuna, validation, holdout, or any research path disabled by the current repository policy;
- weaken reconciliation, accounting, deterministic order identity, kill-switch, stale-book, clock-skew, owned-order, or mutation-safety controls;
- perform Git write operations, commits, pushes, branch creation, or tag creation unless separately authorized by the current `AGENTS.md` and the user.

All testing must remain deterministic/offline and must preserve the repository's socket-denied / zero-network-attempt safety posture.

---

# 1. Problem statement

The current development loop is too expensive because strategy/runtime changes are repeatedly evaluated through a full 12-session campaign before the developer receives useful feedback.

The full 12-session run must stop being the primary debugging loop.

The new architecture must separate:

1. **execution safety**, which is non-negotiable;
2. **economic health**, which evaluates the quality of the strategy/runtime behavior;
3. **evidence sufficiency**, which determines whether enough observations exist to qualify a frozen candidate.

The 12-session campaign must become a **qualification / graduation test for a frozen candidate**, not an iterative development test.

A second objective is to improve **terminal inventory work-off** so the bot relies less on terminal taker-style special flatten while preserving the existing risk boundary.

---

# 2. Current working evidence

The user reports that the strongest local historical offline campaign is:

`economic-package-20260831T140616Z`

with approximately:

- safe sessions: `12 / 12`;
- unsafe sessions: `0`;
- maker fills: `112`;
- bid maker fills: `53`;
- ask maker fills: `59`;
- FIFO maker round trips: `60`;
- normal maker net PnL: approximately `+16.56 USDT`;
- special flatten economic impact: approximately `-3.14 USDT`;
- aggregate net PnL: approximately `+13.42 USDT`;
- special flatten used in `5 / 12` sessions.

Treat these numbers as **working context only** until the package is found and verified locally. Do not fabricate or recreate evidence if the package is absent.

The main remaining concern is no longer basic maker activity. The important unresolved issue is the frequency/cost of terminal inventory cleanup and the cost of the long qualification loop.

---

# 3. Primary execution goals

Implement the following without weakening existing safety behavior.

## Goal A — Staged validation pipeline

Create or refactor the offline validation flow into five tiers:

### Tier 0 — Unit tests

Fast deterministic tests for individual work-off, accounting, and gate decisions.

Expected runtime: very short.

Examples:

- long inventory suppresses exposure-increasing bid when work-off mode requires it;
- short inventory suppresses exposure-increasing ask when work-off mode requires it;
- zero inventory does not trigger work-off unnecessarily;
- terminal mode never increases absolute inventory;
- safety failures remain hard failures;
- deterministic order identity remains unchanged for identical intents.

### Tier 1 — Deterministic scenario tests

Build focused offline scenarios covering at minimum:

1. normal two-sided making with no inventory;
2. one bid maker fill followed by successful ask maker work-off;
3. one ask maker fill followed by successful bid maker work-off;
4. partial work-off before terminal time;
5. persistent inventory approaching terminal time;
6. no-fill session;
7. volatile / adverse-selection-like fixture;
8. cancel/fill race or equivalent deterministic reconciliation fixture if supported by the current harness;
9. emergency terminal flatten path;
10. zero-network / socket-denied proof.

### Tier 2 — One-session offline canary

Run a single representative offline session after Tier 0 and Tier 1 pass.

Purpose:

- validate complete session lifecycle;
- validate accounting closure;
- validate state transitions;
- confirm zero unknown events;
- confirm terminal position/open-order cleanup;
- confirm no network/credential/live mutation attempts.

Do **not** use this tier for statistical qualification.

### Tier 3 — Three-session economic batch

Run exactly three representative offline sessions after the one-session canary passes.

Purpose:

- identify obvious economic regressions early;
- inspect maker fills, FIFO work-off, terminal cleanup behavior, fees/PnL, and session-level variance;
- reject a bad candidate before spending time on a 12-session qualification.

### Tier 4 — Twelve-session offline qualification

Run the 12-session campaign only after:

- Tier 0 passes;
- Tier 1 passes;
- Tier 2 passes;
- Tier 3 passes;
- the candidate configuration and implementation are frozen for qualification.

Do not use Tier 4 as the normal debugging loop.

If Tier 4 fails, first classify the failure as one of:

- `BUG`;
- `STRATEGY_DEFECT`;
- `SAFETY_DEFECT`;
- `SAMPLE_VARIANCE`;
- `MARKET_REGIME_EFFECT`;
- `GATE_DESIGN_PROBLEM`;
- `INSUFFICIENT_EVIDENCE`.

Do not immediately change strategy code solely to make a threshold pass.

---

# 4. Split the current monolithic READY decision

Refactor qualification reporting so that safety, economics, and evidence are evaluated independently.

## 4.1 Safety gate — hard, non-negotiable

Safety must remain fail-closed.

Any of the following must prevent qualification:

- unsafe session;
- unknown or unclassified fill affecting accounting;
- unreconciled owned order;
- foreign/unowned order where current safety rules prohibit it;
- unexpected duplicate side/order;
- terminal position outside the existing flat tolerance;
- terminal owned open order count not equal to zero;
- accounting mismatch;
- unresolved mutation ambiguity;
- kill-switch invariant violation;
- stale/future/non-monotonic market-data violation not handled by the existing fail-closed behavior;
- network/credential/live endpoint attempt during offline validation.

Suggested output enum:

- `SAFETY_PASS`
- `SAFETY_FAIL`

Do not convert a safety failure into a warning.

## 4.2 Economic health — graded

Economic quality should not be represented as a binary safety decision.

Suggested output:

- `HEALTHY`
- `DEGRADED`
- `UNHEALTHY`

Evaluate using existing metrics plus the new terminal-work-off metrics defined below.

## 4.3 Evidence sufficiency — separate state

Suggested output:

- `SUFFICIENT_EVIDENCE`
- `MORE_EVIDENCE_REQUIRED`

A lack of enough fills/sessions/round trips should not be mislabeled as a safety failure.

## 4.4 Overall offline state

Use a state model similar to:

1. `UNSAFE`
2. `SAFE_OFFLINE`
3. `ECONOMICALLY_PROMISING`
4. `OFFLINE_QUALIFICATION_CANDIDATE`
5. `OFFLINE_QUALIFICATION_PASSED`
6. `MORE_EVIDENCE_REQUIRED`

Passing offline qualification must **not** automatically authorize OKX Demo or production.

If useful, emit a separate informational status:

`R1_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION`

This is descriptive only and must never trigger network access.

---

# 5. Terminal work-off redesign

Do not change the core Avellaneda–Stoikov strategy solely to solve the terminal cleanup problem.

Freeze the current strategy core unless a failing deterministic test proves a core defect.

In particular, do not casually modify:

- risk-aversion gamma;
- arrival-decay proxy;
- EWMA volatility logic;
- minimum/maximum half spread;
- inventory cap;
- fixed lot size;
- leverage boundary;
- margin mode;
- maker/taker fee assumptions;
- hard/soft drawdown boundary;
- the existing ten tunable strategy controls in `MarketMakerV1Config`.

The primary change area is **terminal inventory management / session lifecycle behavior**.

## 5.1 Required state model

Implement or make explicit the following conceptual work-off stages:

### `NORMAL_MAKING`

Normal two-sided maker operation under the existing strategy and inventory controls.

### `PASSIVE_WORK_OFF`

Entered when non-zero inventory exists and the session has reached a state where inventory should begin to be reduced before the emergency terminal window.

Requirements:

- do not increase absolute inventory unnecessarily;
- reduce or suppress the exposure-increasing side as appropriate;
- preserve maker/post-only behavior;
- prioritize the inventory-reducing side.

### `AGGRESSIVE_MAKER_WORK_OFF`

Used when inventory remains non-zero and the probability of passive maker work-off before terminal time is becoming insufficient.

Requirements:

- remain maker/post-only;
- move the inventory-reducing quote toward the best maker-eligible price, subject to current tick and safety constraints;
- do not open new exposure on the wrong side;
- never exceed existing inventory/risk limits;
- preserve owned-order and cancel-before-replace safety.

### `EMERGENCY_FLATTEN`

Last-resort terminal cleanup only.

Requirements:

- preserve the current single-flight/reduce-only/safety semantics;
- do not expand its risk authority;
- record an explicit reason code;
- keep it distinguishable from routine maker work-off.

## 5.2 State-aware transition requirement

Do not solve the problem only by hard-coding “start work-off 5–10 minutes earlier.”

Transitions should use the information already available in the runtime where practical, such as:

- remaining session time;
- current signed inventory;
- absolute inventory utilization;
- recent opposite-side maker fill activity or an existing causal fill proxy;
- current quote/order state;
- current volatility/risk state;
- whether the bot is already in a defensive or terminal phase.

Avoid adding unnecessary new strategy optimization parameters.

If a new policy constant is genuinely required, place it in the lifecycle/work-off policy layer rather than expanding the ten frozen strategy tunables, document why it is needed, and test its invariants.

---

# 6. Special flatten classification

Do not count every terminal flatten event as if it has the same meaning.

Add explicit reason classification if the current event model does not already support it.

At minimum distinguish:

- `ROUTINE_TERMINAL_CLEANUP`
- `RISK_EMERGENCY_FLATTEN`

If the existing architecture already has more precise reason codes, reuse them rather than creating redundant enums.

The purpose is observability, not weakening the requirement that every session end flat within the current safety boundary.

---

# 7. New economic/work-off metrics

Add the following metrics to offline campaign/session evidence where feasible.

## Required

### 7.1 Flatten session rate

`flatten_sessions / total_sessions`

### 7.2 Flatten cost ratio

Use a clearly documented denominator.

Preferred form when normal maker PnL is positive:

`abs(special_flatten_net_pnl) / normal_maker_net_pnl`

If the denominator is non-positive, emit `null/not_applicable` rather than a misleading percentage.

### 7.3 Terminal inventory before cleanup

Report at least:

- mean absolute terminal inventory before terminal cleanup;
- max absolute terminal inventory before terminal cleanup.

If enough samples exist, also report p50/p90/p95.

### 7.4 Maker work-off success rate

Define and document a deterministic numerator and denominator.

Preferred concept:

`inventory episodes flattened through maker work-off before emergency flatten / all eligible non-zero inventory episodes`

Do not invent this metric without a reproducible episode definition.

### 7.5 Time-to-flat

For inventory episodes, report time from inventory acquisition to return to flat where data exists.

At minimum:

- count;
- median / p50;
- p90 where sample size supports it.

### 7.6 Emergency versus routine cleanup count

Separate counts for the reason codes above.

## Preserve existing metrics

Continue reporting:

- normal maker fills;
- bid/ask maker fills;
- FIFO maker round trips;
- normal maker PnL;
- special-close/flatten PnL;
- aggregate net PnL;
- fees;
- final position;
- final owned open orders;
- safety violations;
- network/credential/live endpoint attempts.

---

# 8. Qualification philosophy

Do not optimize merely to cross brittle thresholds.

A rule such as:

`special_flatten_sessions <= 2 / 12`

should remain visible if it is part of the current protocol, but economic reporting must also expose **severity**, not only frequency.

For example, one very small routine terminal cleanup and one large emergency flatten must not be treated as economically equivalent simply because both increment the session-level flatten count.

Do **not** silently change current protocol thresholds in this task unless:

1. the threshold is demonstrably implemented incorrectly; or
2. the user separately authorizes a protocol redesign.

This task may add richer metrics and classification around the existing threshold without changing its value.

---

# 9. Candidate freeze rule

Before Tier 4 begins, create a deterministic candidate identity from the current permitted configuration/runtime inputs.

Reuse existing repository fingerprinting/evidence mechanisms where possible.

The Tier 4 evidence must record enough information to prove that the candidate evaluated in session 1 is the same candidate evaluated in session 12.

If strategy/runtime behavior changes during Tier 4, invalidate the qualification and restart only after the candidate is frozen again.

Do not use Git commits as the only candidate identity because Git writes are not authorized by this task.

---

# 10. Preferred implementation areas

Inspect the current code before editing. Prefer minimal changes in the canonical successor stack.

Likely relevant areas include:

- `market_maker/`
- `okx_demo_runtime.py`
- current offline economic session controller modules;
- current multi-session campaign modules;
- FIFO attribution/work-off modules;
- defensive overlay / terminal lifecycle modules;
- evidence/reporting modules;
- `tests/test_okx_*.py`;
- permitted `backtest/` offline tests.

Avoid modifying the legacy runtime unless a regression guard requires it.

Do not route the successor strategy through `main.py` / `trading_bot.py` as part of this task.

---

# 11. Required execution order

Codex must follow this order.

## Step 1 — Inspect and map

Identify:

- the exact current 12-session campaign entry point;
- the exact session controller;
- where terminal work-off begins;
- where special flatten is requested;
- how fill attribution/FIFO work-off is calculated;
- how final READY/NOT_READY is computed;
- how evidence packages are written;
- existing tests that enforce these behaviors.

Produce a short internal change map before editing.

## Step 2 — Add/repair observability first

Before changing behavior, make sure the harness can report:

- work-off stage transitions;
- terminal inventory before cleanup;
- cleanup reason;
- maker work-off success/failure;
- flatten cost ratio.

Do not alter economic behavior merely to create these metrics.

## Step 3 — Split safety/economic/evidence gates

Refactor reporting/decision logic while preserving existing hard safety failures.

Add regression tests proving that a safety failure can never be converted into `MORE_EVIDENCE_REQUIRED` or an economic warning.

## Step 4 — Implement staged validation

Add the Tier 0–Tier 4 flow using existing runners where practical.

Avoid duplicating an entire campaign framework if the current one can accept session-count/tier parameters safely.

The default developer loop should stop after Tier 3 unless explicitly asked to perform Tier 4 qualification.

## Step 5 — Implement terminal work-off state behavior

Implement the smallest state-aware work-off change that can reduce terminal cleanup dependence without weakening safety.

Add deterministic invariant tests before judging economics.

## Step 6 — Run Tier 0 and Tier 1

All relevant tests must pass.

If they fail, fix the defect before moving forward.

## Step 7 — Run Tier 2

Run exactly one offline canary session.

Stop if any safety/accounting invariant fails.

## Step 8 — Run Tier 3

Run the three-session offline economic batch.

Compare with the current baseline/reference behavior using the new metrics.

Do not run Tier 4 if Tier 3 shows an obvious safety or economic regression.

## Step 9 — Freeze candidate

Record the candidate fingerprint and relevant fixed runtime/work-off policy values.

## Step 10 — Run Tier 4 once

Run the 12-session offline qualification only after the candidate is frozen.

If it fails, classify the failure before making any new modification.

---

# 12. Acceptance criteria

This task is complete only when all applicable criteria below are satisfied.

## Safety / integrity

- existing safety boundaries are unchanged or stronger;
- zero OKX/live/demo network attempts during this task;
- zero credential reads;
- zero live/demo order mutations;
- deterministic mutation retry policy remains unchanged;
- owned-order reconciliation remains fail-closed;
- terminal position is flat within the existing tolerance in all completed offline canary/batch/qualification sessions;
- terminal owned open order count is zero;
- no unresolved accounting ambiguity.

## Architecture

- safety decision is separate from economic health;
- evidence sufficiency is separate from both;
- 12-session qualification is no longer required as the default development loop;
- one-session and three-session stages are independently executable;
- candidate identity/fingerprint is captured before 12-session qualification.

## Work-off behavior

- explicit work-off stages exist in code or are otherwise deterministically represented;
- exposure-increasing quoting is suppressed when the active work-off state requires it;
- aggressive work-off remains maker/post-only;
- emergency flatten remains last-resort and reduce-only/single-flight under existing safety rules;
- cleanup reason is observable.

## Evidence

The final offline report must include at least:

- Tier 0 test result;
- Tier 1 scenario result;
- Tier 2 one-session result;
- Tier 3 three-session result;
- Tier 4 result if executed;
- maker fills by side;
- FIFO round trips;
- normal maker PnL;
- special flatten PnL;
- aggregate PnL;
- flatten session rate;
- flatten cost ratio;
- terminal inventory statistics;
- maker work-off success rate if deterministically definable;
- time-to-flat statistics if deterministically definable;
- routine cleanup count;
- emergency flatten count;
- safety violations;
- final position/open orders;
- network attempts;
- credential reads;
- live/demo mutation attempts;
- candidate fingerprint.

---

# 13. Regression protection

Add regression tests that specifically prevent future developers from undoing the intent of this repair.

At minimum protect against:

1. a developer changing a safety failure into a warning;
2. a developer making 12-session execution the default test path again;
3. work-off mode increasing absolute inventory;
4. aggressive work-off using a non-maker order before emergency flatten;
5. replacing an owned order before confirmed cancellation under the existing safety contract;
6. loss of cleanup reason classification;
7. loss of candidate fingerprint from qualification evidence;
8. accidental network/credential access in offline tiers.

---

# 14. Out of scope

Do not perform the following in this task:

- OKX Demo connection;
- R1 preflight execution;
- R2 economic campaign execution;
- production trading;
- production-readiness declaration;
- live API integration changes unrelated to offline work-off behavior;
- Optuna parameter search;
- strategy retuning merely to improve a threshold;
- leverage or lot-size changes;
- inventory-cap increase;
- fee assumptions changed to make PnL look better;
- deletion of historical evidence;
- migration of the legacy stack;
- broad repository cleanup unrelated to this repair.

---

# 15. Final Codex report format

When implementation is finished, return a concise execution report with these sections:

## A. Files changed

For each file:

- purpose of change;
- important behavior changed;
- important behavior intentionally preserved.

## B. Validation tiers

Report:

- Tier 0: PASS/FAIL and test count;
- Tier 1: PASS/FAIL and scenario count;
- Tier 2: PASS/FAIL and session result;
- Tier 3: PASS/FAIL and 3-session aggregate;
- Tier 4: PASS/FAIL/NOT_RUN and reason.

## C. Safety proof

Explicitly report:

- network attempts;
- credential reads;
- demo endpoint attempts;
- live endpoint attempts;
- create/amend/cancel/flatten exchange mutations;
- unresolved owned orders;
- final position;
- final owned orders.

All should remain zero/flat as applicable for the offline task.

## D. Economic/work-off result

Report:

- maker fills bid/ask/total;
- FIFO round trips;
- normal maker PnL;
- special flatten PnL;
- aggregate PnL;
- flatten session rate;
- flatten cost ratio;
- terminal inventory mean/max/p95 if available;
- maker work-off success rate;
- time-to-flat p50/p90 if available;
- routine cleanup count;
- emergency flatten count.

## E. Decision

Choose one:

- `UNSAFE`
- `SAFE_OFFLINE`
- `ECONOMICALLY_PROMISING`
- `OFFLINE_QUALIFICATION_CANDIDATE`
- `OFFLINE_QUALIFICATION_PASSED`
- `MORE_EVIDENCE_REQUIRED`

If the result would otherwise suggest R1, report only:

`R1_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION`

Do not start R1 automatically.

## F. Remaining concerns

List only unresolved technical/economic issues supported by evidence.

---

# 16. Core principle

The purpose of this repair is **not to make the dashboard say READY**.

The purpose is to create a shorter, safer, more informative development loop while preserving the existing execution-safety boundary and reducing unnecessary terminal flatten dependence.

The desired workflow is:

```text
Code change
    ↓
Tier 0 — Unit
    ↓
Tier 1 — Deterministic scenarios
    ↓
Tier 2 — 1-session offline canary
    ↓
Tier 3 — 3-session economic batch
    ↓
Freeze candidate
    ↓
Tier 4 — 12-session offline qualification
    ↓
Offline qualification result
    ↓
STOP — wait for explicit authorization before any OKX Demo phase
```

**12 sessions are the graduation exam, not the debugging loop.**