# Current State

Authoritative snapshot of `UniversalAutoApplier` as of the project rebaseline.
If this document contradicts any older planning doc, this document wins for
"what is implemented"; the planning doc keeps its architectural authority.

- Reference commit: `cab7a13d0e15c06ae04b4c180d11920a9e70fb97` (`main`,
  merge of PR #13 — WQ-7B real ATS navigation reconnaissance)
- Coverage: all roadmap phases 0-8, the controlled final submission
  pipeline, and WQ-1 through WQ-7B are merged to `main` via reviewed PRs.
- Branch work: WQ-2 (JobHunter queue export) lives in the JobHunter repo and
  is merged at JobHunter main `0e8ba2f`. WQ-3 (durable production queue
  import, API, startup import, dashboard Queue Import card), WQ-4 (background
  browser pipeline), WQ-5 (restart recovery), WQ-6 (cross-repo
  orchestration), WQ-7A (safe live ATS dry-run), and WQ-7B (real ATS
  navigation reconnaissance) are all merged to `main`. Real field
  mutation/upload and real submission are still later work.

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
| CI | `verify-windows-py314.yml`, `verify-linux.yml` | Windows+Python 3.14 primary; Linux matrix 3.11-3.14. |

## Status

`main` is at `cab7a13d0e15c06ae04b4c180d11920a9e70fb97` (WQ-7B real ATS
navigation reconnaissance merged via reviewed PR #13). Merge history note:
commit `2cf3f18` introduced the controlled-submission content directly onto
`main` (a local squash onto main) rather than through a reviewed PR. PR #3
(`controlled-final-submission`, head `f5b2055`) was then merged as `f7c49f7`
carrying the same tree — an empty duplicate commit. Harmless: the tree is
correct and clean; history must NOT be rewritten. From now on, every change
to `main` arrives through exactly one reviewed-PR merge — never
commit/squash to main first and also merge the PR. Latest verified:

```text
Linux  runner: 31971929393  (PR #13 / main branch, WQ-7B final head)  -> success
Windows runner: 31971929343  (PR #13 / main branch, WQ-7B final head) -> success
```

WQ-7B (real ATS navigation reconnaissance) was accepted under the
owner-approved amendment and merged via PR #13 (head `adc8c8d`, merge
`cab7a13`). Real public application forms were reached on Greenhouse and
Lever; Workday, SmartRecruiters, and iCIMS were classified as externally
gated / externally imposed unsupported conditions after permitted
replacement attempts. All six required CI checks passed on the final PR
head. JobHunter WQ-2 (atomic `application_queue.jsonl` export) is merged at
JobHunter main `0e8ba2f`.

## Test counts (reference run)

```text
1209 unit/contract/integration  passed  (WQ-7B merged head, 2026-08-16)
256  playwright                passed
1465 total                      passed
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
5. **Real ATS navigation is verified; real field mutation/upload and real
   submission are not.** WQ-7B merged real navigation reconnaissance: forms
   on Greenhouse + Lever reached, Workday/SmartRecruiters/iCIMS externally
   gated, zero typed values/uploads/submits. Real field typing, document
   upload, and final submit remain gated by the controlled test plan on the
   user's machine; no real submission has been executed from CI or a
   sandbox. (WQ-7C next proposed stage; WQ-8)
6. **Queue-import concurrency lock is process-local.** The
   `QueueImportService` uses a `threading.Lock` stored on
   `app.state.queue_import_service`. This is safe for the current
   local single-process deployment (one Uvicorn worker). Multiple
   Uvicorn workers or separate UAA processes do not share this lock,
   so concurrent imports from different processes are not rejected.
   Multi-process deployment would require database-backed or
   distributed locking.

## Environment

- Windows reference; Python 3.12 reference, 3.11+ and 3.14 verified in CI.
- Pinned dependencies in `pyproject.toml` (exact versions).
- `.uaa_data/` is the local data directory (git-ignored).
- `scripts/*.ps1` / `scripts/*.sh` for setup, run, test, verify.