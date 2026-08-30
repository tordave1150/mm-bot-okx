# AGENTS.md — Market Maker v1.3C Fill-Trigger and Activity Evidence Repair

## 1. Mission

These instructions apply only to the current local project folder.

The active strategy remains:

```text
market_maker_v1 / AVELLANEDA_STOIKOV
```

The most recent completed protocol ended with:

```text
MM_V1_3B_REPAIR_FAILED
First failed gate: GATE_0_EVIDENCE_INTEGRITY
```

Retained v1.3B findings:

```text
formal run executed exactly once
structural artifact integrity            passed
stream and completion hashes             passed
maker fills misclassified as terminal    619
formal strict-maker fills                 0
reconstructed FIFO round trips            355
preventable margin breaches               0
inventory breaches                        0
balanced v1.3 rerun                       no
stress v1.3A rerun                        no
```

Diagnostic directional findings from the closed run:

```text
FAST_CANCEL_ON_VOLATILITY
  first hard kill moved from S2_5 to S3_LOW

ONE_SIDED_DEFENSIVE_MODE
  hard kills 0
  drawdown approximately 2.70%
  net PnL approximately -54.97 USDT
  qualifying scenario coverage insufficient

COMPOSITE_DEFENSIVE
  hard kills 0
  drawdown approximately 1.79%
  net PnL approximately +3.00 USDT
  no fills at S2_5 or S3_LOW
```

These directional findings are not admissible promotion evidence because activity classification failed.

The current task is:

> Repair and verify fill-trigger normalization and activity reconciliation, then execute one fresh fixed-profile matrix using the same eight frozen defensive parameter vectors to determine whether any defensive profile truly improves stress resilience while maintaining meaningful normal maker activity.

Do not modify defensive parameters in this cycle.

Assign exactly one final status:

```text
MM_V1_3C_EVIDENCE_REPAIR_FAILED
MM_V1_3C_ACTIVITY_INSUFFICIENT
MM_V1_3C_NO_RESILIENCE_IMPROVEMENT
MM_V1_3C_COMPOSITE_INACTIVITY_REJECTED
MM_V1_3C_RESILIENCE_IMPROVED_BUT_BUDGET_REJECTED
MM_V1_3C_RESILIENCE_EVIDENCE_SUPPORTED
```

No status authorizes:

```text
Optuna
random search
Bayesian search
validation
holdout
exchange access
demo trading
live trading
production-default changes
Git staging
Git commit
Git push
remote access
```

---

## 2. Folder-Only Operating Rules

Work only inside the current project folder.

Allowed:

```text
read local source
edit local source within evidence-repair scope
add local tests
add local reconciliation fixtures
run deterministic offline simulations
run one fresh fixed-profile matrix
write local artifacts
```

Forbidden:

```text
git add
git commit
git push
git pull
git fetch
git merge
git rebase
git reset
git clean
git checkout
git restore
git branch creation
GitHub API access
remote repository access
exchange API access
credential access
Optuna execution
adaptive search
validation
holdout
balanced v1.3 rerun
stress v1.3A rerun
v1.3B rerun
demo trading
live trading
production-default changes
```

Do not delete unrelated files.

Do not overwrite unrelated local changes.

Do not use Git metadata as the source of truth.

Before editing, record:

```text
local directory tree
relevant source files
imports
file sizes
baseline tests
prior protocol hashes
prior artifact hashes
```

---

## 3. Closed Evidence Preservation

Do not rerun, modify, extend, overwrite, reinterpret, or backfill:

```text
Initial rejected MM protocol:
63e259ad69cc6da620494f00d2e4c419ff0b565c9c42ca664023e28221a4a30c

MM v1.1:
7481d1a1a56f4b554026defff0edf86ea0e40730802bd401a291e74618cdffea

MM v1.2:
ca2d726c1bb90ef8202968351b0f5468089bb8dccb7680f3ffcc0511ae395d8f

MM v1.3:
1358b8815f585684250a2369e06411585f06a1a39636ec2465310ff57235bfc9

MM v1.3A:
e395f6a2d1faf69b8f6763c6582fccc63b12b597e4064b7213c6a53a56ee2c81

MM v1.3B:
dfa4734a620dd904266747485ea2ce3f3ac95e1691a299275f6ac7bb03027292
```

The v1.3B evidence defect must remain preserved exactly as executed.

Do not relabel the 619 old fills.

Do not regenerate old activity summaries.

Do not revise the v1.3B final status.

Use old evidence only for:

```text
repair hypothesis
regression expectations
profile carry-forward
historical comparison
```

The v1.3C cycle requires:

```text
new protocol ID
new specification hash
new run ID
new profile IDs and fingerprints
new path IDs
new seeds
new source blocks
new artifact directories
new completion hashes
```

---

# PART I — FILL-TRIGGER NORMALIZATION

## 4. Canonical Trigger Taxonomy

Every fill must have exactly one canonical execution trigger:

```text
STRICT_TRADE_THROUGH
AGGRESSOR_TRADE_AT_QUOTE
TERMINAL_EXECUTION
HARD_KILL_EXECUTION
EMERGENCY_EXECUTION
```

No additional trigger may enter ranking without an explicit protocol revision.

Unknown trigger fails integrity.

### Normal passive fills

A normal passive fill must satisfy:

```text
maker_or_taker == MAKER
fill_trigger in {
  STRICT_TRADE_THROUGH,
  AGGRESSOR_TRADE_AT_QUOTE
}
special_exit == false
```

### Terminal close

A terminal close must satisfy:

```text
fill_trigger == TERMINAL_EXECUTION
maker_or_taker == TAKER
special_exit == true
normal_activity_eligible == false
normal_round_trip_eligible == false
```

### Hard-kill close

A hard-kill close must satisfy:

```text
fill_trigger == HARD_KILL_EXECUTION
maker_or_taker == TAKER
special_exit == true
normal_activity_eligible == false
normal_round_trip_eligible == false
```

### Emergency close

An emergency close must satisfy:

```text
fill_trigger == EMERGENCY_EXECUTION
maker_or_taker == TAKER
special_exit == true
normal_activity_eligible == false
normal_round_trip_eligible == false
```

---

## 5. Forbidden Classification Combinations

The following combinations must fail closed:

```text
maker + TERMINAL_EXECUTION
maker + HARD_KILL_EXECUTION
maker + EMERGENCY_EXECUTION

taker + STRICT_TRADE_THROUGH
taker + AGGRESSOR_TRADE_AT_QUOTE

special_exit == true
and
normal_activity_eligible == true

special_exit == true
and
normal_round_trip_eligible == true
```

Also reject:

```text
missing trigger
unknown trigger
multiple triggers
trigger inferred only after run completion
trigger overwritten during summary aggregation
```

---

## 6. Canonical Fill Identity

Every fill record must include:

```text
fill_id
order_id
profile_id
path_id
tick
side
quantity_btc
fill_price
fee
maker_or_taker
fill_trigger
special_exit
normal_activity_eligible
normal_round_trip_eligible
trigger_event_id
quote_event_id
inventory_before
inventory_after
```

The canonical values must be assigned when the fill is created.

Summary and analysis code may read these fields.

Summary and analysis code must not rewrite them.

---

## 7. Trigger Source of Truth

Trace fill classification through:

```text
matching event
→ execution result
→ fill record
→ accounting ledger
→ FIFO matcher
→ activity analyzer
→ final report
```

There must be one authoritative classification source.

Do not derive normal/special identity independently in multiple modules.

Recommended design:

```text
CanonicalFillClassification
```

with validated fields:

```text
maker_or_taker
fill_trigger
special_exit
normal_activity_eligible
normal_round_trip_eligible
```

Every downstream consumer must use this canonical object or its serialized equivalent.

---

# PART II — RECONCILIATION CONTRACT

## 8. Fill Partition Identity

Require exact partitioning:

```text
all fills
=
normal maker fills
+ terminal fills
+ hard-kill fills
+ emergency fills
```

The four sets must be mutually exclusive.

Require:

```text
normal maker fills
=
strict trade-through maker fills
+ aggressor-at-quote maker fills
```

Require:

```text
unknown fill classifications == 0
multiply classified fills == 0
unclassified fills == 0
```

---

## 9. Activity Identity

Only normal maker fills may enter normal activity.

Require:

```text
normal_activity_fill_count
=
count(
  maker fills
  where normal_activity_eligible == true
)
```

Require:

```text
terminal fills in normal activity       == 0
hard-kill fills in normal activity      == 0
emergency fills in normal activity      == 0
```

Activity summaries must report:

```text
strict trade-through maker fills
aggressor-at-quote maker fills
terminal fills
hard-kill fills
emergency fills
normal activity fills
excluded special-exit fills
```

---

## 10. Round-Trip Identity

Use deterministic FIFO matching.

Normal FIFO round trips may use only:

```text
normal_round_trip_eligible == true
```

Require:

```text
every normal round-trip fill is a normal maker fill
```

Require:

```text
normal round-trip fills
subset of
normal activity fills
```

Special exits must be separately attributed:

```text
terminal close PnL
hard-kill close PnL
emergency close PnL
```

A special-exit fill may close residual inventory but may not become a normal round trip.

---

## 11. Quantity Reconciliation

Require:

```text
sum normal matched quantity
+ remaining normal open quantity
+ special-exit closed quantity
=
total directional fill quantity
```

No quantity may:

```text
disappear
duplicate
enter two round trips
enter both normal and special accounting
```

Partial fills must preserve identity and classification per partial fill.

---

## 12. Fee Reconciliation

Require:

```text
each fill fee charged exactly once
normal maker fees = sum unique normal maker fill fees
terminal fees = sum unique terminal fill fees
hard-kill fees = sum unique hard-kill fill fees
emergency fees = sum unique emergency fill fees
```

Round-trip aggregation references fill fees.

It must not charge them again.

---

# PART III — HAND FIXTURES

## 13. Mandatory Classification Fixtures

Implement before any fresh matrix.

### Fixture 1 — Strict maker fill

Expected:

```text
maker_or_taker = MAKER
fill_trigger = STRICT_TRADE_THROUGH
normal_activity_eligible = true
normal_round_trip_eligible = true
```

### Fixture 2 — Aggressor-at-quote maker fill

Expected:

```text
maker_or_taker = MAKER
fill_trigger = AGGRESSOR_TRADE_AT_QUOTE
normal_activity_eligible = true
normal_round_trip_eligible = true
```

### Fixture 3 — Partial normal maker fill

Expected:

```text
every partial fill retains normal maker classification
quantities reconcile
```

### Fixture 4 — Terminal close

Expected:

```text
maker_or_taker = TAKER
fill_trigger = TERMINAL_EXECUTION
normal activity excluded
normal round trip excluded
```

### Fixture 5 — Hard-kill close

Expected:

```text
maker_or_taker = TAKER
fill_trigger = HARD_KILL_EXECUTION
normal activity excluded
normal round trip excluded
```

### Fixture 6 — Emergency close

Expected:

```text
maker_or_taker = TAKER
fill_trigger = EMERGENCY_EXECUTION
normal activity excluded
normal round trip excluded
```

### Fixture 7 — Normal maker round trip

Expected:

```text
two normal eligible fills
one FIFO normal round trip
activity count = 2
```

### Fixture 8 — Normal fill plus terminal close

Expected:

```text
normal fill enters activity
terminal close excluded
no false normal round trip
```

### Fixture 9 — Normal fill plus hard-kill close

Expected:

```text
normal fill enters activity
hard-kill close excluded
special-exit PnL separate
```

### Fixture 10 — Mixed execution stream

Include:

```text
strict maker
aggressor-at-quote maker
terminal close
hard-kill close
emergency close
```

Expected exact partition and counts.

### Fixture 11 — Invalid maker-terminal combination

Must fail.

### Fixture 12 — Invalid taker-strict combination

Must fail.

### Fixture 13 — Missing trigger

Must fail.

### Fixture 14 — Unknown trigger

Must fail.

### Fixture 15 — Summary cannot overwrite trigger

Must pass immutability test.

### Fixture 16 — FIFO cannot consume special exit as normal

Must pass exclusion test.

If any mandatory classification fixture fails:

```text
MM_V1_3C_EVIDENCE_REPAIR_FAILED
```

Stop before fresh matrix execution.

---

# PART IV — FIXED PROFILE CARRY-FORWARD

## 14. Profile Set

Carry forward the exact eight v1.3B parameter vectors:

```text
BASELINE_CONTROL
WIDER_SPREAD_CONTROL
VOLATILITY_SPREAD_GUARD
FAST_CANCEL_ON_VOLATILITY
TOXIC_FLOW_PAUSE
INVENTORY_REDUCTION_PRIORITY
ONE_SIDED_DEFENSIVE_MODE
COMPOSITE_DEFENSIVE
```

Do not modify:

```text
base parameters
defensive thresholds
pause duration
cancel thresholds
spread multipliers
one-sided mode logic
re-entry cooldown
composite composition
```

Assign new v1.3C IDs and fingerprints while preserving exact values.

Record:

```text
prior profile reference
new profile ID
new fingerprint
exact carried parameters
```

Do not add, remove, replace, or edit profiles.

---

## 15. Fixed Capital and Safety

Use:

```text
capital                     750 USDT
lot size                    0.01 BTC
leverage                    3x
maximum margin utilization  0.80
maximum inventory           0.01 BTC
soft session loss           3%
hard kill drawdown          5%
```

Do not tune or relax.

---

# PART V — FRESH MATRIX

## 16. Refined Severity Ladder

Carry forward the same v1.3B severity definitions exactly:

```text
S2_5
S3_LOW
S3_MID
S3_HIGH
```

Do not change numeric thresholds.

Record exact copied values in the new specification.

---

## 17. Fresh Path Matrix

Freeze:

```text
16 fresh paths
4 paths per severity
```

Required scenario families:

```text
upward toxic trend
downward toxic trend
gap through quote
delayed cancellation with one-sided flow
```

Each path records:

```text
path_id
path_hash
severity
scenario
numeric parameters
market seed
fill seed
source block
generator version
index range
```

Requirements:

```text
new IDs
new hashes
new seeds
new source blocks
no reuse of v1.3B paths
deterministic replay
strict trade-through stress semantics
```

Freeze before the first result.

Run exactly once.

---

# PART VI — ACTIVITY FLOOR

## 18. Frozen Activity Floor

Per profile across S2_5 and S3_LOW combined require:

```text
normal strict maker fills        >= 8
both bid and ask normal fills represented
normal FIFO round trips          >= 2
quote-eligible ticks             > 0
two-sided quote rate             >= 5%
no-quote rate                    <= 85%
represented scenario families    >= 3
```

Only canonical normal activity fills count.

Do not count:

```text
terminal fills
hard-kill fills
emergency fills
touch-only events
probabilistic events
```

---

## 19. Defensive Re-Entry Requirements

A defensive profile must not pass by staying inactive.

Require for any profile considered for support:

```text
at least one normal fill before first defensive activation
at least one normal fill after a completed re-entry
defensive mode exits at least once
profile is not paused for the entire run
profile is not permanently one-sided
```

Additional requirements:

### ONE_SIDED_DEFENSIVE_MODE

```text
both normal fill sides represented across S2_5 and S3_LOW
one-sided mode enters and exits
normal activity remains above floor
represented scenario families >= 3
```

### COMPOSITE_DEFENSIVE

```text
normal fills at S2_5 > 0
normal fills at S3_LOW > 0
normal FIFO round trips across S2_5 and S3_LOW >= 2
completed re-entry observed
no-quote rate <= 85%
```

If Composite has no normal activity at S2_5 or S3_LOW:

```text
MM_V1_3C_COMPOSITE_INACTIVITY_REJECTED
```

unless another defensive profile passes later gates.

---

# PART VII — EVIDENCE AND ATTRIBUTION

## 20. Fill Evidence

Every fill record must include:

```text
canonical classification fields
profile/path/severity
order and quote references
fill trigger event reference
quantity and price
fee
inventory before/after
defensive mode state
```

Every fill must be classified at creation time.

---

## 21. Activity Evidence

For every profile and severity report:

```text
strict maker fills
aggressor-at-quote maker fills
normal activity fills
terminal fills
hard-kill fills
emergency fills
bid/ask normal fills
normal FIFO round trips
scenario coverage
two-sided quote rate
one-sided quote rate
no-quote rate
defensive activation count
pause ticks
one-sided ticks
re-entry count
```

---

## 22. Toxic Fill Timeline

Retain for every normal maker fill at S3_LOW or above:

```text
quote creation tick
quote activation tick
quote side
quote price
quote distance
volatility at creation/fill
inventory at creation/fill
defensive mode at creation/fill
cancel request/completion
trigger event
fill tick
1/5/10-tick markouts
PnL before/after fill
drawdown after fill
```

For every hard kill, retain the full causal chain.

---

## 23. Profile Comparison

Compare all defensive profiles against:

```text
BASELINE_CONTROL
WIDER_SPREAD_CONTROL
```

Required dimensions:

```text
hard-kill count
first hard-kill severity
worst drawdown
net PnL
markout
inventory variance
cancel-latency exposure
normal activity retention
scenario coverage
no-quote rate
```

Do not rank solely by PnL.

---

# PART VIII — GATES

## 24. Gate 0 — Semantic Evidence Integrity

Require:

```text
classification fixtures pass
canonical trigger taxonomy enforced
forbidden combinations rejected
fill partition reconciles
activity identity reconciles
round-trip identity reconciles
quantity identity reconciles
fee identity reconciles
mandatory streams complete
schemas valid
cross-references valid
stream hashes valid
completion hashes valid
quote modes reconcile 100%
deterministic replay passes
unclassified fills == 0
multiply classified fills == 0
all metrics finite
```

Failure:

```text
MM_V1_3C_EVIDENCE_REPAIR_FAILED
```

---

## 25. Gate 1 — Safety Infrastructure

Require:

```text
preventable margin breaches == 0
recorded margin breaches == 0
inventory breaches == 0
unknown margin states == 0
terminal residual inventory == 0
```

Failure:

```text
MM_V1_3C_EVIDENCE_REPAIR_FAILED
```

---

## 26. Gate 2 — Activity

At least one non-control defensive profile must pass the complete activity floor.

If no defensive profile passes:

```text
MM_V1_3C_ACTIVITY_INSUFFICIENT
```

Do not accept no-trade survival.

---

## 27. Gate 3 — Defensive Improvement

At least one activity-qualified defensive profile must improve over both controls in at least three:

```text
hard-kill count
first hard-kill severity
worst drawdown
net PnL
markout
inventory variance
cancel-latency exposure
```

If none improves materially:

```text
MM_V1_3C_NO_RESILIENCE_IMPROVEMENT
```

---

## 28. Gate 4 — Resilience Budget

Carry forward the frozen v1.3B budget:

```text
S2_5:
  hard kills == 0
  worst drawdown <= 3.5%

S3_LOW:
  hard kills == 0
  worst drawdown <= 4.0%

S3_MID:
  hard kills == 0
  worst drawdown <= 5.0%

S3_HIGH:
  boundary diagnostic only
```

Also require:

```text
activity floor passes
re-entry requirements pass
no unexplained hard kill
no catastrophic terminal loss
markout improves versus baseline
```

If an activity-qualified profile improves but fails full budget:

```text
MM_V1_3C_RESILIENCE_IMPROVED_BUT_BUDGET_REJECTED
```

---

## 29. Gate 5 — Support

Assign:

```text
MM_V1_3C_RESILIENCE_EVIDENCE_SUPPORTED
```

only when at least one defensive profile:

```text
passes semantic evidence integrity
passes safety infrastructure
passes activity floor
passes defensive re-entry requirements
improves over both controls
passes S2_5 budget
passes S3_LOW budget
passes S3_MID budget
has no unexplained hard kill
```

This status authorizes only consideration of a separate bounded Optuna protocol.

---

## 30. No Adaptive Search

Do not:

```text
run Optuna
run random search
run Bayesian search
modify profile parameters
append profiles
append paths
change severity
change activity floor
change resilience budget
change capital
change risk limits
```

Freeze the complete protocol before execution.

Execute exactly once.

---

## 31. Required Tests

Keep all retained tests passing.

Add tests for:

1. canonical trigger enum;
2. strict maker classification;
3. aggressor-at-quote maker classification;
4. terminal taker classification;
5. hard-kill taker classification;
6. emergency taker classification;
7. invalid maker-terminal rejection;
8. invalid maker-hard-kill rejection;
9. invalid maker-emergency rejection;
10. invalid taker-strict rejection;
11. missing trigger rejection;
12. unknown trigger rejection;
13. multiply classified fill rejection;
14. trigger assigned at fill creation;
15. summary cannot overwrite classification;
16. fill partition identity;
17. activity identity;
18. special-exit activity exclusion;
19. normal FIFO subset identity;
20. special-exit FIFO exclusion;
21. partial-fill classification;
22. quantity reconciliation;
23. fee reconciliation;
24. normal plus terminal fixture;
25. normal plus hard-kill fixture;
26. mixed-stream fixture;
27. v1.3A writer regression;
28. quote-mode reconciliation;
29. profile carry-forward exactness;
30. no parameter mutation;
31. path disjointness;
32. exact four paths per severity;
33. activity-floor calculation;
34. bid/ask representation;
35. scenario coverage;
36. defensive re-entry requirement;
37. composite lower-severity activity requirement;
38. no-trade rejection;
39. gate ordering;
40. no prior matrix rerun;
41. no Optuna;
42. no validation/holdout;
43. no Git/external access;
44. deterministic replay.

Run:

```text
python -m pytest backtest/tests/ -q
python -m pytest tests/ -q
python -m py_compile <all changed Python files>
```

Do not weaken assertions.

---

## 32. Local Artifact Contract

Write only under:

```text
artifacts/mm_v1_3c_fill_trigger_activity_repair/
```

Required layout:

```text
baseline_<UTC-run-id>/
  folder_inventory.json
  source_map.json
  prior_hashes.json
  test_baseline.json
  baseline_report.md

classification_repair_<UTC-run-id>/
  canonical_trigger_contract.json
  forbidden_combinations.json
  classification_fixtures.jsonl
  reconciliation_results.json
  classification_report.md
  COMPLETED.json

specification_<UTC-run-id>/
  protocol_spec.json
  protocol_spec.sha256
  carried_profiles.json
  capital_policy.json
  fill_model_policy.json
  severity_ladder.json
  stress_path_matrix.json
  activity_floor.json
  resilience_budget.json
  artifact_contract.json
  protocol_spec.md

stress_run_<UTC-run-id>/
  market_events.jsonl
  trade_events.jsonl
  quote_events.jsonl
  defensive_events.jsonl
  margin_events.jsonl
  order_events.jsonl
  fills.jsonl
  round_trips.jsonl
  markouts.jsonl
  hard_kill_events.jsonl
  path_results.jsonl
  stream_manifest.json
  run_manifest.json
  stress_summary.json
  COMPLETED.json

analysis_<UTC-run-id>/
  classification_audit.json
  activity_floor_results.json
  reentry_results.json
  profile_comparison.json
  toxic_fill_timelines.jsonl
  hard_kill_attribution.json
  resilience_results.json
  analysis_report.md

decision_<UTC-run-id>/
  decision.json
  decision.md
```

Requirements:

```text
refuse overwrite
refuse run-ID reuse
write raw evidence during execution
do not backfill after execution
append-only JSONL where applicable
hash every stream
declare empty streams
atomic summary finalization
write COMPLETED.json last
reject incomplete artifacts
record commands and exit codes
record source/specification/profile/path hashes
record every gate and first failure
```

Do not create Optuna, validation, holdout, Git, exchange, balanced-run, or production artifacts.

---

## 33. Required Work Order

### Phase 0 — Baseline

1. inspect local source;
2. verify prior hashes;
3. run retained tests;
4. map fill classification flow;
5. record source fingerprints.

### Phase 1 — Classification repair

1. implement canonical taxonomy;
2. implement classification at fill creation;
3. centralize source of truth;
4. implement forbidden-combination validation;
5. implement fill/activity/round-trip partition checks;
6. run 16 classification fixtures;
7. stop if any fixture fails.

### Phase 2 — Freeze protocol

1. carry forward exact eight profile vectors;
2. freeze capital and safety;
3. copy exact v1.3B severity values;
4. freeze 16 fresh paths;
5. freeze activity floor;
6. freeze re-entry rules;
7. freeze resilience budget;
8. hash exact specification.

### Phase 3 — Final pre-run verification

1. run focused tests;
2. run full retained tests;
3. run compilation;
4. verify path/profile disjointness;
5. modify no covered source afterward.

### Phase 4 — Execute once

1. run all eight profiles;
2. run all 16 paths;
3. retain canonical classification fields;
4. retain defensive and toxic-fill evidence;
5. finalize manifests and hashes;
6. write `COMPLETED.json` last;
7. do not rerun or extend.

### Phase 5 — Analyze

1. apply semantic integrity gate;
2. apply safety;
3. apply activity floor;
4. apply re-entry requirements;
5. compare against both controls;
6. apply resilience budget;
7. assign one final status.

### Phase 6 — Stop

Do not begin Optuna, validation, holdout, or production migration.

---

## 34. Final Report Format

Use:

```text
## Summary
## Folder-Only Work Confirmation
## Closed Evidence Verification
## Fill-Trigger Repair
## Canonical Trigger Contract
## Classification Fixtures
## Fill Partition Reconciliation
## Activity Reconciliation
## FIFO Round-Trip Reconciliation
## Fee and Quantity Reconciliation
## Fixed Profile Carry-Forward
## Capital and Safety Policy
## Severity Ladder
## Fresh Stress Path Matrix
## Artifact Integrity
## Activity Floor
## Defensive Re-Entry
## Baseline Control
## Wider-Spread Control
## Volatility Spread Guard
## Fast Cancel
## Toxic-Flow Pause
## Inventory-Reduction Priority
## One-Sided Defensive Mode
## Composite Defensive
## Profile Comparison
## Toxic Fill Timelines
## Hard-Kill Attribution
## Resilience Budget
## Tests Run
## Final Status
## Files Added
## Files Modified
## Local Artifacts
## Prior Matrix Rerun Status
## Optuna Status
## Validation and Holdout Status
## External Access
## Git Operations
## Remaining Risks
```

Lead with:

```text
final status
classification fixtures passed
misclassified maker fills
unclassified fills
normal activity fills
normal FIFO round trips
profiles passing activity
profiles passing re-entry requirements
profiles improving over controls
profiles passing S2_5/S3_LOW/S3_MID
best defensive profile
preventable margin breaches
inventory breaches
prior matrix rerun status
Optuna/validation/holdout/Git/external status
```

State:

```text
Balanced v1.3 matrix rerun: No
Stress v1.3A matrix rerun: No
Repair v1.3B matrix rerun: No
Optuna ran: No
Validation opened: No
Holdout opened: No
External endpoints contacted: No
Git stage/commit/push: No
Production Default Changes: None
```

---

## 35. Definition of Done

The cycle is complete only when:

- work remains inside the local folder;
- no Git or external operation occurs;
- prior evidence remains unchanged;
- no previous matrix is rerun;
- canonical trigger classification is assigned at fill creation;
- all forbidden combinations fail;
- all classification fixtures pass;
- fill partition reconciles;
- activity identity reconciles;
- normal FIFO identity reconciles;
- fees and quantities reconcile;
- exact eight profile vectors are carried forward;
- fresh paths are frozen;
- activity and re-entry requirements are frozen;
- the matrix executes once;
- no-trade survival is rejected;
- no Optuna, validation, holdout, or parameter tuning occurs;
- one honest final status is assigned.

---

## 36. Final Instruction

First repair fill-trigger normalization and activity reconciliation.

Do not relabel or backfill the closed v1.3B run.

Then execute one fresh matrix with the exact same eight profile parameter vectors.

Do not change defensive parameters.

Do not accept survival through inactivity.

Required progression:

```text
REPAIR CANONICAL FILL CLASSIFICATION
→ PASS CLASSIFICATION FIXTURES
→ VERIFY FILL / ACTIVITY / FIFO PARTITIONS
→ CARRY FORWARD EIGHT EXACT PROFILES
→ FREEZE 16 FRESH PATHS
→ RUN ONCE
→ APPLY ACTIVITY AND RE-ENTRY RULES
→ COMPARE DEFENSIVE PROFILES WITH BOTH CONTROLS
→ APPLY RESILIENCE BUDGET
→ MM_V1_3C_RESILIENCE_EVIDENCE_SUPPORTED
or honest rejection
```
