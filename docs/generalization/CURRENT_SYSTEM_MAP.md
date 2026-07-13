# Current System Map

This document maps the current state of the three repositories that
participate in the generalized job application system. It is the deliverable
for Phase 0 Workpackage 0.1 (`docs/generalization/ROADMAP.md`).

The map distinguishes:

- **Implemented** — code that exists and runs today.
- **Planned** — modules reserved by the bootstrap with docstring placeholders;
  no runtime behavior yet.
- **External repos** — sibling repositories not modified by this phase.
- **Adapter boundaries** — the narrow seams through which external repos will
  be integrated later.

No runtime code is changed by this document. Every path reference was
verified against the actual repository tree at the time of writing.

---

## 1. UniversalAutoApplier (this repository)

### 1.1 Implemented — bootstrap skeleton

The bootstrap phase (`main @ 10667fc`) created a runnable skeleton with
health endpoints, a dashboard shell, persistence, and CI, but no application
behavior. Every module below contains real, tested code.

| Path | Responsibility | Status |
|---|---|---|
| `src/universal_auto_applier/__init__.py` | Package marker, `__version__` | Implemented |
| `src/universal_auto_applier/__main__.py` | `python -m universal_auto_applier` entry: applies migrations, starts uvicorn on 127.0.0.1 | Implemented |
| `src/universal_auto_applier/config.py` | `Settings` (frozen Pydantic model), `load_settings()` from env. Rejects `0.0.0.0` bind. | Implemented |
| `src/universal_auto_applier/core/statuses.py` | All finite enums from `DATA_CONTRACTS.md`: `ApplicationStatus`, `AttemptMode`, `AdapterResultStatus`, `Phase`, `Platform`, `PageState`, `ClickableClassification`, `InterventionKind`, `InterventionStatus`, `HealthState`. Includes `ALLOWED_TRANSITIONS` and `TERMINAL_STATUSES`. | Implemented |
| `src/universal_auto_applier/core/models.py` | `HealthReport`, `ComponentHealth` Pydantic v2 models. Full `ApplicationJob`/`AdapterResult`/etc. are deferred to Phase 1. | Implemented (minimal) |
| `src/universal_auto_applier/persistence/models.py` | SQLAlchemy 2.x ORM for all 7 required tables: `application_jobs`, `application_attempts`, `phase_results`, `interventions`, `answer_memories`, `artifacts`, `system_runs` | Implemented (schema only, no business logic) |
| `src/universal_auto_applier/persistence/db.py` | `make_engine()` with `PRAGMA foreign_keys=ON`, `session_scope()` context manager, `create_all()` | Implemented |
| `src/universal_auto_applier/persistence/migrations.py` | `apply_migrations()` — runs Alembic `upgrade head` programmatically; disposes engine in `finally` | Implemented |
| `src/universal_auto_applier/services/health_service.py` | `build_health_report()`, `make_health_report()` — aggregates per-capability health: `api`, `store`, `worker`, `browser`, `jobhunter_queue`, `siemens_adapter` | Implemented |
| `src/universal_auto_applier/api/app.py` | `create_app()` FastAPI factory with lifespan (creates/disposes engine), mounts `/static`, serves dashboard at `/` | Implemented |
| `src/universal_auto_applier/api/routes/health.py` | `GET /api/health` (lightweight, no browser), `GET /api/health/detail` (launches Chromium) | Implemented |
| `src/universal_auto_applier/ui/static/index.html` | Dashboard shell: status card, capabilities list, bootstrap note | Implemented (shell only) |
| `src/universal_auto_applier/ui/static/styles.css` | Dashboard CSS, responsive at 1280x720 / 1440x900 / 390x844 | Implemented |
| `src/universal_auto_applier/ui/static/app.js` | Vanilla JS polling controller with backoff; fetches `/api/health` | Implemented |
| `migrations/env.py` | Alembic online environment; disposes engine in `finally` | Implemented |
| `migrations/versions/0001_initial_schema.py` | Initial migration creating all 7 tables | Implemented |
| `scripts/setup.ps1`, `scripts/setup.sh` | Create `.venv`, install pinned deps, install Chromium, apply migrations, smoke test | Implemented |
| `scripts/run_local.ps1`, `scripts/run_local.sh` | Start the API/dashboard on 127.0.0.1 | Implemented |
| `scripts/test.ps1`, `scripts/test.sh` | Regression gate: ruff + pyright + pytest with marker selection | Implemented |
| `scripts/verify_local.ps1`, `scripts/verify_local.sh` | 10-step local verification mirroring CI workflows | Implemented |
| `.github/workflows/verify-windows-py314.yml` | CI: Windows + Python 3.14, 15 steps | Implemented |
| `.github/workflows/verify-linux.yml` | CI: Linux + Python 3.11/3.12/3.13/3.14 matrix | Implemented |

### 1.2 Planned — reserved packages (docstring placeholders only)

These packages exist as directories with `__init__.py` docstrings pointing
to the roadmap. They contain **no runtime code**. Each will be implemented
in the phase noted.

| Path | Phase | Planned responsibility |
|---|---|---|
| `src/universal_auto_applier/application_queue/` | Phase 1 | `importer.py` — reads `application_queue.jsonl`, validates rows as `ApplicationJob`, idempotent upsert |
| `src/universal_auto_applier/adapters/` | Phase 2 | `base.py` (`ApplicationAdapter` interface), `registry.py` (`AdapterRegistry`), `siemens_adapter.py`, `generic_adapter.py` |
| `src/universal_auto_applier/navigator/` | Phase 3 | `page_observer.py`, `clickable_classifier.py`, `safe_explorer.py` |
| `src/universal_auto_applier/form_engine/` | Phase 4 | `schema_extractor.py`, `field_mapper.py`, `fill_engine.py`, `validators.py` |
| `src/universal_auto_applier/interventions/` | Phase 5 | `store.py` (intervention queue), `answer_memory.py`, review-before-submit state |
| `src/universal_auto_applier/browser/` | Phase 3+ | Playwright wrapper shared by navigator, form_engine, and adapters |

### 1.3 Planned — core contracts not yet implemented

The following Pydantic v2 models are documented in `DATA_CONTRACTS.md` and
referenced by the roadmap, but are **not yet implemented** in
`core/models.py`. Their status enums exist, but the models themselves will
land in Phase 1+.

| Model | Phase | Home |
|---|---|---|
| `ApplicationJob` | Phase 1 (WP 1.1) | `core/models.py` |
| `ApplicationAttempt` | Phase 2 | `core/models.py` |
| `AdapterResult` | Phase 2 | `core/models.py` |
| `PageObservation` | Phase 3 | `core/models.py` or `navigator/` |
| `Clickable` | Phase 3 | `core/models.py` or `navigator/` |
| `FormField` | Phase 4 | `core/models.py` or `form_engine/` |
| `FieldMapping` | Phase 4 | `core/models.py` or `form_engine/` |
| `Intervention` | Phase 5 | `core/models.py` or `interventions/` |
| `AnswerMemory` | Phase 5 | `core/models.py` or `interventions/` |

### 1.4 Planned — dashboard views

The current dashboard is a shell with health status only. The full view set
from `UI_UX_SPEC.md` lands in Phase 6: Queue, Interventions, History, Job
Detail, Logs & Errors, Settings.

---

## 2. SiemensAutoApplier (external repo — `MohamedAzzam4/SiemensAutoApplier`)

This repository is the existing, working Siemens-specific application
automation. It remains independent and is integrated later through a narrow
adapter boundary (Phase 2, WP 2.3). No code is copied into
UniversalAutoApplier.

The relevant modules below were identified by inspecting the actual repo
tree at the time of writing. File paths are relative to
`siemens-auto-apply/` inside the repo.

### 2.1 Workflows — the orchestration layer

| Path | Responsibility | Reuse classification |
|---|---|---|
| `workflows/apply_workflow.py` | The proven Siemens application flow: login → job detail → questions → submit. **Must not be rewritten.** | Siemens-specific; wrapped by `SiemensAdapter` in Phase 2 |
| `workflows/discover_workflow.py` | Siemens job discovery | Siemens-specific; not used by UniversalAutoApplier (JobHunter owns discovery) |
| `workflows/evaluate_workflow.py` | Siemens job evaluation | Siemens-specific; not used by UniversalAutoApplier (JobHunter owns evaluation) |
| `workflows/tailor_workflow.py` | CV/cover-letter tailoring | Siemens-specific; not used by UniversalAutoApplier (JobHunter owns tailoring) |
| `workflows/llm_stage4_answerer.py` | LLM-based question answering | Siemens-specific; candidate for shared utilities later |

### 2.2 Page objects — Siemens-specific selectors

| Path | Responsibility | Reuse classification |
|---|---|---|
| `pages/login_page.py` | Siemens login | Siemens-specific; **must not be rewritten** |
| `pages/search_page.py` | Siemens search | Siemens-specific |
| `pages/job_detail_page.py` | Siemens job detail | Siemens-specific |
| `pages/job_description_fetcher.py` | JD fetch | Siemens-specific |
| `pages/job_questions_page.py` | Siemens job-specific questions | Siemens-specific |
| `pages/global_questions_page.py` | Siemens global questions | Siemens-specific |
| `pages/country_questions_page.py` | Siemens country questions | Siemens-specific |
| `pages/profile_page.py` | Siemens profile | Siemens-specific |
| `pages/results_page.py` | Siemens results | Siemens-specific |
| `pages/success_page.py` | Siemens success confirmation | Siemens-specific |
| `locators/selectors.py` | Siemens CSS/XPath selectors | Siemens-specific; **must not be copied** into UniversalAutoApplier |

### 2.3 Reusable utilities — candidates for future sharing

These utilities are Siemens-specific in their current form but embody
patterns that UniversalAutoApplier will need. They are **not** copied; they
are reimplemented in UniversalAutoApplier as generic versions, or wrapped
through the adapter boundary.

| Path | Responsibility | UniversalAutoApplier equivalent |
|---|---|---|
| `history/history_store.py` | JSON-based application history | Replaced by SQLAlchemy persistence (`persistence/models.py`) |
| `utils/submission_guard.py` | Prevents accidental double-submit | Replaced by `ApplicationStatus` state machine + `review_ready` gate |
| `utils/eligibility_gate.py` | Job eligibility filtering | Stays in Siemens; adapter calls it |
| `utils/evidence.py` | Screenshot/trace capture | Reimplemented in `browser/` (Phase 3) |
| `utils/safe_actions.py` | Safe click helpers | Reimplemented in `navigator/` (Phase 3) |
| `utils/telegram.py` | Telegram notifications | Not in v1 scope; may be added later |
| `browser/session.py` | Playwright session management | Reimplemented in `browser/` (Phase 3) |
| `ui/api.py` | Siemens dashboard API | Replaced by `api/` in UniversalAutoApplier |
| `logger/logger.py` | Logging | Reimplemented with stdlib `logging` |
| `utils/dedup.py` | Job deduplication | Replaced by `application_id` deterministic hashing |
| `utils/pdf_generator.py` | PDF generation | Stays in JobHunter (owns tailoring) |
| `utils/json_extract.py` | JSON parsing helper | Reimplemented as needed |
| `errors/errors.py` | Typed error categories | Replaced by `core/statuses.py` error enums |

### 2.4 Entry points — adapter invocation candidates

SiemensAutoApplier has multiple `main_*.py` entry points. The Phase 2
`SiemensAdapter` will invoke the apply flow through one of these (the exact
mechanism is decided in Phase 2 after mapping current entry points):

| Path | Purpose | Adapter candidate? |
|---|---|---|
| `main.py` | Default entry | Maybe |
| `main_pipeline.py` | Full pipeline | Maybe |
| `main_discover.py` | Discovery only | No (JobHunter owns discovery) |
| `main_evaluate.py` | Evaluation only | No (JobHunter owns evaluation) |
| `main_tailor.py` | Tailoring only | No (JobHunter owns tailoring) |

### 2.5 Code that must not be rewritten or copied

Per `IMPLEMENTATION_RULES.md` and `ROADMAP.md`:

- All Siemens page objects (`pages/*.py`)
- All Siemens selectors (`locators/selectors.py`)
- The Siemens application stage flow (`workflows/apply_workflow.py`)
- The Siemens eligibility gate (`utils/eligibility_gate.py`)

These stay in SiemensAutoApplier and are accessed only through the
`SiemensAdapter` boundary.

---

## 3. JobHunter (external repo — `MohamedAzzam4/JobHunter`)

This repository owns job discovery, evaluation, ranking, CV/cover-letter
tailoring, and export of ready-to-apply jobs. UniversalAutoApplier consumes
its output through the `application_queue.jsonl` file contract.

### 3.1 Implemented modules

| Path | Responsibility | UniversalAutoApplier relationship |
|---|---|---|
| `scanners/base.py` | Scanner interface | External; not used by UniversalAutoApplier |
| `scanners/bridge.py` | Scanner orchestration | External |
| `scanners/jobspy_scanner.py` | JobSpy scanner | External |
| `scanners/workday.py` | Workday scanner | External |
| `agents/evaluator.py` | Job evaluation | External; output consumed via queue |
| `agents/cv_tailor.py` | CV tailoring | External; output consumed via queue |
| `agents/cover_letter.py` | Cover-letter generation | External; output consumed via queue |
| `agents/smart_router.py` | LLM routing | External |
| `agents/openrouter_client.py` | LLM client | External |
| `agents/google_client.py` | Google API client | External |
| `utils/dedup.py` | Job dedup | External |
| `utils/filters.py` | Job filters | External |
| `utils/jd_fetcher.py` | JD fetcher | External |
| `utils/pdf_generator.py` | PDF generation | External |
| `utils/excel_export.py` | Excel export | External |
| `run_all.py` | Full pipeline entry | External |
| `run_scan.py` | Scan entry | External |
| `run_evaluate.py` | Evaluate entry | External |

### 3.2 Not yet implemented — the queue exporter

**As of this writing, JobHunter does not have an `application_queue.jsonl`
exporter.** The ROADMAP (WP 1.2) calls for adding one in Phase 1. The
exporter will:

- Write `application_queue.jsonl`, one `ApplicationJob` per line.
- Export only jobs that: passed evaluation, have verdict `apply`, are above
  threshold, and have tailored CV + cover letter artifacts.
- Not export rejected, stale, duplicate, or already-applied jobs.
- Be deterministic and idempotent.

UniversalAutoApplier's Phase 1 importer (`application_queue/importer.py`)
will read this file. Until the exporter exists, UniversalAutoApplier's
health endpoint reports `jobhunter_queue: not_configured`.

### 3.3 The file contract boundary

The contract between JobHunter and UniversalAutoApplier is a **file**, not
an API:

```
JobHunter
  -> application_queue.jsonl  (one ApplicationJob per line, JSONL)
  -> UniversalAutoApplier reads it via application_queue/importer.py
```

Each line validates against `ApplicationJob` in `DATA_CONTRACTS.md`. Import
is idempotent. The file can be selected from the dashboard or configured
with `UAA_JOBHUNTER_QUEUE=<absolute path>`.

---

## 4. Adapter boundaries

### 4.1 JobHunter → UniversalAutoApplier (file contract)

```
JobHunter/output/application_queue.jsonl
  (Phase 1 exporter, not yet implemented)
    │
    ▼
UniversalAutoApplier/application_queue/importer.py
  (Phase 1, not yet implemented)
    │
    ▼
UniversalAutoApplier/persistence (application_jobs table)
```

Boundary rules:
- JobHunter writes the file; UniversalAutoApplier reads it.
- UniversalAutoApplier never edits JobHunter files.
- Re-import updates descriptive metadata but never erases attempt history
  or downgrades a final status.
- The contract is the `ApplicationJob` schema in `DATA_CONTRACTS.md`.

### 4.2 UniversalAutoApplier → SiemensAutoApplier (adapter boundary)

```
UniversalAutoApplier/adapters/siemens_adapter.py
  (Phase 2, not yet implemented)
    │
    │  converts ApplicationJob -> Siemens adapter request
    │  calls the narrow Siemens entry point (CLI subprocess or import)
    │  converts Siemens response -> AdapterResult
    ▼
SiemensAutoApplier/workflows/apply_workflow.py
  (existing, must not be rewritten)
```

Boundary rules (from `DEPLOYMENT_AND_REPO_STRATEGY.md`):
- `SiemensAdapter` lives inside UniversalAutoApplier.
- It invokes the existing Siemens workflow through a narrow integration
  boundary (documented CLI subprocess or importable entry point).
- It exchanges a typed request and a structured `AdapterResult`; it does
  **not** parse human log text to determine success.
- Siemens selectors, page objects, and stage logic stay in
  SiemensAutoApplier.
- Shared code moves into a package only after tests prove both callers need
  it; no premature sharing.

### 4.3 UniversalAutoApplier → generic web (future)

```
UniversalAutoApplier/adapters/generic_adapter.py
  (Phase 2+, not yet implemented)
    │
    │  uses navigator/ + form_engine/ + browser/
    ▼
Any ATS website (Greenhouse, Lever, Workday, etc.)
```

Boundary rules:
- Generic adapter **never auto-submits** on unknown sites.
- Review-before-submit is the default and cannot be bypassed for generic.
- Every step records a structured `AdapterResult` and evidence.

---

## 5. Module responsibility matrix

This is the table required by ROADMAP WP 0.1 acceptance criteria.

| Module | Repo | Responsibility | Classification |
|---|---|---|---|
| `ApplyWorkflow` | SiemensAutoApplier | Siemens application orchestration | Siemens-specific; wrapped by adapter |
| `DiscoverWorkflow` | SiemensAutoApplier | Siemens discovery | Siemens-specific; not used by UAA |
| `EvaluateWorkflow` | SiemensAutoApplier | Siemens evaluation | Siemens-specific; not used by UAA |
| `TailorWorkflow` | SiemensAutoApplier | Siemens tailoring | Siemens-specific; not used by UAA |
| `HistoryStore` | SiemensAutoApplier | JSON application history | Replaced by UAA SQLAlchemy persistence |
| `SubmissionGuard` | SiemensAutoApplier | Prevent double-submit | Replaced by UAA status machine |
| `EligibilityGate` | SiemensAutoApplier | Job eligibility filter | Siemens-specific; adapter calls it |
| `evidence.py` | SiemensAutoApplier | Screenshots/traces | Pattern reused in UAA `browser/` |
| `safe_actions.py` | SiemensAutoApplier | Safe clicks | Pattern reused in UAA `navigator/` |
| `telegram.py` | SiemensAutoApplier | Notifications | Not in v1 scope |
| `ui/api.py` | SiemensAutoApplier | Dashboard API | Replaced by UAA `api/` |
| `browser/session.py` | SiemensAutoApplier | Playwright session | Pattern reused in UAA `browser/` |
| Siemens page objects | SiemensAutoApplier | Siemens DOM interaction | **Must not be rewritten** |
| Siemens selectors | SiemensAutoApplier | Siemens CSS/XPath | **Must not be copied** |
| `scanners/*` | JobHunter | Job discovery | External; UAA does not discover |
| `agents/evaluator.py` | JobHunter | Job evaluation | External; UAA does not evaluate |
| `agents/cv_tailor.py` | JobHunter | CV tailoring | External; UAA does not tailor |
| `agents/cover_letter.py` | JobHunter | Cover-letter generation | External; UAA does not generate |
| `application_queue.jsonl` | JobHunter → UAA | Ready-to-apply job handoff | File contract (Phase 1) |
| `core/statuses.py` | UniversalAutoApplier | Finite status enums | Implemented (bootstrap) |
| `core/models.py` | UniversalAutoApplier | Pydantic contracts | Minimal (HealthReport only); Phase 1+ |
| `persistence/` | UniversalAutoApplier | SQLite + SQLAlchemy + Alembic | Implemented (schema only) |
| `api/` | UniversalAutoApplier | FastAPI health + dashboard | Implemented |
| `application_queue/` | UniversalAutoApplier | Queue importer | Planned (Phase 1) |
| `adapters/` | UniversalAutoApplier | Adapter interface + registry + Siemens | Planned (Phase 2) |
| `navigator/` | UniversalAutoApplier | Generic page observation + safe exploration | Planned (Phase 3) |
| `form_engine/` | UniversalAutoApplier | Form extraction + mapping + filling | Planned (Phase 4) |
| `interventions/` | UniversalAutoApplier | Intervention queue + answer memory | Planned (Phase 5) |
| `browser/` | UniversalAutoApplier | Playwright wrapper | Planned (Phase 3+) |
| `ui/` (full dashboard) | UniversalAutoApplier | Queue/Interventions/History/Logs/Settings | Planned (Phase 6) |

---

## 6. Verification

This document is Phase 0 WP 0.1. It changes no runtime code. All path
references were verified against the actual repository trees of:

- `MohamedAzzam4/UniversalAutoApplier` @ `10667fc` (this repo, post-merge)
- `MohamedAzzam4/SiemensAutoApplier` @ `main`
- `MohamedAzzam4/JobHunter` @ `main`

No invented behavior. Planned modules are explicitly marked as planned.
