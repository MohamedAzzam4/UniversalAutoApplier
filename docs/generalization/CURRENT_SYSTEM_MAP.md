# Current System Map

This document maps the current state of the three repositories that
participate in the generalized job application system, as of the project
rebaseline (reference: `main` @ `f7c49f7ad520b9765c2221b506960cd8b8e518bc`).

It distinguishes clearly:

- **Implemented fixture behavior** — verified by tests against local HTML
  fixtures (Level 0 / Level 1 dry-runs).
- **Implemented live-browser behavior** — verified by the opt-in
  `live-dry-run` / `live-submit` paths against real pages (Level 2 / Level 3).
- **Unverified real external behavior** — not yet exercised against real
  ATS sites; requires the user's machine and the sanctioned test plan.
- **Remaining production orchestration gaps** — known defects/limits
  recorded in `docs/NEXT_WORKPACKAGES.md` (WQ-1..WQ-9).

---

## 1. UniversalAutoApplier (this repository)

### 1.1 Implemented — core contracts and status machine

| Path | Responsibility | Verification |
|---|---|---|
| `core/statuses.py` | Finite enums: `ApplicationStatus`, `AttemptMode`, `AdapterResultStatus`, `Phase`, `Platform`, `PageState`, `ClickableClassification`, `InterventionKind`, `InterventionStatus`, `HealthState`; `ALLOWED_TRANSITIONS`, `TERMINAL_STATUSES` | Unit tests (`test_statuses.py`) |
| `core/models.py` | Pydantic v2 contracts: `ApplicationJob`, `AdapterResult`, `PageObservation`, `Clickable`, `FormField`, `FieldMapping`, `Intervention`, `AnswerMemory`, `HealthReport`, ... | Contract tests (`test_application_job_contract.py`) |
| `core/identity.py` | Deterministic `application_id` / canonical URL identity | Unit tests (`test_identity.py`) |
| `core/question_models.py` | Typed question/answer models for LLM resolution | Unit tests (`test_typed_answer_validation.py`) |

### 1.2 Implemented — persistence

| Path | Responsibility | Verification |
|---|---|---|
| `persistence/models.py` | SQLAlchemy 2.x ORM: `application_jobs`, `application_attempts`, `phase_results`, `interventions`, `answer_memories`, `artifacts`, `system_runs`, submission tables | Migration/contract tests |
| `persistence/db.py` | Engine factory (`PRAGMA foreign_keys=ON`), `session_scope()` | Unit tests |
| `persistence/migrations.py` | Alembic `upgrade head` programmatically | `tests/contract/test_migrations.py` |
| `persistence/job_repository.py` | Store APIs: upsert, status transitions, attempts, history | Unit tests (`test_job_repository.py`) |
| `migrations/versions/` | 0001..0006 schema migrations | Contract tests |

### 1.3 Implemented — application queue

| Path | Responsibility | Verification |
|---|---|---|
| `application_queue/importer.py` | Reads `application_queue.jsonl`, validates rows as `ApplicationJob`, idempotent upsert | Contract tests (`test_importer.py`), unit tests |

JobHunter export is the source of the queue. UAA does not write JobHunter
files. Production wiring of the queue (startup consumption, import API) is
a remaining gap — see `docs/NEXT_WORKPACKAGES.md` WQ-3.

### 1.4 Implemented — adapters (fixture-tested)

| Path | Responsibility | Trust | Verification |
|---|---|---|---|
| `adapters/base.py` | `ApplicationAdapter` interface, `AdapterResult` | — | Fake-adapter unit tests |
| `adapters/registry.py` | Deterministic registry, first-match routing, Generic fallback last | — | Unit tests (`test_adapter_registry.py`) |
| `adapters/siemens_adapter.py` | Siemens boundary (`is_trusted=True`) | Trusted | Unit tests (`test_siemens_adapter.py`) |
| `adapters/_ats_base.py` | Shared `_UntrustedATSAdapter` base for ATS adapters | Untrusted | Unit tests |
| `adapters/greenhouse_adapter.py`, `lever_adapter.py`, `workday_adapter.py`, `smartrecruiters_adapter.py`, `linkedin_easy_apply_adapter.py` | Platform adapters; never submit; stop on login/captcha/review; fail safely on changed layout | Untrusted | Unit tests (`test_ats_adapters.py`), fixture dry-run tests (`test_phase7_adapter_dry_run.py`) |
| `adapters/generic_adapter.py` | Fallback; `can_handle` always True; never submits | Untrusted | Unit + fixture tests |

**Fixture behavior is implemented; real external behavior on these ATS
platforms is unverified** (see WQ-7). No ATS adapter has ever touched a
real site in default tests.

### 1.5 Implemented — generic navigation (fixture-tested)

| Path | Responsibility | Verification |
|---|---|---|
| `navigator/page_observer.py` | DOM/accessibility extraction of inputs, clickables, forms, login/captcha/review indicators | Unit tests (`test_page_observer.py`), fixture tests |
| `navigator/clickable_classifier.py` | Classifies `safe_apply` / `safe_continue` / `safe_upload` / `dangerous_submit` / `login` / `unknown`; dangerous is never safe | Unit tests (`test_clickable_classifier.py`) |
| `navigator/safe_explorer.py` | observe -> classify -> act loop with max steps; stops before dangerous controls | Unit tests (`test_safe_explorer.py`) |
| `navigator/apply_path_finder.py` | Apply-path discovery helper | Unit tests |

### 1.6 Implemented — form engine (fixture-tested)

| Path | Responsibility | Verification |
|---|---|---|
| `form_engine/schema_extractor.py` | Form field schema extraction (all control types) | Unit tests (`test_schema_extractor.py`) |
| `form_engine/field_mapper.py` | Deterministic mapping first; interventions for low-confidence/unknown required fields | Unit tests (`test_field_mapper.py`) |
| `form_engine/fill_engine.py` | Fill by control type; validation detection; file upload path checks | Unit tests (`test_fill_engine.py`) |
| `form_engine/live_executor.py` | Live-browser form execution used by the runner | Playwright tests (`test_live_browser_executor.py`) |

### 1.7 Implemented — interventions and answer memory

| Path | Responsibility | Verification |
|---|---|---|
| `interventions/store.py` | Intervention create/resolve; pending counts | Unit tests (`test_intervention_store.py`) |
| `interventions/answer_memory.py` | User-confirmed answer memory; normalized question reuse | Unit tests (`test_answer_memory.py`) |
| `interventions/review.py` | Review-before-submit state and gate | Unit tests (`test_review.py`) |
| `interventions/fill_bridge.py`, `navigation_bridge.py` | Bridge generic flow to interventions | Unit tests (`test_fill_bridge.py`, `test_navigation_bridge.py`) |

### 1.8 Implemented — LLM helpers (grounded, no fact invention)

| Path | Responsibility | Verification |
|---|---|---|
| `llm/question_classifier.py`, `question_resolver.py`, `qa_service.py`, `answer_validator.py`, `truth_ledger.py` | Grounded Google AI (Gemma) question classification/resolution; answer validation against candidate evidence; no invented facts | Unit tests (`test_question_classifier.py`, `test_qa_service.py`, `test_truth_ledger.py`, `test_answer_memory.py`), Playwright LLM tests |

### 1.9 Implemented — live browser runner (Level 1/2 dry-run)

| Path | Responsibility | Verification |
|---|---|---|
| `browser/live_runner.py`, `browser/live_models.py` | Playwright live dry-run: open job URL, safe apply/continue clicks (same-tab, redirects, new tabs), form fill, document upload, stops before final submit; screenshots/DOM/trace evidence; `review_ready` / `needs_user_input` / `failed` outcomes | Playwright tests (`test_live_browser_executor.py`, `test_final_pipeline.py`), opt-in live test (`tests/live/test_live_browser_real_job.py`) |

Live external dry-runs against real sites are **unverified in CI**; they
are opt-in (`UAA_ENABLE_LIVE_TEST=1` + `UAA_LIVE_APPLICATION_ID`).

### 1.10 Implemented — controlled submission (Level 3)

| Path | Responsibility | Verification |
|---|---|---|
| `submission/coordinator.py` | Gate checks (feature flag, active approval, snapshot hash + form fingerprint, interventions, field-level checks, claim, duplicate) | Unit tests (`test_submission_coordinator.py`, `test_submission_gates.py`) |
| `submission/execution_service.py` | Executes the approved snapshot in a real browser; claim acquired before browser | Unit + Playwright tests |
| `submission/store.py` | Approvals, claims, results | Unit tests |
| `submission/models.py` | Submission snapshot/result models | Unit tests |
| `api/routes/submit.py` | Submit view API: observe snapshot, approve, revoke, submit | Integration + Playwright tests (`test_submission_harness.py`, `test_controlled_submission.py`, `test_submit_view_dashboard.py`) |

The controlled route is job-type-agnostic: any `review_ready` job may be
submitted manually when all gates pass. Untrusted adapters never
auto-submit. **Post-submit `ApplicationJob.status` transition is a
confirmed gap** — see `docs/NEXT_WORKPACKAGES.md` WQ-1.

### 1.11 Implemented — API, dashboard, CLI, orchestration

| Path | Responsibility | Verification |
|---|---|---|
| `api/app.py`, `api/routes/*` | FastAPI: health, queue, pipeline, interventions, review, logs, retry, status, submit | Integration tests (`test_api.py`, `test_dashboard_api.py`, `test_openapi_contract.py`) |
| `services/health_service.py` | Per-capability health report | Unit tests |
| `services/pipeline_orchestrator.py` | Safe pipeline routing; review-only default; trusted-adapter path | Unit tests (`test_pipeline_orchestrator.py`) |
| `ui/static/*` | Semantic HTML/CSS/vanilla JS dashboard (queue, interventions, history, job detail, logs, submit view) | Playwright tests (`test_dashboard.py`, `test_phase6_dashboard.py`, `test_submission_dashboard.py`) |
| `cli.py`, `__main__.py` | `list-jobs`, `browser-session`, `live-dry-run`, `live-submit` | Unit tests, Playwright tests |

### 1.12 Remaining production orchestration gaps

Recorded with acceptance criteria and tests in `docs/NEXT_WORKPACKAGES.md`:

- WQ-1: post-submit job/history transitions (confirmed defect).
- WQ-3: production queue import / API / startup consumption.
- WQ-4: background real-browser pipeline from the dashboard with
  pause/cancel.
- WQ-5: restart recovery and stale `in_progress` recovery.
- WQ-7: real external dry-runs across representative ATS platforms.
- WQ-8: one staged controlled real submission using the sanctioned plan.

---

## 2. SiemensAutoApplier (external repo)

Owns the existing Siemens-specific discovery/evaluation/tailoring/apply
automation. It remains independently runnable and is the reference behavior
for Siemens applications.

- Siemens page objects (`pages/*.py`), selectors (`locators/selectors.py`),
  and the stage flow (`workflows/apply_workflow.py`) are **never copied**
  into UniversalAutoApplier.
- `adapters/siemens_adapter.py` is the only boundary; it invokes the
  Siemens entry point and converts responses to structured `AdapterResult`.
- The Siemens integration is **unverified against a real Siemens site in
  CI**; see `docs/NEXT_WORKPACKAGES.md` WQ-9 (live adapter hardening and
  Siemens regression verification).

---

## 3. JobHunter (external repo)

Owns job discovery, evaluation, ranking, CV/cover-letter tailoring, and
export of ready-to-apply jobs. UAA consumes its output through the
`application_queue.jsonl` file contract.

- The exporter wiring (`run_all` -> deterministic `application_queue.jsonl`)
  is **not complete** — see `docs/NEXT_WORKPACKAGES.md` WQ-2.
- The import side (UAA importer + contract fixtures) is implemented and
  tested (1.3 above).

---

## 4. Adapter boundaries

- JobHunter -> UAA: file contract `application_queue.jsonl`, validated
  against `ApplicationJob`. UAA never edits JobHunter files. Re-import
  updates descriptive metadata only.
- UAA -> SiemensAutoApplier: `SiemensAdapter` boundary, typed request ->
  structured `AdapterResult`; no human-log parsing; Siemens internals stay
  in Siemens.
- UAA -> generic web: `GenericAdapter` + navigator/form engine; never
  auto-submits; review-before-submit is the default and cannot be bypassed.

---

## 5. Verification

All paths above were verified against the repository tree at
`f7c49f7ad520b9765c2221b506960cd8b8e518bc` (`main`). Default CI passes on
Linux (3.11-3.14) and Windows (3.14). Playwright suite and contract tests
run in CI; live tests are opt-in and excluded by default.