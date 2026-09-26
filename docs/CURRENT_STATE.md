# Current State

## Current checkout overlay — V2-02 intake preparation WIP (2026-09-26)

This overlay records the selected V2-02 intake-preparation work in progress.
Publication status is resolved dynamically from the active handoff; the V2-00
section below is historical, not current checkout status.

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **Active branch:** `codex/v2-intake-preparation`, based on source checkpoint
  `322f3aaea844f61c090d4f4c8c9167fb7ea4f307`. The latest published checkpoint
  is the CSS wrap fix `9a2f0a03982fdde13c6f1d3ee52aa559770f29bd`, following
  intake checkpoint `84ee4900365a5e7b10734e8bc2b6033f6aa76b92` and worker
  checkpoint `227aac407a1f605ff7e142b110081911be815752`; local `HEAD` and
  `origin/codex/v2-intake-preparation` matched at verification. Current
  documentation updates are unpublished and the branch head must be resolved
  dynamically before claiming a later checkpoint.
- **Actual-input intake:** local capture/import validation is accepted for the
  owner-selected DATEV Workday row in the existing queue snapshot. It passed
  importer validation and two named-service imports against a disposable temp
  SQLite store, producing two successful run records and one persisted job.
  Producer completion/run provenance and a queue-to-document generation
  manifest remain unverified, so completed-export provenance is pending.
  Sanitized evidence is in
  [`docs/evidence/v2/INTAKE_DISCOVERY.md`](evidence/v2/INTAKE_DISCOVERY.md).
- **Current delivery status:** V2-00 and V2-01 are complete. V2-02 remains
  active; the progress-fingerprint and actual-input intake slices are
  validated, and the worker slice has focused synthetic acceptance. Its local
  app/API/subprocess-worker selection passed (12 tests in 41.34s) with
  attempt/phase persistence and expected required-field, upload, and
  denied-HTTP blocks. Python Playwright review passed at 1440×900 and 390×844
  for three synthetic jobs; the fixture observed no final-application POST.
  Repo-wide Ruff check and format check passed (244 files already formatted), Pyright
  reported 0 errors, 0 warnings and 0 informations, and `git diff --check`
  passed on published checkpoint `9a2f0a0`. The first combined non-live gate
  attempt was deliberately stopped at 19% after two failures, so no full-gate
  result is claimed. Both failures concerned swallowed network/navigation
  errors. The opt-in `raise_on_error` correction now preserves these errors as
  durable `failed` results while retaining earlier job results; the corrected
  focused selection passed **18 tests in 53.60s**. Post-correction Ruff check,
  format (244 files already formatted), Pyright (0 errors/warnings), and
  `git diff --check` passed. The corrected full gate is about to rerun.
  Review-boundary acceptance and correction/reuse remain pending.
  Native `Resume` file selection still has no named flow upload/send contract,
  so selection alone cannot satisfy required-document readiness or pass the
  review-ready gate. Full preparation remains pending until the selected
  flow's contract is qualified. No live DATEV preparation or submission is
  claimed. The planned operator
  evidence is limited to current job/run/outcome state; no detailed attempt
  timeline UI or new API is claimed.
  Focused worker evidence is in
  [`docs/evidence/v2/WORKER_PREPARATION.md`](evidence/v2/WORKER_PREPARATION.md).
  V2-07A is a separate unmerged design/prototype checkpoint. The legacy
  Review-tab/persisted-state mismatch is a V2-07B follow-up; local UI review
  does not establish full dashboard consistency.
- **Branch preservation:** the divergent `checkpoint/project-rebaseline`
  branch remains preserved. The current implementation WIP uses
  `codex/v2-intake-preparation`; the earlier documentation-only revision on
  `codex/v2-delivery-plan` is historical. No branch was merged, reset or
  deleted during this work.

## Historical V2-00 baseline snapshot (2026-09-24)

This section records the selected V2-00 baseline at branch creation. It is
retained as history; the current delivery overlay above takes precedence.

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **Base:** `origin/main` at branch creation was
  `76b2e1f166dd56398e7234c733ca24d703d0194a`.
- **Chosen branch:** `checkpoint/v2-00-baseline`, created from the dashboard
  history checkpoint `68014659ea7df5d2c1c632e27891ffbe478d978b` and pushed to
  origin before edits. This is a descendant of
  `origin/checkpoint/wq-8-controlled-real-submission` at
  `18783ffc1da216709d6a36d010157ce3430fd7e3`; it then contains the supervisor
  work and dashboard-history commit. It is one selected line of history from
  `origin/main`, not three divergent trees that need reconciling. No existing
  branch was rewritten or merged.
- **Supervisor reference:** `origin/feature/agent-supervisor-mode-v0` was
  `bc24a153cd4eb7f1e486b0fcdfc63ed9699f571b` at branch creation.
- **Migration head:** `0016_supervisor` (verified with the repository venv's
  Alembic command).
- **Implemented additions beyond the older V1 snapshot:** the WQ-8 controlled
  submission authorization and observation path; the review-only Agent
  Supervisor V0; attachable browser handoff; dashboard history and submitted
  tracking. WQ-8's exact one-submission owner gate remains active. No real
  application has been submitted; this V2-00 work performs no submission or
  live ATS mutation.
- **V2 plan and contracts:** see
  [`docs/v2/UAA_V2_REVIEW_AND_PLAN.md`](v2/UAA_V2_REVIEW_AND_PLAN.md),
  especially section 17. Those later-workpackage contracts are design
  decisions, not claims that the behavior is implemented.
- **V2-00 gate results:** recorded in
  [`docs/handoffs/ACTIVE_WORKPACKAGE.md`](handoffs/ACTIVE_WORKPACKAGE.md) and
  updated there after each pushed checkpoint.

## Historical V1 snapshot (superseded for the V2-00 checkout)

Authoritative snapshot of `UniversalAutoApplier` as of the project rebaseline.
If this document contradicts any older planning doc, this document wins for
"what is implemented"; the planning doc keeps its architectural authority.

- Reference commit: `2ac1e006fa8119d5d487625a5c36a17b4f4c5c20` (`main`,
  merge of PR #15 — WQ-7C controlled synthetic ATS mutation +
  end-to-end vertical slice, accepted/merged)
- Coverage: all roadmap phases 0-8, the controlled final submission
  pipeline, and WQ-1 through WQ-7C are merged to `main` via reviewed PRs.
- Branch work: WQ-2 (JobHunter queue export) lives in the JobHunter repo and
  is merged at JobHunter main `0e8ba2f`. WQ-3 (durable production queue
  import, API, startup import, dashboard Queue Import card), WQ-4 (background
  browser pipeline), WQ-5 (restart recovery), WQ-6 (cross-repo
  orchestration), WQ-7A (safe live ATS dry-run), WQ-7B (real ATS
  navigation reconnaissance), and WQ-7C (controlled synthetic ATS mutation)
  are all merged to `main`. Real **final submission** is still later work
  (WQ-8); synthetic pre-submit field mutation/upload is proven.

## What is implemented

| Area | Implementation | Notes |
| --- | --- | --- |
| Core contracts | `core/models.py`, `core/statuses.py`, `core/identity.py`, `core/question_models.py` | `ApplicationJob`, `AdapterResult`, status enums, canonical URL identity. |
| Application queue | `application_queue/importer.py` | JobHunter `application_queue.jsonl` -> SQLite, idempotent upsert. |
| Persistence | SQLAlchemy 2 + Alembic, 9 migrations | tables: jobs, attempts, phase_results, interventions, answer_memories, artifacts, system_runs, submission tables, queue_import_runs (WQ-3). |
| API + dashboard | `api/`, `ui/static/*` | FastAPI localhost API, HTML/CSS/vanilla JS dashboard, submit view. |
| Queue import (WQ-3) | `services/queue_import_service.py`, `api/routes/queue_import.py`, `cli.py`, `ui/static/*` | durable opt-in queue import: named service, run history in `queue_import_runs` (migration 0009), `POST/GET /api/queue/{import,status}`, `queue-import` CLI, dashboard Queue Import card. Import never starts a browser or pipeline. |
| Health | `services/health_service.py`, `api/routes/health.py` | per-capability report (`api`, `store`, `worker`, `browser`, `jobhunter_queue`, `queue_import`, `siemens_adapter`). |
| Adapters | `adapters/*` | Siemens adapter (trusted), Greenhouse, Lever, Workday, SmartRecruiters, LinkedIn Easy Apply, Generic fallback, `_ATSBase` shared base. Registry deterministic order. |
| Navigator | `navigator/page_observer.py`, `clickable_classifier.py`, `safe_explorer.py`, `apply_path_finder.py` | DOM-based observation, strict dangerous-submit blocking. |
| Form engine | `form_engine/schema_extractor.py`, `field_mapper.py`, `fill_engine.py`, `live_executor.py` | extraction, mapping, fill, live execution. |
| Interventions | `interventions/store.py`, `answer_memory.py`, `review.py`, fill/navigation bridges | intervention records, answer memory, review-before-submit gate. |
| LLM helpers | `llm/` (answer_validator, qa_service, question_classifier, question_resolver, truth_ledger) | grounded Google AI (Gemma) answer validation and question resolution; no inventing facts. |
| Browser | `browser/live_runner.py`, `live_models.py` | live browser dry-run with Playwright, evidence, traces; never clicks final submit. |
| CLI | `cli.py`, `__main__.py` | `list-jobs`, `queue-import`, `browser-session`, `live-dry-run`, `live-submit`. |
| Submission | `submission/coordinator.py`, `execution_service.py`, `models.py`, `store.py`, `api/routes/submit.py` | approval + snapshot + gated submit; `submitted_confirmed`, `outcome_unknown`, `already_submitted` result states. |
| Orchestration | `services/pipeline_orchestrator.py`, `api/routes/pipeline.py` | safe pipeline routing, review-only default. |
| Cross-repo orchestration (WQ-6) | `services/orchestration_service.py`, `services/jobhunter_runner.py`, `api/routes/orchestration.py`, `persistence/orchestration_run_repository.py`, `migrations/0012_orchestration_runs.py`, `migrations/0013_orchestration_durable_evidence.py` | Sequential/parallel orchestration of JobHunter export → UAA import → UAA pipeline. Durable run state with targeted/processed/remaining IDs, all pipeline run IDs, and pass count. Process-level boundary (never imports JobHunter modules). Fail-closed target manifest. Multi-batch continuation with no-progress detection. Never performs final submission. |
| Live ATS dry-run (WQ-7A) | `browser/live_runner.py`, `browser/submit_interlock.py`, `cli.py`, `execution_mode.py`, `services/live_dry_run_platforms.py`, `synthetic_profile.py` | opt-in live browser dry-run with a hard submit interlock installed before any page script; never clicks final submit. |
| Recon-only nav (WQ-7B) | `execution_mode.py` (`UAA_LIVE_RECON_ONLY`), `navigator/apply_path_finder.py` (`embed_rank` widget preference), fixtures under `tests/fixtures/recon/`, `docs/evidence/wq-7b/MANIFEST.md` | real public forms reached on Greenhouse + Lever; Workday, SmartRecruiters, iCIMS externally gated; zero typed values, zero uploads, zero UAA submit clicks. |
| Synthetic ATS mutation (WQ-7C) | `synthetic_profile.py`, `config.py` (`UAA_LIVE_SYNTHETIC_MUTATION` opt-in), `browser/mutation_plan.py`, `form_engine/live_executor.py`, `browser/live_runner.py` (`run_synthetic_mutation`), `browser/submit_interlock.py`, `cli.py` (`live-synthetic-mutation`), `queue-import --synthetic-mutation`, orchestration `synthetic_orchestration` opt-in (migration `0014`), `navigator/apply_path_finder.py` invisible-badge/`cards[` fixes | opt-in only (default off), synthetic identity + approved-document hash enforcement, mutually exclusive with real submission, interlock armed before mutation, plan frozen/hashed pre-mutation, `submitted=false` always. Full-system same-job proof to the pre-submit boundary accepted and merged via PR #15 (see `docs/evidence/wq-7c/`). |
| Agent Supervisor V0 | `supervisor/` (models, policy, planner, tools, service, store, handoff, repair), `interventions/resolve_service.py`, `migrations/0016_supervisor.py`, `cli.py` (`supervisor-run` + status/handoffs/tickets/review-ready), `docs/agent_supervisor/AGENT_SUPERVISOR_V0.md` | Review-only, concurrency=1. Typed business-level tools wrap existing UAA services; no raw browser/submit escape. Policy gates A/B/C/D, Siemens skip, bounded retries, human handoffs, repair tickets, fail-closed planner. See `docs/agent_supervisor/AGENT_SUPERVISOR_V0.md`. Branch `feature/agent-supervisor-mode-v0` (not yet merged). |
| CI | `verify-windows-py314.yml`, `verify-linux.yml` | Windows+Python 3.14 primary (core + playwright jobs); Linux matrix 3.11-3.14; non-live only (no external live tests in default CI). |

## Status

`main` is at `b5e1532f763b5c5f4e86d36061d7f175158415c8` (post-merge closure of
PR #14 after WQ-7B). Merge history note:
commit `2cf3f18` introduced the controlled-submission content directly onto
`main` (a local squash onto main) rather than through a reviewed PR. PR #3
(`controlled-final-submission`, head `f5b2055`) was then merged as `f7c49f7`
carrying the same tree — an empty duplicate commit. Harmless: the tree is
correct and clean; history must NOT be rewritten. From now on, every change
to `main` arrives through exactly one reviewed-PR merge — never
commit/squash to main first and also merge the PR.

WQ-7B (real ATS navigation reconnaissance) was accepted and merged via PR #13
(head `adc8c8d`, merge `cab7a13`), closed by PR #14 merge `b5e1532f`.

**WQ-7C (controlled synthetic ATS mutation + end-to-end vertical slice) is
MERGED / COMPLETE.** It was accepted by the owner and merged to `main` via
PR #15 (head `395b7dc`, merge commit `2ac1e00`). WQ-7C proved the complete
core workflow through the **pre-submit boundary** on one job kept identical
throughout (normal JobHunter discovery → Robco/Ashby official board), with
`submitted=false`, an all-zero submit interlock, and **zero actual
applications submitted**. WQ-7C's six final CI checks all passed on the PR
head (Linux 3.11/3.12/3.13/3.14, Windows Core, Windows Playwright).

Conservative statement of what the **complete core workflow** now proves
(through the pre-submit boundary):

```text
JobHunter search/discovery
-> evaluation
-> tailoring
-> generated synthetic artifacts
-> queue export
-> UAA import/orchestration
-> same-job ATS navigation
-> field resolution
-> synthetic field mutation
-> synthetic document upload
-> safe pre-submit stop
```

This was demonstrated end-to-end on one job kept identical throughout (Robco →
RobCo's official Ashby board, `submitted=false`, interlock all-zero). See
`docs/evidence/wq-7c/FULL_SAME_JOB_CLOSURE.md` (authoritative) and
`docs/evidence/wq-7c/FINAL_ACCEPTANCE.md`.

**Explicitly NOT proven** (do not claim complete):

- real application submission (WQ-8, not started);
- production operation with the owner's real candidate data;
- broad long-run reliability across many jobs / ATS variants;
- optional field-mapping / embedding optimizations (deliberately deferred).

Latest verified CI (WQ-7C final PR head, `395b7dc` — exactly the six
required checks, all successful):

```text
Linux  + Python 3.11   -> success
Linux  + Python 3.12   -> success
Linux  + Python 3.13   -> success
Linux  + Python 3.14   -> success
Windows Core           -> success
Windows Playwright     -> success
```

## Test counts (reference run)

```text
1258 unit/contract/integration  passed  (WQ-7C merged head `395b7dc`, not live/not playwright)
269  playwright                passed  (WQ-7C merged head `395b7dc`)
1527 total                      passed
```

## Submission capability (accurate contract)

- **Untrusted adapters never auto-submit.** Generic and ATS adapters
  (`is_trusted=False`) never submit through adapter `submit_or_pause`
  behavior; that path always returns `review_ready`.
- **Manual controlled submission is job-type-agnostic.** A `review_ready`
  job — generic or ATS, not only Siemens — MAY be submitted manually
  through the `live-submit` CLI or the submission API, and only when ALL
  of these gates pass:
  - `UAA_ENABLE_REAL_SUBMISSION=true`
  - the current snapshot is explicitly approved (hash + form fingerprint
    match)
  - high-risk fields are explicitly confirmed
  - no pending intervention, stale snapshot, or duplicate gate blocks it
- **Siemens is the only trusted adapter path** for adapter-driven submit
  (`is_trusted=True`), but it is not the only job type supported by the
  manually approved controlled submission route.
- **Default behavior remains no submission.** No adapter and no API/CLI
  path submits without the approved-snapshot gates above.

## Known gaps / limitations

- **Post-submit status transition (WQ-1):** implemented and merged at
  `3ddc4be`. `ApplicationJob.status` now transitions to
  `submitted`/`applied`/`needs_review` from the persisted submission result;
  the dashboard reflects the effective post-submit state. Duplicate
  prevention blocks resubmission via the gates.
- **Queue import (WQ-3) merged to `main`.** Durable opt-in import,
  `POST/GET /api/queue/{import,status}`, CLI, and dashboard card are
  implemented on `main`. Auto-wiring `run_all` to produce + import is still
  future work (see operational gaps).
- Live re-check of login state is user-required; UAA never bypasses login,
  CAPTCHA, SSO, or payment walls.
- Test suite never touches real ATS sites in default runs.

## Known operational gaps (post-roadmap production integration)

These are integration gaps to complete when UAA and JobHunter run together
in production; none of them are unimplemented core phases. See
`docs/NEXT_WORKPACKAGES.md` for the corresponding workpackages.

1. **JobHunter export is not auto-invoked by `run_all`** — the queue must be
   produced, then imported; the wired trigger does not exist yet (WQ-2).
2. **UAA queue import requires explicit configuration.** `UAA_QUEUE_PATH` +
   opt-in `UAA_IMPORT_QUEUE_ON_STARTUP` (or the API/CLI) are implemented
   (WQ-3); wiring `run_all` to produce + import automatically is not yet done.
3. **Dashboard `/api/pipeline/start` is synchronous, fixture/planning-only.**
   It does not run the live-dry-run / live-submit execution paths; those
   remain separate CLI-driven flows. Queue import (WQ-3) explicitly does NOT
   start the pipeline.
4. **Cross-repository orchestration controls are merged (WQ-6); parallel
   producing is not yet exercised.** Sequential/parallel JobHunter export →
   UAA import → UAA pipeline orchestration with durable run state is on
   `main`; real concurrent scan/apply production use still awaits the real
   field-mutation/upload stage.
5. **Real-ATS synthetic field mutation/upload is proven; real submission is
   not.** WQ-7C (accepted and merged via PR #15) proved synthetic field
   typing + approved synthetic document upload on real public forms
   (Greenhouse, Lever, and the full-system Robco/Ashby trace) stopping
   pre-submit with `submitted=false` and an all-zero submit interlock. Real
   **final submit** remains only for the owner-approved staged plan on the
   user's machine (WQ-8, not started); no real application has ever been
   submitted from CI, the sandbox, or the synthetic proof runs.
6. **Queue-import concurrency lock is process-local.** The
   `QueueImportService` uses a `threading.Lock` stored on
   `app.state.queue_import_service`. This is safe for the current
   local single-process deployment (one Uvicorn worker). Multiple
   Uvicorn workers or separate UAA processes do not share this lock,
   so concurrent imports from different processes are not rejected.
   Multi-process deployment would require database-backed or
   distributed locking.
7. **Not proven: production operation with real candidate data.** Only
   synthetic identities/documents were used in WQ-7C; owner-validated real
   candidate data handling is future hardening, not demonstrated.
8. **Not proven: broad long-run reliability across many jobs / ATS
   variants.** WQ-7C exercised a bounded set of real platforms/forms; burst
   reliability and ATS runtime drift are future work (WQ-9 hardening).
9. **Field-resolution/embedding optimization is deliberately deferred.**
   WQ-7C used deterministic label allowlisting + strict value-source gating;
   no embeddings were added and no mapper optimization was performed.

## Environment

- Windows reference; Python 3.12 reference, 3.11+ and 3.14 verified in CI.
- Pinned dependencies in `pyproject.toml` (exact versions).
- `.uaa_data/` is the local data directory (git-ignored).
- `scripts/*.ps1` / `scripts/*.sh` for setup, run, test, verify.
