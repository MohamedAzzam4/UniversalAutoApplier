# Active Workpackage

- **WP ID:** WQ-1 — Correct post-submit job / history transitions (CONFIRMED DEFECT).
- **Status:** in progress — implementation complete, tests green, ready for review.
- **Branch:** `checkpoint/wq-1-post-submit-transitions`
- **Base SHA:** `cec8f2ef45f510705887dba5891ab8cf15bee901` (`origin/main`)
- **Last completed/checkpoint SHA:** none yet (first commit pending).
- **Branch-head verification (must be run dynamically; do not trust an
  embedded SHA as the current HEAD because committing changes the SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-1-post-submit-transitions
  ```

  The two resolved values must match before handoff/review.
- **Last updated:** 2026-08-04

## Objective

Close the confirmed post-submit status-transition defect. The controlled
live-browser submission path (`submission/execution_service.py` +
`api/routes/submit.py`) persists `SubmissionResult` rows but never
transitions `ApplicationJob.status`. Implement one authoritative, typed
transition policy:

- `submitted_confirmed` -> `ApplicationStatus.SUBMITTED`
- `APPLIED` only with a reliable structured ATS application/reference ID
- `outcome_unknown` -> `ApplicationStatus.NEEDS_REVIEW`
- failed/pre-click/blocked/rejected/stale/validation outcomes never
  `SUBMITTED`/`APPLIED`
- duplicate/replay is idempotent and never downgrades terminal status

Add `NEEDS_REVIEW` if missing (enum + persistence + migration + API +
tests) through the established patterns. Do not substitute
`NEEDS_USER_INPUT`.

## Rules (from the task and repo doctrine)

- Keep the transition policy in ONE named production function/service.
- Base transitions on structured `SubmissionResult` state and structured
  ATS reference data; never parse human logs or arbitrary page text.
- Persist result + status transactionally (or a clearly tested
  recovery-safe design). Persistence failure must not return success.
- Preserve approval, snapshot, staleness, high-risk confirmation,
  kill-switch, claim, and duplicate-prevention gates. Do not weaken
  untrusted-adapter safety or Siemens behavior.
- Status APIs and dashboard show the persisted status immediately.
- No unrelated refactors. Contents only WQ-1-relevant files.
- Local fixtures only. No real ATS contact.

## Completed work

- Added `submission/status_transitions.py` — the ONE authoritative,
  typed post-submit status transition policy:
  - `target_status_for_result()` maps a structured `SubmissionResult`
    to the post-submit status: `submitted_confirmed` -> `SUBMITTED`;
    `APPLIED` only when a structured `ats_reference_id` is provided;
    `outcome_unknown` -> `NEEDS_REVIEW`; all pre-click/failed/blocked/
    stale/validation outcomes -> no change.
  - `apply_result_status_transition()` applies the transition by walking
    the allowed-transition graph one validated edge at a time, is
    idempotent, and never downgrades a terminal status or overwrites it.
- Wired the policy into `submission/store.record_result()` (the single
  choke point every result flows through): the job-status transition and
  the result row persist in the SAME transaction; replay of an
  already-recorded result (UNIQUE on approval_id) is a no-op.
- `NEEDS_REVIEW` required no enum/migration/API work — it already exists
  in `ApplicationStatus`, `ALLOWED_TRANSITIONS`, and the contract test.
- Surfaced the persisted status immediately: `LiveReviewSnapshotResponse`
  gained `application_status` (populated in `api/routes/submit.py`), and
  the submit view in `ui/static/app.js` renders it.
- Tests: new `tests/unit/test_status_transitions.py` (19 tests);
  `tests/playwright/test_final_pipeline.py` step 12 now asserts the job
  reaches `submitted` after a confirmed controlled submission (was
  asserting the defect: `review_ready`); `test_submission_harness.py`
  asserts `application_status == "submitted"` after a valid submission.

## Changed files

- `src/universal_auto_applier/submission/status_transitions.py` (new)
- `src/universal_auto_applier/submission/store.py`
- `src/universal_auto_applier/api/models/submission.py`
- `src/universal_auto_applier/api/routes/submit.py`
- `src/universal_auto_applier/ui/static/app.js`
- `tests/unit/test_status_transitions.py` (new)
- `tests/integration/test_submission_harness.py`
- `tests/playwright/test_final_pipeline.py`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`

## Tests

```text
tests/unit/test_status_transitions.py          19 passed
tests/unit + contract + integration          969 passed
tests/playwright (browser)                    174 passed
ruff check / ruff format --check / pyright     0 errors
git diff --check                               clean
```

## Decisions made

- `NEEDS_REVIEW` needs no enum/persistence/migration work — it was already
  present. WQ-1 is purely the missing application wiring.
- The transition policy lives in ONE named module
  (`submission.status_transitions`) and is applied from
  `store.record_result` so every submission path (CLI, API, coordinator,
  execution service) is covered transactionally.
- `APPLIED` requires a reliable structured ATS application/reference ID
  passed explicitly as `ats_reference_id` — page text and human-readable
  `confirmation_evidence` are never parsed into one (per doctrine).
- Status transition is best-effort after a result is recorded: a failed
  walk (no allowed path) logs and leaves the job unchanged rather than
  failing the submission transaction.

## Blockers / risks

- None. No real ATS contact; all tests run against local fixtures.

## Exact next action

1. Reviewer: fetch the branch and resolve its head dynamically
   (`git rev-parse HEAD` / `git rev-parse origin/<branch>`).
2. Open a PR: base `main`, head `checkpoint/wq-1-post-submit-transitions`,
   title "fix(submission): post-submit job status transitions (WQ-1)".
3. Review the diff; do not merge into `main` more than once.
4. After merge, update `docs/CURRENT_STATE.md` (known-gap item).

## Session protocol reminder

At session start: `git fetch origin`, verify repo/branch/base SHA, inspect
`git status`, read the handoff pack, stop if local changes could be
overwritten. During work: update this file after every major milestone;
checkpoint before ~60-70% context, before pausing/handoff/switching AI;
never rely on chat history as project memory.

## Rules

- Unknown platforms never auto-submit. Untrusted adapters (`is_trusted=False`)
  never submit through adapter `submit_or_pause`.
- A `review_ready` job (generic or ATS) may be submitted manually via the
  controlled `live-submit` CLI / submission API only when
  `UAA_ENABLE_REAL_SUBMISSION=true`, the snapshot is explicitly approved,
  high-risk fields are confirmed, and no intervention/stale/duplicate gate
  blocks it.
- Siemens is the only trusted adapter path, but not the only job type
  supported by manually approved controlled submission.
- Never parse human logs to determine submission success.
- Keep the handoff files updated when this state changes.