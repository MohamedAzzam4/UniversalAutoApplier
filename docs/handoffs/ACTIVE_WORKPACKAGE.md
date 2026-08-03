# Active Workpackage

- **WP ID:** WP-H0 — Project Rebaseline and Durable AI Handoff (complete — awaiting PR review).
- **Status:** complete — awaiting PR review.
- **Branch:** `checkpoint/project-rebaseline`
- **Base SHA:** `f7c49f7ad520b9765c2221b506960cd8b8e518bc` (`origin/main`)
- **Last substantive documentation checkpoint:** `33bb2f4`
- **Branch-head verification (must be run dynamically; do not trust an
  embedded SHA as the current HEAD because committing changes the SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/project-rebaseline
  ```

  The reviewer must resolve the actual branch head with the commands above
  and confirm the two values match before review/handoff.
- **Last updated:** 2026-08-04

## Objective

Documentation-only rebaseline: correct submission capability language,
record the confirmed post-submit status-transition defect as WQ-1, expand
the next-workpackage backlog, add the session/checkpoint protocol for AI
context resets, finish the stale-document cleanup, fix minor doc defects,
and correct the merge-history wording. No runtime code, tests, config,
migrations, or workflows change.

## Status

Complete — awaiting PR review. Commit 1 (`5915ead`) delivered the initial
handoff pack. Commit 2 (`33bb2f4`) applied the review corrections. Commit 3
(this correction: `33bb2f4` is the last substantive checkpoint; the current
branch head is newer) applies the self-referential-HEAD fix, the
operational-gaps expansion, the merge-history wording, and the WQ-6
strengthening. Not merged; open a PR, review, then merge once through the
PR.

## Completed work (commit 1: `5915ead`)

- Created `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/NEXT_WORKPACKAGES.md`,
  `docs/handoffs/ACTIVE_WORKPACKAGE.md`.
- Updated `README.md`, `docs/generalization/ROADMAP.md`,
  `docs/generalization/AI_HANDOFF_PROMPTS.md`,
  `docs/generalization/DRY_RUN_LEVELS.md`,
  `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`.

## Completed work (commit 2: `33bb2f4`)

- Correct submission capability language (all five handoff docs).
- WQ-1 confirmed-defect status and required future behavior.
- Expanded `NEXT_WORKPACKAGES.md` (WQ-1 .. WQ-9 + optional UI polish).
- AGENTS.md session protocol, ACTIVE_WORKPACKAGE required content, git rules.
- `CURRENT_SYSTEM_MAP.md` and `PHASE_7_ATS_ADAPTERS.md` cleanup.
- Minor doc defect fixes (Gemma wording, AGENTS.md typo, commands/paths).

## Completed work (commit 3: this correction)

- Fixed the self-referential HEAD protocol in AGENTS.md and this file
  (dynamic `git rev-parse` resolution; base SHA + last checkpoint instead
  of an embedded current HEAD).
- Expanded `docs/CURRENT_STATE.md` known operational gaps (5 entries).
- Corrected merge-history wording (`2cf3f18` then duplicate `f7c49f7`).
- Strengthened WQ-6 (sequential/parallel, worker limits, atomic handoff,
  no duplicate processing, status visibility, forbidden shortcuts, tests).

## Changed files (commit 3)

- `AGENTS.md`
- `docs/CURRENT_STATE.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/NEXT_WORKPACKAGES.md`

## Tests

None required (documentation-only). Verification is `git diff --check`, the
changed-files audit, the operational-gap list check, and the WQ-6
completeness check.

## Decisions made

- Generic/ATS jobs are submit-eligible via the controlled live-submit
  CLI/API when all gates pass; untrusted adapters never auto-submit.
- The post-submit job-status transition gap is a confirmed implementation
  defect (WQ-1), not an open design question.
- No merge to `main` in this workpackage; merge once via reviewed PR.
- The current branch HEAD must be resolved dynamically
  (`git rev-parse HEAD` / `git rev-parse origin/<branch>`); no file
  embeds its own commit SHA.
- Merge history (`2cf3f18` then duplicate `f7c49f7`) is preserved and not
  rewritten.

## Blockers / risks

- No runtime verification possible from sandbox; real submission requires
  the user's machine per the controlled test plan.
- Stale planning docs that were NOT part of this cleanup (e.g.,
  `DEPLOYMENT_AND_REPO_STRATEGY.md` "initial repository milestones",
  `TECHNICAL_BASELINE.md` "before Phase 1 begins") may still read as
  forward-looking; recorded for a future doc pass.

## Exact next action

1. Reviewer: fetch the branch and resolve its head dynamically:
   `git rev-parse HEAD` and `git rev-parse origin/checkpoint/project-rebaseline`;
   confirm they match.
2. Open a PR: base `main`, head `checkpoint/project-rebaseline`,
   title "docs: rebaseline project state and durable AI handoff".
3. Review the documentation diff; do not merge into `main` more than once.
4. Merge once through the reviewed PR; then update `docs/CURRENT_STATE.md`.

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