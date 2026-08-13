# Active Workpackage

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`
- **Workpackage:** WQ-7 — Real ATS Dry-Run Verification
- **Branch:** `checkpoint/wq-7-real-ats-dry-runs`
- **Base SHA:** `2733a1a1da082946857692e0902b21f81033a685` (`origin/main`, WQ-6 merge commit)
- **Local HEAD:** Resolved dynamically — run `git rev-parse HEAD`
- **Verified remote HEAD:** Resolved dynamically — run `git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs`
- **Last successful checkpoint time:** 2026-08-13
- **PR:** To be created
- **Status:** IN PROGRESS — Synthetic profile + submit interlock complete. Navigation reconnaissance pending.
- **Last updated:** 2026-08-13

## Verify current state

```text
git fetch origin
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs
```

## Objective

Level-2 dry-runs against real Greenhouse/Lever/Workday/SmartRecruiters sites
with evidence capture. Never performs final submission. LinkedIn Easy Apply
is excluded.

## Accepted-risk decision (2026-08-13)

WQ-7 uses entirely synthetic candidate data. The user accepts that:

- Synthetic data, synthetic documents, autosave requests, uploads, or drafts
  may reach the ATS server.
- Blocking all mutation traffic (fetch, XHR, sendBeacon, POST/PUT/PATCH) would
  break realistic ATS behavior and is NOT implemented.
- The form-submit interlock (JavaScript init-script) blocks ordinary form
  submit events, form.submit, requestSubmit, Enter-based submission, and
  recognized final-submit controls. It is defense in depth, NOT a universal
  guarantee against every custom network submission mechanism.
- WQ-7 accepts this residual risk because all live-test data is synthetic.
- Never claim a mathematical guarantee of zero outbound requests.

## Completed milestones

- Phase 0: Startup verification, branch creation, push auth verified.
- Phase 3+4 (initial): Implemented live_dry_run_platforms.py service,
  hard_submit_block flag, attempt_submit() method, CLI subcommand, config.
- Safety audit: Comprehensive call-path audit of submit-capable code paths.
- Safety guard: ExecutionMode enum and SubmitSafetyGuard class.
- Submit interlock: Browser-side JavaScript interlock installed via
  page.add_init_script() before site scripts. Blocks submit events,
  form.submit(), requestSubmit(), dispatched SubmitEvents. 23 production-path
  tests prove zero form submissions across all vectors.
- Synthetic profile: SyntheticProfile class with example.com email, 555 phone,
  TEST DATA marking. Synthetic CV/cover letter PDFs generated with visible
  "TEST DATA — AUTOMATION DRY RUN — NOT A REAL APPLICATION" watermark.

## Remaining work

- Stage 1: Navigation reconnaissance — **BLOCKED by sandbox network limitation.**
  The sandbox cannot reach JavaScript-rendered Greenhouse/Lever/Workday
  application forms. Must be run from an environment with full network access.
- Stage 2: Synthetic fill-only dry run — **BLOCKED by Stage 1.**
- Phase 7: UI integration (dashboard "Real-site dry run" view).
- Phase 8: Full validation + CI.

## Blockers

- **Sandbox network limitation:** The sandbox's browser can reach some sites
  (e.g., job-boards.greenhouse.io/gitlab) but cannot render JavaScript-heavy
  application form widgets. Lever boards return 404. This prevents target
  discovery and navigation reconnaissance from this environment.
- **Requires operator environment:** Stages 1 and 2 must be run from the
  operator's machine or a CI environment with full network access.

## Changed files

1. `src/universal_auto_applier/config.py` — WQ-7 settings
2. `src/universal_auto_applier/browser/live_runner.py` — hard_submit_block, interlock
3. `src/universal_auto_applier/browser/submit_interlock.py` — JS interlock
4. `src/universal_auto_applier/services/live_dry_run_platforms.py` — orchestrator
5. `src/universal_auto_applier/cli.py` — live-dry-run-platforms subcommand
6. `src/universal_auto_applier/execution_mode.py` — ExecutionMode + SubmitSafetyGuard
7. `src/universal_auto_applier/synthetic_profile.py` — SyntheticProfile + document generation
8. `tests/unit/test_wq7_live_dry_run_platforms.py` — 20 unit tests
9. `tests/unit/test_wq7_synthetic_profile.py` — synthetic profile tests
10. `tests/playwright/test_wq7_submit_safety_guard.py` — 30 guard tests
11. `tests/playwright/test_wq7_production_safety.py` — 23 production-path tests
12. `tests/live/test_live_platform_dry_runs.py` — opt-in live test
13. `.env.example` — WQ-7 env vars
14. `docs/handoffs/ACTIVE_WORKPACKAGE.md` — this file

## Exact next action

1. Run full deterministic validation (ruff, pyright, pytest).
2. Begin Stage 1 (navigation reconnaissance) — requires network access.
3. Open PR after validation passes.
