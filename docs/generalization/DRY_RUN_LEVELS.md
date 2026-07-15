# Dry-Run Levels

This document defines the four dry-run levels for UniversalAutoApplier.
Each level increases in realism and risk. Default tests run deterministic
fixture coverage; CI also runs the local Playwright suite where configured.

## Level 0 - Fixture Dry-Run

- Uses saved HTML fixtures only (tests/fixtures/).
- No browser launched.
- No network access.
- Runs in default CI (Linux + Windows).
- Tests: page observer, clickable classifier, safe explorer, form
  schema extraction, field mapping, fill engine, intervention store,
  review gate.
- Safe for every push and PR.

## Level 1 - Local Browser Dry-Run

- Uses Playwright against local fixture pages served from localhost.
- No external websites.
- Verifies browser execution behavior (Playwright locators, fill methods,
  file uploads, screenshots).
- Safe for CI if the fixture server is stable.
- Implemented by `LiveBrowserRunner` and
  `tests/playwright/test_live_browser_executor.py`.

## Level 2 - Live External Dry-Run

- Uses real job/application pages on the internet.
- Requires the explicit `live-dry-run` CLI command and an imported
  application ID.
- Never presses the final submit button.
- Captures screenshot, DOM snapshot, Playwright trace/video, and logs.
- Not part of default CI. Must be explicitly opted in.
- Implemented as an opt-in one-job command. The corresponding pytest test
  additionally requires `UAA_ENABLE_LIVE_TEST=1` and
  `UAA_LIVE_APPLICATION_ID`; it is marked `live` and excluded from normal
  CI selection.

## Level 3 - Trusted Adapter Controlled Submit

- Only for explicitly trusted adapters (e.g., SiemensAdapter with
  `dry_run=False`).
- Requires:
  - `UAA_SUBMIT_MODE=trusted_auto_submit` in config
  - adapter is marked trusted
  - job passed eligibility gate
  - no unresolved interventions
  - review evidence was captured
- Never enabled by default.
- Not yet implemented. The safety gate (`check_submit_approval`)
  exists in Phase 5, but the pipeline orchestration that would call it
  during a real submit is Phase 8.

## Summary

| Level | Browser | Network | Submit | Default CI | Implemented |
|-------|---------|---------|--------|------------|-------------|
| 0     | No      | No      | No     | Yes        | Yes         |
| 1     | Yes     | Local   | No     | Optional   | Yes         |
| 2     | Yes     | External| No     | No         | Yes         |
| 3     | Yes     | External| Yes    | No         | No (Phase 8+)|
