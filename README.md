# Universal Auto Applier

A local-first, generalized job application system. Owns queue import,
adapter routing, generic navigation, form filling, interventions, answer
memory, review-before-submit, evidence, application history, and the
operational dashboard.

> Repository bootstrap phase (checkpoint/bootstrap-phase-0).
> No application behavior is implemented yet. See
> `docs/generalization/ROADMAP.md` for the phase plan.

## Repository boundaries

```text
JobHunter
  search -> evaluate -> tailor CV and cover letter

UniversalAutoApplier
  import ready jobs -> route by platform -> navigate -> fill forms
  -> review -> submit
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

## Version 1 is local-first

* Runs locally on the user's Windows machine (Linux/macOS also supported
  for development).
* The dashboard binds to `127.0.0.1` by default.
* No VPS, container platform, Cloudflare, or other hosted service required.
* No authentication on the localhost API (later public deployments must add
  auth before binding publicly).

## Stack (fixed for version 1)

| Concern        | Choice                              |
| -------------- | ----------------------------------- |
| Runtime        | Python 3.11+ (3.12 reference)       |
| API            | FastAPI                             |
| Contracts      | Pydantic v2                         |
| Persistence    | SQLite + SQLAlchemy 2.x             |
| Migrations     | Alembic                             |
| Browser        | Playwright (Chromium)               |
| Dashboard      | Semantic HTML + CSS + vanilla JS    |
| Tests          | pytest, pytest-playwright, httpx    |
| Lint/format    | Ruff, Pyright                       |

Exact resolved versions are recorded in
`docs/generalization/TECHNICAL_BASELINE.md`.

## Quick start

### Windows (PowerShell)

```powershell
.\scripts\setup.ps1
.\scripts\run_local.ps1
```

### Linux / macOS

```bash
./scripts/setup.sh
./scripts/run_local.sh
```

### Equivalent direct commands (no scripts)

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium
python -m universal_auto_applier
```

The dashboard URL is printed on startup. Default: `http://127.0.0.1:8000/`.

## Configuration

Copy `.env.example` to `.env` and edit values. All settings can also be
provided as real environment variables (which take precedence).

| Variable                | Default        | Purpose                                        |
| ----------------------- | -------------- | ---------------------------------------------- |
| `UAA_HOST`              | `127.0.0.1`    | API bind host (do not use `0.0.0.0`)           |
| `UAA_PORT`              | `8000`         | API bind port                                  |
| `UAA_DATA_DIR`          | `.uaa_data`    | Local data directory for DB, logs, artifacts   |
| `UAA_JOBHUNTER_QUEUE`   | _(unset)_      | Absolute path to `application_queue.jsonl`     |
| `UAA_SIEMENS_REPO`      | _(unset)_      | Absolute path to SiemensAutoApplier repo       |
| `UAA_BROWSER_HEADLESS`  | `false`        | Headed mode is local default                   |
| `UAA_SUBMIT_MODE`       | `review`       | `dry_run` / `review` / `trusted_auto_submit`   |

## Health endpoints

| Endpoint               | Behavior                                            |
| ---------------------- | --------------------------------------------------- |
| `GET /api/health`      | Lightweight status (no Chromium launch)             |
| `GET /api/health/detail` | Includes a real Chromium smoke check              |
| `GET /api/docs`        | FastAPI Swagger UI                                  |
| `GET /`                | Dashboard shell                                     |

The health report lists per-capability state for `api`, `store`, `worker`,
`browser`, `jobhunter_queue`, and `siemens_adapter`.

## Tests

### Windows

```powershell
.\scripts\test.ps1
.\scripts\test.ps1 -IncludePlaywright
.\scripts\test.ps1 -All
```

### Linux / macOS

```bash
./scripts/test.sh
INCLUDE_PLAYWRIGHT=1 ./scripts/test.sh
RUN_ALL=1 ./scripts/test.sh
```

### Direct pytest

```bash
python -m pytest                              # default: not live, not playwright
python -m pytest -m playwright                # Playwright UI tests only
python -m pytest -m "not live and not playwright"
```

Test markers (per `docs/generalization/TESTING_STRATEGY.md`): `unit`,
`contract`, `integration`, `playwright`, `pipeline`, `live`.

## Project layout

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
    setup.ps1 / setup.sh
    run_local.ps1 / run_local.sh
    test.ps1 / test.sh
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

## Architecture enforcement

```text
api/ui -> services -> core contracts
services -> repositories and adapter interfaces
adapters -> browser/navigator/form engine and core contracts
persistence -> core contracts
core -> standard library and Pydantic only
```

`core` must not import FastAPI, SQLAlchemy, Playwright, or UI modules. API
routes must not import SQLAlchemy models directly. Generic form modules must
not import Siemens code.

## Safety rules (system-level)

1. Default mode is dry-run or review-before-submit.
2. Unknown sites must never auto-submit.
3. Final submit buttons are dangerous actions.
4. Navigation buttons are allowed only when classified as safe with high
   confidence.
5. Low-confidence field mappings must create interventions.
6. Every application attempt must leave history, logs, and enough evidence to
   debug what happened.

See `docs/generalization/IMPLEMENTATION_RULES.md` for the full rule set.

## Documentation pack

Read the planning pack in this order (see `docs/generalization/README.md`):

1. `DEPLOYMENT_AND_REPO_STRATEGY.md`
2. `TECHNICAL_BASELINE.md`
3. `ROADMAP.md`
4. `DATA_CONTRACTS.md`
5. `IMPLEMENTATION_RULES.md`
6. `TESTING_STRATEGY.md`
7. `UI_UX_SPEC.md`
8. `AI_HANDOFF_PROMPTS.md`

## Non-Goals (version 1)

* A perfect universal bot that submits every unknown website automatically.
* A rewrite of Siemens page objects.
* A giant monolithic "AI browser agent" that clicks whatever the model
  suggests.
* A replacement for JobHunter search and tailoring logic.
* Auto-submit for unknown platforms.
* A cloud or VPS deployment in version 1.
* Generalized production modules added to the Siemens repository.
