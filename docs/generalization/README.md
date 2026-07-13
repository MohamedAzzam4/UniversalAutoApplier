# Universal Auto Applier Documentation Pack

This folder documents the plan for turning the current Siemens-focused auto
applier into a generalized job application system.

The final repository architecture is:

```text
JobHunter
  search -> evaluate -> tailor CV and cover letter

UniversalAutoApplier
  import ready jobs -> route by platform -> navigate -> fill forms -> review -> submit
  new repository; owns the generalized product and local dashboard

SiemensAutoApplier
  existing repository; preserved as a working Siemens implementation
```

The most important rule is:

```text
Create UniversalAutoApplier as a new repository. Do not implement the
generalized product inside SiemensAutoApplier. Preserve the working Siemens
flow and integrate it through a narrow adapter boundary.
```

## File Map

Read the files in this order:

1. `DEPLOYMENT_AND_REPO_STRATEGY.md`
   - Final repository boundaries, local-first version 1 deployment, integration
     contracts, startup health, and repository bootstrap instructions.

2. `TECHNICAL_BASELINE.md`
   - Fixed runtime, framework, persistence, browser, frontend, package layout,
     commands, dependency policy, and quality tooling for version 1.

3. `ROADMAP.md`
   - Phases, workpackages, acceptance criteria, and implementation order.

4. `DATA_CONTRACTS.md`
   - The shared job queue schema, application state model, adapter result model,
     intervention model, and history rules.

5. `IMPLEMENTATION_RULES.md`
   - Coding style, project fingerprint, module boundaries, logging rules,
     safety rules, and clean-code requirements for humans and AI agents.

6. `TESTING_STRATEGY.md`
   - Unit, contract, integration, regression, fixture, Playwright, and full
     pipeline dry-run tests. Includes the regression gate required after each
     phase.

7. `UI_UX_SPEC.md`
   - Dashboard views, workflows, status model, intervention UX, history UX, and
     user-control requirements.

8. `AI_HANDOFF_PROMPTS.md`
   - Copy-paste prompts for GLM or another AI implementer. Includes phase
     prompts, review prompts, and reporting requirements.

## Vocabulary

Use these terms consistently.

`ApplicationJob`
: A normalized job that is ready for the applier. It includes company, title,
  URL, score, verdict, tailored documents, and application status.

`Adapter`
: A platform-specific implementation that knows how to navigate and apply on a
  specific application platform. Example: `SiemensAdapter`, `GreenhouseAdapter`.

`GenericNavigator`
: A safe fallback navigator that can click obvious "Apply", "Next", and
  "Continue" actions but must stop before final submit or uncertainty.

`GenericFormFiller`
: A platform-agnostic form filler that extracts visible fields, maps them to the
  candidate profile, fills high-confidence answers, and creates interventions
  for uncertain fields.

`Intervention`
: A user-facing task that asks for approval or manual input. Example: "Confirm
  answer to sponsorship question."

`Review Before Submit`
: The safety mode where the system fills the application and pauses before the
  final submission action.

## Non-Goals

Do not build these in the first phases:

- A perfect universal bot that submits every unknown website automatically.
- A rewrite of Siemens page objects.
- A giant monolithic "AI browser agent" that clicks whatever the model suggests.
- A replacement for JobHunter search and tailoring logic.
- Auto-submit for unknown platforms.
- A cloud or VPS deployment in version 1.
- Generalized production modules added to the Siemens repository.

## Fixed Version 1 Decisions

- `UniversalAutoApplier` is a new sibling repository.
- Version 1 runs locally on the user's Windows machine.
- The dashboard binds to localhost by default.
- JobHunter integrates through a versioned queue contract.
- Siemens integrates through `SiemensAdapter`; its browser workflow is not
  copied into the generalized repository.

## System-Level Safety Rules

1. Default mode is dry-run or review-before-submit.
2. Unknown sites must never auto-submit.
3. Final submit buttons are dangerous actions.
4. Navigation buttons are allowed only when classified as safe with high
   confidence.
5. Low-confidence field mappings must create interventions.
6. Every application attempt must leave history, logs, and enough evidence to
   debug what happened.
