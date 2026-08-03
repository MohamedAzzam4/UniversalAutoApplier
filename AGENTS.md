# UniversalAutoApplier — Agent Operating Guide

This file is the first thing an AI or human collaborator should read. It sets
the working conventions, the source-of-truth documents, and the rules that
must never be broken.

## What this repository is

`UniversalAutoApplier` is a **local-first, generalized job application
system**. It imports a ready-to-apply queue (`application_queue.jsonl`),
routes each job to an adapter by platform, navigates and fills the application
form, creates interventions for anything uncertain, pauses before final
submit with review-before-submit, and records evidence and history.

- It does **not** own search, evaluation, or tailoring. `JobHunter` owns
  those and exports the queue.
- It does **not** own the proven Siemens automation. `SiemensAutoApplier`
  owns that and is reached only through a narrow adapter boundary.
- It **never auto-submits** on unknown/generic platforms.

## Start here (source of truth)

| Document | What it gives you |
| --- | --- |
| `docs/CURRENT_STATE.md` | Authoritative snapshot of what is implemented right now, with commit SHAs and test status. Read this first. |
| `docs/handoffs/ACTIVE_WORKPACKAGE.md` | The current workpackage, its branch, pending items, and how to resume it. |
| `docs/NEXT_WORKPACKAGES.md` | Backlog, ordered workpackages, stale-note follow-ups, and optional work. |
| `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md` | The only sanctioned way to test a real submission locally. |
| `docs/generalization/ROADMAP.md` | Original phase plan (phases 0-8, all completed) plus status annotations. |
| `docs/generalization/README.md` | Index of the generalization pack. |
| `README.md` | Quick start and repository overview. |

## Submission capability (accurate contract)

- **Untrusted adapters never auto-submit.** Generic and ATS (`is_trusted=False`)
  adapters never submit through adapter `submit_or_pause`; that path always
  returns `review_ready`.
- **Manual controlled submission is job-type-agnostic.** A `review_ready`
  job — generic or ATS, not only Siemens — MAY be submitted manually through
  the `live-submit` CLI or the submission API, and only when ALL gates pass:
  - `UAA_ENABLE_REAL_SUBMISSION=true`
  - the current snapshot is explicitly approved (hash + form fingerprint match)
  - high-risk fields are explicitly confirmed
  - no pending intervention, stale snapshot, or duplicate gate blocks it
- **Siemens is the only trusted adapter path** (`is_trusted=True`) for an
  adapter-driven submit, but it is not the only job type supported by the
  manually approved controlled submission route.
- **Default behavior remains no submission.**

## Doctrine (non-negotiable)

- **Safe by default.** Dry-run and review-before-submit are the default.
  Unknown platforms never auto-submit. Final submit requires explicit
  approval of the current snapshot.
- **Preserve what works.** Siemens page objects, selectors, and the
  application stage flow are never copied or rewritten here.
- **State through store APIs.** All job/attempt/phase mutations go through
  the persistence store or repository methods. No module rewrites JSON
  persistence except the store owners.
- **Generalized code only here.** Do not implement generalized production
  behavior inside the Siemens or JobHunter repositories.
- **Local-first v1.** No VPS, cloud, or hosted service is required. The
  dashboard binds to `127.0.0.1` and refuses public bind.
- **No invented facts.** Never invent candidate data, dates, employers, or
  document paths. AI answers must carry `source`, `confidence`, `reason`,
  and `requires_confirmation`.

## Git rules

- **Never push or merge directly into `main`.**
- **Merge exactly once through the reviewed PR.** Never perform a local or
  direct squash merge and then also merge the PR; that produced duplicate
  commits in the past and is forbidden.
- **Preserve `checkpoint/*` branches** unless explicitly approved for
  deletion.
- Documentation-only work is developed on `checkpoint/project-rebaseline`
  and merged to `main` via a reviewed PR.
- **Only commit what the workpackage asked for.** Never commit screenshots,
  PDFs, `live-runs`, `.uaa_data`, `.env`, browser profiles, traces, or a
  local database. Check `git status --short` before committing and
  `git diff --check` for whitespace.
- Commit messages match the repo style, e.g.
  `feat(phase-4): generic form filling engine` or
  `docs(rebaseline): project-state audit and handoff pack`.

## Session protocol (mandatory for AI context resets)

### At session start

1. `git fetch origin`.
2. Verify the repository and the current branch.
3. Resolve the branch head dynamically (never trust an embedded SHA for the
   current HEAD; committing changes the SHA, so a file cannot self-report
   the commit that contains it):

   ```text
   git rev-parse HEAD
   git rev-parse origin/<branch>
   ```

   The two values must match before any handoff or review. Compare the
   **base SHA** and the **last completed/checkpoint SHA** from
   `docs/CURRENT_STATE.md` / `docs/handoffs/ACTIVE_WORKPACKAGE.md` against
   the resolved values; they are reference points, not the current HEAD.
4. Inspect `git status --short` before any checkout or reset.
5. Read, in order: `AGENTS.md`, `docs/CURRENT_STATE.md`,
   `docs/handoffs/ACTIVE_WORKPACKAGE.md`, `docs/NEXT_WORKPACKAGES.md`,
   `docs/generalization/IMPLEMENTATION_RULES.md`,
   `docs/generalization/TESTING_STRATEGY.md`.
6. **Stop immediately** if any local changes could be overwritten; do not
   `checkout`/`stash`/`reset` over them without explicit instruction.

### During work

- Update `docs/handoffs/ACTIVE_WORKPACKAGE.md` after every major milestone
  (status, completed work, changed files, tests and exact results,
  decisions, blockers, next action).
- **Checkpoint before context reaches approximately 60-70 percent.**
  Persist progress and commit before continuing.
- Checkpoint before pausing, handing off, or switching AI.
- **Never rely on chat history as project memory.** The documents are the
  memory.

## Commands

```text
# setup (creates .venv, installs deps + Chromium, applies migrations)
.\scripts\setup.ps1                 # Windows
./scripts/setup.sh                  # Linux/macOS

# run the local API + dashboard (prints URL)
.\scripts\run_local.ps1             # Windows
./scripts/run_local.sh              # Linux/macOS

# tests (gates)
.\scripts\test.ps1                  # default (no playwright/live)
.\scripts\test.ps1 -IncludePlaywright
.\scripts\test.ps1 -All

# direct quality gates
python -m ruff check src tests migrations
python -m ruff format --check src tests migrations
python -m pyright
python -m pytest                      # default: not live, not playwright
python -m pytest -m "not live"        # includes playwright if installed
```

`filterwarnings = ["error", "error::ResourceWarning"]` means any
`ResourceWarning` is a test failure — dispose engines/close contexts in
`finally` blocks.

## What to verify after a change to code

Run the full gate after any change that touches code:

1. Unit + contract + integration + pipeline tests pass.
2. Playwright (UI/browser) tests pass if browser or dashboard changed.
3. `ruff check`, `ruff format --check`, and `pyright` pass.
4. `git diff --check` passes and no unrequested files are staged.
5. If UI or browser behavior changed, run the local system and verify with
   Playwright MCP at 1440x900 and 390x844 before claiming acceptance.

For real-submission work: only the standalone `live-submit` CLI/API path
plus a user-approved run per `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`.

## Markers and naming

Use the project fingerprint from `docs/generalization/IMPLEMENTATION_RULES.md`:

`ApplicationJob`, `ApplicationAttempt`, `ApplicationAdapter`,
`AdapterRegistry`, `AdapterResult`, `PageObservation`, `Clickable`,
`FormField`, `FieldMapping`, `Intervention`, `AnswerMemory`.

Verbs, no synonyms: `observe_page`, `classify_clickables`,
`extract_form_schema`, `map_fields`, `fill_form`, `create_intervention`,
`record_attempt`, `mark_review_ready`, `mark_applied`.

## Logging

- Format: `[application_id] phase action result`.
- Never log passwords, tokens, or API keys.
- Errors should include a next action where possible.

## Error categories

`navigation_error`, `login_required`, `captcha_detected`,
`form_extraction_error`, `field_mapping_error`, `validation_error`,
`submission_blocked`, `platform_changed`, `unknown_page`.

## When to update the handoff pack

Change `docs/handoffs/ACTIVE_WORKPACKAGE.md` whenever the active workpackage
enters a new state (in progress, blocked, needs-review). Update
`docs/CURRENT_STATE.md` whenever `main` advances and any fact in that
document goes stale. Audit against the generalization docs for
contradictions rather than silently carrying them forward.

## ACTIVE_WORKPACKAGE.md required content

The document must always contain:

- WP ID and objective
- status
- base SHA
- current branch
- last completed/checkpoint SHA
- a mandatory command block to resolve the branch head dynamically:

  ```text
  git rev-parse HEAD
  git rev-parse origin/<branch>
  ```

  The two resolved values must match before handoff/review.
- completed work
- changed files
- tests and exact results
- decisions made
- blockers / risks
- exact next action and command
- last-updated timestamp

Do not embed a "current HEAD" SHA in the file — the file cannot contain the
SHA of the commit that modifies it. Do not create status-only commits
merely to update the file with its own new SHA. Update the file on
substantive milestones; resolve the head dynamically when needed.

## This file's scope

This file is process and documentation metadata. It intentionally does not
re-state the detailed phase plans, data contracts, or safety rules from the
`docs/generalization/` pack — those remain the authority on behavior.