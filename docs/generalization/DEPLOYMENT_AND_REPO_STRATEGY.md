# Repository and Deployment Strategy

This document records the final project-boundary and deployment decisions for
version 1. An implementation agent must treat these as requirements, not open
architecture questions.

## Final Decisions

1. Build the generalized product in a new repository named
   `UniversalAutoApplier`.
2. Keep `JobHunter` and `SiemensAutoApplier` as independent repositories.
3. Run version 1 locally on the user's Windows machine.
4. Do not require a VPS, container platform, Cloudflare, or another hosted
   service for version 1.
5. Preserve the existing Siemens workflow and access it through a defined
   adapter boundary.

## Repository Ownership

The three repositories have separate responsibilities:

```text
JobHunter/
  Owns job discovery, evaluation, ranking, CV tailoring, cover-letter
  generation, and export of jobs that are ready to apply.

SiemensAutoApplier/
  Owns the existing proven Siemens-specific discovery and application
  automation. It remains independently runnable and is the reference behavior
  for Siemens applications.

UniversalAutoApplier/
  Owns queue import, application state, adapter routing, generic navigation,
  generic form filling, interventions, answer memory, review-before-submit,
  evidence, application history, and the operational dashboard.
```

No repository may directly edit another repository's private persistence
files. Integration must use the contracts described below.

## Source of Truth

The source of truth changes by stage:

| Information | Owner |
| --- | --- |
| Search result and evaluation | `JobHunter` |
| Tailored CV and cover letter | `JobHunter` |
| Existing Siemens workflow behavior | `SiemensAutoApplier` |
| Application queue after import | `UniversalAutoApplier` |
| Application attempts and phase timeline | `UniversalAutoApplier` |
| Interventions and remembered answers | `UniversalAutoApplier` |
| Submission result | `UniversalAutoApplier`, based on adapter result |

Once a queue row is imported, `UniversalAutoApplier` assigns and owns its
application status. Re-import may update descriptive job metadata and artifact
paths, but it must not erase attempt history or downgrade a final state.

## Integration Contracts

### JobHunter to UniversalAutoApplier

Version 1 uses a file contract:

```text
application_queue.jsonl
```

Each line must validate against `ApplicationJob` in `DATA_CONTRACTS.md`.
Import must be idempotent. The file can be selected from the dashboard or
configured with a local absolute path. A later version may add an HTTP API,
but the file contract remains supported for reproducible testing.

### UniversalAutoApplier to SiemensAutoApplier

Implement the Siemens integration in stages:

1. Define `SiemensAdapter` inside `UniversalAutoApplier`.
2. Initially invoke the existing Siemens workflow through a narrow integration
   boundary, such as a documented CLI/subprocess command or an importable entry
   point when both repositories are configured locally.
3. Exchange a typed request and a structured `AdapterResult`; do not parse
   human log text to determine success.
4. Keep selectors, Siemens page objects, and Siemens stage logic in
   `SiemensAutoApplier`.
5. Move shared code into a package only after tests prove that both callers
   need it. Do not copy the workflow into both repositories.

The exact invocation mechanism is selected during Phase 0 after mapping the
current Siemens entry points. The behavioral boundary is fixed even if the
mechanism changes.

## Documentation Bootstrap

The planning pack currently lives in:

```text
SiemensAutoApplier/siemens-auto-apply/docs/generalization/
```

When `UniversalAutoApplier` is created:

1. Copy this documentation pack into its `docs/` directory.
2. Preserve the original files until the new repository is initialized and the
   copied documents are verified.
3. Make the `UniversalAutoApplier` copy authoritative for future generalized
   implementation changes.
4. Leave a short pointer in `SiemensAutoApplier` to the new repository after
   the move.

Do not begin generalized production implementation inside
`SiemensAutoApplier` merely because the planning pack currently resides there.

## Local-First Version 1

All required components run on the user's machine:

```text
Local dashboard and API
  -> local application queue and history store
  -> local orchestration worker
  -> local Playwright browser
  -> local CV and cover-letter files
  -> local screenshots, traces, and logs
```

The dashboard must bind to `127.0.0.1` by default. It must not be publicly
reachable unless the user explicitly changes the configuration.

### Required Local Capabilities

- Start the API, dashboard, and worker with one documented command.
- Open the real browser in headed mode for debugging and user intervention.
- Support headless mode for automated tests.
- Reuse a configured local browser profile or saved authentication state
  without committing credentials or session files.
- Store queue, history, logs, screenshots, traces, and artifacts under one
  configurable local data directory.
- Recover unfinished non-final attempts after a restart.
- Display dependency and browser readiness in the dashboard.
- Stop all workers cleanly from the UI or terminal.

### Local Configuration

Use environment variables or a local configuration file excluded from Git.
At minimum, support:

```text
UAA_HOST=127.0.0.1
UAA_PORT=8000
UAA_DATA_DIR=<absolute local path>
UAA_JOBHUNTER_QUEUE=<absolute path to application_queue.jsonl>
UAA_SIEMENS_REPO=<absolute path to SiemensAutoApplier>
UAA_BROWSER_HEADLESS=false
UAA_SUBMIT_MODE=review
```

Defaults must be safe. Missing optional integration paths should mark that
integration unavailable in system health; they must not crash the dashboard.

## Startup and Health Contract

Version 1 must expose these health states:

```text
api: ready | unavailable
store: ready | unavailable
worker: idle | running | paused | unavailable
browser: ready | unavailable
jobhunter_queue: ready | not_configured | invalid
siemens_adapter: ready | not_configured | unavailable
```

The dashboard may report the system as ready for generic applications when the
Siemens integration is not configured. It must show exactly which capability
is unavailable and how to correct the local configuration.

## Failure and Restart Rules

- Persist a phase transition before starting the next external browser action.
- Never infer `applied` merely because a process exited successfully.
- Record `applied` only from explicit submission confirmation defined by the
  responsible adapter.
- On restart, do not repeat an action known to have submitted an application.
- Mark uncertain interrupted attempts as `needs_review`, with the latest URL,
  screenshot, trace, and action available to the user.
- A missing browser, bad queue path, or unavailable Siemens repository is a
  visible health/configuration error, not an unhandled exception.

## Cloud and Remote Access

Cloud deployment is outside version 1. The architecture must avoid blocking a
later move, but no cloud abstraction should be added without a current need.

Possible later additions include:

- Cloudflare Tunnel or Tailscale for private dashboard access.
- A VPS worker with persistent storage.
- Hosted queue coordination for multiple workers.
- Object storage for evidence and artifacts.

These are optional future work. They are not Phase 0-8 acceptance criteria and
must not delay the local product.

## Initial Repository Milestones

### Milestone A: Create the Repository

- Create `UniversalAutoApplier` as a sibling of the two existing repositories.
- Add README, Python project configuration, `.gitignore`, source package, test
  package, and copied planning documents.
- Add a local setup command and a test command.
- Do not add browser automation behavior yet.

### Milestone B: Establish Contracts

- Implement `ApplicationJob`, statuses, queue importer, and persistence.
- Add contract fixtures representing JobHunter output.
- Render imported jobs in a minimal local dashboard.

### Milestone C: Establish Adapter Boundaries

- Implement adapter interface and registry.
- Add a fake adapter for deterministic full-pipeline tests.
- Add the Siemens adapter boundary without copying Siemens selectors or flow.

### Milestone D: Add Browser Capabilities

- Implement generic observation, safe navigation, form extraction, and filling.
- Keep review-before-submit as the default.
- Add Playwright fixture tests and full-pipeline dry-run tests.

## Version 1 Completion Criteria

Version 1 is complete only when all of these are true:

1. A user can start the entire local system with one documented command.
2. The dashboard shows system health, current phase, queue, interventions,
   history, logs, and errors.
3. A JobHunter fixture queue can run through the full generic dry-run pipeline.
4. The pipeline fills a fixture application and stops at review before submit.
5. The Siemens adapter passes its contract and existing Siemens regression
   tests remain green.
6. Restart and retry tests prove that successful applications are not
   duplicated.
7. Unit, contract, integration, fixture, Playwright, full-pipeline, and
   regression tests pass.
8. No VPS or cloud account is required.

## Instructions for AI Implementers

- Confirm the active repository before editing.
- Implement generalized production code only in `UniversalAutoApplier` unless
  a workpackage explicitly changes the JobHunter export or Siemens integration
  entry point.
- Present cross-repository changes as separate commits and reports.
- Do not silently copy code between repositories.
- Do not deploy to a public service during version 1.
- Do not ask for cloud credentials to complete local phases.
- Do not mark a phase complete without its regression gate and user-perspective
  Playwright verification when UI or browser behavior changed.
