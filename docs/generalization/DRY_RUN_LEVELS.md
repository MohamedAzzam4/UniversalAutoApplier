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

## Level 3 - Controlled Submit

The controlled real-submission path. Two distinct flows share the same
safety gates; neither is enabled by default:

- **Trusted adapter (adapter-driven) submit.** Only explicitly trusted
  adapters (e.g., `SiemensAdapter` with `dry_run=False` /
  `trusted_auto_submit` mode) may advance through `submit_or_pause`. This
  is the only adapter-driven auto-submit path (`is_trusted=True`);
  generic and ATS adapters (`is_trusted=False`) never submit through
  adapter behavior.
- **Manual controlled submission (job-type-agnostic).** A `review_ready`
  job — generic or ATS, not only Siemens — MAY be submitted manually
  through the `live-submit` CLI or the submission API.

Both flows require ALL of:

- `UAA_ENABLE_REAL_SUBMISSION=true` (and/or `UAA_SUBMIT_MODE=trusted_auto_submit`
  for the trusted-adapter path)
- the current snapshot is explicitly approved (hash + form fingerprint match)
- high-risk fields are explicitly confirmed
- no pending interventions
- review evidence was captured
- no stale snapshot, duplicate-prevention, or concurrency claim gate blocks it

- Never enabled by default. Live submission is exercised only via the
  explicit `live-submit` CLI / submit API, and the safety gate
  (`check_submit_approval`) plus the submission coordinator enforce it.
- Implemented in the controlled-final-submission pipeline
  (`submission/coordinator.py`, `submission/execution_service.py`,
  `api/routes/submit.py`). Still requires a human-approved snapshot; no
  default CI path runs it.

## Summary

| Level | Browser | Network | Submit | Default CI | Implemented |
|-------|---------|---------|--------|------------|-------------|
| 0     | No      | No      | No     | Yes        | Yes         |
| 1     | Yes     | Local   | No     | Optional   | Yes         |
| 2     | Yes     | External| No     | No         | Yes         |
| 3     | Yes     | External| Yes    | No         | Yes (human-approved only) |
