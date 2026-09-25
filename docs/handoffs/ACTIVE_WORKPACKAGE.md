# Active Workpackage — V2-02 Safety, Classifier, and Request-Guard Integration

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02, one executor. Preserve the browser safety and
  visible-text classifier checkpoints, integrate the shared observation/fill
  HTTP guard, and continue toward one multi-step state machine across CLI,
  dashboard worker, supervisor preparation and snapshot observation.
- **Status:** **V2-02 remains in progress. Safety/classifier and request-guard
  source checkpoints are pushed separately. The combined safety tree is
  validated and staged locally for supervisor review; it has not been committed
  or pushed.**
- **Branch:** `checkpoint/v2-02-safety`.
- **Base SHA:** `8ed966d7e9a1c775a268ea2b1f9262d5dda57cd2` (V2-01 checkpoint).
- **Last completed/pushed checkpoint:** resolve `origin/checkpoint/v2-02-safety`
  dynamically. At merge start it was `88a279cfceaea7b7a7ac1c9cedadcb0257b5a926`;
  request-guard source checkpoint `021330b811c33888dcf15a37d35d49596f6eafb3` is
  pushed separately on `checkpoint/v2-02-request-guard`.
- **Branch-head verification (required before any checkpoint/handoff):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-02-safety
  git rev-parse origin/checkpoint/v2-02-request-guard
  ```

The merge is intentionally uncommitted. `HEAD` and the safety origin ref still
identify the last pushed safety checkpoint; the index and working tree contain
the local integration. Do not treat this as a published combined checkpoint.

## Completed work in this integration

- The safety/browser slice makes `hard_submit_block` mandatory and installs
  context-scoped HTTP and submit guards before page creation/navigation on
  covered `LiveBrowserRunner` paths. It covers popups, blocks non-read HTTP
  methods, and fails closed for visible pre-existing pages or service workers.
  Attached-session launch modes are rejected pending safe V2-04 ownership/replay
  behavior.
- Failed continuation or abort requires request-outcome reconciliation and is
  excluded from automatic retry in the covered pipeline path.
- The static page observer excludes script, style and inert template source from
  page-state text while retaining title, body text, clickable labels and
  `noscript`. Its CSS visibility limitation is documented.
- `SubmissionExecutionService.observe_and_persist_snapshot()` now installs the
  submit interlock and default-deny request guard before creating a page,
  rejects contexts with any pre-existing page or active service worker, and
  prevents an approvable snapshot after blocked non-read HTTP.
- Request evidence is sanitized. The pending intervention commits before
  approval revocation in a separate transaction. A pending blocker gates
  API/supervisor preparation and approval, coordinator/CLI preparation and
  controlled-submit claims. A revocation failure therefore leaves a durable
  blocker.
- Abort/continuation uncertainty has distinct reconciliation state and takes
  precedence over an earlier blocked-mutation result after callbacks settle.
  Supervisor handling is terminal and does not automatically retry.
- Failure to persist the first blocker raises a typed persistence error, returns
  HTTP 503 and latches the application closed for this process. The process
  latch is secondary: if no initial durable write succeeds, process loss erases
  that latch, so storage recovery and owner reconciliation are required before
  restart or retry.
- The manually approved WQ-8 final-submit path remains separate; an outstanding
  blocker prevents it from claiming or clicking, and existing explicit approval
  gates remain required.

## Changed paths

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

## Validation

- Focused request-guard, supervisor, intervention-store, page-observer and WQ-8
  persistence tests: **119 passed in 123.28 seconds**.
- Affected preparation-interlock/WQ-8/default-no-submit browser selection: **21
  passed in 46.64 seconds**.
- Full `pytest -m "not live and not playwright" -x -vv --tb=short -p
  no:cacheprovider`: **1,526 passed, 313 deselected (1,839 collected) in 935.79
  seconds**.
- Full browser-inclusive `pytest -m "not live" -x -vv --tb=short -p
  no:cacheprovider`: **1,836 passed, 3 deselected (1,839 collected) in 1,878.03
  seconds**.
- `ruff check src tests migrations`: passed. `ruff format --check src tests
  migrations`: passed (**242 files already formatted**).
- Pyright: **0 errors, 0 warnings, 0 informations**. The isolated worktree has
  no local `.venv`; the installed executable was used.
- Integrated staged/unstaged diff checks passed. No live tests, ATS targets or
  real submissions were run.

## Remaining V2-02 scope

V2-02 is not complete. The remaining objective is a unified multi-step state
machine across CLI, dashboard worker, supervisor preparation and
observation/fill, removal of duplicated readiness decisions, and structured
errors/progress fingerprints. Acceptance still includes equivalent fixture
outcomes across entry points, three same-URL steps, nested conditional
questions, and no review-ready state while a final boundary is incomplete.

## Decisions, limits, and risks

- Request evidence is limited to sanitized method, resource type, origin and
  reason.
- Routed HTTP guards do not cover WebSocket frames, GET endpoints with
  server-side effects, or dormant service workers that Playwright cannot
  enumerate; do not describe this as universal network-side-effect prevention.
- `submitted=false` does not prove remote non-submission when
  `request_outcome_unknown=true`.
- If the first blocker transaction cannot commit, there is no durable evidence
  that survives process loss. Restore persistence and reconcile with the target
  owner before restarting or retrying.
- No change authorizes a real ATS run or changes the WQ-8 controlled-submission
  approval contract.

## Exact next action

The combined safety tree is validated and staged for supervisor review. Review
the exact staged source, tests, backlog ledger and handoff. Do not commit or
push until supervisor approval; do not merge to main or run real ATS actions.

- **Last updated:** 2026-09-25T20:59:13Z.
