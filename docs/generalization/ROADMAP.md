# Roadmap: Generalized Job Application System

This roadmap is written for an implementation agent. Follow the phases in
order. Do not skip acceptance criteria. Do not change working Siemens behavior
unless the phase explicitly requires it.

All generalized production implementation in this roadmap belongs in a new
`UniversalAutoApplier` repository. The planning documents currently live in
the Siemens repository only as the bootstrap source. Read
`DEPLOYMENT_AND_REPO_STRATEGY.md` before implementing any phase.

## Target Architecture

```text
JobHunter
  scanners/
  agents/
  output/
  application_queue.jsonl

UniversalAutoApplier
  core/
  application_queue/
  adapters/
  navigator/
  form_engine/
  interventions/
  ui/
  tests/

Existing SiemensAutoApplier repository
  workflows/
  pages/
  history/
  ui/
  exposed through a narrow integration entry point

Deployment v1
  all required services and Playwright run on the user's local machine
  dashboard binds to 127.0.0.1 by default
```

## Repository Bootstrap Gate

Complete this gate before Phase 0:

1. Create the new sibling repository `UniversalAutoApplier`.
2. Copy this documentation pack into `UniversalAutoApplier/docs/generalization`.
3. Create the package skeleton, local scripts, and quality configuration exactly
   as specified in `TECHNICAL_BASELINE.md`.
4. Initialize Git and commit only the skeleton, documentation, and lock/config
   files.
5. Run the technical verification gate in `TECHNICAL_BASELINE.md`.

Do not implement queue, adapter, or browser behavior during bootstrap.

## Phase 0: Architecture Baseline

Purpose:
Create the foundation for safe implementation. This phase should not change
runtime behavior.

### Workpackage 0.1: Current System Map

Tasks:

- Document current Siemens modules and responsibilities.
- Document current JobHunter outputs.
- Identify existing reusable components:
  - `HistoryStore`
  - `ApplyWorkflow`
  - `TailorWorkflow`
  - `EvaluateWorkflow`
  - dashboard API
  - Telegram notifier
  - evidence/screenshot helpers
  - submission guard
- Identify code that must not be rewritten:
  - Siemens page objects
  - Siemens application stage flow
  - current eligibility gate

Deliverables:

- `docs/generalization/CURRENT_SYSTEM_MAP.md`
- A table of modules, responsibilities, and whether they are reusable,
  Siemens-specific, or deprecated.

Acceptance criteria:

- No production behavior changes.
- No file outside docs changed.
- Reviewer can understand where discovery, evaluation, tailoring, apply, and UI
  currently live.

### Workpackage 0.2: Shared Architecture Decision Record

Tasks:

- Create `docs/generalization/ADR_001_ARCHITECTURE.md`.
- State that JobHunter remains responsible for search, evaluation, and document
  generation.
- State that UniversalAutoApplier is responsible for applying.
- State that SiemensAutoApplier becomes the first adapter.
- State that generic unknown-site automation must use review-before-submit.
- State that UniversalAutoApplier is a new sibling repository.
- State that version 1 is local-first and requires no cloud service.

Acceptance criteria:

- ADR includes decision, context, alternatives considered, and consequences.
- ADR explicitly rejects "rewrite Siemens as generic code" as the first step.
- ADR names repository ownership and the initial Siemens invocation boundary.

## Phase 1: Shared Data Contract and Queue

Purpose:
Create a stable handoff from JobHunter into the applier.

### Workpackage 1.1: `ApplicationJob` Schema

Tasks:

- Implement a typed schema for `ApplicationJob`.
- Implement it as a Pydantic version 2 model.
- Store it at:

```text
src/universal_auto_applier/core/models.py
```

Required fields:

- `application_id`
- `platform`
- `source`
- `company`
- `title`
- `url`
- `location`
- `job_description`
- `score`
- `verdict`
- `cv_pdf`
- `cover_letter_pdf`
- `status`

Recommended optional fields:

- `job_id`
- `external_job_id`
- `date_posted`
- `tailored_at`
- `evaluation_reason`
- `german_filter_result`
- `metadata`

Acceptance criteria:

- Invalid URLs fail validation.
- Missing document paths are allowed only when status is not `ready_to_apply`.
- `application_id` is deterministic for the same URL.
- Unit tests cover valid, invalid, and missing-field cases.

### Workpackage 1.2: JobHunter Queue Export

Tasks:

- In JobHunter, add an exporter that writes `application_queue.jsonl`.
- Export only jobs that:
  - passed evaluation
  - have verdict `apply`
  - are above threshold
  - have tailored CV and cover letter artifacts, if available
- Do not export rejected, stale, duplicate, or already-applied jobs.

Output format:

```json
{"application_id":"...","platform":"unknown","source":"linkedin","company":"...","title":"...","url":"...","score":4.2,"verdict":"apply","cv_pdf":"...","cover_letter_pdf":"...","status":"ready_to_apply"}
```

Acceptance criteria:

- Export is deterministic.
- Re-running export does not duplicate jobs.
- Contract tests validate exported JSONL against `ApplicationJob`.

### Workpackage 1.3: Universal Applier Queue Import

Tasks:

- Add importer in the new UniversalAutoApplier repository:

```text
application_queue/importer.py
```

- Read `application_queue.jsonl`.
- Validate each row.
- Insert or update jobs in history through store methods only.
- Do not directly mutate JSON from arbitrary modules.

Acceptance criteria:

- Importing the same queue twice is idempotent.
- Imported jobs appear in dashboard history.
- Imported jobs retain document paths.
- No Siemens job ID is required for non-Siemens jobs.

## Phase 2: Adapter Architecture

Purpose:
Introduce a stable interface for platform-specific apply behavior.

### Workpackage 2.1: Adapter Interface

Tasks:

- Add an adapter interface:

```python
class ApplicationAdapter:
    platform = "unknown"

    def can_handle(self, job: ApplicationJob) -> bool:
        ...

    def prepare(self, job: ApplicationJob) -> AdapterResult:
        ...

    def navigate_to_form(self, job: ApplicationJob) -> AdapterResult:
        ...

    def fill(self, job: ApplicationJob) -> AdapterResult:
        ...

    def submit_or_pause(self, job: ApplicationJob) -> AdapterResult:
        ...
```

- Add `AdapterResult` with:
  - `status`
  - `message`
  - `phase`
  - `screenshots`
  - `errors`
  - `next_action`

Acceptance criteria:

- Adapter interface has unit tests using a fake adapter.
- Result statuses are finite and documented.

### Workpackage 2.2: Adapter Registry

Tasks:

- Add `AdapterRegistry`.
- Register adapters in a deterministic order and select the first known adapter
  that returns `can_handle(job) == True`.
- Fail startup if two known adapters have the same routing priority and both
  claim the same platform fixture.
- Fall back to `GenericAdapter` only when no known adapter matches.

Acceptance criteria:

- Siemens URLs route to Siemens adapter.
- Greenhouse/Lever/Workday URLs can be detected as future placeholders.
- Unknown URLs route to generic fallback.

### Workpackage 2.3: Siemens Adapter Wrapper

Tasks:

- Create `adapters/siemens_adapter.py`.
- Integrate with the existing Siemens `ApplyWorkflow` through the narrow entry
  point selected in Phase 0.
- Do not rewrite existing Siemens page objects.
- Convert generic `ApplicationJob` into a typed Siemens adapter request without
  directly mutating Siemens history files.
- Convert the Siemens workflow response into `AdapterResult`; do not determine
  success by parsing human-readable logs.
- Keep existing `ApplyWorkflow` usable from the old CLI.

Acceptance criteria:

- Current Siemens pipeline still works.
- Adapter-based Siemens dry-run works for a fixture or test job.
- Existing Siemens regression tests still pass.
- No Siemens selectors or stage logic are copied into UniversalAutoApplier.

## Phase 3: Generic Navigation Layer

Purpose:
Reach the application form without writing a custom full bot per company.

### Workpackage 3.1: Page Observer

Tasks:

- Implement `PageObserver`.
- Extract from DOM/accessibility tree, not from screenshot alone:
  - URL
  - title
  - visible inputs
  - visible buttons/links
  - forms
  - file inputs
  - login indicators
  - captcha indicators
  - review/submit indicators
- Save screenshot as evidence, but use DOM data for automation.

Acceptance criteria:

- Unit tests on saved HTML fixtures.
- Observer returns stable output for common pages.
- Hidden and disabled elements are not treated as safe actions.

### Workpackage 3.2: Clickable Classifier

Tasks:

- Classify clickables as:
  - `safe_apply`
  - `safe_continue`
  - `safe_upload`
  - `dangerous_submit`
  - `login`
  - `unknown`

Safe terms:

```text
apply
apply now
start application
bewerben
jetzt bewerben
next
continue
save and continue
weiter
fortfahren
```

Dangerous terms:

```text
submit
submit application
send application
complete application
finish
bewerbung absenden
absenden
```

Acceptance criteria:

- Classifier never marks dangerous submit as safe.
- Tests cover English and German labels.
- Unknown text remains unknown, not safe.

### Workpackage 3.3: Safe Exploration Loop

Tasks:

- Implement loop:

```text
observe -> classify -> choose safe action -> click -> observe again
```

- Stop when:
  - form is visible
  - login is required and no credentials are configured
  - captcha is detected
  - final submit is detected
  - page is unknown
  - max step count is reached

Acceptance criteria:

- Unknown pages do not cause random clicks.
- Final submit is never clicked in generic dry-run.
- Every step is logged with URL, action, and screenshot path.

## Phase 4: Generic Form Filler

Purpose:
Fill visible application forms using structured profile data and tailored
documents.

### Workpackage 4.1: Form Schema Extractor

Tasks:

- Extract fields:
  - selector
  - label
  - type
  - required
  - options
  - current value
  - nearby text
  - confidence

Supported controls:

- text
- email
- phone
- textarea
- select
- radio
- checkbox
- file upload
- date

Acceptance criteria:

- Fixture tests cover all supported controls.
- Labels can be found from `label for`, `aria-label`, placeholder, name/id, and
  nearby text.

### Workpackage 4.2: Field Mapper

Tasks:

- Deterministic rules first.
- AI only for ambiguous fields.
- Every mapping returns:
  - value
  - source
  - confidence
  - explanation

Examples:

```text
first name -> candidate.first_name
email -> candidate.email
resume -> job.cv_pdf
cover letter -> job.cover_letter_pdf
sponsorship -> candidate.work_authorization.requires_sponsorship
```

Acceptance criteria:

- Low-confidence mappings create interventions.
- AI responses cannot directly write to page without validation.

### Workpackage 4.3: Fill Engine

Tasks:

- Fill fields by control type.
- After filling, detect validation errors.
- Save evidence before and after filling.

Acceptance criteria:

- Fixture form can be filled end to end.
- Required fields are reported when missing.
- File upload paths are validated before upload.

## Phase 5: Human Review and Answer Memory

Purpose:
Make generic automation practical and safe.

### Workpackage 5.1: Intervention Queue

Tasks:

- Add intervention records for questions requiring user action.
- Record:
  - job
  - page URL
  - field label
  - options
  - suggested answer
  - confidence
  - screenshot
  - status

Acceptance criteria:

- Dashboard shows pending interventions.
- User can approve, edit, skip, or mark blocked.

### Workpackage 5.2: Answer Memory

Tasks:

- Store confirmed answers by normalized question pattern.
- Use memory only after exact or high-confidence semantic match.
- Track source:
  - user confirmed
  - profile derived
  - adapter default

Acceptance criteria:

- Same question is auto-filled after user confirmation.
- User can edit or delete stored answers.

### Workpackage 5.3: Review Before Submit

Tasks:

- Add a review state before final submission.
- Show:
  - job
  - documents uploaded
  - filled fields summary
  - unanswered questions
  - screenshots
  - final action detected

Acceptance criteria:

- Generic adapter never submits without explicit approval.
- Trusted adapters can submit only when config allows it.

## Phase 6: UI/UX Dashboard

Purpose:
The user starts and monitors the system from UI.

### Workpackage 6.1: Dashboard Status

Tasks:

- Show current phase:

```text
Search -> Evaluate -> Tailor -> Queue -> Navigate -> Fill -> Review -> Submit
```

- Show active job, last action, last error, and run mode.

Acceptance criteria:

- Status updates while pipeline runs.
- User can tell whether the system is idle, running, waiting for input, failed,
  or done.

### Workpackage 6.2: Queue and History

Tasks:

- Add queue view:
  - ready to apply
  - needs user input
  - in progress
  - review ready
  - needs review
  - applied
  - failed
  - skipped

- Add history view:
  - searchable
  - filter by status/platform/company/score
  - open job detail

Acceptance criteria:

- Imported JobHunter jobs appear.
- Siemens jobs still appear.
- Score `0.0` renders correctly.
- `needs_review` clearly warns that submission state is uncertain and blocks
  automatic resubmission.

### Workpackage 6.3: Intervention UI

Tasks:

- Show pending interventions.
- Allow approve/edit/skip.
- Show field context and screenshot.
- Save answer memory when user approves.

Acceptance criteria:

- User can unblock a paused application from the UI.
- All changes are written through API/store methods.

### Workpackage 6.4: Logs and Error Observer

Tasks:

- Show latest logs.
- Show structured errors.
- Link screenshots/evidence.
- Add retry controls for safe phases.

Acceptance criteria:

- Errors are visible without reading terminal logs.
- Retry does not duplicate submitted applications.

## Phase 7: ATS Platform Adapters

Purpose:
Implement adapters by platform, not by company.

Recommended order:

1. Siemens adapter, already wrapped.
2. Greenhouse.
3. Lever.
4. Workday.
5. SmartRecruiters.
6. LinkedIn Easy Apply.
7. Generic fallback improvements.

Each adapter must include:

- platform detection
- navigation behavior
- login behavior
- form handling strategy
- submit safety rules
- fixture tests
- Playwright dry-run test
- documentation

Acceptance criteria:

- Adapter fails safely when page layout changes.
- Adapter produces structured history and evidence.
- Adapter does not bypass review-before-submit unless trusted.

## Phase 8: Full Pipeline Orchestration

Purpose:
Run everything from the dashboard or CLI.

Target flow:

```text
JobHunter export
  -> queue import
  -> adapter route
  -> navigate
  -> fill
  -> intervention or review
  -> submit if approved
  -> history update
```

Acceptance criteria:

- Full dry-run pipeline test passes with fixtures.
- Full Siemens regression still passes.
- Dashboard shows every phase transition.
- Failed jobs can be retried without duplicating successful submissions.

## Required Regression Gate After Every Phase

After every phase:

1. Run all old tests.
2. Run all new tests.
3. Run the full dry-run pipeline fixture test.
4. Run UI smoke tests if UI was touched.
5. Confirm no unrelated files changed.
6. Write a short implementation report.

No phase is complete until the regression gate passes.
