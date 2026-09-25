# Active Workpackage — V2-02 Visible-Text Classifier

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 classifier follow-up — keep inert script/style/template source out of static page-state classification while preserving visible error, login, CAPTCHA, title and clickable-label behavior.
- **Status:** **IMPLEMENTATION COMPLETE; FULL NON-LIVE GATE AND STATIC CHECKS GREEN; READY FOR SUPERVISOR REVIEW.**
- **Branch:** `checkpoint/v2-02-classifier`.
- **Base SHA:** `6ba4a551f1d22f0f4a656394529d90c095e63a55` (pushed V2-02 HTTP interlock checkpoint).
- **Last completed/checkpoint SHA:** `6ba4a551f1d22f0f4a656394529d90c095e63a55` (inherited base checkpoint; classifier changes are not committed and their SHA must be resolved dynamically after approval/push).
- **Last successful classifier checkpoint time:** none yet. Inherited safety checkpoint time: `2026-09-25T03:06:03+02:00`.
- **Branch-head verification (required after checkpoint push):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-02-classifier
  ```

  Push authentication was verified with `git push --dry-run origin checkpoint/v2-02-classifier`; that did not create the remote ref. Commit and push only after supervisor review, then verify the two resolved SHAs match. Never embed this file's own commit SHA as current HEAD.

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
- This classifier slice excludes `script`, `style`, and inert `template` subtree data from `_DomExtractor` page-state text. It preserves ordinary visible body/title text and clickable labels. `noscript` remains included because its rendering depends on script availability, which static HTML parsing cannot know.
- Inspection corrected the defect location: live `navigator.apply_path_finder.analyze_page()` already reads rendered `body.inner_text()` and is unchanged. The false positive was in static `navigator.page_observer.observe_html()` / `_DomExtractor`.
- Added synthetic regressions for `new Error(...)` and style/template source on an application form, a visible error page, harmless source text on a login page, a `noscript` fallback login signal, and CAPTCHA's existing precedence over visible error/login signals.

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

## Changed files in this classifier slice

- `src/universal_auto_applier/navigator/page_observer.py`
- `tests/unit/test_page_observer.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`

## Inherited V2-02 HTTP-interlock validation

- Focused request-interlock and CLI attachment unit selection: **9 passed in 0.86 seconds**.
- Browser order `test_preparation_request_interlock.py` → `test_wq8_phase_a_interlock.py` → `test_wq7c_synthetic_mutation.py`: **18 passed in 66.54 seconds**. The exact WQ-7C → WQ-8 pair passed **14 tests in 54.67 seconds**. The broader lifecycle regression, with a pytest-playwright page fixture first, then safety fixture → WQ-7C → WQ-8, passed **41 tests in 120.31 seconds**. This verifies both ordering and the shared Playwright-manager lifecycle.
- Broader final browser selection — `test_live_browser_executor.py`, `test_preparation_request_interlock.py`, `test_wq7b_recon_mode.py`, `test_wq7_submit_safety_guard.py`, `test_wq7_production_safety.py`, `test_default_cannot_submit.py`, and `test_wq8_interlock.py`: **106 passed in 118.07 seconds**. The safety fixture exercises both 1440×900 and 390×844 viewports.
- Full `pytest -m "not live and not playwright" -q`: **1,511 passed, 313 deselected in 795.80 seconds**.
- `ruff check src tests migrations` passed. `ruff format --check src tests migrations` passed (**242 files already formatted**).
- `pyright`: **0 errors, 0 warnings, 0 informations**. It noted no `.venv` under the isolated worktree path configured in `pyproject.toml`; it used the main workspace's installed venv executable. The diagnostic did not affect type-check results.
- `git diff --cached --check` and `git diff --check` both pass for the staged review package. No Playwright-specific MCP is exposed in the enabled tool catalog; local synthetic browser tests verify the request guard at 1440×900 and 390×844. No dashboard UI changed. No live tests, ATS targets, or real submissions have been run.

## Classifier-slice validation

- Focused observer tests: `python -m pytest tests/unit/test_page_observer.py -q` — **33 passed in 0.28s** (run with the repository venv from the isolated worktree).
- Full non-live/non-Playwright suite: `python -m pytest -m "not live and not playwright" -q` — **1,516 passed, 313 deselected in 825.01s**.
- Ruff check passed; Ruff format check passed (**242 files already formatted**); Pyright reported **0 errors, 0 warnings, 0 informations**. Pyright noted that the isolated worktree has no local `.venv` and used the configured main-workspace environment.
- Final diff checks: `git diff --check` passed; staging is pending supervisor review.
- No Playwright browser tests are directly affected: static `observe_html()` parsing changed; live Playwright `analyze_page()` is unchanged. No live ATS pages or submissions were used.

## Classifier decisions and limits

- The parser now omits data from `script`, `style`, and `template` subtrees. It leaves `noscript` content included because `noscript` may render when scripting is disabled and this static parser has no scripting-mode input.
- `observe_html()` parses markup without browser layout/computed-style information. CSS-hidden body text can still affect its state classification; this bounded change only removes inherently inert source/template text. Live `analyze_page()` remains on Playwright `body.inner_text()` and is not modified.
- Existing visible error, login, CAPTCHA detections and their priority remain unchanged. Unknown page layouts remain unknown and safely blocked by the existing exploration path.
- The inherited HTTP interlock only covers `LiveBrowserRunner` paths; API/supervisor observation/fill still has the separate unresolved P1 recorded below. This classifier change does not affect or claim to fix that guard coverage.
- This branch starts at `6ba4a551f1d22f0f4a656394529d90c095e63a55`. The later V2-01 browser-gate integration is separate on `checkpoint/v2-02-safety`; this classifier branch has not merged it.

## Blockers / risks

- No implementation blocker. The parent safety branch has a separate browser-gate integration; await supervisor direction before reconciling it into this classifier checkpoint.
- `noscript` and CSS-hidden content have the documented static-parser limitations above. No browser behavior or user-facing UI changed.

## Exact next action

Stage only the four classifier-slice paths, run `git diff --cached --check`, and send the staged diff/status plus exact gate results to the supervisor. Do not commit or push before review. After approval, commit and push `checkpoint/v2-02-classifier`, then fetch and compare the dynamic local and remote checkpoint SHAs:

```text
git commit -m "fix(v2-02): ignore inert markup in page classification"
git push -u origin checkpoint/v2-02-classifier
git fetch origin
git rev-parse HEAD
git rev-parse origin/checkpoint/v2-02-classifier
```

Do not merge into `main` or start the shared-executor implementation until the supervisor directs the next step.

- **Last updated:** 2026-09-25T02:52:58Z.
