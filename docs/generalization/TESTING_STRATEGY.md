# Testing Strategy

The generalized system must be tested at multiple levels. The goal is not only
to prove new code works, but also to prevent future phases from breaking
previous working behavior.

## Testing Principles

1. Every phase adds tests.
2. Every phase runs all previous tests.
3. Siemens regressions are blockers.
4. Unknown-site generic automation must be tested in dry-run only.
5. Tests must cover failure paths, not only happy paths.
6. Fixture-based tests are required for ATS platforms.
7. UI changes require Playwright verification.

## Test Types

### Unit Tests

Purpose:
Test pure logic without launching a browser.

Required areas:

- `ApplicationJob` validation.
- Platform detection.
- Status transitions.
- Clickable classification.
- Form label extraction helpers.
- Field mapping rules.
- Answer memory normalization.
- Adapter registry routing.

Examples:

```text
test_application_job_rejects_invalid_url
test_platform_detection_greenhouse
test_clickable_classifier_submit_is_dangerous
test_field_mapper_email_exact_match
test_answer_memory_normalizes_question_text
```

### Contract Tests

Purpose:
Protect the boundary between JobHunter and UniversalAutoApplier.

Required tests:

- JobHunter export row validates as `ApplicationJob`.
- Applier importer accepts valid queue rows.
- Applier importer rejects invalid rows with clear error.
- Import is idempotent.
- Queue export does not include rejected jobs.
- Queue export does not include jobs missing required documents when status is
  `ready_to_apply`.

### Store Tests

Purpose:
Protect history and intervention persistence.

Required tests:

- `upsert_application_job` is idempotent.
- Attempt records append instead of replacing history.
- Terminal statuses are not overwritten accidentally.
- Interventions can be created and resolved.
- Answer memory can be created, reused, edited, and deleted.

### Adapter Tests

Purpose:
Ensure routing and platform behavior are stable.

Required tests per adapter:

- `can_handle` positive URL.
- `can_handle` negative URL.
- Adapter returns structured `AdapterResult`.
- Adapter fails safely on unexpected page.
- Adapter respects dry-run.
- Adapter does not submit when not approved.

For Siemens adapter:

- Existing Siemens workflow tests must still pass.
- Adapter wraps existing `ApplyWorkflow` rather than duplicating it.

### Fixture HTML Tests

Purpose:
Test form extraction and navigation without live websites.

Maintain fixtures:

```text
tests/fixtures/forms/simple_application.html
tests/fixtures/forms/file_upload.html
tests/fixtures/forms/radio_checkbox.html
tests/fixtures/forms/select_dropdown.html
tests/fixtures/forms/review_submit.html
tests/fixtures/platforms/greenhouse_job.html
tests/fixtures/platforms/lever_job.html
tests/fixtures/platforms/workday_login.html
tests/fixtures/platforms/unknown_custom_form.html
```

Required fixture tests:

- Extract text inputs.
- Extract file inputs.
- Extract radio groups.
- Extract checkboxes.
- Extract dropdown options.
- Detect dangerous submit button.
- Detect login page.
- Detect review page.

### Playwright Tests

Purpose:
Verify the actual browser behavior and UI.

Required tests:

- Dashboard loads.
- Queue view loads.
- History view loads.
- Job detail view loads.
- Intervention appears and can be resolved.
- Generic fixture form is filled in dry-run.
- Final submit is detected and blocked.
- Status timeline updates across phases.
- Error panel shows failed phase.

Recommended command shape:

```text
python -m pytest tests/test_*.py
python -m pytest tests/playwright/test_dashboard.py
python -m pytest tests/playwright/test_generic_form_dry_run.py
```

### Full Pipeline Dry-Run Tests

Purpose:
Simulate the real user workflow without submitting anything.

Test: JobHunter export to generic form dry-run

Steps:

1. Load a fake JobHunter `application_queue.jsonl`.
2. Import queue.
3. Route job through adapter registry.
4. Use fixture application page.
5. Navigate to form.
6. Fill known fields.
7. Create intervention for unknown required field.
8. Resolve intervention.
9. Continue to review page.
10. Stop before final submit.
11. Verify history contains full phase timeline.

Acceptance:

- Job status ends as `review_ready`, not `applied`.
- No submit click happened.
- Documents were uploaded or simulated.
- Evidence screenshot paths were recorded.
- UI displays the job in review-ready state.

Test: Siemens adapter regression dry-run

Steps:

1. Use current Siemens test history fixture.
2. Route Siemens job to `SiemensAdapter`.
3. Verify existing eligibility gate is used.
4. Verify ApplyWorkflow receives the expected job ID.
5. Verify dry-run does not submit.

Acceptance:

- Existing Siemens behavior is preserved.
- Rejected jobs are not sent to apply.
- Tailored document paths are used when present.

## Regression Gate

Run this gate after every workpackage and every phase.

### Required Checks

```text
1. git status --short
2. unit tests
3. contract tests
4. adapter tests
5. existing Siemens regression tests
6. full pipeline dry-run test
7. Playwright UI tests if UI changed
8. Playwright MCP user-perspective inspection if UI or browser behavior changed
9. git diff --check
10. changed-file review for unrelated files
```

### User-Perspective Playwright MCP Check

After any phase that changes the dashboard, navigation, form filling,
interventions, review, or browser-visible errors, the implementation agent must
start the local system and inspect it with Playwright MCP as a user would.

Required inspection:

1. Open the printed localhost dashboard URL.
2. Verify the dashboard is not blank and has no visible overlap at 1440x900 and
   390x844.
3. Start the phase's fixture or dry-run workflow through the UI.
4. Observe the phase indicator, active job, queue/history update, and any
   intervention or error without relying on terminal output.
5. Confirm dangerous submit remains blocked where required.
6. Capture screenshots for the implementation report.
7. Inspect browser console errors and failed network requests.

This MCP inspection complements automated Playwright tests; it does not replace
them. If Playwright MCP is unavailable in the implementation environment, the
agent must report the gate as unverified and must not claim the UI phase is
fully accepted.

### No-Go Conditions

Do not continue to the next phase if any of these happen:

- Siemens existing tests fail.
- Generic mode can click final submit.
- Import creates duplicate jobs.
- Queue/history loses previous job state.
- UI cannot show waiting/error state.
- Required Playwright MCP user-perspective verification was skipped.
- Tests pass only because they inspect strings instead of behavior, when
  behavior can be tested.
- Generated artifacts are committed accidentally.

## Test Data Rules

- Use fake candidate data in tests.
- Use fake PDF files where possible.
- Do not depend on real credentials.
- Do not call live ATS websites in unit or regression tests.
- Live website tests, if any, must be opt-in and dry-run.

## Test Naming Convention

Use descriptive names:

```text
test_import_queue_is_idempotent
test_generic_navigator_blocks_final_submit
test_field_mapper_creates_intervention_for_unknown_required_field
test_siemens_adapter_uses_existing_apply_workflow
test_dashboard_shows_needs_user_input_job
```

Avoid vague names:

```text
test_fix
test_new_flow
test_ai
test_button
```

## Minimum Tests Per Phase

Phase 0:

- Documentation only. No tests required unless code changes.

Phase 1:

- Schema unit tests.
- Queue import/export contract tests.
- Idempotency tests.

Phase 2:

- Adapter interface tests.
- Registry routing tests.
- Siemens adapter regression tests.

Phase 3:

- Page observer fixture tests.
- Clickable classifier tests.
- Safe exploration dry-run tests.

Phase 4:

- Form schema extraction tests.
- Field mapping tests.
- Fill engine fixture tests.

Phase 5:

- Intervention store tests.
- Answer memory tests.
- Review-before-submit tests.

Phase 6:

- API tests.
- UI Playwright tests.
- Dashboard status tests.

Phase 7:

- Per-adapter fixture tests.
- Per-adapter dry-run Playwright tests.

Phase 8:

- Full pipeline dry-run tests.
- Regression test matrix.

## Implementation Report Test Section

Every AI implementation report must include:

```text
Tests run:
- command:
- result:
- output summary:

Regression:
- old tests:
- new tests:
- full pipeline dry-run:
- UI Playwright:
- Playwright MCP user-perspective check:

Not run:
- list anything skipped and why
```
