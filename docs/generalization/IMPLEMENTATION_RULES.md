# Implementation Rules and Project Fingerprint

This file defines the coding fingerprint for the generalized application
system. Every AI or human implementer must follow it.

Repository ownership and local deployment requirements are defined in
`DEPLOYMENT_AND_REPO_STRATEGY.md`. Generalized production code belongs in the
new `UniversalAutoApplier` repository. This planning pack being stored in the
Siemens repository does not authorize implementation here.

The technology and package choices in `TECHNICAL_BASELINE.md` are fixed for
version 1 unless an approved architecture decision record changes them.

## Design Principles

1. Preserve what works.
   - Siemens-specific behavior remains in Siemens-specific modules.
   - Generalization happens by adding layers around the existing workflow.

2. Prefer explicit state over hidden side effects.
   - Every workflow phase writes a structured result.
   - Every status transition goes through a store method.

3. Deterministic code first, AI second.
   - Use rules for obvious field mappings and button classification.
   - Use AI only for ambiguity.
   - AI output must be validated before it affects the browser.

4. Safe by default.
   - Dry-run and review-before-submit are default.
   - Unknown platforms never auto-submit.
   - Final submit requires explicit approval.

5. Small modules with clear ownership.
   - Avoid giant workflow files.
   - Avoid platform-specific logic leaking into generic modules.

## Project Fingerprint

The codebase should feel like one system. Use these conventions everywhere.

### Naming

Use these names consistently:

```text
ApplicationJob
ApplicationAttempt
ApplicationAdapter
AdapterRegistry
AdapterResult
PageObservation
Clickable
FormField
FieldMapping
Intervention
AnswerMemory
```

Use verb names for actions:

```text
observe_page
classify_clickables
extract_form_schema
map_fields
fill_form
create_intervention
record_attempt
mark_review_ready
mark_applied
```

Do not invent synonyms like:

```text
job_payload
apply_item
site_worker
browser_brain
question_task
```

### Module Boundaries

Package layout under `src/universal_auto_applier/`:

```text
core/
  models.py
  statuses.py
  result.py

application_queue/
  importer.py
  exporter.py

adapters/
  base.py
  registry.py
  siemens_adapter.py
  generic_adapter.py
  greenhouse_adapter.py
  lever_adapter.py

navigator/
  page_observer.py
  clickable_classifier.py
  safe_explorer.py

form_engine/
  schema_extractor.py
  field_mapper.py
  fill_engine.py
  validators.py

interventions/
  store.py
  answer_memory.py

history/
  history_store.py

ui/
  api.py
  static/
```

Rules:

- `adapters/*` may know platform details.
- `navigator/*` must not know candidate profile details.
- `form_engine/*` must not know platform-specific URLs.
- `core/*` must not import Playwright.
- `history/*` owns persistent job state.
- UI routes call service/store APIs; they do not mutate JSON directly.

### Result Objects

Every workflow method that can fail returns or records a structured result.

Bad:

```python
def fill_form(job):
    page.click("button")
```

Good:

```python
def fill_form(job: ApplicationJob) -> AdapterResult:
    try:
        ...
        return AdapterResult.success(phase="fill", message="Filled 12 fields")
    except Exception as exc:
        return AdapterResult.failed(phase="fill", message=str(exc))
```

### Store Access

Rules:

- Do not mutate `history._data` outside store classes.
- Do not open and rewrite history JSON from random modules.
- Add store methods for every new state transition.
- Store methods should be idempotent where practical.

Required store methods:

```text
upsert_application_job
record_attempt_started
record_phase_result
record_intervention
resolve_intervention
mark_review_ready
mark_applied
mark_failed
mark_skipped
mark_blocked
```

### Logging Style

Every important action logs:

```text
[application_id] phase action result
```

Examples:

```text
[abc123] navigate observed 5 clickables on job_page
[abc123] navigate clicked safe_apply: Apply now
[abc123] fill mapped email -> candidate.email confidence=0.99
[abc123] review final submit detected; waiting for approval
```

Rules:

- Logs must not contain passwords, tokens, or API keys.
- Logs must include URL only when useful and not secret-bearing.
- Errors should include next action when possible.

### Error Handling

Use typed error categories:

```text
navigation_error
login_required
captcha_detected
form_extraction_error
field_mapping_error
validation_error
submission_blocked
platform_changed
unknown_page
```

Do not swallow exceptions silently.

Every caught exception must produce:

- log entry
- structured result
- history attempt update
- screenshot when browser context exists

### Browser Automation Rules

1. Prefer Playwright locators over raw coordinates.
2. Do not click invisible or disabled controls.
3. Do not click final submit in generic mode.
4. Use screenshots for evidence, not primary control discovery.
5. Use DOM/accessibility tree to extract controls.
6. Wait for stable page state after navigation.
7. Cap exploration with `max_steps`.
8. Detect login, captcha, expired jobs, and already-applied states.

### AI Usage Rules

AI may:

- classify ambiguous field intent
- suggest answers to custom questions
- summarize page state for human review
- help map nonstandard labels to profile fields

AI may not:

- click browser controls directly
- submit applications directly
- invent candidate facts
- overwrite verified candidate profile data
- change dates, employers, degrees, or document paths

AI output must include:

```text
answer
confidence
source_fields_used
reason
requires_confirmation
```

### Safety Rules for Submit

Dangerous controls include labels containing:

```text
submit
submit application
send application
finish
complete application
bewerbung absenden
absenden
```

Rules:

- Generic adapter must stop at dangerous controls.
- Known adapters may submit only if:
  - `auto_submit_enabled` is true
  - adapter is marked trusted
  - job passed eligibility gate
  - no unresolved interventions exist
  - review evidence was captured

### UI/API Rules

- UI should show status, not hide state in logs.
- API responses should use stable JSON shapes.
- API should expose phase status, queue, history, interventions, and errors.
- API should never trigger submit without explicit user action or config.
- Retry endpoints must be idempotent or clearly scoped to a new attempt.

### Documentation Rules

Every new adapter must include:

- supported URL patterns
- login behavior
- navigation behavior
- form handling behavior
- submit behavior
- known limitations
- test fixtures

Every phase implementation report must include:

- changed files
- behavior added
- tests run
- regression results
- screenshots if UI changed
- known risks

## Code Review Checklist

Before accepting a change, verify:

- Existing Siemens flow still works.
- No platform-specific code leaked into generic modules.
- New state transitions go through store APIs.
- Tests cover success and failure paths.
- Generic mode cannot click final submit.
- UI shows new waiting/error states.
- No unrelated files are committed.
- No generated screenshots, PDFs, local history, or `.env` changes are committed
  unless explicitly requested.
