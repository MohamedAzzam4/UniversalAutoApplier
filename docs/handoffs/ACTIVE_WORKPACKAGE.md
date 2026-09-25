# Active Workpackage — V2-01 F2b/T04/T19 Upload-Evidence Integration

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 F2b/T04/T19 — preserve truthful file-selection evidence, require an explicit flow contract before upload readiness, propagate evidence through the review API, and bind it into WQ-8 review-plan authorization.
- **Status:** **IMPLEMENTATION COMPLETE; FULL NON-LIVE BROWSER GATE GREEN; SUPERVISOR REVIEW APPROVED; COMMIT/PUSH PENDING.**
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `8cdb3925142f406a94228992ff42c72c21acd187` (pushed F4/T06 eligibility checkpoint from which F2b started).
- **Last completed/checkpoint SHA:** `8ed966d7e9a1c775a268ea2b1f9262d5dda57cd2`.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-01-correctness
  ```

  The two resolved values must match before checkpoint handoff. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- **V2-00 baseline:** checkpointed at `0e21adb43b400dd90a91a3ba754269e1a061375e`; selected branch lineage descends from WQ-8, then the supervisor and dashboard-history commits. Migration head and baseline test state are recorded in `docs/NEXT_WORKPACKAGES.md`.
- **F1/T01–T02:** skill evidence abstains on absent, negated and contradictory statements, including German negative CV text; visa possession is not inferred from `requires_sponsorship`.
- **Consent lifecycle repair:** pytest-playwright's managed page fixture replaces nested sync Playwright managers. Ordered dashboard-plus-consent reproduction passed 8/8; standalone consent module passed 7/7.
- **F2/T03:** checkpointed at `706831cf86f5d28ab7cd862de098b72f643a0112`. Text fills require stable compatible DOM read-back; incompatible rewrites remain interventions. Live requiredness reaches the snapshot.
- **F4/T10 and F4/T06:** re-import protects UAA-owned operational keys under an interim allowlist; shared eligibility blocks duplicate preparation/submission and preserves WQ-8 authorization and unknown-outcome gates. Last combined branch checkpoint is `8cdb3925142f406a94228992ff42c72c21acd187`.
- **F2b/T04/T19 implemented:** native selection stays visible as `selection_verified`, but it is unresolved unless a typed `NativeFinalSubmitUploadContract` explicitly qualifies the matching input. No generic or ATS native flow is qualified by default. Declared async acceptance/rejection requires a valid, unique post-selection signal in the target form/frame; attribution remains unknown for multi-input forms and multi-file bundles without an explicit correlation/aggregate contract. Malformed evidence is unresolved; a local accept-hint mismatch is `unknown`, not a site rejection. Per-document filenames, evidence source/detail, constraints, and contract reach the review API. Approval/readiness/coordinator gates block unresolved uploads. WQ-8 plan hashing binds upload status, contract and evidence, while legacy document plan encodings remain backward-compatible until fresh evidence exists.
- **F2b fallback gate:** the no-browser-context submit route loads the active persisted approval snapshot, validates approval identity and snapshot hashes, and checks current readiness. It never reports `ready_to_submit` when no context can execute a final submit; it returns `submission_not_allowed` with `clicked=false`.
- **WQ-8 interlock test lifecycle:** the interlock regression now uses pytest-playwright's `browser` fixture and closes its context and database engine in `finally`, avoiding nested synchronous Playwright managers in combined runs.
- **Browser-gate follow-up:** the synthetic final-pipeline fixture now sends the selected CV and cover letter as multipart file bytes under test-owned typed contracts. It proves zero submit requests before approval, verifies full SHA-256 hashes after approval, and checks the duplicate guard blocks a second request. LLM acceptance assertions identify interventions by stable field identity and leave CV unresolved without a native contract. The delayed-submit safety vector is opt-in for its dedicated test, and the dashboard header assertion is case-insensitive while still verifying both headers.
- **Playwright lifecycle regression repair:** WQ7C uses pytest-playwright's context fixture; WQ8 uses that context for `run_in_context`, retains public `runner.run()` coverage on a dedicated worker thread, and probes the runner's actual interlocked page. The two WQ8 form-heuristic browser tests use pytest-playwright's `page` fixture and remain marked `playwright`.

## Current package files

- `src/universal_auto_applier/api/models/submission.py`
- `src/universal_auto_applier/api/routes/submit.py`
- `src/universal_auto_applier/browser/live_models.py`
- `src/universal_auto_applier/form_engine/live_executor.py`
- `src/universal_auto_applier/submission/authorization.py`
- `src/universal_auto_applier/submission/coordinator.py`
- `src/universal_auto_applier/submission/models.py`
- `tests/integration/test_live_review_api.py`
- `tests/integration/test_wq8_document_bundle_execution.py`
- `tests/integration/test_wq8_snapshot_persistence.py`
- `tests/playwright/test_live_browser_executor.py`
- `tests/playwright/test_wq7c_synthetic_mutation.py`
- `tests/fixtures/live_browser/final_pipeline_apply.html`
- `tests/harness/final_pipeline_server.py`
- `tests/playwright/test_final_pipeline.py`
- `tests/playwright/test_llm_acceptance.py`
- `tests/playwright/test_phase6_dashboard.py`
- `tests/playwright/test_wq7_production_safety.py`
- `tests/playwright/test_wq8_phase_a_interlock.py`
- `tests/unit/test_wq8_form_heuristic.py`
- `tests/unit/test_llm_integration_fixes.py`
- `tests/unit/test_submission_safety_consistency.py`
- `tests/unit/test_wq8_authorization.py`
- `docs/NEXT_WORKPACKAGES.md`, this handoff, and the archived prior F4/T06 handoff

## Validation results

- Focused F2b selection (live-review API, WQ-8 auth/coordinator, snapshot safety, bundle, live executor, and WQ-8 persistence): **133 passed in 165.52s**.
- Focused WQ-7C synthetic-mutation Playwright module: **9 passed in 39.69s**.
- Ordered Playwright lifecycle reproducer (async upload test, WQ-8 interlock regression, and WQ-8 authorization-store case): **3 passed in 5.64s** with no resource warning.
- Focused rerun of the two broad-gate assertion updates: **2 passed in 5.93s**.
- Full non-live/non-Playwright gate, `pytest -m "not live and not playwright" -q`: **1,502 passed, 309 deselected in 787.59s**.
- Ordered browser lifecycle selection (dashboard, WQ-7B, WQ-7C, WQ-8): **33 passed in 94.72s**; the focused WQ-7C + WQ-8 pair passed **14 in 54.79s**; dashboard plus WQ-8 heuristic browser tests passed **9 in 12.72s**.
- Complete browser-inclusive gate, `.venv\Scripts\python.exe -m pytest -m 'not live' -vv -x --tb=short`: **1,808 passed, 3 deselected in 1,616.60s**.
- Ruff check passed; Ruff format check passed (**239 files already formatted**); Pyright reported **0 errors, 0 warnings, 0 informations**; `git diff --check` passed.
- No live tests, real ATS actions, or real submissions were run.

## Decisions

- Native file selection is evidence of the browser input state, not proof that an arbitrary final form request sends the file. The typed native-final-submit declaration must be explicitly supplied per qualified selector. Without it, selected names stay visible and the file blocks review readiness.
- No ATS async protocol or native final-submit capability is enabled by default. Async terminal states require a declared and validated protocol; non-terminal/ambiguous state remains unresolved.
- The synthetic final-pipeline fixture owns its upload contracts and multipart endpoint; those test declarations do not qualify any production flow.
- A page-classification defect discovered while keeping the synthetic E2E JavaScript neutral is tracked separately under V2-02: `analyze_page` includes script source text in its static error-page signal, so harmless script text containing `new Error(...)` can produce a false `unknown_page`.
- Async status cannot be assigned to one selection when a target form has multiple file inputs or one input carries a multi-file bundle without a per-file/aggregate correlation declaration; those records remain unknown.
- WQ-8 Phase A now requires an explicit qualifying upload contract and a fresh observation before reaching review-ready. This readiness change does not authorize a final submit.
- Snapshot/API completeness and approval use derived unresolved-upload state as well as field state. Legacy documents without upload status remain readable but require re-observation before a new approval.
- The frozen review plan includes upload evidence where present; legacy documents without evidence retain their old canonical plan representation. Authorization remains single-use and existing consumption ordering is unchanged.

## Blockers / risks

- No live flow currently supplies `NativeFinalSubmitUploadContract`; native selections therefore remain unresolved until a specifically qualified flow passes the declaration. This is intentional and prevents arbitrary ATS/generic readiness claims.
- No outstanding implementation blocker remains for this slice. Supervisor review of the staged browser-gate follow-up is approved; production page-classifier behavior remains a separately tracked V2-02 item.
- No real target choice, live action, or submission is authorized or needed for this milestone.

## Exact next action

Commit the supervisor-approved 10-path browser-gate follow-up and push `checkpoint/v2-01-correctness`. Then fetch origin, verify local `HEAD` equals `origin/checkpoint/v2-01-correctness`, and stop before the next V2-01 slice.

- **Last updated:** 2026-09-25T01:46:28Z.
