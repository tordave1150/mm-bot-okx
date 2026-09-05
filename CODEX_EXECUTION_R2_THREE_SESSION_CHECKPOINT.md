# CODEX EXECUTION BRIEF — R2 Three-Session Economic Checkpoint (OKX Demo)

## 0. Purpose & Scope

This execution brief defines the architecture, parameters, admission gates, and operational boundaries for the **Stage C Three-Session Economic Checkpoint** (`Q01–Q03`) of the `R2 SUCCESSOR_ECONOMIC_CAMPAIGN` on **OKX Demo** only.

### Core Objectives:
1. **Separate Operational Canary**: Formally treat the completed R2 operational canary (`r2-canary-run-20260904T131836Z`) as an independent operational validation run that proved sandbox connectivity, post-only order creation, clock skew adherence, and cancel confirmation. It is **explicitly excluded from the 12-session economic qualification denominator**.
2. **Fresh Qualification Identities**: Allocate fresh conceptual campaign and session identities for **Q01 through Q12**, establishing the clean denominator for full economic qualification.
3. **Bounded Stage C Preparation**: Authorize preparation strictly for **Q01–Q03**. Sessions Q04–Q12 remain planned in the qualification schedule but are strictly **unauthorized for execution**.
4. **Mandatory Per-Session Admission**: Require a fresh, authoritative read-only preflight and reconciliation gate immediately before the first order of **each individual session** (Q01, Q02, and Q03).
5. **Frozen Qualified Candidate**: Inherit the frozen candidate fingerprint `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf` and promoted profile `mm-v1-6-profile-02` (`FEE_AWARE_SPREAD_6`).
6. **Strict Zero-Tuning Policy**: Explicitly forbid any strategy parameter tuning or spread adjustments based on the zero-fill operational canary run.
7. **Three-Session Hard Checkpoint**: Define the 17-point hard safety check + operational/economic quality evaluation following Q03.
8. **Enforce Hard Stop**: Stop immediately after Q03. Zero execution of Q04–Q12. Zero production authorization.
9. **Preparation Boundary**: This brief authorizes offline contracts, preparation packages, and test suites only. Zero session execution is authorized by this brief.

---

# 1. Authority, Precedence & Governance

1. Governed strictly by root `AGENTS.md`. In case of conflict, root `AGENTS.md` supersedes.
2. Follows the fail-closed doctrine established in `CODEX_EXECUTION_R2_FINAL_ADMISSION_AND_CANARY_PREPARATION.md`.
3. Prerequisite packages must be verified and intact:
   - R0 Offline Qualification Closure: `r0-closure-20260904T121733Z`
   - R1 Read-Only Preflight Run: `r1-preflight-run-20260904T121733Z`
   - R2 Final Preparation: `r2-final-prep-20260904T130500Z`
   - R2 Operational Canary Run: `r2-canary-run-20260904T131836Z`
4. This brief is **strictly preparation-only**. A separate, explicit user instruction is required before Q01 can be executed.

---

# 2. Hard Prohibitions

Under this preparation brief, do **NOT**:
- Place, amend, or cancel any OKX Demo order;
- Mutate account configuration (`set_position_mode`, `set_leverage`, `set_margin_mode`);
- Connect to live production endpoints or access production accounts;
- Authorize or execute Sessions Q01, Q02, or Q03;
- Authorize or plan early execution for Sessions Q04–Q12;
- Adjust any strategy control or risk parameter based on the operational canary;
- Include the operational canary in the 12-session economic denominator;
- Perform any git write operations (`git_write_operation: false`).

---

# 3. Prerequisite Evidence Verification

Before any Stage C preparation artifact is sealed, the following evidence chain must be verified:

| Stage | Identifier | Required Status | Verified Digest / Outcome |
| :--- | :--- | :---: | :--- |
| **R0 Closure** | `r0-closure-20260904T121733Z` | `R0_OFFLINE_QUALIFICATION_PASSED` | 120 normal fills, +15.40 USDT PnL |
| **R1 Preflight** | `r1-preflight-run-20260904T121733Z` | `R1_PREFLIGHT_PASSED` | Read-only transport verified, 585ms skew |
| **R2 Final Prep** | `r2-final-prep-20260904T130500Z` | `R2_FINAL_PREPARATION_PASSED` | 16 artifacts, candidate drift = 0 |
| **R2 Canary Run** | `r2-canary-run-20260904T131836Z` | `R2_CANARY_PASSED` | 4 post-only creates, 4 cancels, 17/17 hard checks |
| **Candidate Fingerprint** | `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf` | `MATCH_CONFIRMED` | Intact across all source files |

---

# 4. Operational Canary Separation & Exclusion

### 4.1 Purpose of Operational Canary
The Session 1 Canary (`r2-canary-run-20260904T131836Z`) was executed as a transport and mutation safety canary:
- Verified that CCXT sandbox transport functions under active market conditions;
- Verified that post-only limit orders (`ordType="post_only"`) are accepted without taker execution;
- Verified that order cancellations are authoritatively confirmed;
- Verified that terminal state is completely flat (`position_btc == 0.0`) and empty (`open_orders == 0`);
- Verified that clock skew (`341 ms`) remains well within the `<= 1500 ms` budget.

### 4.2 Exclusion from Economic Qualification Denominator
- The operational canary ran for exactly 2 short cycles (~24 seconds) with conservative wide spreads, resulting in 0 maker fills.
- It was designed as an operational smoke test, not an economic sample.
- **Doctrine**: The 12-session economic qualification campaign must consist of 12 full economic sessions (`Q01–Q12`).
- Including the 2-cycle operational canary in the economic qualification denominator would contaminate fill-rate, markout, and PnL statistics.
- Therefore, the operational canary is formally archived as **Operational Validation Evidence** and **excluded from the 12-session economic qualification denominator**.

---

# 5. Fresh Qualification Campaign & Session Identities (Q01–Q12)

To prevent collision with prior runs or the operational canary, fresh conceptual identities are allocated:

```text
Campaign ID:            r2-qualification-campaign-20260904T133500Z
Run Package ID:         r2-qualification-package-20260904T133500Z
Prepared Scope:         STAGE_C_SESSIONS_Q01_Q03_PREPARATION
Authorized Sessions:    NONE (Preparation Only)
Target Sessions:        Q01, Q02, Q03 (Prepared for future authorization)
Unauthorized Sessions:  Q04 through Q12 (Strictly blocked pending Checkpoint 2)
```

### 5.1 Twelve-Session Qualification Schedule

| Slot | Qualification Index | Canonical Session ID | Slot Status | Execution Boundary |
|:---:|:---:|:---|:---:|:---:|
| 1 | `Q01` | `r2-session-20260904T133500Z-q01:p0:9c1a01f1` | PREPARED | Stage C (Session 1 of 3) |
| 2 | `Q02` | `r2-session-20260904T133500Z-q02:p0:3e4b02a2` | PREPARED | Stage C (Session 2 of 3) |
| 3 | `Q03` | `r2-session-20260904T133500Z-q03:p0:7f8c03d3` | PREPARED | Stage C (Session 3 of 3) ➔ **CHECKPOINT 2** |
| 4 | `Q04` | `r2-session-20260904T133500Z-q04:p0:a1b204e4` | UNAUTHORIZED | Stage D (Blocked) |
| 5 | `Q05` | `r2-session-20260904T133500Z-q05:p0:b2c305f5` | UNAUTHORIZED | Stage D (Blocked) |
| 6 | `Q06` | `r2-session-20260904T133500Z-q06:p0:c3d40606` | UNAUTHORIZED | Stage D (Blocked) |
| 7 | `Q07` | `r2-session-20260904T133500Z-q07:p0:d4e50717` | UNAUTHORIZED | Stage D (Blocked) |
| 8 | `Q08` | `r2-session-20260904T133500Z-q08:p0:e5f60828` | UNAUTHORIZED | Stage D (Blocked) |
| 9 | `Q09` | `r2-session-20260904T133500Z-q09:p0:f6070939` | UNAUTHORIZED | Stage D (Blocked) |
| 10 | `Q10` | `r2-session-20260904T133500Z-q10:p0:0718104a` | UNAUTHORIZED | Stage D (Blocked) |
| 11 | `Q11` | `r2-session-20260904T133500Z-q11:p0:1829115b` | UNAUTHORIZED | Stage D (Blocked) |
| 12 | `Q12` | `r2-session-20260904T133500Z-q12:p0:2930126c` | UNAUTHORIZED | Stage D (Blocked) |

---

# 6. Execution Bounds for Stage C (Q01–Q03 Only)

When future authorization is granted for Stage C:
1. Only **Q01, Q02, and Q03** may be armed and executed.
2. Each session runs sequentially with a mandatory fresh admission check preceding it.
3. No parallel sessions.
4. Maximum session duration: 30 minutes (`1,800,000 ms`) per session.
5. Maximum normal creates: 60 per session (180 total across Q01–Q03).
6. **Hard Stop**: Immediately upon completion of Q03, all quoting halts.
7. Under no circumstances may Q04 be started without passing the Three-Session Checkpoint and receiving a separate explicit authorization.

---

# 7. Frozen Qualified Candidate & Lineage

The strategy configuration is strictly bound to the qualified candidate:

```text
candidate_fingerprint: 1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf
profile_id:            mm-v1-6-profile-02
profile_name:          FEE_AWARE_SPREAD_6
specification_sha256:  e846b9ee21177f2af7dc8a8d114c7a2d9cdd4877a2b04c8b687e4487fa2c8f0f
strategy_fingerprint:  793dc869b8264dd8addde9d59c83ca73210978700b44c6a2055474ce33186e41
```

### 7.1 Canonical Ten Strategy Controls
1. `risk_aversion_gamma`: `0.08`
2. `arrival_decay_k_or_proxy`: `15000.0`
3. `volatility_ewma_decay`: `0.92`
4. `minimum_half_spread_bps`: `6.0` (Promoted fee-aware floor)
5. `maximum_half_spread_bps`: `30.0`
6. `inventory_skew_strength`: `1.0`
7. `imbalance_skew_strength`: `0.25`
8. `minimum_order_lifetime_ticks`: `2`
9. `maximum_order_age_ticks`: `8`
10. `requote_threshold_ticks`: `2`

### 7.2 Frozen Model Inputs
- `volatility_min_samples`: 12
- `volatility_floor`: 0.00002
- `volatility_cap`: 0.004
- `time_horizon_ticks`: 12
- `minimum_size_change_ratio`: 0.25
- `maker_fee_rate`: 0.0002
- `taker_fee_rate`: 0.0005
- `terminal_slippage_bps`: 5.0

---

# 8. Strict Zero-Tuning Policy

> [!CAUTION]
> **No Strategy Parameter Tuning Permitted**:
> - The operational canary produced zero maker fills because it executed only 2 cycles of wide post-only quotes to verify cancellation confirmation.
> - Tuning spreads, reducing minimum half-spread below `6.0 bps`, increasing gamma, or altering order lifetimes to force fills would violate candidate freeze doctrine.
> - The candidate was qualified under 12-session economic soak in R0.
> - **Behavioral Parameter Drift Must Remain Exactly 0**.
> - Any modification to strategy controls invalidates R0 qualification and requires full requalification from scratch.

---

# 9. Frozen Risk Boundary & Safety Limits

| Risk Control | Frozen Value | Scope & Policy |
| :--- | :---: | :--- |
| **Modeled Capital** | 750.0 USDT | Account reference equity |
| **Lot Size** | 0.01 BTC | 1 contract per order |
| **Absolute Inventory Cap** | 0.01 BTC | **Dominant exposure limit** (1 lot). Dominates regardless of account equity, leverage, or any notional capacity. |
| **Max Position Notional** | Informational Only | Derived capacity (e.g. 2250 USDT) cannot widen the 0.01 BTC dominant inventory cap. |
| **Leverage** | 3.0x | Isolated margin |
| **Position Mode** | `net_mode` | Single net position |
| **Margin Mode** | `isolated` | Isolated sub-margin |
| **Session Soft Drawdown** | 22.50 USDT (3.0%) | Order throttling threshold |
| **Session Hard Kill** | 37.50 USDT (5.0%) | Immediate session abort & emergency flatten |
| **Campaign Hard Loss** | 75.00 USDT (10.0%) | Aggregate campaign termination |
| **Session Wall Time** | <= 30 minutes | `1,800,000 ms` per session |
| **Campaign Wall Time** | <= 6 hours | `21,600,000 ms` aggregate across 12 sessions |
| **Campaign Sessions** | <= 12 sessions | Full economic qualification denominator |
| **Session Create Cap** | <= 60 normal creates | Per session normal create budget |
| **Stage C Create Cap** | <= 180 normal creates | Q01 + Q02 + Q03 aggregate create budget |
| **Campaign Create Cap** | <= 720 normal creates | Q01–Q12 aggregate campaign create budget |
| **Book Age Budget** | <= 1000 ms | Quotes cancelled if order book exceeds 1000 ms |
| **Observation Interval** | >= 2000 ms | Minimum interval between quoting cycles |
| **Read Retries** | <= 3 | Maximum retries on idempotent read calls |
| **Mutation Retries** | 0 | Strict fail-closed policy (zero retry loops) |
| **Owned Quote Limits** | Max 1 bid, 1 ask | At most 1 owned bid and 1 owned ask open simultaneously |
| **In-Flight Flatten** | <= 1 | At most 1 unresolved single-flight reduce-only flatten |
| **Special Flatten Ceiling**| <= 2 / 12 sessions | Canonical campaign ceiling for permitted special flatten |

---

# 10. Preserved Terminal-Flatten Lifecycle & Mutation Policy

### 10.1 Quoting vs. Terminal Flatten Lifecycle
1. **Normal Quoting Path**:
   - Quotes must strictly use `post_only` execution (`ordType="post_only"`).
   - Normal-path taker fills are strictly prohibited.
2. **Terminal & Emergency Flatten Path**:
   - The existing single-flight reduce-only terminal/emergency flatten path **remains preserved** under the frozen lifecycle.
   - `automated_flatten_enabled` is **NOT** globally set to false.
   - Permitted special flattens are classified strictly into:
     - `ROUTINE_TERMINAL_CLEANUP`: Clean shutdown of residual inventory at session end.
     - `RISK_EMERGENCY_FLATTEN`: Immediate position exit triggered by hard kill drawdown breach.
   - Canonical campaign special-flatten ceiling remains `<= 2 / 12` sessions across the campaign.
   - Emergency flatten sessions expected = 0.
   - Terminal position must still reconcile to flat (`position_btc == 0.0`) and owned open orders to zero (`open_orders == 0`).
   - At most 1 unresolved single-flight reduce-only flatten may be in flight (`<= 1`).

### 10.2 Zero Mutation Retry Policy
Any ambiguous mutation state must result in an immediate fail-closed abort:
- If `create_order` raises `TimeoutError` or network drop ➔ Do NOT retry. Inspect open orders authoritatively.
- If `cancel_order` raises `TimeoutError` ➔ Poll `fetch_open_orders` until order disappears or fail closed.
- Zero retry loops on mutation endpoints.
- Ambiguous order states trigger `AmbiguousExchangeState` and immediate safe shutdown.

---

# 11. Mandatory Per-Session Admission Reconciliation Gate

Immediately before the first order creation of **each individual session** (Q01, Q02, and Q03), the adapter must perform an authoritative read-only reconciliation:

```text
1. Candidate Fingerprint Check (Exact match: 1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf)
                    ↓
2. OKX Demo Transport Proof (sandboxMode active with simulated-trading header)
                    ↓
3. Exchange Clock Skew Check (absolute_clock_skew_ms <= 1500 ms; NOT 5000 ms)
                    ↓
4. Market & Contract Spec Check (BTC/USDT:USDT linear swap, contract_size == 0.01 BTC)
                    ↓
5. Position Mode Check (position_mode == 'net_mode')
                    ↓
6. Margin Mode Check (margin_mode == 'isolated')
                    ↓
7. Leverage Check (leverage == 3.0x)
                    ↓
8. Startup Position Check (position_btc == 0.0 BTC; zero unowned startup exposure)
                    ↓
9. Open Orders Check (open_orders count == 0; zero foreign/dangling orders)
                    ↓
10. Fills & Trades Cursor Check (Cursor fully reconciled with local state)
                    ↓
11. State Consistency Check (No active unexpected kill-switch, latch, or state-store inconsistency)
                    ↓
R2_ADMISSION_PASS (Session Authorized to Place Post-Only Quotes)
```

If any check fails:
- Return `R2_ADMISSION_BLOCKED` or `R2_RECONCILIATION_REQUIRED`.
- **Zero Automated Account State Mutations** (`account_state_mutation_allowed: false` inside admission).
- Halt immediately without attempting to repair or alter account settings.

---

# 12. Stage C Three-Session Checkpoint Protocol (Checkpoint 2)

Following the completion of Q03, the **Stage C Three-Session Checkpoint** is evaluated across the aggregate evidence of Q01, Q02, and Q03.

> [!IMPORTANT]
> **Early Regression & Safety Checkpoint**:
> - The Q01–Q03 checkpoint is an early regression and safety checkpoint, **NOT** the final 12-session qualification gate.
> - Full campaign economic floors (e.g., >= 24 normal fills, >= 8 FIFO round trips) are **not** required after only three sessions.
> - Report maker fills, FIFO round trips, PnL, work-off behavior, routine cleanup, and emergency flatten counts as **diagnostics**.
> - Hard-stop triggers **only** for safety/reconciliation breaches or systemic execution defects.

### 12.1 Seventeen Hard Safety Invariants (Zero Tolerance)
1. `01_fresh_admission_passed_all_sessions`: Fresh admission passed before Q01, Q02, and Q03 with zero admission account mutations.
2. `02_candidate_fingerprint_intact`: Exact match `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf`; behavioral parameter drift = 0.
3. `03_terminal_position_flat_all_sessions`: `position_btc == 0.0` at the end of each session.
4. `04_terminal_owned_orders_zero_all_sessions`: `open_orders == 0` at the end of each session.
5. `05_unknown_fills_zero_all_sessions`: `0` unmapped or unknown fills across all 3 sessions.
6. `06_unclassified_events_zero`: `0` unclassified execution events.
7. `07_mutation_ambiguity_zero`: `0` unresolvable mutation events.
8. `08_unresolved_flatten_zero`: `0` unresolved emergency/routine flatten orders at session close; `<= 1` in flight during lifecycle.
9. `09_inventory_cap_breaches_zero`: Absolute inventory never exceeded `0.01 BTC` (dominant exposure limit; never 0.05 BTC).
10. `10_owned_order_count_breaches_zero`: Never exceeded 1 owned bid and 1 owned ask simultaneously.
11. `11_normal_create_budget_breaches_zero`: Normal creates `<= 60` per session, `<= 180` across Stage C.
12. `12_normal_path_post_only_enforced`: Normal quotes strictly post-only maker execution; zero normal-path taker fills.
13. `13_special_flatten_ceiling_adhered`: Permitted special flattens classified as `ROUTINE_TERMINAL_CLEANUP` or `RISK_EMERGENCY_FLATTEN`; special-flatten sessions `<= 2 / 12` campaign ceiling.
14. `14_loss_guards_respected`: Session soft drawdown `<= 22.50 USDT`, session hard kill `<= 37.50 USDT`, aggregate campaign loss `<= 75.00 USDT` (no 25.00 USDT cap).
15. `15_exact_accounting_reconciled_all_sessions`: Balance changes reconcile with fill ledger and fee attribution.
16. `16_clock_skew_and_book_safety_respected`: Clock skew `<= 1500 ms`, book age `<= 1000 ms`, observation interval `>= 2000 ms`.
17. `17_zero_mutation_retries_and_live_denial`: Exactly `0` mutation retries, `0` live endpoint calls, `0` account configuration mutations.

### 12.2 Operational & Economic Diagnostic Metrics
- **Normal Maker Fills**: Observed maker fills across Q01–Q03 (Diagnostic; full campaign floor >= 24 evaluated at session 12).
- **Bid/Ask Fill Balance**: Observed ratio of bid to ask fills (Diagnostic).
- **FIFO Round Trips**: Completed buy/sell cycles (Diagnostic; full campaign floor >= 8 evaluated at session 12).
- **Maker Work-Off Behavior**: Eligible episodes, maker-resolved episodes, work-off resolution rate.
- **Routine Terminal Cleanup**: Count of permitted routine cleanups (Classified under permitted special flatten).
- **Risk Emergency Flatten**: Count of emergency flattens (Expected 0).
- **Net Realized PnL**: Normal PnL and special PnL breakdown vs loss limits.
- **Systemic Defect Audit**: Verification that execution does not exhibit adverse latency loops or race defects.

---

# 13. Checkpoint Decision Model

- **`R2_THREE_SESSION_CHECKPOINT_PASSED`**:
  All 17 hard safety checks pass, and diagnostic metrics confirm absence of systemic execution defects. The campaign becomes eligible for Stage D (Sessions Q04–Q12) pending explicit future user authorization.
- **`R2_THREE_SESSION_CHECKPOINT_FAILED`**:
  Any hard safety check fails, cumulative loss exceeds 75.00 USDT, or an obvious systemic execution defect is revealed. Execution permanently halts. No further sessions permitted.

---

# 14. Terminal Hard Stop Enforcement

```text
Stage C Complete (Q01, Q02, Q03)
            ↓
Evaluate 3-Session Checkpoint
            ↓
Emit Checkpoint Evidence & Hashes
            ↓
HARD STOP (Session Q04 is BLOCKED)
            ↓
Require Explicit User Authorization for Stage D
```

Under this protocol:
- `sessions_executed: 0` during preparation.
- When Stage C is executed in the future: `sessions_executed: 3`, `session_4_started: false`.
- `q04_q12_execution_authorized: false`.
- `production_authorized: false`.

---

# 15. Required Offline Test Requirements (Reqs 01–20)

The offline preparation test suite must verify:
1. `Req-01`: Prerequisite evidence intact (R0, R1, R2 prep, R2 canary).
2. `Req-02`: Candidate fingerprint matches `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf`.
3. `Req-03`: Candidate drift audit reports 0 behavioral drift.
4. `Req-04`: Operational canary excluded from qualification denominator.
5. `Req-05`: Qualification schedule allocates exactly 12 fresh slots (Q01–Q12).
6. `Req-06`: Session nonces for Q01–Q12 are cryptographically unique with 0 collisions.
7. `Req-07`: Execution scope restricted strictly to Q01–Q03.
8. `Req-08`: Sessions Q04–Q12 marked strictly unauthorized.
9. `Req-09`: Per-session admission contract requires fresh pre-order checks.
10. `Req-10`: Admission gate prohibits automated account mutations.
11. `Req-11`: Frozen risk boundary preserved (750 USDT capital, 0.01 lot, 3x leverage, 75 USDT loss cap).
12. `Req-12`: No-tuning attestation verified.
13. `Req-13`: Three-Session Checkpoint contract enforces all 17 hard safety checks.
14. `Req-14`: Checkpoint failure blocks Q04 continuation.
15. `Req-15`: Account mutation methods unreachable.
16. `Req-16`: Live endpoints unreachable and denied.
17. `Req-17`: Zero secrets serialized in any artifact.
18. `Req-18`: Preparation mode performs zero order mutations.
19. `Req-19`: Strict socket denial enforced during tests (`_OfflineSocketGuard`).
20. `Req-20`: Terminal completion marker valid with complete SHA-256 manifest.

---

# 16. Required Final Codex Report Format

The final report must return:
1. **Prerequisite & Canary Audit**: Proof of prerequisite chain and operational canary exclusion.
2. **Candidate Verification**: Fingerprint check and zero-drift proof.
3. **Qualification Identity Manifest**: Table of Q01–Q12 slots, session IDs, and authorization boundaries.
4. **Per-Session Admission Contract**: Description of the fresh pre-order gate.
5. **Stage C Three-Session Checkpoint Specification**: 17 hard safety checks + operational metrics.
6. **Risk Boundary & No-Tuning Attestation**: Preservation of frozen inputs.
7. **Offline Test Summary**: Results of the 20 deterministic requirements.
8. **Terminal Status**: `R2_THREE_SESSION_PREPARATION_PASSED`, `Q01_Q03_ELIGIBLE_PENDING_EXPLICIT_AUTHORIZATION`.
9. **Hard Stop Assertions**: `sessions_executed: 0`, `q01_q03_execution_authorized: false`, `production_authorized: false`.
