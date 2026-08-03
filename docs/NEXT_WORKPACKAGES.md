# Next Workpackages

Ordered candidate work. Each entry states its objective, concrete behavior,
forbidden shortcuts, acceptance criteria, required tests, and predecessor.
Items are not started until they are pulled into an active workpackage in
`docs/handoffs/ACTIVE_WORKPACKAGE.md`.

## WQ-1 — Correct post-submit job / history transitions (CONFIRMED DEFECT)

**Objective.** Close the status-transition defect: a controlled submission
result is recorded in the submission tables but `ApplicationJob.status` is
not advanced, so the dashboard queue/history does not show the effective
post-submit state.

**Confirmed implementation gap.** Verified against `f7c49f7`:
`submission/execution_service.py` and `api/routes/submit.py` record
`SubmissionResult` rows but never call the job-store transition; the only
place that writes `ApplicationStatus.SUBMITTED` is the trusted-adapter
orchestrator path (`services/pipeline_orchestrator.py`). This is a defect,
not an open design question.

**Required future behavior.**
- `submitted_confirmed` -> `ApplicationStatus.SUBMITTED`.
- `APPLIED` only after a reliable ATS reference number or a stronger
  completion contract — never from a generic success signal alone.
- `outcome_unknown` -> `ApplicationStatus.NEEDS_REVIEW`.
- Duplicate prevention remains durable (gates + status both block).
- Dashboard queue/history must immediately show the effective state.

**Forbidden shortcuts.** Writing job status from the browser thread
without the claim; dropping `needs_review` on unknown outcomes; logging
"applied" without a reliable reference; skipping persistence tests.

**Acceptance criteria.**
- `submitted_confirmed` sets job status `SUBMITTED` and the UI shows it.
- `outcome_unknown` sets `NEEDS_REVIEW` and blocks automatic retry.
- `APPLIED` is only reached via an explicit ATS completion reference.
- No duplicate `SUBMITTED`/`APPLIED` on retry.

**Required tests.**
- Persistence/unit tests for each result-state transition.
- API/history tests asserting the API reflects the transition.
- Playwright test that the dashboard shows `submitted` / `needs_review`.
- Full regression gate.

**Predecessor/dependency.** None (blocker for WQ-8).

## WQ-2 — Wire JobHunter `run_all` to deterministic queue export

**Objective.** Make JobHunter's pipeline produce a reproducible
`application_queue.jsonl` that UAA can consume.

**Concrete behavior.** `run_all` (or a dedicated exporter) writes one
JSONL row per ready-to-apply job with a deterministic `application_id`,
absolute artifact paths, and `status: ready_to_apply`; no duplicate,
rejected, stale, or already-applied rows.

**Forbidden shortcuts.** Writing ad-hoc files in different schemas; seeding
the UAA DB manually instead of going through the importer contract.

**Acceptance criteria.** An empty queue is a valid empty file; re-run is
idempotent; every line validates against `ApplicationJob`.

**Required tests.** Contract + golden canonical-URL cases; idempotent
export; export contract fixtures.

**Predecessor/dependency.** None.

## WQ-3 — Wire UAA production queue import / API / startup consumption

**Objective.** Let v1 actually read and import a real queue at startup /
from configuration and expose it through the API, not only via the test
importer.

**Concrete behavior.** `UAA_JOBHUNTER_QUEUE` path is consumed on startup
and/or via an import endpoint; immediately usable dashboard views; health
reflects queue presence/validation.

**Forbidden shortcuts.** Only accepting the path but never reading it;
double-import when both settings and API are used without a lock.

**Acceptance criteria.** Imported queue is reflected in the dashboard
history; import is idempotent; a missing file is a visible health error;
restart re-imports.

**Required tests.** Import integration; restart; health; idempotency.

**Predecessor/dependency.** WQ-2.

## WQ-4 — Background real-browser pipeline from dashboard with pause/cancel

**Objective.** A user starts the pipeline from the dashboard; it runs in a
background orchestrator with pause/cancel controls.

**Concrete behavior.** Start/pause/cancel controls; a real worker picks
jobs, runs the dry-run/navigate/fill flow with review-before-submit; a safe
stop is available at every phase; state is shown live.

**Forbidden shortcuts.** Opening a headed browser on the dashboard bind
thread; ignoring a pause/cancel request mid-run; risking duplicate
applications.

**Acceptance criteria.** Any new pipeline run may be paused and canceled;
no duplicate; the job phase is visible; browser cleanup on cancel.

**Required tests.** Orchestration units; API controls; Playwright
run + cancel; restart without duplicate.

**Predecessor/dependency.** WQ-3.

## WQ-5 — Restart recovery and stale `in_progress` recovery

**Objective.** An unfinished, interrupted attempt recovers into a known
state on restart; stale `in_progress` becomes reviewable `needs_review`.

**Concrete behavior.** On startup, active/in-progress jobs with an expired
claim are marked `needs_review`; the job continues safely from the last
evidence; nothing auto-resubmits.

**Forbidden shortcuts.** Silently dropping interrupted attempts; claiming
submission implied by process exit; auto-repeating actions.

**Acceptance criteria.** After restart, each unfinished job is in a
defined state (`needs_review`, etc.) and the evidence path is displayed.

**Required tests.** Persistence restart; API/history; no duplicate on
restart.

**Predecessor/dependency.** WQ-1.

## WQ-6 — Cross-repository sequential/parallel orchestration controls

**Objective.** Let users drive `JobHunter export -> UAA import -> pipeline
-> submit approval` sequentially or in parallel with explicit controls.

**Concrete behavior.** CLI commands / API endpoints to run export then
UAA import; Siemens-adapted and generic paths are separated so they never
interfere.

**Acceptance criteria.** Coordinated swap without hand-editing;
deterministic ordering.

**Required tests.** Contract tests proving export -> import is stable;
orchestrator tests.

**Predecessor/dependency.** WQ-2, WQ-3.

## WQ-7 — Real external dry-runs across representative ATS platforms

**Objective.** Level-2 dry-runs against real Greenhouse/Lever/Workday (and
other representative) sites with evidence capture.

**Concrete behavior.** Opt-in per-platform `live-dry-run` with no final
click; a cache of blockers and review evidence.

**Forbidden shortcuts.** Using the live dry-run to bypass submit safety;
running in default CI.

**Acceptance criteria.** Reports show "stopped before submit" for each
platform.

**Required tests.** Opt-in live dry-run per platform; regression preserves
fixture behavior.

**Predecessor/dependency.** WQ-3 (API/state ready to capture evidence).

## WQ-8 — One staged controlled real submission using the sanctioned plan

**Objective.** On the user's machine, execute exactly one manually approved
real submission following the staged plan, and prove WQ-1 transitions and
evidence.

**Concrete behavior.** Follow the exact plan steps; verify `submitted` ->
`SUBMITTED`, snapshot, evidence, duplicate block.

**Forbidden shortcuts.** Automated real submission; submitting a job not
in `review_ready`; skipping the backup step.

**Acceptance criteria.** Not in CI; user-witnessed screenshot and DB state
per the plan.

**Required tests.** Only the plan stages, not CI.

**Predecessor/dependency.** WQ-1 (so the transitions exist).

## WQ-9 — Live adapter hardening and Siemens regression verification

**Objective.** Harden the live Siemens + generic adapter behaviors and prove
the Siemens regression gate remains green after the WQ packages.

**Concrete behavior.** Verify Siemens known URLs/entry points through the
adapter boundary; the runner falls back safely; evidence capture is stable;
no Siemens logic is copied.

**Forbidden shortcuts.** Faking Siemens regression; copying selectors.

**Acceptance criteria.** Siemens regression remains green; timeouts report
visible errors.

**Required tests.** Re-created Siemens adapter tests; regression.

**Predecessor/dependency.** WQ-1, WQ-4.

## Optional — UI polish (after operational correctness)

**Objective.** Cosmetic/UX improvements only after WQ-1..WQ-9 are done:
pagination on history, filter persistence, hiding submit controls for
`is_trusted=False` jobs more aggressively, better empty states.

**Dependency.** All WQ-1..WQ-9; must not change safety behavior.

## How to start one

1. Read `docs/handoffs/ACTIVE_WORKPACKAGE.md` and `docs/CURRENT_STATE.md`.
2. Create a `checkpoint/<topic>` branch from `main`.
3. Implement; run the full regression gate; update the handoff pack.
4. Merge once via a reviewed PR and update `docs/CURRENT_STATE.md`.