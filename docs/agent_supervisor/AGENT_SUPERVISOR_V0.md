# Agent Supervisor V0 — Review-Only Pilot

> **Hard boundary:** V0 never submits. An application that reaches
> `review_ready` stops and waits for the human owner. There is no
> `SUBMITTED` supervisor state, no submit tool, and no authorization tool.

## Pipeline

```
JobHunter
   ↓  application_queue.jsonl (one row per ready-to-apply job)
UAA queue importer (existing service)
   ↓  ApplicationJob rows
Supervisor V0 (this workpackage)
   ↓  typed safe tools  →  deterministic UAA services  →  browser (review-only)
   ↓
review packet  →  human handoff / repair ticket / review_ready
```

Concurrency in V0 is **1** — no parallel real browser attempts.

## Architecture Boundary

```
AI Supervisor (planner: Deterministic or Model-backed)
            ↓  SupervisorDecision (typed enum, validated)
      PolicyEngine (deterministic gates around the LLM)
            ↓
   SupervisorTools (typed business-level tools)
            ↓
   Existing UAA deterministic services
            ↓
   Browser — review only, submit interlock always armed
```

The supervisor **never receives**:

- `click(selector)`, `page.goto`, `page.evaluate`, `set_input_files`
- `submit`, `live-submit`, `wq8-authorize`, interlock disable
- generic browser object

All operations go through `supervisor/tools.py:SupervisorTools`:

| Tool | Underlying service |
|------|-------------------|
| `import_queue` | `application_queue/importer.import_queue_file` |
| `list_applications` / `get_job` / `get_application_status` | `job_repository` |
| `prepare_application` / `retry_application` | `submission/execution_service.observe_and_persist_snapshot` (review-only) |
| `sync_interventions_from_snapshot` / `get_interventions` | `interventions/store` |
| `resolve_intervention` | `interventions/resolve_service.resolve_with_persistence` (shared with `POST /api/interventions/{id}/resolve`) |
| `get_review_packet` / `load_review_snapshot` | `submission/store` + `authorization.build_review_plan` |
| `handoff_to_human` / `create_repair_ticket` / `skip_application` | `supervisor/handoff.py`, `supervisor/repair.py`, `job_repository` |

There is **no duplication** of queue import, browser runner, mapper,
snapshot, or intervention storage logic.

## State Machine

`supervisor/models.py:SupervisorState`:

```
imported → preparing → running → waiting_for_intervention → retry_pending → review_ready
                                    ↓
                           needs_human | repair_needed | blocked | skipped | failed
```

- No `SUBMITTED` state exists in V0.
- Unknown planner output fails closed to `REQUEST_HUMAN` — no mutation.
- Siemens (`platform == siemens` or `detect_platform(url) == siemens`) is
  **always skipped before preparation** with `reason_code = dedicated_siemens_workflow`
  (dedicated workflow). Proven in `tests/unit/test_supervisor_v0.py:test_i_*`.

Bounded loop (`supervisor/models.py:SupervisorLimits`):

- `max_application_attempts = 2`
- `max_intervention_resolutions = 3`
- `max_same_failure_retries = 2`
- CAPTCHA stops immediately (Class D, no retry).
- Human-required condition stops that application.
- Repair-needed condition creates ticket and stops.
- Repeated identical failure terminates.

## Knowledge Levels & Provenance

Three knowledge levels plus provenance (`supervisor/models.py:AnswerSource`):

1. **Candidate facts** — stable factual profile data (`candidate_profile` keys).
   Only key *names* are passed to the planner for the Class-C mapping-defect
   check; values are never read or stored in supervisor rows.
2. **Owner policies** — reusable owner-approved decisions loaded from a local
   JSON file (`supervisor/policy.py:load_owner_policies`). Matched by exact
   `normalize_question` against the field label/question.
3. **Job-specific answers** — per-job `metadata.form_answers` supplied by the
   owner for this one application (via intervention resolve).
4. **AnswerMemory** — exact `normalize_question` match of a previously
   `user_confirmed` answer.

All answers carry `source`, `confidence`, and `reason`. `MODEL_INFERENCE`
is restricted to low-risk `field_answer` questions only and is vetoed for
any high-risk category.

## Policy Gates

`supervisor/policy.py:PolicyEngine`:

- **Class A — automatically resolvable** — exact trusted source exists
  (owner policy → answer memory). Returns `auto_resolvable = true`.
- **Class B — human required** — high-risk categories: CAPTCHA, login, 2FA,
  identity verification, unknown salary, unknown legal declaration, unknown
  disability declaration, work authorization uncertainty, unknown personal
  facts, sensitive consent. Always hands off; never fabricated.
- **Class C — likely software defect** — candidate fact key tokens overlap
  the field label but mapper reports `intervention_needed` → `REPAIR_NEEDED`.
- **Class D — hard blocker** — `captcha`, `login_required`, `unknown_page`
  → stop that application, no retry.

High-risk keyword heuristics (`_HIGH_RISK_KEYWORDS`) plus explicit
`category` from the field classifier provide conservative fallback — when in
doubt, the question is treated as high risk.

`validate_decision` re-validates every planner decision against these gates;
`MODEL_INFERENCE` for high-risk or non-field answers is vetoed.

## Planner Abstraction

`supervisor/planner.py`:

- `SupervisorPlanner` protocol: `decide(context: PlannerContext) -> SupervisorDecision`.
- `DeterministicPlanner` — policy-driven, no LLM; used for tests and as
  conservative default. Safety-first order: D → B → C → A → retry/stop.
- `OpenAICompatiblePlanner` — provider-neutral OpenAI-compatible
  `chat/completions` endpoint, configured only from
  `UAA_SUPERVISOR_MODEL_*` env vars. Any transport error, malformed JSON,
  or schema-invalid decision fails closed to `REQUEST_HUMAN`. Raw model-
  generated tool names are never executed; the action is a validated enum.

Core tests **never call a real external LLM**.

## Intervention Resolution Semantics (Phase-0 Correction)

Shared implementation: `interventions/resolve_service.py`.

| Input | `save_to_memory` | Per-job `metadata.form_answers` | Global `AnswerMemory` |
|-------|------------------|-------------------------------|----------------------|
| scalar | `false` | persists | — |
| scalar | `true` | persists | persists (reusable) |
| file bundle | `false` | persists (complete bundle) | — |
| file bundle | `true` | persists (complete bundle) | **no** lossy first-path entry |
| skip/block | — | **never** persists supplied answer | — |

Both `POST /api/interventions/{id}/resolve` and
`SupervisorTools.resolve_intervention` call the same `resolve_with_persistence`.

## Human Handoffs & Repair Tickets

Sanitized by design (`supervisor/handoff.py`, `supervisor/repair.py`,
`persistence/models.py:HumanHandoffRow`, `RepairTicketRow`):

- Handoffs carry `company`, `role`, `reason_code`, `question`,
  `action_required`, and redacted `detail_json` (`value_redacted: true`).
  Sufficient for CAPTCHA, unknown question, login/2FA, owner decision.
- Repair tickets carry `ats_family`, `page_fingerprint`, `field_label`,
  `field_type`, `reason_code`, `expected_source`, `actual_failure`,
  `selector_metadata_json`, `retry_history_json`, `suggested_reproduction`.
  Sufficient for mapper defect, unsupported widget, executor defect, ATS changed.

**Never persisted** in supervisor rows: passwords, cookies, session tokens,
CV contents, raw owner profile, or unnecessary answer values.

## JobHunter Queue Bridge

Supervisor consumes the existing `application_queue.jsonl` through UAA's
existing `import_queue_file` — no JobHunter modification needed.
Only PII-sanitized counts are surfaced (`total_lines`, `imported`,
`skipped`, structured `row_errors`).

## CLI / Status Interface

All commands follow `src/universal_auto_applier/cli.py`:

```text
python -m universal_auto_applier supervisor-run --queue <path> --review-only
# --queue optional; --review-only is default (V0 never submits).
# --owner-policy <path> loads reusable owner policies (JSON).
# --application-id <id> limits to specific jobs (repeatable).

python -m universal_auto_applier supervisor-status [--run-id <id>]
python -m universal_auto_applier supervisor-handoffs [--run-id <id>] [--status open]
python -m universal_auto_applier supervisor-tickets [--run-id <id>] [--status open]
python -m universal_auto_applier supervisor-review-ready [--run-id <id>]
```

- `supervisor-run` prints JSON summary plus human-readable counts.
- `supervisor-status` shows the latest (or specified) run, its summary,
  per-application states, and recent audit events.
- `supervisor-handoffs`, `supervisor-tickets`, `supervisor-review-ready`
  provide the same filtered views as the dashboard will in a future workpackage.

No unnecessary CLI proliferation; all commands follow the existing
`CLI_COMMANDS` / `run_command` pattern.

## Persistence & Migration

Migration `migrations/versions/0016_supervisor.py` (revises `0015`):

- `supervisor_runs` (run_id, status, queue_path, review_only, summary_json, ...)
- `supervisor_application_states` (application_id PK, run_id, state, reason_code, ...)
- `supervisor_events` (event_id, run_id, application_id, action, previous/resulting state, ...)
- `human_handoffs` (handoff_id, run_id, application_id, company, role, reason_code, ...)
- `repair_tickets` (ticket_id, run_id, application_id, ats_family, field_label, ...)

IDs are random hex; timestamps are UTC-aware; JSON fields carry redacted
metadata only. Deterministic upgrade/downgrade, proper indexes, no PII-heavy
columns. `tests/contract/test_migrations.py:CURRENT_HEAD = "0016_supervisor"`.

## Cookie / CMP Handling (Pilot Defect Closure)

Pilot `https://jobs.msg.group/de/jobs/411/form` with 25 underlying fields was **not interaction-ready**: full-page Usercentrics `Privatsphäre-Einstellungen` overlay (buttons `Nur technisch notwendige Cookies akzeptieren` / `Alle akzeptieren`) blocked the UI, but `analyze_page` still saw the form. New deterministic component `src/universal_auto_applier/browser/consent_banner.py` distinguishes `FORM_DISCOVERED` vs `FORM_INTERACTION_READY`.

- **Component**: `ConsentBannerHandler` (`handle_consent_banner`) in deterministic browser layer, **not** an LLM tool. Flow: navigate → detect CMP → resolve per policy → verify dismissed → only then analyze/fill.
- **Policy** `UAA_COOKIE_CONSENT_POLICY` (Settings) default `necessary_only` (other values `accept_all`, `human`). Under `necessary_only`, Usercentrics correctly clicks `Nur technisch notwendige Cookies akzeptieren`, never `Alle akzeptieren`.
- **Application consent vs cookie consent**: Handler requires CMP context (known selectors `usercentrics`/`onetrust`/`cookiebot` or visible `role=dialog`/`overlay` containing cookie semantics `cookie`/`privatsphäre`/`privacy settings`). Form fields `Einwilligung in die Speicherung...` without CMP are never auto-clicked.
- **Fail closed**: Unknown CMP with no suitable necessary_only button → `blocked`, no retry loop, `COOKIE_CONSENT_BLOCKED` handoff, no form filling.
- **Reason codes** `COOKIE_CONSENT_BLOCKED` / `COOKIE_CONSENT_RESOLVED` (ReasonCode), audit `cmp=Usercentrics policy=necessary_only action=necessary_only result=resolved clicked=true` (sanitized, no cookie values).
- **Integration**: `browser/live_runner.py` (both `run_in_context` and `run_in_context_synthetic`) preflights at top of each navigation step (A/B/C), `submission/execution_service.py` preflights after initial `page.goto` and after form navigation before `execute_live_form`. Already-cleared banners are idempotent (`absent`).
- **Tests**: fixtures `tests/fixtures/consent/{usercentrics,generic,unknown_cmp,application_consent}.html`, `tests/playwright/test_consent_banner.py` 7 tests (Usercentrics, generic, unknown blocked, application consent not clicked, pilot regression, unresolved blocks, never clicks `dangerous_submit`), `test_u_cookie_consent_blocked_handoff` in supervisor, full non-live gate 1430 passed.

## Safety Audit

- No raw browser escape hatch in `supervisor/` (proven in `test_n_*`).
- `UAA_ENABLE_REAL_SUBMISSION` default remains `false`.
- Existing submit interlock unchanged; WQ-8 one-real-submit budget untouched.
- `application_id` semantics unchanged; `job.url` semantics unchanged.
- Supervisor V0 persistence changes narrowly; no unrelated WQ-8 modifications.
- CMP handler never clicks `dangerous_submit` or application `Einwilligung`; verified in `test_handler_never_clicks_dangerous_submit` and `test_application_consent_not_clicked`.

## Future Integration

- **Coding-agent repair** — `RepairTicket` is the structured input for a
  future coding agent that fixes mapper/executor defects without touching
  live runs.
- **Controlled submit** — a future workpackage may add a tightly scoped,
  owner-approved `UAA_ENABLE_REAL_SUBMISSION`-gated submit path reusing the
  existing `SubmissionCoordinator`. V0 proves the review-only boundary holds
  before any such integration.

## Tests

`tests/unit/test_supervisor_v0.py` (hermetic, synthetic, no ATS traffic, 28 tests):

A. `prepare → review_ready` · B. known candidate fact `resolve → retry → review_ready`
· C. owner policy trusted answer · D. unknown salary → `NEEDS_HUMAN` · E. CAPTCHA
→ immediate human/block, no retry · F. login/2FA → human · G. mapper defect
→ `RepairTicket` · H. repeated identical failure → bounded termination · I.
Siemens → `SKIPPED` (preparation never starts) · J. CV + transcript bundle
→ complete per-job bundle survives retry · K. `save_to_memory=false`
→ per-job persists, global memory untouched · L. invalid planner/model output
→ fail closed, no mutation · M. attempted `SUBMIT` decision → rejected by
schema/policy, no submission tool · N. raw browser click attempt → impossible
through tool registry · O. aggregated handoff/run summary · P. run scope
isolation · Q. Siemens mixed queue · R. policy audit 8 personal facts ·
S. CLI review-only · T. no dynamic dispatch · U. cookie `COOKIE_CONSENT_BLOCKED` handoff.

`tests/playwright/test_consent_banner.py` (7 tests, hermetic fixtures):
Usercentrics `necessary_only`, generic OneTrust, unknown CMP blocked, application consent not clicked, pilot regression old vs new, unresolved blocks, never clicks `dangerous_submit`.

Full non-live gate `pytest tests/unit tests/contract tests/integration -m "not playwright and not live"` → **1430 passed, 4 deselected**.

## Diagrams

For the current system map and backlog, see:

- `docs/CURRENT_STATE.md` — authoritative snapshot
- `docs/NEXT_WORKPACKAGES.md` — ordered backlog
- `docs/generalization/ROADMAP.md` — phase plan (phases 0-8)
