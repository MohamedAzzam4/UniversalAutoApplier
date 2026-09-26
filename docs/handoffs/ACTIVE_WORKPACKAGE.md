# Active Workpackage — V2-02 Shared Preparation and Intake

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 — finish the bounded shared preparation/readiness core through thin entry points. The current selected flow is the owner's DATEV Workday target; preserve the no-submit boundary.
- **Status:** **IN PROGRESS — WIP.** The worker, CSS, and worker failure-outcome correction checkpoints are published through `5735967`. The combined `pytest -m "not live"` run on that checkpoint exited 1 with 1,864 passed, 1 failed, and 3 deselected in 1,377.31s. Its sole failure was the final-pipeline fixture's telemetry POST receiving the preparation interlock's HTTP 409. A test-only fixture/harness repair now passes its focused test (1 passed in 26.45s); targeted Ruff check/format and Pyright pass. The full gate was not rerun. A job-local scalar field-correction/resume WIP is now implemented; global memory reuse and remaining correction/reuse are pending. Required-document preparation, producer provenance, and DATEV live readiness also remain pending; the Review-tab mismatch is a V2-07B follow-up.
- **Branch:** `codex/v2-intake-preparation`.
- **Base SHA:** `322f3aaea844f61c090d4f4c8c9167fb7ea4f307` (selected V2-02 source checkpoint; not `origin/main`).
- **Last completed/checkpoint SHA:** `5735967` (tested production checkpoint used for the combined gate, not the current branch head. Resolve current publication using the dynamic commands below.)
- **Branch-head verification:** after commit/push, run:

  ```text
  git rev-parse HEAD
  git rev-parse origin/codex/v2-intake-preparation
  ```

  Resolve both values dynamically and require equality before reporting a published checkpoint. Do not embed the commit that contains this handoff. The branch's dry-run push authentication check succeeded before edits. The worker and CSS changes are published; resolve publication of the correction/resume WIP using the branch-head commands above.

## Completed work

- Preserved the prior DOC-V2-PLAN active handoff byte-for-byte at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_DOC-V2-PLAN_pre_intake_discovery.md` (SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3`, 4,631 bytes).
- Inspected the existing JobHunter queue snapshot read-only: one nonblank row, producer verdict `consider` with queue status `ready_to_apply`, accepted by the current importer on Workday; both referenced PDFs existed and passed `pdfinfo` inspection. No completion/run marker or queue-to-document generation manifest was available, so producer completion and common generation remain unverified. Sanitized evidence is in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- Ran the named `QueueImportService` twice against the same queue with an explicit path and disposable temporary SQLite store. Both runs succeeded with one line/one import, persisted one job and two run records, and matching source fingerprints. The source was unchanged; the engine was disposed before temp cleanup. No browser, production worker, pipeline, or submission path ran.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Published intake checkpoint `84ee4900365a5e7b10734e8bc2b6033f6aa76b92`; dynamic branch-head verification returned the same SHA for local `HEAD` and `origin/codex/v2-intake-preparation`.
- Published worker checkpoint `227aac407a1f605ff7e142b110081911be815752`; its focused local synthetic app/API/subprocess-worker selection passed with persisted attempt/phase assertions.
- Published CSS wrap-fix checkpoint `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd`; `HEAD` and `origin/codex/v2-intake-preparation` matched at verification. Local desktop/mobile UI review passed for the inspected synthetic queue states; exact dimensions and limitations are in `docs/evidence/v2/WORKER_PREPARATION.md`.
- Safety review found that native `Resume` selection can yield `selection_verified` / `native_selection`, while `upload_contract` remains unset and `execute_live_form` has no named qualified native-upload flow. The safe gate therefore rejects `review_ready`; keep this fail-closed behavior.
- **Correction/resume WIP:** added an owner-supplied scalar `FIELD_ANSWER` route requiring the current intervention revision and persisted snapshot hash. It stores job-local provenance, matches the exact source field token and step identity, resolves only that pending intervention, revokes only that job's latest unconsumed approval, and queues the existing worker only after all interventions are resolved. Resolving the API request does not start a browser. The route rejects unsafe HTTP/outcome blockers and unsupported fields. It adds no AnswerMemory/global reuse. Conditional-reveal schemas can change the stored step identity before resume; exact-step mismatch fails closed and may leave a fresh field intervention, so conditional-reveal correction is not qualified by this slice.

## Changed files

- **Published worker/correction files:** `src/universal_auto_applier/persistence/job_repository.py`, `src/universal_auto_applier/services/pipeline_worker_runner.py`, `src/universal_auto_applier/supervisor/tools.py`, `src/universal_auto_applier/interventions/snapshot_sync.py`, `src/universal_auto_applier/submission/execution_service.py`, `tests/integration/test_pipeline_worker.py`, `tests/integration/test_wq8_snapshot_persistence.py`, `tests/unit/test_preparation_request_interlock.py`, and the worker fixture (checkpoints `227aac4` and `5735967`).
- **Final-pipeline fixture repair:** `tests/fixtures/live_browser/final_pipeline_apply.html`, `tests/harness/final_pipeline_server.py`, and `tests/playwright/test_final_pipeline.py`; removes fixture telemetry POSTs and reads the actual browser `FileList` on the Playwright owner thread.
- **Published UI file:** `src/universal_auto_applier/ui/static/styles.css` (CSS wrap checkpoint `9a2f0a0`).
- **Published intake files:** importer/service implementation, their contract/API tests, and the sanitized intake evidence are preserved at the checkpoint above. The source-generation and live-flow limitations remain in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- **Correction/resume WIP files:** `src/universal_auto_applier/api/routes/interventions.py`, `src/universal_auto_applier/form_engine/field_mapper.py`, `src/universal_auto_applier/form_engine/live_executor.py`, `src/universal_auto_applier/interventions/resolve_service.py`, `src/universal_auto_applier/interventions/snapshot_sync.py`, `src/universal_auto_applier/persistence/job_repository.py`, `src/universal_auto_applier/submission/store.py`, `tests/integration/test_pipeline_worker.py`, and `tests/integration/test_field_correction_api.py`.
- **Handoff/status/evidence updates:** `docs/handoffs/ACTIVE_WORKPACKAGE.md`, `docs/NEXT_WORKPACKAGES.md`, `docs/CURRENT_STATE.md`, and `docs/evidence/v2/WORKER_PREPARATION.md`.

## Tests and exact results

- In-memory actual-row validation via current `_validate_and_build_job`: **1 row valid**, `ready_to_apply`, Workday; **2 referenced PDFs present**.
- Actual-input named-service import in disposable SQLite: **PASS** — 2/2 runs `success`, each `total_lines=1`, `imported=1`; final persisted jobs **1** (`ready_to_apply`), durable run rows **2**, both fingerprints equal the source SHA-256; source unchanged; temporary DB only; no browser/pipeline invoked.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Ruff check and format check on the four intake code/test files: **passed**; Pyright: **0 errors**.
- Repo-wide static checks after the published worker correction: Ruff check passed; format passed (**244 files already formatted**); Pyright reported **0 errors, 0 warnings, 0 informations**.
- Focused worker selection: **12 passed in 41.34s** across `tests/unit/test_preparation_request_interlock.py`, `tests/unit/test_supervisor_v0.py::test_g_mapper_defect_repair_ticket`, and `tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation`. The nested three-step no-upload route persists attempt/phase state; required-field, unqualified-upload, and denied-HTTP paths remain blocked as expected.
- The full `pytest -m "not live"` run on published checkpoint `5735967` exited 1: **1,864 passed, 1 failed, 3 deselected in 1,377.31s**. The sole failure was `tests/playwright/test_final_pipeline.py::TestFinalCompletePipeline::test_full_pipeline_workflow`; the fixture's `/record-file` telemetry POST received HTTP 409 because the preparation interlock correctly blocked it.
- After the test-only fixture repair, `pytest tests/playwright/test_final_pipeline.py::TestFinalCompletePipeline::test_full_pipeline_workflow -q`: **1 passed in 26.45s**. Targeted Ruff check passed; Ruff format check passed (**2 files already formatted**); Pyright reported **0 errors, 0 warnings, 0 informations**. The combined full gate was not rerun; no all-green full-gate result is claimed.
- Correction worker E2E, `tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation::test_api_correction_is_consumed_by_worker_for_duplicate_labels`: **1 passed in 11.25s**; it exercises two same-label fields through correction, worker consumption, and persisted snapshot readiness without submission.
- Focused existing API/bridge/store/refresh/fill/resume regressions: **61 passed in 25.10s**. Correction API/store contract tests, `tests/integration/test_field_correction_api.py`: **8 passed in 6.41s**, including rollback, stale/replay/conflict, producer metadata protection, approval isolation, and exact-step mapper matching.
- Ruff check passed and Ruff format check passed (**9 files already formatted**) across correction production/tests; Pyright reported **0 errors, 0 warnings, 0 informations**; `git diff --check` passed. No combined full gate was run for this WIP.
- Local UI review via Python Playwright: **passed at 1440×900 and 390×844** for three synthetic jobs (one `review_ready`, two `needs_input`); fixture observed no final-application POST; initial console/page/request/HTTP error counts were zero. Root reviewed both screenshots. The separate Review-tab/persisted-state mismatch remains a V2-07B follow-up; see `docs/evidence/v2/WORKER_PREPARATION.md`.
- The earlier interrupted `9a2f0a0` run stopped at 19% after two worker network-error failures. The worker/service correction passed its affected selection (**18 passed in 53.60s**) and was published in `5735967`; this history does not change the later full-gate failure recorded above.
- Archive verification: source and archived prior active handoff both had SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3` and length 4,631 bytes.
- Git write authentication: dry-run push for this branch was verified by the integration owner before work; publication is not claimed here.

## Decisions

- DATEV Workday is the owner-selected first flow. The existing queue snapshot is import-compatible, but no producer completion/run marker or queue-to-document generation manifest was available. Local capture/import evidence does not establish producer provenance or a complete generation. Private source paths, candidate values, document filenames and URLs stay out of Git.
- This intake slice accepts local snapshot capture/import through the importer and named queue service only. It does not accept production app/worker preparation or live DATEV behavior.
- No browser navigation, field mutation, upload, or submission was authorized or performed in this slice. Final submission remains subject to the existing explicit approval gates.
- Keep the implementation bounded to the current importer/service path. Preserve the current WQ-8 approval contract and the single browser owner.
- The worker acceptance under development is local synthetic qualification. It is separate from actual DATEV live preparation and grants no live-site authorization. The correction route is a local synthetic WIP; global memory reuse, conditional-reveal correction, and actual DATEV behavior remain unqualified.
- A positive shared-routing result would not accept full preparation when required documents lack a qualified send contract. After the selected flow is inspected under appropriate authorization, qualify that flow's upload/send contract explicitly; do not add a generalized upload exception.
- This WIP adds only the explicit job-local scalar correction/resume API route described above; it adds no generalized correction, AnswerMemory reuse, or detailed attempt-timeline UI. The operator surface may report current job/run/outcome evidence only; do not claim that it displays an attempt timeline.
- Local UI review does not establish full dashboard consistency: the legacy Review tab uses an in-memory API and generic worker defaults, while the existing Submit panel reads persisted status. Carry that mismatch to V2-07B.
- A preserved WIP is not workpackage acceptance. Combined non-live gate is required for accepted code; resolve branch SHA values dynamically.

## Blockers / risks

- The normal runtime still needs an explicit `UAA_QUEUE_PATH`; the local configuration inspected for this discovery had no queue path, and the launcher does not load `.env` automatically. The temp-store service run therefore does not prove production startup/API/worker configuration.
- Producer completion/run provenance and a manifest tying the queue snapshot to its referenced PDFs are unverified. A stable hash establishes capture identity only, not that the producer finished or that all artifacts belong to one generation.
- Full required-document preparation, DATEV live readiness, global answer-memory reuse, and a clean combined non-live gate remain pending. Conditional-reveal correction is not qualified: its final-schema step identity may differ from the resumed initial schema, in which case exact-step matching leaves the answer unapplied and the field unresolved. The focused worker checks and viewport review cover only synthetic flows and blocked negative cases.
- Current native upload readiness remains unqualified: `selection_verified` does not prove remote acceptance or satisfy a named flow contract, so the safe gate must continue to block `review_ready` for that required document path.
- Job-local scalar correction/resume is implemented as WIP; wider correction/reuse remains pending. The clean combined non-live gate is deferred to the accepted integration checkpoint.

## Exact next action

Continue the remaining bounded V2-02 work under review. At the accepted integration checkpoint, run a clean combined non-live gate and record its exact result. Then qualify the selected product workflow's upload/send contract after flow discovery. Keep full required-document preparation, production DATEV live readiness, and global correction/reuse pending unless separately accepted. Track legacy Review-tab consistency under V2-07B. Do not claim full UI consistency from the two-viewport review. The commands for the next gate and dynamic branch check are:

```powershell
python -m pytest -m "not live"
git diff --check
git rev-parse HEAD
git rev-parse origin/codex/v2-intake-preparation
```

- **Last updated:** 2026-09-26T18:08:43Z.
