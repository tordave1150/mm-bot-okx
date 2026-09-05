# CODEX EXECUTION BRIEF — R2 Final Admission & Canary Preparation

## 0. Purpose

This execution brief defines the final **pre-execution admission and canary preparation** for the `R2 SUCCESSOR_ECONOMIC_CAMPAIGN` of the `tordave1150/mm-bot-okx` repository.

The purpose is to close the remaining R2 preparation gaps before any OKX Demo order mutation is authorized.

This brief must:

1. prove that the R2 prepared candidate is behaviorally identical to the R0-qualified and R1-bound candidate;
2. distinguish canonical strategy tunables from frozen model/safety inputs correctly;
3. add a mandatory fresh pre-mutation admission reconciliation immediately before the first R2 order;
4. convert R2 from an uninterrupted 12-session campaign into a staged execution:
   - first-session canary;
   - three-session checkpoint;
   - remaining qualification campaign;
5. preserve the existing frozen risk boundary;
6. stop before any R2 session is armed or any order is submitted.

This brief **does not authorize R2 execution**.

---

# 1. Authority and precedence

Before performing any action:

1. read the current root `AGENTS.md`;
2. read the current R0 closure package;
3. read the passed R1 preflight package;
4. read the current R2 preparation package;
5. inspect the exact candidate/config/profile sources used by each stage.

If this brief conflicts with root `AGENTS.md`, follow `AGENTS.md`.

No approval from R0 or R1 automatically authorizes R2.

This task remains preparation-only.

---

# 2. Hard prohibitions

Under this brief, do **not**:

- place any OKX Demo order;
- amend any order;
- cancel any exchange order;
- flatten any position;
- mutate leverage;
- mutate margin mode;
- mutate position mode;
- transfer funds;
- withdraw funds;
- access production/live trading;
- run the R2 campaign;
- arm an R2 session;
- reuse R1 identities;
- reuse predecessor identities;
- weaken safety limits;
- increase modeled capital;
- increase lot size;
- increase inventory cap;
- increase leverage;
- enable mutation retries;
- execute Optuna;
- open validation or holdout;
- perform Git write operations.

Any network access during this preparation must remain prohibited unless current `AGENTS.md` explicitly permits a read-only check. Prefer complete offline preparation with socket denial.

---

# 3. Starting prerequisite chain

The following evidence chain must be treated as immutable prerequisites:

```text
R0
r0-closure-20260904T121733Z
R0_OFFLINE_QUALIFICATION_PASSED

        ↓

R1
r1-preflight-run-20260904T121733Z
R1_PREFLIGHT_PASSED

        ↓

R2 PREPARATION
r2-prep-20260904T124817Z
R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION
```

Expected R0/R1 candidate fingerprint:

```text
1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf
```

Do not trust the R2 preparation manifest merely because it claims this fingerprint.

Recompute and compare the actual behavioral inputs.

---

# 4. Critical blocker — Candidate parameter reconciliation

Before R2 can become execution-ready, prove that:

```text
R0 Qualified Candidate
        ==
R1 Bound Candidate
        ==
R2 Prepared Candidate
```

for all behaviorally material strategy, lifecycle, safety, market, and campaign fields.

The required outcome is:

```text
behavioral_parameter_drift = 0
risk_parameter_drift = 0
market_spec_drift = 0
lifecycle_policy_drift = 0
```

Any unexplained non-zero drift must produce:

```text
R2_PREPARATION_INVALID
```

and stop the process.

---

# 5. Canonical MarketMakerV1Config classification

Do not mislabel frozen model/safety inputs as the ten tunable strategy controls.

The canonical `MarketMakerV1Config` defines exactly ten tunable controls:

```text
risk_aversion_gamma
arrival_decay_k_or_proxy
volatility_ewma_decay
minimum_half_spread_bps
maximum_half_spread_bps
inventory_skew_strength
imbalance_skew_strength
minimum_order_lifetime_ticks
maximum_order_age_ticks
requote_threshold_ticks
```

The following are examples of frozen model/safety inputs and must be reported separately:

```text
volatility_min_samples
volatility_floor
volatility_cap
time_horizon_ticks
minimum_size_change_ratio
fixed_lot_size_btc
maximum_inventory_lots
maximum_margin_utilization
soft_session_loss_pct
hard_kill_drawdown_pct
tick_size_usdt
maker_fee_rate
taker_fee_rate
terminal_slippage_bps
```

Do not present:

```text
fixed_lot_size_btc
maximum_inventory_lots
volatility_cap
time_horizon_ticks
```

as part of the "ten tunable controls."

---

# 6. Promoted profile reconciliation

The R2 preparation report references:

```text
promoted_profile = FEE_AWARE_SPREAD_6
profile_id = mm-v1-6-profile-02
```

and reports strategy/model values that differ from default `MarketMakerV1Config`.

Codex must determine whether `mm-v1-6-profile-02` is:

```text
A. a valid promoted override that was already frozen before R0 qualification;
B. a presentation/reporting artifact only;
C. a candidate drift introduced after R0 qualification;
D. an unverified profile source.
```

## Required proof

Produce an exact profile lineage:

```text
default MarketMakerV1Config
        ↓
research/promoted override source
        ↓
R0 qualified effective config
        ↓
R1 bound effective config
        ↓
R2 prepared effective config
```

For every field report:

```text
field_name
default_value
promoted_value
r0_effective_value
r1_effective_value
r2_effective_value
field_class
source_file
source_hash
drift_status
```

`field_class` must be one of:

```text
TUNABLE_STRATEGY_CONTROL
FROZEN_MODEL_INPUT
FROZEN_SAFETY_INPUT
LIFECYCLE_POLICY
CAMPAIGN_LIMIT
MARKET_SPEC
REPORT_ONLY
```

---

# 7. Drift decision rules

A field passes only if the effective behavior used in R2 is exactly the behavior qualified by R0.

Allowed difference:

```text
REPORT_ONLY
```

Examples:

- renamed metric;
- new report label;
- extra evidence field;
- formatting-only metadata.

Not allowed without new qualification:

```text
risk_aversion_gamma change
arrival_decay change
spread bound change
volatility model change
inventory skew change
imbalance skew change
order age/lifetime change
requote threshold change
time horizon change
volatility cap change
lot change
inventory cap change
fee assumption change
work-off behavior change
terminal timing change
drawdown limit change
campaign budget change
```

If a behavioral field differs:

```text
R2 candidate is NOT the R0-qualified candidate.
```

Stop and classify:

```text
R2_REQUALIFICATION_REQUIRED
```

Do not silently adopt the new value.

---

# 8. Required drift artifact

Create:

```text
r2_candidate_drift_audit.json
```

Suggested schema:

```json
{
  "r0_candidate_fingerprint": "...",
  "r1_candidate_fingerprint": "...",
  "r2_candidate_fingerprint": "...",
  "effective_config_match": true,
  "behavioral_parameter_drift": 0,
  "risk_parameter_drift": 0,
  "market_spec_drift": 0,
  "lifecycle_policy_drift": 0,
  "report_only_differences": [],
  "field_comparison": []
}
```

If any material drift exists:

```text
effective_config_match = false
```

and preparation must stop.

---

# 9. Frozen economic risk boundary

Regardless of Demo account equity, R2 must continue using the protocol risk budget rather than available exchange equity.

The R2 campaign must remain bounded by:

```text
symbol:
BTC/USDT:USDT

market:
linear USDT swap
inverse = false

margin mode:
isolated

position mode:
net

leverage:
3x

modeled capital:
750 USDT

normal lot:
0.01 BTC

absolute inventory cap:
0.01 BTC

owned bid orders:
<= 1

owned ask orders:
<= 1

normal create:
post-only

normal cancel:
owned-only

mutation retries:
0

read retries:
<= 3

session duration:
<= 30 minutes

normal creates/session:
<= 60

campaign sessions:
<= 12

campaign wall time:
<= 6 hours

campaign normal creates:
<= 720

campaign hard loss:
<= 75 USDT

session soft drawdown:
22.50 USDT

session hard drawdown:
37.50 USDT

book age:
<= 1000 ms

absolute clock skew:
<= 1500 ms

unresolved single-flight flatten:
<= 1
```

The R1-observed Demo account equity must **not** alter:

```text
modeled capital
lot size
inventory cap
loss budgets
campaign limits
```

---

# 10. Account-mode semantics

Keep these fields separate:

```text
position_mode = net
margin_mode = isolated
leverage = 3x
```

Do not combine them into a single ambiguous field such as:

```text
account_mode = isolated net_mode
```

The final admission evidence must report them independently.

---

# 11. Fresh R2 pre-mutation admission gate

R1 preflight evidence proves that the Demo environment was safe at the R1 observation time.

It does **not** prove that account state is unchanged when R2 begins.

Therefore, immediately before the first R2 mutation, a fresh admission reconciliation must execute.

The future R2 execution sequence must be:

```text
Verify fresh R2 authorization
        ↓
Verify fresh R2 identity
        ↓
Verify candidate drift audit = PASS
        ↓
Verify OKX Demo transport
        ↓
Fetch fresh exchange time
        ↓
Validate fresh clock skew
        ↓
Fetch fresh market metadata
        ↓
Validate frozen MarketSpec
        ↓
Fetch fresh account configuration
        ↓
Validate:
    position_mode = net
    margin_mode = isolated
    leverage = 3x
        ↓
Fetch fresh position snapshot
        ↓
Fetch fresh open-order snapshot
        ↓
Fetch fresh recent trade/fill state
        ↓
Run startup reconciliation
        ↓
ADMISSION_PASS
        ↓
Only then allow first post-only create
```

---

# 12. Admission gate must fail closed

The admission gate must reject R2 execution if any of the following is observed:

```text
candidate fingerprint mismatch
effective config mismatch
profile lineage unresolved
wrong symbol
wrong market type
inverse contract
wrong contract size
wrong position mode
wrong margin mode
wrong leverage
unexpected non-zero position
foreign open order
stale owned order
duplicate owned side
incomplete position snapshot
incomplete order snapshot
incomplete fills/trades snapshot
clock skew > 1500 ms
stale/future market data
sandbox/demo transport not proven
simulated-trading header missing
unknown endpoint use
credential contract violation
kill-switch/latch active unexpectedly
state-store inconsistency
```

R2 must not mutate account configuration to repair admission.

For example, do **not**:

```text
set leverage to 3x
set net mode
set isolated mode
cancel foreign order
flatten unexpected position
```

inside admission.

Instead return:

```text
R2_ADMISSION_BLOCKED
```

or:

```text
R2_RECONCILIATION_REQUIRED
```

and stop.

---

# 13. Admission decision model

Use:

```text
R2_ADMISSION_PASS
R2_ADMISSION_BLOCKED
R2_RECONCILIATION_REQUIRED
R2_SAFETY_FAILED
R2_REQUALIFICATION_REQUIRED
```

## R2_ADMISSION_PASS

All fresh exchange/account/candidate checks pass.

This allows only the separately authorized R2 canary stage.

## R2_ADMISSION_BLOCKED

Environment/configuration/preconditions prevent safe start.

## R2_RECONCILIATION_REQUIRED

Unexpected position/order/trade state prevents clean start.

No automatic account repair.

## R2_SAFETY_FAILED

Hard safety invariant failure.

## R2_REQUALIFICATION_REQUIRED

Candidate differs materially from R0-qualified behavior.

---

# 14. R2 execution must be staged

Do not execute all twelve sessions as one uninterrupted authorization unit.

Use:

```text
STAGE A
Fresh R2 Admission
        ↓
STAGE B
Session 1 Canary
        ↓
Hard Checkpoint 1
        ↓
STAGE C
Sessions 2–3
        ↓
Hard Checkpoint 2
        ↓
STAGE D
Sessions 4–12
        ↓
Campaign Qualification
```

---

# 15. Stage A — Fresh admission

No normal order may be created before:

```text
R2_ADMISSION_PASS
```

Evidence must include:

```text
fresh exchange timestamp
clock skew
transport proof
market metadata
contract size
position mode
margin mode
leverage
position snapshot
open-order snapshot
recent trade/fill cursor
startup reconciliation
candidate fingerprint
candidate drift audit
```

---

# 16. Stage B — Session 1 canary

The first R2 session is the first real order-mutation canary on OKX Demo.

It must be treated separately from the remaining campaign.

Use the existing frozen session limits:

```text
<= 30 minutes
<= 60 normal creates
0.01 BTC lot
0.01 BTC inventory cap
1 owned bid
1 owned ask
post-only create
owned-only cancel
0 mutation retries
```

No risk budget may be increased for the canary.

---

# 17. Session 1 hard checkpoint

After Session 1, continuation is prohibited unless all hard checks pass.

Required hard checks:

```text
terminal position = 0 within tolerance
terminal owned open orders = 0
unknown fills = 0
unclassified execution events = 0
mutation ambiguity = 0
unresolved flatten attempts = 0
inventory cap breach = 0
owned-order count breach = 0
normal create budget breach = 0
accounting reconciliation = exact
fee attribution = reconciled
FIFO attribution = reconciled
clock-skew violations = 0
stale/future book safety violations unresolved = 0
live endpoint attempts = 0
account mutation attempts = 0
mutation retries = 0
```

If any hard check fails:

```text
R2_CANARY_FAILED
```

and stop.

Do not continue to Session 2.

---

# 18. Session 1 diagnostic checks

The following are diagnostic rather than automatic safety failures unless current protocol says otherwise:

```text
maker fills
fill side balance
FIFO maker work-off
normal net PnL
special net PnL
aggregate net PnL
routine terminal cleanup
maker work-off resolution
time-to-flat
cancel/fill race observations
observed request latency
observed acknowledgement latency
```

The purpose of Session 1 is operational validation, not statistical qualification.

One low-fill session is not sufficient reason to retune the strategy.

---

# 19. Stage C — Sessions 2–3

Only after Session 1 hard checkpoint passes may Sessions 2 and 3 proceed.

Sessions 2–3 must use the same frozen candidate.

No parameter changes between sessions.

Before each session:

```text
fresh reconciliation
position = flat
owned orders = 0
account mode unchanged
margin mode unchanged
leverage unchanged
candidate fingerprint unchanged
campaign budgets not exhausted
```

---

# 20. Three-session hard checkpoint

After Sessions 1–3, evaluate:

## Hard safety

All three sessions must have:

```text
safe terminal state
flat position
0 owned open orders
0 unknown fills
0 unresolved ambiguity
0 inventory breach
0 forbidden endpoint
0 account mutation
0 mutation retry
```

## Operational quality

Inspect:

```text
normal maker fills
bid/ask fills
FIFO round trips
maker work-off behavior
routine cleanup count
emergency flatten count
normal/special PnL
fee reconciliation
latency/cancel-fill behavior
```

If the first three sessions reveal an obvious systemic defect:

```text
R2_THREE_SESSION_CHECKPOINT_FAILED
```

and stop.

Do not continue merely because the final 12-session qualification threshold has not yet been evaluated.

---

# 21. Stage D — Sessions 4–12

Only after the three-session checkpoint passes may the remaining sessions continue.

The remaining stage must preserve:

```text
same candidate fingerprint
same effective config
same MarketSpec
same risk limits
same lifecycle/work-off policy
same campaign identity
same qualification floors
```

Per-session fresh reconciliation remains mandatory.

---

# 22. Campaign-wide limits

At all times, independently of per-session limits:

```text
sessions <= 12
wall time <= 6 hours
normal creates <= 720
aggregate hard loss <= 75 USDT
```

The supervisor must prevent admission of a new session if any campaign-wide budget would be exceeded.

---

# 23. Canonical qualification metrics

Use the current protocol floors exactly.

At minimum:

```text
normal maker fills >= 24
normal bid maker fills >= 8
normal ask maker fills >= 8
FIFO maker round trips >= 8
special flatten sessions <= 2 / 12
emergency flatten sessions = 0
normal net PnL > 0
```

If fill-balance ratio remains part of the active protocol, report it using the active definition and floor.

Do not replace:

```text
special flatten sessions <= 2
```

with only:

```text
special flatten rate <= 20%
```

The percentage may be reported as a secondary diagnostic.

---

# 24. Special-flatten semantics

Continue distinguishing:

```text
ROUTINE_TERMINAL_CLEANUP
RISK_EMERGENCY_FLATTEN
```

The existence of a routine terminal cleanup must not be mislabeled as a safety failure by itself.

However, the canonical count ceiling remains binding.

Emergency flatten remains a separate hard diagnostic and expected campaign count:

```text
0
```

unless current protocol explicitly says otherwise.

---

# 25. Work-off metrics

Continue reporting:

```text
eligible_inventory_episodes
maker_resolved_episodes
eligible_maker_workoff_resolution_rate
routine_cleanup_episodes
emergency_flatten_episodes
terminal_inventory_before_cleanup
time_to_flat
```

Never report unavailable metrics as numeric zero.

Use:

```text
N/A — not captured
```

where appropriate.

---

# 26. Endpoint permissions

The future R2 execution layer must separate:

```text
local/helper method calls
CCXT abstraction methods
actual remote HTTP/API requests
```

Do not report local methods such as:

```text
market()
set_markets()
```

as if each were an exchange endpoint request.

Evidence should contain both:

```text
logical_method_call_counts
network_request_counts
```

where deterministically available.

---

# 27. R2 mutation contract

During actual R2 execution, permitted mutation types are limited to those required by the frozen successor protocol.

Normal path:

```text
post-only create
owned-order cancel
```

Terminal/risk path:

```text
single-flight reduce-only flatten
```

subject to the existing protocol.

Forbidden:

```text
unowned cancel
cancel-all indiscriminately
account mode change
leverage change
position mode change
fund transfer
withdrawal
self-trade
mutation retry
production/live order
```

Unknown mutation methods must fail closed.

---

# 28. Session identity audit

Verify all prepared R2 session IDs and nonces.

Requirements:

```text
unique
fresh
non-overwriting
not reused from R0
not reused from R1
not reused from predecessor
not deterministically colliding
```

The apparent sequence irregularity around the prepared `s10` nonce must be reviewed.

A non-sequential nonce is not itself a failure.

The requirement is uniqueness and correct generation provenance.

Do not manually "fix" a nonce merely for aesthetics.

Instead prove:

```text
generation_method
uniqueness
collision_count = 0
```

---

# 29. New preparation artifacts

Create a fresh non-overwriting package such as:

```text
artifacts/
└─ r2_final_admission_preparation/
   └─ r2-final-prep-<fresh-id>/
      ├─ prerequisite_manifest.json
      ├─ candidate_profile_lineage.json
      ├─ r2_candidate_drift_audit.json
      ├─ effective_config_manifest.json
      ├─ frozen_risk_specification.json
      ├─ fresh_admission_contract.json
      ├─ admission_failure_matrix.json
      ├─ canary_stage_contract.json
      ├─ three_session_checkpoint_contract.json
      ├─ continuation_contract.json
      ├─ endpoint_permissions.json
      ├─ session_identity_audit.json
      ├─ expected_execution_evidence_schema.json
      ├─ offline_test_summary.json
      ├─ completion_hashes.json
      └─ R2_FINAL_PREPARATION_COMPLETED.json
```

Terminal marker must be written last.

---

# 30. Required offline tests

Before declaring R2 execution-ready, add or run deterministic offline tests proving at least:

1. R0/R1/R2 exact candidate match passes;
2. strategy tunable drift fails;
3. frozen model input drift fails;
4. frozen safety input drift fails;
5. report-only difference does not invalidate candidate;
6. wrong candidate fingerprint fails;
7. wrong symbol fails admission;
8. wrong contract size fails admission;
9. wrong position mode fails admission;
10. wrong margin mode fails admission;
11. wrong leverage fails admission;
12. non-zero startup position returns reconciliation-required;
13. foreign open order returns reconciliation-required;
14. incomplete position snapshot fails;
15. incomplete order snapshot fails;
16. incomplete trade/fill snapshot fails;
17. clock skew violation fails;
18. sandbox/demo transport mismatch fails;
19. Session 1 hard checkpoint failure blocks Session 2;
20. three-session hard checkpoint failure blocks Session 4;
21. campaign create budget blocks further sessions;
22. campaign hard-loss budget blocks further sessions;
23. candidate drift between sessions blocks continuation;
24. account configuration drift between sessions blocks continuation;
25. mutation retries remain zero;
26. account mutation methods remain unreachable;
27. unknown mutation endpoint fails closed;
28. session IDs are unique;
29. secret values are never serialized;
30. preparation mode performs zero order mutation.

All preparation tests should use:

```text
blank credentials
socket denial
zero external network
```

unless the active protocol requires otherwise.

---

# 31. Final preparation decision model

Use one of:

```text
R2_FINAL_PREPARATION_PASSED
R2_PARAMETER_RECONCILIATION_FAILED
R2_REQUALIFICATION_REQUIRED
R2_PREPARATION_BLOCKED
R2_SAFETY_FAILED
```

Expected successful informational next state:

```text
R2_CANARY_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION
```

This does **not** authorize Session 1.

---

# 32. Hard stop boundary

At successful completion, assert:

```text
r2_final_preparation_complete: true
r2_execution_authorized: false
r2_canary_authorized: false

sessions_executed: 0
orders_created: 0
orders_amended: 0
orders_cancelled: 0
flatten_attempts: 0
account_mutations: 0

live_endpoint_attempts: 0
production_authorized: false
```

No R2 session may arm.

---

# 33. Required final Codex report

Return:

## A. Candidate drift audit

```text
r0 fingerprint:
r1 fingerprint:
r2 fingerprint:

behavioral_parameter_drift:
risk_parameter_drift:
market_spec_drift:
lifecycle_policy_drift:

effective_config_match:
```

## B. Profile lineage

Explicitly report:

```text
default profile:
promoted profile:
promotion source:
R0 effective profile:
R1 effective profile:
R2 effective profile:
```

Provide field-by-field comparison for all behaviorally material inputs.

## C. Risk boundary

Report the exact values for:

```text
modeled capital
lot size
inventory cap
leverage
position mode
margin mode
session loss limits
campaign loss limit
session create limit
campaign create limit
campaign wall time
```

## D. Admission gate

Report:

```text
fresh_admission_contract_ready:
reconciliation_contract_ready:
failure_matrix_ready:
account_state_mutation_allowed: false
```

## E. Staged execution

Report:

```text
Session 1 canary contract:
3-session checkpoint contract:
remaining-session continuation contract:
```

## F. Session identity audit

Report:

```text
session_count:
unique_session_ids:
collision_count:
generation_method:
```

## G. Tests

Report:

```text
drift tests:
admission tests:
canary checkpoint tests:
campaign continuation tests:
secret/network tests:
```

## H. Terminal status

Expected:

```text
R2_FINAL_PREPARATION_PASSED
R2_CANARY_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION
```

---

# 34. Future execution authorization boundary

A future user authorization should explicitly authorize only the next stage.

Recommended next authorization scope:

```text
Authorize R2 Session 1 Canary execution on OKX Demo only using the frozen
qualified candidate and passed fresh R2 admission gate. Do not continue to
Sessions 2–12 without the Session 1 hard checkpoint passing.
```

Do not treat authorization for Session 1 as authorization for all 12 sessions unless the user explicitly says so and the active protocol permits it.

Preferred governance remains staged.

---

# 35. Core principle

The final preparation sequence is:

```text
R0 Qualified Candidate
        ↓
R1 Passed Preflight
        ↓
R2 Prepared Candidate
        ↓
PROVE ZERO DRIFT
        ↓
Freeze Effective R2 Candidate
        ↓
Prepare Fresh Admission Gate
        ↓
Prepare Session 1 Canary
        ↓
Prepare 3-Session Checkpoint
        ↓
Prepare Remaining Campaign
        ↓
HARD STOP
        ↓
Separate Authorization
```

The future execution sequence is:

```text
Fresh Admission
    ↓
Session 1
    ↓
Hard Checkpoint
    ↓
Sessions 2–3
    ↓
Hard Checkpoint
    ↓
Sessions 4–12
```

Not:

```text
R2 prepared
    ↓
run all 12 automatically
```

**The candidate must be proven identical before the first mutation.  
The account state must be proven fresh before the first mutation.  
The first mutation must be treated as a canary, not as blanket authorization for the whole campaign.**
