# Data Contracts

This file defines the shared data structures for the generalized application
system. Implementers must not invent incompatible fields or bypass these
contracts.

## Contract Rules

1. All queue and history mutations go through store APIs.
2. JSON files are external persistence, not internal business logic.
3. Public contracts must have validation tests.
4. Unknown fields may be preserved under `metadata`.
5. Required fields must not be silently guessed.
6. Status transitions must be explicit.

## `ApplicationJob`

`ApplicationJob` is the normalized handoff from JobHunter to the applier.

Required fields:

```json
{
  "application_id": "sha256-url-or-stable-source-id",
  "platform": "unknown",
  "source": "linkedin",
  "company": "Example GmbH",
  "title": "Working Student AI",
  "url": "https://example.com/jobs/123",
  "location": "Munich, Germany",
  "job_description": "Full job description text",
  "score": 4.1,
  "verdict": "apply",
  "cv_pdf": "C:/JobHunter/output/example-working-student-cv.pdf",
  "cover_letter_pdf": "C:/JobHunter/output/example-working-student-cover.pdf",
  "status": "ready_to_apply"
}
```

Optional fields with their fixed names:

```json
{
  "job_id": "platform-specific-id-if-known",
  "external_job_id": "id-from-source",
  "date_posted": "2026-07-10",
  "evaluated_at": "2026-07-10T12:00:00",
  "tailored_at": "2026-07-10T12:10:00",
  "evaluation_reason": "Score 4.1 >= threshold",
  "german_filter_result": "passed",
  "documents": {
    "cv_md": "C:/JobHunter/output/example-cv.md",
    "cover_letter_md": "C:/JobHunter/output/example-cover.md"
  },
  "metadata": {
    "original_source_row": 17
  }
}
```

Validation rules:

- `application_id` is `sha256(identity_source).hexdigest()` in lowercase. If
  both `platform` and `external_job_id` exist, `identity_source` is
  `platform + ":" + external_job_id.strip()`. Otherwise, `identity_source` is
  the canonical URL.
- Canonical URL construction lowercases scheme and host, removes the fragment
  and default port, removes a trailing slash except at the host root, removes
  query keys beginning with `utm_`, and removes these case-insensitive query
  keys: `gclid`, `fbclid`, `mc_cid`, `mc_eid`, `ref`, `refid`, and
  `trackingid`. It preserves all other query keys and values and sorts them by
  key and then value. JobHunter and UniversalAutoApplier must share golden
  contract cases for this algorithm.
- `url` must be an HTTP or HTTPS URL.
- `score` must be numeric.
- `verdict` must be one of:
  - `apply`
  - `consider`
  - `skip`
- `status` must be one of the statuses documented below.
- `cv_pdf` and `cover_letter_pdf` must exist before status becomes
  `ready_to_apply`, unless the platform allows no-document applications.
- Artifact paths in exported queue rows must be absolute local paths. Import
  normalizes path separators for the host OS, verifies existence for
  `ready_to_apply`, and stores the resolved path. Relative paths are rejected
  with a row-specific contract error.
- Date-times use ISO 8601 UTC with a `Z` suffix. Date-only fields use
  `YYYY-MM-DD`.

## Platform Values

Use these exact platform values:

```text
siemens
greenhouse
lever
workday
smartrecruiters
linkedin_easy_apply
generic
unknown
```

Platform detection should be deterministic where possible:

```text
jobs.siemens.com -> siemens
greenhouse.io, boards.greenhouse.io -> greenhouse
jobs.lever.co -> lever
myworkdayjobs.com -> workday
smartrecruiters.com -> smartrecruiters
linkedin.com/jobs -> linkedin_easy_apply or unknown, depending on flow
```

## Application Status Lifecycle

Allowed statuses:

```text
discovered
evaluated
rejected
tailored
ready_to_apply
queued
in_progress
needs_user_input
review_ready
submitted
needs_review
applied
failed
skipped
closed
blocked
```

Expected transitions:

```text
discovered -> evaluated
evaluated -> rejected
evaluated -> tailored
tailored -> ready_to_apply
ready_to_apply -> queued
queued -> in_progress
in_progress -> needs_user_input
needs_user_input -> in_progress
in_progress -> review_ready
review_ready -> submitted
submitted -> applied
submitted -> needs_review
in_progress -> failed
failed -> queued
blocked -> queued
needs_review -> queued
queued -> skipped
any pre-submit non-terminal -> closed
any pre-submit non-terminal -> blocked
```

Terminal statuses:

```text
applied
rejected
skipped
closed
```

Rules:

- Start a new application attempt only when status is `ready_to_apply`,
  `queued`, `failed`, `blocked`, or `needs_review`, and the transition to
  `queued` is valid.
- Do not submit a job unless status is `review_ready` and submit approval is
  present.
- Do not transition from `applied` back to non-final status.
- Retry must create an attempt record, not erase history.
- `submitted` means the final action was triggered but confirmation has not yet
  been verified. If verification is interrupted or ambiguous, transition to
  `needs_review`; never retry submission automatically.
- `failed`, `blocked`, and `needs_review` are retryable or recoverable states,
  not terminal outcomes.

## `ApplicationAttempt`

Every processing run for a job creates a new immutable attempt record.

```json
{
  "attempt_id": "uuid",
  "application_id": "stable-application-id",
  "run_id": "uuid",
  "adapter": "greenhouse",
  "mode": "review",
  "status": "in_progress",
  "started_at": "2026-07-10T12:20:00Z",
  "finished_at": null,
  "last_phase": "navigate",
  "submit_approval_id": null
}
```

Allowed attempt modes:

```text
dry_run
review
trusted_auto_submit
```

Rules:

- Attempt IDs and run IDs are UUIDs generated by UniversalAutoApplier.
- Phase results append to an attempt; they are not overwritten.
- At most one active attempt may exist for one application.
- A retry creates a new attempt linked to the same `application_id`.
- A submission approval applies to one attempt only and is consumed after the
  submit action.

## `AdapterResult`

Every adapter method returns an `AdapterResult`.

```json
{
  "status": "success",
  "phase": "navigate",
  "message": "Clicked Apply Now and reached form page",
  "application_id": "abc123",
  "platform": "greenhouse",
  "next_action": "fill_form",
  "screenshots": ["artifacts/abc123/001-form.png"],
  "errors": [],
  "metadata": {
    "url_after_action": "https://..."
  }
}
```

Allowed result statuses:

```text
success
skipped
dry_run
needs_user_input
review_ready
submitted
failed
blocked
unsupported
```

Allowed phases:

```text
prepare
navigate
observe
fill
review
submit
verify
cleanup
```

Rules:

- A result must be structured even when an exception happens.
- Error messages must be human-readable.
- Screenshots must be stored as paths, not embedded binary data.
- `metadata` can contain platform-specific details.

## `PageObservation`

Generated by the generic navigator.

```json
{
  "url": "https://...",
  "title": "Application",
  "page_state": "form",
  "inputs": [],
  "clickables": [],
  "forms": [],
  "file_inputs": [],
  "warnings": [],
  "screenshot": "artifacts/job123/observe-001.png"
}
```

Allowed `page_state` values:

```text
job_page
apply_page
login
register
form
screening_questions
review
submitted
captcha
expired
error
unknown
```

## `Clickable`

```json
{
  "selector": "button[data-test='apply']",
  "tag": "button",
  "text": "Apply now",
  "aria_label": "Apply now",
  "href": "",
  "role": "button",
  "enabled": true,
  "visible": true,
  "bbox": {"x": 120, "y": 400, "width": 160, "height": 44},
  "classification": "safe_apply",
  "confidence": 0.96
}
```

Allowed classifications:

```text
safe_apply
safe_continue
safe_upload
dangerous_submit
login
external_link
unknown
```

Rule:
Screenshots are evidence only. The system must extract clickables from DOM or
accessibility data first. Vision is fallback only.

## `FormField`

```json
{
  "selector": "#email",
  "name": "email",
  "label": "Email address",
  "type": "email",
  "required": true,
  "options": [],
  "current_value": "",
  "nearby_text": "Please enter your email address",
  "confidence": 0.94
}
```

Supported field types:

```text
text
email
phone
textarea
select
radio
checkbox
file
date
number
unknown
```

## `FieldMapping`

```json
{
  "field_selector": "#email",
  "value": "candidate@example.com",
  "source": "candidate.email",
  "confidence": 0.99,
  "requires_user_confirmation": false,
  "explanation": "Label exactly matched email field."
}
```

Allowed mapping sources:

```text
candidate_profile
application_job
document_path
answer_memory
adapter_default
ai_suggestion
user_input
unknown
```

Rules:

- `ai_suggestion` with confidence below threshold must require confirmation.
- File fields must map only to existing files.
- Required unknown fields must create interventions.

## `Intervention`

```json
{
  "intervention_id": "stable-id",
  "application_id": "job-id",
  "status": "pending",
  "kind": "field_answer",
  "question": "Do you require visa sponsorship?",
  "options": ["Yes", "No"],
  "suggested_answer": "No",
  "confidence": 0.62,
  "field_selector": "input[name='sponsorship']",
  "page_url": "https://...",
  "screenshot": "artifacts/job123/intervention-001.png",
  "created_at": "2026-07-10T12:00:00",
  "resolved_at": null
}
```

Allowed intervention kinds:

```text
field_answer
login_required
captcha
unknown_page
review_before_submit
missing_document
validation_error
manual_upload_required
```

Allowed intervention statuses:

```text
pending
approved
edited
skipped
blocked
resolved
```

## Answer Memory

Answer memory stores user-confirmed answers.

```json
{
  "normalized_question": "do you require visa sponsorship",
  "answer": "No",
  "source": "user_confirmed",
  "confidence": 1.0,
  "last_used": "2026-07-10T12:00:00",
  "use_count": 4
}
```

Rules:

- Do not store answers from AI unless user approved them.
- Do not apply answer memory to semantically different questions.
- User must be able to edit or delete memory entries.
