# Repository cleanup audit — 2026-09-05

## Scope and limits

Read-only inventory of root files, directory sizes, selected source dependencies,
README/classification documents and current continuation code. No deletion,
movement, credential reads, network calls, bot execution or tests performed.
This is a triage audit, not a complete dependency graph or proof of production readiness.
Unreadable paths exist under artifacts/, scratch/ and tmp_pytest/; sizes are lower bounds.
Sizes below are MiB (bytes / 1,048,576).

## What is taking space

| Path | Readable size | Recommendation |
|---|---:|---|
| artifacts/ | 1,653.15 MiB; 11,286 files | Retain dependencies and evidence; archive selected historical runs after verification |
| myenv/ | 434.18 MiB; 16,160 files | Rebuildable environment; remove only after proving an alternative environment works and no process uses it |
| artifacts/okx_demo_soak_validation/ | 523.67 MiB | Historical archive candidate; reference and hash audit first |
| artifacts/mm_v1_1_post_admission_smoke/ | 409.91 MiB | Historical archive candidate; reference and hash audit first |
| artifacts/mm_v1_6_economic_viability/ | 234.47 MiB | Mixed data; contains required runtime specification, never bulk-delete |
| artifacts/mm_v1_3_balanced_causal_smoke/ | 85.51 MiB | Historical archive candidate |
| artifacts/mm_v1_5_timeboxed_drawdown_repair/ | 72.03 MiB | Historical archive candidate |
| artifacts/mm_v1_4_targeted_causal_defense/ | 67.28 MiB | Historical archive candidate |
| artifacts/mm_v1_3c_fill_trigger_activity_repair/ | 57.94 MiB | Historical archive candidate |
| artifacts/mm_v1_3b_stress_resilience_repair/ | 53.30 MiB | Historical archive candidate |

The first two historical artifact directories total 933.58 MiB, but this is
potential archive volume, not an approved deletion list. Moving within the repo
does not reduce disk usage; external verified backup or compression is needed.

## Keep in active paths

- market_maker/ and tests/: model, accounting and regression coverage.
- okx_demo_adapter.py, okx_demo_runtime.py, okx_demo_state.py,
  okx_demo_profile.py, okx_demo_protocol.py, okx_demo_economic_fill_engine.py,
  okx_demo_economic_session_controller.py, okx_demo_multi_session_campaign.py,
  okx_demo_soak_executor.py and okx_demo_staged_validation.py.
- market_spec.py, fill_tracker.py, fill_classification.py and
  okx_execution_safety.py: shared components; do not classify by filename age.
- okx_fill_restart_*.py: recovery and evidence dependencies; retain pending full graph audit.
- Current R0/R1/R2 builders, verifiers and executors, including
  okx_demo_r2_s02_s03_continuation.py and okx_demo_r2_stage_c_executor.py:
  currently referenced, although they require correctness review (see below).
- requirements*.txt, .gitignore, architecture and promotion-process documents.
- .git/ and local .env: preserve. Do not publish or read secrets for cleanup.
- artifacts/mm_v1_6_economic_viability/specification_20260801T070658Z/protocol_spec.json:
  loaded explicitly by okx_demo_profile.py::_spec_path. Deleting it breaks profile loading.
- Recent R0/R1/R2 predecessor chains and failed-run evidence, including
  r0_session3_walltime_gate/, r0_session2_terminal_label_audit/,
  r2_current_stage_c_preparation/, r2_stage_c_execution/ and their referenced packages.
  Keep failures as evidence and identity-use records; expiry does not make them disposable.

## Delete candidates after stopped-process and exact-path checks

| Path | Reason / condition |
|---|---|
| __pycache__/ and nested __pycache__/ | Rebuildable bytecode; do not traverse myenv unnecessarily |
| .pytest_cache/ | Rebuildable pytest cache |
| .tmp_post_campaign_targeted/ | Test output; confirm no evidence references before deletion |
| .tmp_r0_expanded_targeted/ and .tmp_r0_expanded_targeted_2/ | Test output; approximately 4.96 MiB combined |
| .tmp_r0_repair_targeted/ and .tmp_r0_repair_targeted_2/ | Test output; approximately 2.66 MiB combined |
| tmp_pytest/ and scratch/ | Access denied during inventory: pending inspection, not cleared for deletion |

Root bytecode/cache plus the five readable .tmp directories total about 10.25 MiB.
This cleans clutter but does not solve the main disk footprint.

## Archive or migrate, not immediate deletion

- Legacy async runtime: main.py, trading_bot.py, config.py, order_manager.py,
  quote_engine.py, risk_manager.py, regime_detector.py, market_state.py,
  state_persistence.py, dashboard_state.py, web_server.py, static/ and utils.py.
  README identifies this stack as legacy. trading_bot.py imports config,
  fill_tracker and order_manager: moving only one file breaks imports. Audit
  tests and shared dependencies before moving the stack to legacy/.
- strategy.py and .lumibot/: legacy Lumibot implementation/state. Archive as a group
  after checking consumers; they are still bot-related, not unrelated junk.
- bot_state.json: old runtime state. Preserve with provenance; never present it
  as current exchange state or reset it as a cleanup shortcut.
- compare_params.py, backtest/optimize.py, backtest/robust_optimize.py and
  requirements-research-disabled.txt: research-disabled, not automatically obsolete.
  Retain separately from active execution instructions. Do not run research during cleanup.
- *_repair_offline.py, *_audit_offline.py, historical *_campaign_prepare.py and
  *_campaign_supervisor.py: one-off names do not prove disuse. Some are imported by
  verifiers/tests or included in source hashes. Archive only after import, literal
  path and evidence-reference checks; do not mass-delete with a wildcard.
- Root r1_special_flatten_admission_repair*.log, r2_special_flatten_repair_run*.log,
  r2_mapping_evidence.log: seven small historical logs (90–164 bytes each).
  Negligible space benefit; archive with the corresponding repair evidence.

## Markdown that needs a clear current/historical distinction

| Document | Recommended treatment |
|---|---|
| README.md | Update current status and entry points; it says R0-only and links missing AGENTS.md |
| REPO_CLASSIFICATION_MANIFEST.md | Refresh; it lists missing AGENTS documents and is too broad about retaining all okx files |
| BOT_TRADE_STATUS_AND_REPO_ANALYSIS.md | Mark as dated historical analysis until reconciled with current evidence |
| REPO_CLEANUP_GUIDE.md, EXECUTION_REPOSITORY_CLEANUP.md | Consolidate into one current cleanup procedure; retain old versions as historical |
| EXECUTION_R0_CLOSURE_R1_PREFLIGHT_PREPARATION.md, EXECUTION_R1_READ_ONLY_PREFLIGHT.md, EXECUTION_R2_SUCCESSOR_ECONOMIC_CAMPAIGN.md | Treat fixed-run instructions as historical references, not reusable authorization |
| CODEX_EXECUTION_R2_FINAL_ADMISSION_AND_CANARY_PREPARATION.md, CODEX_EXECUTION_R2_THREE_SESSION_CHECKPOINT.md | Preserve protocol history; distinguish old Q01–Q03 flow from current code |
| R1_NETWORK_CAPABLE_PREFLIGHT_IMPLEMENTATION_PLAN.md, R2_POST_Q06_IMPLEMENTATION_PLAN.md, R2_STAGE_C_S02_S03_IMPLEMENTATION_PLAN.md | Archive after capturing unresolved items in a current plan; do not treat completion claims as independently verified |
| ARCHITECTURE.md, BOT_TRADE_PRODUCTION_PROMOTION_PROCESS_MEMORY.md | Keep and reconcile with the chosen canonical execution path |

## Correctness issues that cleanup must not hide

1. okx_demo_r2_stage_c_executor.py has several checkpoint fields hard-coded True,
   including exact accounting reconciliation, and emits mutation_retries_attempted=0
   as a literal. A passing marker alone does not independently prove these properties.
   Earlier summaries overstated how strongly these checks had been verified.
2. The current continuation design excludes the canary from the economic denominator
   but uses slots s02–s12 of a 12-slot campaign. That leaves only 11 potential economic
   sessions. Resolve qualification accounting before describing this route as a
   complete 12-session qualification pipeline; do not lower criteria to accommodate it.
3. Prior plan used preparation created_at as campaign start. Verify the canonical
   wall-time policy and durable start event before describing that as established fact.
   Preserve the historical gate result; do not reuse its identities.
4. Historical tests reference real artifact paths. Archiving artifacts can break tests
   even when runtime code no longer uses them. Migrate to deterministic fixtures first.
5. .gitignore already ignores artifacts/ and myenv/: local cleanup and Git repository
   cleanup are different. Removing ignored data does not shrink existing Git history.

## Recommended order

1. Establish one CURRENT_STATUS.md with evidence links, unresolved issues and canonical
   entry points; link it from README. Documents do not grant execution permission.
2. Remove only verified disposable caches and temporary test output after approval.
3. Build an exact archive manifest for the two largest historical directories:
   IDs, relative paths, byte counts, SHA-256, source/test references and protected files.
4. Verify an external archive before removing any original local evidence.
5. Migrate legacy/research modules and dated instructions as separate changes with
   import/path tests. Preserve frozen source and evidence hashes through explicit migration.

No files were deleted or moved by this audit. No claim is made that every .py or
historical artifact is safe to delete; unresolved dependencies remain protected.
