# CODEX EXECUTION BRIEF — R1 Read-Only OKX Demo Preflight Execution

## 0. Purpose

This execution brief defines the execution protocol and verification gates for the **R1 Read-Only OKX Demo Preflight** phase of the `tordave1150/mm-bot-okx` successor market-maker stack.

The goals are:

1. Consume the frozen R0 qualification closure package (`r0-closure-20260904T121733Z`);
2. Consume the offline R1 preparation package (`r1-prep-20260904T121733Z`);
3. Record explicit user authorization transitioning the system from `R1_PREPARED_AWAITING_EXPLICIT_AUTHORIZATION` to `R1_PREFLIGHT_AUTHORIZED`;
4. Execute authoritative read-only observation and reconciliation against the OKX Demo (sandbox) environment using authorized credentials in memory only;
5. Validate all frozen environment parameters (sandbox headers, clock skew $\le 1,500$ ms, contract metadata, leverage, isolated margin mode, net position mode, zero foreign exposure, zero open orders, sufficient USDT margin equity);
6. Guarantee and audit **zero order mutations** and **zero account mutations**;
7. Produce an immutable, tamper-evident R1 preflight execution evidence package;
8. Enforce a hard stop before any R2 soak, multi-session campaign, or order mutation.

This document **does not authorize R2 execution**.

---

# 1. Authority, Scope & Strict Boundaries

Before executing this brief:

1. Comply with the current root `AGENTS.md`;
2. Obey the strictest applicable safety rule;
3. Verify that the candidate fingerprint matches `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf`.

### Scope of Authorization:
The user has explicitly authorized:
- Verification of prepared R1 identities and contracts;
- In-memory credential loading from the local environment;
- Network connection to the OKX Demo sandbox environment;
- Read-only queries strictly confined to the 12 endpoints on the `read_endpoint_allowlist.json`;
- Exchange time and clock-skew validation;
- Market and instrument specification verification against frozen `MarketSpec`;
- Account mode and margin configuration verification;
- Position, order, and trade snapshot reconciliation;
- Emission of the immutable R1 evidence package.

### Strictly Forbidden Operations:
This brief strictly forbids and fails closed upon:
- Any call to `create_order` (post-only, limit, or market);
- Any call to `cancel_order` or `cancel_all_owned`;
- Any call to `submit_emergency_flatten` or reduce-only market orders;
- Any call to `set_position_mode`, `set_leverage`, `transfer`, or `withdrawal`;
- Any access to live / production exchange endpoints (`live_mode_available: false`);
- Any execution of Optuna optimization or parameter sweeping;
- Any opening of validation or holdout data partitions;
- Any Git write operations (`git commit`, `git push`, `git tag`, `git checkout -b`);
- Any automatic progression to R2 soak or multi-session trading.

---

# 2. Immutable Identity Bindings

The execution must strictly bind to the fresh conceptual identities established during R1 preparation:

| Identifier | Canonical Value |
|---|---|
| `r0_closure_ref` | `r0-closure-20260904T121733Z` |
| `r0_candidate_fingerprint` | `1d618d809a004001d6be5ad35f5865292b81c6cd4d7a7713f21bb6cde9636ccf` |
| `r1_package_id` | `r1-package-20260904T121733Z` |
| `r1_run_id` | `r1-preflight-run-20260904T121733Z` |
| `r1_session_id` | `r1-preflight-session-20260904T121733Z:p0:319720120a59` |
| `arm_token` | `OKX_DEMO:r1-preflight-session-20260904T121733Z:p0:319720120a59` |
| `symbol` | `BTC/USDT:USDT` |
| `execution_mode` | `OKX_DEMO` |
| `transport_environment` | `OKX_DEMO_SANDBOX` |

Identity collision or reuse of predecessor run IDs will fail closed immediately.

---

# 3. Read Endpoint Allowlist

All remote network calls during R1 preflight must belong to the approved read-only allowlist:

```text
1.  fetch_markets            - Enumerate available markets and quarantine invalid instruments
2.  set_markets              - Cache validated markets locally without network mutation
3.  fetch_market_info        - Read contract specification and verify against frozen MarketSpec
4.  fetch_time               - Exchange server timestamp for clock-skew budget validation
5.  fetch_balance            - Authoritative account equity and free margin readings
6.  privateGetAccountConfig  - Authoritative account configuration (uid and posMode)
7.  fetch_positions          - Authoritative position state for frozen symbol
8.  fetch_open_orders        - Authoritative open orders snapshot for frozen symbol
9.  fetch_my_trades          - Authoritative trade execution and fill cursor reconciliation
10. fetch_leverage           - Read-only verification of leverage configuration
11. fetch_trading_fee        - Authoritative maker/taker fee tier verification
12. fetch_position_mode      - Read-only verification of net mode configuration
```

**Policy**: `DENY_UNKNOWN`. Any invocation of an endpoint outside this allowlist fails closed immediately.

---

# 4. Prohibited Mutation Denylist

The following operations are intercepted and prohibited by runtime guards:

| Prohibited Method | Classification | Fail-Closed Policy |
|---|---|---|
| `create_order` | Order Mutation | FAIL_CLOSED_MUTATION_DENIED |
| `cancel_order` | Order Mutation | FAIL_CLOSED_MUTATION_DENIED |
| `cancel_all_owned` | Order Mutation | FAIL_CLOSED_MUTATION_DENIED |
| `submit_emergency_flatten` | Market Liquidation | FAIL_CLOSED_MUTATION_DENIED |
| `set_position_mode` | Account Configuration | FAIL_CLOSED_MUTATION_DENIED |
| `set_leverage` | Account Configuration | FAIL_CLOSED_MUTATION_DENIED |
| `transfer` | Asset Transfer | FAIL_CLOSED_MUTATION_DENIED |
| `withdrawal` | Asset Withdrawal | FAIL_CLOSED_MUTATION_DENIED |
| `unknown_category` | Unrecognized Call | FAIL_CLOSED_ENDPOINT_DENIED |

---

# 5. Credential Contract & Zero-Leakage Hygiene

The executor must adhere to strict credential hygiene:

1. **Required Environment Variables**:
   - `OKX_API_KEY`
   - `OKX_SECRET`
   - `OKX_PASSPHRASE`
2. **In-Memory Use Only**:
   - Credentials are read from `.env` or process environment directly into the CCXT client instance.
   - Credentials must never be logged, printed to stdout/stderr, written to disk, or serialized into any evidence artifact.
3. **Artifact Sanitization**:
   - `account_uid` must be masked as `"present"` in all public dictionaries.
   - Post-run regex audit verifies that no credential values or raw identifiers appear anywhere in `artifacts/r1_read_only_preflight_runs/`.

---

# 6. Transport & Sandbox Verification

Before making any account-level calls, the transport layer must verify:

1. `exchange.options["sandboxMode"] == True`
2. `exchange.headers["x-simulated-trading"] == "1"`
3. `config.mode == ExecutionMode.OKX_DEMO`
4. `LIVE` mode is completely unavailable (`ExecutionMode.LIVE` validation raises `DemoAdapterError`).

Failure of any transport condition halts execution before remote queries proceed.

---

# 7. Authoritative Preflight Reconciliation Gates

Execution passes preflight only if every gate is satisfied:

### 7.1 Clock Skew Budget
- $\Delta t = \max(|t_{\text{before}} - t_{\text{server}}|, |t_{\text{after}} - t_{\text{server}}|) \le 1,500\text{ ms}$.
- If $\Delta t > 1,500\text{ ms}$, raise `ClockSkewBudgetError` and fail closed.

### 7.2 Market Specification
- Instrument ID: `BTC-USDT-SWAP` (`BTC/USDT:USDT`).
- Contract type: Linear USDT perpetual swap (`linear: true`, `inverse: false`).
- Contract size: Exactly `0.01` BTC per contract.
- Price tick: `0.1` USDT. Amount step: `1` contract.

### 7.3 Account Mode & Leverage
- Account Position Mode: `net_mode`.
- Margin Mode: `isolated`.
- Leverage: Exactly `3.0x`.

### 7.4 Exposure & Order Reconciliation
- Startup Position: Exactly `0.0` BTC (no unowned or pre-existing inventory).
- Startup Open Orders: Exactly `0` orders (no unowned, foreign, or dangling orders).
- Total USDT Equity: $> 100.0$ USDT (sufficient margin capacity).

---

# 8. Failure Matrix & Remediation Actions

| Failure Scenario | Error Raised | Terminal Decision |
|---|---|---|
| Live mode requested | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Missing/blank credentials | `DemoAdapterError` | `R1_PREFLIGHT_BLOCKED` |
| Wrong arm token | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Wrong symbol | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Wrong leverage | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Wrong margin mode | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Sandbox not enabled | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Header `x-simulated-trading` missing | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Order mutation attempt | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Unknown endpoint call | `DemoAdapterError` | `R1_SAFETY_FAILED` |
| Nonzero position at startup | `DemoAdapterError` | `R1_RECONCILIATION_REQUIRED` |
| Open orders at startup | `DemoAdapterError` | `R1_RECONCILIATION_REQUIRED` |
| Clock skew $> 1,500$ ms | `ClockSkewBudgetError` | `R1_PREFLIGHT_BLOCKED` |
| Network / DNS unreachable | `DemoAdapterError` | `R1_PREFLIGHT_BLOCKED` |

---

# 9. Evidence Artifacts Structure

All evidence from the preflight execution is written to an immutable, non-overwriting run directory:

```text
artifacts/
└─ r1_read_only_preflight_runs/
   └─ r1-preflight-run-20260904T121733Z/
      ├─ candidate_identity.json
      ├─ preflight_authorization.json
      ├─ transport_audit.json
      ├─ endpoint_audit.json
      ├─ account_snapshot.json
      ├─ reconciliation_audit.json
      ├─ safety_audit.json
      ├─ preflight_decision.json
      ├─ completion_hashes.json
      └─ R1_PREFLIGHT_PASSED.json  (terminal marker written last)
```

### Required Fields in `R1_PREFLIGHT_PASSED.json`:
- `status`: `"R1_PREFLIGHT_PASSED"`
- `run_id`: `"r1-preflight-run-20260904T121733Z"`
- `session_id`: `"r1-preflight-session-20260904T121733Z:p0:319720120a59"`
- `create_attempts`: `0`
- `cancel_attempts`: `0`
- `flatten_attempts`: `0`
- `account_mutation_attempts`: `0`
- `live_endpoint_attempts`: `0`
- `r2_authorized`: `false`
- `production_authorized`: `false`
- `git_write_operation`: `false`
- `completion_hashes_sha256`: `<valid_digest>`

---

# 10. Terminal State & R2 Boundary

Upon successful completion of R1 Read-Only Preflight:

```text
R1 Terminal Decision: R1_PREFLIGHT_PASSED
Next Informational State: R2_ELIGIBLE_PENDING_SEPARATE_EXPLICIT_AUTHORIZATION
```

**Hard Stop Protocol**:
1. Preflight execution terminates immediately after writing the terminal marker.
2. R2 multi-session soak or trading campaign execution is **NOT** authorized.
3. Any future R2 phase requires:
   - Fresh non-overwriting identities;
   - New arm token;
   - Separate, explicit human authorization.
