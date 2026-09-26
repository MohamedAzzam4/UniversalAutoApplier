# Active Workpackage — V2-02 Shared Preparation and Intake

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 — finish the bounded shared preparation/readiness core through thin entry points. The current selected flow is the owner's DATEV Workday target; preserve the no-submit boundary.
- **Status:** **IN PROGRESS — WIP.** Worker checkpoint `227aac4` and the CSS wrap fix `9a2f0a0` are published. The focused worker checks, local UI review at 1440×900 and 390×844, and post-correction repo-wide static checks passed. The first combined non-live gate attempt stopped at 19% after two navigation-error failures; the targeted opt-in correction passed its affected 18-test selection, and the corrected full gate is about to rerun. Full required-document preparation remains blocked by the absent flow-specific upload/send contract. Producer completion provenance, DATEV live readiness, and correction/reuse remain pending. The Review-tab/persisted-state mismatch is a V2-07B follow-up.
- **Branch:** `codex/v2-intake-preparation`.
- **Base SHA:** `322f3aaea844f61c090d4f4c8c9167fb7ea4f307` (selected V2-02 source checkpoint; not `origin/main`).
- **Last completed/checkpoint SHA:** `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd` (published CSS wrap fix after the worker checkpoint; `HEAD` and `origin/codex/v2-intake-preparation` matched when verified. Current documentation updates await the combined gate.)
- **Branch-head verification:** after commit/push, run:

  ```text
  git rev-parse HEAD
  git rev-parse origin/codex/v2-intake-preparation
  ```

  Resolve both values dynamically and require equality before reporting a published checkpoint. Do not embed the commit that contains this handoff. The branch's dry-run push authentication check succeeded before edits. The worker and CSS changes are published; the current documentation updates are not yet published.

## Completed work

- Preserved the prior DOC-V2-PLAN active handoff byte-for-byte at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_DOC-V2-PLAN_pre_intake_discovery.md` (SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3`, 4,631 bytes).
- Inspected the existing JobHunter queue snapshot read-only: one nonblank row, producer verdict `consider` with queue status `ready_to_apply`, accepted by the current importer on Workday; both referenced PDFs existed and passed `pdfinfo` inspection. No completion/run marker or queue-to-document generation manifest was available, so producer completion and common generation remain unverified. Sanitized evidence is in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- Ran the named `QueueImportService` twice against the same queue with an explicit path and disposable temporary SQLite store. Both runs succeeded with one line/one import, persisted one job and two run records, and matching source fingerprints. The source was unchanged; the engine was disposed before temp cleanup. No browser, production worker, pipeline, or submission path ran.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Published intake checkpoint `84ee4900365a5e7b10734e8bc2b6033f6aa76b92`; dynamic branch-head verification returned the same SHA for local `HEAD` and `origin/codex/v2-intake-preparation`.
- Published worker checkpoint `227aac407a1f605ff7e142b110081911be815752`; its focused local synthetic app/API/subprocess-worker selection passed with persisted attempt/phase assertions.
- Published CSS wrap-fix checkpoint `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd`; `HEAD` and `origin/codex/v2-intake-preparation` matched at verification. Local desktop/mobile UI review passed for the inspected synthetic queue states; exact dimensions and limitations are in `docs/evidence/v2/WORKER_PREPARATION.md`.
- Safety review found that native `Resume` selection can yield `selection_verified` / `native_selection`, while `upload_contract` remains unset and `execute_live_form` has no named qualified native-upload flow. The safe gate therefore rejects `review_ready`; keep this fail-closed behavior.

## Changed files

- **Published worker/correction files:** `src/universal_auto_applier/persistence/job_repository.py`, `src/universal_auto_applier/services/pipeline_worker_runner.py`, `src/universal_auto_applier/supervisor/tools.py`, `src/universal_auto_applier/interventions/snapshot_sync.py`, `tests/integration/test_pipeline_worker.py`, `tests/unit/test_preparation_request_interlock.py`, and `tests/fixtures/platforms/worker_preparation_same_url_nested.html` (worker checkpoint `227aac4`).
- **Published UI file:** `src/universal_auto_applier/ui/static/styles.css` (CSS wrap checkpoint `9a2f0a0`).
- **Published intake files:** importer/service implementation, their contract/API tests, and the sanitized intake evidence are preserved at the checkpoint above. The source-generation and live-flow limitations remain in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- **Current handoff/status/evidence updates:** `docs/handoffs/ACTIVE_WORKPACKAGE.md`, `docs/NEXT_WORKPACKAGES.md`, `docs/CURRENT_STATE.md`, and `docs/evidence/v2/WORKER_PREPARATION.md` (these documentation updates await the combined gate).

## Tests and exact results

- In-memory actual-row validation via current `_validate_and_build_job`: **1 row valid**, `ready_to_apply`, Workday; **2 referenced PDFs present**.
- Actual-input named-service import in disposable SQLite: **PASS** — 2/2 runs `success`, each `total_lines=1`, `imported=1`; final persisted jobs **1** (`ready_to_apply`), durable run rows **2**, both fingerprints equal the source SHA-256; source unchanged; temporary DB only; no browser/pipeline invoked.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Ruff check and format check on the four intake code/test files: **passed**; Pyright: **0 errors**.
- `git diff --check` on the published intake checkpoint: **passed**. Worker-WIP diff check: **passed**; the combined non-live gate remains **pending**.
- Focused worker selection: **12 passed in 41.34s** across `tests/unit/test_preparation_request_interlock.py`, `tests/unit/test_supervisor_v0.py::test_g_mapper_defect_repair_ticket`, and `tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation`. The nested three-step no-upload route persists attempt/phase state; required-field, unqualified-upload, and denied-HTTP paths remain blocked as expected.
- Focused worker static checks: Ruff check and format passed (**6 files already formatted**); Pyright **0 errors, 0 warnings**; `git diff --check` passed. Repo-wide checks on published checkpoint `9a2f0a0`: Ruff check for `src tests migrations` passed; Ruff format check passed (**244 files already formatted**); Pyright **0 errors, 0 warnings, 0 informations**; `git diff --check` passed. Post-correction repo-wide checks also passed: `ruff check src tests migrations`; `ruff format --check src tests migrations` (**244 already formatted**); Pyright **0 errors, 0 warnings**; `git diff --check` passed.
- Local UI review via Python Playwright: **passed at 1440×900 and 390×844** for three synthetic jobs (one `review_ready`, two `needs_input`); fixture observed no final-application POST; initial console/page/request/HTTP error counts were zero. Root reviewed both screenshots. The separate Review-tab/persisted-state mismatch remains a V2-07B follow-up; see `docs/evidence/v2/WORKER_PREPARATION.md`.
- First combined `pytest -m "not live"` attempt on `9a2f0a0`: **stopped at 19% after two failures**. `TestErrorsVisible::test_failed_job_records_durable_error` and `TestOneFailedJobDoesNotEraseResults::test_previous_results_preserved` exposed swallowed network/navigation errors; the service returned `None`, causing the worker to report `needs_input` rather than durable `failed`. The worker/service opt-in `raise_on_error` correction passed the affected selection (**18 passed in 53.60s**), including both regressions, `TestProductionWorkerPreparation`, request-interlock, WQ-8 snapshot/event-order tests including blocked-CMP opt-in, and supervisor sync. Network failures now produce durable `FAILED` results, earlier job results are preserved, and blocker outcomes remain gated. The corrected full gate is about to rerun. No full-gate result is claimed; focused tests and UI review do not accept full required-document preparation or DATEV live readiness.
- Archive verification: source and archived prior active handoff both had SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3` and length 4,631 bytes.
- Git write authentication: dry-run push for this branch was verified by the integration owner before work; publication is not claimed here.

## Decisions

- DATEV Workday is the owner-selected first flow. The existing queue snapshot is import-compatible, but no producer completion/run marker or queue-to-document generation manifest was available. Local capture/import evidence does not establish producer provenance or a complete generation. Private source paths, candidate values, document filenames and URLs stay out of Git.
- This intake slice accepts local snapshot capture/import through the importer and named queue service only. It does not accept production app/worker preparation or live DATEV behavior.
- No browser navigation, field mutation, upload, or submission was authorized or performed in this slice. Final submission remains subject to the existing explicit approval gates.
- Keep the implementation bounded to the current importer/service path. Preserve the current WQ-8 approval contract and the single browser owner.
- The worker acceptance under development is local synthetic qualification. It is separate from actual DATEV live preparation and grants no live-site authorization. Correction/reuse remains pending.
- A positive shared-routing result would not accept full preparation when required documents lack a qualified send contract. After the selected flow is inspected under appropriate authorization, qualify that flow's upload/send contract explicitly; do not add a generalized upload exception.
- No new API or detailed attempt-timeline UI is planned for this slice. The operator surface may report current job/run/outcome evidence only; do not claim that it displays an attempt timeline.
- Local UI review does not establish full dashboard consistency: the legacy Review tab uses an in-memory API and generic worker defaults, while the existing Submit panel reads persisted status. Carry that mismatch to V2-07B.
- A preserved WIP is not workpackage acceptance. Combined non-live gate is required for accepted code; resolve branch SHA values dynamically.

## Blockers / risks

- The normal runtime still needs an explicit `UAA_QUEUE_PATH`; the local configuration inspected for this discovery had no queue path, and the launcher does not load `.env` automatically. The temp-store service run therefore does not prove production startup/API/worker configuration.
- Producer completion/run provenance and a manifest tying the queue snapshot to its referenced PDFs are unverified. A stable hash establishes capture identity only, not that the producer finished or that all artifacts belong to one generation.
- Full required-document preparation, DATEV live readiness, correction/reuse, and the combined non-live gate remain pending. The focused worker checks and viewport review cover only the synthetic no-upload route, current job/run/outcome display, and blocked negative cases.
- Current native upload readiness remains unqualified: `selection_verified` does not prove remote acceptance or satisfy a named flow contract, so the safe gate must continue to block `review_ready` for that required document path.
- Correction/reuse and the combined non-live gate remain pending while implementation work continues.

## Exact next action

Record the exact corrected combined non-live gate result when it completes. Keep full required-document preparation, production DATEV live readiness, and correction/reuse pending unless separately accepted. Follow up by qualifying the selected flow's upload/send contract after flow discovery; preserve the current fail-closed review-ready gate. Track legacy Review-tab consistency under V2-07B. Do not claim full UI consistency from the two-viewport review. The command used for the final gate and dynamic branch check is:

```powershell
python -m pytest -m "not live"
git diff --check
git rev-parse HEAD
git rev-parse origin/codex/v2-intake-preparation
```

- **Last updated:** 2026-09-26T15:41:28Z.
