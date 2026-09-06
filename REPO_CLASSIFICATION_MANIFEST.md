# Repository classification manifest

This manifest is for review and cleanup planning. It does not authorize deletion, movement, test execution, or external archival.

## Core: retain in active paths

- `market_maker/` excluding `__pycache__/`
- `okx_demo_*.py`, `okx_fill_restart_*.py`, and `okx_execution_safety.py`
- `market_spec.py`, `fill_tracker.py`, `fill_classification.py`
- `tests/`, `backtest/`, and `static/index.html`
- immutable predecessor package `economic-package-20260818T125221Z`
- `CURRENT_STATUS.md` and `REPO_CLEANUP_AUDIT_20260905.md`

There is no active `AGENTS.md` file in this checkout. Execution authority comes
from the applicable explicitly authorized package and the durable evidence it
names, not from a missing repository-local instruction file.

## Legacy: retain until a deliberate migration is complete

- `main.py`, `trading_bot.py`, `config.py`, dashboard/runtime modules
- `strategy.py` (unimported Lumibot implementation)
- `.lumibot/` (legacy Lumibot state)
- `bot_state.json` (legacy async runtime state)
- historical protocol and planning Markdown documents

Do not move legacy sources before updating dependent documentation, tests, source-hash manifests, and any explicit import/path references.

## Generated: removable after a stopped-process and path review

- `__pycache__/`, `market_maker/__pycache__/`, `backtest/__pycache__/`
- `.pytest_cache/`
- `.tmp_*`, `*_tmp`, and test output directories
- empty logs and `bot_state.json.corrupt`

Some generated directories are ACL-locked by the sandbox owner. Do not alter broad ACLs; resolve the exact path with the owning account or an administrator.

## Research-disabled: retain, but do not execute/import in the current phase

- `backtest/optimize.py`
- `backtest/robust_optimize.py`
- `compare_params.py`
- robust/validation/holdout test and workflow files
- Optuna artifacts, if retained outside active cleanup targets

## Historical evidence: archive, never bulk-delete

Examples include `artifacts/mm_v1_*`, `artifacts/okx_demo_soak_validation/`, and earlier repair packages. Before external archival:

1. Inventory exact package/run IDs.
2. Retain completion markers, decisions, registries, and hashes.
3. Verify the archive before any local deletion.
4. Exclude the immutable predecessor package from all candidate lists.
