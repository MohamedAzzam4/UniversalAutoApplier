# Active Workpackage

- **WP ID:** WQ-4 — background browser pipeline (`pipeline_runs` durable state, subprocess worker, stable start/pause/resume/cancel/status API, dashboard integration).
- **Status:** implementation complete and all local gates green; handoff checkpoint ready; awaiting commit + push + CI + review. Full gate on 2026-08-06.
- **Branch:** `checkpoint/wq-4-background-browser-pipeline`
- **Base SHA:** `c9f5e23020cb083453ce116e6be836dc9420f966` (`origin/main`)
- **Last completed/checkpoint SHA:** `662e4d81500f02582fc46d04590f02b5c8a19395` (pushed to origin, at branch head before the WQ-4 work below)
- **Branch-head verification (must be run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-4-background-browser-pipeline
  ```

  The two resolved values must match before handoff/review.
- **Last updated:** 2026-08-06

## Objective

WQ-4 — durable background browser pipeline. Replace the thread-based pipeline
worker with a dedicated worker subprocess, add a durable `pipeline_runs` table
(model + repository + migration), expose a stable
`POST/POST/POST/POST/GET /api/pipeline/{start,pause,resume,cancel,status}`
contract, and restore meaningful integration/regression coverage so all CI
jobs go green.

## Rules (from the task and repo doctrine)

- Browser (Playwright) work runs only in a dedicated worker subprocess — never
  in a FastAPI background thread (not stable on Python 3.13/3.14 and leaks
  resources guaranteed to fail under `filterwarnings = ["error"]`).
- Durable run state: run id, status, mode, current job/phase/action, counters,
  timestamps, cancel_reason, and errors survive restart. WQ-5 owns stale
  `in_progress` job recovery.
- Start returns promptly; exactly one active run; pause happens between jobs;
  cancel prevents the next job while the current job finishes safely; browser
  cleanup on cancel.
- `fixture_html` is honored (fixture mode = generic orchestrator dry-run, no
  browser, no network) and documented; no external hosts in tests (local
  fixture server or unused 127.0.0.1 ports only).
- The pipeline never performs final submission; no job may become
  `SUBMITTED`/`APPLIED`; `UAA_ENABLE_REAL_SUBMISSION=false` is forced in the
  worker environment.
- Tests must be meaningful (no vacuous assertions, no `_ = ...` discards, no
  skip/weakening). Durable state observed through the API + DB.
- Every CI failure is a blocker. Fix ResourceWarning leaks correctly — never
  suppress globally; the pre-existing untracked debug artifacts
  (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`) are
  preserved and never committed.

## Completed work

- Session start verified (2026-08-06): `git fetch origin`; branch
  `checkpoint/wq-4-background-browser-pipeline` at `662e4d8`; merge-base with
  `origin/main` = `c9f5e23`; `git status` clean apart from untracked debug
  artifacts.
- Read the WQ-4 spec (`docs/NEXT_WORKPACKAGES.md`) and the current code,
  including the old thread-based worker, old pipeline API routes, orchestrator,
  `candidate_profile_loader`, persistence models/repositories,
  `api/app.py` lifespan, `browser/live_runner.py`, `interventions/fill_bridge.py`,
  old integration/unit/contract tests, greenhouse fixture, and the proven
  local-harness pattern.
- **Durable foundational state (from the earlier checkpoint commit `662e4d8`):**
  - `PipelineRunRow` in `persistence/models.py` with `errors_json: list[dict]`.
  - `migrations/versions/0010_pipeline_runs.py` (down_revision
    `0009_queue_import_runs`; additive table only).
  - `persistence/pipeline_run_repository.py::create_pipeline_run /
    get_pipeline_run / list_pipeline_runs / get_active_pipeline_run /
    get_latest_pipeline_run / update_pipeline_run / mark_pipeline_run_terminal /
    pipeline_run_to_dict` with `ACTIVE_STATUSES` (`running/pausing/paused/
    cancelling`) and `TERMINAL_STATUSES` (`cancelled/completed/failed`).
- **`services/pipeline_worker_runner.py` (NEW) — worker subprocess entrypoint.**
  - CLI: `python -m universal_auto_applier.services.pipeline_worker_runner
    --data-dir DIR --run-id ID [--max-jobs N] [--fixture-file PATH]
    [--job-pulse-ms MS]`.
  - Exit codes: 0 = finished (completed/cancelled), 2 = run row missing or in
    an unknown state, 3 = worker itself failed.
  - Reads the run row; contradicts true startup handling: if `cancelling`
    at first read (control raced worker startup) → marks terminal `cancelled`
    and exits 0; if `paused`/`pausing` at startup → waits for resume; if
    already terminal → exits 0; only unknown states refuse (exit 2).
  - Loads eligible jobs (`READY_TO_APPLY` + `QUEUED`, capped at `max_jobs`)
    from the shared SQLite database.
  - Fixture mode: `PipelineOrchestrator.process_job(job, fixture_html)`; live
    mode: `resolve_candidate_profile(job.metadata)` +
    `LiveBrowserRunner.run(job, candidate=..., qa_service=None)`.
  - Control channel = re-reading the run row at every job boundary
    (`_at_checkpoint` with `pulse_ticks`, `_wait_for_resume` honoring
    `pausing/paused/cancelling/terminal`); `--job-pulse-ms` gives the dashboard
    a deterministic pause/cancel window.
  - Per-job error boundary sets `FAILED`, bumps `jobs_failed`, persists
    `last_error` + `errors_json`; counters/terminal state all go through the
    repository. `main()` disposes the engine in `finally`.
- **`services/pipeline_worker_service.py` (REWRITTEN) — subprocess launcher**
  with `start(max_jobs=10, fixture_html=None)` (409-style `RuntimeError` when
  an active run exists; fixture HTML written to
  `<data_dir>/fixtures/run-<hex>.html`; `create_pipeline_run`; Popen of the
  module CLI; OSError → run marked failed + re-raised; stdout/stderr drain
  threads), `pause()`/`resume()`/`cancel(reason="User cancelled")` (direct
  terminal recovery when no live process), `shutdown()` (terminate, 5s then kill),
  `get_state_dict()` via repository (idle dict when not started).
  `_worker_env()` forces `UAA_ENABLE_REAL_SUBMISSION=false`.
- **`api/routes/pipeline.py` (REWRITTEN)** — stable `PipelineRunState` model;
  `PipelineStartRequest(fixture_html: str|None, max_jobs: int 1..100)` with
  OpenAPI description documenting `fixture_html` as test-only; 409 on
  active-run, 503 when the worker is uninitialized; GET `/status` pollable.
- **`api/routes/status.py`** now reads `worker.get_state_dict()` (durable rows)
  instead of the removed in-memory state. **`api/app.py`** lifespan finalizer
  calls `app.state.pipeline_worker.shutdown()` before engine disposal.
- **`config.py`**: `pipeline_job_pulse_ms` (default 0, `UAA_PIPELINE_JOB_PULSE_MS`)
  parsed via `_parse_int(..., 0, 0, 60000)`.
- **`pipeline_orchestrator.py`**: public `process_job(job, fixture_html=None)`
  wrapper around `_process_job` so the worker can iterate jobs with pause/cancel.
- **`tests/contract/test_migrations.py`**: `CURRENT_HEAD = "0010_pipeline_runs"`.
- **`tests/unit/test_pipeline_orchestrator.py`** TestPipelineStartAPI tests
  updated for the durable contract (409 via active row; status via state dict).
- **`tests/integration/test_api_candidate_profile.py` (REWRITTEN)** — proves
  `POST /start` loads the candidate profile: with a profile snapshot the
  run completes, the job reaches a review state, and no
  first/last/email `llm_metadata.field_label`-style interventions are created;
  without a profile those fields DO become interventions (each with
  `field_type`); never `SUBMITTED`/`APPLIED`.
- **`tests/integration/test_pipeline_worker.py` (REWRITTEN)** — 9 tests, single
  lifespan per app (engine created and disposed exactly once):
  start-returns- promptly (no jobs → `completed`, totals 0), duplicate start
  409 + cancel terminates, pause/resume keep counters and block the next job,
  resume-without-pause 409, cancel stops before the next job and the worker
  subprocess exits, paused state survives an app restart and a duplicate start
  is refused until cancelled (direct recovery) then a fresh run completes only
  the remaining eligible job, no job becomes submitted/applied, a failed live
  job records a durable `errors_json` entry, and one failed job does not erase
  earlier results. Traffic stays local (local fixture HTTP server or unused
  127.0.0.1 ports).
- **`tests/playwright/test_phase7_adapter_dry_run.py`, `test_final_pipeline.py`,
  `test_llm_acceptance.py`** — updated for the WQ-4 contract: `start` now
  returns `running` and tests poll `/api/pipeline/status` until terminal (the
  tests previously asserted synchronous `completed`); the resume-UI test now
  drives a local fixture form (no external URL) and asserts a terminal review
  state (review_ready or needs_user_input; never submitted/applied/in_progress).
- **`tests/unit/test_phase7_regression.py`** TestDashboardStartSafety — now
  polls the durable status to terminal and asserts real invariants
  (jobs_completed == 1, job in a review state, never SUBMITTED/APPLIED).

## Changed files

- `src/universal_auto_applier/services/pipeline_worker_runner.py` (new)
- `src/universal_auto_applier/services/pipeline_worker_service.py` (rewritten)
- `src/universal_auto_applier/api/routes/pipeline.py` (rewritten)
- `src/universal_auto_applier/api/routes/status.py` (worker-backed status)
- `src/universal_auto_applier/api/app.py` (shutdown hook)
- `src/universal_auto_applier/config.py` (`pipeline_job_pulse_ms`)
- `src/universal_auto_applier/services/pipeline_orchestrator.py` (`process_job`)
- `src/universal_auto_applier/persistence/models.py` (`PipelineRunRow`,
  `errors_json`)
- `src/universal_auto_applier/persistence/pipeline_run_repository.py` (new)
- `migrations/versions/0010_pipeline_runs.py` (new)
- `tests/contract/test_migrations.py` (CURRENT_HEAD)
- `tests/unit/test_pipeline_orchestrator.py`, `tests/unit/test_phase7_regression.py`
- `tests/integration/test_pipeline_worker.py` (rewritten),
  `tests/integration/test_api_candidate_profile.py` (rewritten)
- `tests/playwright/test_phase7_adapter_dry_run.py`,
  `tests/playwright/test_final_pipeline.py`,
  `tests/playwright/test_llm_acceptance.py`

## Tests and results (2026-08-06, on the WQ-4 branch + working tree)

- `python -m ruff check src migrations tests` → All checks passed.
- `python -m ruff format --check src migrations tests` → 173 files already formatted.
- `python -m pyright` → 0 errors, 0 warnings, 0 informations.
- `python -m pytest tests/unit tests/contract tests/integration -q` → 1044 passed.
- `python -m pytest tests/playwright -q` → 185 passed (20 phase-7 adapter dry-run
  tests, final pipeline, resume UI, remaining suite).
- `python -m pytest -m "not live" -q` (the CI full run) → 1229 passed,
  1 deselected (opt-in live).
- `git diff --check` clean; untracked debug artifacts not staged.

## Decisions made

- Subprocess over in-process thread (stability on Python 3.13/3.14, resource
  cleanup). Control via the durable run row (no IPC).
- `UAA_ENABLE_REAL_SUBMISSION=false` forced in the worker env regardless of the
  server settings.
- Worker tolerates startup control races: `cancelling`/`paused`/`pausing`/
  terminal states reached before the worker's first read are honored, not
  refused (this was the "stuck cancelling" bug).
- Integration tests never enter a second lifespan on the same app (engine
  created/disposed exactly once via `_running_app`); leak-tested under
  `filterwarnings = ["error"]` including `ResourceWarning`.
- `fixture_html` honored (fixture mode) and treated as a test-only surface in
  the OpenAPI description.
- Dashboard start/resume default to live dry-run mode (no fixture); the
  Playwright resume test runs against a local form server and asserts a
  terminal review state rather than a hardcoded `review_ready`.

## Blockers / risks

- CI (push to main / PR to main) has never run with these WQ-4 changes yet.
  The full local gate is green on 3.14, but real CI still needs a run.
- `gh` on PATH is a browser-opener shim; use the GitHub REST API (token via
  `git credential-manager` on the `.../UniversalAutoApplier.git` remote) for
  PR creation/updates.
- Untracked debug artifacts (`tmp_debug_status.py`, `tmp_debug_status/`,
  `tmp_final_pipeline/`) must stay out of commits.

## Exact next action

1. Review `git status --short` + `git diff --check`, then create the WQ-4
   checkpoint commit (this handoff doc is included) and push to
   `checkpoint/wq-4-background-browser-pipeline`.
2. Trigger CI. Open PR against `main` from the WQ-4 branch (GitHub REST API;
   do not merge). Wait for all 5 jobs (Linux 3.11/3.12/3.13/3.14 + Windows
   3.14) to be green. If the new worker subprocess tests fail on any Python
   version, treat every failure as actionable.
3. Update `docs/CURRENT_STATE.md` once `main` advances / after merge.
4. Before handoff/review run:

   ```text
   git fetch origin
   git rev-parse HEAD
   git rev-parse origin/checkpoint/wq-4-background-browser-pipeline
   ```

   The two values must match. Re-verify the base reference (`c9f5e23`) and
   resolve the current HEAD dynamically.
5. Final WQ-4 report: exact SHAs, changed files + rationale per requirement,
   behavior (durable state, subprocess worker, control semantics, no
   submission), schema (migration notes), test evidence + commands, CI URLs
   per job, limitations, and explicit READY/NOT-READY.

## Rules

- Never merge or push to `main` directly — only through the reviewed PR,
  exactly once.
- Preserve `checkpoint/*` branches.
- Only commit what the workpackage asked for; never commit live-runs data,
  `.uaa_data`, `.env`, browsers/Databases, screenshots, or the tmp debug dirs.
- Do not embed a "current HEAD" SHA in this file.

## Session protocol reminder

At session start: `git fetch origin`, verify repo/branch/base SHA, inspect
`git status --short`, read the handoff pack, stop if local changes could be
overwritten. During work: update this file after every major milestone;
checkpoint before ~60-70% context and before pausing/handoff/switching AI;
never rely on chat history as project memory.

## Rules

- Unknown platforms never auto-submit. Untrusted adapters never submit.
- No real submission happens from WQ-4 code: the worker performs only dry-run
  and review states (rigorous trace through the pipeline path).
- Keep the handoff files updated when this state changes.