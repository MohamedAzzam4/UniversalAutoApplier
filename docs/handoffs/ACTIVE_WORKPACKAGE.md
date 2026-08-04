# Active Workpackage

- **WP ID:** WQ-1 — Correct post-submit job / history transitions (CONFIRMED DEFECT).
- **Status:** in progress — round-2 rework implementing the reviewer's 7 findings; all gates green locally.
- **Branch:** `checkpoint/wq-1-post-submit-transitions`
- **Base SHA:** `cec8f2ef45f510705887dba5891ab8cf15bee901` (`origin/main`)
- **Round-1 commit SHA:** `e3cd291e641cf605f72675fb1fcbd121b4af6fb2` (superseded by round 2).
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
live-browser submission path persists `SubmissionResult` rows but never
transitioned `ApplicationJob.status`. Round 2 reworks the round-1
implementation to satisfy the reviewer requirements:

1. Explicit transitions only — no BFS graph walk.
2. Structured, durable ATS reference (`ats_reference_id`) on the result,
   persisted via migration; status derived from the persisted row.
3. Transactional consistency — transition failure rolls back result + status
   together; no best-effort swallowing.
4. Bounded idempotent reconciliation for legacy inconsistent rows.
5. Corrected `SubmissionResultState` contract documentation.
6. User-POV Playwright/dashboard tests (status visible + reload/restart).
7. All required regressions.

## Rules (from the task and repo doctrine)

- Explicit post-submission transitions ONLY:

  ```
  review_ready + submitted_confirmed            -> submitted
  review_ready + submitted_confirmed + ref      -> submitted -> applied
  submitted    + submitted_confirmed + ref      -> applied
  review_ready + outcome_unknown                -> needs_review   (direct)
  submitted    + outcome_unknown                -> needs_review
  ```

- Earlier pipeline statuses are NEVER auto-advanced by a result. Terminal
  statuses are never downgraded. Same-status replay is a no-op.
- `APPLIED` requires the durable structured `ats_reference_id` persisted with
  the `SubmissionResult`. Never parse page text / logs / evidence into one;
  never confuse it with `external_job_id`; production may leave it empty.
- Result row + status commit or roll back together; invariant failures
  propagate (no catch-and-commit).
- Reconciliation: `submitted_confirmed` w/o ref -> `submitted`;
  `outcome_unknown` -> `needs_review`; never downgrade terminal; never infer
  `applied` for legacy rows; document when it runs + restart behavior.
- Preserve approval/snapshot/staleness/high-risk/kill-switch/claim/duplicate
  gates. No unrelated refactors. Local fixtures only.

## Completed work (round 2)

- Rewrote `submission/status_transitions.py`:
  - Removed the BFS walk (`_find_transition_path` / `deque`).
  - Explicit table `POST_SUBMIT_TRANSITIONS` keyed on (current, target)
    mapping to the exact validated edge sequence; missing key = no change.
  - `target_status_for_result(result)` now reads the structured
    `result.ats_reference_id` (no kwarg).
  - No `try/except ValueError` swallowing — invalid transitions raise and
    the caller's `session_scope` rolls back result + status together.
- `core/statuses.py`: `ALLOWED_TRANSITIONS[REVIEW_READY]` now includes
  `NEEDS_REVIEW` (canonical direct edge); `tests/unit/test_statuses.py`
  spot-check updated.
- Structured ATS ref end-to-end:
  - `submission/models.py`: `SubmissionResult.ats_reference_id: str = ""`;
    `SubmissionResultState` docstring corrected (proves `submitted`;
    `applied` additionally requires the structured ref).
  - `persistence/models.py`: `SubmissionResultRow.ats_reference_id` column.
  - Migration `0007_submission_results_ats_reference` (adds the column,
    server_default `''`).
  - `submission/store.record_result()` no longer takes a kwarg; persists
    `result.ats_reference_id` and uses it for the transition.
  - `api/models/submission.py` + `api/routes/submit.py`: submit response
    returns `ats_reference_id`; snapshot response exposes
    `latest_submission_ats_reference_id`.
  - `cli.py`: the redundant manual `update_application_status(SUBMITTED)`
    block removed (the service now applies the transition from the
    persisted result; the old block could downgrade an APPLIED result or
    raise). Reports the ATS reference when present.
- Reconciliation migration `0008_reconcile_submission_statuses`: one-time,
    bounded-idempotent SQL repair with safety WHERE guards; never infers
    `applied` for legacy rows; documented execution/restart model.
- Contract docs (`docs/generalization/DATA_CONTRACTS.md`): added
  `review_ready -> needs_review`; rules clarify `submitted_confirmed` proves
  `submitted` and `applied` requires a persisted structured ATS ref; explicit
  monotone transitions only.
- Dashboard (`ui/static/app.js`): new always-visible "Submission Outcome"
  section (latest submission + application status + ATS reference) rendered
  even when no persisted snapshot exists — status stays readable after
  restart on a submitted job.
- Tests:
  - `tests/unit/test_status_transitions.py` rewritten (32 tests): explicit
    table literal, early-state regressions, APPLIED-unreachable-without-ref,
    ref-persistence across restart, rollback atomicity via failure injection,
    terminal protection, idempotent replay.
  - `tests/contract/test_migrations.py`: head bumped to 0008; `ats_reference_id`
    column present; legacy-DB seeded at 0006 is repaired on upgrade with
    bounds (terminal never downgraded, earlier never advanced, no fabricated
    `applied`).
  - `tests/playwright/test_submission_status.py` (new, 7 tests): dashboard
    shows `submitted` / `applied` + ATS ref / `needs_review` / `review_ready`
    (unaffected); latest-submission pill; status survives page reload and
    full server restart.

## Changed files (round 2)

- `src/universal_auto_applier/submission/status_transitions.py` (rewrite)
- `src/universal_auto_applier/core/statuses.py`
- `src/universal_auto_applier/submission/models.py`
- `src/universal_auto_applier/submission/store.py`
- `src/universal_auto_applier/persistence/models.py`
- `src/universal_auto_applier/api/models/submission.py`
- `src/universal_auto_applier/api/routes/submit.py`
- `src/universal_auto_applier/cli.py`
- `src/universal_auto_applier/ui/static/app.js`
- `migrations/versions/0007_submission_results_ats_reference.py` (new)
- `migrations/versions/0008_reconcile_submission_statuses.py` (new)
- `docs/generalization/DATA_CONTRACTS.md`
- `tests/unit/test_status_transitions.py` (rewrite)
- `tests/unit/test_statuses.py`
- `tests/contract/test_migrations.py`
- `tests/playwright/test_submission_status.py` (new)

## Tests (round 2)

```text
python -m pytest tests/unit tests/contract tests/integration   981 passed
python -m pytest tests/playwright                               181 passed
python -m pytest (full suite)                                  1162 passed, 1 skipped (opt-in live)
python -m ruff check src tests migrations                        0 errors
python -m ruff format --check src tests migrations               163 files formatted
python -m pyright                                                 0 errors
git diff --check                                                 clean
```

## CI results (commit `3d48b12902d5576555506bac5fbe0f97afcdaa38`, PR #5)

```text
Linux + Python 3.11/3.12/3.13/3.14        completed  success (all 4)
Windows + Python 3.14 bootstrap gate      completed  cancelled (45-min workflow timeout)
```

Windows evidence from the job log: every test-bearing step PASSED before the
cancellation — `test.ps1 -All -IncludePlaywright` reported `1162 passed,
1 deselected in 1286.65s` (full suite incl. Playwright), plus ruff check,
ruff format --check, and pyright all green. The job was then killed by the
workflow's 45-minute `timeout-minutes` while re-running the DUPLICATE
"direct pytest (full suite)" step (the workflow runs the full suite twice).

Fix applied on this branch (commit `a6dd0b3c77b3fbd5eb79303859016d02b645881c`):
`.github/workflows/verify-windows-py314.yml` `timeout-minutes` 45 -> 65 with a
comment explaining why. The final green check results are pending that re-run.

PR: https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/5 (open)

## Decisions made

- Replace the BFS lifecycle walk with a hard-coded explicit transition table;
  earlier pipeline statuses get no automatic advancement from a result.
- The ATS reference becomes a persisted column, read back on replay; the
  `ats_reference_id` kwarg on `record_result` was dropped in favor of the
  field on `SubmissionResult` itself (single source of truth).
- Transition invariant failures propagate (no best-effort swallow); the
  caller's `session_scope` rollback covers the result row and the status
  together — verified by a failure-injection test.
- Reconciliation lives in migration `0008` (runs exactly once per DB at
  `alembic upgrade head`/startup; fresh DBs are no-ops; WHERE guards make it
  idempotent). `applied` is never inferred for legacy rows.
- `REVIEW_READY -> NEEDS_REVIEW` added to `ALLOWED_TRANSITIONS` so a direct
  ambiguous-outcome transition is a canonical single edge (not a walk via a
  phantom state).
- The CLI's old manual `SUBMITTED` write was removed as redundant/unsafe.

## Blockers / risks

- PR tooling: the `gh` on PATH is a browser-opener shim and there is no
  `GITHUB_TOKEN`/gh config; PR creation/update must use the GitHub REST API
  (token via `git credential-manager` on the `https://github.com/MohamedAzzam4/UniversalAutoApplier.git`
  the only route — fallback limitation).
- Untracked debug artifacts (`tmp_debug_status.py`, `tmp_debug_status/`,
  `tmp_final_pipeline/`) exist but are excluded from commits.

## Exact next action

1. Commit the round-2 changes (files above; NOT the `tmp_debug_*` artifacts).
2. Push the branch.
3. Create/update the PR via the GitHub REST API (base `main`, head
   `checkpoint/wq-1-post-submit-transitions`, title
   "fix(submission): explicit post-submit transitions + structured ATS ref (WQ-1)").
4. Wait for Linux + Windows CI; report final SHA, changed files, migration
   behavior, transition table, test results, CI URLs, limitations.
5. After merge, update `docs/CURRENT_STATE.md` (known-gap item).

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
- Only explicit post-submit edges are ever applied automatically; no graph
  walking; terminal never downgraded; `applied` requires a persisted
  structured ATS reference.
- Never parse human logs or page text to determine submission success or an
  ATS reference.
- Keep the handoff files updated when this state changes.