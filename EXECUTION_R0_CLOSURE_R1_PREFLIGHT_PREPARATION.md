# CODEX EXECUTION BRIEF — R0 Qualification Closure & R1 Read-Only Demo Preflight Preparation

## 0. Purpose

This execution brief defines the **next process after the successful offline staged qualification** of the `tordave1150/mm-bot-okx` successor market-maker stack.

The goals are:

1. close and freeze R0 offline qualification evidence correctly;
2. correct qualification-report semantics where necessary;
3. produce a clean, immutable R0 closure package;
4. prepare all non-network components required for a future **R1 Read-Only OKX Demo Preflight**;
5. stop before any OKX connection, credential read, or remote endpoint access.

This document **does not authorize R1 execution**.

It authorizes only:

```text
R0 closure
+
offline verification
+
R1 preparation
+
fresh identity generation
+
read-only preflight plan construction
```

and then requires a hard stop.

---

# 1. Authority and precedence

Before executing this brief:

1. read the current root `AGENTS.md`;
2. read the current repository classification/architecture documents;
3. inspect the latest R0 staged-validation evidence;
4. obey the strictest applicable rule.

If this brief conflicts with the current root `AGENTS.md`, follow `AGENTS.md`.

This brief must never be interpreted as implicit authorization for:

- OKX Demo connection;
- credential access;
- account-state retrieval;
- exchange-time retrieval;
- market metadata retrieval from OKX;
- order/trade/position/balance reads from OKX;
- order creation;
- order amendment;
- order cancellation;
- position flattening;
- account/leverage/margin changes;
- production access;
- production shadow;
- Optuna;
- validation/holdout;
- Git write operations.

---

# 2. Starting point

The current offline staged candidate is reported as approximately:

```text
SafetyDecision:
SAFETY_PASS

EconomicHealth:
HEALTHY

EvidenceSufficiency:
SUFFICIENT_EVIDENCE

OverallOfflineState:
OFFLINE_QUALIFICATION_PASSED

Normal maker fills:
120 total
60 bid
60 ask

FIFO maker round trips:
60

Normal maker net PnL:
+15.60 USDT

Special flatten net PnL:
-0.20 USDT

Aggregate net PnL:
+15.40 USDT

Special flatten:
2 / 12 sessions

Emergency flatten:
0

candidate_fingerprint:
349f4ffa21339814e7fa831e63b7da618587781d2ad6ccee99430abcf0b88ebf
```

These values are working inputs until verified against the local evidence files.

Do not fabricate missing evidence.

---

# 3. Phase A — R0 report correctness repair

Before freezing R0, correct any report semantics that are inconsistent with the active protocol.

## 3.1 Qualification floors

The canonical qualification report must use the active R0 floors exactly.

At minimum verify and report:

```text
normal maker fills >= 24
bid maker fills >= 8
FIFO maker round trips >= 8
special flatten sessions <= 2 / 12
```

Do not substitute:

```text
maker fills >= 8
flatten rate <= 20%
```

as the canonical protocol thresholds unless the current root `AGENTS.md` has explicitly changed them.

Percentage metrics may be shown as secondary diagnostics.

Example:

```text
special_flatten_sessions = 2 / 12
special_flatten_rate = 16.67%
canonical_gate = PASS because 2 <= 2
```

---

# 4. Maker work-off metric correction

Verify the exact numerator and denominator used for:

```text
maker_workoff_success_rate
```

The metric must have a deterministic definition.

Preferred definition:

```text
maker-resolved eligible inventory episodes
--------------------------------------------
all eligible inventory episodes
```

If terminal cleanup episodes are excluded from eligibility, state that explicitly.

If the existing value of `100%` can only be achieved by excluding routine terminal cleanup episodes, rename the metric to a less ambiguous name such as:

```text
eligible_maker_workoff_resolution_rate
```

Report supporting counts:

```text
eligible_inventory_episodes
maker_resolved_episodes
routine_cleanup_episodes
emergency_flatten_episodes
```

Never report `100%` without the counts that produce it.

---

# 5. Historical metric correction

For the historical package:

```text
economic-package-20260831T140616Z
```

do not report unavailable newly introduced metrics as numeric zero.

If the historical schema did not capture:

```text
terminal_inventory_before_cleanup
maker_workoff_success_rate
time_to_flat
cleanup_reason_classification
```

report:

```text
N/A — metric not captured by historical schema
```

rather than:

```text
0
0.0000
```

Distinguish:

```text
measured zero
```

from:

```text
not measured
```

---

# 6. Historical decision immutability

Do not rewrite or replace the official terminal decision of any historical immutable package.

Any new evaluation of an old package must be labeled:

```text
COUNTERFACTUAL_EVALUATION_UNDER_CURRENT_GATE
```

and must include:

```text
official_historical_decision_unchanged: true
```

A counterfactual evaluation is analysis only.

It is not a replacement of historical evidence.

---

# 7. Git-operation reporting correction

If any read-only Git command was previously executed, report it accurately.

Do not conflate:

```text
git_write_operation = false
```

with:

```text
git_operations_executed = false
```

Recommended fields:

```text
git_read_operations_executed:
git_write_operation:
git_commit_created:
git_push_executed:
git_tag_created:
git_branch_created:
```

If the active `AGENTS.md` prohibits all Git operations, do not run any new Git command during this brief.

---

# 8. Phase B — Freeze the final R0 candidate

After report corrections, freeze the candidate.

The candidate freeze must include:

## 8.1 Source identity

Record SHA-256 for the exact successor files that materially determine:

- strategy decision;
- session lifecycle;
- work-off state;
- flatten classification;
- staged validation;
- campaign qualification;
- accounting;
- safety/reconciliation.

At minimum include the files used by the existing candidate fingerprint mechanism.

## 8.2 Configuration identity

Record:

- all 10 frozen strategy controls;
- frozen risk boundary;
- lifecycle/work-off policy constants;
- campaign/session limits;
- fees/slippage assumptions used by offline evaluation;
- market specification;
- observation/book-age/clock-skew limits.

## 8.3 Candidate fingerprint

Recompute the candidate fingerprint after report-only fixes.

If code behavior did not change, the behavioral candidate fingerprint should remain unchanged.

If it changes unexpectedly:

```text
STOP
```

and determine why.

A report-formatting change must not silently produce a behaviorally different trading candidate.

---

# 9. Phase C — R0 closure verification

Run only the minimum permitted offline verification needed to prove the candidate and evidence remain intact.

Do not rerun the entire 12-session campaign solely because report wording changed.

A Tier 4 rerun is required only if:

- trading/runtime behavior changed;
- work-off behavior changed;
- economic calculation changed;
- evidence calculation affecting qualification changed;
- candidate fingerprint changed due to behavioral code changes.

If only reporting semantics changed:

```text
reuse existing frozen Tier 4 evidence
+
recompute reporting outputs offline
```

---

# 10. R0 closure package

Create a fresh non-overwriting R0 closure package.

Suggested conceptual structure:

```text
artifacts/
└─ r0_offline_qualification_closure/
   └─ r0-closure-<fresh timestamp or permitted identity>/
      ├─ R0_CLOSURE_COMPLETED.json
      ├─ candidate_identity.json
      ├─ candidate_source_hashes.json
      ├─ frozen_risk_specification.json
      ├─ qualification_decision.json
      ├─ qualification_metrics.json
      ├─ workoff_metrics.json
      ├─ historical_comparison.json
      ├─ safety_audit.json
      ├─ network_audit.json
      ├─ credential_audit.json
      ├─ test_summary.json
      ├─ source_manifest.json
      └─ completion_hashes.json
```

Use current repository evidence conventions where possible.

Do not overwrite older packages.

---

# 11. Required R0 terminal decision

The final R0 closure decision should be one of:

```text
R0_OFFLINE_QUALIFICATION_PASSED
R0_MORE_EVIDENCE_REQUIRED
R0_SAFETY_FAILED
R0_CLOSURE_BLOCKED
```

If the reported evidence remains valid, the expected result is:

```text
R0_OFFLINE_QUALIFICATION_PASSED
```

and the informational next-phase status may be:

```text
R1_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION
```

This informational status must not trigger R1.

---

# 12. Phase D — Prepare R1 package offline

After R0 closes successfully, prepare a **fresh R1 package** without executing it.

Do not reuse any predecessor, R0, historical, or prior R1 identity.

Generate fresh conceptual identities for:

```text
r1_package_id
r1_run_id
r1_session_id
r1_arm_token_expected_value
```

Do not populate real credentials.

Do not read environment credentials.

Do not connect to OKX.

---

# 13. R1 objective

The future R1 phase is **read-only OKX Demo preflight**.

Its purpose will be to prove that the successor runtime can safely observe the real OKX Demo environment before any economic trading campaign.

R1 must not create, amend, cancel, or flatten any order.

R1 should eventually verify:

1. demo/sandbox transport is active;
2. exchange time is reachable and clock skew is within bounds;
3. instrument metadata matches the frozen market specification;
4. contract size is correct;
5. market is linear and not inverse;
6. account mode is compatible;
7. margin mode is compatible;
8. leverage state is compatible or safely observable;
9. current positions can be read;
10. current open orders can be read;
11. recent trades/fills can be read;
12. balance/account state can be read;
13. startup reconciliation can determine whether R2 would be safe to consider;
14. no mutation endpoint is called.

But none of these remote checks are executed under this brief.

---

# 14. R1 read-only endpoint allowlist design

Prepare an explicit allowlist of read-only operations required for R1.

Use the current adapter implementation as the source of truth.

Conceptually classify operations into:

## Allowed in future R1

Potential read-only categories:

```text
exchange/server time
market metadata
instrument metadata
balance/account state
account configuration
position snapshot
owned/open order snapshot
recent trades/fills
```

## Forbidden in R1

```text
create order
amend order
cancel order
cancel all
market order
reduce-only flatten
leverage change
margin-mode change
position-mode change
account mutation
transfer
withdrawal
```

The preparation artifact must map the actual adapter methods/endpoints into one of these categories.

Unknown methods must be:

```text
DENY
```

---

# 15. R1 credential contract

Prepare, but do not read, the required credential contract.

Expected logical credential fields may include:

```text
OKX_API_KEY
OKX_SECRET
OKX_PASSPHRASE
```

The preparation phase may validate only:

```text
required credential names are documented
```

It must not:

```text
load
print
hash
log
inspect
validate
test
```

real credential values.

The future R1 executor must ensure secrets are never written into evidence artifacts.

---

# 16. R1 demo transport contract

Prepare offline checks that will later assert the exchange object is configured for OKX Demo/Sandbox mode.

Expected future properties include:

```text
sandbox enabled
simulated-trading header enabled
LIVE mode unavailable
execution mode == OKX_DEMO
```

The future R1 executor must fail closed if any of these conditions are not proven.

Do not instantiate a network-capable exchange under this brief.

Use mocks/stubs only if implementation validation is needed.

---

# 17. R1 arm-token contract

Prepare the exact future authorization contract.

The expected token format should remain consistent with the adapter, for example:

```text
OKX_DEMO:<fresh_session_id>
```

Do not create an actual armed R1 execution marker under this brief.

The offline preparation package may record:

```text
expected_arm_token_format
authorization_required: true
```

but must leave:

```text
authorized: false
executed: false
```

---

# 18. R1 fail-closed preconditions

Prepare the future preflight so that R1 must abort if any of the following occurs:

```text
missing credential
demo mode not proven
sandbox header missing
unexpected live transport
wrong symbol
wrong market type
inverse contract
wrong contract size
unexpected account mode
unexpected margin mode
clock skew violation
incomplete position snapshot
incomplete order snapshot
incomplete trade snapshot
foreign/unowned order ambiguity
position reconciliation ambiguity
unexpected endpoint
any mutation attempt
```

No warning-only bypass.

---

# 19. R1 startup-state decision model

Prepare an explicit read-only R1 output decision.

Recommended:

```text
R1_PREFLIGHT_PASS
R1_PREFLIGHT_BLOCKED
R1_RECONCILIATION_REQUIRED
R1_SAFETY_FAILED
```

Definitions:

## `R1_PREFLIGHT_PASS`

Read-only environment and startup state are compatible with considering a separately authorized R2 campaign.

Does not authorize R2.

## `R1_PREFLIGHT_BLOCKED`

Preflight could not complete because environment/transport/configuration was unavailable or incomplete.

## `R1_RECONCILIATION_REQUIRED`

Account contains state that prevents a clean successor campaign from being considered, such as unexpected position/order/trade ambiguity.

R1 must not mutate the account to fix it.

## `R1_SAFETY_FAILED`

A hard safety contract was violated.

---

# 20. R1 preparation artifacts

Create an offline R1 preparation package.

Suggested structure:

```text
artifacts/
└─ r1_read_only_preflight_preparation/
   └─ r1-prep-<fresh identity>/
      ├─ R1_PREPARATION_COMPLETED.json
      ├─ r1_identity.json
      ├─ r1_authorization_contract.json
      ├─ read_endpoint_allowlist.json
      ├─ mutation_endpoint_denylist.json
      ├─ credential_contract.json
      ├─ demo_transport_contract.json
      ├─ market_spec_expectations.json
      ├─ account_state_expectations.json
      ├─ reconciliation_requirements.json
      ├─ failure_matrix.json
      ├─ expected_evidence_schema.json
      ├─ offline_tests.json
      └─ completion_hashes.json
```

No credential values.

No remote response data.

No OKX endpoint results.

---

# 21. R1 offline preparation tests

Add or run deterministic offline tests proving:

1. LIVE mode remains unavailable;
2. missing credentials fail before any remote operation;
3. wrong arm token fails;
4. wrong symbol fails;
5. wrong leverage expectation fails where required by current contract;
6. wrong margin mode expectation fails;
7. sandbox/demo transport is mandatory;
8. mutation methods are unreachable in R1 preflight mode;
9. unknown endpoint category fails closed;
10. R1 evidence writer never serializes secrets;
11. foreign/unowned state produces reconciliation-required/failure output;
12. incomplete snapshots fail closed;
13. clock-skew violation fixture fails;
14. market metadata mismatch fails;
15. valid mocked read-only state produces `R1_PREFLIGHT_PASS`.

All tests must run with:

```text
blank credentials
socket denial
zero external network
```

---

# 22. No remote R1 execution in this brief

At the end of preparation, assert:

```text
r1_prepared: true
r1_authorized: false
r1_executed: false

credential_reads: 0
network_attempts: 0
demo_endpoint_attempts: 0
live_endpoint_attempts: 0

create_attempts: 0
amend_attempts: 0
cancel_attempts: 0
flatten_attempts: 0
account_mutation_attempts: 0
```

This is a hard acceptance criterion.

---

# 23. Required user authorization boundary

The process must stop after:

```text
R1_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION
```

Codex must not interpret:

- this document;
- successful R0 closure;
- presence of credentials;
- existence of an arm-token format;
- successful offline R1 tests;

as authorization to connect to OKX.

A separate user instruction must explicitly authorize the **R1 Read-Only OKX Demo Preflight execution**.

---

# 24. Future R1 execution sequence

This section is planning only.

After separate authorization, the future R1 executor should follow:

```text
Verify exact fresh R1 identity
        ↓
Verify explicit R1 authorization
        ↓
Read credentials without logging them
        ↓
Construct demo-only transport
        ↓
Prove sandbox/simulated-trading mode
        ↓
Read exchange time
        ↓
Validate clock skew
        ↓
Read market/instrument metadata
        ↓
Validate frozen market spec
        ↓
Read account configuration
        ↓
Read positions
        ↓
Read open orders
        ↓
Read recent trades/fills
        ↓
Run startup reconciliation
        ↓
Write read-only evidence
        ↓
STOP
```

There must be no:

```text
create
amend
cancel
flatten
account mutation
```

during R1.

---

# 25. R2 remains separate

Even if a future R1 returns:

```text
R1_PREFLIGHT_PASS
```

do not start R2.

The result may only state:

```text
R2_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION
```

R2 requires:

- fresh identities;
- successful R1 evidence;
- separate user authorization;
- its own execution brief or active protocol.

---

# 26. Final Codex report format

At the end of this brief, return:

## A. R0 closure

```text
R0 decision:
candidate fingerprint:
behavioral code changed: true/false
Tier 4 rerun required: true/false
Tier 4 rerun executed: true/false
```

## B. Report corrections

Explicitly confirm:

```text
canonical fill floor:
canonical bid floor:
canonical FIFO floor:
canonical flatten gate:
workoff metric definition:
historical unavailable metrics use N/A:
historical decision unchanged:
```

## C. R0 safety audit

```text
production_authorized: false
live_mode_available: false
credential_reads: 0
network_attempts: 0
demo_endpoint_attempts: 0
live_endpoint_attempts: 0
live_orders: 0
account_mutations: 0
optuna_executed: false
validation_opened: false
holdout_opened: false
git_write_operation: false
```

## D. R1 preparation

Report:

```text
r1_package_id:
r1_run_id:
r1_session_id:
arm_token_format:
read_allowlist_ready:
mutation_denylist_ready:
credential_contract_ready:
transport_contract_ready:
failure_matrix_ready:
offline_tests:
```

Never print real secrets.

## E. Terminal status

The expected successful terminal status is:

```text
R0_OFFLINE_QUALIFICATION_PASSED
R1_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION
```

---

# 27. Acceptance criteria

This process is complete only when:

- R0 report semantics match the active protocol;
- historical evidence is not rewritten;
- unavailable historical metrics are not represented as zero;
- work-off metric numerator/denominator is explicit;
- candidate identity is frozen;
- R0 closure package is complete and non-overwriting;
- no unnecessary Tier 4 rerun is performed;
- fresh R1 identities are prepared;
- R1 allowlist/denylist contracts are explicit;
- all R1 preparation tests are offline;
- zero credentials are read;
- zero network connections are attempted;
- zero demo/live mutations occur;
- execution stops before R1.

---

# 28. Core principle

The transition must be:

```text
R0 evidence
    ↓
Correct semantics
    ↓
Freeze candidate
    ↓
Close R0
    ↓
Prepare R1 offline
    ↓
HARD STOP
    ↓
Separate human authorization
    ↓
Future R1 read-only execution
```

Not:

```text
R0 PASS
    ↓
automatic OKX Demo execution
```

The objective of this process is to make the next phase **ready to execute safely**, not to execute it prematurely.

**R0 proves the offline candidate.  
R1 will prove the demo environment.  
Neither authorizes R2 or production.**
