# AI Handoff Prompts

Use these prompts when asking GLM or another AI to implement a phase. Copy the
relevant prompt and fill in the phase number.

## Universal Implementation Rules Prompt

```text
You are implementing part of a generalized job application system.

Read these files first:
- siemens-auto-apply/docs/generalization/README.md
- siemens-auto-apply/docs/generalization/DEPLOYMENT_AND_REPO_STRATEGY.md
- siemens-auto-apply/docs/generalization/TECHNICAL_BASELINE.md
- siemens-auto-apply/docs/generalization/ROADMAP.md
- siemens-auto-apply/docs/generalization/DATA_CONTRACTS.md
- siemens-auto-apply/docs/generalization/IMPLEMENTATION_RULES.md
- siemens-auto-apply/docs/generalization/TESTING_STRATEGY.md
- siemens-auto-apply/docs/generalization/UI_UX_SPEC.md

Rules:
- UniversalAutoApplier is a new sibling repository and owns generalized production code.
- Version 1 runs locally and must not require cloud or VPS services.
- Confirm which repository you are editing before making changes.
- Do not rewrite the existing Siemens application workflow unless explicitly required.
- Wrap Siemens as an adapter.
- Keep default mode dry-run/review-before-submit.
- Unknown/generic sites must never auto-submit.
- All status changes must go through store APIs.
- Do not directly mutate JSON persistence outside store classes.
- Add tests for new behavior.
- Run all existing regression tests that are available.
- For every UI or browser-facing change, start the local system and use Playwright MCP to verify the workflow from the user's point of view at desktop and mobile sizes.
- Playwright MCP inspection supplements automated tests; do not substitute one for the other.
- Do not commit unrelated files, generated screenshots, PDFs, history files, or .env files.
- Keep cross-repository changes in separate commits and reports.

Before editing, report:
1. files you inspected
2. implementation plan
3. risks

After editing, report:
1. changed files
2. behavior added
3. tests added
4. tests run and exact results
5. regression status
6. Playwright MCP steps, screenshots, console/network findings, and viewport results when required
7. known risks
```

After the bootstrap copies these files into UniversalAutoApplier, use their
paths under `docs/generalization/` in that repository.

## Repository Bootstrap Prompt

```text
Bootstrap the new UniversalAutoApplier repository. Do not implement application
behavior yet.

Source planning pack:
- SiemensAutoApplier/siemens-auto-apply/docs/generalization/

Required actions:
1. Create UniversalAutoApplier as a sibling repository.
2. Copy the complete planning pack to docs/generalization/.
3. Follow DEPLOYMENT_AND_REPO_STRATEGY.md and TECHNICAL_BASELINE.md exactly.
4. Create the src package, tests layout, local PowerShell scripts, pyproject,
   migrations scaffold, .gitignore, .env.example, and README.
5. Resolve and record compatible dependency versions.
6. Add only skeleton health behavior and tests required by the technical gate.
7. Run Ruff, Pyright, Pytest, migration, API health, and Chromium smoke checks.

Do not:
- add generic navigation or form filling
- copy Siemens workflow code
- alter JobHunter or Siemens production behavior
- bind the API publicly
- commit local databases, environments, browser profiles, credentials, traces,
  screenshots, PDFs, or generated artifacts

Report exact commands, results, changed files, commit hash, and any unmet gate.
Do not declare bootstrap complete while any gate is unverified.
```

## Phase 0 Prompt

```text
Implement Phase 0: Architecture Baseline.

Goal:
Create documentation only. Do not change runtime behavior.

Workpackages:
- 0.1 Current System Map
- 0.2 Architecture Decision Record

Deliverables:
- docs/generalization/CURRENT_SYSTEM_MAP.md
- docs/generalization/ADR_001_ARCHITECTURE.md

Acceptance:
- No production code changed.
- Current Siemens workflow is documented.
- JobHunter role is documented.
- SiemensAutoApplier is documented as first adapter, not a rewrite target.
- New-repository ownership and local-first deployment are documented.
```

## Phase 1 Prompt

```text
Implement Phase 1: Shared Data Contract and Queue.

Goal:
Create the ApplicationJob schema and queue import/export contract.

Scope:
- Add ApplicationJob validation.
- Add the queue importer in UniversalAutoApplier.
- If working in JobHunter too, add application_queue.jsonl exporter.

Required behavior:
- Deterministic application_id.
- Idempotent import.
- No duplicates.
- Invalid rows produce clear errors.
- Existing Siemens history behavior remains compatible.

Tests required:
- schema validation tests
- invalid row tests
- idempotent import tests
- JobHunter export contract tests if export is touched
- existing Siemens regression tests

Do not:
- start browser automation
- implement generic form filling
- change Siemens apply flow
```

## Phase 2 Prompt

```text
Implement Phase 2: Adapter Architecture.

Goal:
Introduce ApplicationAdapter, AdapterResult, AdapterRegistry, and SiemensAdapter.

Rules:
- SiemensAdapter must call the defined Siemens integration entry point.
- Do not duplicate Siemens page object logic.
- Old CLI behavior must still work.
- Do not parse human-readable Siemens logs to infer application success.

Tests required:
- fake adapter interface test
- registry routing test
- Siemens URL routes to SiemensAdapter
- unknown URL routes to Generic placeholder
- existing Siemens tests still pass

Acceptance:
- Adapter-based flow can represent Siemens job application.
- No generic code has Siemens-specific selectors.
```

## Phase 3 Prompt

```text
Implement Phase 3: Generic Navigation Layer.

Goal:
Create PageObserver, ClickableClassifier, and SafeExplorer.

Rules:
- Extract buttons from DOM/accessibility tree, not screenshot alone.
- Screenshot is evidence only.
- Dangerous submit is never safe.
- Generic navigator stops on unknown, captcha, login without credentials, or final submit.

Tests required:
- fixture tests for apply buttons
- fixture tests for continue buttons
- fixture tests for final submit blocking
- fixture tests for login/captcha detection
- dry-run safe exploration test

Acceptance:
- Safe explorer reaches a form in a fixture.
- Safe explorer blocks final submit in a fixture.
- Every step records structured observation/action result.
```

## Phase 4 Prompt

```text
Implement Phase 4: Generic Form Filler.

Goal:
Extract form schema, map fields to profile/job/document data, and fill fields.

Rules:
- Deterministic mapping first.
- AI only for ambiguous fields.
- Low confidence creates interventions.
- File paths must exist before upload.
- Required unknown fields cannot be ignored.

Tests required:
- text/email/phone mapping
- textarea mapping
- radio/checkbox mapping
- dropdown mapping
- file upload mapping
- required unknown field creates intervention
- fixture form filled end to end in dry-run

Acceptance:
- Generic fixture form is filled.
- Unknown required fields pause the workflow.
- Final submit remains blocked.
```

## Phase 5 Prompt

```text
Implement Phase 5: Human Review and Answer Memory.

Goal:
Create intervention queue, answer memory, and review-before-submit state.

Rules:
- User-confirmed answers can be stored.
- AI suggestions are not stored as memory unless user approves.
- Review-before-submit is required for generic adapter.
- Legal/work authorization questions require explicit user confirmation when uncertain.

Tests required:
- create intervention
- approve intervention
- edit intervention
- answer memory reuse
- review-ready state blocks submit until approval

Acceptance:
- Dashboard/API can show and resolve interventions.
- Workflow can resume after intervention resolution.
```

## Phase 6 Prompt

```text
Implement Phase 6: UI/UX Dashboard.

Goal:
Make the system startable and observable from UI.

Required views:
- Dashboard
- Queue
- Interventions
- History
- Job Detail
- Logs and Errors
- Settings

Rules:
- First screen is operational dashboard.
- Current phase must be visible.
- Waiting-for-user state must be obvious.
- Dangerous submit/auto-submit controls must be visually distinct.
- User strings must be escaped.
- Score 0.0 must render correctly.

Tests required:
- API tests
- dashboard Playwright test
- queue Playwright test
- intervention Playwright test
- history sorting/rendering test
- error panel test

Acceptance:
- User can see whether the system is idle/running/waiting/failed.
- User can resolve an intervention from UI.
```

## Phase 7 Prompt

```text
Implement one ATS adapter from Phase 7.

Adapter:
<greenhouse|lever|workday|smartrecruiters|linkedin_easy_apply>

Rules:
- Implement adapter by platform, not by company.
- Add platform detection.
- Add navigation behavior.
- Add form behavior.
- Add submit safety behavior.
- Unknown page layouts must fail safely.
- No auto-submit unless trusted and explicitly enabled.

Tests required:
- can_handle positive and negative URLs
- fixture navigation test
- fixture form extraction/fill test
- final submit block test
- adapter result structure test

Acceptance:
- Adapter works on fixture pages.
- Adapter failure is structured and visible in history/UI.
```

## Phase 8 Prompt

```text
Implement Phase 8: Full Pipeline Orchestration.

Goal:
Run JobHunter export -> queue import -> adapter route -> navigate -> fill -> review -> history update.

Rules:
- Default full pipeline test is dry-run.
- Do not require live ATS websites for regression.
- Do not submit any real application.
- UI must show each phase transition.

Tests required:
- full dry-run pipeline fixture test
- queue import to review-ready state
- intervention resolution in pipeline
- failed job retry without duplicate submission
- Siemens regression test

Acceptance:
- Full dry-run pipeline can run from CLI or dashboard.
- History records every phase.
- Review-ready jobs are visible in UI.
```

## Review Prompt

```text
Review the implementation as a senior engineer.

Prioritize:
1. bugs that could submit accidentally
2. broken Siemens regression behavior
3. state/history corruption
4. duplicate jobs/applications
5. missing tests
6. unclear UI states
7. platform-specific code in generic modules

Output:
- Findings first, ordered by severity.
- File and line references.
- What test would catch each issue.
- Whether the phase can be accepted.
```

## Final Report Template

```text
Phase:
Commit:
Base commit:

Summary:
- ...

Changed files:
- ...

Behavior added:
- ...

Tests added:
- ...

Tests run:
- command: ...
  result: ...

Regression:
- Siemens tests: pass/fail/not run
- Contract tests: pass/fail/not run
- Full pipeline dry-run: pass/fail/not run
- UI Playwright: pass/fail/not run
- Playwright MCP user view: pass/fail/not run

Screenshots/evidence:
- ...

Known risks:
- ...

Unrelated files:
- none / list

Ready for review:
- yes/no
```
