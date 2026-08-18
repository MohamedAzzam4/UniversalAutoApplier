# Active Workpackage — WQ-7C (IN PROGRESS)

- **WP ID:** WQ-7C — Controlled Synthetic ATS Mutation + End-to-End Vertical Slice.
- **Status:** IN PROGRESS (implementation `417ce97` + CLI-dispatch defect fix
  `e838039` + live-proof detector fixes `fd155f6`/`aae24d0`/`162233d` pushed;
  live real-ATS mutation proof on Greenhouse + Lever DONE, **vertical slice
  (milestone 9) DONE at `6462402`**, evidence finalization + PR pending).
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
- **Prior checked-in milestone:** Live real-ATS synthetic mutation proof
  (pre-submit). Two UAA detector defects found during the proof and fixed with
  hermetic regressions + full gates + push before continuing:
  1. **Greenhouse invisible reCAPTCHA badge** — every Greenhouse board renders a
     fixed off-screen `grecaptcha-badge` (`size=invisible`); Playwright reports
     it visible and its anchor-frame body text ("protected by reCAPTCHA")
     matched `_CAPTCHA_TERMS`, so **every** Greenhouse form was misclassified
     `captcha_detected`. Fixed in two commits (`fd155f6`, `aae24d0`):
     captcha widget selector excludes `size=invisible` iframes, and
     `analyze_page` skips text from anti-bot widget frames.
  2. **Lever `cards[...]` payment false positive** — Lever names form sections
     `name="cards[<uuid>][fieldN]"`; `input[name*='card' i]` matched and
     reported `payment_required`. Fixed (`162233d`): payment selector excludes
     `cards[` array-group convention (`:not([name*='cards[' i])`).
  Both defects were reproduced, fixed minimally, regression-tested hermetically
  (fixtures + playwright tests, keeping genuine captcha/payment blockers
  intact), and pushed; local HEAD == origin HEAD each time.
  Live proof results (both pre-submit, `submitted=false`): Greenhouse Carta
  (19 fields planned / 10 filled, 1 synthetic CV upload, 2-pass plan chain,
  stopped `required_fields_unresolved`); Lever Apply Digital (21 fields filled,
  2 synthetic CV uploads, 2-pass plan chain, interlock blocked 1 Lever-page
  `form.submit()`, stopped `required_fields_unresolved`). Anthropic Greenhouse
  target superseded by Carta after Defect 1. Evidence manifest:
  `docs/evidence/wq-7c/MANIFEST.md`.
- **Last checked-in milestone:** End-to-end vertical slice (milestone 9) — DONE
  at `6462402` (pushed, local==origin==`64624026a2507d84b024a5024820335155c8fe2e`).
  Unchanged JobHunter evaluated the real Carta Greenhouse posting via its normal
  pipeline (`data/pipeline.md` → `run_evaluate.py --next --threshold 3.0
  --german-policy accept_all`) with a fully synthetic senior-AE persona:
  **score 5/5, recommendation apply**, tailored CV + cover PDFs generated. The
  normal `run_export_queue.py` exported `data/application_queue.jsonl` with
  `application_id=869bbd6e4ab460259cceb30f8996599dd6216091f7ecada7688b64cd9278d485`
  (`platform=greenhouse`, `external_job_id=null` → canonical-URL identity).
  A UAA-side opt-in `queue-import --synthetic-mutation` stamps
  `synthetic_test`/`wq7_synthetic` onto `metadata.candidate_profile` ONLY when
  the snapshot already matches the WQ-7C synthetic identity (Test Candidate /
  test.candidate@example.com); any other row is refused. Import in a temp data
  dir (`uaa_data`) produced the **same application_id** in UAA's DB with both
  markers present, status `ready_to_apply`. `live-synthetic-mutation --headless`
  against the Carta Greenhouse form reached the ATS, extracted 19 fields,
  uploaded the approved synthetic CV (`input[id='resume']`), recorded
  `plan_hash=b6763fd5`, 2-pass plan chain `1101539781`, interlock installed with
  all-zero counters, **`submitted=false`**, stopped `required_fields_unresolved`
  (visa/LinkedIn/work-history/demographic never auto-answered — correct).
- **Last updated:** 2026-08-18.

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
   verify each target immediately, ≥2 platforms attempted, ≥1 completes) —
   DONE (Greenhouse Carta + Lever Apply Digital; both reached mutation and
   stopped pre-submit).
9. End-to-end vertical slice across the JobHunter process boundary (no hand-
   fabricated queue, no DB seeding, no JobHunter code changes; STOP if a
   JobHunter change would be needed).
10. Evidence under `docs/evidence/wq-7c/`, validation gates, docs updates.
11. Final PR against `main` via GitHub REST API; six CI checks green; no merge.

## Completed work

Milestone: End-to-end vertical slice across the JobHunter boundary — DONE
- Unchanged JobHunter (branch `main` `0e8ba2f9`, zero production-code edits)
  run from a synthetic workdir (`C:\Users\LOQ\AppData\Local\Temp\opencode\jh_synthetic_20260817_214734`)
  with a fully synthetic senior-AE persona (`config/profile.yml`, `cv.md`):
  wrote `data/pipeline.md` with the real Carta Greenhouse posting
  (`https://job-boards.greenhouse.io/carta/jobs/7822002003`), then
  `run_evaluate.py --next --threshold 3.0 --german-policy accept_all` →
  **score 5/5 (skills 5, education 5, location 5, language 5, growth 5),
  recommendation `apply`**, tailored CV + cover letter PDFs generated
  (weasyprint via the UAA venv; OpenRouter free model
  `nvidia/nemotron-3-ultra-550b-a55b:free`; no Google AI, Telegram degraded).
- `run_export_queue.py` → `data/application_queue.jsonl`: 1 row,
  `application_id=869bbd6e4ab4...`, `platform=greenhouse`,
  `external_job_id=null`, `company=Carta`,
  `title=Account Executive, Legal Services`, absolute artifact paths,
  `metadata.candidate_profile` = whitelisted synthetic snapshot (no markers).
- UAA opt-in (this workpackage's only code change, JobHunter untouched):
  `queue-import --synthetic-mutation` — new CLI flag + service/import plumbing
  (`cli.py`, `services/queue_import_service.py`,
  `application_queue/importer.py`) using
  `synthetic_profile.stamp_synthetic_mutation_metadata()` which stamps
  `synthetic_test`/`wq7_synthetic` ONLY when `candidate_profile` already IS
  the WQ-7C synthetic identity (full_name "Test Candidate" AND email
  "test.candidate@example.com"); any other snapshot is refused per-row.
- Import executed against a fresh temp `uaa_data`:
  `python -m universal_auto_applier queue-import --path <abs queue>
  --synthetic-mutation` → state `success`, total 1 imported 1, errors 0,
  run_id `1d4ef028`. UAA DB holds the **same application_id**
  `869bbd6e4ab4...`, status `ready_to_apply`, both synthetic markers present.
  Identity across the boundary verified: UAA
  `compute_application_id(platform='greenhouse', external_job_id=None, url=...)`
  == JobHunter's exported id.
- Live pre-submit synthetic mutation on the real Carta Greenhouse form:
  `python -m universal_auto_applier live-synthetic-mutation --application-id
  869bbd6e4ab4 --headless --timeout-ms 60000` (temp data dir,
  `UAA_LIVE_SYNTHETIC_MUTATION=true`, real submission off) → ATS reached,
  19 fields extracted, synthetic CV uploaded (`input[id='resume']`,
  "approved synthetic document"), `plan_hash=b6763fd5`, 2-pass plan chain
  (`plan_chain_hashes=2`, chain hash `1101539781`), interlock
  `installed=True blocked=0` all-zero counters, **`submitted=false`**, stopped
  `required_fields_unresolved` (`needs_user_input`). Cover-letter upload hit a
  Playwright locator timeout (hidden remix required-input shadowing) and the
  "Location (City)" field was mis-targeted to the file input; both are
  recorded as deferred field-mapping weaknesses — the run stopped safely.
  Artifacts under `uaa_data/live-runs/869bbd6e4ab4-20260818T153838773260Z/`
  (report.json, mutation-plan.json + pass-1, trace.zip, screenshots).

Milestone: Live real-ATS synthetic mutation proof (pre-submit) — DONE
- Queue/store seeding via the production CLI (`queue-import`): synthetic probe
  queue at `$env:LOCALAPPDATA\Temp\opencode\uaa_wq7c_queue\synthetic_probe_queue.jsonl`
  → `UAA_DATA_DIR=...\uaa_wq7c_data` (run_id `b032d6d0`, total 3 imported 3).
- Greenhouse Carta (`fdda3f191ee1...`, `https://job-boards.greenhouse.io/carta/jobs/7822002003`):
  `live-synthetic-mutation` → 19 fields planned / 10 filled, 1 synthetic CV
  upload, 2-pass plan chain (`plan_hash=c45c3a53`, chain `d3fbef5f`), stopped
  `required_fields_unresolved` (safe: location autocomplete, cover-letter
  upload timeout, unmapped free-text, sensitive/demographic categories never
  auto-answered), `submitted=false`, interlock all-zero.
- Lever Apply Digital (`f02ef0fb6ce1...`, `https://jobs.lever.co/applydigital/e67e06b6-48e7-471d-8050-34127416dcf8`):
  1 safe_apply click → `/apply`, 21 fields filled, 2 synthetic CV uploads,
  2-pass plan chain (`plan_hash=e3c2cfe6`, chain `6cc80e9b`), interlock blocked
  1 Lever-page programmatic `form.submit()` (same attribution as WQ-7B: page
  JS, not UAA — `uaa_submit_clicks=0`, `submit_events=0`), stopped
  `required_fields_unresolved`, `submitted=false`.
- Anthropic Greenhouse target superseded by Carta after Defect 1 (recorded in
  the evidence manifest as superseded, kept as replacement-pool target).
- Evidence manifest written: `docs/evidence/wq-7c/MANIFEST.md`.

Milestone: Live-proof detector defect fixes (commits `fd155f6`, `aae24d0`,
`162233d`, pushed, verified):
- Defect 1 (Greenhouse invisible reCAPTCHA badge → false `captcha_detected`):
  root cause = widget selector matched `size=invisible` badge iframes AND the
  badge anchor frame's "protected by reCAPTCHA" body text matched
  `_CAPTCHA_TERMS`. Fix: selector excludes invisible-badge iframes
  (`:not([src*='size=invisible'])`), `analyze_page` skips anti-bot widget-frame
  text (`_CAPTCHA_WIDGET_URL_MARKS`). Genuine `.g-recaptcha`/`.h-captcha`/
  `[data-sitekey]`/hcaptcha iframes still block.
- Defect 2 (Lever `cards[<uuid>][fieldN]` → false `payment_required`): payment
  selector now `input[name*='card' i]:not([name*='cards[' i])`; real card
  fields (`autocomplete^='cc-'`, `card_number`, `card_cvv`) still block.
- Regressions (hermetic, playwright): `invisible_recaptcha_badge.html` +
  `www.recaptcha.net/recaptcha_anchor.html` (form reaches `review_ready`),
  visible `.g-recaptcha` still blocks, `lever_cards_groups.html` reaches
  `review_ready`, `payment_wall.html` still blocks.

Milestone: CLI-dispatch defect fix (commit `e838039`, pushed, verified):
- Reproduced: `python -m universal_auto_applier live-synthetic-mutation` fell
  through to the dashboard server because `__main__.py`'s dispatch allowlist
  was stale (WQ-3 era): only `list-jobs`, `browser-session`, `live-dry-run`,
  `queue-import` were routed.
- Fix: `cli.py` now declares `CLI_COMMANDS` (single source of truth for every
  subcommand incl. `live-submit`, `live-dry-run-platforms`,
  `live-synthetic-mutation`); `__main__.py` imports it lazily and routes
  argv[0]; the dashboard bootstrap is extracted into `_run_dashboard`.
- Regression: `tests/unit/test_main_cli_dispatch.py` (9 tests) — parity
  allowlist==registered parser commands (guards future drift), every CLI
  command dispatches to `run_command` via `main()`, and empty argv reaches
  `_run_dashboard` (server path), not the CLI.
- Gate results at `e838039`: ruff check pass, ruff format pass (202 files),
  pyright 0/0/0, non-live `pytest -m "not live and not playwright"` =
  **1238 passed / 268 deselected**, playwright = **265 passed**.

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
  (`is_synthetic_identity_snapshot`, `stamp_synthetic_mutation_metadata`)
- `src/universal_auto_applier/application_queue/importer.py`
  (`import_queue_file(synthetic_mutation=...)`, `_stamp_synthetic_mutation`)
- `src/universal_auto_applier/services/queue_import_service.py`
  (`run`/`_run_import` propagate `synthetic_mutation`)
- `src/universal_auto_applier/cli.py` (`queue-import --synthetic-mutation`)
- `tests/contract/test_importer.py` (stamp/refuse/no-flag tests)
- `tests/contract/test_queue_import_service.py` (opt-in propagate/refuse)
- `tests/integration/test_queue_import_api.py` +
  `tests/integration/test_queue_import_concurrency.py` (mock signatures
  updated for the new `_run_import` param)
- `tests/unit/test_main_cli_dispatch.py` (parser flag test)
- `tests/unit/test_wq7_synthetic_profile.py` (stamp unit tests)
- `docs/evidence/wq-7c/MANIFEST.md` (NEW)
- `src/universal_auto_applier/navigator/apply_path_finder.py` (captcha widget
  selector + widget-frame text skip + payment `cards[` exclusion)
- `tests/fixtures/live_browser/invisible_recaptcha_badge.html` (NEW)
- `tests/fixtures/live_browser/www.recaptcha.net/recaptcha_anchor.html` (NEW)
- `tests/fixtures/live_browser/lever_cards_groups.html` (NEW)
- `tests/fixtures/live_browser/payment_wall.html` (NEW)
- `tests/playwright/test_live_browser_executor.py` (4 new tests)
- `src/universal_auto_applier/__main__.py` (CLI dispatch allowlist → `CLI_COMMANDS`; `_run_dashboard`)
- `src/universal_auto_applier/cli.py` (`CLI_COMMANDS`)
- `tests/unit/test_main_cli_dispatch.py` (NEW, 9 tests)
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

Full local gate (all green) at commit `6462402` (vertical slice):
- `ruff check src tests migrations` — pass.
- `ruff format --check src tests migrations` — 202 files clean.
- `pyright` — 0 errors, 0 warnings, 0 informations.
- `pytest -q` (default, non-live) — **1513 passed, 3 skipped** + 5 initial
  failures in queue-import concurrency mocks (their `_run_import` fakes did
  not accept the new third param); after updating the mock signatures:
  `tests/integration/test_queue_import_api.py` +
  `test_queue_import_concurrency.py` → **25 passed**. Target suites
  (importer, queue_import_service, cli dispatch, wq7 synthetic profile)
  → **68 passed**.
- `git diff --check` — clean; untracked `tmp_debug_status.py`,
  `tmp_debug_status/`, `tmp_final_pipeline/` preserved.

Full local gate (all green) at commit `162233d` (latest pushed milestone):
- `ruff check src tests migrations` — pass.
- `ruff format --check src tests migrations` — 202 files clean.
- `pyright` — 0 errors, 0 warnings, 0 informations.
- `pytest -p no:cacheprovider --ignore=tests/live` — **1507 passed**.
- Related suites (live_browser_executor, wq7b_recon_mode,
  wq7c_synthetic_mutation, wq7c_mutation_plan) — **48 passed**.
- Playwright suite — **267 passed** at `aae24d0` (265 baseline + 2 new).
- `git diff --check` — clean; untracked `tmp_debug_status.py`,
  `tmp_debug_status/`, `tmp_final_pipeline/` preserved.

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

- Deliver the vertical-slice cross-boundary markers as a UAA-side opt-in
  (`queue-import --synthetic-mutation`) rather than editing JobHunter —
  satisfies "JobHunter untouched" and keeps marker stamping identity-guarded.
- The synthetic persona was changed from "working student" to a senior
  Account Executive (same Test Candidate / test.candidate@example.com identity)
  so the unchanged evaluator emits `recommendation: apply` (5/5) against the
  Carta full-time senior AE JD; only the persona text changed, never UAA or
  JobHunter code, and the identity contract is unchanged.
- Field-resolution weaknesses observed in the live run (cover-letter upload
  locator timeout; "Location (City)" mis-targeted to the file input;
  "current position" mis-mapped to the eligibility question) are recorded as
  deferred — do NOT open a field-mapping workpackage now.
- Use JobHunter's normal pipeline.md-driven flow for the Carta row to get
  proper company/title (avoids the `--url` "Unknown" fallback) while still
  letting JobHunter, unchanged, do discovery-input→evaluation→tailoring→export.

- Deliver WQ-7C as a new distinct opt-in mode; recon-only (WQ-7B) is NOT
  converted into a fill mode.
- No embeddings — value allowlist + strict `candidate_profile`/`document_path`
  source gating yields the required precision; documented in milestone 6.
- Keep the WQ-7C CLI always ephemeral (never reuse saved profiles/cookies).
- Plan/hash-before-mutation contract; `generated_at` excluded from the hash so
  identical plans hash identically and the persisted plan re-verifies.
- Preserve all pre-existing untracked debug artifacts
  (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`).
- During the live proof, follow the defect policy (stop → hermetic regression →
  smallest fix → full gates → push → verify → continue) rather than working
  around detector false positives. Do not weaken real captcha/payment
  detection to make a live target succeed; exclude only the specific
  invisible-badge (`size=invisible`) and Lever `cards[` naming patterns.
- Anthropic Greenhouse target recorded as superseded by Carta (same platform,
  same blocker), not as a second failure; the ≥2-platforms proof stands.

## Blockers / risks

- Live ATS availability is externally variable (proven in WQ-7B). Targets
  verified immediately before each run.
- Vertical slice depends on the JobHunter repo running a synthetic-profile
  workflow without production-code changes; if that is impossible, WQ-7C will
  STOP and report the exact cross-repository blocker.
- Greenhouse/Lever runtimes evolve (as seen with the invisible reCAPTCHA badge
  and `cards[` naming); blocker/payment detectors are now narrow but must be
  revisited if those platforms change their markup again.

## Exact next action

1. **Evidence finalization** — add sanitized vertical-slice evidence under
   `docs/evidence/wq-7c/` (no cookies/tokens/sessions/raw HTML dumps): the
   exported queue record's application_id, the UAA query result showing the
   same application_id + markers, an artifact summary of the live run
   (plan hash, chain hash, uploads, interlock counters, `submitted=false`),
   plus the deferred field-mapping notes.
2. Update `docs/CURRENT_STATE.md`, `docs/WQ7_LOCAL_LIVE_RUN.md`,
   `docs/NEXT_WORKPACKAGES.md`.
3. Open ONE PR against `main` via GitHub REST API; wait for six CI checks on
   the final SHA; do not merge.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit what the workpackage asked for; never commit screenshots, PDFs,
  live-runs, `.uaa_data`, `.env`, browser profiles, traces, or a local
  database.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.