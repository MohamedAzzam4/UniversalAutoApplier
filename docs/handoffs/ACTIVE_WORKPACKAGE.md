# Active Workpackage

- **WP ID:** WP-H0 — Project Rebaseline and Durable AI Handoff (in progress).
- **Branch:** `checkpoint/project-rebaseline`
- **Base SHA:** `f7c49f7ad520b9765c2221b506960cd8b8e518bc` (`origin/main`)
- **Current HEAD:** `5915ead0d7a0e3239a0f91134f91140fe422bf0c` (commit 1 of 2; commit 2 pending review corrections)
- **Last updated:** 2026-08-04

## Objective

Documentation-only rebaseline: correct submission capability language,
record the confirmed post-submit status-transition defect as WQ-1, expand
the next-workpackage backlog, add the session/checkpoint protocol for AI
context resets, finish the stale-document cleanup, and fix minor doc
defects. No runtime code, tests, config, migrations, or workflows change.

## Status

In progress. Commit 1 (`5915ead`) delivered the initial handoff pack.
Commit 2 (this session) applies the review corrections. Not merged.

## Completed work (commit 1: `5915ead`)

- Created `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/NEXT_WORKPACKAGES.md`,
  `docs/handoffs/ACTIVE_WORKPACKAGE.md`.
- Updated `README.md`, `docs/generalization/ROADMAP.md`,
  `docs/generalization/AI_HANDOFF_PROMPTS.md`,
  `docs/generalization/DRY_RUN_LEVELS.md`,
  `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`.

## Completed work (commit 2, in progress)

- Correct submission capability language (all five handoff docs).
- WQ-1 confirmed-defect status and required future behavior.
- Expanded `NEXT_WORKPACKAGES.md` (WQ-1 .. WQ-9 + optional UI polish).
- AGENTS.md session protocol, ACTIVE_WORKPACKAGE required content, git rules.
- `CURRENT_SYSTEM_MAP.md` and `PHASE_7_ATS_ADAPTERS.md` cleanup.
- Minor doc defect fixes (Gemma wording, AGENTS.md typo, commands/paths).

## Changed files (commit 2)

- `AGENTS.md`
- `docs/CURRENT_STATE.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/generalization/CURRENT_SYSTEM_MAP.md`
- `docs/generalization/PHASE_7_ATS_ADAPTERS.md`
- `docs/generalization/DRY_RUN_LEVELS.md`
- `docs/generalization/ROADMAP.md`
- `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`
- `README.md`

## Tests

None required (documentation-only). Verification is `git diff --check` and
the stale-phrase search documented in the task.

## Decisions made

- Generic/ATS jobs are submit-eligible via the controlled live-submit
  CLI/API when all gates pass; untrusted adapters never auto-submit.
- The post-submit job-status transition gap is a confirmed implementation
  defect (WQ-1), not an open design question.
- No merge to `main` in this workpackage; merge once via reviewed PR.

## Blockers / risks

- No runtime verification possible from sandbox; real submission requires
  the user's machine per the controlled test plan.
- Stale planning docs that were NOT part of this cleanup (e.g.,
  `DEPLOYMENT_AND_REPO_STRATEGY.md` "initial repository milestones",
  `TECHNICAL_BASELINE.md` "before Phase 1 begins") may still read as
  forward-looking; recorded for a future doc pass.

## Exact next action

1. (Done) Editing commit-2 files.
2. (Done) Verify: `git diff --check`; `git diff --name-only
   origin/main...HEAD`; stale-phrase search; submission-rule consistency
   review.
3. `git add` the documentation files; `git commit`; `git push`.
4. Do not merge. Report READY/NOT READY for merge.

## Session protocol reminder

At session start: `git fetch origin`, verify repo/branch/base SHA, inspect
`git status`, read the handoff pack, stop if local changes could be
overwritten. During work: update this file after every major milestone;
checkpoint before ~60-70% context, before pausing/handoff/switching AI;
never rely on chat history as project memory.

## Rules

- Unknown platforms never auto-submit. Untrusted adapters (`is_trusted=False`)
  never submit through adapter `submit_or_pause`.
- A `review_ready` job (generic or ATS) may be submitted manually via the
  controlled `live-submit` CLI / submission API only when
  `UAA_ENABLE_REAL_SUBMISSION=true`, the snapshot is explicitly approved,
  high-risk fields are confirmed, and no intervention/stale/duplicate gate
  blocks it.
- Siemens is the only trusted adapter path, but not the only job type
  supported by manually approved controlled submission.
- Never parse human logs to determine submission success.
- Keep the handoff files updated when this state changes.