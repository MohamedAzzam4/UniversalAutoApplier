# UI/UX Specification

The dashboard is the control room for the generalized system. It must make the
automation understandable, inspectable, and interruptible.

Do not build a marketing page. The first screen must be the working dashboard.

## UX Goals

The user should be able to answer these questions within five seconds:

1. Is the system running?
2. Which phase is active?
3. Which job is active?
4. Did anything fail?
5. Does the system need my input?
6. Is anything about to submit?

## Primary Navigation

Use these sections:

```text
Dashboard
Queue
Interventions
History
Job Detail
Logs and Errors
Settings
```

## Dashboard View

Purpose:
Show live status and top-level controls.

Required elements:

- Run status:
  - idle
  - running
  - waiting for user
  - failed
  - completed
- Current phase:

```text
Search -> Evaluate -> Tailor -> Queue -> Navigate -> Fill -> Review -> Submit
```

- Active job:
  - company
  - title
  - platform
  - score
  - URL
- Last action.
- Last error.
- Number of jobs in each state.
- Controls:
  - start scan/import
  - start apply dry-run
  - pause
  - resume
  - stop
  - open queue

Rules:

- Dangerous actions must be visually distinct.
- Auto-submit setting must be visible when enabled.
- If waiting for user input, show the intervention link immediately.

## Queue View

Purpose:
Show jobs waiting for application processing.

Columns:

- status
- platform
- company
- title
- score
- source
- documents
- last phase
- last updated
- action

Filters:

- all
- ready to apply
- needs user input
- review ready
- needs review
- failed
- skipped
- applied
- platform
- score threshold

Actions:

- open job detail
- retry safe phase
- skip
- mark blocked
- run dry-run
- approve submit, only if review-ready

Rules:

- `review_ready` jobs must stand out.
- `needs_review` jobs must explain whether submission may already have occurred
  and must never offer an automatic submit retry.
- Failed jobs must show the phase that failed.
- A job with missing documents cannot be started without clear warning.

## Job Detail View

Purpose:
Show complete context for one job.

Sections:

1. Summary
   - company
   - title
   - platform
   - source
   - URL
   - score
   - verdict

2. Documents
   - tailored CV
   - cover letter
   - generated time
   - file existence status

3. Phase Timeline

```text
Imported
Evaluated
Tailored
Queued
Navigated
Filled
Waiting for user
Review ready
Submitted
Applied
```

4. Form Fields
   - label
   - mapped value
   - source
   - confidence
   - status

5. Interventions
   - pending and resolved

6. Evidence
   - screenshots
   - page URLs
   - stage reports

7. Errors
   - phase
   - message
   - stack trace if available
   - retry action

Rules:

- Do not hide uncertainty.
- Every AI-generated answer must show source and confidence.
- Every manual user answer must be clearly marked.

## Interventions View

Purpose:
Let the user unblock automation.

Intervention card fields:

- job title and company
- platform
- question or problem
- suggested answer
- confidence
- options
- screenshot
- page URL
- created time

Actions:

- approve
- edit answer
- skip field
- block job
- open job detail

After approval:

- update answer memory if user allows it
- resume job from paused phase
- record action in history

Rules:

- Do not auto-save AI suggestions as memory.
- User edits should become the authoritative value.
- If a question affects legal/work authorization, require explicit user action.

## History View

Purpose:
Long-term audit trail.

Columns:

- first seen
- last updated
- company
- title
- platform
- source
- score
- status
- applied at
- last error

Features:

- search by company/title/URL
- filter by status
- filter by platform
- filter by score
- sort by latest meaningful date
- open job detail

Rules:

- Missing dates must not break sorting.
- Score `0.0` must display as `0.0`, not blank.
- User-provided strings must be escaped in HTML.

## Logs and Errors View

Purpose:
Make failures actionable.

Required:

- latest logs
- structured errors
- current run ID
- active job ID
- screenshot links
- retry buttons where safe

Error card fields:

- severity
- phase
- job
- message
- timestamp
- next suggested action

Rules:

- Logs are for detail; structured errors are for decisions.
- Stack traces can be collapsed.
- Retry must not duplicate submitted applications.

## Settings View

Purpose:
Configure behavior safely.

Settings:

- dry-run mode
- review-before-submit
- auto-submit enabled
- trusted adapters
- max applications per run
- headless/headed browser
- queue path
- JobHunter export path
- confidence threshold
- answer memory enabled

Rules:

- `auto_submit_enabled` must be visibly dangerous.
- Unknown/generic adapter should not be trustable by default.
- Changing settings should show whether restart is needed.

## Status Colors and Language

Use consistent status labels:

```text
Idle
Running
Waiting for user
Review ready
Needs review
Applied
Failed
Skipped
Blocked
Closed
```

Avoid vague labels:

```text
Done-ish
Maybe apply
AI pending
Unknown ok
```

## UI Tests

Every UI change must be tested with Playwright.

Every UI or browser-facing phase must also be inspected through Playwright MCP
against the running local system at desktop and mobile viewports. The agent
must exercise the workflow from visible controls and attach screenshots to the
phase report; a code-only or API-only inspection is insufficient.

Required checks:

- Dashboard loads.
- Current phase is visible.
- Queue rows render.
- History rows render.
- `0.0` score renders correctly.
- HTML is escaped.
- Intervention can be approved/edited.
- Review-ready job does not submit accidentally.
- Logs/errors show a simulated failure.
