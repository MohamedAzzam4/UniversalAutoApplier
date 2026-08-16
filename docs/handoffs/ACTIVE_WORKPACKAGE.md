# Active Workpackage — WQ-7C (IN PROGRESS)

- **WP ID:** WQ-7C — Controlled Synthetic ATS Mutation + End-to-End Vertical Slice.
- **Status:** IN PROGRESS (initial checkpoint pushed; implementation underway).
- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **PR:** none yet (one PR against `main` will be opened at the end via GitHub
  REST API; no local `gh` shim; do not merge).
- **Branch:** `checkpoint/wq-7c-synthetic-mutation`.
- **Base SHA:** resolve dynamically (see command block below). The branch was
  created from the exact `origin/main` at WQ-7C start — the PR #14 merge
  `b5e1532f763b5c5f4e86d36061d7f175158415c8` (WQ-7B post-merge closure), with
  the WQ-7B closure commit `5b498c863d2df7a57d9a706521cf76a8d876bae8` and
  the PR #13 WQ-7B implementation merge `cab7a13` as ancestors.
- **Prerequisites verified at start:** origin/main contained merged PR #13
  (WQ-7B implementation, merge `cab7a13`) and the merged post-merge closure
  PR #14 (commit `5b498c8`) as an ancestor; `ACTIVE_WORKPACKAGE.md` showed
  WQ-7B `MERGED/COMPLETE` and `WQ-7C: NOT started`. Verified via
  `git merge-base --is-ancestor 5b498c8 origin/main` → true.
- **Last completed/checkpoint SHA:** resolve dynamically (see command block).
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7c-synthetic-mutation
  ```

  The two resolved values must match before handoff/review.
- **Last checked-in milestone:** implementation + hermetic tests checkpoint
  (`417ce97`, pushed; local HEAD == origin HEAD).
- **Last updated:** 2026-08-17.

## Objective

Prove that UniversalAutoApplier can safely perform REAL browser mutation on a
REAL public ATS form using ONLY synthetic candidate data and synthetic
documents, while making final application submission technically impossible.
Also prove at least one end-to-end vertical slice:

`JobHunter → real job discovery/evaluation → tailoring → synthetic tailored CV →
application_queue.jsonl → UAA queue import → UAA orchestration → real ATS
navigation → schema extraction → field resolution → synthetic field fill →
synthetic document upload → STOP BEFORE SUBMISSION`.

WQ-7C is NOT a real application-submission workpackage.

## Synthetic-only policy (non-negotiable)

- Synthetic text entry, select/radio/checkbox selection, and synthetic document
  upload are allowed.
- **Forbidden:** final submit, review-and-submit, application confirmation,
  account creation, login, SSO, CAPTCHA solving/bypass, anti-bot bypass,
  authenticated LinkedIn/Easy Apply, use of any real candidate data, cookies/
  profile/session reuse, weakening any submit interlock, disabling blocker
  detection to make a live run succeed, modifying a third-party page to bypass
  its safety state.
- If CAPTCHA, login, account creation, or a security wall is present BEFORE
  mutation: DO NOT FILL. Record the blocker and stop.
- Recon-only logic that may observe a form containing a blocker must NOT be
  reused as justification to fill that form.

## Submit prohibition

- No code path in this workpackage may click a final submit, call
  `form.submit()` / `requestSubmit()`, dispatch a synthetic submit event, or
  perform an application-completion navigation.
- The mutation run installs and verifies the browser-side submit interlock
  BEFORE the first field mutation and keeps it armed throughout.
- Any unexpected submission signal → block it, stop immediately, capture
  evidence, classify the run.

## Planned milestones

1. Initial checkpoint (branch + handoff) — DONE.
2. Exploration of production modules + WQ-7A/B infra — DONE.
3. Synthetic identity contract + approved synthetic documents — DONE (commit
   `417ce97`).
4. Opt-in synthetic-mutation mode (`UAA_LIVE_SYNTHETIC_MUTATION`), config,
   incompatibility with real submission, ephemeral browser, mutation budget and
   interlock evidence — DONE (commit `417ce97`).
5. Pre-mutation machine-readable plan (frozen/hashed) + field-resolution
   correctness gate — DONE (commit `417ce97`).
6. Field-mapper/embedding decision: NOT needed — the value allowlist + source
   gating (`candidate_profile`/`document_path` only) gives precision without
   embeddings; revisit only if a labelled-gap benchmark proves otherwise.
7. Local/hermetic tests (unit + playwright fixture) proving every safety
   requirement from the WQ-7C contract — DONE (commit `417ce97`).
8. Live real-ATS mutation proof on currently-open public forms (target policy,
   verify each target immediately, ≥2 platforms attempted, ≥1 completes).
9. End-to-end vertical slice across the JobHunter process boundary (no hand-
   fabricated queue, no DB seeding, no JobHunter code changes; STOP if a
   JobHunter change would be needed).
10. Evidence under `docs/evidence/wq-7c/`, validation gates, docs updates.
11. Final PR against `main` via GitHub REST API; six CI checks green; no merge.

## Completed work

Implementation milestone shipped as commit `417ce97` (pushed, verified):
- `synthetic_profile.py`: `SyntheticMutationProfile` (Test/Candidate,
  `test.candidate@example.com`, `+1 555 0199`, empty linkedin, both synthetic
  markers), `SYNTHETIC_MUTATION_BANNER`-labelled CV/cover PDFs,
  `sha256_file`/`approved_document_hashes`, `is_synthetic_metadata`,
  `to_candidate_profile` (linkedin stays None), `__all__` updated.
- `config.py`: `live_synthetic_mutation` (default False) +
  `synthetic_mutation_max_mutations` (default 60, 1..200); `model_validator`
  rejects mutation+real-submission and mutation+recon-only at load; both env
  vars parsed by `load_settings`.
- `browser/mutation_plan.py` (NEW): frozen `MutationPlan`/`MutationPlanEntry`
  with `plan_hash` (SHA-256 of canonical JSON, `generated_at` excluded);
  `build_mutation_plan()` gates — mutate/skip/block/intervention decisions,
  `_NEVER_MUTATE_CATEGORIES` skip (legal_declaration, consent_signature,
  demographic_sensitive, work_authorization, availability), value allowlist
  `_declared_synthetic_values` (bools only as exact declared Yes/No),
  `_value_fits_options`/`_normalize_option` guard (mapped "5" never fills a
  Yes/No radio), confidence < 0.7 skipped, unapproved/unpresent doc blocked,
  missing-required → `needs_intervention` ("not fabricated").
- `form_engine/live_executor.py`: `SyntheticMutationExecution` +
  `execute_live_form_synthetic`/`_run_mutation_pass` (plan frozen+hashed BEFORE
  mutation, budget consumed per mutation, doc hash re-verified at execution,
  typed-answer validation, one bounded re-observation pass only if budget
  remains). Circular import (form_engine→live_executor→mutation_plan→
  field_mapper→form_engine) resolved with TYPE_CHECKING + lazy import.
- `browser/live_runner.py`: `run_synthetic_mutation` (refuses non-synthetic
  profile via getattr markers, refuses `hard_submit_block=False`),
  `run_in_context_synthetic` made public (artifact_dir param) for production-
  path tests; interlock armed BEFORE mutation, `mutation-plan.json` persisted,
  plan_hash recorded, stops at `final_submit_detected` (review_ready), reads
  interlock counters into errors, `submitted=False` always.
- `browser/live_models.py`: `LiveRunReport` += `plan_hash`,
  `mutation_plan_path`.
- `cli.py`: `live-synthetic-mutation` subcommand + `_live_synthetic_mutation`
  handler (refuses when mode off exit 2; refuses non-synthetic job metadata;
  generates synthetic docs under `data_dir/synthetic-docs`; ephemeral profile
  always; `hard_submit_block=True`; budget clamped to config; overrides job
  `cv_pdf`/`cover_letter_pdf`; exits 0 review_ready / 3 needs_user_input /
  1 submitted / 2 error).
- `tests/unit/test_wq7c_mutation_plan.py` (NEW, 15 tests) + `test_config.py`
  additions (mode/budget/conflicts).
- `tests/playwright/test_wq7c_synthetic_mutation.py` (NEW, 7 tests) using the
  production `run_in_context_synthetic` path over Hygiene/Hydro served
  greenhouse/lever apply fixtures: synthetic identity only, approved-doc upload
  only (hash membership), plan frozen+hashed+re-verifiable, interlock armed /
  zero submit attempts, stops at final submit without submitting, refuses
  non-synthetic profile and disarmable-interlock config.

## Changed files

- `src/universal_auto_applier/synthetic_profile.py`
- `src/universal_auto_applier/config.py`
- `src/universal_auto_applier/browser/mutation_plan.py` (NEW)
- `src/universal_auto_applier/browser/live_models.py`
- `src/universal_auto_applier/form_engine/live_executor.py`
- `src/universal_auto_applier/browser/live_runner.py`
- `src/universal_auto_applier/cli.py`
- `tests/unit/test_wq7c_mutation_plan.py` (NEW)
- `tests/unit/test_config.py`
- `tests/playwright/test_wq7c_synthetic_mutation.py` (NEW)

## Tests and exact results

Full local gate (all green) at commit `417ce97`:
- `ruff check src tests migrations` — pass.
- `ruff format --check src tests migrations` — 201 files clean (3 reformatted).
- `pyright` — 0 errors, 0 warnings, 0 informations.
- `pytest -m "not live and not playwright"` — **1229 passed**, 266 deselected
  (baseline was 1209).
- `pytest tests/playwright` — **263 passed** (includes 7 new WQ-7C tests).
- `git diff --check` — clean; only the 10 intended files staged; untracked
  `tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/` preserved.

## Decisions made

- Deliver WQ-7C as a new distinct opt-in mode; recon-only (WQ-7B) is NOT
  converted into a fill mode.
- No embeddings — value allowlist + strict `candidate_profile`/`document_path`
  source gating yields the required precision; documented in milestone 6.
- Keep the WQ-7C CLI always ephemeral (never reuse saved profiles/cookies).
- Plan/hash-before-mutation contract; `generated_at` excluded from the hash so
  identical plans hash identically and the persisted plan re-verifies.
- Preserve all pre-existing untracked debug artifacts
  (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`).

## Blockers / risks

- Live ATS availability is externally variable (proven in WQ-7B). Targets
  verified immediately before each run.
- Vertical slice depends on the JobHunter repo running a synthetic-profile
  workflow without production-code changes; if that is impossible, WQ-7C will
  STOP and report the exact cross-repository blocker.

## Exact next action

1. **Live real-ATS mutation proof**: re-verify ≥2 currently-open public
   Greenhouse/Lever apply URLs immediately before each run; run via the new
   CLI (`python -m universal_auto_applier live-synthetic-mutation --application-id ...
   --max-mutations 60` or directly through `run_synthetic_mutation`) with
   `UAA_LIVE_SYNTHETIC_MUTATION=true`; blocker-before-mutation = record + skip;
   ≥1 platform must complete the mutation proof; verify stop-pre-submit.
2. **Vertical slice**: JobHunter → synthetic CV → queue → import → orchestrate
   (application_id trace) without editing JobHunter production code; STOP and
   report if a JobHunter change would be needed.
3. Collect sanitized evidence under `docs/evidence/wq-7c/` (no cookies/tokens/
   sessions/raw HTML dumps).
4. Update `docs/CURRENT_STATE.md`, `docs/WQ7_LOCAL_LIVE_RUN.md`,
   `docs/NEXT_WORKPACKAGES.md`.
5. Open ONE PR against `main` via GitHub REST API; wait for six CI checks on
   the final SHA; do not merge.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit what the workpackage asked for; never commit screenshots, PDFs,
  live-runs, `.uaa_data`, `.env`, browser profiles, traces, or a local
  database.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.