# Active Workpackage

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`
- **Workpackage:** WQ-7 — Real ATS Dry-Run Verification
- **Branch:** `checkpoint/wq-7-real-ats-dry-runs`
- **Base SHA:** `2733a1a1da082946857692e0902b21f81033a685` (`origin/main`, WQ-6 merge commit)
- **Local HEAD:** Resolved dynamically — run `git rev-parse HEAD`
- **Verified remote HEAD:** Resolved dynamically — run `git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs`
- **Last successful checkpoint time:** 2026-08-12
- **PR:** To be created
- **Status:** IN PROGRESS — Phase 3+4 complete (runner, config, CLI, tests). Phases 1, 2, 5, 6, 7, 8 pending.
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
- Phase 3+4: Implemented `live_dry_run_platforms.py` service, `hard_submit_block`
  flag in `LiveBrowserConfig`, `attempt_submit()` method (always returns "blocked"),
  CLI `live-dry-run-platforms` subcommand, WQ-7 config settings, 20 unit tests.

## Changed files

- `src/universal_auto_applier/config.py` — WQ-7 settings (opt-in, per-platform URLs, hard submit block)
- `src/universal_auto_applier/browser/live_runner.py` — `hard_submit_block` flag, `attempt_submit()` method
- `src/universal_auto_applier/services/live_dry_run_platforms.py` — NEW: per-platform orchestrator
- `src/universal_auto_applier/cli.py` — `live-dry-run-platforms` subcommand
- `tests/unit/test_wq7_live_dry_run_platforms.py` — NEW: 20 unit tests
- `tests/live/test_live_platform_dry_runs.py` — NEW: opt-in live test

## Remaining work

- Phase 1: Select 5 real ATS job URLs (requires user input for live URLs)
- Phase 2: Data preflight (check candidate data, documents, API config)
- Phase 5: Run live dry-runs on selected jobs (requires opt-in + real URLs)
- Phase 6: Regression fixtures for any defects found
- Phase 7: UI integration (dashboard "Real-site dry run" view)
- Phase 8: Full validation + CI

## Blockers

- **Live URLs needed:** The operator must provide real, currently-open job
  application URLs via env vars (UAA_LIVE_GREENHOUSE_URL, etc.) before Phase 5
  can execute.
- **Candidate data:** Synthetic candidate data is used by default. Real candidate
  data (CV, cover letter, profile) must be provided by the operator for real
  form fills.

## Exact next action

1. Wait for operator to provide real ATS URLs and candidate data.
2. Implement Phase 7 (UI integration) — can proceed without live URLs.
3. Run full validation (Phase 8) and open PR.
