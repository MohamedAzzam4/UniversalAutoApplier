# Active Workpackage — V2-02 Preparation HTTP Interlock

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 safety-first slice — make the preparation runner's submit block mandatory; install a default-deny HTTP method guard before pages or target navigation; preserve the separately authorized controlled-submission service.
- **Status:** **V2-02 CHECKPOINT PUSHED; V2-01 BROWSER GATE MERGED LOCALLY; COMBINED VALIDATION GREEN; SUPERVISOR REVIEW PENDING.**
- **Branch:** `checkpoint/v2-02-safety`.
- **Base SHA:** `8ed966d7e9a1c775a268ea2b1f9262d5dda57cd2` (V2-01 checkpoint).
- **Last completed/checkpoint SHA:** `6ba4a551f1d22f0f4a656394529d90c095e63a55` (pushed V2-02 safety checkpoint; resolve the current merge head dynamically after review).
- **Last successful V2-02 checkpoint time:** `2026-09-25T03:06:03+02:00`.
- **Branch-head verification (required after review and checkpoint push):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-02-safety
  ```

  The V2-02 checkpoint is already pushed. The V2-01 browser-gate merge passed combined validation in this worktree; keep it uncommitted until the supervisor reviews the staged diff. Never embed this file's own commit SHA as current HEAD.

## Completed work

- `LiveBrowserConfig.hard_submit_block` defaults to `true` and rejects `false`; the ordinary live CLI and pipeline-worker paths explicitly set it to `true`.
- `LiveBrowserRunner.run` and its synthetic-mutation path install the submit interlock and request guard on the context before creating a page. They check both guards on `about:blank` before navigating to the target URL. Submit-interlock or request-guard setup errors fail closed. `attempt_submit` always blocks; the controlled submission service is unchanged.
- Within those `LiveBrowserRunner` paths, the request guard allows only HTTP `GET`, `HEAD` and `OPTIONS`; it aborts every other method. There are no URL/method exceptions. Context routing covers normal pages and popups, and the local fixture verifies a `_blank` application page.
- Internally created persistent and ephemeral contexts set `service_workers="block"`. Caller-owned contexts refuse setup when pages or active workers are visible, block new service-worker registration, attach CDP service-worker bypass to each page before navigation, and stop if bypass/state cannot be verified.
- `LiveBrowserRunner` reports and its CLI paths identify request-guard installation and scope, Playwright context HTTP-route coverage, sanitized blocked-request evidence, and limitations. Evidence stores only method, resource type, origin and reason; it excludes paths, queries, headers and bodies.
- CLI `browser-session --attachable` and `live-dry-run --browser-session-file` / `--cdp-endpoint` now return a clear error before launching or connecting. The old flow selects an already-open page, which cannot satisfy the fresh-context interlock precondition. Attached-session resume is intentionally unavailable until V2-04 provides verified session/tab ownership and safe resume.
- If a routed request's `route.abort()` or safe `route.continue_()` fails, the report marks `request_outcome_unknown=true`, `needs_user_input`, and `http_request_outcome_unknown_reconciliation_required`. It tells the operator to reconcile state before retry. For the pipeline worker's `LiveBrowserRunner` path, the worker stores `NEEDS_USER_INPUT` with an intervention; queued/ready automatic selection excludes the job.
- The loopback fixture verifies zero server-side POSTs for native onchange `form.submit()`, fetch POST and `sendBeacon`; it also verifies safe GET navigation, field fill, popup handling, and secret-sentinel redaction from reports/logs. A separate local synthetic test exercises the same interlock outcome at 1440×900 and 390×844 because no Playwright MCP is exposed.
- CLI regressions prove both attachable-session launch and browser-session/CDP execution are rejected before Playwright launches/connects or a browser profile is created; safe resume of the same authenticated tab is a V2-04 follow-up.
- The WQ-7 production-safety fixture's delayed `setTimeout(form.submit())` vector is now opt-in and enabled only by its dedicated test. This narrow test-only fix was ported from the main-workspace browser-gate package; the delayed-submit assertion remains intact, and unrelated safe-Continue tests no longer race against the timer.
- WQ-7C and most WQ-8 Phase A fixture tests reuse pytest-playwright's context fixture. One public `LiveBrowserRunner.run` proof runs in a bounded worker thread, avoiding a second sync manager on pytest's event-loop thread while preserving internally owned-context coverage.

- The reviewed V2-01 browser-gate checkpoint `15beda81fd12542808e49a62bb523932b683a0b4` is merged into this uncommitted validation tree. It adds multipart upload evidence and duplicate-submit assertions, answer-memory/retry checks, the case-insensitive dashboard header assertion, and pytest-playwright fixture-lifecycle coverage. The WQ-8 interlock test preserves V2-02's required submit/request guards even when `wq8_phase_a=False`, and exercises `form.submit()` and `requestSubmit()` against the runner-created interlocked page.

## Explicit unresolved P1 — observation/fill path outside this guard

This package protects only the `LiveBrowserRunner.run` and synthetic-mutation
paths. The API `POST /api/submit/{application_id}/observe` and supervisor
`prepare_application`/`retry_application` paths use
`SubmissionExecutionService.observe_and_persist_snapshot()` / `_observe_and_fill`,
which still installs only the submit interlock before `execute_live_form`.
That review-only path can therefore still issue POST-based autosave or
intermediate requests. The normal pipeline worker's live path uses
`LiveBrowserRunner` and is covered. Do not claim all UAA preparation is
protected. Address this P1 in the next V2-02 shared-executor slice; do not
silently merge the guard into controlled submission execution without a
separate design and review.

## Current package files

- `src/universal_auto_applier/browser/live_models.py`
- `src/universal_auto_applier/browser/live_runner.py`
- `src/universal_auto_applier/browser/request_interlock.py`
- `src/universal_auto_applier/browser/submit_interlock.py`
- `src/universal_auto_applier/cli.py`
- `src/universal_auto_applier/services/pipeline_worker_runner.py`
- `tests/playwright/test_preparation_request_interlock.py`
- `tests/playwright/test_wq7_production_safety.py`
- `tests/playwright/test_wq7c_synthetic_mutation.py`
- `tests/playwright/test_wq8_phase_a_interlock.py`
- `tests/fixtures/live_browser/final_pipeline_apply.html`
- `tests/harness/final_pipeline_server.py`
- `tests/playwright/test_final_pipeline.py`
- `tests/playwright/test_llm_acceptance.py`
- `tests/playwright/test_phase6_dashboard.py`
- `tests/unit/test_wq8_form_heuristic.py`
- `tests/unit/test_preparation_request_interlock.py`
- `tests/unit/test_cli_llm_wiring.py`
- `tests/unit/test_wq7_live_dry_run_platforms.py`
- `docs/NEXT_WORKPACKAGES.md`, `docs/v2/UAA_V2_REVIEW_AND_PLAN.md`, and this handoff
- `docs/generalization/LIVE_BROWSER_DRY_RUN.md`

## Validation results

- Focused request-interlock and CLI attachment unit selection: **9 passed in 0.86 seconds**.
- Browser order `test_preparation_request_interlock.py` → `test_wq8_phase_a_interlock.py` → `test_wq7c_synthetic_mutation.py`: **18 passed in 66.54 seconds**. The exact WQ-7C → WQ-8 pair passed **14 tests in 54.67 seconds**. The broader lifecycle regression, with a pytest-playwright page fixture first, then safety fixture → WQ-7C → WQ-8, passed **41 tests in 120.31 seconds**. This verifies both ordering and the shared Playwright-manager lifecycle.
- Broader final browser selection — `test_live_browser_executor.py`, `test_preparation_request_interlock.py`, `test_wq7b_recon_mode.py`, `test_wq7_submit_safety_guard.py`, `test_wq7_production_safety.py`, `test_default_cannot_submit.py`, and `test_wq8_interlock.py`: **106 passed in 118.07 seconds**. The safety fixture exercises both 1440×900 and 390×844 viewports.
- Full `pytest -m "not live and not playwright" -q`: **1,511 passed, 313 deselected in 795.80 seconds**.
- `ruff check src tests migrations` passed. `ruff format --check src tests migrations` passed (**242 files already formatted**).
- `pyright`: **0 errors, 0 warnings, 0 informations**. It noted no `.venv` under the isolated worktree path configured in `pyproject.toml`; it used the main workspace's installed venv executable. The diagnostic did not affect type-check results.
- `git diff --cached --check` and `git diff --check` both pass for the staged review package. No Playwright-specific MCP is exposed in the enabled tool catalog; local synthetic browser tests verify the request guard at 1440×900 and 390×844. No dashboard UI changed. No live tests, ATS targets, or real submissions have been run.
- Before reconciliation, the V2-01 source branch passed the full browser-inclusive non-live gate: **1,808 passed, 3 deselected in 1,616.60 seconds**. The combined V2-01 + V2-02 browser-inclusive non-live gate passed: **1,821 passed, 3 deselected in 1,693.04 seconds (28:13)**. The conflict-area WQ-7C/WQ-8/final-pipeline selection passed **42 tests in 107.96 seconds**.
- On the combined tree, `ruff check src tests migrations` passed; `ruff format --check src tests migrations` passed (**242 files already formatted**); Pyright reported **0 errors, 0 warnings, 0 informations** (with only the existing notice that the isolated worktree has no local `.venv`). `git diff --cached --check` and `git diff --check` pass. No live tests, ATS targets, or real submissions were run.

## Decisions and limits

- This is a preparation HTTP request guard, not universal network-submission prevention. It blocks non-read-only HTTP methods observed by Playwright context routing; `GET` endpoints with server-side effects and WebSocket frames are outside coverage. Reports state these limits.
- Playwright exposes active service workers, not every dormant registration in a caller-owned context. Such contexts may have background-worker activity that this guard cannot enumerate. Internally created UAA contexts block service workers. Caller-owned contexts fail closed when an existing page or active worker is visible and must pass the registration-guard and per-page bypass checks; the report does not claim those checks prove dormant registrations absent.
- The blanket method policy may pause legitimate uploads, autosave and intermediate steps that use POST/PUT/PATCH/DELETE. That is intentional for this slice. No exception is enabled; a future exception requires a qualified flow plus dedicated evidence, tests and review.
- Existing `--browser-session-file` / `--cdp-endpoint` preparation is temporarily unavailable. The old CLI selected an existing browser page before entering a caller-owned context, and the safety guard correctly refuses that context. The CLI now fails early with a next action; implement verified same-tab resume and ownership in V2-04 before restoring this capability.
- Existing WQ-8 authorization/hash/claim gates and `submission/execution_service.py` were not changed. Preparation remains separate from owner-approved controlled submission.
- Report/UI follow-up: `submitted=false` means UAA did not confirm a submission; when `request_outcome_unknown=true`, remote non-submission is not established. Any dashboard or report consumer must show the unknown/reconciliation state separately, not present `submitted=false` as proof of no remote application.
- Separate deferred observer finding: the static `observe_html` path in `navigator/page_observer.py` collects inline script/style source in `_DomExtractor._all_text_parts`; `_detect_page_state` can then treat harmless `new Error(...)` source as an error-page signal, which static orchestration / `safe_explorer` may surface as error/unknown-page. This is distinct from live `analyze_page`. The later fix should exclude non-rendered source text while preserving visible error, login and CAPTCHA signals; see `docs/NEXT_WORKPACKAGES.md`.

## Blockers / risks

- The V2-02 checkpoint is pushed. The V2-01 browser-gate merge is uncommitted and all targeted, browser-inclusive, static and diff checks are green. Supervisor review of the staged combined diff remains pending; do not commit before review.
- The known transport coverage limits above remain. An abort failure is escalated as unknown remote outcome and blocks automatic retry; no live target behavior was tested.

## Exact next action

Review the staged nine-path combined diff and the recorded green results. If approved, create one merge commit on `checkpoint/v2-02-safety`, push it, fetch origin, and verify local `HEAD` equals `origin/checkpoint/v2-02-safety`. Do not merge into `main` or start shared-executor implementation yet; no real ATS action or submission is permitted.

- **Last updated:** 2026-09-25T02:37:22Z.
