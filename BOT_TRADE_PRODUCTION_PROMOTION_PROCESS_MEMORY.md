# Bot-Trade Production Promotion Process Memory

## Purpose

This file is the durable repository memory for the bot-trade promotion workflow.
It records the current checkpoint, the required sequence to production, and the
authorization handoff that must occur after every completed checkpoint.

This file does not activate a phase, grant authorization, replace `AGENTS.md`,
or permit network, credential, order, Live, production, R1, R2, or R3 activity.
The root `AGENTS.md` remains authoritative.

## Current checkpoint

- Latest campaign package: `economic-package-20260831T140616Z`
- Campaign: `economic-campaign-20260831T140616Z`
- Run: `economic-campaign-run-20260831T140616Z`
- Sessions completed safely: `12 / 12`
- Terminal account evidence: position/open orders `0 / 0`
- Unsafe sessions: `0`
- Mutation retries: `0`
- Live endpoint attempts/orders: `0 / 0`
- Normal fills: `112` (`53` bid, `59` ask)
- FIFO maker round trips: `60`
- Normal net PnL: `+16.5647804936 USDT`
- Special net PnL: `-3.139840900 USDT`
- Aggregate net PnL: `+13.4249395936 USDT`
- Terminal decision: `NOT_READY`

The two current blockers are:

1. `SPECIAL_FLATTEN_RATE`: `5 / 12` sessions (`41.67%`) versus a maximum of
   `20%` (at most two sessions in a twelve-session campaign).
2. `CAUSAL_REENTRY_RECONCILIATION`: `107` causal re-entry records versus `112`
   normal fills.

The matching deficit of five records and five special-flatten sessions is a
strong diagnostic lead, not permission to weaken either gate. The next action
is one bounded R0 offline repair cycle focused on terminal maker work-off,
special-flatten dependence, and exact causal attribution.

## Required path to production

1. **R0 offline repair**
   - Diagnose all five special-flatten sessions and the five-record causal gap.
   - Repair terminal maker work-off and causal attribution without widening any
     frozen risk, inventory, create, time, loss, retry, or endpoint boundary.
   - Add deterministic fixtures and run targeted/root/backtest non-Optuna tests
     with blank credentials and socket denial.
   - Produce fresh non-overwriting R0 evidence and stop.

2. **R1 read-only OKX Demo preflight**
   - First prepare a fresh R1 package offline under separate authorization.
   - Then run it under another exact authorization with fresh identities.
   - Permit only required read-only OKX Demo requests and Demo credentials.
   - Require clock, transport, binding, account, position, and open-order gates.

3. **R2 fresh successor economic campaign**
   - First prepare a fresh R2 package offline under separate authorization.
   - Then arm/run the campaign under another exact authorization.
   - Run fresh sessions only; never reuse an identity.
   - Every session must finish with authoritative `0 / 0`, reconciliation true,
     mutation retries zero, and no fail-closed blocker before the next session.
   - Promotion gates include special flatten at most `2 / 12`, causal records
     equal to normal fills, positive normal net PnL, and all existing sample,
     accounting, risk, and safety gates.

4. **R3 operational failure injection**
   - Requires fresh identities and separate preparation/run authorizations.
   - Exercise restart, network/read failures, stale/future/crossed/empty book,
     order disappearance, partial fills, state-store/lease faults, terminal
     ambiguity, and recovery behavior without relaxing fail-closed controls.

5. **Production read-only shadow**
   - Requires a new protocol/package and exact separate authorization.
   - No order mutations.
   - Compare Live market/account observations, latency, fees, signals, decisions,
     and reconciliation assumptions against the passing Demo evidence.

6. **Limited production canary**
   - Requires a new production-specific risk package and exact authorization.
   - Start at the minimum approved exposure with kill switch, monitoring,
     immutable identities, and authoritative terminal reconciliation.
   - Never inherit Demo authorization or promote automatically.

7. **Staged production**
   - Increase duration or capital only after multiple passing canary windows.
   - Each expansion is separately reviewed and authorized.
   - Any ambiguity returns the system to fail-closed and a fresh lower phase.

## Authorization handoff rule

After completing any checkpoint, the assistant must:

1. Stop before the next phase or materially different action.
2. Report the result, evidence ID, safety counters, and remaining blocker.
3. Provide one exact, copy-ready plain-text authorization request for the next
   permitted checkpoint, including all required fresh IDs and arm token when
   those identities exist.
4. Do not prepare or run the next checkpoint until the user sends that exact
   authorization (or an equivalently precise authorization).
5. After the newly authorized checkpoint completes, repeat this handoff rule
   automatically so the user does not need to ask for the next permission text.

Conditional multi-session authorization is acceptable only when every session
identity is listed exactly and each transition remains gated by authoritative
terminal `0 / 0`, reconciliation true, mutation retries zero, campaign limits,
and absence of a fail-closed condition.

## Repair-cycle stop rule

The current blockers receive one bounded, targeted R0 repair plus one fresh R2
confirmation campaign. If the next fresh R2 campaign again exceeds two special
flatten sessions or fails exact causal reconciliation, stop incremental patching
and propose a work-off/session-ending policy redesign before another campaign.

## Permanent prohibitions unless separately authorized by a future protocol

- Live endpoints or production trading
- Withdrawals or transfers
- Leverage, position-mode, or account-configuration changes
- Mutation retries
- Reused package, run, session, or arm-token identities
- Optuna, validation, or holdout access
- Git write operations

Always report:

- `production_authorized: false`
- `live_mode_available: false`
- `live_endpoint_attempts: 0`
- `live_orders: 0`
- `optuna_executed: false`
- `validation_opened: false`
- `holdout_opened: false`
- `git_write_operation: false`
