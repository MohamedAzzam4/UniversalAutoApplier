# V2 Local Worker Preparation — Focused WIP Evidence

**Status:** WIP. The full combined non-live gate on `5735967` had one fixture-telemetry failure; a test-only repair passes the affected test, but the full gate has not been rerun. Full required-document preparation is not accepted.
**Published worker checkpoint:** `227aac407a1f605ff7e142b110081911be815752`.
**Published UI checkpoint:** `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd`. The published worker failure-outcome correction is `5735967` on `codex/v2-intake-preparation`; `HEAD` and the remote branch matched at verification.
**Scope:** local synthetic app/API/subprocess-worker and safety fixtures only. No live DATEV navigation, mutation, upload, or submission was performed.
**Current WIP:** test-only repair in `final_pipeline_apply.html`, `final_pipeline_server.py`, and `test_final_pipeline.py`; it removes telemetry POSTs and reads the actual browser `FileList` on its Playwright owner thread.

## Focused results

The focused selection reported **12 passed in 41.34 seconds**:

```text
tests/unit/test_preparation_request_interlock.py
tests/unit/test_supervisor_v0.py::test_g_mapper_defect_repair_ticket
tests/integration/test_pipeline_worker.py::TestProductionWorkerPreparation
```

The worker fixture verifies the nested three-step no-upload review snapshot and persisted attempt/preparation-phase state. The selection also verifies that an unresolved required field creates an intervention and stops retry, an unqualified native upload stays blocked, and a denied HTTP mutation stays blocked without retry.

An earlier preliminary selection had 8 passes and 2 failures because its native-upload fixture expectations did not reflect the required block for a flow without an upload contract. The fixture and expectations were corrected; the final focused selection passed without weakening the safety gate.

The earlier worker error-propagation correction passed its affected selection (**18 tests in 53.60 seconds**) and was published in `5735967`. Repo-wide Ruff check/format passed (**244 files already formatted**), and Pyright reported **0 errors, 0 warnings, 0 informations** on that checkpoint.

The combined `pytest -m "not live"` run on `5735967` exited 1: **1,864 passed, 1 failed, 3 deselected in 1,377.31 seconds**. The sole failure was `tests/playwright/test_final_pipeline.py::TestFinalCompletePipeline::test_full_pipeline_workflow`: fixture file-change telemetry POSTed to `/record-file` and received HTTP 409 from the preparation interlock. The test-only repair removes those POSTs and reads the real browser `FileList`; the focused repaired test passed (**1 passed in 26.45 seconds**). Targeted Ruff check/format passed, and Pyright reported **0 errors, 0 warnings, 0 informations**. The full gate was not rerun, so no all-green full-gate result is claimed.

## Local UI inspection

Root reviewed Python Playwright captures of the actual local app and worker at **1440×900** and **390×844** with three synthetic jobs: one displayed `review_ready` and two displayed `needs_input`. The fixture server observed **no final-application POST**. The initial page/console/request/HTTP inspection reported zero errors.

At 1440×900, long queue-path text wrapped inside the card after the six-line CSS fix. At 390×844, document width was 390 pixels; the tested tile spanned x=201–353 and its path text x=213–341, all within the viewport. Root reviewed both screenshots. The mobile screenshot is `Temp/uaa_dashboard_inspection/run-da8d2751/post-css-ui/dashboard-390x844-queue-path.png`; captures remain local and are not committed. This was Python Playwright inspection, not Playwright MCP.

The legacy Review tab still calls an in-memory API and shows generic defaults for a worker run. The existing Submit panel GET-status view reads the persisted snapshot. The initial `timeout` inspector note came from selecting `CAN SUBMIT` instead of `Can Submit`, not from a UI error. Track Review-tab/persisted-state consistency under V2-07B; this V2-02 work does not claim full UI consistency or add a detailed attempt timeline.

## Document readiness limit

The current native `Resume` path can report `selection_verified` / `native_selection`, but it has no named flow `upload_contract`; `execute_live_form` has no qualified native-upload flow. The safe readiness gate therefore rejects `review_ready` when the required-document path is unqualified. Keep that behavior fail-closed. Qualify the selected flow's upload/send contract after flow discovery; do not add a generalized exception.

The positive nested-route fixture has no upload control. It supports local shared-routing and persistence evidence, not readiness for DATEV or any required-document flow. Actual DATEV live readiness, full preparation, and correction/reuse remain pending and separately gated. The operator surface is limited to current job/run/outcome evidence; this work adds no new API or detailed attempt-timeline UI.
