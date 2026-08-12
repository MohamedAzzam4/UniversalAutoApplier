# Active Workpackage

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`
- **Workpackage:** WQ-7 — Real ATS Dry-Run Verification
- **Branch:** `checkpoint/wq-7-real-ats-dry-runs`
- **Base SHA:** `2733a1a1da082946857692e0902b21f81033a685` (`origin/main`, WQ-6 merge commit)
- **Local HEAD:** Resolved dynamically — run `git rev-parse HEAD`
- **Verified remote HEAD:** Resolved dynamically — run `git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs`
- **Last successful checkpoint time:** 2026-08-12
- **PR:** To be created
- **Status:** IN PROGRESS — Safety guard + counter tests complete. Phases 1, 2, 5, 7, 8 pending.
- **Last updated:** 2026-08-12

## Verify current state

```text
git fetch origin
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs
```

All three values must match. If they don't, fetch again; if still mismatched,
the local HEAD is not on origin — do not continue development.

## Objective

Implement Level-2 dry-runs against real Greenhouse/Lever/Workday/SmartRecruiters
sites with evidence capture. Never performs final submission. LinkedIn Easy Apply
is excluded.

## Completed milestones

- Phase 0: Startup verification, branch creation, push auth verified.
- Phase 3+4 (initial): Implemented `live_dry_run_platforms.py` service,
  `hard_submit_block` flag in `LiveBrowserConfig`, `attempt_submit()` method,
  CLI `live-dry-run-platforms` subcommand, WQ-7 config settings, 20 unit tests.
- Safety audit: Comprehensive call-path audit of every submit-capable code path.
  Found one residual gap: `set_input_files` and `change`-event mutations could
  auto-submit on pages with `onchange="this.form.submit()"` handlers.
- Safety guard: Implemented `ExecutionMode` enum and `SubmitSafetyGuard` class
  with authoritative enforcement at the lowest browser-action layer. Closes the
  residual gap by pre-screening file inputs for auto-submit handlers. Added 30
  counter-based Playwright tests proving zero submit events in all paths.

## Corrected changed-file list (8 files from milestones 1+2, +2 new = 10 total)

1. `src/universal_auto_applier/config.py` — WQ-7 settings
2. `src/universal_auto_applier/browser/live_runner.py` — hard_submit_block flag
3. `src/universal_auto_applier/services/live_dry_run_platforms.py` — per-platform orchestrator
4. `src/universal_auto_applier/cli.py` — live-dry-run-platforms subcommand
5. `tests/unit/test_wq7_live_dry_run_platforms.py` — 20 unit tests
6. `tests/live/test_live_platform_dry_runs.py` — opt-in live test
7. `.env.example` — WQ-7 env vars section
8. `docs/handoffs/ACTIVE_WORKPACKAGE.md` — Updated for WQ-7
9. `src/universal_auto_applier/execution_mode.py` — NEW: ExecutionMode + SubmitSafetyGuard
10. `tests/playwright/test_wq7_submit_safety_guard.py` — NEW: 30 counter-based tests

## Remaining work

- Phase 1: Discover 5 real ATS job URLs (will search, not ask user)
- Phase 2: Data preflight (check candidate data, documents, API config)
- Phase 5: Run live dry-runs on selected jobs (requires opt-in + real URLs)
- Phase 6: Regression fixtures for any defects found (NOT complete — no live-derived fixtures exist yet)
- Phase 7: UI integration (dashboard "Real-site dry run" view)
- Phase 8: Full validation + CI

## Blockers

- **Live URLs needed:** Phase 5 requires real ATS URLs. Phase 1 will discover them.
- **Candidate data:** Synthetic candidate data is used by default. Real candidate
  data (CV, cover letter, profile) must be provided by the operator for real form fills.
- **WQ-7 hard-submit safety:** Now proven with counter-based tests. The
  `SubmitSafetyGuard` blocks all submit-capable actions at the lowest layer.

## Exact next action

1. Wait for operator to provide real ATS URLs and candidate data.
2. Implement Phase 7 (UI integration) — can proceed without live URLs.
3. Run full validation (Phase 8) and open PR.
