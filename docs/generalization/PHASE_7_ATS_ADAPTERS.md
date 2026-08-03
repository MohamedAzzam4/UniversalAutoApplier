# Phase 7 — ATS Platform Adapters

This document describes the six platform-specific adapters introduced in
Phase 7 of the generalization roadmap. Each adapter plugs into the
existing `AdapterRegistry` and pipeline orchestration while preserving
all safety gates.

## Design summary

All five new ATS adapters (Greenhouse, Lever, Workday, SmartRecruiters,
LinkedIn Easy Apply) and the improved Generic fallback share a common
private base class, `_UntrustedATSAdapter`, that enforces these
invariants:

1. `is_trusted = False`. The pipeline orchestrator only allows
   `submit_or_pause` to actually submit when `is_trusted` is True AND
   the review gate has approved. Untrusted adapters therefore never
   submit, even if a caller passes `approved=True`.
2. `submit_or_pause` ALWAYS returns `review_ready` and records a
   `review_before_submit` intervention note. It never clicks a submit
   button.
3. `navigate_to_form` and `fill` use the shared Phase 3/4
   infrastructure (`observe_html`, `safe_explore`,
   `extract_form_fields`, `fill_form`) on the provided fixture HTML.
4. Every phase returns a structured `AdapterResult` even on failure.
   Layout changes / missing selectors fail safely with
   `AdapterResult.failed` and a `reason` metadata field.
5. Login, captcha, and review pages stop the adapter with
   `needs_user_input` and a structured `intervention_kind` metadata
   field. The adapter never bypasses login or captcha.

## Adapter registry order

The default registry registers adapters in this deterministic order:

1. `SiemensAdapter` (trusted, narrow hostname match `jobs.siemens.com`)
2. `GreenhouseAdapter` (`boards.greenhouse.io`, `greenhouse.io`)
3. `LeverAdapter` (`jobs.lever.co`)
4. `WorkdayAdapter` (`myworkdayjobs.com`)
5. `SmartRecruitersAdapter` (`smartrecruiters.com`)
6. `LinkedInEasyApplyAdapter` (`linkedin.com/jobs`)
7. `GenericAdapter` (fallback, `can_handle` always returns True)

Order matters: the registry selects the first adapter whose `can_handle`
returns True. `GenericAdapter.can_handle` always returns True, so it
MUST be registered last.

## Per-adapter documentation

Each adapter module contains a docstring covering:

- Supported URL patterns (from `DATA_CONTRACTS.md`)
- Navigation behavior
- Login behavior
- Form handling strategy
- Submit safety rules
- Failure behavior
- Test fixtures
- Known limitations

See:

- `src/universal_auto_applier/adapters/greenhouse_adapter.py`
- `src/universal_auto_applier/adapters/lever_adapter.py`
- `src/universal_auto_applier/adapters/workday_adapter.py`
- `src/universal_auto_applier/adapters/smartrecruiters_adapter.py`
- `src/universal_auto_applier/adapters/linkedin_easy_apply_adapter.py`
- `src/universal_auto_applier/adapters/generic_adapter.py`
- `src/universal_auto_applier/adapters/_ats_base.py` (common base)

## Test fixtures

Each platform has five fixture HTML files under
`tests/fixtures/platforms/`:

- `<platform>_job.html` — the job description page with an apply button
- `<platform>_apply.html` — the application form page
- `<platform>_login.html` — the login page
- `<platform>_review.html` — the review/submit page
- `<platform>_changed_layout.html` — a changed-layout page (no apply
  button, no form) used to test safe-failure behavior

All fixtures are static HTML. No live browser, no network access, no
real submissions.

## Safety behavior

| Adapter                | is_trusted | Auto-submits? | Stops on login? | Stops on captcha? | Stops on review? |
| ---------------------- | ---------- | ------------- | --------------- | ----------------- | ---------------- |
| SiemensAdapter         | True       | No (gated by review approval) | Yes (via Siemens CLI exit code) | Yes (via Siemens CLI exit code) | Yes (via Siemens CLI exit code) |
| GreenhouseAdapter      | False      | No            | Yes             | Yes               | Yes              |
| LeverAdapter           | False      | No            | Yes             | Yes               | Yes              |
| WorkdayAdapter         | False      | No            | Yes             | Yes               | Yes              |
| SmartRecruitersAdapter | False      | No            | Yes             | Yes               | Yes              |
| LinkedInEasyApplyAdapter | False    | No            | Yes             | Yes               | Yes              |
| GenericAdapter         | False      | No            | Yes             | Yes               | Yes              |

## Pipeline integration

The pipeline orchestrator was updated minimally:

- Replaced the hardcoded `adapter.__class__.__name__ == "SiemensAdapter"`
  check with `getattr(adapter, "is_trusted", False)`. This routes any
  untrusted adapter (including all five new ATS adapters and the
  Generic fallback) through the existing `_run_generic_path`, which
  uses the shared infrastructure directly and never submits.
- The trusted adapter path (`_run_trusted_adapter_path`) is unchanged.
  It still calls `check_submit_approval(review_state)` before
  `submit_or_pause` and only proceeds if the review state is approved.

The dashboard `POST /api/pipeline/start` endpoint is unchanged. It
creates a `PipelineOrchestrator` and runs it with the provided fixture
HTML (or planning mode if no fixture is given). It cannot submit
applications.

## Limitations

- The adapters do not handle multi-step application flows (Next /
  Continue buttons across multiple pages). Fixture-based tests are
  single-page only.
- The adapters do not bypass login, captcha, SSO, or password fields.
  All such states create an intervention and stop.
- The adapters do not store or submit credentials.
- The adapters do not call live external ATS websites in default tests.
  All tests use local fixture HTML.
- Level 1 (local browser dry-run) tests exist via the shared live browser
  executor (`tests/playwright/test_live_browser_executor.py`) and cover
  the generic/live path rather than per-ATS live pages. Level 2 (live
  external dry-run) per-ATS tests are not implemented for these adapters
  yet; that is planned as `docs/NEXT_WORKPACKAGES.md` WQ-7. Per-ATS
  fixture dry-run (Level 0) tests are present in CI, per
  `DRY_RUN_LEVELS.md`.

## Stop / approval state

Phase 7 is **merged into `main`** (commit `2a1a50a`, later superseded by
later main commits; the adapter set is present in `main` @ `f7c49f7`).
The original development branch was `checkpoint/phase-7-ats-platform-adapters`.

## Verification status (what is proven vs. not)

- **Implemented fixture behavior:** all five ATS adapters and the Generic
  fallback are covered by unit tests (`tests/unit/test_ats_adapters.py`),
  registry tests, and fixture dry-run Playwright tests
  (`tests/playwright/test_phase7_adapter_dry_run.py`) using static HTML
  fixtures. This is verified in CI.
- **Implemented live-browser behavior:** adapters run through the shared
  Level 1/2 live browser path (`browser/live_runner.py`); the opt-in live
  test exists (`tests/live/test_live_browser_real_job.py`) but is excluded
  from default CI.
- **Unverified real external behavior:** no ATS adapter has been exercised
  against a real external site in CI. Real external dry-runs are a planned
  workpackage (see `docs/NEXT_WORKPACKAGES.md` WQ-7).
- **Remaining production orchestration gaps:** see `docs/NEXT_WORKPACKAGES.md`
  (post-submit job-status transitions WQ-1, queue production wiring WQ-3,
  background pipeline WQ-4, restart recovery WQ-5).
