# Active Workpackage

- **WP ID:** WQ-7A — live dry-run infrastructure PR refresh against merged main (PR #12 consumed).
- **Status:** IN PROGRESS — PR #12 merged to `main` (`6828f1a`); PR #11 refreshed by merging new `main` no-ff and pushed; awaiting final 6-check CI on the refreshed head.
- **Branch:** `checkpoint/wq-7-real-ats-dry-runs` (actual PR #11 head ref)
- **Base SHA:** `2733a1a1da082946857692e0902b21f81033a685` (pre-task `origin/main`) → new main `6828f1a5f5ed36bc630cb5c7b7ef43b346845ae2` (PR #12 squash `test(queue-import): make concurrent import verification deterministic`)
- **PR:** https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/11 (open, not draft, not merged)
- **Original PR head SHA:** `76b7eeae95c5e0aed4048e1eb85eaafd19aee568`
- **Refresh merge commit:** `6bd8687789768f208ffbae7e61a43f58aed55e90` (`git merge --no-ff origin/main`, message `chore: refresh WQ-7A checkpoint from latest main`; parents `76b7eea` + `6828f1a`). The subsequent handoff commit advances HEAD past this merge.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs
  git rev-parse origin/main
  ```

  Local PR head must equal remote PR head before handoff/review.
- **Last updated:** 2026-08-15

## Objective

Merge the already-approved deterministic concurrency-test PR #12 (squash once
through the GitHub REST API), then refresh WQ-7A PR #11 against the new `main`
using a normal `--no-ff` merge pushed to the existing checkpoint branch. Do NOT
start WQ-7B, do NOT merge PR #11, do NOT modify JobHunter/SiemensAutoApplier/
ERP-Yarn. Windows-only dispatch of the Windows CI workflow on `main` is
expected because `verify-windows-py314.yml` does not run on `main` pushes.

## Rules (from the task and repo doctrine)

- Never `reset --hard` / `clean` / force-push / rewrite history. Preserve all
  pre-existing untracked files. Keep checkpoint branches permanently.
- Use only the GitHub REST API with the PAT from the credential store (env
  var absent). `gh` is a browser-opener shim — never use it. Never print,
  log, commit, or persist the PAT; never put the PAT in `remote.origin.url`.
- Merge PR #12 exactly once via REST squash; never locally, never force-push.
- If the PR #12 pre-merge gates or post-merge CI fail → STOP (BLOCKED). If the
  PR #11 refresh conflicts → STOP and report exact files; never guess-resolve.
- PR #11 must stay unmerged and open at the end.

## Completed work

- Startup verified: repo toplevel correct; branch under check was
  `checkpoint/wq-5-stale-run-recovery` (head `cdec5e6`); untracked debug
  artifacts (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`)
  preserved and never staged. Read AGENTS.md, CURRENT_STATE.md,
  NEXT_WORKPACKAGES.md, ACTIVE_WORKPACKAGE.md. Remote URL has no PAT.
- PAT availability: no named env var set; token supplied by the wincred
  credential-manager entry for `github.com` (40-char secret, used only in an
  in-memory variable, header `Authorization: Bearer`, never persisted).
- **Part A — PR #12** verified via REST API before merge: state=open,
  merged=false, head SHA `82347a6c4edbe7b31495ce1121f12a4e67c2d5b3` (exact),
  base=main `2733a1a1da082946857692e0902b21f81033a685` (exact),
  mergeable=true, mergeable_state=clean, diff = exactly the 3 reviewed files
  (`docs/CURRENT_STATE.md` +8, `tests/integration/test_queue_import_api.py`
  +79/-12, `tests/integration/test_queue_import_concurrency.py` +292), and all
  6 head checks completed/success (Linux 3.11/3.12/3.13/3.14, Windows Core,
  Windows Playwright).
- **Part A merge:** squash-merge PR #12 once via REST `PUT /pulls/12/merge`
  with `merge_method=squash`, `commit_title=test(queue-import): make
  concurrent import verification deterministic`. Result SHA
  `6828f1a5f5ed36bc630cb5c7b7ef43b346845ae2` (merged=true; single parent
  2733a1a; stat = exactly the 3 files, +379/-12). Confirm PR #12 state=closed,
  merged=true, merged_commit_sha=6828f1a. Checkpoint branch
  `checkpoint/concurrent-import-deterministic-test` preserved at `82347a6`.
- **Part A main CI:** verify-linux push on 6828f1a (run 31847561183) →
  success; verify-windows-py314 dispatched via REST
  `workflow_dispatch ref=main` (it has no `push: main` trigger) → run
  31847583442 → success.
- **Part B — PR #11 inspected:** API state=open, merged=false, draft=false,
  base=main `2733a1a` (pre-merge), head ref `checkpoint/wq-7-real-ats-dry-runs`
  (NOT the name in the task text, but head SHA `76b7eea...` matches the
  reported PR head exactly — same PR, unambiguous). `merge-tree --write-tree
  --name-only` on (PR head, new main) = clean (rc=0, no conflicted files).
- **Part B refresh:** created local
  `checkpoint/wq-7-real-ats-dry-runs` from `origin/...` (76b7eea) and ran
  `git merge --no-ff origin/main -m "chore: refresh WQ-7A checkpoint from
  latest main"` → merge commit `6bd8687` (clean, ort strategy; only PR #12's 3
  files flowed in). Pushed to the existing checkpoint branch (see push note).
- **Part B net diff vs new main** (`git diff origin/main...HEAD`), 15 files —
  all WQ-7A content; PR #12 files NOT present: `.env.example` (M, purely
  additive opt-in WQ-7A settings), `docs/handoffs/ACTIVE_WORKPACKAGE.md` (M),
  `docs/handoffs/WQ7_LOCAL_LIVE_RUN.md` (A),
  `src/universal_auto_applier/browser/live_runner.py` (M),
  `src/universal_auto_applier/browser/submit_interlock.py` (A),
  `src/universal_auto_applier/cli.py` (M), `config.py` (M),
  `execution_mode.py` (A), `services/live_dry_run_platforms.py` (A),
  `synthetic_profile.py` (A), `tests/live/test_live_platform_dry_runs.py` (A),
  `tests/playwright/test_wq7_production_safety.py` (A),
  `tests/playwright/test_wq7_submit_safety_guard.py` (A),
  `tests/unit/test_wq7_live_dry_run_platforms.py` (A),
  `tests/unit/test_wq7_synthetic_profile.py` (A). No `.github/workflows`,
  `pyproject.toml`, pytest config, conftest, or migration changes → no
  workflow/setup overlap and no mode-only changes.

## Tests and results (2026-08-15, refreshed working tree)

- `ruff check src tests migrations` → all checks passed.
- `ruff format --check src tests migrations` → 197 files already formatted.
- `pyright` → 0 errors, 0 warnings, 0 informations.
- `pytest -m "not live and not playwright" -q` → **1191 passed, 244 deselected**
  (separate run; respected the sandbox memory cap).
- `pytest tests/playwright -q` → **242 passed** (separate run).
- `git diff --check` → clean. Untracked debug artifacts untouched.

## Decisions made

- PR #11 head ref is `checkpoint/wq-7-real-ats-dry-runs`, not
  `checkpoint/wq-7a-live-dry-run-infrastructure` as stated in the task; the
  confirmed head SHA `76b7eea` matches, so no ambiguity — operated on the real
  PR branch and its PR head.
- Refresh via a plain `--no-ff` merge of `origin/main` (no rebase, no
  force-push), exactly as instructed; merge-tree pre-check proved it clean.
- Windows main CI dispatched manually (`workflow_dispatch ref=main`) because
  `verify-windows-py314.yml` does not trigger on `main` pushes.
- Only the GitHub REST API was used for all remote mutations; the token lived
  in one PowerShell session variable and was nulled after use.

## Blockers / risks

- None. PR #12 merged; main CI green on both workflows; PR #11 refreshed
  cleanly with all local gates green. Awaiting final 6-check CI result on the
  refreshed PR #11 head before the report decision.
- Reminder: do NOT merge PR #11 and do NOT start WQ-7B. Keep
  `checkpoint/concurrent-import-deterministic-test` and all other checkpoint
  branches.

## CI results (2026-08-15)

- `main` after PR #12: Linux `verify-linux` (run 31847561183) → success;
  Windows `verify-windows-py314` (run 31847583442, manually dispatched) →
  success.
- PR #11 refreshed head: six checks (Linux 3.11/3.12/3.13/3.14, Windows Core,
  Windows Playwright) pending after the handoff+refresh push; resolve run IDs
  from https://github.com/MohamedAzzam4/UniversalAutoApplier/actions and
  verify final mergeability (open, merged=false, mergeable=true,
  mergeable_state=clean, local==remote==PR head).

## Exact next action

1. Confirm the refreshed head push updated PR #11.
2. Poll the six PR #11 checks on the final head to terminal.
3. Verify PR open / merged=false / mergeable=true / mergeable_state=clean and
   local==remote==PR head.
4. Write the final two-part report with the single decision (READY FOR MERGE
   REVIEW / NEEDS CHANGES / BLOCKED).

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7-real-ats-dry-runs
  ```

## Rules

- Never merge or push to `main` directly — only through the reviewed PR,
  exactly once. Preserve all `checkpoint/*` branches.
- Only commit what the workpackage asked for; never commit live-runs data,
  `.uaa_data`, `.env`, browsers/databases, screenshots, or the tmp debug
  dirs. `git diff --check` before committing.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.