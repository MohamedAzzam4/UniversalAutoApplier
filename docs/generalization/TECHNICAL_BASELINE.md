# Technical Baseline

This file fixes the version 1 technology choices and project layout. An
implementation agent must not replace these choices during a phase without a
separate architecture decision record and explicit approval.

## Runtime and Language

- Python 3.12 is the reference runtime.
- Support Python 3.11 or newer unless a dependency requires otherwise.
- Use type annotations for all public functions and methods.
- Use `async` only at I/O boundaries that benefit from it. Do not mix sync and
  async Playwright APIs in the same runtime path.
- Use UTF-8 files and LF line endings in the new repository.

## Backend and Contracts

- FastAPI provides the local HTTP API and serves the dashboard assets.
- Pydantic version 2 models define API and external file contracts.
- Use a FastAPI lifespan context manager to create and close shared resources.
- Keep route handlers thin; they validate input, call an application service,
  and serialize a result.
- Bind to `127.0.0.1` by default.
- Do not add authentication to the localhost-only version 1 API. Any later
  remote-access mode must add authentication before binding publicly.

## Persistence

- SQLite is the version 1 database.
- SQLAlchemy 2.x is the persistence layer.
- Alembic owns schema migrations.
- Use explicit session and transaction context managers.
- Service methods define transaction boundaries; repositories do not commit
  secretly inside reusable operations.
- Enable SQLite foreign keys for every connection.
- Store timestamps as timezone-aware UTC values and render them in local time
  in the UI.
- Do not use JSON files as the internal history database. JSONL remains the
  external JobHunter queue contract.

Required database tables:

```text
application_jobs
application_attempts
phase_results
interventions
answer_memories
artifacts
system_runs
```

Use migrations from the first schema. Tests may create a fresh temporary
SQLite database by applying all migrations.

## Browser Automation

- Use Playwright for Python.
- Chromium is the required version 1 browser; Firefox and WebKit are optional
  compatibility targets.
- Application runtime uses one browser context per application attempt.
- Automated tests use an isolated context per test.
- Use Playwright locators based on role, label, test ID, and stable DOM
  attributes before CSS selectors. XPath and screen coordinates are last
  resorts and require a comment explaining why.
- Saved authentication state and persistent browser profiles are local secrets
  and must be excluded from Git.
- Capture a Playwright trace for failed browser attempts and for full-pipeline
  regression tests.
- Headed mode is the local default; headless mode is the test default.

## Dashboard Frontend

- Version 1 uses semantic HTML, CSS, and modular browser JavaScript served by
  FastAPI.
- Do not add a frontend framework or a separate Node build pipeline in version
  1 unless the dashboard requirements demonstrably exceed this baseline and an
  ADR approves the change.
- Use JSON API calls for live state. Polling is acceptable initially; use one
  shared polling controller with backoff and cancellation.
- The UI must remain usable at 1280x720, 1440x900, and 390x844 viewports.
- Follow `UI_UX_SPEC.md`; do not create a marketing or landing page.

## Package and Directory Layout

Create the new repository with this shape:

```text
UniversalAutoApplier/
  pyproject.toml
  README.md
  .env.example
  .gitignore
  alembic.ini
  migrations/
  docs/
  scripts/
    setup.ps1
    run_local.ps1
    test.ps1
  src/
    universal_auto_applier/
      api/
      core/
      services/
      persistence/
      application_queue/
      adapters/
      navigator/
      form_engine/
      interventions/
      browser/
      ui/
        static/
  tests/
    unit/
    contract/
    integration/
    fixtures/
    playwright/
    pipeline/
```

Use absolute imports from `universal_auto_applier`. Do not modify `sys.path` at
runtime.

## Dependency Policy

- Declare application and development dependencies in `pyproject.toml`.
- Commit a resolved lock file when the selected packaging tool supports one.
- Pin major versions and let patch updates remain possible.
- Do not add a dependency for logic that is clear and small in the standard
  library.
- Run dependency installation only inside `.venv`.
- Keep browser binaries out of Git.

Required dependency families:

```text
fastapi >=0.115,<1
pydantic >=2,<3
sqlalchemy >=2,<3
alembic >=1,<2
playwright
pytest
pytest-playwright
httpx
```

The implementation agent must resolve and record exact compatible versions
when the repository is bootstrapped. It must run the test suite after any
dependency update.

## Commands

The repository must provide these stable Windows entry points:

```text
PowerShell: .\scripts\setup.ps1
PowerShell: .\scripts\run_local.ps1
PowerShell: .\scripts\test.ps1
```

The scripts must call documented Python module entry points. CI and non-Windows
users must also be able to run equivalent commands directly:

```text
python -m universal_auto_applier
python -m pytest
```

`setup.ps1` creates `.venv`, installs the project and development dependencies,
installs Chromium through Playwright, applies migrations, and prints the next
run command. It must be safe to rerun.

`run_local.ps1` starts the API/dashboard and worker, opens no public listener,
and prints the dashboard URL.

`test.ps1` runs the regression gate appropriate to the current phase. It must
return a nonzero exit code on failure.

## Quality Tools

Use one configuration in `pyproject.toml` for:

- Ruff formatting and linting.
- Pyright strictness for the source package, allowing narrow documented
  exceptions at third-party boundaries.
- Pytest markers for `unit`, `contract`, `integration`, `playwright`,
  `pipeline`, and `live`.

Tests marked `live` must never run in the default regression command. Live ATS
tests require an explicit flag and must still use review-before-submit unless a
specific trusted-adapter test is separately approved.

## Architecture Enforcement

The following dependency direction is mandatory:

```text
api/ui -> services -> core contracts
services -> repositories and adapter interfaces
adapters -> browser/navigator/form engine and core contracts
persistence -> core contracts
core -> standard library and Pydantic only
```

Forbidden dependencies:

- `core` importing FastAPI, SQLAlchemy, Playwright, or UI modules.
- API routes importing SQLAlchemy models directly.
- Generic form modules importing Siemens code.
- Siemens adapter code importing dashboard DOM or view logic.
- Tests depending on execution order or a developer's real local database.

## Technical Verification Gate

Before Phase 1 begins, verify:

1. A clean clone can run `setup.ps1` successfully.
2. The local API starts and responds to `/api/health`.
3. The dashboard opens at the printed localhost URL.
4. A fresh database reaches the current Alembic revision.
5. A smoke test launches and closes Chromium.
6. Ruff, Pyright, and Pytest pass on the skeleton.
7. No credentials, browser profiles, database files, screenshots, or generated
   artifacts are tracked.
