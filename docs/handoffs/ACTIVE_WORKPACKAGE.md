# Active Workpackage

- **WP ID:** WQ-6 — cross-repository orchestration controls (JobHunter export → UAA import → UAA pipeline).
- **Status:** IN PROGRESS — round 7 defects fixed, all local gates green, PR updating.
- **Branch:** `checkpoint/wq-6-cross-repo-orchestration`
- **Base SHA:** `c7b6b2c672a7e96be643c01b2add761e78865967` (`origin/main`, WQ-5 merge commit)
- **PR:** https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/9 (open, not draft, not merged)
- **Last updated:** 2026-08-11

## Objective

Make UAA the local control plane that can run JobHunter and UAA together
from the dashboard. The user can choose:

- **Sequential mode:** JobHunter completes, then UAA imports the queue,
  then UAA starts its safe browser pipeline.
- **Parallel mode:** UAA processes existing queued jobs while JobHunter
  searches/evaluates new jobs concurrently. When JobHunter finishes, UAA
  imports the queue once and starts a second pipeline pass for newly
  imported jobs.

This workpackage is dry-run/review-only. It never performs final submission.

## Defects fixed (round 7)

1. **Subprocess cleanup (Popen.__del__ ResourceWarning):** Added explicit
   `_close_pipes()` and public `close()` methods to `JobHunterRunner`.
   Every terminal path (`wait()`, `cancel()`, `_handle_timeout()`,
   `_ensure_reaped()`/`close()`) now joins drain threads AND closes
   `proc.stdout` / `proc.stderr` so `Popen.__del__` never observes open
   pipes. `PipelineWorkerService.start()` now joins the previous drain
   threads and closes the previous Popen's pipes before replacing
   `self._proc`. `PipelineWorkerService.shutdown()` is idempotent and
   closes pipes. `OrchestrationService._run()` wraps the run in a
   `finally` block that always calls `runner.close()`. The
   `OrchestrationService.start()` method calls `runner.close()` on the
   previous runner before discarding the reference. No `gc.collect()` or
   sleeps are used as substitutes.

2. **Durable orchestration evidence:** Migration `0013` adds 8 new columns
   to `orchestration_runs`: `targeted_ids_json`, `processed_ids_json`,
   `remaining_ids_json`, `targeted_count`, `processed_count`,
   `remaining_count`, `pipeline_run_ids_json`, `pass_count`. The
   orchestration service persists these after every batch via the new
   `_persist_batch_evidence()` helper. The API status response exposes
   them as `targeted_ids`, `processed_ids`, `remaining_ids`,
   `targeted_count`, `processed_count`, `remaining_count`,
   `pipeline_run_ids`, and `pass_count`. Evidence remains truthful after
   restart or failure (every early-exit path persists current state).

3. **Multi-batch behavior:** The continuation loop processes target IDs in
   batches of `max_jobs` until all leave `READY_TO_APPLY`/`QUEUED`.
   `max_passes` is `ceil(N / max_jobs) + 1` safety margin. No-progress
   detection checks if ANY batch ID is still eligible after a pass (not
   total remaining vs batch size). Durable evidence is persisted after
   every batch, on every early-exit path.

4. **Boundary and cleanup coverage:** 51 new tests in
   `tests/integration/test_orchestration_round7.py` covering:
   - Subprocess cleanup regression (no ResourceWarning on normal/cancel/fail/timeout)
   - Durable evidence (zero and one-job cases)
   - Multi-batch behavior (5 jobs, max_jobs=2, exactly 3 passes)
   - No-progress detection (first batch, later batch, terminates without looping)
   - Manifest validation (malformed, missing, empty, non-list, duplicate, blank, non-string)
   - Manifest cleanup (after success, failure, cancellation, stale startup)
   - Sequential and parallel modes
   - max_jobs configuration from API (validation: ge=1, le=100)
   - Worker count bounds (defaults to 1, rejects >1, API reports counts)
   - Terminal immutability (completed/failed/cancelled stay terminal across polls)
   - JobHunter config validation (malformed YAML, non-mapping root/queue_export, blank/non-string output_path, valid relative/absolute, missing config/key/file)
   - Queue path mismatch (override must match JH config)

## Changed files

### Production code
- `src/universal_auto_applier/services/jobhunter_runner.py` — Added `_close_pipes()`, `close()`, made `_ensure_reaped()` an alias for `close()`. All terminal paths now close pipes.
- `src/universal_auto_applier/services/pipeline_worker_service.py` — `start()` joins previous drain threads and closes previous Popen's pipes. `shutdown()` is idempotent and closes pipes.
- `src/universal_auto_applier/services/orchestration_service.py` — `_run()` wraps in `finally` that calls `runner.close()`. `start()` and `shutdown()` use `close()`. Multi-batch loop persists durable evidence after every batch via `_persist_batch_evidence()`.
- `src/universal_auto_applier/persistence/models.py` — `OrchestrationRunRow`: 8 new columns.
- `src/universal_auto_applier/persistence/orchestration_run_repository.py` — `create_orchestration_run()` and `orchestration_run_to_dict()` include the 8 new fields.
- `src/universal_auto_applier/api/routes/orchestration.py` — Status response includes the 8 new fields (both service-initialized and not-initialized paths).

### Migrations
- `migrations/versions/0013_orchestration_durable_evidence.py` — Adds `targeted_ids_json`, `processed_ids_json`, `remaining_ids_json`, `targeted_count`, `processed_count`, `remaining_count`, `pipeline_run_ids_json`, `pass_count` to `orchestration_runs`.

### Tests
- `tests/integration/test_orchestration.py` — Fixture teardown improvements (call `shutdown()`, remove `gc.collect()`).
- `tests/integration/test_orchestration_audit.py` — Same fixture teardown improvements.
- `tests/integration/test_orchestration_final.py` — Same fixture teardown improvements.
- `tests/integration/test_orchestration_round7.py` — NEW: 51 tests covering all round 7 requirements.

### Documentation
- `.env.example` — Added WQ-6 orchestration env vars section.
- `docs/CURRENT_STATE.md` — Added WQ-6 row to the implementation table.
- `docs/handoffs/ACTIVE_WORKPACKAGE.md` — This file.

## Exact persisted orchestration fields

The `orchestration_runs` table (migration 0012 + 0013) persists:

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | String(64) PK | UUID of the run |
| `mode` | String(16) | `sequential` or `parallel` |
| `status` | String(32) | `idle`/`running`/`jobhunter_running`/`importing`/`pipeline_running`/`cancelling`/`completed`/`failed`/`cancelled` |
| `current_phase` | String(64) | Current phase name |
| `last_action` | Text | Human-readable last action |
| `last_error` | Text | Error message (empty if no error) |
| `cancel_reason` | Text | Cancellation reason (empty if not cancelled) |
| `jobhunter_pid` | Integer? | JobHunter child PID |
| `jobhunter_started_at` | DateTime? | When the child was launched |
| `jobhunter_finished_at` | DateTime? | When the child exited |
| `jobhunter_exit_code` | Integer? | Child exit code |
| `jobhunter_stdout` | Text | Bounded stdout (secrets filtered) |
| `jobhunter_stderr` | Text | Bounded stderr (secrets filtered) |
| `queue_import_run_id` | String(64)? | Linked queue-import run ID |
| `queue_import_state` | String(16)? | Queue import state |
| `queue_imported` | Integer? | Number of imported jobs |
| `queue_skipped` | Integer? | Number of skipped jobs |
| `pipeline_run_id_initial` | String(64)? | Initial pipeline run ID (parallel mode) |
| `pipeline_state_initial` | String(32)? | Initial pipeline terminal state |
| `pipeline_run_id` | String(64)? | Last continuation pipeline run ID |
| `pipeline_state` | String(32)? | Last continuation pipeline state |
| `queue_hash_before` | String(64)? | Queue content hash before JobHunter |
| `queue_hash_after` | String(64)? | Queue content hash after JobHunter |
| `queue_mtime_ns_before` | Integer? | Queue mtime_ns before JobHunter |
| `queue_mtime_ns_after` | Integer? | Queue mtime_ns after JobHunter |
| `queue_published` | Boolean? | Whether the queue was published during this run |
| `newly_eligible_count` | Integer? | Count of newly eligible IDs after import |
| `newly_eligible_ids_json` | JSON? | List of newly eligible application_id hashes |
| `targeted_ids_json` | JSON? | Original targeted IDs (set once at loop start) |
| `processed_ids_json` | JSON? | Processed target IDs (updated after every batch) |
| `remaining_ids_json` | JSON? | Remaining target IDs (updated after every batch) |
| `targeted_count` | Integer? | Count of targeted IDs |
| `processed_count` | Integer? | Count of processed IDs |
| `remaining_count` | Integer? | Count of remaining IDs |
| `pipeline_run_ids_json` | JSON? | Ordered list of all continuation pipeline run IDs |
| `pass_count` | Integer? | Number of completed pipeline passes |
| `errors_json` | JSON | Bounded structured errors |
| `started_at` | DateTime | Run start time |
| `finished_at` | DateTime? | Run finish time |

All `*_ids_json` fields contain only `application_id` SHA-256 hashes — never
candidate data. All lists are bounded by the number of newly imported jobs.

## Subprocess cleanup implementation

Every `Popen` handle is waited on and its pipes are closed in all paths:

1. **`JobHunterRunner`:**
   - `wait()`: on normal exit, joins drain threads + calls `_close_pipes()`.
   - `cancel()`: terminates → waits → joins drains + closes pipes.
   - `_handle_timeout()`: terminates → force-kills → waits → joins drains + closes pipes.
   - `close()` (public, idempotent): if still running, terminates → waits → joins drains + closes pipes.
   - `_ensure_reaped()` is now an alias for `close()` (backward compat).

2. **`PipelineWorkerService`:**
   - `start()`: before replacing `self._proc`, waits for the previous proc to exit, joins the previous drain threads, and closes the previous proc's pipes.
   - `shutdown()` (idempotent): terminates → waits → joins drains + closes pipes. Sets `self._proc = None`.

3. **`OrchestrationService`:**
   - `_run()`: wraps the entire run in a `try/finally` that calls `runner.close()` — the single source of truth for runner cleanup.
   - `start()`: before discarding `self._runner`, calls `runner.close()` on the previous runner.
   - `shutdown()`: calls `runner.close()` (not `_ensure_reaped()`) and clears the reference.

No `gc.collect()` or sleeps are used as substitutes for cleanup. The
`filterwarnings = ["error", "error::ResourceWarning"]` pytest config
ensures any `ResourceWarning` is a test failure.

## Tests and results (2026-08-11, working tree)

- `ruff check src tests migrations` -> All checks passed.
- `ruff format --check src tests migrations` -> All files already formatted.
- `pyright` -> 0 errors, 0 warnings, 0 informations.
- `pytest tests/integration/test_orchestration*.py` -> 94 passed (43 existing + 51 new).
- Subprocess cleanup and multi-batch tests run 8+ consecutive times: all pass.

## Limitations

- **Single-worker only.** `jobhunter_workers=1`, `pipeline_workers=1` (validated `le=1`). `max_jobs` is a batch-size limit, not a worker count. Real worker pools are future work.
- **No auto-resubmission.** Orchestration is dry-run/review-only. Final submission requires the separate `live-submit` CLI/API path with explicit approval gates.
- **No cross-repo Python imports.** JobHunter runs as an external subprocess; UAA never imports JobHunter modules. The boundary is process-level.
- **Stale manifest cleanup is NOT automatic at startup.** Each run creates its own manifest with a unique run-id prefix. Stale manifests from crashed runs remain on disk (they are not consumed by new runs). This is acceptable: manifests are small JSON files in `data_dir/target_ids/`.
- **PID liveness is conservative.** On startup recovery, a live PID keeps the run active (blocks duplicate start) even if UAA doesn't own the process. The operator can cancel manually. This is safer than killing a PID we don't own.
- **Multi-batch max_passes is `ceil(N/max_jobs)+1`.** The +1 safety margin allows the no-progress detection to fire on the last batch without hitting the max-passes limit first. If a batch makes no progress, the run fails immediately with a clear durable error.

## Exact next action

1. Push the new commits to the existing PR #9 branch.
2. Wait for all 5 CI checks (Linux 3.11/3.12/3.13/3.14 + Windows 3.14).
3. Do not merge until all 5 are green.

```text
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-6-cross-repo-orchestration
```

The two resolved values must match before handoff/review.
