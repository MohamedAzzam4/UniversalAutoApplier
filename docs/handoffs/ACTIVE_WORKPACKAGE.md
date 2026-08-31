# Active Workpackage — WQ-8 (IN PROGRESS)

- **WP ID:** WQ-8 — One staged, owner-approved, controlled real application
  submission.
- **Status:** **SNAPSHOT-PERSISTENCE FIX LANDED** (Phase A real run reached
  `review_ready` with all 14 interventions resolved and bridge/regression
  closure accepted by owner 2026-08-30; the snapshot-persistence production
  wiring defect that blocked re-freeze has been fixed and pushed — the
  production `create_app` lifespan now registers
  `app.state.submission_context_factory` and the observe path installs the
  Phase-A submit interlock before navigation. Hermetic regressions prove the
  official `POST /api/submit/{id}/observe` flow is reachable (non-503),
  persists a non-empty snapshot, and cannot authorize or submit. Next: on
  the user's machine, re-observe via the dashboard "Refresh Live Review"
  button, verify the persisted snapshot is non-empty (fields + documents +
  submit control), then re-freeze `review_plan_hash` via `wq8-review-packet`.
  **No real application has ever been submitted** from any UAA run (CI,
  sandbox, or proof runs); no `wq8-authorize`, no `live-submit` was ever
  run.)
- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **PR:** none yet (do NOT open/merge a WQ-8 PR without explicit owner/reviewer
  authorization).
- **Branch:** `checkpoint/wq-8-controlled-real-submission`.
- **Base SHA:** `76b2e1f166dd56398e7234c733ca24d703d0194a` — the `origin/main`
  HEAD (PR #16 merge) this branch was created from. Verify dynamically; do not
  trust an embedded SHA.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-8-controlled-real-submission
  ```

  The two resolved values must match before handoff/review.
- **Snapshot-persistence fix milestone:** commit `bff622f` pushed and verified
  local == origin (resolve dynamically for the current HEAD). Resolves the
  §2.2 defect: production lifespan registers `PlaywrightContextFactory`;
  observe path installs the interlock before `page.goto`.
- **Last updated:** 2026-08-30 (snapshot-persistence production wiring fix
  landed and pushed; ready for real re-observation on the user's machine).

## Objective

On the user's machine, execute **exactly one** manually approved real job
application submission through the complete normal workflow
(JobHunter discovery/evaluation/tailoring/export → UAA import/orchestration →
ATS navigation → field fill → real document upload → **owner-approved
single-use authorization** → ONE controlled submit), then prove the WQ-1
status transitions (`REVIEW_READY → SUBMITTED`, `APPLIED` only with a
structured ATS reference), truthful post-submit classification, sanitized
evidence, and duplicate prevention.

WQ-8 is split into two owner-controlled phases:

- **Phase A (this session's deliverable):** prepare everything — real job
  selected through the normal workflow to `review_ready`, final `review_plan_hash`
  frozen, single-use authorization designed/implemented/hermetically tested,
  full local gates green, sanitized review packet written — then **STOP**.
  Phase A ends by returning a review packet ending with
  `WQ-8 OWNER APPROVAL REQUIRED`. The agent never proceeds past this gate
  autonomously.
- **Phase B (only after explicit owner approval):** the owner issues an
  approval matching the exact `application_id` + `review_plan_hash`; the
  single-use authorization is enabled and consumed for exactly one intentional
  submit; outcome is truthfully classified; evidence is written under
  `docs/evidence/wq-8/`.

## Owner contract (authoritative for WQ-8)

Issued by the owner this session; supersedes the brief WQ-8 entry in
`docs/NEXT_WORKPACKAGES.md` and any older planning text.

### Absolute submission limit

- **Exactly ONE real application submission total for the whole WQ-8.** This
  limit is absolute. Ambiguous attempts count conservatively against the
  limit; on any ambiguity **STOP** and record `submission_unknown`
  (block automatic action). Never auto-retry.
- Phase B may only happen after explicit owner approval that matches the exact
  `application_id` AND the exact frozen `review_plan_hash`. If anything changes
  (contents, CV, application, target URL, hash, page), the approval is invalid
  and WQ-8 returns to Phase A. A generic instruction to "finish WQ-8" /
  "continue" does **NOT** authorize submission. Approval cannot be delegated
  to any AI.

### Real candidate data policy

- Only the approved real candidate profile/CV configuration may be used.
- Never fabricate candidate facts; missing information → skip /
  intervention / explicit owner input.
- **Never commit** real CV, email, phone, address, LinkedIn, GitHub, or
  artifacts to git. Committed WQ-8 evidence must be sanitized/redacted
  (placeholders + hashes for sensitive values).

### Job targeting

- ONE real, currently-open Germany Working Student / Werkstudent / Student
  Assistant role (AI/ML/Data/Python-data preferred), found via the **normal
  JobHunter workflow** (ordinary discovery/evaluation/tailoring → export).
  No fabricated queue rows; no manual score edits; **no JobHunter production
  code changes.**
- Prefer a safe, simple ATS already exercised in WQ-7C (e.g. Ashby/GH-style
  public anonymous form, no login/CAPTCHA).
- Any security wall/consent overlay/login/CAPTCHA before the form → record and
  STOP for that target (never bypass).

### Authorization design (implementation requirement)

- Tightly scoped single-use real-submission authorization.
- Bound to: `application_id`, job/company identity, target ATS URL,
  final `review_plan_hash`, CV/document SHA-256 hashes, expiration,
  one-time state.
- Default = submission forbidden. Authorization consumed immediately when
  submission initiates. **Exactly one** controlled submit path (no
  `form.submit()` / `requestSubmit()` / dispatch-event bypass).
- Do NOT remove the WQ-7 submit interlock; the interlock remains default
  armed, and the authorized submit uses the existing controlled path
  (`SubmissionCoordinator` is the single entry point that may click the final
  submit control). Unexpected ATS submit before authorization → BLOCK and STOP.
- Coexist with synthetic mode: synthetic mutation and real submission remain
  mutually exclusive.

### Post-submit classification (truthful only)

- `submitted_confirmed` / `submission_rejected` / `submission_unknown` /
  `submission_blocked_before_attempt`.
- "Button clicked" is never proof of submission — classification must come
  from authoritative evidence (confirmation page/URL, ATS reference,
  recognized success state).
- Use the existing state machine (`core/statuses.py`): `REVIEW_READY →
  SUBMITTED`; `APPLIED` only with a structured ATS reference; `outcome_unknown
  → NEEDS_REVIEW`. No new parallel state machine. DB-vs-external mismatch is a
  defect — report it.

### Hermetic tests (required BEFORE any live prep)

Real submission off by default; synthetic and real mutually exclusive; exact
`application_id` + exact `review_plan_hash` + one-time authorization
enforced; expired/wrong authorization rejected; changed CV/document/job/URL
invalidates authorization; duplicate / `submission_unknown` blocks retry;
second submit rejected; arbitrary submit bypass impossible; non-final controls
cannot consume the authorization; deterministic transitions; timeout = no
retry; default CI never submits.

### Evidence

Sanitized evidence under `docs/evidence/wq-8/`: run IDs, timestamps, job/
company, ATS, `application_id`, `review_plan_hash`, document hashes, field
categories/sources, approvals proof reference, authorization lifecycle,
submit-attempt count, classification, DB transition, duplicate protection,
screenshot hashes. Never commit real PII, cookies, browser profiles, tokens,
session storage, or raw sensitive HTML.

### Gates before Phase A stops

All local gates green (ruff check, ruff format --check, pyright, non-live
non-playwright pytest, playwright pytest, `git diff --check`), exact job in
`review_ready`, real form reached, real CV uploaded, unresolved/high-risk
handled, `review_plan_hash` frozen, duplicate/submission-history check clean,
authorization DISABLED, review packet ready.

### Git policy for WQ-8

Push checkpoints after deterministic milestones (initial handoff included);
verify local == remote; never reset/clean/rebase/amend/force-push; never push
to `main`; do NOT open/merge the WQ-8 PR without owner/reviewer authorization;
do not merge into `main` directly. Preserve untracked `tmp_debug_status.py`,
`tmp_debug_status/`, `tmp_final_pipeline/`.

### Out of scope

No WQ-9 hardening, no embeddings, no field-mapping redesign, no mass
submission. Do not start other workpackages during WQ-8.

## Existing authorization surface (verified this session)

The controlled-submission stack already provides a strong base that WQ-8 will
tighten, NOT redesign:

- `submission/models.py`: `SubmissionSnapshot` (deterministic
  `form_fingerprint` = form structure; `snapshot_hash` = full state incl.
  values, document content hashes, URL, pending interventions),
  `SubmissionApproval` (one-time, tied to `application_id` +
  `snapshot_hash`, consumed after a click, revocable), `SubmissionClaim`
  (one-time transactional lock against duplicate clicks, consumed after the
  outcome is recorded), `SubmissionResult` + `SubmissionResultState`
  (8 terminal states incl. `submitted_confirmed`, `outcome_unknown`,
  `already_submitted`, `approval_stale`, `submit_control_ambiguous`,
  `submission_not_allowed`, `validation_failed`, `blocked_user_action`).
- `submission/coordinator.py`: `SubmissionCoordinator` is the SINGLE entry
  point that clicks a final submit control; gates are feature kill switch
  (`settings.enable_real_submission`), active approval, snapshot hash + form
  fingerprint match, no pending interventions, no unresolved required fields,
  no unconfirmed high-risk answers, exactly one unambiguous visible/enabled
  submit control, no unconsumed claim, no consumed `outcome_unknown`, job not
  already submitted/applied, browser still on approved `application_url`;
  pre-submit screenshot → click ONCE → bounded confirmation window →
  post-submit evidence → classify → consume claim+approval → record result.
- `submission/store.py` + `persistence/models.py`: `SubmissionApprovalRow`,
  `SubmissionClaimRow`, `SubmissionResultRow`; compare-and-set one-time
  approval consumption, transactional claim acquisition, idempotent result
  recording, revocation.
- `submission/execution_service.py`: shared controlled-submission entry used
  by CLI `live-submit` and `POST /api/submit/{id}/submit`;
  `BrowserContextFactory` dependency injection (Playwright default).
- `submission/status_transitions.py`: WQ-1 policy — `submitted_confirmed →
  SUBMITTED`; `+` structured `ats_reference_id → APPLIED`; `outcome_unknown →
  NEEDS_REVIEW`; other states no transition.
- `core/statuses.py`: `ApplicationStatus` enum incl. `REVIEW_READY`,
  `SUBMITTED`, `NEEDS_REVIEW`, `APPLIED`; `TERMINAL_STATUSES`; allowed
  transitions.
- `browser/submit_interlock.py`: WQ-7 init-script interlock — capture-phase
  submit blocking, `form.submit()`/`requestSubmit()` overrides,
  `__wq7_counters` (incl. `navigation_attempts`, `uaa_submit_clicks`,
  `submit_events`). To be kept armed; the authorized WQ-8 submit path must
  coexist explicitly.

### WQ-8 gap notes (to be closed in the implementation milestone)

1. Approvals are matched by `snapshot_hash` + `application_id` today; WQ-8
   requires the approval to additionally be bound to the frozen
   `review_plan_hash` and to explicit CV/document SHA-256 hashes. The
   `review_plan_hash` itself (final plan covering application_id, job target,
   ATS target, planned answers/sources/options, submit control identity,
   document hashes) is not currently persisted/compared.
2. No expiration on approvals today (contract requires expiry + one-time).
3. No absolute "one real submission ever" registry beyond per-job
   status/claim/approval gates; WQ-8 must make the total one-submission limit
   explicit and auditable (submit-attempt counter) and stop on any ambiguity.
4. Classification vocab: contract terms `submitted_confirmed`,
   `submission_rejected`, `submission_unknown`, `submission_blocked_before_attempt`
   must map truthfully to the persisted `SubmissionResultState` values
   (existing `submitted_confirmed`, `validation_failed`, `outcome_unknown`,
   and the no-click blocked gates respectively).
5. `UAA_ENABLE_REAL_SUBMISSION` exists as the kill switch; the WQ-8
   authorization must be an additional, tighter control (single-use, bound to
   `application_id` + `review_plan_hash` + URL + doc hashes + expiry), enabled
   only for Phase B by the owner.

## Planned milestones

1. Initial checkpoint (branch + WQ-8 handoff) — DONE (this commit).
2. Finalize WQ-8 authorization design against the verified submission stack —
   DONE (`docs/evidence/wq-8/DESIGN.md`, `bc1f8eb`).
3. Implement single-use authorization (application_id + review_plan_hash +
   URL + doc hashes + expiry; consumed on initiation; default forbidden;
   synthetic/real exclusivity kept) — DONE (`431dc07`).
4. Hermetic tests proving every WQ-8 safety property above — DONE (18 unit +
   7 interlock, all green).
5. Full local gate (ruff, pyright, pytest non-live/non-playwright, playwright,
   git diff --check); push deterministic milestone; verify local==origin —
   DONE (`431dc07`, local==origin verified).
6. Phase A live prep: choose target via normal JobHunter workflow; UAA through
   to `review_ready` on the real form; real CV uploaded; handle
   unresolved/high-risk; freeze `review_plan_hash`; duplicate check; authorize
   disabled.
7. Sanitized review packet (see contract); commit nothing sensitive.
8. STOP — return `# WQ-8 Owner Review Packet` ending
   `WQ-8 OWNER APPROVAL REQUIRED` with the exact approval command
   (application_id + review_plan_hash).
9. Phase B (only with owner approval): enable authorization, exactly one
   submit, truthful classification, sanitized evidence, status transition
   verification, duplicate-block verification, WQ-8 report.
10. Final PR (only with owner/reviewer authorization to open/merge).

## Completed work

- Session-start protocol executed: `git fetch origin`; `origin/main` resolved
  to `76b2e1f166dd56398e7234c733ca24d703d0194a` (PR #16 post-merge-closure
  merge) and both WQ-7C commits (`2ac1e00` merge, `76b2e1f` closure) verified
  as ancestors of `origin/main`; working tree clean except preserved untracked
  `tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`.
- GitHub write auth verified via `git push --dry-run origin
  checkpoint/wq-7c-post-merge-cleanup` → "Everything up-to-date".
- Read the handoff pack: `AGENTS.md`, `docs/development/CHECKPOINT_POLICY.md`,
  `docs/handoffs/ACTIVE_WORKPACKAGE.md` (WQ-7C closed handoff),
  `docs/handoffs/WQ7_LOCAL_LIVE_RUN.md`, `docs/CURRENT_STATE.md`,
  `docs/NEXT_WORKPACKAGES.md`, `docs/generalization/IMPLEMENTATION_RULES.md`,
  `docs/generalization/TESTING_STRATEGY.md`,
  `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md`.
- Verified the submission authorization stack (see above): models, coordinator
  gates/flow, store, execution service, status transitions, statuses,
  submit interlock, config defaults.
- Branch `checkpoint/wq-8-controlled-real-submission` created from
  `origin/main` `76b2e1f`; untracked debug artifacts preserved.
- WQ-8 design finalized: `docs/evidence/wq-8/DESIGN.md` (commit `bc1f8eb`,
  pushed).
- **Implementation milestone completed** (commit `431dc07`, pushed):
  - Migration `0015_submission_authorization` + `SubmissionAuthorizationRow`;
    `tests/contract/test_migrations.py` `CURRENT_HEAD` updated.
  - `submission/authorization.py`: `MAX_TOTAL_REAL_SUBMISSIONS = 1`,
    public `CLICKED_ATTEMPT_STATES`, `compute_review_plan_hash`,
    `build_review_plan`, `compute_frozen_review_plan_hash`,
    `SubmissionAuthorization`, `make_authorization_id`.
  - `submission/authorization_store.py`: persisted single-use auth with
    absolute-limit + expiry + idempotent creation + compare-and-set consume.
  - `browser/submit_interlock.py`: one-shot browser allowance
    (`arm_authorized_submit`/`disarm_authorized_submit`, `authorized_submits`
    counter); interlock stays default armed.
  - `submission/coordinator.py`: WQ-8 gate #12 (`_check_wq8_authorization_db`),
    binding validation `_validate_wq8_binding` (→ `APPROVAL_STALE`),
    consume-before-click `_check_and_consume_wq8_authorization`,
    arm/disarm around the single click, `interlock-before/after-click.json`
    artifacts.
  - `submission/execution_service.py`: interlock installed on the controlled
    submit path only when an active authorization exists (else unchanged).
  - `cli.py`: `wq8-review-packet`, `wq8-authorize` (owner-only, exact plan
    hash, `UAA_ENABLE_REAL_SUBMISSION=true`, `review_ready`,
    converted-submission refusal, expiry), `wq8-status` (read-only).
  - Hermetic tests: `tests/unit/test_wq8_authorization.py` (18),
    `tests/playwright/test_wq8_interlock.py` (7).
- **Canonical JobHunter runtime configured for Phase A** (non-git, local only;
  nothing committed): fresh `.venv` in
  `D:\Programming\Antigravity-Projects\JobHunter` built from `requirements.txt`
  with `python-jobspy==1.1.82 --no-deps` + pinned `numpy==2.4.6`,
  `pandas==2.3.3`, `beautifulsoup4<5`, `markdownify<0.14`, `regex<2025`,
  `tls-client<2` (matches the proven operational venv in
  `D:\Programming\Antigravity-Projects\job_apply\jobhunter_venv`); `.env`
  created in the canonical repo with only the required API keys copied locally
  from the owner's existing `job_apply/.env` (OpenRouter `sk-or-*` x4 +
  `GOOGLE_AI_API_KEY`/2, no Telegram), verified gitignored before writing and
  values never printed; `data/`, `reports/`, `output/`, `logs/` created.
  Health check: python-jobspy detected, cv.md/profile.yml/portals.yml present,
  OpenRouter connectivity flagged 429 rate-limited on free tier (known WQ-7C
  issue; profile model chain `google/gemma-4-26b-a4b-it:free` etc. differs
  from `run_health.py`'s hardcoded fallbacks).
- **Remaining-shortlist inspection milestone completed** (dry-run/
  navigation-only; nothing evaluated/tailored/exported/filled/uploaded/
  submitted): all previously-unverified candidate roles inspected and
  classified (PwC, msg, Maisel, MDT, forensica, AIBE@FAU FAUstairs). Full
  detail in `docs/evidence/wq-8/TARGET_INSPECTION.md`.
- **Snapshot-persistence production wiring fix completed** (commit `bff622f`,
  pushed, local == origin verified): resolved the §2.2 defect from
  `docs/evidence/wq-8/FINAL_REVIEW_PACKET.md`. The production `create_app`
  lifespan now registers `app.state.submission_context_factory` (reusing the
  existing `PlaywrightContextFactory` — no second browser implementation,
  no browser launched at startup, honors `browser_headless`/`profile`/`channel`
  settings, preserves test/harness-injected factories). The observe path
  (`SubmissionExecutionService.observe_and_persist_snapshot`) now installs the
  established Phase-A submit interlock (`install_interlock` from
  `browser/submit_interlock`) BEFORE `page.goto` — reusing the SAME interlock
  implementation as `live_runner.py` (WQ-7) and `_execute_in_browser` (WQ-8
  controlled submit), no JS duplication. The interlock stays armed; no one-shot
  authorized-submit allowance is ever armed during observation. 18 new
  hermetic regressions added (15 in `test_wq8_snapshot_persistence.py` + 3 in
  `test_live_review_api.py`) proving: event order (context -> interlock ->
  navigation, fails if nav first); production-app observe returns non-503 with
  a non-empty snapshot; snapshot persisted and reloadable from a fresh DB
  session; pre-injected factory preserved; observation creates no
  `SubmissionAuthorization`/`SubmissionResult`/`SubmissionClaim`, creates
  exactly one unapproved approval row, does not change job status; interlock
  records zero authorized_submits and blocks all submit signals. No CLI
  snapshot persistence alternative added (canonical path repaired cleanly,
  per §5 of the workpackage).

## Snapshot-persistence sandbox closure (2026-08-30)

- **Root cause:** production `create_app` lifespan
  (`src/universal_auto_applier/api/app.py`) never registered
  `app.state.submission_context_factory`. Only test harnesses
  (`tests/harness/submission_server.py:207`, `final_pipeline_server.py:316`)
  injected a factory (`FixtureContextFactory`). In the real local deployment
  the official observe flow was unreachable dead code returning 503.
  Additionally, `SubmissionExecutionService.observe_and_persist_snapshot`
  navigated before installing the Phase-A submit interlock — unacceptable
  for WQ-8 Phase A.
- **Exact production wiring implemented:**
  - `api/app.py` lifespan: `if not getattr(app.state,
    "submission_context_factory", None): app.state.submission_context_factory
    = PlaywrightContextFactory(settings=settings, profile_dir=…,
    headless=…, channel=…)`. Factory closed in the lifespan `finally`
    (safe no-op if `create_context` was never called).
  - `submission/execution_service.py` `observe_and_persist_snapshot`:
    `install_interlock(context)` called immediately after
    `create_context()` and BEFORE `page.goto(...)`. Reuses the same
    `install_interlock` from `browser/submit_interlock` used by
    `live_runner.py` and `_execute_in_browser`.
- **Interlock-before-navigation proof:** hermetic regression
  `test_observe_installs_interlock_before_navigation` uses a stub factory
  whose context records the call order; asserts `interlock_installed`
  appears before any `goto_*` entry and that `goto_BEFORE_interlock` never
  appears. A second test
  `test_observe_never_navigates_before_interlock_even_on_failure` proves
  the order holds even when navigation fails (connection-refused URL).
- **Injected factories remain supported:** hermetic regression
  `test_pre_injected_fixture_factory_preserved` pre-injects a
  `FixtureContextFactory` before the lifespan runs and asserts the lifespan
  does NOT overwrite it. The existing test harnesses
  (`submission_server.py`, `final_pipeline_server.py`) continue to work
  unchanged.
- **Snapshot persistence regression result:** hermetic regression
  `test_observe_snapshot_reloadable_from_fresh_session` runs the real
  `create_app` lifecycle with a `FixtureContextFactory` + loopback HTTP
  fixture, POSTs `/api/submit/{id}/observe`, then opens a FRESH DB session
  and reloads the snapshot from the persisted approval row — asserts
  `snapshot_hash` matches, `application_url` matches, submit control is
  present. After the reviewer correction (see "Reviewer correction"
  section below), the fixture job now carries a synthetic
  `candidate_profile` in metadata so the official field-mapping path
  deterministically fills the `Resume` file field (uploading the synthetic
  CV) and the snapshot contains non-empty fields (Full Name, Email, Resume)
  and at least one document (the CV with a SHA-256-derived content hash).
- **Test counts:** `ruff check` 0 errors; `ruff format --check` 0 errors;
  `pyright` 0 errors; `pytest tests/unit tests/contract tests/integration
  -m "not playwright and not live"` → **1343 passed**; relevant Playwright
  tests: `test_wq8_interlock.py` 7 passed, `test_wq7_production_safety.py`
  23 passed, `test_controlled_submission.py` 5 passed,
  `test_wq8_phase_a_interlock.py` 5 passed individually. `git diff --check`
  clean. (Pre-existing cross-file Playwright greenlet isolation issue
  between `test_wq8_interlock.py` and `test_wq8_phase_a_interlock.py` when
  run in the same session — confirmed present on the clean `09816c5`
  checkout before this fix; not caused by this change.)
- **Final commit SHA:** `bff622f2054ffd55d43f03d5c069f18021c0c4d8`
  (resolve dynamically for current HEAD).
- **local == origin:** YES — `git rev-parse HEAD` ==
  `git rev-parse origin/checkpoint/wq-8-controlled-real-submission` ==
  `bff622f…` after push.
- **Safety invariants preserved:** `UAA_ENABLE_REAL_SUBMISSION=false`
  default unchanged; no `SubmissionAuthorization` created by observation;
  no final submit; no real ATS requests from sandbox; no owner PII
  committed; no Phase-B authorization semantics touched; no weakening of
  snapshot/hash binding.

## Reviewer correction (2026-08-31)

Three reviewer findings on the initial snapshot-persistence fix were
corrected in this follow-up commit:

- **Finding 1 (document-hash false positive):** the previous
  `test_observe_snapshot_has_document_content_hash` only checked that the
  `documents` field existed and explicitly allowed it to be empty — a false
  positive. Corrected: the fixture job now carries a synthetic
  `candidate_profile` in metadata so the official field-mapping path
  deterministically uploads the synthetic CV via the `Resume` file input
  (label matches the `resume` pattern in `_FILE_FIELD_PATTERNS`). The test
  now asserts `len(documents) > 0`, `document_kind == "cv"`,
  `content_hash` is non-empty, and `content_hash` equals the expected
  `sha256(cv.pdf bytes)[:32]` per the canonical
  `submission/models.py:build_snapshot_from_report` implementation. A new
  `test_observe_document_hash_persists_across_fresh_db_session` proves the
  same document + hash survives a fresh DB session reload.

- **Finding 2 (non-empty fields not asserted):** the previous
  `test_observe_persists_non_empty_snapshot` only checked `snapshot_hash`
  and `submit_control` — it did NOT prove fields were non-empty. Corrected:
  the test (renamed to
  `test_observe_persists_non_empty_snapshot_with_fields`) now asserts
  `len(fields) > 0`, expected fixture fields (Full Name, Email, Resume) are
  represented, the Resume file field has `status == "filled"` with a
  non-empty `filled_value` (the synthetic CV path). The
  `test_observe_snapshot_reloadable_from_fresh_session` test now also
  asserts the reloaded snapshot still contains the non-empty fields.

- **Finding 3 (lifespan factory ownership mismatch):** the previous
  lifespan `finally` block closed ANY `app.state.submission_context_factory`,
  including a pre-injected factory — violating ownership semantics.
  Corrected: the lifespan now tracks `_owns_factory` (exactly like
  `_owns_engine`) and closes the factory on shutdown ONLY if the lifespan
  created it. A new `TestLifespanFactoryOwnership` class with a
  `_CloseCountingFactory` sentinel proves: (a) a pre-injected factory is
  preserved and `close()` is NOT called by the lifespan; (b) a
  production-created `PlaywrightContextFactory` IS closed safely on
  lifespan shutdown (no exception, no ResourceWarning leak under the
  project's strict `filterwarnings = ["error"]` config).

- **Test counts after correction:** `ruff check` 0 errors; `ruff format
  --check` 0 errors; `pyright` 0 errors; `pytest tests/unit tests/contract
  tests/integration -m "not playwright and not live"` → **1346 passed**
  (was 1343; +3 new tests); relevant Playwright: `test_wq8_interlock.py`
  7, `test_wq7_production_safety.py` 23, `test_controlled_submission.py`
  5, `test_wq8_phase_a_interlock.py` 5 individually. `git diff --check`
  clean.

## Changed files

- `docs/handoffs/ACTIVE_WORKPACKAGE.md` — WQ-8 handoff (initial commit
  `eae9569b`).
- `docs/evidence/wq-8/DESIGN.md` — WQ-8 authorization design (commit
  `bc1f8eb`).
- `migrations/versions/0015_submission_authorization.py` — NEW
  `submission_authorizations` table.
- `src/universal_auto_applier/submission/authorization.py` — NEW.
- `src/universal_auto_applier/submission/authorization_store.py` — NEW.
- `src/universal_auto_applier/persistence/models.py` —
  `SubmissionAuthorizationRow`.
- `src/universal_auto_applier/browser/submit_interlock.py` — one-shot
  authorized-allowance.
- `src/universal_auto_applier/submission/coordinator.py` — WQ-8 gate + bind/
  consume/arm/disarm wiring.
- `src/universal_auto_applier/submission/execution_service.py` — conditional
  interlock install.
- `src/universal_auto_applier/cli.py` — `wq8-review-packet`/`wq8-authorize`/
  `wq8-status`.
- `tests/contract/test_migrations.py` — `CURRENT_HEAD` → `0015`.
- `tests/unit/test_wq8_authorization.py` — NEW (18 tests).
- `tests/playwright/test_wq8_interlock.py` — NEW (7 tests).
- `docs/evidence/wq-8/TARGET_INSPECTION.md` — NEW per-role inspection matrix
  (all 12 shortlist roles inspected; ATS, login/CAPTCHA status, form gates,
  role-fit notes).
- `src/universal_auto_applier/api/app.py` — WQ-8 snapshot-persistence fix:
  lifespan registers `PlaywrightContextFactory` when no factory pre-injected;
  factory closed in `finally` (commit `bff622f`).
- `src/universal_auto_applier/submission/execution_service.py` — WQ-8
  snapshot-persistence fix: `observe_and_persist_snapshot` installs the
  submit interlock BEFORE `page.goto` (commit `bff622f`).
- `tests/integration/test_wq8_snapshot_persistence.py` — NEW (15 tests):
  event-order, production-app observe, persistence, factory preservation,
  observation-cannot-authorize-or-submit regressions (commit `bff622f`).
- `tests/integration/test_live_review_api.py` — updated
  `test_observe_without_context_factory` for new contract; added
  `test_production_lifespan_registers_factory` and
  `test_pre_injected_factory_preserved_by_lifespan` (commit `bff622f`).

## Tests and exact results

- `ruff check src tests migrations` → 0 errors.
- `ruff format --check src tests migrations` → 0 errors.
- `pyright` (project config `include = ["src/universal_auto_applier"]`) → 0
  errors.
- `pytest -m "not live and not playwright"` → **1261 passed** (272
  deselected).
- `pytest tests/playwright` → **276 passed**.
- `tests/contract/test_migrations.py` → **13 passed** (head
  `0015_submission_authorization`).
- `tests/unit/test_wq8_authorization.py` → **18 passed**.
- `tests/playwright/test_wq8_interlock.py` → **7 passed**.
- `git diff --check` clean; commit `431dc07` contains only WQ-8 files (6
  modified + 5 new; +2291/−20).
- **Snapshot-persistence fix (commit `bff622f`):** `ruff check` 0 errors;
  `ruff format --check` 0 errors; `pyright` 0 errors; `pytest tests/unit
  tests/contract tests/integration -m "not playwright and not live"` →
  **1343 passed**; `tests/integration/test_wq8_snapshot_persistence.py` →
  **15 passed**; relevant Playwright: `test_wq8_interlock.py` 7 passed,
  `test_wq7_production_safety.py` 23 passed, `test_controlled_submission.py`
  5 passed, `test_wq8_phase_a_interlock.py` 5 passed individually; `git
  diff --check` clean.

## Decisions made

- Reuse (not redesign) the existing controlled-submission stack as the WQ-8
  authorization base; the new work tightens binding to `review_plan_hash`
  + doc hashes + expiry + absolute one-submission audit rather than
  introducing a second submission pathway.
- Keep the WQ-7 submit interlock armed; the authorized submit goes through the
  existing single `SubmissionCoordinator` click path, using a one-shot
  browser allowance armed immediately before the click and disarmed in
  `finally` (never a bypass of the interlock).
- `review_plan_hash` canonicalized (sort_keys, strip `generated_at`,
  field_token/document sort, machine-independent paths) so identical plans
  hash identically; validated at authorize AND submit.
- Script-driven `form.submit()`/`requestSubmit()` remain defense-in-depth
  over-blocked; the coordinator's real click is the ONLY signal reaching the
  page handler (verified in Playwright).
- Auth consumption happens on the held page before the click; gate failures
  consume the claim (if locally acquired) so no retry path exists.
- WQ-8 is a two-phase owner-controlled workpackage; the agent stops at the
  Phase A gate and never self-approves Phase B.
- Do not modify JobHunter; the real target must arise from its normal
  workflow.
- Verify SHAs dynamically at every checkpoint; never trust an embedded SHA.
- **Snapshot-persistence fix:** reuse the existing `PlaywrightContextFactory`
  (no second browser implementation); register it in the production lifespan
  only when no test/harness factory was pre-injected (preserves dependency
  injection). Do NOT invent a new environment flag — the observe flow is a
  review-mode feature available in all modes; the actual submit remains gated
  by `enable_real_submission` + approval + interlock. Reuse the SAME
  `install_interlock` from `browser/submit_interlock` (no JS duplication) —
  install it before `page.goto` in the observe path, exactly as
  `live_runner.py` and `_execute_in_browser` do. No CLI snapshot persistence
  alternative added (canonical path repaired cleanly, per §5 of the
  workpackage).

## Blockers / risks

- **Phase A live prep depends on external real resources:** OpenRouter free
  tier (50 free model requests/day) consumed at times during WQ-7C (HTTP 429);
  real JobHunter discovery + evaluation will be needed to find the real
  target. If the quota is exhausted, evaluation/export may need to wait or use
  the owner's key.
- Real ATS availability changes (proven in WQ-7A/B); every live target must be
  verified immediately before a run.
- Phase B is blocked by design on the owner's explicit matched approval.
- The owner's real candidate CV/profile must be present and correct in the
  normal config; missing facts → skip/intervention, never fabricated.
- The implementation milestone (`431dc07`) is pushed and verified == origin;
  no further risk there.
- The snapshot-persistence fix (`bff622f`) is pushed and verified == origin;
  the §2.2 defect is resolved. The only remaining blocker for Phase A
  re-freeze is executing the real re-observation on the user's machine
  (cannot be done from the sandbox — no real ATS navigation, no owner PII).

## Exact next action

1. **Snapshot-persistence fix is landed (`bff622f`, pushed, local == origin).**
   The §2.2 defect from `docs/evidence/wq-8/FINAL_REVIEW_PACKET.md` is
   resolved: the production `create_app` lifespan registers
   `app.state.submission_context_factory`, and the observe path installs the
   Phase-A submit interlock before navigation.
2. On the user's machine: start the local server (`./scripts/run_local.sh`),
   open the dashboard, and click "Refresh Live Review" on the msg 411 job
   (`fd9a41480fc6…`). This will now succeed (non-503) and persist a
   non-empty snapshot (fields + documents + submit control) — replacing the
   stale empty snapshot (`ed5241a7…`).
3. Verify the persisted snapshot is non-empty via `GET /api/submit/{id}/status`
   or `wq8-status --application-id <id>`: confirm `snapshot_hash` is set,
   `submit_control` is present, `fields` is non-empty, `documents` carry
   content hashes.
4. Re-freeze `review_plan_hash` via `wq8-review-packet --application-id <id>`.
5. Proceed to the `WQ-8 OWNER APPROVAL REQUIRED` gate (Phase A stop). Never
   self-approve Phase B.

Phase B (owner-gated): `wq8-authorize --application-id <id>
--review-plan-hash <frozen> --expires-in-hours <n> --confirm` then the
UNCHANGED `live-submit --approval-id <id>`, truthful classification, sanitized
evidence under `docs/evidence/wq-8/`, no final PR without owner authorization.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit what the workpackage asked for; never commit screenshots, PDFs,
  live-runs, `.uaa_data`, `.env`, browser profiles, traces, real candidate
  PII, or a local database.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.