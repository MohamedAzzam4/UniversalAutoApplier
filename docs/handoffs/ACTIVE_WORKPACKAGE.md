# Active Workpackage — V2-00 Baseline and Execution Contracts (NEEDS REVIEW)

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-00 — preserve and document one selected implementation baseline; record the migration head and non-live gate state; adopt execution, re-import, upload, readiness, and browser-ownership contracts; fix only baseline formatting or narrowly attributable test-lifecycle issues.
- **Status:** **NEEDS SUPERVISOR REVIEW.** V2-00 documentation and formatting are ready for review. The combined non-live pytest gate produced an unidentified failure in the first attempt and did not complete; the consent module passes in isolation. No lifecycle code change was made without a narrow reproduction.
- **Branch:** `checkpoint/v2-00-baseline`.
- **Base SHA:** `76b2e1f166dd56398e7234c733ca24d703d0194a` (`origin/main` at branch creation).
- **Selected starting checkpoint:** `68014659ea7df5d2c1c632e27891ffbe478d978b`, pushed before edits. It descends from WQ-8 remote checkpoint `18783ffc1da216709d6a36d010157ce3430fd7e3`, then contains supervisor work and the dashboard-history commit. This is one line of history above `origin/main`, not three divergent branches requiring reconciliation.
- **Last successful checkpoint SHA/time:** `68014659ea7df5d2c1c632e27891ffbe478d978b` at `2026-09-24T13:30:26Z` (initial baseline preservation push). Resolve the current branch head dynamically after any later push.
- **Migration head:** `0016_supervisor` at the selected starting checkpoint.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-00-baseline
  ```

  The two resolved values must match. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- Verified the clean checkout, selected branch ancestry, `origin/main`, WQ-8 checkpoint ancestry, supervisor branch reference, and write access.
- Created and pushed `checkpoint/v2-00-baseline` at the selected dashboard-history checkpoint before substantive edits.
- Verified Alembic head `0016_supervisor`.
- Copied the V2 plan and critique into `docs/v2/` and adopted section-17 contracts for canonical state/commands, UAA-owned re-import keys, truthful upload states, final-boundary readiness, browser ownership, and uncertain side effects.
- Archived the former WQ-8 active handoff at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_WQ-8.md`; the exact WQ-8 owner authorization, one-submission maximum, and Phase-A/Phase-B gate remain in force.
- Recorded V2-01 ownership/readiness requirements and a separate V2-07A dashboard interaction-design package in `docs/NEXT_WORKPACKAGES.md`.
- Corrected Ruff formatting in `api/routes/queue.py` and `cli.py` only.

## Changed files

- `docs/CURRENT_STATE.md`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/handoffs/archive/ACTIVE_WORKPACKAGE_WQ-8.md`
- `docs/v2/UAA_V2_REVIEW_AND_PLAN.md`
- `docs/v2/UAA_V2_PLAN_CRITIQUE.md`
- `src/universal_auto_applier/api/routes/queue.py` (formatting only)
- `src/universal_auto_applier/cli.py` (formatting only)

No tests or production behavior were changed. No real ATS test, authorization, or submission was run.

## Validation results

- `.venv/Scripts/python.exe -m alembic heads` → `0016_supervisor (head)`.
- `.venv/Scripts/python.exe -m ruff check src tests migrations` → passed, 0 errors.
- `.venv/Scripts/python.exe -m ruff format --check src tests migrations` → passed, 238 files already formatted (after correcting the two reported files).
- `.venv/Scripts/python.exe -m pyright` → 0 errors, 0 warnings, 0 informations.
- `git diff --check` → passed.
- `pytest --collect-only -q -m "not live"` → 1,757 tests collected.
- `pytest -m "not live" -q` → one `F` appeared at about 20%; the run was interrupted before pytest printed a traceback or summary. This is not a completed or passing gate.
- `pytest tests/contract tests/integration tests/playwright/test_consent_banner.py -m "not live" --maxfail=1 -q` → timeboxed at 41% with no failure output; not a completed gate.
- `pytest tests/playwright/test_consent_banner.py -q` → 7 passed in 13.02s.
- No live tests were run. Parent review has a separate diagnostic of the known combined-only consent lifecycle failure; no speculative fix was made here.

## Decisions

- Use a dedicated V2-00 checkpoint because the package combines documentation with quality-baseline formatting and possible test-lifecycle work; it is not documentation-only work.
- Treat the selected branch as one ancestry line: WQ-8 checkpoint → supervisor work → dashboard-history commit. Preserve all existing checkpoint branches.
- V2-00 defines contracts but does not implement the V2-01 correctness behavior. V2-01 preserves at least `dashboard_submitted` and `dashboard_submitted_at` through an explicit interim metadata allowlist; full upstream/local fact provenance is V2-03.
- Native file selection is `selection_verified`, not remote acceptance. Asynchronous upload states are `uploading`, `remote_accepted`, `rejected`, and `unknown`; unknown/rejected blocks readiness for that declared async flow.
- `review_ready` requires a verified final review boundary, complete attempt snapshot, required-field read-back, flow-specific document evidence, guarded final action, and no unresolved intervention or unknown side effect.
- No first live form family was selected because no current owner-approved queue target was supplied. Choose it from the actual queue before V2-06 live acceptance.
- V2-07A stays a separate interaction-design package. Its explicit correction sequence is `Save → Saved → Resume queued → Rechecking`; V2-07B UI implementation waits for executor command/event contracts.
- Existing WQ-8 owner requirements are preserved unchanged. V2-00 does not perform a live action or real submission. V2-01 does not start until supervisor checkpoint review.

## Blockers / risks

- Combined non-live pytest is not green or fully assessed. The first failure’s exact test/traceback was not captured; a scoped diagnostic is in progress separately. The consent module passes alone (7/7).
- The live form-family choice remains an owner decision requiring an available queue and approved target. It does not block synthetic fixes or dashboard design.
- The test lifecycle issue is not fixed in this milestone unless a narrow reproduction identifies a scoped repair.

## Exact next action

Review this V2-00 diff (`git status --short`, `git diff --stat`, and the changed files). After supervisor review, commit and push the documentation + formatting milestone, verify local HEAD equals `origin/checkpoint/v2-00-baseline`, then handle any consent lifecycle fix as a separate V2-00 checkpoint before beginning V2-01.

- **Last updated:** 2026-09-24T14:04:07Z.
