# Active Workpackage

- **WP ID:** WQ-3 — UAA production queue import, API, startup integration, and dashboard visibility.
- **Status:** in progress — implementation complete; gate green; docs updated; checkpoint commit pending.
- **Branch:** `checkpoint/wq-3-uaa-production-queue-import`
- **Base SHA:** `3ddc4becdab1dac9cc8b867c82c190fc42178f51` (`origin/main`)
- **Last completed/checkpoint SHA:** pending first checkpoint commit (implementation + tests done; commit before continuing).
- **Branch-head verification (must be run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-3-uaa-production-queue-import
  ```

  The two resolved values must match before handoff/review.
- **Last updated:** 2026-08-05

## Objective

JobHunter now produces an atomic `application_queue.jsonl`. UAA must import
that configured queue through a real local CLI/API/dashboard workflow, persist
the result durably, and show the user what happened. This workpackage must not
start a browser, fill a form, or submit an application.

## Rules (from the task and repo doctrine)

- Record every import run durably (survives restart), driven by one named
  service — the only production entry point for queue import.
- Calls the existing contract importer (`application_queue.importer.import_queue_file`);
  never reimplement JSONL validation. Valid-row behavior preserved when other
  lines are malformed (partial runs).
- Re-import is idempotent; never erases terminal job states or previous
  attempts; never starts the pipeline automatically after import.
- `UAA_QUEUE_PATH` (absolute; no personal path hard-coding, no folder scanning)
  and `UAA_IMPORT_QUEUE_ON_STARTUP` (default false). Startup import is opt-in;
  failures surface in health/status/dashboard without crashing the server.
- API endpoints POST `/api/queue/import` (only the configured path; never a
  browser-supplied arbitrary path) and GET `/api/queue/status`. HTML errors for
  missing configuration (400) and concurrent imports (409).
- DashboardQueue section shows configured/not-configured, Import control, latest
  result + timestamp, imported/skipped/error counts, readable error state, and
  job summary. Import and pipeline-start controls remain separate. The UI never
  implies import applies/submits jobs.
- No direct JSON/database mutation outside repository/store/service boundaries.
- No in-memory-only import status. No schema changes to compensate for malformed
  JobHunter rows. No unrelated refactors.
- Update `docs/CURRENT_STATE.md` (WQ-1 transitions merged, JobHunter WQ-2 merged
  at JobHunter main `0e8ba2f`, WQ-3 accurate, real browser orchestration later).

## Completed work

- Session-start protocol done: fetched origin; verified `origin/main` == base
  `3ddc4bec...`; `git status --short` shows only pre-existing untracked
  artifacts (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`)
  which are preserved and excluded from commits.
- Created branch `checkpoint/wq-3-uaa-production-queue-import` from `origin/main`.
- Read: `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/NEXT_WORKPACKAGES.md`,
  `handoffs/ACTIVE_WORKPACKAGE.md`, `docs/generalization/DATA_CONTRACTS.md`,
  and existing importer / config / persistence models / migrations / API routes /
  health service / pipeline route / CLI / dashboard static files / tests.
- Confirmed no contract conflict: JobHunter's merged export lines already
  validate against the existing `ApplicationJob` contract importer; the WQ-3
  migration is additive-only (new `queue_import_runs` table) so no existing
  job/submission/intervention history is at risk; startup import only touches
  the database through the importer, never the browser.
- Implemented configuration: `Settings.queue_path` (primary) +
  `import_queue_on_startup` (opt-in, default false); loader maps
  `UAA_QUEUE_PATH` (primary) with `UAA_JOBHUNTER_QUEUE` as legacy fallback;
  `settings.jobhunter_queue` is now a read-only alias of `queue_path`;
  `.env.example` updated.
- Implemented `persistence/models.py::QueueImportRunRow` and additive migration
  `migrations/versions/0009_queue_import_runs.py` (down_revision
  `0008_reconcile_submission_statuses`) — new table only, no data modification.
- Implemented `services/queue_import_service.py`: `QueueImportService`
  (app-scoped named service, non-blocking `threading.Lock`), states
  `success/partial/failed/skipped`, durably persisted runs with sha256 fingerprint,
  structured `{line_number, error}` row errors (raw JSONL never stored), bounded
  safety-limited failure reasons, `latest_run()` / `job_summary()` / `status()`,
  and `run_startup_import` (never raises; missing path -> failed, unconfigured ->
  skipped, concurrent -> returns None).
- Implemented `api/routes/queue_import.py`: `POST /api/queue/import`
  (400 unconfigured, 409 concurrent, 200 with durable run summary incl. persisted
  `failed` when file missing; no arbitrary-path parameter) and
  `GET /api/queue/status` (configured/path/startup flag/source_exists/latest
  durable run/queue-job summary). Wired into `api/app.py` lifespan (optional
  opt-in startup import) and router, registered BEFORE the dynamic
  `/api/queue/{application_id}` route so the fixed paths win.
- Implemented queue-import CLI: `python -m universal_auto_applier queue-import
  [--path <abs>]` (`cli.py::run_command`, `__main__.py` subcommand set).
- Extended health: `queue_import` component in `services/health_service.py`
  (`_check_queue_import`, `session_factory` kwarg on `build_health_report`;
  `make_health_report` passes it through); existing component-name contract
  preserved.
- Extended dashboard: `ui/static/index.html` Queue Import card (configured
  path, startup flag, Import Queue button, durable last-run details, job
  summary, safety note) and `app.js` (`loadQueueStatus`, pill classes, button
  POST handler). Import and pipeline-start controls remain separate.
- Tests added and passing:
  - `tests/unit/test_config_queue_import.py` (path resolution, legacy fallback,
    startup-flag parsing; 8 tests).
  - `tests/contract/test_queue_import_service.py` (success/partial/failed/skipped,
    empty file, missing file, unconfigured/relative rejection, concurrency lock +
    release, durability across instances, fingerprint change, status payload,
    startup runner incl. concurrent -> None, no-browser/no-pipeline import
    guarantee; 21 tests).
  - `tests/integration/test_queue_import_api.py` (POST import success/idempotent
    durable/partial row errors/missing file 200-failed/unconfigured 400/localpath
    never accepted/concurrent 409 incl. two-request race; GET status initial +
    after; startup opt-in/opt-out/missing-file; health component ready/
    not_configured/invalid; api-root lists new endpoints; 18 tests).
  - `tests/playwright/test_queue_import_dashboard.py` (card renders
    configured/not-configured, import button produces durable run in the grid,
    import never creates submission/system-run rows and keeps job at `evaluated`;
    4 tests).
  - Bumped `tests/contract/test_migrations.py` `CURRENT_HEAD` to
    `0009_queue_import_runs`.
- Ran full gate on 2026-08-05 (results below).

## Changed files

- `.env.example` (UAA_QUEUE_PATH, UAA_IMPORT_QUEUE_ON_STARTUP)
- `src/universal_auto_applier/config.py` (queue_path, import_queue_on_startup,
  loader, jobhunter_queue alias)
- `src/universal_auto_applier/persistence/models.py` (QueueImportRunRow)
- `migrations/versions/0009_queue_import_runs.py` (new)
- `src/universal_auto_applier/services/queue_import_service.py` (new)
- `src/universal_auto_applier/api/routes/queue_import.py` (new)
- `src/universal_auto_applier/api/app.py` (router wiring + ordering, lifespan
  startup import, endpoint list)
- `src/universal_auto_applier/services/health_service.py` (queue_import
  component + session_factory kwarg)
- `src/universal_auto_applier/cli.py` + `__main__.py` (queue-import command)
- `src/universal_auto_applier/ui/static/index.html` + `app.js` (Queue Import card)
- `tests/unit/test_config_queue_import.py` (new)
- `tests/contract/test_queue_import_service.py` (new)
- `tests/integration/test_queue_import_api.py` (new)
- `tests/playwright/test_queue_import_dashboard.py` (new)
- `tests/contract/test_migrations.py` (CURRENT_HEAD bump only)

## Tests and results

Full gate run 2026-08-05 on the WQ-3 branch:

- `python -m ruff check src tests migrations` -> All checks passed.
- `python -m ruff format --check src tests migrations` -> 169 files already formatted.
- `python -m pyright` -> 0 errors, 0 warnings, 0 informations.
- `python -m pytest tests/unit tests/contract tests/integration -q` -> 1035 passed.
- `python -m pytest tests/playwright -q` -> 185 passed.
- `python -m pytest -q` (all markers) -> 1220 passed, 1 skipped (opt-in live browser test, as designed).
- Concurrency + startup subset repeated 8x -> 7 passed each run (56/56).
- `git diff --check` clean; untracked debug artifacts not staged.

## Decisions made

- Configuration: add `UAA_QUEUE_PATH` -> `Settings.queue_path` (primary) and
  `UAA_IMPORT_QUEUE_ON_STARTUP` -> `import_queue_on_startup` (default false).
  `UAA_JOBHUNTER_QUEUE` behaves as a fallback to keep the existing health
  contract and tests intact; `settings.jobhunter_queue` becomes a read-only
  alias of `queue_path`.
- Import-run states: `success` (no row errors; empty file is a valid empty
  queue => success,0), `partial` (row errors with at least one imported),
  `failed` (row errors with zero imported, or unreadable/missing file),
  `skipped` (startup enabled but queue path not configured).
- Persisted row errors store only `{line_number, error}` — never the raw line
  (avoids storing raw candidate data).
- POST `/api/queue/import`: 400 when not configured, 409 when a concurrent
  import is in flight (non-blocking thread lock on the app-scoped service);
  otherwise 200 with the durable run summary (a missing file is a persisted
  `failed` run, surfaced in the response and in status).
- Startup handler runs one import only when `import_queue_on_startup` is true;
  missing path -> persisted `failed` run; unconfigured -> persisted `skipped`
  run; neither crashes the server. Latest durable run drives
  GET `/api/queue/status` and a new `queue_import` health component.

## Blockers / risks

- `gh` on PATH is a browser-opener shim; no `GITHUB_TOKEN`/gh config. PR
  creation/update must use the GitHub REST API (token via `git
  credential-manager` on the `.../UniversalAutoApplier.git` remote).
- Untracked debug artifacts (`tmp_debug_status.py`, `tmp_debug_status/`,
  `tmp_final_pipeline/`) must stay out of commits.

## Exact next action

1. Update `docs/CURRENT_STATE.md` (WQ-1 transitions merged, JobHunter WQ-2
   merged, WQ-3 implemented; real browser orchestration still later).
2. Checkpoint commit of the implemented+tested WQ-3 milestone on this branch;
   push to origin. Command:

   ```text
   git status --short
   git diff --check
   git add .env.example docs/handoffs/ACTIVE_WORKPACKAGE.md \
     src/universal_auto_applier/__main__.py src/universal_auto_applier/api/app.py \
     src/universal_auto_applier/cli.py src/universal_auto_applier/config.py \
     src/universal_auto_applier/persistence/models.py \
     src/universal_auto_applier/services/health_service.py \
     src/universal_auto_applier/ui/static/app.js \
     src/universal_auto_applier/ui/static/index.html \
     tests/contract/test_migrations.py \
     migrations/versions/0009_queue_import_runs.py \
     src/universal_auto_applier/api/routes/queue_import.py \
     src/universal_auto_applier/services/queue_import_service.py \
     tests/contract/test_queue_import_service.py \
     tests/integration/test_queue_import_api.py \
     tests/playwright/test_queue_import_dashboard.py \
     tests/unit/test_config_queue_import.py
   git commit -m "feat(wq-3): durable queue import service, API, CLI, dashboard"
   git push -u origin checkpoint/wq-3-uaa-production-queue-import
   ```

3. Confirm the pushed head (``git rev-parse HEAD`` vs
   ``git rev-parse origin/checkpoint/wq-3-uaa-production-queue-import``).
4. Create the PR via GitHub REST API (token via `git credential-manager` on the
   `.../UniversalAutoApplier.git` remote; `gh` is a browser-opener shim). See
   blocking note below. Do NOT merge.
5. Wait for Linux + Windows CI to pass on the PR head; collect run URLs.
6. Keep this file updated at each milestone; final WQ-3 report with branch/base/
   final SHAs, changed files, behavior, schema, tests, repeated-test evidence,
   CI URLs, PR URL/state, limitations, and explicit confirmation that no
   browser/submission path runs during import.

## Session protocol reminder

At session start: `git fetch origin`, verify repo/branch/base SHA, inspect
`git status`, read the handoff pack, stop if local changes could be overwritten.
During work: update this file after every major milestone; checkpoint before
~60-70% context and before pausing/handoff/switching AI; never rely on chat
history as project memory.

## Rules

- Unknown platforms never auto-submit. Untrusted adapters never submit through
  adapter `submit_or_pause`.
- No real submission happens from WQ-3 code: import only writes to the database
  through the contract importer; it never launches a browser or starts a
  pipeline.
- Keep the handoff files updated when this state changes.