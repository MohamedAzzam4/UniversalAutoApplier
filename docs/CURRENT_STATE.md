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
| LLM helpers | `llm/` (answer_validator, qa_service, question_classifier, question_resolver, truth_ledger) | grounded Gemini so questions resolve || help; no inventing facts. |
| Browser | `browser/live_runner.py`, `live_models.py` | live browser dry-run with Playwright, evidence, traces; never clicks final submit. |
| CLI | `cli.py`, `__main__.py` | `list-jobs`, `browser-session`, `live-dry-run`, `live-submit`. |
| Submission | `submission/coordinator.py`, `execution_service.py`, `models.py`, `store.py`, `api/routes/submit.py` | approval + snapshot + gated submit; `submitted_confirmed`, `outcome_unknown`, `already_submitted` result states. |
| Orchestration | `services/pipeline_orchestrator.py`, `api/routes/pipeline.py` | safe pipeline routing, review-only default. |
| CI | `verify-windows-py314.yml`, `verify-linux.yml` | Windows+Python 3.14 primary; Linux matrix 3.11-3.14. |

## Status

`main` (`f7c49f7ad520b9765c2...`) is a squash-merge of PR #3
(`controlled-final-submission`, head `f5b2055`). Latest verified:

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

## Known gaps / limitations

- The `submit` endpoint/CLI (`live-submit`) is the real-submission path, and
  it requires an explicitly approved snapshot and `UAA_ENABLE_REAL_SUBMISSION=true`
  per the controlled test plan. Generic/ATS adapters are `is_trusted=False`
  and can never submit.
- Only the trusted Siemens adapter path (`is_trusted=True`) may reach a real
  submit, and only when config + approval gates pass.
- Live re-check of login state is user-required; UAA never bypasses login,
  CAPTCHA, SSO, or payment walls.
- Test suite never touches real ATS sites in default runs.

## Environment

- Windows reference; Python 3.12 reference, 3.11+ and 3.14 verified in CI.
- Pinned dependencies in `pyproject.toml` (exact versions).
- `.uaa_data/` is the local data directory (git-ignored).
- `scripts/*.ps1` / `scripts/*.sh` for setup, run, test, verify.