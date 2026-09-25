# Active Workpackage — V2-02 Preparation HTTP Interlock

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02, one executor. This bounded slice protects the shared API/supervisor snapshot-observation and form-fill path from mutating HTTP requests, persists a durable intervention before revoking prior approval, and requires owner reconciliation when request delivery is uncertain.
- **Status:** **Implementation and all non-live gates for this slice are complete. Exact source, tests and docs are staged for supervisor review. No commit or push has been made for this slice.**
- **Base SHA:** `a8c42880449be75a20a7c855e5031468c543bcf0` (the local request-guard branch starting point).
- **Current branch:** `checkpoint/v2-02-request-guard`.
- **Last completed/checkpoint SHA:** `88a279cfceaea7b7a7ac1c9cedadcb0257b5a926` is the verified pushed classifier integration on `checkpoint/v2-02-safety`; it is a shared-checkpoint reference, not this branch's `HEAD`. Resolve the latest pushed checkpoint dynamically with `git rev-parse origin/checkpoint/v2-02-safety`.
- **Branch-head verification (required before checkpoint publication):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-02-request-guard
  git rev-parse origin/checkpoint/v2-02-safety
  ```

  The request-guard branch has no published remote checkpoint and its staged changes remain local. Do not treat a missing `origin/checkpoint/v2-02-request-guard` or the `HEAD`/origin difference as a successful checkpoint verification. The safety branch may advance independently. Reconcile the staged slice with the latest safety checkpoint only after supervisor review, then publish and verify its exact remote ref. Do not commit or push without supervisor approval.

## Completed work in this staged slice

- `SubmissionExecutionService.observe_and_persist_snapshot()` installs the HTTP request guard and submit interlock before creating its page, and rejects contexts with any pre-existing pages or active service workers.
- Non-read HTTP requests block observation and prevent a review snapshot from being persisted. Request evidence excludes paths, queries, headers and bodies.
- The pending intervention commits before approval revocation in a separate transaction. A revocation failure leaves the durable blocker in place; a blocker-write failure raises a typed persistence error, returns HTTP 503 and sets a per-process fail-closed latch.
- Abort/continue uncertainty has distinct reconciliation state and takes precedence over an earlier blocked-mutation result after route callbacks settle. The API prevents approval/retry, supervisor preparation yields a terminal human handoff without automatic retry, and direct coordinator/CLI callers cannot obtain a submission claim while the blocker is pending.
- A pending blocker prevents controlled WQ-8 submission from reaching browser/claim; the explicit manually approved final-submit route remains separate and usable when its existing gates pass.
- The irreducible no-durable-write case is documented: if the first blocker transaction cannot commit, the in-process latch cannot survive process loss. Restore storage and reconcile with the target owner before process restart or retry.

## Changed files

- `src/universal_auto_applier/api/routes/submit.py`
- `src/universal_auto_applier/browser/request_interlock.py`
- `src/universal_auto_applier/core/statuses.py`
- `src/universal_auto_applier/submission/coordinator.py`
- `src/universal_auto_applier/submission/execution_service.py`
- `src/universal_auto_applier/supervisor/models.py`
- `src/universal_auto_applier/supervisor/service.py`
- `src/universal_auto_applier/supervisor/tools.py`
- `tests/integration/test_wq8_snapshot_persistence.py`
- `tests/unit/test_intervention_store.py`
- `tests/unit/test_supervisor_v0.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`

## Tests and exact results

- Focused snapshot-persistence, supervisor, intervention-store and request-interlock selection: **86 passed in 99.36 seconds**.
- Affected WQ-8/default-no-submit browser selection: **17 passed in 28.24 seconds**.
- Full `pytest -m "not live and not playwright" -x -vv --tb=short -p no:cacheprovider`: **1,521 passed, 313 deselected (1,834 collected) in 896.69 seconds**.
- Full browser-inclusive `pytest -m "not live" -x -vv --tb=short -p no:cacheprovider`: **1,831 passed, 3 deselected (1,834 collected) in 1,808.33 seconds**.
- `ruff check src tests migrations`: passed.
- `ruff format --check src tests migrations`: passed, **242 files already formatted**.
- `pyright`: **0 errors, 0 warnings, 0 informations**. The configured isolated-worktree `.venv` is absent; the main workspace's installed executable was used.
- `git diff --check`: passed.
- No live test, real ATS target, or real submission was run.

## Decisions and limits

- Request evidence remains sanitized to method, resource type, origin and reason.
- The blocker commits before revocation so a partial revocation failure remains durably gated.
- A route-callback settle boundary promotes abort/continuation failure over ordinary blocked status. If route handling cannot settle before its bound, the result is treated as uncertain.
- A process latch is secondary protection only. When the initial durable write fails, the latch cannot survive process loss; operators must repair storage and reconcile the target before restarting or retrying.
- The HTTP guard does not cover WebSocket frames, server-side effects on GET endpoints or dormant service-worker activity that Playwright cannot enumerate. Caller-owned contexts still fail closed on visible pre-existing pages/workers.
- This slice protects observation/fill and gates controlled submit on blockers; it does not change the WQ-8 authorization contract or authorize any real submission.

## Blockers / risks

- The latest classifier integration is on pushed `checkpoint/v2-02-safety`, while this isolated request-guard branch starts at `a8c4288`. The branches are intentionally not reconciled yet; do so only after supervisor review.
- Current changes are staged for review only. They have not been committed or pushed.

## Exact next action

Review the staged source, tests, backlog ledger and this handoff. If approved, reconcile with the latest `origin/checkpoint/v2-02-safety`, create the requested checkpoint, push it, fetch origin, and verify the local and remote checkpoint SHAs match. Do not commit or push before that approval. No live ATS action is permitted.

- **Last updated:** 2026-09-25T19:48:36Z.
