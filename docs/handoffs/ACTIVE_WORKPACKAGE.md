# Active Workpackage

- **WP ID:** WQ-5 — restart recovery and stale `in_progress` recovery (durable worker liveness + startup stale-run recovery).
- **Status:** IN PROGRESS — acceptance-proof pass strengthened (durable heartbeat subprocess-exit + dashboard `uaa-pill-recovered` positive proof). PR #8 head updating.
- **Branch:** `checkpoint/wq-5-stale-run-recovery`
- **Base SHA:** `736242b5ed06ccfdf9f94a2061e4a6ce00031aca` (`origin/main`, WQ-4 merge commit)
- **PR:** https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/8 (open, not draft, not merged)
- **Last completed/checkpoint SHA:** `4cedb40cf23a6a131034f950c0258275dfc2d27f` (implementation + first test commit `cdec5e6`). The acceptance-proof strengthening commit is on top of `cdec5e6`; resolve the head dynamically.
- **Branch-head verification (must be run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-5-stale-run-recovery
  ```

  The two resolved values must match before handoff/review.
- **Last updated:** 2026-08-08

## Objective

WQ-5 — an unfinished, interrupted attempt recovers into a known state on
restart; stale `in_progress` becomes reviewable `needs_review`. Durable
worker liveness (pid + worker-start + heartbeat, heartbeat refreshed while
active including paused/waiting) lets startup recovery prove staleness
(dead/missing pid AND expired/missing heartbeat) instead of guessing.
Recovery runs once after migrations, before any pipeline action: stale
active runs become terminal `recovered` (durable reason), the interrupted
`in_progress` job becomes `needs_review` with exactly one idempotent
intervention, a fresh start is allowed afterwards, and a healthy live
worker with a fresh heartbeat is never touched (its run still blocks
duplicate starts with 409). Nothing auto-submits or auto-retries.

## Rules (from the task and repo doctrine)

- Heartbeat at minimum: worker pid + worker-start timestamp + heartbeat
  timestamp; the worker refreshes the heartbeat continuously (paused/waiting
  included). Never rely on `app.state`/in-memory state alone.
- Recovery runs at startup after migrations and before normal pipeline
  actions are accepted.
- Recover only proven-stale runs (dead/missing pid + expired heartbeat).
  Never take over/cancel/alter a healthy fresh-heartbeat worker.
- Idempotent recovery: no duplicate interventions, no double transitions,
  no corrupted counters. Exactly one durable intervention explaining the
  interruption and the next safe action.
- Stale worker-owned `in_progress` job -> `needs_review` only (never
  `ready_to_apply`/`submitted`/`applied`); terminal jobs never downgraded;
  no auto-retry of recovered jobs; no final submit and no real ATS request
  during recovery.
- Preserve the run's id/counters/errors and current/last job context and
  history; status API + dashboard visibly identify a recovered/interrupted
  run and the next safe action.
- New start works after recovery; a healthy active run still blocks
  duplicate start with HTTP 409. Extend only the existing pipeline
  status/dashboard — no decorative UI, no landing page.
- Local fixtures/fakes only in tests; no public URLs, no real ATS, no real
  submissions.
- WQ-4 regressions remain intact.

## Completed work

- Session start verified (2026-08-06): `git fetch origin`;
  `origin/main` == `736242b5ed0...`; worktree safe (only the unstaged WQ-4
  handoff edit + untracked debug artifacts, preserved, never staged);
  branch `checkpoint/wq-5-stale-run-recovery` created from `origin/main`.
- Read AGENTS.md + all relevant APIs: `api/app.py` lifespan,
  `api/routes/pipeline.py`, `api/routes/status.py`,
  `persistence/pipeline_run_repository.py`, `persistence/job_repository.py`,
  `persistence/models.py`, `core/statuses.py`, `config.py`,
  `interventions/store.py`, `pipeline_worker_service.py`,
  `pipeline_worker_runner.py`, `__main__.py`, dashboard
  `app.js`/`styles.css`, `tests/contract/test_migrations.py`,
  `tests/integration/test_pipeline_worker.py`, WQ-5 spec in
  `docs/NEXT_WORKPACKAGES.md`.
- **`migrations/versions/0011_pipeline_worker_liveness.py` (NEW)** — additive
  nullable columns on `pipeline_runs`: `worker_pid` (Integer),
  `worker_started_at` (DateTime), `heartbeat_at` (DateTime); down_revision
  `0010_pipeline_runs`.
- **`persistence/models.py`** — `PipelineRunRow` gains the three liveness
  columns; the documented status set now includes `recovered`.
- **`persistence/pipeline_run_repository.py`** — `TERMINAL_STATUSES` now
  `("cancelled", "completed", "failed", "recovered")` (so a recovered run no
  longer blocks start; `ACTIVE_STATUSES` unchanged); new
  `list_active_pipeline_runs(session)` (oldest first).
- **`config.py`** — `pipeline_heartbeat_timeout_ms` (default 30_000,
  1_000..3_600_000, `UAA_PIPELINE_HEARTBEAT_TIMEOUT_MS`).
- **`core/statuses.py`** — `IN_PROGRESS` allowed transitions now include
  `NEEDS_REVIEW` (recovery moves stale in-progress jobs to it via the store
  guard); new `InterventionKind.RECOVERY = "recovery"` for the idempotent
  interruption intervention.
- **`services/pipeline_recovery_service.py` (NEW)** —
  `recover_stale_pipeline_runs(session_factory, settings)` scans active runs
  once; `run_is_stale(row, timeout)` = live pid? keep (never touch) : stale
  when heartbeat missing/expired. Windows pid liveness uses
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` (on Windows
  `os.kill(pid, 0)` does not raise for dead pids — verified locally).
  `_recover_run` marks the run terminal `recovered` via repository with
  `finished_at`, durable `last_action`/`last_error` reason, preserving
  current_job_id/current_phase/counters/errors. `_recover_current_job`
  moves an `in_progress` job to `needs_review` through
  `update_application_status` (transition guard; terminal jobs skipped) and
  creates exactly one intervention (deterministic id via
  `create_intervention` + stable `RECOVERY_QUESTION`). No browser/adapter/
  submission work anywhere.
- **`services/pipeline_worker_service.py`** — after a successful Popen the
  run row is stamped with `worker_pid`, `worker_started_at`, `heartbeat_at`
  (server-side liveness; the worker refreshes heartbeat from then on).
- **`services/pipeline_worker_runner.py`** — new `_touch_heartbeat()`
  (writes `heartbeat_at`) called at worker start, at every job-boundary
  checkpoint poll, inside the paused `_wait_for_resume` loop (heartbeat stays
  fresh while paused/waiting), and at the start of each job.
- **`api/app.py`** — lifespan runs `recover_stale_pipeline_runs` right after
  the worker service is initialized (post-migrations, before any pipeline
  action); summary on `app.state.pipeline_recovery_summary`; a recovery
  failure is logged but never blocks startup (safe default: stale runs then
  keep blocking start with 409).
- **`ui/static/app.js` + `styles.css`** — `pillClassFor` maps
  `recovered -> uaa-pill-recovered`; the pipeline panel's `isTerminal` set
  includes `recovered` (start button re-enabled after recovery); new
  `.uaa-pill-recovered` style. No other UI changes.
- **`tests/contract/test_migrations.py`** — `CURRENT_HEAD =
  "0011_pipeline_worker_liveness"`.
- **`tests/integration/test_pipeline_recovery.py` (NEW)** — 7 tests, all
  local/no-network: stale run (dead pid + old heartbeat) recovered on
  startup with preserved run context + durable reason, job -> needs_review,
  exactly one RECOVERY intervention, start allowed afterwards and the
  recovered job NOT auto-retried; legacy run without liveness info
  recovered; terminal (applied) job never downgraded + no intervention;
  healthy run (live pid `os.getpid()` + fresh heartbeat) never recovered and
  duplicate start still 409, manual cancel clears it; fresh heartbeat with
  missing pid kept (409); recovery idempotent across a second app restart
  with still exactly one intervention; recovery never creates
  `submission_results` rows nor `submitted`/`applied` jobs.
- Existing WQ-4 restart test keeps passing unchanged (a freshly-killed
  worker's run still holds a fresh heartbeat -> kept; cancel then works —
  the intended hysteresis).
- **Acceptance-proof strengthening (2026-08-08)** —
  `tests/integration/test_pipeline_worker.py::TestPauseAndResume::test_paused_worker_updates_durable_heartbeat`
  now also (a) asserts the run does not become `recovered` during the
  healthy pause (both before and after the wait), (b) explicitly waits for
  the worker subprocess to exit after cancel
  (`worker._proc.wait(timeout=10)` + `poll() is not None`), making the
  no-ResourceWarning proof visible (the global
  `filterwarnings = ["error::ResourceWarning"]` config fails the test if
  the worker leaks). The test still does not fake the heartbeat: every
  `heartbeat_at` advance is performed by the worker subprocess.
  `tests/playwright/test_pipeline_recovery_dashboard.py::test_dashboard_visibly_renders_recovered_run_and_guidance`
  now also asserts `uaa-pill-recovered` IS present on `#run-status`
  (positive proof the run is styled as recovered, not merely "not
  running"), alongside the existing `uaa-pill-running` absence check and
  the controls-state check. Both tests keep their prior assertions (no
  weakening).

## Changed files (uncommitted)

- `migrations/versions/0011_pipeline_worker_liveness.py` (new)
- `src/universal_auto_applier/persistence/models.py` (liveness columns)
- `src/universal_auto_applier/persistence/pipeline_run_repository.py`
  (recovered terminal, `list_active_pipeline_runs`)
- `src/universal_auto_applier/config.py` (`pipeline_heartbeat_timeout_ms`)
- `src/universal_auto_applier/core/statuses.py` (IN_PROGRESS -> NEEDS_REVIEW,
  `InterventionKind.RECOVERY`)
- `src/universal_auto_applier/services/pipeline_recovery_service.py` (new)
- `src/universal_auto_applier/services/pipeline_worker_service.py`
  (liveness stamp after Popen)
- `src/universal_auto_applier/services/pipeline_worker_runner.py`
  (`_touch_heartbeat` in checkpoint/pause/job-start paths)
- `src/universal_auto_applier/api/app.py` (startup recovery call)
- `src/universal_auto_applier/ui/static/app.js`, `ui/static/styles.css`
  (`recovered` pill + isTerminal)
- `tests/contract/test_migrations.py` (CURRENT_HEAD)
- `tests/integration/test_pipeline_recovery.py` (new)
- `tests/integration/test_pipeline_worker.py`
  (`test_paused_worker_updates_durable_heartbeat` strengthened: subprocess
  exit + no-recovery assertions)
- `tests/playwright/test_pipeline_recovery_dashboard.py`
  (`test_dashboard_visibly_renders_recovered_run_and_guidance` strengthened:
  positive `uaa-pill-recovered` class assertion + clearer error messages)

## Tests and results (2026-08-08, working tree on top of `cdec5e6`)

- `python -m ruff check src tests migrations` -> All checks passed.
- `python -m ruff format --check src tests migrations` -> 176 files already formatted.
- `python -m pyright` -> 0 errors, 0 warnings, 0 informations.
- `python -m pyright --pythonplatform Linux` -> 0 errors, 0 warnings, 0 informations.
- `python -m pytest tests/unit tests/contract tests/integration -q` -> 1052 passed.
- `python -m pytest tests/playwright -q -m "not live"` -> 186 passed.
- `git diff --check` clean; untracked debug artifacts (`tmp_final_pipeline/`)
  untouched/unstaged.

## Decisions made

- Recovery runs in the app lifespan (migrations already run in
  `__main__.py`/test helpers before `create_app`), not inside a migration:
  it is runtime state repair with store API access.
- Staleness = dead/missing pid AND expired/missing heartbeat. A fresh
  heartbeat (even with a missing pid) keeps the run — recovery is
  conservative and never guesses.
- Windows pid liveness via `OpenProcess` (verified: `os.kill(pid, 0)` is a
  no-op for dead pids on Windows).
- `recovered` is a run-level terminal status (repository-owned), NOT a job
  status; the job becomes `needs_review` only, preserving the submission
  gate (review_ready -> submitted -> applied path untouched).
- `update_application_status` (guarded transition) used for the job change;
  the deterministic `create_intervention` id keeps the interruption
  intervention exactly-once even across re-runs.
- Recovery failure never blocks startup (safe default = stale runs keep
  returning 409).
- WQ-4's "cancel with no live worker marks cancelled" path stays as the
  manual fallback for fresh-heartbeat rows.

## Blockers / risks

- None. CI verified all 5 jobs on PR head `cdec5e6` (the first test commit).
  The acceptance-proof strengthening commit is on top of `cdec5e6`; CI will
  re-run on push. Awaiting PR #8 review/merge; no merge or push to `main`
  without the reviewed PR (exactly once).
- `gh` on PATH is a browser-opener shim; use the GitHub REST API (token via
  `git credential-manager` on the `.../UniversalAutoApplier.git` remote).
- Untracked debug artifacts (`tmp_debug_status.py`, `tmp_debug_status/`,
  `tmp_final_pipeline/`) stay out of commits.

## CI results (2026-08-06, PR #8 head `4cedb40` — implementation only)

- verify-linux (4 jobs: Python 3.11, 3.12, 3.13, 3.14) → success
  https://github.com/MohamedAzzam4/UniversalAutoApplier/actions/runs/31067711762
- verify-windows-py314 (Windows + Python 3.14) → success
  https://github.com/MohamedAzzam4/UniversalAutoApplier/actions/runs/31067711731
- First Linux attempt failed on pyright (`ctypes.windll` unknown on Linux);
  fixed in `4cedb40` (`getattr(ctypes, "windll", None)` + `Any`), verified
  locally with `pyright --pythonplatform Linux`.

## CI results (2026-08-07, PR #8 head `cdec5e6` — first test commit)

- verify-linux (4 jobs: Python 3.11, 3.12, 3.13, 3.14) → success
  https://github.com/MohamedAzzam4/UniversalAutoApplier/actions/runs/31209265720
- verify-windows-py314 (Windows + Python 3.14) → success
  https://github.com/MohamedAzzam4/UniversalAutoApplier/actions/runs/31209265724

## CI results (2026-08-08, PR #8 head after acceptance-proof strengthening)

- Pending push; will be re-verified after the new commit lands on the branch.

## Exact next action

1. Await PR #8 review. If changes requested: make them on the WQ-5 branch,
   re-run the full local gate, push; update the PR body handoff section.
2. After merge: update `docs/CURRENT_STATE.md` (main advanced) and audit
   `docs/NEXT_WORKPACKAGES.md` for follow-ups (WQ-6+ are still backlog).
3. Before any subsequent handoff/review run, resolve the head dynamically and
   compare to origin:

   ```text
   git fetch origin
   git rev-parse HEAD
   git rev-parse origin/checkpoint/wq-5-stale-run-recovery
   ```

   The two values must match. Re-verify the base reference (`736242b5`).

## Rules

- Never merge or push to `main` directly — only through the reviewed PR,
  exactly once. Preserve `checkpoint/*` branches.
- Only commit what the workpackage asked for; never commit live-runs data,
  `.uaa_data`, `.env`, browsers/databases, screenshots, or the tmp debug
  dirs. `git diff --check` before committing.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.

## Session protocol reminder

At session start: `git fetch origin`, verify repo/branch/base SHA, inspect
`git status --short`, read the handoff pack, stop if local changes could be
overwritten. During work: update this file after every major milestone;
checkpoint before ~60-70% context and before pausing/handoff/switching AI;
never rely on chat history as project memory.

## Rules

- Unknown platforms never auto-submit. Untrusted adapters never submit.
- Recovery performs no browser work, no adapter calls, no submission-table
  writes, and never auto-retries a recovered job.
- Keep the handoff files updated when this state changes.
