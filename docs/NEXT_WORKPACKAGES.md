# Next Workpackages

Candidate work ordered by priority. Each entry states what it is, why it
matters, and what must not break. Items here are not committed; they are
the short list a future implementer should pick from after the controlled
submission handoff.

## WQ-1 — Close the production submit gap (High)

The `submit` route/CLI and the local test pipeline end at `review-ready`
for generic/ATS adapters by design. There is no production path that turns
a `review_ready` job into `submitted`/`applied` for the fixture/generic
flow — only the trusted Siemens path (`is_trusted=True`) plus the CLI
`live-submit` handles a real controlled submit. Decide, before improving,
which behaviors are "by design" (documented) vs. actually missing.

Acceptance:
- A short ADR/note recording exactly which transitions are possible today.
- No new dangerous click paths are added.

## WQ-2 — Refresh stale planning docs

Older generalization documents still describe the project as "bootstrap,
no behavior":
- `docs/generalization/CURRENT_SYSTEM_MAP.md` — describes the repo as
  skeleton only; all phases are now implemented.
- `docs/generalization/PHASE_7_ATS_ADAPTERS.md` — states "this branch is
  not merged" and names `checkpoint/phase-7-ats-platform-adapters`;
  phase 7 is merged to main.
- `docs/generalization/ROADMAP.md` — forward-looking; recorded as complete
  in the rebaseline but can carry per-phase "Done" markers if desired.

Acceptance:
- No runtime files changed; docs-only.

## WQ-3 — Siemens adapter dry-run fixture coverage (optional)

`siemens_adapter` exists and is trusted. It currently relies on the
external Siemens repo boundary. If the Siemens repo is available locally,
add a documented fixture/CLI test that proves the adapter fails safely and
produces structured results. This does not change the live boundary.

## WQ-4 — UI polish pass (optional)

The dashboard passes Playwright tests at 1440x900 and 390x844. Optional
polish: pagination on history, filters persistence, and hiding submit
controls for `is_trusted=False` jobs more aggressively.

## WQ-5 — Do not add

- Cloud/VPS deployment.
- Auto-submit for generic/unknown platforms.
- Patching core modules to import FastAPI/SQLAlchemy/Playwright.
- New Siemens selectors or page objects in this repo.

## How to start one

1. Read `docs/handoffs/ACTIVE_WORKPACKAGE.md` and `docs/CURRENT_STATE.md`.
2. Create a `checkpoint/<topic>` branch from main.
3. Implement; run the full regression gate; update the handoff pack.
4. Squash-merge and update `docs/CURRENT_STATE.md`.