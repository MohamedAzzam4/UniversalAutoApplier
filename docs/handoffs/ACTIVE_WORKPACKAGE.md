# Active Workpackage — V2-01 Immediate Correctness Blockers (F2/T03)

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 — close the immediate correctness blockers in the V2 plan. Current bounded slice F2/T03 verifies text-field values after input and blur, reports incompatible site-side rewrites as unresolved validation failures, and preserves observed requiredness into the submission snapshot.
- **Status:** **F2/T03 IMPLEMENTATION COMPLETE; SUPERVISOR REVIEW PENDING.** F1/T01–T02 and the separate consent-test lifecycle checkpoint are already committed and pushed. F2b upload evidence and remaining V2-01 slices are outside this change.
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `4b85767b8d48fa01a30cf9d24d75dcce25276d60` (last pushed checkpoint before F2/T03).
- **Last completed/checkpoint SHA:** `4b85767b8d48fa01a30cf9d24d75dcce25276d60`; no F2 commit has been created yet.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-01-correctness
  ```

  The two resolved values must match before checkpoint handoff. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- **F1/T01–T02 (checkpointed):** skill evidence abstains on absent, negated, and contradictory statements, including tested German negative CV evidence. Visa possession is not inferred from `requires_sponsorship`; explicit sponsorship-No mapping remains covered.
- **Consent lifecycle repair (separate checkpoint):** consent tests use pytest-playwright's managed `page` fixture instead of nesting sync Playwright managers. The ordered dashboard-plus-consent reproduction passed 8/8, and the standalone consent module passed 7/7.
- **F2/T03 (current review):** text-like controls are filled, blurred, sampled until their DOM value remains stable for 150 ms (up to 1.5 seconds), then read again through Playwright. Empty or incompatible rewrites raise a structured read-back mismatch, produce `intervention_needed` with an empty `filled_value`, preserve the observed DOM value and proposed answer separately, and append a validation error. Compatible email-case, numeric-equivalence, and recognized date-format normalization return the actual browser value.
- `LiveFieldRecord.required` now carries observed form requiredness with a backward-compatible `False` default. Live deterministic, revealed, LLM, and synthetic records populate it. Submission snapshots copy that value and include requiredness in the form-structure fingerprint, so unresolved required fields remain visible to the readiness gate.
- F2 hermetic tests cover a required email input cleared on blur, an LLM-proposed required textarea cleared on blur, email case normalization, empty `filled_value` on mismatch, and requiredness/unresolved readiness in the submission snapshot. They use in-memory page content and a mocked resolver; they do not navigate to a real site or submit.
- The previous active F1/consent handoff is archived verbatim at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-01_F1.md`. The WQ-8 real-submission authorization remains archived unchanged at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_WQ-8.md`.

## F2/T03 files for supervisor review

- `src/universal_auto_applier/form_engine/live_executor.py`
- `src/universal_auto_applier/browser/live_models.py`
- `src/universal_auto_applier/submission/models.py`
- `tests/playwright/test_live_browser_executor.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-01_F1.md`

The parallel F4 edits in `persistence/job_repository.py`, `tests/unit/test_job_repository.py`, and `tests/contract/test_importer.py` are not part of this staged F2 scope.

## Validation results

- `.venv/Scripts/python.exe -m pytest tests/playwright/test_live_browser_executor.py -q` → **13 passed in 48.57s**.
- `.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/integration tests/pipeline -m "not live" -q` → **1,479 passed in 603.35s (10:03)**. This shared-worktree run also included the parallel F4 edits in `persistence/job_repository.py`, `tests/unit/test_job_repository.py`, and `tests/contract/test_importer.py`; those paths are excluded from the F2 staged scope. Persistent log: `%TEMP%\uaa-v2-01-f2-nonplaywright-3e490a240d114c9391eb1c1a0b2f4acf.log`.
- `.venv/Scripts/ruff.exe check` on the four F2 source/test files → passed, all checks passed.
- `.venv/Scripts/ruff.exe format --check` on the four F2 source/test files → passed, 4 files already formatted.
- `.venv/Scripts/python.exe -m pyright` → 0 errors, 0 warnings, 0 informations.
- `git diff --check` → passed.
- The full Playwright-inclusive combined gate remains unverified. No live tests, real ATS actions, or real submissions were run.

## Decisions

- A fill is verified only when a stable post-blur DOM read-back is compatible with the proposed value. A failed read-back is represented as an intervention/validation error and never as a filled value.
- Compatibility is narrow: exact text, case-insensitive email equivalence, equal finite decimal numbers, or equal calendar dates in the supported formats. Other site-side changes require review.
- Requiredness is part of form structure and is preserved through `LiveFieldRecord` and snapshot construction; the safe default preserves compatibility with older report constructors.
- Upload status/evidence and remote acceptance are explicitly deferred to F2b. No implementation for import ownership, duplicate gates, readiness boundaries, or UI design is included here.
- Supervisor review is the current checkpoint gate. Existing WQ-8 owner approval still applies to any real target/live action and real submission; this synthetic F2 work used none.

## Blockers / risks

- Supervisor review is pending before F2 commit/push.
- The combined Playwright-inclusive non-live suite has not been rerun after the consent lifecycle fix; the focused executor module and consent reproducer passed separately.
- F2b upload evidence and the remaining V2-01 slices are still open.

## Exact next action

After supervisor approval, commit and push only the staged F2/T03 paths above, verify `HEAD` equals `origin/checkpoint/v2-01-correctness`, then wait for the next bounded supervisor assignment. Do not start F4 integration or another slice before that assignment.

- **Last updated:** 2026-09-24T15:38:45Z.
