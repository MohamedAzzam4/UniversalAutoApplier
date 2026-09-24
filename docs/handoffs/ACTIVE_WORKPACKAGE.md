# Active Workpackage — V2-01 F4/T06 Manual-Submission Eligibility

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 F4/T06 — apply one shared eligibility rule so an operator-marked submitted job or a canonical `submitted`/`applied` job cannot be prepared, retried, or submitted again. The V2-01 re-import allowlist (F4/T10) is already checkpointed; full upstream/local fact separation remains V2-03.
- **Status:** **IMPLEMENTATION COMPLETE; SUPERVISOR CHECKPOINT REVIEW REQUIRED BEFORE COMMIT/PUSH.**
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `cb0ceba0bf38206ceb4609e13019b5a6660598f9` (pushed F4/T10 checkpoint).
- **Last completed/checkpoint SHA:** `cb0ceba0bf38206ceb4609e13019b5a6660598f9`.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-01-correctness
  ```

  The two resolved values must match before checkpoint handoff. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- **F1/T01–T02:** skill evidence abstains on absent, negated and contradictory statements, including tested German negative CV evidence. Visa possession is not inferred from `requires_sponsorship`; explicit sponsorship-No mapping remains covered.
- **Consent lifecycle repair:** pytest-playwright's managed `page` fixture replaces nested sync Playwright managers. The ordered dashboard-plus-consent reproduction passed 8/8; the standalone consent module passed 7/7.
- **F2/T03:** checkpointed at `706831cf86f5d28ab7cd862de098b72f643a0112`. Text fills require stable compatible DOM read-back; incompatible rewrites remain interventions with no verified value. Requiredness reaches the submission snapshot. The focused executor module passed 13 tests and the broad non-Playwright selection passed 1,479 tests. Full Playwright-inclusive combined gate remains unverified.
- **F4/T10:** checkpointed at `cb0ceba0bf38206ceb4609e13019b5a6660598f9`. Re-import preserves UAA-owned manual-submission markers and selected answer maps under the interim allowlist, while refusing producer-supplied submission markers on new inserts. Repository/importer tests passed 37; Ruff, Pyright, and the broad non-Playwright selection passed.
- **F4/T06 implemented:** a shared `core.eligibility` rule covers canonical `submitted`/`applied` statuses and the exact boolean `dashboard_submitted=true` marker. Coordinator, retry, observation, supervisor and pipeline/orchestration boundaries use it. Submission paths re-check at the claim boundary; the final coordinator gate still runs before the click. An `ALREADY_SUBMITTED` result from the dashboard marker does not mutate canonical status, consume an in-flight claim, or consume a WQ-8 authorization. Clearing the operator marker restores eligibility unless canonical submitted/applied state or a prior unknown/confirmed ATS outcome still blocks it. A changed-snapshot explicit reapproval does not clear an unknown outcome.

## Current package files

- `src/universal_auto_applier/core/eligibility.py`
- `src/universal_auto_applier/api/routes/retry.py`
- `src/universal_auto_applier/api/routes/submit.py`
- `src/universal_auto_applier/submission/coordinator.py`
- `src/universal_auto_applier/submission/execution_service.py`
- `src/universal_auto_applier/supervisor/service.py`
- `src/universal_auto_applier/supervisor/tools.py`
- `src/universal_auto_applier/services/pipeline_orchestrator.py`
- `src/universal_auto_applier/services/pipeline_worker_runner.py`
- `src/universal_auto_applier/services/orchestration_service.py`
- `tests/unit/test_submission_coordinator.py`
- `tests/unit/test_job_repository.py`
- `tests/unit/test_supervisor_v0.py`
- `tests/unit/test_pipeline_orchestrator.py`
- `tests/integration/test_dashboard_api.py`
- `docs/NEXT_WORKPACKAGES.md`, this handoff, and the archived prior handoff

Parallel F2b work remains outside this package and must not be staged here.

## Validation results

- Focused F4/T06 selection passed: `test_submission_coordinator`, `test_job_repository`, `test_supervisor_v0`, `test_pipeline_orchestrator`, `test_status_transitions`, and `test_dashboard_api` — **157 passed**. After the pipeline counter fix, API candidate-profile, background-worker, and direct orchestrator modules passed **43 tests**.
- Full repository default non-live/non-Playwright gate passed in the shared working tree (which also contained unstaged parallel F2b upload changes): `pytest -m "not playwright and not live" --maxfail=1 -q` — **1,481 passed, 303 deselected in 832.35s**.
- Static gates passed after the final source/test edits: Ruff check, Ruff format check (**239 files already formatted**), Pyright (**0 errors, 0 warnings, 0 informations**), and `git diff --check`.
- A separate bare `pytest -x -vv` run includes Playwright modules and stopped at the pre-existing dashboard header assertion `tests/playwright/test_phase6_dashboard.py::test_queue_view_renders[chromium]`: expected `Documents`, rendered header was `DOCUMENTS`. **385 passed** before that failure in 759.78s. This UI test was not modified; a complete Playwright-inclusive suite is not claimed green.
- No live tests, real ATS actions, or real submissions were run.

## Decisions

- Manual `dashboard_submitted=true` and canonical `submitted`/`applied` block repeat processing. Clearing the marker through the explicit dashboard toggle restores eligibility; this block is not ATS evidence and must not advance canonical job status.
- The coordinator continues to check the latest `outcome_unknown` before duplicate eligibility, so explicit re-approval cannot erase the manual-review requirement.
- Claim-boundary eligibility rechecks occur before claim acquisition. A regression test binds a valid WQ-8 authorization, introduces the operator marker after the initial gate and confirms the authorization remains active/unconsumed. WQ-8 authorization validation/consumption stays in its existing position immediately before the final click.
- F4/T10 re-import ownership remains an interim key allowlist. Full local/upstream fact separation is V2-03.
- F2b upload evidence is a separate parallel slice. V2-07A remains a separate design follow-up with the interaction contract recorded in `docs/NEXT_WORKPACKAGES.md`.

## Blockers / risks

- The Playwright-inclusive bare suite has a separate stale dashboard header assertion (`Documents` versus rendered `DOCUMENTS`) and was not rerun to completion after the failure; no combined Playwright gate is claimed green.
- Parallel F2b changes remain unstaged and are outside this checkpoint. Its upload evidence must be propagated through the API response mapping in a later integration review; preserve WQ-8 plan-hash binding when doing so.
- No real target or submission is authorized or needed for this milestone.

## Exact next action

Inspect exact paths, keep parallel F2b edits unstaged, stage only the F4/T06 files listed above, and send the staged diff for supervisor review. Commit/push only after that review; verify local `HEAD` equals `origin/checkpoint/v2-01-correctness`. Stop before V2-01's next slice.

- **Last updated:** 2026-09-24T19:23:23Z.
