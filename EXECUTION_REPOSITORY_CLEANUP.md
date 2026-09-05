# CODEX EXECUTION BRIEF — Repository Cleanup, Archive & Dead-File Reduction

## 0. Purpose

This document defines a safe cleanup procedure for the `tordave1150/mm-bot-okx` repository.

The objective is to reduce repository clutter caused by:

- generated test/runtime files;
- temporary directories;
- caches;
- corrupt/stale state files;
- obsolete duplicate artifacts;
- superseded documentation/protocol files;
- legacy runtime files that may no longer be part of the canonical successor stack.

The cleanup must **not** trade repository cleanliness for loss of reproducibility, evidence, safety coverage, or execution integrity.

The preferred end state is:

```text
ACTIVE / CANONICAL
    +
REQUIRED TESTS
    +
CURRENT DOCUMENTATION
    +
VERIFIED HISTORICAL ARCHIVE
    +
MINIMAL LEGACY COMPATIBILITY
```

instead of:

```text
ACTIVE CODE
    +
TEMP FILES
    +
OLD TEST OUTPUT
    +
DUPLICATE PROTOCOL FILES
    +
UNCLASSIFIED LEGACY CODE
    +
UNVERIFIED HISTORICAL EVIDENCE
```

---

# 1. Authority and precedence

Before doing anything:

1. read the current root `AGENTS.md`;
2. read `REPO_CLASSIFICATION_MANIFEST.md`;
3. inspect the current working tree;
4. follow the stricter rule if this document conflicts with either file.

Current project rules are fail-closed.

This cleanup task does **not** authorize:

- OKX network access;
- credential access;
- OKX Demo execution;
- production execution;
- Optuna;
- validation/holdout execution;
- modification of immutable predecessor evidence;
- deletion of historical evidence without verified archival;
- Git commit/push/tag/branch operations;
- broad ACL or permission changes.

Do not use repository cleanup as a reason to weaken safety controls or delete inconvenient regression tests.

---

# 2. Cleanup philosophy

Use four classifications only:

## `KEEP_ACTIVE`

Required by the canonical successor runtime, current tests, active documentation, or current safety/evidence protocol.

Do not move or delete.

## `DELETE_GENERATED`

Generated, reproducible, temporary, cached, corrupt, or transient files that are safe to recreate.

These may be deleted in **Pass 1** after the checks in this document pass.

## `ARCHIVE_CANDIDATE`

Historical or superseded material that is no longer active but still has provenance, audit, regression, or documentation value.

Do not delete immediately.

Move/archive only after dependency and evidence checks.

## `REVIEW_REQUIRED`

Anything whose use cannot be proven either way.

Fail closed: retain it and report why classification is uncertain.

---

# 3. Canonical code that must remain active

Treat the following as protected unless the current root `AGENTS.md` explicitly says otherwise:

```text
AGENTS.md
AGENTS_OKX_DEMO_ECONOMIC_SAMPLE_EFFICIENCY_REPAIR.md

market_maker/

okx_demo_*.py
okx_fill_restart_*.py
okx_execution_safety.py

market_spec.py
fill_tracker.py
fill_classification.py

tests/
backtest/

static/index.html
```

Within `market_maker/`, caches such as `__pycache__/` are not protected.

The immutable predecessor package identified by the active `AGENTS.md` must remain untouched.

Do not rename, rewrite, repackage, truncate, regenerate, or deduplicate immutable predecessor evidence.

---

# 4. Pass 1 — Safe generated-file cleanup

Pass 1 is the only cleanup stage that may remove files without a second human review, provided the pre-delete checks pass.

## 4.1 Primary delete targets

Search recursively for:

```text
__pycache__/
*.pyc
*.pyo

.pytest_cache/

.tmp_*
*_tmp/
tmp_tests_*/
test-output/
test_outputs/
test-results/
test_results/

bot_state.json.corrupt

empty *.log files
empty generated runtime directories
```

Known examples that should be inspected as likely generated cleanup candidates include:

```text
.okx_fill_restart_tmp/
.tmp_r0_expanded_targeted/
```

Do not assume a directory is disposable only because its name contains `tmp`.

Verify its contents and references first.

---

# 5. Pass 1 pre-delete checks

Before deleting each generated candidate, prove all of the following:

1. it is not referenced by active Python imports;
2. it is not referenced by current test fixtures using an exact filesystem path;
3. it is not referenced by root `AGENTS.md`;
4. it is not part of the immutable predecessor package;
5. it is not an input to source/evidence hash verification;
6. it is not the only copy of a required fixture;
7. it is not a manually curated dataset disguised as generated output;
8. no relevant process is actively writing to it.

For generated test outputs, inspect representative contents.

Files such as:

```text
formal_state.json
formal_state_journal.jsonl
checkpoint-guards.json
*_journal.jsonl
```

inside temporary test-output directories are removable **only if** they are produced by tests and are not canonical fixtures committed intentionally for deterministic replay.

---

# 6. Pass 1 deletion rule

A candidate may be deleted only when:

```text
generated_or_reproducible = true
AND active_reference_count = 0
AND protected_evidence = false
AND canonical_fixture = false
```

Otherwise classify it as:

```text
REVIEW_REQUIRED
```

Do not guess.

---

# 7. Pass 1 repository hygiene

After cleanup, inspect `.gitignore`.

The current `.gitignore` already intends to exclude several generated paths, including:

```text
__pycache__/
*.pyc
*.pyo
state/
bot_state.json
bot.log
bot_error.log
bot_state.json.corrupt
artifacts/
```

Also check the final temporary-test ignore pattern for malformed or encoding-corrupted content.

If the trailing ignore entry is malformed, normalize it to a clear UTF-8 pattern only after confirming the intended directory name.

Recommended coverage should include, where applicable:

```gitignore
.pytest_cache/
__pycache__/
*.py[cod]

.tmp_*/
*_tmp/
tmp_tests_*/

bot_state.json.corrupt
```

Do not add a blanket pattern that accidentally hides source fixtures, current evidence, or important JSON/JSONL files.

---

# 8. Pass 2 — Legacy and obsolete-file audit

Pass 2 is **classification-first**.

Do not bulk-delete legacy code.

The current repository classification specifically treats the following as legacy that must be retained until deliberate migration is complete:

```text
main.py
trading_bot.py
config.py
strategy.py
.lumibot/
bot_state.json
historical AGENTS_*.md
dashboard/runtime legacy modules
```

The goal of Pass 2 is to determine which of these can eventually be archived or deleted safely.

---

# 9. Canonical vs legacy dependency map

Build a dependency/reference map for every Pass 2 candidate.

Search:

- Python imports;
- dynamic imports;
- subprocess/script invocation;
- test references;
- documentation references;
- fixture paths;
- source hash manifests;
- evidence manifests;
- workflow references;
- shell/PowerShell/batch scripts;
- README usage examples;
- package configuration;
- CI configuration if present.

For each candidate report:

```text
path
classification
imported_by
executed_by
referenced_by_tests
referenced_by_docs
referenced_by_evidence
runtime_entrypoint
last_known_purpose
safe_to_archive
safe_to_delete
reason
```

---

# 10. Legacy runtime handling

The repository currently contains two conceptual generations:

```text
CANONICAL SUCCESSOR
market_maker/
okx_demo_*.py
okx_fill_restart_*.py
okx_execution_safety.py

LEGACY
main.py
trading_bot.py
config.py
strategy.py
.lumibot/
bot_state.json
```

Do not delete the legacy group merely because it is not the canonical runtime.

First prove:

1. the successor stack does not import it;
2. active tests do not require it except explicit legacy regression guards;
3. documentation clearly marks the successor as canonical;
4. source/evidence hashing does not depend on the legacy file remaining at the original path;
5. no current operational script invokes it;
6. migration/compatibility intent is documented.

If legacy regression tests intentionally confirm that old entry points fail closed, those files are still serving a safety purpose and must remain until the guard is replaced.

---

# 11. Historical `AGENTS_*.md` files

Do not blindly delete historical protocol files.

Classify each as one of:

```text
CURRENT_CANONICAL
CURRENT_SOURCE_COPY
SUPERSEDED_PROTOCOL
HISTORICAL_AUDIT_RECORD
UNKNOWN
```

## Keep active

Always retain:

```text
AGENTS.md
```

and any source copy explicitly required by the current root `AGENTS.md` or current classification manifest.

## Archive candidates

Older `AGENTS_*.md` files may become archive candidates if:

- they are superseded;
- they are not imported or executed;
- current documentation does not rely on their path;
- evidence/source-hash verification does not require the active path;
- their historical provenance remains accessible after archive.

Preferred destination if archival is authorized later:

```text
docs/archive/protocols/
```

Do not delete them directly in this pass.

---

# 12. Historical evidence and artifact cleanup

Historical evidence is not ordinary clutter.

Examples may include:

```text
artifacts/mm_v1_*/
artifacts/okx_demo_soak_validation/
older repair packages
campaign evidence
completion markers
decision files
registry files
hash manifests
```

Never perform:

```text
rm -rf artifacts/
```

or equivalent broad deletion.

## Evidence archival procedure

For every historical evidence package considered for archival:

1. inventory exact package/campaign/run IDs;
2. identify terminal completion marker;
3. identify decision file;
4. identify registry/registry tail if applicable;
5. identify hash manifest;
6. compute or verify current hashes;
7. confirm immutable predecessor exclusion;
8. prepare archive inventory;
9. verify archive copy;
10. only then mark the local source as `ARCHIVE_DELETE_ELIGIBLE`.

The immutable predecessor package must remain excluded from all deletion candidates.

---

# 13. Duplicate-file detection

Look for actual duplicate content using hashes, not filenames.

For duplicates:

```text
same SHA-256
same semantic purpose
same provenance requirements
```

do not automatically delete all but one.

Check whether multiple paths are intentionally preserved because:

- a source copy must match root `AGENTS.md`;
- an evidence package requires exact source layout;
- tests reference a fixed path;
- historical protocol provenance requires the original path.

Report duplicates separately:

```text
DUPLICATE_IDENTICAL_BUT_REQUIRED
DUPLICATE_ARCHIVE_CANDIDATE
DUPLICATE_SAFE_DELETE
```

---

# 14. Dead Python module detection

For `.py` files outside the protected canonical groups:

Run static reference analysis.

At minimum check:

```text
import X
from X import ...
dynamic imports
script execution
test imports
documentation invocation
```

Do not use "no direct import found" as sufficient proof of dead code.

Also inspect:

```python
if __name__ == "__main__":
```

CLI entry points, subprocess calls, plugin registries, strategy registries, and string-based module loading.

A Python source file can be classified `DEAD_CODE_CANDIDATE` only when:

```text
no active import
no active invocation
no test dependency
no evidence dependency
no documentation-supported entrypoint
not protected legacy
```

Pass 2 should report these candidates rather than deleting them automatically.

---

# 15. Test cleanup rules

Do not delete tests just because their associated bug has been fixed.

Regression tests are part of the safety architecture.

A test may be a deletion candidate only if it is:

- an exact duplicate;
- testing code that has already been removed;
- superseded by stronger equivalent coverage;
- not referenced by current evidence or gate matrices.

If deleting or consolidating a test later, document:

```text
old_test
replacement_test
coverage_equivalence
why_behavior_is_still_protected
```

If equivalent coverage cannot be demonstrated, retain it.

---

# 16. Documentation cleanup

Documentation may be classified:

```text
ACTIVE
SUPERSEDED
HISTORICAL
DUPLICATE
STALE_BROKEN
```

Prefer archiving superseded design/protocol documentation rather than deletion.

Active docs should clearly describe:

- canonical successor runtime;
- legacy runtime status;
- current active phase;
- current allowed execution boundaries.

Do not leave contradictory instructions where an old README suggests running a legacy entry point that current `AGENTS.md` prohibits.

If a stale document cannot safely be removed due to historical provenance, add an archive/deprecated header instead.

---

# 17. State files

Special care is required for:

```text
bot_state.json
.lumibot/
```

These are currently legacy state and must not be treated like ordinary cache.

Do not:

- load them into the successor runtime;
- reset them as if reconciliation occurred;
- rewrite them to make the repository appear clean;
- delete them before the deliberate legacy migration decision.

`bot_state.json.corrupt`, by contrast, is a generated/corrupt cleanup candidate if no current test fixture or audit explicitly depends on it.

---

# 18. Cleanup execution order

Use this exact order.

## Step 1 — Snapshot inventory

Produce a recursive inventory containing:

```text
path
type
size
classification_guess
tracked/untracked if determinable without prohibited Git operations
```

If determining tracked state would require a prohibited Git command under the current `AGENTS.md`, omit that field rather than violating policy.

## Step 2 — Protected-path list

Build an explicit protected list from:

- root `AGENTS.md`;
- `REPO_CLASSIFICATION_MANIFEST.md`;
- immutable predecessor paths;
- canonical successor paths.

## Step 3 — Reference scan

Search all cleanup candidates for:

- imports;
- path references;
- evidence references;
- test references;
- docs references.

## Step 4 — Generate cleanup plan

Produce:

```text
DELETE_GENERATED
ARCHIVE_CANDIDATE
KEEP_ACTIVE
REVIEW_REQUIRED
```

No deletion yet.

## Step 5 — Execute Pass 1 only

Delete only `DELETE_GENERATED`.

Do not execute Pass 2 deletion.

## Step 6 — Run allowed offline tests

Run the smallest relevant test set needed to prove cleanup did not break imports/fixtures.

Then run the current permitted successor-applicable non-Optuna regression suite if allowed by root `AGENTS.md`.

Tests must use:

```text
blank credentials
socket denial
zero external network
```

## Step 7 — Re-scan

Check for:

- broken imports;
- missing fixture paths;
- documentation references to deleted files;
- recreated temp directories;
- unexpected runtime state.

## Step 8 — Produce Pass 2 candidate report

Return legacy, dead-code, duplicate, protocol, and evidence archive candidates for review.

Do not delete them yet.

---

# 19. Acceptance criteria for Pass 1

Pass 1 is successful only if:

- generated caches/temp files are removed;
- protected files remain byte-for-byte unchanged unless separately authorized;
- immutable predecessor evidence is untouched;
- canonical successor imports remain valid;
- current permitted tests pass;
- no external network attempt occurs;
- no credential access occurs;
- no OKX Demo/live order mutation occurs;
- no Git write/commit/push operation occurs;
- regenerated temp directories are properly ignored where appropriate;
- the repository is materially cleaner than before.

---

# 20. Required cleanup report

At completion, Codex must produce:

## A. Cleanup summary

```text
files_scanned:
directories_scanned:
bytes_before:
bytes_after:
bytes_removed:
```

Use `unknown` where the environment cannot determine a value safely.

## B. Deleted generated items

| Path | Type | Size | Why safe to delete | Reproducible by |
|---|---|---:|---|---|

## C. Kept protected items

| Path | Protection reason |
|---|---|

Include at least canonical successor paths and immutable evidence.

## D. Archive candidates

| Path | Category | Why inactive/superseded | Dependencies remaining | Recommendation |
|---|---|---|---|---|

## E. Review-required items

| Path | Concern | What prevents automatic cleanup |
|---|---|---|

## F. Tests

Report:

```text
targeted_tests:
successor_tests:
root_non_optuna:
backtest_non_optuna:
```

Use PASS / FAIL / NOT_RUN with reason.

## G. Safety audit

Always report:

```text
production_authorized: false
live_mode_available: false
live_endpoint_attempts: 0
live_orders: 0
credential_reads: 0
optuna_executed: false
validation_opened: false
holdout_opened: false
git_write_operation: false
```

## H. Next cleanup recommendation

Choose one:

```text
CLEANUP_PASS1_COMPLETE
CLEANUP_BLOCKED
DEEP_CLEANUP_REVIEW_REQUIRED
LEGACY_MIGRATION_RECOMMENDED
EVIDENCE_ARCHIVE_RECOMMENDED
```

---

# 21. Explicitly forbidden shortcuts

Never use cleanup commands equivalent to:

```text
delete every old file
delete every unused-looking file
rm -rf artifacts
rm -rf tests
rm -rf backtest
rm -rf AGENTS_*
delete all JSON/JSONL
delete all files older than N days
delete all files not imported by Python
```

Age is not proof of irrelevance.

Import analysis alone is not proof of irrelevance.

A file is removable only after its role is classified.

---

# 22. Desired repository structure after future deep cleanup

This is a target architecture, not an instruction to move protected files immediately:

```text
repo/
├─ AGENTS.md
├─ README.md
├─ ARCHITECTURE.md
│
├─ market_maker/
├─ okx_demo_*.py
├─ okx_fill_restart_*.py
├─ okx_execution_safety.py
├─ market_spec.py
├─ fill_tracker.py
├─ fill_classification.py
│
├─ tests/
├─ backtest/
├─ static/
│
├─ docs/
│  └─ archive/
│     ├─ protocols/
│     ├─ legacy-runtime/
│     └─ historical-design/
│
└─ artifacts/
   ├─ current/
   └─ archive/
```

Do not force the repository into this layout if exact-path evidence, tests, or active protocol requirements would be broken.

---

# 23. Final principle

The objective is not:

> make the repository look small.

The objective is:

> make every remaining file have a known reason to exist.

Use:

```text
PROVE → CLASSIFY → DELETE GENERATED → TEST → REPORT
```

before:

```text
ARCHIVE → MIGRATE → DELETE LEGACY
```

When uncertain, retain the file and classify it `REVIEW_REQUIRED`.

**Do not destroy provenance to achieve cleanliness.**
