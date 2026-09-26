# V2 Local Worker Preparation — Focused WIP Evidence

**Status:** focused worker checks and correction, local UI review, and repo-wide static checks pass. The first combined non-live gate attempt stopped at 19% after two failures; the corrected full gate is about to rerun. Full required-document preparation is not accepted.
**Published worker checkpoint:** `227aac407a1f605ff7e142b110081911be815752`.
**Published UI checkpoint:** `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd` on `codex/v2-intake-preparation`; `HEAD` and the remote branch matched at verification.
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

Focused worker selection: **12 passed in 41.34 seconds**. Its matrix verifies persisted preparation-phase/attempt evidence on the nested three-step no-upload path and blocks required-field, unqualified-upload, and denied-HTTP cases. The first `pytest -m "not live"` attempt was stopped at 19% after two failures to avoid spending the remaining runtime before correcting them. `TestErrorsVisible::test_failed_job_records_durable_error` and `TestOneFailedJobDoesNotEraseResults::test_previous_results_preserved` exposed swallowed network/navigation errors: the service returned `None`, so the worker reported `needs_input` instead of the required durable `failed` outcome.

The worker and service now support the minimal opt-in `raise_on_error` correction. The affected selection passed **18 tests in 53.60 seconds**, covering both network-failure regressions, `TestProductionWorkerPreparation`, the request-interlock unit suite, WQ-8 snapshot persistence / `TestEventOrderInterlockBeforeNavigation` including the blocked-CMP opt-in case, and the supervisor sync regression. Network failures now produce durable `FAILED` results, earlier job results are preserved, and typed guards / the production worker matrix still pass. Post-correction repo-wide static checks passed: `ruff check src tests migrations`; `ruff format --check src tests migrations` (**244 already formatted**); Pyright (**0 errors, 0 warnings**); `git diff --check`. The corrected full `pytest -m "not live"` gate is about to rerun; no full-gate result is claimed yet.

## Local UI inspection

Root reviewed Python Playwright captures of the actual local app and worker at **1440×900** and **390×844** with three synthetic jobs: one displayed `review_ready` and two displayed `needs_input`. The fixture server observed **no final-application POST**. The initial page/console/request/HTTP inspection reported zero errors.

At 1440×900, long queue-path text wrapped inside the card after the six-line CSS fix. At 390×844, document width was 390 pixels; the tested tile spanned x=201–353 and its path text x=213–341, all within the viewport. Root reviewed both screenshots. The mobile screenshot is `Temp/uaa_dashboard_inspection/run-da8d2751/post-css-ui/dashboard-390x844-queue-path.png`; captures remain local and are not committed. This was Python Playwright inspection, not Playwright MCP.

The legacy Review tab still calls an in-memory API and shows generic defaults for a worker run. The existing Submit panel GET-status view reads the persisted snapshot. The initial `timeout` inspector note came from selecting `CAN SUBMIT` instead of `Can Submit`, not from a UI error. Track Review-tab/persisted-state consistency under V2-07B; this V2-02 work does not claim full UI consistency or add a detailed attempt timeline.

## Document readiness limit

The current native `Resume` path can report `selection_verified` / `native_selection`, but it has no named flow `upload_contract`; `execute_live_form` has no qualified native-upload flow. The safe readiness gate therefore rejects `review_ready` when the required-document path is unqualified. Keep that behavior fail-closed. Qualify the selected flow's upload/send contract after flow discovery; do not add a generalized exception.

The positive nested-route fixture has no upload control. It supports local shared-routing and persistence evidence, not readiness for DATEV or any required-document flow. Actual DATEV live readiness, full preparation, and correction/reuse remain pending and separately gated. The operator surface is limited to current job/run/outcome evidence; this work adds no new API or detailed attempt-timeline UI. The first combined non-live gate attempt stopped after two failures; the targeted correction and static checks now pass, but no full-gate result is claimed until the corrected run completes.
