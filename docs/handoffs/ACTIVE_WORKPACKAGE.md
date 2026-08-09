# Active Workpackage

- **WP ID:** WQ-6 — cross-repository orchestration controls (JobHunter export → UAA import → UAA pipeline).
- **Status:** IN PROGRESS — implementation complete, all local gates green, PR opened.
- **Branch:** `checkpoint/wq-6-cross-repo-orchestration`
- **Base SHA:** `c7b6b2c672a7e96be643c01b2add761e78865967` (`origin/main`, WQ-5 merge commit)
- **PR:** https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/9 (open, not draft, not merged)
- **Last updated:** 2026-08-10

## Objective

Make UAA the local control plane that can run JobHunter and UAA together
from the dashboard. The user can choose:

- **Sequential mode:** JobHunter completes, then UAA imports the queue,
  then UAA starts its safe browser pipeline.
- **Parallel mode:** UAA processes existing queued jobs while JobHunter
  searches/evaluates new jobs concurrently. When JobHunter finishes, UAA
  imports the queue once.

This workpackage is dry-run/review-only. It never performs final submission.

## Completed work

- **Migration `0012_orchestration_runs`** — new `orchestration_runs` table
  with mode, status, phase, JobHunter child PID/exit code, bounded
  stdout/stderr, queue import result, pipeline run link, errors, timestamps.
- **`persistence/orchestration_run_repository.py`** — repository with
  create/update/get/list/terminal-mark helpers + `to_dict` serializer.
- **`persistence/models.py`** — `OrchestrationRunRow` ORM model.
- **`config.py`** — new settings: `jobhunter_repo`, `jobhunter_python`,
  `jobhunter_entry_point`, `jobhunter_queue_output`, `orchestration_mode`,
  `orchestration_cancel_grace_seconds`, `orchestration_capture_max_bytes`.
- **`services/jobhunter_runner.py`** — subprocess boundary: launches
  JobHunter as an external process (never imports its modules), captures
  stdout/stderr with bounded storage + secret filtering, uses `communicate()`
  for safe pipe cleanup, cancel uses graceful-then-forced termination.
- **`services/orchestration_service.py`** — coordinates JobHunter → import
  → pipeline in sequential or parallel mode. Only one active run (409 on
  duplicate). Durable state survives restart. Startup recovery marks
  orphaned runs as failed. No auto-retry.
- **`services/pipeline_worker_service.py`** — added `_drain_threads` tracking
  and `shutdown()` waits for drain threads + previous proc reap.
- **`api/routes/orchestration.py`** — 3 endpoints: POST start, POST cancel,
  GET status. 409 on duplicate, 400 on config error.
- **`api/app.py`** — wires orchestration service into lifespan, calls
  `recover_on_startup()` after pipeline recovery, calls `shutdown()` on exit.
- **`ui/static/index.html` + `app.js`** — dashboard card with mode dropdown,
  status pill, JobHunter/queue-import state, phase/action/error, start/cancel
  controls with correct enabled states.
- **`tests/fixtures/fake_jobhunter/run_export_queue.py`** — local fake
  JobHunter that writes a valid queue atomically; supports `--delay`,
  `--fail`, `--jobs`, `--secret-leak` for test control.
- **`tests/integration/test_orchestration.py`** — 16 integration tests
  covering sequential ordering, JobHunter failure prevention, parallel mode,
  idempotency, 409 duplicate, cancellation, restart recovery, no submission,
  no SUBMITTED/APPLIED, paths with spaces, bounded capture + secret filter,
  JobHunterRunner unit tests.
- **`tests/playwright/test_orchestration_dashboard.py`** — 3 Playwright
  tests proving mode selection, phase visibility, progress, and JobHunter/
  queue-import state rendering.
- **`tests/contract/test_migrations.py`** — `CURRENT_HEAD` updated to
  `0012_orchestration_runs`.

## Changed files

- `migrations/versions/0012_orchestration_runs.py` (new)
- `src/universal_auto_applier/persistence/models.py` (OrchestrationRunRow)
- `src/universal_auto_applier/persistence/orchestration_run_repository.py` (new)
- `src/universal_auto_applier/config.py` (orchestration settings)
- `src/universal_auto_applier/services/jobhunter_runner.py` (new)
- `src/universal_auto_applier/services/orchestration_service.py` (new)
- `src/universal_auto_applier/services/pipeline_worker_service.py` (drain thread cleanup)
- `src/universal_auto_applier/api/routes/orchestration.py` (new)
- `src/universal_auto_applier/api/app.py` (lifespan wiring)
- `src/universal_auto_applier/ui/static/index.html` (orchestration card)
- `src/universal_auto_applier/ui/static/app.js` (orchestration JS)
- `tests/contract/test_migrations.py` (CURRENT_HEAD)
- `tests/fixtures/fake_jobhunter/run_export_queue.py` (new)
- `tests/integration/test_orchestration.py` (new, 16 tests)
- `tests/playwright/test_orchestration_dashboard.py` (new, 3 tests)

## Tests and results (2026-08-10, working tree)

- `ruff check src tests migrations` -> All checks passed.
- `ruff format --check src tests migrations` -> 183 files already formatted.
- `pyright` -> 0 errors, 0 warnings, 0 informations.
- `pyright --pythonplatform Linux` -> 0 errors, 0 warnings, 0 informations.
- `pytest tests/unit` -> 876 passed.
- `pytest tests/contract` -> 79 passed.
- `pytest tests/integration` -> 113 passed (97 existing + 16 new WQ-6).
- `pytest tests/playwright -q -m "not live"` -> 189 passed (186 existing + 3 new WQ-6).
- `git diff --check` -> clean.

## Design decisions

- **Process boundary, not module import:** UAA never imports JobHunter Python
  modules. JobHunter runs as an external subprocess via `python run_export_queue.py
  --output <path>`. Success is determined by exit code + atomic queue file,
  never by parsing human logs.
- **Argument list, not shell string:** `subprocess.Popen` is called with
  `shell=False` and an argument list. No tokens, API keys, CV data, or
  candidate data are ever placed in command-line arguments.
- **Bounded capture + secret filter:** stdout/stderr are captured with a
  configurable max bytes (default 8192). A conservative secret filter redacts
  any line containing `api_key`, `token`, `password`, `secret`, `openrouter`,
  `google_ai`, `telegram`, etc.
- **Poll loop for cancel responsiveness:** `wait()` uses a poll loop (0.1s
  interval) instead of `communicate()` so `cancel()` can terminate the process
  from another thread. After the process exits, `communicate()` is called once
  to drain remaining output.
- **Durable state + startup recovery:** Orchestration state persists in
  `orchestration_runs` table. On restart, orphaned active runs are marked
  `failed` with a durable reason. Nothing is auto-retried. WQ-5 recovery
  handles stale UAA pipeline runs.
- **Only one active run:** In-process lock + DB status check. Duplicate start
  returns HTTP 409.
- **Sequential ordering is exact:** validate config → start JobHunter → wait
  for exit → verify queue file stable → import → start pipeline → wait for
  pipeline → mark completed. If JobHunter fails, no import and no pipeline.
- **Parallel mode:** UAA pipeline starts for existing jobs while JobHunter
  runs concurrently in a sub-thread. After JobHunter succeeds, the queue is
  imported once. JobHunter failure does not erase UAA work.
- **Cancel safety:** Requests UAA pipeline cancellation safely, terminates
  only the owned JobHunter child (graceful first, forced after grace), never
  kills based on a stale PID, persists the final outcome.

## Blockers / risks

- None. All local gates green. CI pending push.

## Exact next action

1. Push the branch and open PR #9 against main.
2. Wait for all 5 CI checks (Linux 3.11/3.12/3.13/3.14 + Windows 3.14).
3. Do not merge until all 5 are green.
