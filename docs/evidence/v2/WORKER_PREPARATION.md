# V2 Local Worker Preparation — Focused WIP Evidence

**Status:** focused worker regression and static checks pass; UI review and the combined non-live gate remain pending.
**Published base checkpoint:** `84ee4900365a5e7b10734e8bc2b6033f6aa76b92` on `codex/v2-intake-preparation`.
**Scope:** local synthetic app/API/subprocess-worker and safety fixtures only. No live DATEV navigation, mutation, upload, or submission was performed.

## Focused results

The focused selection reported **12 passed in 41.34 seconds**:

```text
tests/unit/test_preparation_request_interlock.py
tests/unit/test_supervisor_v0.py::test_g_mapper_defect_repair_ticket
tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation
```

The worker fixture verifies the nested three-step no-upload review snapshot and persisted attempt/preparation-phase state. The selection also verifies that an unresolved required field creates an intervention and stops retry, an unqualified native upload stays blocked, and a denied HTTP mutation stays blocked without retry.

An earlier preliminary selection had 8 passes and 2 failures because its native-upload fixture expectations did not reflect the required block for a flow without an upload contract. The fixture and expectations were corrected; the final focused selection passed without weakening the safety gate.

Static results reported on the worker WIP: Ruff check passed; Ruff format check passed (**6 files already formatted**); Pyright reported **0 errors and 0 warnings** before final formatting cleanup; `git diff --check` passed. The production diff was reviewed with no further code changes requested. Rerun final static checks after any formatting cleanup. UI review and the combined non-live gate remain pending.

## Document readiness limit

The current native `Resume` path can report `selection_verified` / `native_selection`, but it has no named flow `upload_contract`; `execute_live_form` has no qualified native-upload flow. The safe readiness gate therefore rejects `review_ready` when the required-document path is unqualified. Keep that behavior fail-closed. Qualify the selected flow's upload/send contract after flow discovery; do not add a generalized exception.

The positive nested-route fixture has no upload control. It supports local shared-routing and persistence evidence, not readiness for DATEV or any required-document flow. Actual DATEV live readiness, full preparation, and correction/reuse remain pending and separately gated. The operator surface is limited to current job/run/outcome evidence; this work adds no new API or detailed attempt-timeline UI.
