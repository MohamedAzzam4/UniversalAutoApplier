# Next Workpackages

Ordered candidate work. Each entry states its objective, concrete behavior,
forbidden shortcuts, acceptance criteria, required tests, and predecessor.
Items are not started until they are pulled into an active workpackage in
`docs/handoffs/ACTIVE_WORKPACKAGE.md`.

## V2 roadmap — current delivery order

The V2 plan and critique are now in [`docs/v2/`](v2/UAA_V2_REVIEW_AND_PLAN.md).
V2 is the active delivery roadmap for the current checkout. The WQ-1 through
WQ-10 and Supervisor V0 entries below are retained as historical backlog and
authorization context; they do not override the V2 ordering. The archived
WQ-8 handoff remains authoritative for its exact owner-approved submission
gate.

### V2-00 — Baseline and execution contracts (checkpointed; full Playwright gate unverified)

**Objective.** Preserve the chosen dashboard-history + supervisor baseline,
record the migration head and full non-live gate state, repair formatting and
any narrowly attributable combined browser-test lifecycle issue, promote the
review/critique into the repository, and adopt the concrete state, re-import,
upload, readiness and browser-ownership contracts in section 17 of the plan.

**Chosen baseline.** Branch `checkpoint/v2-00-baseline`, created from the
dashboard-history commit. It descends from the WQ-8 checkpoint
`18783ffc1da216709d6a36d010157ce3430fd7e3`, then includes the supervisor
history and dashboard-history commit above `origin/main`. The migration head
at that starting point is `0016_supervisor`. This is one selected line of
history; the mixed documentation and quality-baseline package does not rewrite,
merge or delete the existing dashboard, supervisor or WQ-8 checkpoints.

**Checkpoint result.** The selected baseline, migration head, formatting and
type-check results are recorded in the archived V2-00 handoff. The first
combined `pytest -m "not live" -q` attempt printed a failure before it was
stopped, so no full combined result is claimed. A focused dashboard-plus-
consent reproducer later isolated the sync Playwright lifecycle conflict; the
consent tests were changed to use pytest-playwright's page fixture and the
ordered 8-test reproducer plus standalone 7-test module passed. The complete
Playwright-inclusive suite has not been rerun. The WQ-8 handoff remains
archived, and no live mutation or real submission occurred. The first real
form family remains an explicit owner decision until an approved queue target
is available.

### V2-01 — Immediate correctness blockers (active; F2b checkpointed, browser-gate follow-up awaiting supervisor review)

**Objective.** Correct unsupported fact assertions, missing field read-back,
destructive re-import of UAA operational state, inconsistent duplicate gates,
and misleading required-document/request evidence. Unqualified mutation flows
stop at observation or owner handoff.

**Ownership contract.** JobHunter owns source/job fields and produced
tailoring outputs. UAA owns its manual-submitted marker, corrections and
answer provenance, attempts, interventions, approvals, submission outcomes
and evidence. Implement an explicit interim metadata allowlist that preserves
at least `dashboard_submitted` and `dashboard_submitted_at` through re-import;
do not blindly deep-merge arbitrary imported metadata. Full versioned
upstream/local fact separation is V2-03.

**Document/readiness contract.** Report native file selection as
`selection_verified`, never as remote acceptance. Native selection alone is
visible evidence and does not make a document resolved: a typed,
UAA-owned `NativeFinalSubmitUploadContract` must explicitly qualify the
specific input for the final submit flow. No ATS or generic flow is qualified
by default. An asynchronous flow may report `remote_accepted` or `rejected`
only under an explicitly declared, validated status protocol scoped to the
target form and frame; timeout or ambiguous evidence remains `unknown`. A
status signal remains unknown when a form has multiple file inputs or a
single input carries multiple files without an explicit correlation or
aggregate-status contract. A local `accept` hint mismatch is an unknown local
constraint result, not a site rejection. Legacy snapshots remain readable, but
missing or contradictory
upload evidence blocks new approval. WQ-8 Phase A therefore requires a
qualified upload contract and fresh observation before it can be review-ready;
this does not authorize or perform a real submission.

`review_ready` requires a verified final review boundary, full-attempt
snapshot, read-back of required answers, evidence that meets the flow's
declared upload contract, guarded final action, and no pending intervention
or unknown side effect. An intermediate completed form step is not
review-ready.

**Acceptance and tests.** Negative skill evidence and ambiguous visa answers
abstain; cleared/rewritten values fail read-back; rejected files are not
accepted; manual-submitted state survives unchanged and updated imports and
blocks every execution entry point; native and asynchronous upload protocols
produce truthful readiness. Include the plan cases T01–T06, T10–T11, T19 and
T29 where applicable, then run the full regression gate. No new local model is
required.

**Checkpointed slices.** F1 / T01–T02 is committed and pushed at
`e77940b4fb7ee2d8d22e8a09adc887734ac59edb`: abstain for absent, negated, or
contradictory skill claims, including tested German negative evidence; do not
infer valid-visa possession from `requires_sponsorship`. Its focused mapper
tests and the 1,474-test unit/contract/integration/pipeline selection passed.
The separate consent-test fixture lifecycle repair is committed and pushed at
`4b85767b8d48fa01a30cf9d24d75dcce25276d60`; its ordered dashboard-plus-consent
reproduction passed 8/8 and its standalone module passed 7/7.

**F2/T03 checkpointed.** Commit `706831cf86f5d28ab7cd862de098b72f643a0112`
verifies stable text-field values after blur, reports cleared or incompatible
rewrites as interventions with no verified fill, and carries live requiredness
into the submission snapshot. Deterministic and LLM mismatch cases plus a
compatible email-normalization case passed in the focused 13-test Playwright
executor module. The full non-Playwright selection passed 1,479 tests; the
shared working tree also contained the parallel F4 changes now under review.
Ruff, Pyright and `git diff --check` passed. The combined Playwright-inclusive
gate remains unverified; upload evidence is deferred to F2b.

**F4/T10 checkpointed.** Commit `cb0ceba0bf38206ceb4609e13019b5a6660598f9`
adds the interim re-import metadata allowlist in `persistence/job_repository.py`
and its unit/contract tests. Re-import preserves local `dashboard_submitted`
and `dashboard_submitted_at` markers, strips producer attempts to set them on a
new insert, refreshes producer metadata, and retains the whole per-job answer
map keys `application_answers`, `form_answers`, and `question_answers` only
when the producer omits those keys. An explicitly supplied producer answer map
replaces the prior map; arbitrary old metadata is not deep-merged. Full
upstream/local fact separation remains V2-03. Validation passed: 37 focused
repository/importer tests, Ruff check/format, Pyright, and the 1,479-test
unit/contract/integration/pipeline selection.

**F4/T06 manual-submission eligibility checkpointed.** A shared domain eligibility check
blocks canonical `submitted`/`applied` jobs and the UAA-owned
`dashboard_submitted=true` marker from repeat preparation or submission across
the coordinator, retry API, supervisor, and existing pipeline/orchestration
entry points. An explicit dashboard correction to `false` restores
eligibility; the block itself does not advance canonical job status or act as
ATS confirmation. The marker is rechecked at the submission claim boundary;
the final coordinator gate remains in place before the click. Tests confirm a
marker-block result leaves the WQ-8 authorization active and unconsumed, and an
unknown outcome still blocks after changed-snapshot reapproval. Pipeline
eligibility checks retain the job object used for result accounting.

F4/T06 validation passed: the focused six-module selection passed **157
tests**; the API candidate-profile, background-worker and orchestrator
regressions passed **43 tests** after the pipeline counter fix; Ruff check,
Ruff format check, Pyright and `git diff --check` passed; and the full default
non-live/non-Playwright gate passed **1,481 tests** with **303 deselected** in
832.35 seconds. A bare browser-inclusive `pytest -x -vv` run stopped at the
separate stale dashboard header assertion (`Documents` expected versus
`DOCUMENTS` rendered) after **385 passed**; the full Playwright-inclusive
suite is not claimed green. The broad non-Playwright result includes the
parallel F2b source changes present in the shared working tree; the F4/T06
focused tests isolate that package. No real target or submission was in scope.

**F2b/T04/T19 upload evidence integration (checkpointed; browser-gate
follow-up under review).** Live file execution
preserves per-document selected filenames, observed constraints and evidence
source/detail. A native `selection_verified` record without an explicit
native-final-submit flow contract remains visible but unresolved; an explicit
typed contract qualifies only its matching file input. Declared async status
is validated, unique, and scoped to the same form/frame. Invalid combinations
cannot establish readiness, local accept-hint mismatches remain `unknown`, and
exception details exposed as evidence are sanitized. API review responses
expose the same document evidence and unresolved-upload count; completeness,
approval and coordinator gates block every unresolved or legacy-unobserved
document. WQ-8 review-plan hashing binds status, flow contract and evidence,
while legacy documents keep their prior plan encoding until re-observed.

F2b validation passed: focused API, WQ-8 authorization/coordinator, snapshot
safety, bundle, live-executor and WQ-8 persistence tests — **133 passed** in
165.52 seconds; the WQ-7C synthetic-mutation Playwright module passed **9/9**
in 39.69 seconds; the ordered Playwright lifecycle reproduction passed **3/3**
in 5.64 seconds; and the full non-live/non-Playwright gate passed **1,502
tests, 309 deselected** in 787.59 seconds. Ruff check and format check passed
(**239 files already formatted**); Pyright reported **0 errors, 0 warnings, 0
informations**; and `git diff --check` passed. The later full browser-inclusive
non-live gate passed **1,808 tests, 3 deselected in 1,616.60 seconds**. Its
targeted lifecycle checks passed in order after earlier pytest-playwright
modules: dashboard/WQ-7B/WQ-7C/WQ-8 passed **33 tests**, WQ-7C/WQ-8 passed
**14**, and dashboard plus WQ-8 heuristic browser tests passed **9**. Ruff,
format (**239 files already formatted**), Pyright (**0 errors, 0 warnings, 0
informations**) and `git diff --check` also pass on the final test-only
follow-up. No new ATS protocol is enabled by default, and no real ATS action
or submission occurred.

The synthetic final-pipeline regression proves actual multipart CV and
cover-letter bytes reach the local test server under test-owned native-upload
contracts; it proves no submit request occurs before approval, checks full
SHA-256 hashes after approval, and confirms the duplicate guard prevents an
extra request. These test declarations do not enable a production ATS or
generic upload contract. Browser tests use pytest-playwright-owned contexts;
the public `LiveBrowserRunner.run()` entry point remains covered from an
isolated worker thread where the main test thread already owns the plugin's
synchronous Playwright manager.

**Next action.** Supervisor review of the exact staged browser-gate test,
harness and handoff paths; commit and push the approved follow-up on
`checkpoint/v2-01-correctness`, verify local HEAD equals
`origin/checkpoint/v2-01-correctness`, and stop before V2-01's next slice.

**Predecessor.** V2-00 baseline checkpoint `0e21adb43b400dd90a91a3ba754269e1a061375e`.
Owner approval is separately required for any real target/live action under
the existing WQ-8 contract; no code review gate blocks this synthetic work.

### V2-02 — Visible-page classification and script-source false positives (backlog)

**Objective.** Keep benign page scripts from triggering the observer's
error-page or `unknown_page` classification, while preserving fail-closed
behavior for actual visible error and blocker pages.

**Observed defect.** During the synthetic final-pipeline E2E, `analyze_page`
included inline script source in the text used by its static error-page
classifier. A harmless script containing `new Error(...)` therefore produced
`unknown_page` with “Error page detected.” The E2E fixture was made neutral so
the current gate can exercise upload and approval behavior; this backlog item
tracks the product classifier defect separately.

**Concrete behavior.** Base page classification on rendered, user-visible
content and explicit browser/page signals. Ignore inert source text in
`script`, `style`, and other non-visible document nodes. Keep genuine visible
error content, CAPTCHA/login gates, and unknown layouts blocked as before.

**Required regression tests.** A form page with harmless inline code
containing `new Error(...)` remains correctly classified; inert script/style
source cannot trigger an error-page result; a genuinely visible error page is
still `unknown_page`; existing login/CAPTCHA blocker classifications remain
unchanged.

**Forbidden shortcuts.** Weakening the actual-error or blocker guards; treating
unknown pages as forms; making production behavior depend on a fixture URL or
test-only marker.

**Predecessor.** V2-01 F2b upload-evidence integration; fix the classifier in a
separate implementation slice with its own targeted and full regression gate.

### V2-07A — Dashboard interaction design (separate follow-up)

**Objective.** Prototype the operator journey and visible action/state
contract. This package is design work; production UI changes belong to V2-07B
after the executor command/revision/idempotency and event contracts settle.

**Known design gaps to address.** Replace prompt-based intervention editing,
10-second status polling and blind resume. Add a queue/document preflight
before scheduling, per-job next actions, mobile layout and keyboard/screen
reader access.

**Required interaction contract.** Corrections proceed through explicit
`Save → Saved → Resume queued → Rechecking` states. Show each job's current
phase and next action. Distinguish flow support, answer source/confidence and
document evidence (including local selection versus remote acceptance). A
stale correction shows a conflict with current state instead of silently
overwriting it. The prototype must explain why work paused and what the owner
can do next.

**Acceptance.** The owner can understand the queue, a paused job, a correction
and its resume state without shell commands. Cover 1440×900 and 390×844 plus
keyboard/focus and screen-reader labels. This package does not implement the
dashboard or alter submission controls.

**Predecessor.** Starts from V2-00; implementation dependencies for V2-07B
remain V2-02–V2-04.

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
-> submit approval` sequentially or in parallel with explicit controls and
a deterministic, atomic handoff between the two repositories.

**Concrete behavior.**

- **Sequential mode.** JobHunter completes its export fully (writes and
  closes a valid `application_queue.jsonl`), then UAA imports and applies.
  UAA never starts against a partial export.
- **Parallel mode.** JobHunter scans/evaluates/tailors new jobs while UAA
  processes already-exported jobs; the shared queue is consumed with
  atomic handoff so neither process reads a partially-written file.
- **Worker counts.** Separate, bounded worker counts per phase (export vs
  import/apply) that users configure explicitly; no unbounded pools.
- **Queue handoff is atomic.** A JSONL row is handed from exporter to
  importer only as a complete, validated record; the importer never consumes
  a partially-written `application_id`.
- **No duplicate processing.** Once an `application_id` is claimed, no
  other worker/phase re-processes it, in either mode.
- **Status visibility.** Both repositories' pipelines expose a visible
  status (started / exporting / exported / importing / applying / done /
  blocked) through the dashboard/health surface so the operator sees the
  cross-process state.
- **Repository boundaries.** Orchestration talks only client/CLI/API
  boundaries with the queue files (JSONL + DB). JobHunter never imports UAA
  Python modules, and UAA never imports JobHunter modules; no process parses
  human logs to learn completion.

**Forbidden shortcuts.**

- Exporting and importing on the same path concurrently without file-level
  locking / atomic rename.
- Reading a partially-written JSONL file; reading tail-only lines then
  assuming the rest of the record.
- Re-processing an `application_id` already handled within a run or across
  runs (duplicate applications).
- One process silently waiting for the other with no status indicator.
- Cross-repository Python imports or human-log parsing to determine
  "success".
- Running parallel mode as a disguised sequential loop with no worker
  limiting.

**Acceptance criteria.** Coordinated swap without hand-editing;
deterministic ordering in sequential mode; in parallel mode, both
pipelines make progress and every `application_id` is processed exactly
once; a crash at any point leaves the queue in a state that resumes without
duplicating or dropping rows; the dashboard shows a visible status for both
repositories.

**Required tests.** Contract tests proving export -> import is stable
under sequential and parallel schedules; atomic handoff tests (partial
file, then completed row); duplicate-`application_id` tests proving exactly-once
processing in both modes; worker-count limit tests; status-visibility API
tests; process-boundary tests asserting no cross-repo Python imports and no
human-log parsing.

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

**Progress (WQ-7A/B merged 2026-08-16; WQ-7C merged 2026-08-20 via PR #15).**

- **WQ-7A complete** — safe live ATS dry-run infrastructure merged
  (`6326e4e`): opt-in live browser dry-run, hard submit interlock installed
  before any page script, synthetic profile, never clicks final submit.
- **WQ-7B complete** — real ATS navigation reconnaissance merged via PR #13
  (head `adc8c8d`, merge `cab7a13`) under the owner-approved amendment:
  real public application forms reached on Greenhouse and Lever; Workday,
  SmartRecruiters, and iCIMS classified as externally gated / externally
  imposed unsupported conditions after permitted replacement attempts; zero
  typed values, zero uploads, zero UAA submit clicks; no applications
  submitted.
- **WQ-7C complete and merged** (PR #15, head `395b7dc…`, merge `2ac1e00…`; six
  required CI checks green) — controlled **synthetic** field fill + synthetic
  document upload on real public ATS forms, final submission forbidden:
  component proofs on Greenhouse + Lever, then the accepted full-system
  same-job proof (normal JobHunter discovery → Robco/Ashby form mutated with
  the approved synthetic CV, `submitted=false`, submit interlock all-zero,
  `test.candidate@example.com` identity constant throughout). Docs and
  evidence: `docs/evidence/wq-7c/` (manifest, same-job closure,
  `FINAL_ACCEPTANCE.md`). The OpenRouter 429 on an independent same-day
  re-evaluation is external and does not invalidate the successful proof.
- **Superseded intermediate WQ-7C experiments are audit history only** — the
  senior Account-Executive persona variant and the pipeline.md-seeded Carta
  flow were abandoned in favor of the natural AI/Data Working-Student
  discovery proof described above; both are explicitly marked SUPERSEDED in
  `docs/handoffs/ACTIVE_WORKPACKAGE.md` and `docs/evidence/wq-7c/`.
- **Real submission is still out of scope for WQ-7C.** Nothing in WQ-7C is a
  real-application test; the next controlled stage is WQ-8 below.

## WQ-8 — One staged controlled real submission using the sanctioned plan
(NOT STARTED — successor to the accepted WQ-7C pre-submit proof)

**Objective.** On the user's machine, execute exactly one manually approved
real submission following the staged plan, and prove WQ-1 transitions and
evidence.

**Concrete behavior.** Follow the exact plan steps; verify `submitted` ->
`SUBMITTED`, snapshot, evidence, duplicate block.

**Forbidden shortcuts.** Automated real submission; submitting a job not
in `review_ready`; skipping the backup step.

**Acceptance criteria.** Not in CI; user-witnessed screenshot and DB state
per the plan (`docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`).

**Required tests.** Only the plan stages, not CI.

**Predecessor/dependency.** WQ-1 (so the transitions exist), WQ-7C (accepted
pre-submit boundary).

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

## WQ-10 — Long-run reliability and footprint hardening (NOT STARTED)

**Objective.** Broaden what WQ-7C proved pre-submit: stress the fill/mutation
boundary across more jobs and ATS variants, tighten evidence cleanup, and
improve error classification for runtime drift.

**Concrete behavior.** Deterministic re-runs over a bounded job set; stable
evidence layout; visible errors for platform changes; no new bypasses.

**Forbidden shortcuts.** Adding more live ATS runs to default CI; touching
submit safety.

**Acceptance criteria.** Re-runs produce equivalent mutation outcomes;
reporting remains consistent; no false `submitted`.

**Required tests.** Contract + regression on mutation-plan and
evidence-writing paths.

**Predecessor/dependency.** WQ-7C merged.

## Optional — Field-resolution/embedding optimization (NOT STARTED)

**Objective.** After operational correctness: optional semantic
field-mapping upgrade (e.g., embeddings) to reduce skipped fields.

**Concrete behavior.** Deterministic, reversible; strict value-source gating
kept; never auto-answers a field that needs confirmation.

**Forbidden shortcuts.** Any behavior that relaxes WQ-7C's
never-auto-answer / intervention guarantees.

**Predecessor/dependency.** WQ-7C merged, WQ-10; deliberately deferred from
WQ-7C.

## Optional — UI polish (after operational correctness)

**Objective.** Cosmetic/UX improvements only after WQ-1..WQ-9 are done:
pagination on history, filter persistence, hiding submit controls for
`is_trusted=False` jobs more aggressively, better empty states.

**Dependency.** All WQ-1..WQ-9; must not change safety behavior.

## SUPERVISOR V0 — Real Review-Only Pilot (NEXT)

**Branch:** `feature/agent-supervisor-mode-v0` (implemented, not yet merged; `docs/agent_supervisor/AGENT_SUPERVISOR_V0.md`).

**Objective.** Run a small real review-only pilot using a dedicated pilot data directory (e.g. `UAA_DATA_DIR=/tmp/uaa_pilot_data` or `UAA_DATA_DIR=D:\uaa_pilot_uaa_data`) so the pilot never touches the WQ-8 DB (`.uaa_data`). Recommended invocation:

```text
UAA_DATA_DIR=/tmp/uaa_pilot_data python -m universal_auto_applier supervisor-run --queue <path> --review-only
UAA_DATA_DIR=/tmp/uaa_pilot_data python -m universal_auto_applier supervisor-status
UAA_DATA_DIR=/tmp/uaa_pilot_data python -m universal_auto_applier supervisor-handoffs
UAA_DATA_DIR=/tmp/uaa_pilot_data python -m universal_auto_applier supervisor-review-ready
```

No real ATS traffic beyond the review-only prepare path; no submission, no authorization, no WQ-8 budget consumed. Concurrency remains 1.

**After pilot:**
- policy refinement from pilot findings (owner policy allowlist, high-risk keyword expansion);
- optional repair-agent integration (consume `RepairTicket` rows);
- controlled-submit integration only after separate explicit owner approval (not in this workpackage).

## How to start one

1. Read `docs/handoffs/ACTIVE_WORKPACKAGE.md` and `docs/CURRENT_STATE.md`.
2. Create a `checkpoint/<topic>` branch from `main`.
3. Implement; run the full regression gate; update the handoff pack.
4. Merge once via a reviewed PR and update `docs/CURRENT_STATE.md`.
