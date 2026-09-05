# CODEX EXECUTION BRIEF — R2 Successor Economic Campaign Execution (OKX Demo)

## 0. Purpose & Scope

This execution brief defines the architecture, parameters, safety gates, and operational boundaries for the **R2 Successor Economic Campaign** (`SUCCESSOR_ECONOMIC_CAMPAIGN`) on **OKX Demo** only.

The goals of this phase are:

1. Consume and verify prerequisite evidence:
   - Frozen R0 Offline Qualification Closure package (`r0-closure-20260904T121733Z`);
   - Completed and passed R1 Read-Only OKX Demo Preflight run (`r1-preflight-run-20260904T121733Z`);
   - Intact candidate fingerprint: `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf`.
2. Generate fresh, non-overwriting R2 conceptual identities for the campaign and its 12 constituent sessions;
3. Bind the existing, immutable frozen risk boundary (`frozen_risk_specification.json`, `CampaignLimits`, and `PromotedProfile`);
4. Define the 12-session lifecycle, session transition protocol, work-off mechanics, and qualification criteria;
5. Produce the complete, tamper-evident R2 offline preparation package (`artifacts/r2_economic_campaign_preparation/r2-prep-20260904T124817Z/`);
6. **Enforce a strict hard stop before R2 execution**.

> [!CAUTION]
> **DO NOT EXECUTE R2 YET.**
> This document authorizes **campaign preparation, contract freezing, and session planning only**.
> It does NOT authorize starting sessions, sending orders, connecting to OKX Demo for trading, or any state mutation.
> A separate, explicit user authorization instruction is strictly required before any session can be armed or executed.

---

# 1. Authority, Precedence & Environment Boundaries

### 1.1 Governance
- Governed strictly by root `AGENTS.md`. In case of conflict, root `AGENTS.md` supersedes.
- Follows the fail-closed doctrine established in `EXECUTION_R0_CLOSURE_R1_PREFLIGHT_PREPARATION.md` and `EXECUTION_R1_READ_ONLY_PREFLIGHT.md`.

### 1.2 Environment & Exchange Boundary
- **Transport Environment**: `OKX_DEMO_SANDBOX` only.
- **Mandatory Exchange Headers**: `x-simulated-trading: 1`, `sandboxMode: True`.
- **LIVE / Production Access**: **STRICTLY PROHIBITED**. `ExecutionMode.LIVE` is permanently disabled in the adapter.
- **Optuna / Grid Search**: Completely disabled.
- **Validation / Holdout Data**: Closed and untouched.
- **Git Operations**: Zero write operations (`git_write_operation: false`).

---

# 2. Prerequisite Evidence Verification

Before any preparation artifact is finalized, the following verified evidence must be proven:

| Prerequisite Phase | Identifier | Required Status | Verified Digest / Metric |
|---|---|---|:---:|
| **R0 Offline Closure** | `r0-closure-20260904T121733Z` | `R0_OFFLINE_QUALIFICATION_PASSED` | `120 normal fills`, `+15.40 USDT PnL` |
| **R1 Preflight Run** | `r1-preflight-run-20260904T121733Z` | `R1_PREFLIGHT_PASSED` | `0 orders`, `0 pos`, `585ms skew` |
| **Candidate Fingerprint** | `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf` | `MATCH_CONFIRMED` | Intact across 4 core files |

---

# 3. Fresh R2 Conceptual Identities

All identities for R2 must be fresh, unique, and strictly non-overwriting. No identity from prior runs, predecessor campaigns, or mock suites may be reused:

```text
r2_package_id:               r2-package-20260904T124817Z
r2_campaign_id:              r2-campaign-20260904T124817Z
r2_run_id:                   r2-campaign-run-20260904T124817Z
r2_prep_id:                  r2-prep-20260904T124817Z
r2_campaign_session_id:      r2-campaign-session-20260904T124817Z:p0:7a9e286f01bb
expected_campaign_arm_token: OKX_DEMO:r2-campaign-session-20260904T124817Z:p0:7a9e286f01bb
```

### 3.1 Twelve-Session Slot Schedule

The economic campaign comprises exactly 12 sequential session slots:

| Slot | Session Index | Canonical Session ID | Slot Timeout | Max Normal Creates |
|:---:|:---:|---|:---:|:---:|
| 1 | `s01` | `r2-session-20260904T124817Z-s01:p0:d81a9f01` | 30 min | 60 |
| 2 | `s02` | `r2-session-20260904T124817Z-s02:p0:d81a9f02` | 30 min | 60 |
| 3 | `s03` | `r2-session-20260904T124817Z-s03:p0:d81a9f03` | 30 min | 60 |
| 4 | `s04` | `r2-session-20260904T124817Z-s04:p0:d81a9f04` | 30 min | 60 |
| 5 | `s05` | `r2-session-20260904T124817Z-s05:p0:d81a9f05` | 30 min | 60 |
| 6 | `s06` | `r2-session-20260904T124817Z-s06:p0:d81a9f06` | 30 min | 60 |
| 7 | `s07` | `r2-session-20260904T124817Z-s07:p0:d81a9f07` | 30 min | 60 |
| 8 | `s08` | `r2-session-20260904T124817Z-s08:p0:d81a9f08` | 30 min | 60 |
| 9 | `s09` | `r2-session-20260904T124817Z-s09:p0:d81a9f09` | 30 min | 60 |
| 10 | `s10` | `r2-session-20260904T124817Z-s10:p0:d81a9f10` | 30 min | 60 |
| 11 | `s11` | `r2-session-20260904T124817Z-s11:p0:d81a9f11` | 30 min | 60 |
| 12 | `s12` | `r2-session-20260904T124817Z-s12:p0:d81a9f12` | 30 min | 60 |

---

# 4. Frozen Risk Boundary & Strategy Controls

R2 execution strictly inherits the frozen profile `mm-v1-6-profile-02` and canonical `CampaignLimits`. No parameter tuning or modification is permitted.

### 4.1 Ten Frozen Strategy Controls
1. `fixed_lot_size_btc`: `0.01` (1 contract)
2. `maximum_inventory_lots`: `1` (0.01 BTC)
3. `maximum_order_age_ticks`: `12`
4. `minimum_order_lifetime_ticks`: `2`
5. `requote_threshold_ticks`: `2`
6. `time_horizon_ticks`: `100`
7. `risk_aversion_gamma`: `0.1`
8. `inventory_skew_strength`: `0.5`
9. `imbalance_skew_strength`: `0.3`
10. `volatility_cap`: `0.005`

### 4.2 Frozen Campaign & Session Risk Limits
- **Aggregate Campaign Hard Loss**: `75.00 USDT` (Breach causes immediate permanent campaign shutdown)
- **Session Hard Loss Drawdown**: `37.50 USDT` (Breach triggers emergency shutdown and session failure)
- **Session Soft Loss Drawdown**: `22.50 USDT` (Triggers graceful quote-pulling and work-off mode)
- **Maximum Absolute Inventory**: `0.01 BTC` (1 contract maximum at all times)
- **Maximum Owned Bid Orders**: `1`
- **Maximum Owned Ask Orders**: `1`
- **Maximum Unresolved Flatten Attempts**: `1`
- **Maximum Mutation Retries**: `0` (Zero mutation retries; fail closed immediately on failure)
- **Maximum Read Retries**: `3`
- **Clock Skew Budget**: `1,500 ms`
- **Maximum Campaign Wall Clock**: `6 hours` (21,600,000 ms)
- **Maximum Session Wall Clock**: `30 minutes` (1,800,000 ms)

### 4.3 Market Specification
- **Symbol**: `BTC/USDT:USDT` (`BTC-USDT-SWAP`)
- **Contract Type**: Linear USDT Perpetual Swap (`linear: true`, `inverse: false`)
- **Contract Size**: `0.01 BTC`
- **Price Tick**: `0.1 USDT`
- **Amount Step**: `1 contract` (0.01 BTC)
- **Leverage**: `3.0x`
- **Margin Mode**: `isolated`
- **Position Mode**: `net_mode`

---

# 5. Canonical Campaign Qualification Floors

To achieve qualification upon future completion, the aggregate campaign evidence must satisfy every floor:

```text
1. normal_maker_fills >= 24             (Canonical fill floor)
2. bid_maker_fills >= 8                (Canonical bid floor)
3. ask_maker_fills >= 8                (Canonical ask floor)
4. fill_balance_ratio >= 0.60           (Symmetric fill balance)
5. fifo_maker_round_trips >= 8          (Canonical round-trip floor)
6. special_flatten_sessions <= 2 / 12   (Canonical gate: <= 16.67% <= 20%)
7. emergency_flatten_sessions == 0      (Zero emergency flattens)
8. unowned_orders_detected == 0         (Zero unowned orders)
9. unowned_positions_detected == 0      (Zero unowned exposure)
10. aggregate_net_pnl > 0.0 USDT        (Positive net economic viability)
```

---

# 6. Session Lifecycle & Execution State Machine

Each of the 12 sessions operates through a deterministic 7-stage state machine:

```text
[1. PRE-SESSION RECONCILIATION]
  └─ Read position (assert 0.0 BTC)
  └─ Read open orders (assert 0)
  └─ Verify clock skew (<= 1500ms)
  └─ Check free equity (>= 100 USDT)
         ↓
[2. SESSION ARMING]
  └─ Verify exact arm token: OKX_DEMO:<session_id>
  └─ Latch session start timestamp
         ↓
[3. NORMAL QUOTING PHASE]
  └─ Post-only two-sided quotes (1 bid, 1 ask)
  └─ Respect min_order_lifetime and max_order_age
  └─ Cancel-replace on requote threshold
         ↓
[4. INVENTORY WORK-OFF MODE (ON FILL)]
  └─ Enter maker work-off: post-only skew to reduce absolute inventory
  └─ Invariant: work-off mode NEVER increases absolute inventory
         ↓
[5. TERMINAL WORK-OFF & CLEANUP]
  └─ Session budget/time limit reached: unquote opposite side
  └─ Work off residual inventory via maker post-only quotes
  └─ If within special flatten window and inventory remains: controlled reduce-only markout
         ↓
[6. POST-SESSION RECONCILIATION]
  └─ Verify 0 open orders, 0 exposure
  └─ Attribute fills via FIFO accounting
  └─ Record session PnL, markouts, and work-off resolution
         ↓
[7. INTER-SESSION COOLDOWN & PERSISTENCE]
  └─ Write immutable session artifacts
  └─ Update aggregate campaign accumulator
  └─ Hand over state to Campaign Supervisor
```

---

# 7. Fail-Closed Handling & Safety Matrix

| Condition | Immediate Action | Campaign Result |
|---|---|---|
| Live mode requested | Reject process startup | `CAMPAIGN_SAFETY_FAILED` |
| Missing/blank credentials | Stop pre-connection | `CAMPAIGN_BLOCKED` |
| Token / Identity mismatch | Refuse session arming | `CAMPAIGN_SAFETY_FAILED` |
| Clock skew $> 1,500\text{ ms}$ | Immediate quote halt, wait/cancel | `CAMPAIGN_BLOCKED` |
| Unowned position at startup | Halt before quotes placed | `RECONCILIATION_REQUIRED` |
| Foreign/unowned order detected | Halt immediately | `RECONCILIATION_REQUIRED` |
| Soft drawdown breach ($> 22.50\text{ USDT}$) | Quote halt, enter work-off only | `SESSION_SOFT_STOP` |
| Hard drawdown breach ($> 37.50\text{ USDT}$) | Emergency cancel & flatten | `SESSION_HARD_STOP` |
| Aggregate hard loss ($> 75.00\text{ USDT}$) | Permanent campaign shutdown | `CAMPAIGN_FAILED_DRAWDOWN` |
| Special flatten count $> 2$ | Gate failure | `QUALIFICATION_FAILED` |
| Mutation call failure | Immediate halt (no retries) | `CAMPAIGN_SAFETY_FAILED` |

---

# 8. Credential & Secrets Hygiene

- Credentials are read into memory only at the time of execution.
- No credential values (`apiKey`, `secret`, `password`, `passphrase`) may ever appear in any artifact, JSON file, markdown report, or log.
- Public account snapshots must mask `account_uid` as `"present"`.
- Every generated artifact is subject to automated regex leakage audits.

---

# 9. Offline Preparation Artifact Package

The preparation package is stored in:
`artifacts/r2_economic_campaign_preparation/r2-prep-20260904T124817Z/`

```text
artifacts/r2_economic_campaign_preparation/r2-prep-20260904T124817Z/
  ├─ candidate_identity.json
  ├─ prerequisite_evidence_manifest.json
  ├─ r2_identity.json
  ├─ r2_authorization_contract.json
  ├─ frozen_risk_specification.json
  ├─ session_schedule.json
  ├─ endpoint_permissions.json
  ├─ qualification_expectations.json
  ├─ failure_matrix.json
  ├─ credential_contract.json
  ├─ demo_transport_contract.json
  ├─ expected_evidence_schema.json
  ├─ completion_hashes.json
  └─ R2_PREPARATION_COMPLETED.json   (terminal marker written last)
```

---

# 10. Required User Authorization Boundary (HARD STOP)

The preparation process transitions the system to:

```text
Status: R2_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION
```

**Acceptance Boundary**:
1. All preparation artifacts must be written offline with **zero network connections**, **zero credential reads**, **zero order placements**, and **zero state mutations**.
2. **Hard Stop Enforced**: Neither the supervisor nor any executor may start Session 1 or contact OKX Demo.
3. Execution of R2 requires a separate, explicit user instruction authorizing the armed execution of the campaign.
