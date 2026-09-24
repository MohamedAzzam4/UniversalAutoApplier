> Archived predecessor note: F4/T10 was subsequently supervisor-approved and
> committed/pushed at `cb0ceba0bf38206ceb4609e13019b5a6660598f9`. The status
> below is the original pre-checkpoint handoff state.

# Active Workpackage — V2-01 Immediate Correctness Blockers (F4/T10)

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 — preserve UAA-owned operational state when a JobHunter queue re-import refreshes producer-owned job data. Current bounded slice F4/T10 implements the interim metadata allowlist; full upstream/local fact separation remains V2-03.
- **Status:** **F4/T10 IMPLEMENTATION COMPLETE; SUPERVISOR REVIEW PENDING.** F1, consent lifecycle repair, and F2/T03 are checkpointed. F4 is a separate uncommitted package; no further slice is included.
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `706831cf86f5d28ab7cd862de098b72f643a0112` (pushed F2/T03 checkpoint).
- **Last completed/checkpoint SHA:** `706831cf86f5d28ab7cd862de098b72f643a0112`; no F4 commit has been created yet.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-01-correctness
  ```

  The two resolved values must match before checkpoint handoff. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- **F1/T01–T02:** skill evidence abstains on absent, negated and contradictory statements, including tested German negative CV evidence. Visa possession is not inferred from `requires_sponsorship`; explicit sponsorship-No mapping remains covered.
- **Consent lifecycle repair:** a separate test-only checkpoint uses pytest-playwright's managed `page` fixture instead of nested sync Playwright managers. The ordered dashboard-plus-consent reproduction passed 8/8; the standalone consent module passed 7/7.
- **F2/T03:** committed and pushed as `706831cf86f5d28ab7cd862de098b72f643a0112`. Text fills require a stable, compatible post-blur DOM read-back; incompatible rewrites remain interventions with no verified value. Requiredness is preserved through live records and the submission snapshot. Focused executor tests passed 13/13; the broad non-Playwright gate passed 1,479/1,479 with the F4 working-tree edits present. Ruff and Pyright passed.
- **F4/T10 (current review):** new imports discard producer-supplied `dashboard_submitted` and `dashboard_submitted_at` markers. Re-imports preserve those local markers, including an explicit local `False`, while refreshing producer metadata. The allowlisted per-job answer maps (`application_answers`, `form_answers`, `question_answers`) are retained only when the producer omits the entire key; an explicitly supplied producer map replaces the prior map. Unrelated old producer keys are dropped rather than deep-merged. New producer keys are accepted.
- The F4 unit and contract tests cover marker preservation/clearing, producer marker rejection on insert, answer-map preservation/replacement, producer metadata refresh, and import-session cleanup.
- The previous active F2 handoff is archived at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-01_F2.md`; the preceding F1/consent handoff remains at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-01_F1.md`. The WQ-8 real-submission authorization remains archived unchanged at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_WQ-8.md`.

## F4/T10 files for supervisor review

- `src/universal_auto_applier/persistence/job_repository.py`
- `tests/unit/test_job_repository.py`
- `tests/contract/test_importer.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-01_F2.md`

## Validation results

- `.venv/Scripts/python.exe -m pytest tests/unit/test_job_repository.py tests/contract/test_importer.py -q` → **37 passed in 6.25s**.
- `.venv/Scripts/ruff.exe check` on the three F4 source/test files → passed, all checks passed.
- `.venv/Scripts/ruff.exe format --check` on the three F4 source/test files → passed, 3 files already formatted.
- `.venv/Scripts/python.exe -m pyright` → 0 errors, 0 warnings, 0 informations.
- The broader `.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/integration tests/pipeline -m "not live" -q` gate passed **1,479 tests in 603.35s (10:03)** on the shared worktree with these F4 edits present.
- The F2 focused Playwright executor module passed 13 tests; the ordered consent lifecycle reproduction passed 8 tests and its standalone module passed 7 tests. The complete Playwright-inclusive combined gate remains unverified.
- F4 whitespace validation: `git diff --check` → passed.
- No live tests, real ATS actions, or real submissions were run.

## Decisions

- Re-import uses a shallow, key-specific ownership policy. UAA's manual submission markers always remain local once present; producer data cannot create those markers on insert.
- Known answer-map metadata is retained when omitted upstream and replaced only when the producer explicitly supplies that whole key. Arbitrary deep merge of metadata is not permitted.
- This allowlist is an interim V2-01 step. Versioned separation of upstream and local facts belongs to V2-03.
- The F4 package is separate from F2/T03 and from upload evidence work F2b. Supervisor review is the current code checkpoint gate. Existing WQ-8 owner approval remains required for any real target/live action and real submission; this synthetic work used none.

## Blockers / risks

- Supervisor review is pending before F4 commit/push.
- Full Playwright-inclusive regression remains unverified; the focused browser modules passed separately.
- F2b upload evidence and remaining V2-01 slices remain open.

## Exact next action

After supervisor approval, commit and push only the staged F4/T10 paths above, verify `HEAD` equals `origin/checkpoint/v2-01-correctness`, then wait for the next bounded supervisor assignment. Do not begin F2b or another slice before it is assigned.

- **Last updated:** 2026-09-24T15:47:07Z.
