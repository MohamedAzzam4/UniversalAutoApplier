# Active Workpackage — V2-02 Shared Preparation and Intake

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-02 — finish the bounded shared preparation/readiness core through thin entry points. The current selected flow is the owner's DATEV Workday target; preserve the no-submit boundary.
- **Status:** **IN PROGRESS — WIP.** Local snapshot capture/import validation and the isolated named-service intake check are accepted. Producer completion provenance, production app/worker preparation, review-boundary acceptance, correction/reuse, and the combined non-live gate remain pending.
- **Branch:** `codex/v2-intake-preparation`.
- **Base SHA:** `322f3aaea844f61c090d4f4c8c9167fb7ea4f307` (selected V2-02 source checkpoint; not `origin/main`).
- **Last completed/checkpoint SHA:** `322f3aaea844f61c090d4f4c8c9167fb7ea4f307` (published base before this unpublished WIP; resolve the branch head dynamically after preservation).
- **Branch-head verification:** after commit/push, run:

  ```text
  git rev-parse HEAD
  git rev-parse origin/codex/v2-intake-preparation
  ```

  Resolve both values dynamically and require equality before reporting the published checkpoint. Do not embed the commit that contains this handoff. The branch's dry-run push authentication check succeeded before edits. This WIP is unpublished until that equality is verified.

## Completed work

- Preserved the prior DOC-V2-PLAN active handoff byte-for-byte at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_DOC-V2-PLAN_pre_intake_discovery.md` (SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3`, 4,631 bytes).
- Inspected the existing JobHunter queue snapshot read-only: one nonblank row, producer verdict `consider` with queue status `ready_to_apply`, accepted by the current importer on Workday; both referenced PDFs existed and passed `pdfinfo` inspection. No completion/run marker or queue-to-document generation manifest was available, so producer completion and common generation remain unverified. Sanitized evidence is in `docs/evidence/v2/INTAKE_DISCOVERY.md`.
- Ran the named `QueueImportService` twice against the same queue with an explicit path and disposable temporary SQLite store. Both runs succeeded with one line/one import, persisted one job and two run records, and matching source fingerprints. The source was unchanged; the engine was disposed before temp cleanup. No browser, production worker, pipeline, or submission path ran.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.

## Changed files

- **Intake slice:** `src/universal_auto_applier/application_queue/importer.py`, `src/universal_auto_applier/services/queue_import_service.py`, `tests/contract/test_queue_import_service.py`, and `tests/integration/test_queue_import_api.py`.
- **Worker/correction slice still in progress:** `src/universal_auto_applier/services/pipeline_worker_runner.py`, `src/universal_auto_applier/supervisor/tools.py`, and `src/universal_auto_applier/interventions/snapshot_sync.py`.
- **Worker regression/fixture still in progress:** `tests/unit/test_preparation_request_interlock.py` and `tests/fixtures/platforms/worker_preparation_same_url_nested.html`.
- **Intake documentation:** `docs/evidence/v2/INTAKE_DISCOVERY.md`, `docs/handoffs/ACTIVE_WORKPACKAGE.md`, `docs/handoffs/archive/ACTIVE_WORKPACKAGE_DOC-V2-PLAN_pre_intake_discovery.md`, `docs/NEXT_WORKPACKAGES.md`, and `docs/CURRENT_STATE.md`.

## Tests and exact results

- In-memory actual-row validation via current `_validate_and_build_job`: **1 row valid**, `ready_to_apply`, Workday; **2 referenced PDFs present**.
- Actual-input named-service import in disposable SQLite: **PASS** — 2/2 runs `success`, each `total_lines=1`, `imported=1`; final persisted jobs **1** (`ready_to_apply`), durable run rows **2**, both fingerprints equal the source SHA-256; source unchanged; temporary DB only; no browser/pipeline invoked.
- Focused importer/service/API/concurrency regressions: **69 passed in 62.81s**; strengthened replacement regression: **1 passed in 1.46s**.
- Ruff check and format check on the four intake code/test files: **passed**; Pyright: **0 errors**.
- `git diff --check` on the current worktree: **passed**. The combined non-live gate remains **pending**.
- Archive verification: source and archived prior active handoff both had SHA-256 `703b3a0d0b26e788655db56c54b9d87325f25d42e22f2508662e0f6cb4018da3` and length 4,631 bytes.
- Git write authentication: dry-run push for this branch was verified by the integration owner before work; publication is not claimed here.

## Decisions

- DATEV Workday is the owner-selected first flow. The existing queue snapshot is import-compatible, but no producer completion/run marker or queue-to-document generation manifest was available. Local capture/import evidence does not establish producer provenance or a complete generation. Private source paths, candidate values, document filenames and URLs stay out of Git.
- This intake slice accepts local snapshot capture/import through the importer and named queue service only. It does not accept production app/worker preparation or live DATEV behavior.
- No browser navigation, field mutation, upload, or submission was authorized or performed in this slice. Final submission remains subject to the existing explicit approval gates.
- Keep the implementation bounded to the current importer/service path. Preserve the current WQ-8 approval contract and the single browser owner.
- A preserved WIP is not workpackage acceptance. Combined non-live gate is required for accepted code; resolve branch SHA values dynamically.

## Blockers / risks

- The normal runtime still needs an explicit `UAA_QUEUE_PATH`; the local configuration inspected for this discovery had no queue path, and the launcher does not load `.env` automatically. The temp-store service run therefore does not prove production startup/API/worker configuration.
- Producer completion/run provenance and a manifest tying the queue snapshot to its referenced PDFs are unverified. A stable hash establishes capture identity only, not that the producer finished or that all artifacts belong to one generation.
- Production app/API/worker preparation, browser/executor persistence, truthful review boundary, and no-final-request acceptance remain pending. No live DATEV readiness is claimed.
- Combined non-live gate and production app/worker acceptance are pending while implementation work continues in parallel.

## Exact next action

Preserve this intake milestone, then continue the production local app/API/worker preparation acceptance using an explicit local queue path and deterministic fixture. Do not navigate or prepare on live DATEV without separate authorization. After the integration code settles, run the combined non-live gate and record its exact output:

```powershell
python -m pytest -m "not live"
git diff --check
git rev-parse HEAD
git rev-parse origin/codex/v2-intake-preparation
```

- **Last updated:** 2026-09-26T14:46:43Z.
