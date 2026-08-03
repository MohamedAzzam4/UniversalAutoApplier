# Current State

Authoritative snapshot of `UniversalAutoApplier` as of the project rebaseline.
If this document contradicts any older planning doc, this document wins for
"what is implemented"; the planning doc keeps its architectural authority.

- Reference commit: `f7c49f7ad520b9765c2221b506960cd8b8e518bc` (`main`)
- Coverage: all roadmap phases 0-8 and the controlled final submission
  pipeline are implemented and merged to `main`.

## What is implemented

| Area | Implementation | Notes |
| --- | --- | --- |
| Core contracts | `core/models.py`, `core/statuses.py`, `core/identity.py`, `core/question_models.py` | `ApplicationJob`, `AdapterResult`, status enums, canonical URL identity. |
| Application queue | `application_queue/importer.py` | JobHunter `application_queue.jsonl` -> SQLite, idempotent upsert. |
| Persistence | SQLAlchemy 2 + Alembic, 6 migrations | tables: jobs, attempts, phase_results, interventions, answer_memories, artifacts, system_runs, submission tables. |
| API + dashboard | `api/`, `ui/static/*` | FastAPI localhost API, HTML/CSS/vanilla JS dashboard, submit view. |
| Health | `services/health_service.py`, `api/routes/health.py` | per-capability report (`api`, `store`, `worker`, `browser`, `jobhunter_queue`, `siemens_adapter`). |
| Adapters | `adapters/*` | Siemens adapter (trusted), Greenhouse, Lever, Workday, SmartRecruiters, LinkedIn Easy Apply, Generic fallback, `_ATSBase` shared base. Registry deterministic order. |
| Navigator | `navigator/page_observer.py`, `clickable_classifier.py`, `safe_explorer.py`, `apply_path_finder.py` | DOM-based observation, strict dangerous-submit blocking. |
| Form engine | `form_engine/schema_extractor.py`, `field_mapper.py`, `fill_engine.py`, `live_executor.py` | extraction, mapping, fill, live execution. |
| Interventions | `interventions/store.py`, `answer_memory.py`, `review.py`, fill/navigation bridges | intervention records, answer memory, review-before-submit gate. |
| LLM helpers | `llm/` (answer_validator, qa_service, question_classifier, question_resolver, truth_ledger) | grounded Google AI (Gemma) answer validation and question resolution; no inventing facts. |
| Browser | `browser/live_runner.py`, `live_models.py` | live browser dry-run with Playwright, evidence, traces; never clicks final submit. |
| CLI | `cli.py`, `__main__.py` | `list-jobs`, `browser-session`, `live-dry-run`, `live-submit`. |
| Submission | `submission/coordinator.py`, `execution_service.py`, `models.py`, `store.py`, `api/routes/submit.py` | approval + snapshot + gated submit; `submitted_confirmed`, `outcome_unknown`, `already_submitted` result states. |
| Orchestration | `services/pipeline_orchestrator.py`, `api/routes/pipeline.py` | safe pipeline routing, review-only default. |
| CI | `verify-windows-py314.yml`, `verify-linux.yml` | Windows+Python 3.14 primary; Linux matrix 3.11-3.14. |

## Status

`main` is at `f7c49f7ad520b9765c2221b506960cd8b8e518bc`. Merge history note:
commit `2cf3f18` introduced the controlled-submission content directly onto
`main` (a local squash onto main) rather than through a reviewed PR. PR #3
(`controlled-final-submission`, head `f5b2055`) was then merged as `f7c49f7`
carrying the same tree — an empty duplicate commit. Harmless: the tree is
correct and clean; history must NOT be rewritten. From now on, every change
to `main` arrives through exactly one reviewed-PR merge — never
commit/squash to main first and also merge the PR. Latest verified:

```text
Linux  runner: 30105080305  (main)  -> success
Windows runner: 30105128952  (main)  -> success
```

## Test counts (reference run)

```text
950 unit/contract/integration  passed
174  playwright                passed
1124 total                      passed, 1 skipped (live)
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

- **Post-submit status transition (confirmed defect, see WQ-1):** the
  submission result (`submitted_confirmed`, `outcome_unknown`) is
  recorded in the submission tables, but `ApplicationJob.status` is not
  transitioned to `SUBMITTED` / `NEEDS_REVIEW` by the submit route or the
  execution service. The dashboard queue/history therefore does not
  immediately show the effective post-submit state. Duplicate prevention
  already blocks resubmission via the gates above.
- Live re-check of login state is user-required; UAA never bypasses login,
  CAPTCHA, SSO, or payment walls.
- Test suite never touches real ATS sites in default runs.

## Known operational gaps (post-roadmap production integration)

These are integration gaps to complete when UAA and JobHunter run together
in production; none of them are unimplemented core phases. See
`docs/NEXT_WORKPACKAGES.md` for the corresponding workpackages.

1. **JobHunter export is not auto-invoked by `run_all`** — the queue must be
   produced, then imported; the wired trigger does not exist yet (WQ-2/WQ-3).
2. **UAA `import_queue_file` is not wired into production startup or the
   API.** It exists as a standalone CLI path; production startup/API have no
   automatic ingest trigger. (WQ-2)
3. **Dashboard `/api/pipeline/start` is synchronous, fixture/planning-only.**
   It does not run the live-dry-run / live-submit execution paths; those
   remain separate CLI-driven flows. (WQ-3)
4. **Cross-repository concurrency is not wired.** JobHunter scanning /
   evaluating / tailoring and UAA applying cannot yet run concurrently with
   controlled handoff between the two processes. (WQ-6)
5. **Real external ATS execution is unverified.** All real-platform behavior
   is gated by the controlled test plan on the user's machine; no real ATS
   submission has been executed from CI or a sandbox. (WQ-7/WQ-8)

## Environment

- Windows reference; Python 3.12 reference, 3.11+ and 3.14 verified in CI.
- Pinned dependencies in `pyproject.toml` (exact versions).
- `.uaa_data/` is the local data directory (git-ignored).
- `scripts/*.ps1` / `scripts/*.sh` for setup, run, test, verify.