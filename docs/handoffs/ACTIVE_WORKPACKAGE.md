# Active Workpackage

- **WP ID:** WQ-6 — cross-repository orchestration controls (JobHunter export → UAA import → UAA pipeline).
- **Status:** IN PROGRESS — defects fixed, all local gates green, PR updating.
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
  imports the queue once and starts a second pipeline pass for newly
  imported jobs.

This workpackage is dry-run/review-only. It never performs final submission.

## Defects fixed (round 2)

1. **Full JobHunter workflow:** Production default changed from
   `run_export_queue.py` to `run_all.py` (scan → evaluate/tailor → atomic
   export). `run_all.py` does NOT accept `--output`; UAA reads the queue
   from `<jobhunter_repo>/data/application_queue.jsonl` (JobHunter's
   default output path). Added fake `run_all.py` with phase evidence.
2. **Complete parallel scheduling:** Parallel mode now starts a second
   pipeline pass for newly imported eligible jobs after the initial pass
   completes. Migration 0012 updated with `pipeline_run_id_initial` /
   `pipeline_state_initial` columns. No second pass when zero new eligible.
3. **Timeout cleanup:** `JobHunterRunner.wait()` on timeout now marks
   timed_out, terminates, force-kills after grace, reaps, joins drains,
   and the orchestration becomes failed (no import, no pipeline).
4. **stdout/stderr deadlock:** Drain threads now read CONCURRENTLY and
   continue discarding after the buffer is full (prevents OS pipe deadlock
   with high-volume output). 256KB output test passes with no deadlock.
5. **Strengthened idempotency:** Strict assertions for no duplicate rows,
   no second pipeline pass, no duplicate submission results.
6. **Restart/liveness safety:** Added PID liveness check to startup
   recovery (reuses WQ-5 `pid_is_alive`). Live PID → kept active (409 on
   duplicate start). Dead PID → marked failed. Cancel never kills using
   stale PID.

## Changed files

- `migrations/versions/0012_orchestration_runs.py` (added initial pipeline columns)
- `src/universal_auto_applier/persistence/models.py` (OrchestrationRunRow: initial pipeline fields)
- `src/universal_auto_applier/persistence/orchestration_run_repository.py` (to_dict: initial pipeline)
- `src/universal_auto_applier/config.py` (default entry_point=run_all.py, timeout_seconds)
- `src/universal_auto_applier/services/jobhunter_runner.py` (concurrent drain, timeout cleanup)
- `src/universal_auto_applier/services/orchestration_service.py` (second pass, PID liveness)
- `src/universal_auto_applier/services/pipeline_recovery_service.py` (public pid_is_alive)
- `src/universal_auto_applier/api/routes/orchestration.py` (idle state: initial pipeline)
- `tests/fixtures/fake_jobhunter/run_all.py` (new: full-workflow fake producer)
- `tests/fixtures/fake_jobhunter/run_export_queue.py` (added --volume, --timeout-test)
- `tests/integration/test_orchestration.py` (25 tests: all defects covered)
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`

## Tests and results (2026-08-10, working tree)

- `ruff check src tests migrations` -> All checks passed.
- `ruff format --check src tests migrations` -> 184 files already formatted.
- `pyright` -> 0 errors, 0 warnings, 0 informations.
- `pyright --pythonplatform Linux` -> 0 errors, 0 warnings, 0 informations.
- `pytest tests/unit` -> 876 passed.
- `pytest tests/contract` -> 79 passed.
- `pytest tests/integration` -> 122 passed (97 existing + 25 new WQ-6).
- `pytest tests/playwright -q -m "not live"` -> 189 passed.
- `git diff --check` -> clean.

## Exact next action

1. Push the new commits to the existing PR #9 branch.
2. Wait for all 5 CI checks (Linux 3.11/3.12/3.13/3.14 + Windows 3.14).
3. Do not merge until all 5 are green.
