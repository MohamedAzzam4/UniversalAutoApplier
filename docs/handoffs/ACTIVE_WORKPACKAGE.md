# Active Workpackage

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`
- **Workpackage:** WQ-7 — Real ATS Dry-Run Verification (split into WQ-7A/B/C/D)
- **Branch:** `checkpoint/wq-7-real-ats-dry-runs`
- **Base SHA:** `2733a1a1da082946857692e0902b21f81033a685` (`origin/main`)
- **Local HEAD:** Resolved dynamically — run `git rev-parse HEAD`
- **Verified remote HEAD:** Resolved dynamically — run `git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs`
- **PR:** https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/11
- **Status:** WQ-7A complete (infrastructure + synthetic data). WQ-7B/C/D pending.
- **Last updated:** 2026-08-13

## Verify current state

```text
git fetch origin
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs
```

## WQ-7 scope split

- **WQ-7A (this PR):** Infrastructure and synthetic-data preparation. COMPLETE.
  - Submit interlock (defense in depth, not universal guarantee)
  - ExecutionMode + SubmitSafetyGuard
  - SyntheticProfile + synthetic CV/cover letter generation
  - Per-platform dry-run orchestrator + CLI command
  - 53 Playwright tests + 990 unit/contract tests
  - All 6 CI checks pass (Linux 3.11–3.14, Windows Core, Windows Playwright)
  - PR #11 open, mergeable_state=clean

- **WQ-7B:** Local real-site navigation reconnaissance. PENDING.
  - Requires operator's machine with full network access
  - Discover 5 real ATS targets (2 Greenhouse, 2 Lever, 1 Workday/SR)
  - Navigate to forms, observe controls, no data entry
  - See: docs/handoffs/WQ7_LOCAL_LIVE_RUN.md

- **WQ-7C:** Local synthetic fill-only execution. PENDING.
  - Requires WQ-7B targets
  - Fill safe fields with synthetic profile
  - Upload synthetic documents
  - Stop before final submit
  - Record evidence per target

- **WQ-7D:** Dashboard UI integration and final regression closure. PENDING.
  - "Real-site dry run" view in the dashboard
  - Playwright UI tests (desktop + mobile)
  - Full regression suite

## Safety wording (exact)

WQ-7 blocks recognized final form submissions and common browser
form-submission mechanisms as defense in depth. It does not guarantee
that no synthetic data, autosave request, upload, draft, or custom
network request reaches the ATS.

## Changed files (18 in PR #11)

1. `.env.example` — WQ-7 env vars section
2. `.github/workflows/verify-linux.yml` — CI deduplication (from PR #10)
3. `.github/workflows/verify-windows-py314.yml` — CI parallelization (from PR #10)
4. `docs/handoffs/ACTIVE_WORKPACKAGE.md` — this file
5. `docs/handoffs/WQ7_LOCAL_LIVE_RUN.md` — local execution handoff (NEW)
6. `scripts/setup.ps1` — SkipSmokeTests flag (from PR #10)
7. `scripts/setup.sh` — smoke test marker fix (from PR #10)
8. `src/universal_auto_applier/browser/live_runner.py` — hard_submit_block + interlock
9. `src/universal_auto_applier/browser/submit_interlock.py` — JS interlock (NEW)
10. `src/universal_auto_applier/cli.py` — live-dry-run-platforms subcommand
11. `src/universal_auto_applier/config.py` — WQ-7 settings
12. `src/universal_auto_applier/execution_mode.py` — ExecutionMode + SubmitSafetyGuard (NEW)
13. `src/universal_auto_applier/services/live_dry_run_platforms.py` — orchestrator (NEW)
14. `src/universal_auto_applier/synthetic_profile.py` — synthetic profile + documents (NEW)
15. `tests/live/test_live_platform_dry_runs.py` — opt-in live test (NEW)
16. `tests/playwright/test_wq7_production_safety.py` — 23 production-path tests (NEW)
17. `tests/playwright/test_wq7_submit_safety_guard.py` — 30 guard tests (NEW)
18. `tests/unit/test_wq7_live_dry_run_platforms.py` — 20 unit tests (NEW)
19. `tests/unit/test_wq7_synthetic_profile.py` — synthetic profile tests (NEW)

## Exact next action

1. Review and merge PR #11 (WQ-7A infrastructure).
2. Run WQ-7B (navigation reconnaissance) on the operator's machine.
3. Run WQ-7C (synthetic fill) after WQ-7B targets are confirmed.
4. Implement WQ-7D (dashboard UI) after WQ-7C evidence is collected.
