# Active Workpackage

- **WP ID:** FINAL-CONTROLLED-SUBMISSION (merged) — now in handoff mode.
- **Branch:** `main` (`f7c49f7ad520b9765c2221b506960cd8b8e518bc`)
- **Last updated:** 2026-08-04 (project rebaseline)

## What is active

The controlled final submission pipeline is complete and merged
(`checkpoint/controlled-final-submission` -> `main` via PR #3,
squash `f7c49f7`). Verified on Linux and Windows CI. The system is in
"documented, ready for a trusted real submission" state, not an
open-ended coding sprint.

## What needs a human right now

- **Real controlled submission is not yet exercised against a live ATS.**
  Follow `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md` on the
  user's machine. It requires a `review_ready` job, a browser profile,
  and explicit env flag `UAA_ENABLE_REAL_SUBMISSION=true`.
- No agent should attempt a real submit from a sandbox.

## Resume point for an agent

1. `git fetch origin` then start from `main`.
2. Read in order:
   - `docs/CURRENT_STATE.md`
   - `docs/handoffs/ACTIVE_WORKPACKAGE.md` (this file)
   - `docs/generalization/ROADMAP.md` (phases 0-8 all done)
   - `docs/generalization/DRY_RUN_LEVELS.md`
   - `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`
3. If you change code, run:
   - `ruff check`, `ruff format --check`, `pyright`, `pytest` (unit,
     contract, integration, pipeline), plus `-m playwright` when UI/browser
     is touched.
4. Do not modify `pyproject.toml` pins without a full re-run on 3.12 and
   3.14. Do not commit `.uaa_data`, browser profiles, or evidence.

## Next candidate workpackages

Recorded in `docs/NEXT_WORKPACKAGES.md`. Highest value:
- WQ-1: document the exact production submit-transitions gap.
- WQ-2: refresh two stale planning docs.

## Rules

- Unknown platforms never auto-submit.
- `review_ready` is the required gate; only trusted adapter + explicit
  approval may submit.
- Never parse human logs to determine submission success.
- Keep the handoff files updated when this state changes.