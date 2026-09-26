# Active Workpackage — V2-02 Shared Preparation and Intake

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 — finish the bounded shared preparation/readiness core through thin entry points. The current selected flow is the owner's DATEV Workday target; preserve the no-submit boundary.
- **Status:** **IN PROGRESS — WIP.** The local snapshot intake slice is published. The focused synthetic worker/routing/persistence selection passed. Ruff check/format and diff checks passed; Pyright reported 0 errors and 0 warnings before final formatting cleanup. Rerun final static checks after cleanup. UI review and the combined non-live gate remain pending. Full required-document preparation remains blocked by the absent flow-specific upload/send contract. Producer completion provenance, DATEV live readiness, and correction/reuse remain pending.
- **Branch:** `codex/v2-intake-preparation`.
- **Base SHA:** `322f3aaea844f61c090d4f4c8c9167fb7ea4f307` (selected V2-02 source checkpoint; not `origin/main`).
- **Last completed/checkpoint SHA:** `84ee4900365a5e7b10734e8bc2b6033f6aa76b92` (published intake checkpoint; `HEAD` and `origin/codex/v2-intake-preparation` matched when verified. The current worker WIP is unpublished.)
- **Branch-head verification:** after commit/push, run:

  ```text
  git rev-parse HEAD
  git rev-parse origin/codex/v2-intake-preparation
  ```

  Resolve both values dynamically and require equality before reporting a published checkpoint. Do not embed the commit that contains this handoff. The branch's dry-run push authentication check succeeded before edits. Current worker changes remain unpublished.

## Completed work

- Preserved the prior DOC-V2-PLAN active handoff byte-for-byte at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_DOC-V2-PLAN_pre_intake_discovery.md` (SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3`, 4,631 bytes).
- Inspected the existing JobHunter queue snapshot read-only: one nonblank row, producer verdict `consider` with queue status `ready_to_apply`, accepted by the current importer on Workday; both referenced PDFs existed and passed `pdfinfo` inspection. No completion/run marker or queue-to-document generation manifest was available, so producer completion and common generation remain unverified. Sanitized evidence is in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- Ran the named `QueueImportService` twice against the same queue with an explicit path and disposable temporary SQLite store. Both runs succeeded with one line/one import, persisted one job and two run records, and matching source fingerprints. The source was unchanged; the engine was disposed before temp cleanup. No browser, production worker, pipeline, or submission path ran.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Published intake checkpoint `84ee4900365a5e7b10734e8bc2b6033f6aa76b92`; dynamic branch-head verification returned the same SHA for local `HEAD` and `origin/codex/v2-intake-preparation`.
- Worker/correction implementation is underway. The focused local synthetic app/API/subprocess-worker selection passed with persisted attempt/phase assertions; see `docs/evidence/v2/WORKER_PREPARATION.md` for exact coverage and limits.
- Safety review found that native `Resume` selection can yield `selection_verified` / `native_selection`, while `upload_contract` remains unset and `execute_live_form` has no named qualified native-upload flow. The safe gate therefore rejects `review_ready`; keep this fail-closed behavior.

## Changed files

- **Current unpublished worker/correction WIP:** `src/universal_auto_applier/persistence/job_repository.py`, `src/universal_auto_applier/services/pipeline_worker_runner.py`, `src/universal_auto_applier/supervisor/tools.py`, `src/universal_auto_applier/interventions/snapshot_sync.py`, `tests/integration/test_pipeline_worker.py`, `tests/unit/test_preparation_request_interlock.py`, and `tests/fixtures/platforms/worker_preparation_same_url_nested.html`. This list reflects the worktree at handoff update and must be refreshed after implementation changes.
- **Published intake files:** importer/service implementation, their contract/API tests, and the sanitized intake evidence are preserved at the checkpoint above. The source-generation and live-flow limitations remain in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- **Current handoff/status/evidence files:** `docs/handoffs/ACTIVE_WORKPACKAGE.md`, `docs/NEXT_WORKPACKAGES.md`, `docs/CURRENT_STATE.md`, and `docs/evidence/v2/WORKER_PREPARATION.md`.

## Tests and exact results

- In-memory actual-row validation via current `_validate_and_build_job`: **1 row valid**, `ready_to_apply`, Workday; **2 referenced PDFs present**.
- Actual-input named-service import in disposable SQLite: **PASS** — 2/2 runs `success`, each `total_lines=1`, `imported=1`; final persisted jobs **1** (`ready_to_apply`), durable run rows **2**, both fingerprints equal the source SHA-256; source unchanged; temporary DB only; no browser/pipeline invoked.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Ruff check and format check on the four intake code/test files: **passed**; Pyright: **0 errors**.
- `git diff --check` on the published intake checkpoint: **passed**. Worker-WIP diff check: **passed**; the combined non-live gate remains **pending**.
- Focused worker selection: **12 passed in 41.34s** across `tests/unit/test_preparation_request_interlock.py`, `tests/unit/test_supervisor_v0.py::test_g_mapper_defect_repair_ticket`, and `tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation`. The nested three-step no-upload route persists attempt/phase state; required-field, unqualified-upload, and denied-HTTP paths remain blocked as expected.
- Worker WIP static checks reported: Ruff check **passed**; Ruff format check **passed (6 files already formatted)**; Pyright **0 errors, 0 warnings before final formatting cleanup**; `git diff --check` **passed**. Production diff review requested no further code changes. Rerun final static checks after any formatting cleanup.
- UI review and combined non-live gate: **pending**. The focused pass does not accept full required-document preparation or DATEV live readiness.
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
- A preserved WIP is not workpackage acceptance. Combined non-live gate is required for accepted code; resolve branch SHA values dynamically.

## Blockers / risks

- The normal runtime still needs an explicit `UAA_QUEUE_PATH`; the local configuration inspected for this discovery had no queue path, and the launcher does not load `.env` automatically. The temp-store service run therefore does not prove production startup/API/worker configuration.
- Producer completion/run provenance and a manifest tying the queue snapshot to its referenced PDFs are unverified. A stable hash establishes capture identity only, not that the producer finished or that all artifacts belong to one generation.
- Full required-document preparation, DATEV live readiness, correction/reuse, UI review, and the combined non-live gate remain pending. The focused worker regression proves only the synthetic no-upload route and blocked negative cases.
- Current native upload readiness remains unqualified: `selection_verified` does not prove remote acceptance or satisfy a named flow contract, so the safe gate must continue to block `review_ready` for that required document path.
- Correction/reuse and the combined non-live gate remain pending while implementation work continues.

## Exact next action

Collect the exact UI review result and refresh the changed-file list. Preserve the WIP before the long combined non-live gate. Keep full required-document preparation, production DATEV live readiness, and correction/reuse pending unless separately accepted. Follow up by qualifying the selected flow's upload/send contract after flow discovery; preserve the current fail-closed review-ready gate. After root approves the worker slice and the implementation settles, run the combined non-live gate and record its exact output:

```powershell
python -m pytest -m "not live"
git diff --check
git rev-parse HEAD
git rev-parse origin/codex/v2-intake-preparation
```

- **Last updated:** 2026-09-26T15:14:47Z.
