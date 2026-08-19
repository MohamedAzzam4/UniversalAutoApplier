# WQ-8 Design — Single-Use Real-Submission Authorization

Status: DESIGN (milestone 2). Branch `checkpoint/wq-8-controlled-real-submission`.
Base `origin/main` `76b2e1f`. No code changes yet beyond the handoff doc.

## Goal

Exactly ONE controlled real application submission, in two owner-controlled
phases, keep the existing controlled-submission stack (approval + claim +
result + WQ-1 transitions) and the WQ-7 submit interlock, and add a tighter
single-use authorization layer. No second submit path, no bypass.

## Principle

The existing stack is reused (not redesigned):

- `SubmissionCoordinator` remains the SINGLE entry point that may click the
  final submit control (`submission/coordinator.py`).
- `live-submit` CLI + `POST /api/submit/{id}/submit` remain the ONLY submit
  surfaces.
- `SubmissionApproval` / `SubmissionClaim` / `SubmissionResult` keep their
  exact semantics (one-time approval, one-time transactional claim, terminal
  result states, WQ-1 transitions `REVIEW_READY→SUBMITTED`,
  `+ats_reference_id→APPLIED`, `outcome_unknown→NEEDS_REVIEW`).

WQ-8 adds a NEW, tighter, single-use **authorization** layer that gates the
EXISTING submit path. When present, it must be valid before the coordinator
may click; it is consumed as part of claim acquisition (before the browser
starts, "consumed immediately when submission initiates"). When absent (all
existing tests and current behavior), the coordinator is byte-for-byte
unchanged.

## New component 1 — submission authorization model + store + table

New module `src/universal_auto_applier/submission/authorization.py`:

- `SubmissionAuthorization` (Pydantic):
  - `authorization_id` — deterministic: `sha256(app_id|review_plan_hash|counter)`
  - `application_id`, `application_url`, `job_company`, `job_title`
  - `review_plan_hash` — the frozen final review plan hash
  - `document_hashes` — sorted `[{document_kind, content_hash}]`
  - `created_at`, `consumed_at`, `revoked_at`, `expires_at`
  - `is_active` — not consumed, not revoked, not expired
- `review_plan.py` (or same module): `compute_review_plan_hash(*, application_id,
  company, job_title, application_url, ats_target_url, fields: [{field_token,
  value, options, source, risk_level, requires_confirmation}], documents:
  [{document_kind, content_hash}], submit_control: {text, selector, frame_url},
  interventions_summary, generated_at)` → deterministic SHA-256 (sort keys,
  exclude `generated_at` like the WQ-7C `MutationPlan`, so identical plans hash
  identically). This hash is the identifier in the owner's Phase B approval.
- Absolute limit constant `MAX_TOTAL_REAL_SUBMISSIONS = 1`.

New migration `migrations/versions/0015_submission_authorization.py` — table
`submission_authorizations`; new `SubmissionAuthorizationRow` in
`persistence/models.py`.

Store functions in `submission/store.py` (or new `authorization_store.py`):
- `create_authorization(...)` — refuses if any other application already has a
  consumed `submitted_confirmed` result (absolute limit) and refuses if an
  active authorization exists for a DIFFERENT application (one total).
- `get_active_authorization(session, application_id)`
- `get_authorization(session, authorization_id)`
- `revoke_authorization(...)`
- `consume_authorization(...)` — compare-and-set (SELECT ... FOR UPDATE / 
  update-where-consumed-is-null), idempotent.
- `count_real_submit_attempts(session)` — count `submission_results` rows with
  `clicked=true` and state in (submitted_confirmed, outcome_unknown,
  validation_failed) → must stay ≤ 1.

## New component 2 — coordinator WQ-8 gate (additive)

New gate, active ONLY when an active authorization row exists for the
application at submit time:

- `enable_real_submission` True (existing gate).
- The active authorization exists and `is_active` (not consumed/revoked/expired).
- `authorization.review_plan_hash` == the review plan hash re-derived at
  submit time from the current job + the recomputed live snapshot (fields,
  documents with content hashes, URL, submit control).
- `authorization.document_hashes` == the content hashes of the actual files the
  snapshot uploads reference (present on disk).
- `authorization.application_url` == current `page.url` (also existing gate 11)
  and == `job.url`.
- `authorization.application_id` == job's application_id.
- Consumed as part of claim acquisition (same transaction) — before the browser
  starts in `execute_controlled_submission`.

The snapshot must ALSO pass its existing gates (approval snapshot_hash match,
no pending interventions, etc.). New gate return states reuse
`SubmissionResultState` (`SUBMISSION_NOT_ALLOWED` for missing/expired/consumed,
`APPROVAL_STALE` for any mismatched binding) — no new enum value. The WQ-8
contract's four classification labels map to persisted states:
`submitted_confirmed`→`submitted_confirmed`,
`submission_rejected`→`validation_failed`, `submission_unknown`→`outcome_unknown`,
`submission_blocked_before_attempt`→ any no-click blocked state.

## New component 3 — interlock one-shot authorized allowance

The WQ-7 `INTERLOCK_SCRIPT` blocks ALL submit events (`preventDefault` in a
capture listener, `form.submit`/`requestSubmit` overrides, `dispatchEvent`
guard). A real authorized click would therefore be blocked too. WQ-8 keeps the
interlock and adds an explicit one-shot pass:

- New globals in the init script: `window.__wq8_submit_authorization =
  {armed: false, token: null, authorized_submits: 0}`.
- New helper `armAuthorizedSubmit(token)` — sets `armed=true` once (idempotent)
  with the given token.
- Each blocking layer checks `__wq8_submit_authorization.armed`; if armed, it
  consumes the flag (sets `armed=false`), increments `authorized_submits`, and
  lets THAT one signal through (no preventDefault / block). Every subsequent
  signal is blocked again.
- New Python helpers in `submit_interlock.py`:
  - `arm_authorized_submit(page, token)` — sets the one-shot armed state.
  - `disarm_authorized_submit(page)` — clears the armed state (called in a
    `finally` so the allowance can never leak).
  - `read_counters` extended with `authorized_submits`.
- The coordinator calls `arm_authorized_submit(page, token)` immediately before
  `locator.click(timeout=...)` and `disarm_authorized_submit(page)` in the
  `finally` (covers fetch/XHR forms where no submit event fires → no pass
  consumed → explicitly cleared).

## New component 4 — interlock installation on the submit path

Today the submit path (`SubmissionExecutionService._execute_in_browser`) does
NOT install the interlock at all. WQ-8 makes it conditional:

- When an active WQ-8 authorization exists for the job, the execution service
  installs the interlock on the context BEFORE navigation
  (`install_interlock(context)`).
- When no authorization exists (all current tests/behavior), the submit path is
  byte-for-byte unchanged (no interlock — existing `submitted_confirmed`
  behaviors preserved).
- This keeps the contract "the interlock remains default armed" for the WQ-8
  run while the authorized submit passes exactly one signal.

## New component 5 — CLI surface (no new submit path)

New commands only for preparation/authorization; submit still goes through the
existing `live-submit`:

- `wq8-review-packet --application-id <id> --artifacts-dir <dir>` — Phase A and
  Phase B diagnostic: recomputes the review plan from persisted job + documents
  + latest snapshot, emits the frozen `review_plan_hash` and a SANITIZED review
  packet (no PII) to stdout + optional file. Never reads or prints real
  candidate values.
- `wq8-authorize --application-id <id> --review-plan-hash <hash>
  --expires-in-hours <h> --confirm` — OWNER-ONLY Phase B step. Recomputes the
  plan from current persisted state and refuses unless the provided hash
  matches EXACTLY; refuses unless `UAA_ENABLE_REAL_SUBMISSION=true`; refuses
  unless job is `review_ready`; refuses if any converted submission already
  exists (absolute limit); then creates the single-use authorization and prints
  `authorization_id`. The authorization is a DB row (owner approval token), NOT
  a generic instruction — safe default stays "submission forbidden".
- `wq8-status --application-id <id>` — read-only auth + gate diagnostic.

Phase B submission then uses the UNCHANGED `live-submit --approval-id ...`
command; the coordinator's WQ-8 gate activates because the active authorization
row exists.

## Document/binding invalidation

Document hashes are recomputed from the actual files at snapshot/build and at
authorization-consume time; the plan hash is recomputed and compared at
`wq8-authorize` (against frozen hash) and at submit (against the live page).
Any change to job target URL, company/title, CV document bytes, plan answers,
submit control identity, or page URL → hash mismatch → `APPROVAL_STALE` /
`SUBMISSION_NOT_ALLOWED` → submit blocked and WQ-8 returns to Phase A.

## Hermetic tests (new)

- `tests/unit/test_wq8_review_plan.py` — plan hash determinism, coverage of all
  binding fields, `generated_at` excluded, insertion-order independence.
- `tests/unit/test_wq8_authorization.py` — create/refuse (absolute limit,
  another-job refusals), expiry, revocation, consumed single-use, idempotent
  consume, wrong app/hash/url/doc rejected.
- `tests/unit/test_wq8_coordinator_gate.py` — coordinator gate with active auth:
  valid passes to click; missing/expired/consumed/mismatched-hash/
  mismatched-doc → blocked, no click; without an authorization row the gate is
  a no-op (existing behavior preserved).
- `tests/playwright/test_wq8_interlock_authorized_submit.py` — fixture form:
  armed one-shot passes exactly one submit and is then blocked again;
  unauthorized submit is blocked; `authorized_submits` counter == 1 and other
  counters unchanged; `disarm` clears. Uses the production interlock script.
- `tests/contract/test_migrations.py` — CURRENT_HEAD → `0015...`.
- Regression: the existing submission tests must pass unchanged (no
  authorization row present → no new gate, no interlock installed).

## Gates

Full local gate: ruff check, ruff format --check, pyright, non-live
non-playwright pytest, playwright pytest, `git diff --check`. Then push
milestone and verify local==origin.

## Milestone order (deterministic checkpoints, each pushed)

1. Design doc + handoff — DONE (`eae9569` pushed).
2. Review-plan/compute + authorization model + store + migration 0015.
3. Coordinator WQ-8 gate + conditional interlock install + interlock one-shot.
4. CLI (`wq8-review-packet`, `wq8-authorize`, `wq8-status`).
5. Hermetic tests; full gate; push.
6. Phase A live prep → review packet → STOP (`WQ-8 OWNER APPROVAL REQUIRED`).
7. Phase B only with owner approval.