# Current repository status

Last reconciled: 2026-09-05

## Execution state

The repository is a research and safety-validation workspace for an OKX Demo
market-maker. It is not approved for Live trading or production.

The most recent R2 campaign, `r2-campaign-20260905T025807Z`, is closed for
further execution. Session 1 canary and Session 2 completed with terminal
position/open orders of `0/0`. Session 3 did not start because the frozen
six-hour campaign wall-time had insufficient time remaining for a full session.
Its identity must not be resumed or reused.

Authoritative recent evidence:

- `artifacts/r2_canary_runs/r2-canary-run-20260905T045300Z/`
- `artifacts/r2_stage_c_execution/r2-stage-c-run-20260905T033400Z/`
- `artifacts/r0_session2_terminal_label_audit/`
- `artifacts/r0_session3_walltime_gate/`
- `artifacts/r0_fresh_chain_readiness/r0-fresh-chain-readiness-offline-20260905T093430Z/`
- `artifacts/r1_read_only_preflight_preparation/r1-prep-20260905T093701Z/`
- `artifacts/r1_read_only_preflight_runs/r1-preflight-run-20260905T093701Z/`
- `artifacts/r2_economic_campaign_preparation/r2-prep-20260905T095901Z/`
- `artifacts/r2_final_admission_preparation/r2-final-prep-20260905T100401Z/`
- `artifacts/r2_canary_runs/r2-canary-run-20260905T100701Z/`
- `artifacts/r0_canary_checkpoint_evidence_repair/r2-canary-checkpoint-repair-offline-20260905T102100Z/`
- `artifacts/r1_read_only_preflight_preparation/r1-prep-20260905T102601Z/`
- `artifacts/r1_read_only_preflight_runs/r1-preflight-run-20260905T102601Z/`

## Active source paths

The active Demo safety path is built from `market_maker/`, `market_spec.py`,
`fill_tracker.py`, `fill_classification.py`, `okx_execution_safety.py`, the
`okx_demo_*` runtime and evidence modules, and the `okx_fill_restart_*`
reconciliation modules. The currently added continuation entry point is
`okx_demo_r2_s02_s03_continuation.py`.

`main.py`, `trading_bot.py`, `strategy.py`, dashboard modules, `.lumibot/` and
`bot_state.json` belong to the older runtime. They remain in the repository for
reference and must not be treated as the current Demo execution route.

## Known review items

- `okx_demo_r2_canary_executor.py` and `okx_demo_r2_stage_c_executor.py`
  contain checkpoint fields that need independent evidence wiring instead of
  unconditional values before they can be relied upon for a promotion decision.
- The canary exclusion and the current s02–s12 schedule require an explicit
  qualification-denominator decision before any claim of a full 12-session
  economic qualification.
- Historical artifacts are evidence, not disposable cache. Archive with an
  inventory and verified hashes before removing originals.

## Repository hygiene

`REPO_CLEANUP_AUDIT_20260905.md` is the current cleanup inventory. It identifies
rebuildable caches and temporary test output separately from source dependencies
and immutable evidence. Cleanup of files, environments, or artifacts requires
an explicit scoped deletion request.
