# R2 Stage C Sessions 2-3 Implementation Plan

## Scope and predecessor chain

This plan prepares the continuation of the current R2 campaign.  It does not
create a second qualification campaign and it does not reuse any historic Q01-
Q03 identities.

| Field | Bound value |
| --- | --- |
| R0 evidence | `r0-post-q06-campaign-wall-audit-offline-20260905T024551Z` |
| R1 run | `r1-preflight-run-20260905T025038Z` |
| R2 preparation | `r2-package-20260905T025807Z` |
| R2 campaign | `r2-campaign-20260905T025807Z` |
| Final admission package | `r2-final-prep-20260905T043900Z` |
| Canary run | `r2-canary-run-20260905T045300Z` |
| Session 2 | `r2-session-20260905T025807Z-s02:p0:aff7afc4` |
| Session 3 | `r2-session-20260905T025807Z-s03:p0:267ff777` |

The campaign began at `2026-09-05T02:58:07Z`.  Its frozen six-hour deadline is
`2026-09-05T08:58:07Z`.  At the offline audit time of `2026-09-05T03:18:41Z`,
the remaining wall-time was approximately 5 hours 39 minutes.  Execution must
recalculate this from durable timestamps immediately before Session 2 and
again before Session 3; it must not start a session unless its full 30-minute
budget fits before the deadline.

## Problem being corrected

The existing `okx_demo_r2_stage_c_prep.py` and
`okx_demo_r2_stage_c_executor.py` were built for an older Q01-Q03 campaign.
They hard-code predecessor IDs and derive a new qualification campaign.  That
would sever the current R2 package's hash and identity chain, so it is not
eligible to run Sessions 2-3 for this campaign.

## Implementation

1. Add a current-campaign continuation preparation module that reads the R2
   preparation schedule and final-admission package.  It must bind the current
   R0, R1, R2, final-package, and canary IDs exactly and verify every listed
   completion hash before preparing anything.
2. Verify the canary marker is `R2_CANARY_PASSED`, its hard checkpoint passed,
   its terminal state is flat with zero open orders, and its final-admission
   reference is the supplied package.  Reject a mismatch before credential or
   transport setup.
3. Generate a two-session contract using only the pre-existing `s02` and
   `s03` identities and arm tokens from `session_schedule.json`.  The contract
   preserves post-only normal orders, owned-order cancel, single-flight
   reduce-only flatten, zero mutation retries, and every frozen campaign limit.
4. Add a continuation executor that accepts the prepared contract and performs
   a fresh admission gate before each session.  It must verify predecessor
   hashes and identity bindings before loading credentials or constructing the
   exchange.
5. After Session 2, require authoritative durable terminal evidence with
   position/open orders `0/0`, reconciliation true, and mutation retries `0`.
   If any field is missing, ambiguous, or exceeds a frozen limit, stop before
   Session 3.  After Session 3, emit a three-session checkpoint report for the
   Canary plus Sessions 2-3 and stop before Session 4.
6. Add deterministic socket-denied tests for the valid chain and each rejected
   predecessor, hash, session, arm-token, wall-time, and terminal-gate case.
   Tests may use the existing in-memory exchange fixture only.

## Acceptance criteria

- No separate campaign or replacement identities are generated.
- Sessions 2 and 3 exactly match the original R2 preparation schedule.
- Canary is excluded from the 12-session economic denominator but remains a
  mandatory predecessor gate.
- Every credential/network path is reached only after all local admission
  checks pass.
- The execution remains Demo-only with no account configuration writes, no
  Live endpoint, and zero mutation retries.
- The plan and executor retain the frozen 12-session, 6-hour, 720-create,
  0.01 BTC, and 75 USDT limits without relaxation.
