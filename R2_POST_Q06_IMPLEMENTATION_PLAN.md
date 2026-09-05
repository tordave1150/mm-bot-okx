# R2 post-Q06 offline diagnostic plan

## Confirmed baseline

- Campaign `r2-qualification-campaign-20260904T154500Z` has six credited sessions.
- Q01-Q03 and Q04-Q06 completion manifests were independently hash-verified.
- The six-session checkpoint reports terminal position/open owned orders `0/0`, no live endpoints, no mutation retries, and one special-flatten session.
- Q02's raw session audit is not authoritative for FIFO, fee, or PnL values. The immutable reconciliation record is authoritative: one maker bid fill, zero maker ask fills, zero maker FIFO round trips, normal net PnL `-0.1594042` USDT, and aggregate net PnL `-1.3005432` USDT.

## Decision: current campaign is expired

The campaign identity timestamp is `2026-09-04T15:45:00Z`. Under the frozen six-hour campaign wall it expired at `2026-09-04T21:45:00Z`. This audit occurs after that deadline. Q07-Q12 must not be started, resumed, accepted, retried, or run under any Q01-Q12 identity.

## Proven defect repaired offline

The Q04-Q06 executor enforces a 30-minute per-session boundary but does not contain a reusable cross-run campaign wall admission gate. `okx_demo_r2_campaign_wall_guard.py` now provides a pure, fail-closed gate that accounts for interruption gaps and rejects the exact deadline. A future fresh R2 executor must invoke it before loading credentials, creating an adapter, or making any network request.

## Fill-scarcity finding

Q04-Q06 each exhausted the 60-create budget in roughly 219-223 seconds. With two post-only orders per cycle and a two-second rest, that behavior is consistent with the recorded lifecycle, not proof of a cancel/requote defect. It provides insufficient market exposure to meet the 12-session economic floors: at Q06, one total maker fill, zero ask fills, and zero FIFO maker round trips are far below the final thresholds. No parameter change is authorized by this plan.

## Required next gates

1. Preserve this campaign as immutable NOT_READY/EXPIRED evidence.
2. Obtain authorization for a fresh R1 read-only Demo preflight package with fresh identities.
3. Only after a passed fresh R1, prepare a fresh R2 package. It must embed the campaign wall gate and preserve all frozen risk and economic criteria.
4. Obtain distinct authorization before any network execution. A new campaign must never inherit Q01-Q06 economics or identities.

## Offline verification

Run only with blank credentials and socket denial:

```powershell
python -m pytest tests/test_okx_demo_r2_campaign_wall_guard.py tests/test_okx_demo_r2_canonical_q04_q06_executor.py tests/test_okx_demo_r2_canonical_q01_q03_reconcile.py -q
```

Acceptance requires all tests passing, no socket attempts, and no modification of historical evidence.
