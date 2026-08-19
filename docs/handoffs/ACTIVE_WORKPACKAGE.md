# Active Workpackage — WQ-8 (IN PROGRESS)

- **WP ID:** WQ-8 — One staged, owner-approved, controlled real application
  submission.
- **Status:** **IN PROGRESS** (Phase A: prepare + review packet). Branch just
  created from `origin/main`; no code changes yet. WQ-8 succeeds the accepted
  WQ-7C pre-submit proof (merged via PR #15, merge `2ac1e00`; post-merge
  closure merged via PR #16, merge `76b2e1f`). **No real application has ever
  been submitted** from any UAA run (CI, sandbox, or proof runs).
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
- **Last updated:** 2026-08-20 (WQ-8 initial handoff checkpoint).

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
2. Finalize WQ-8 authorization design against the verified submission stack.
3. Implement single-use authorization (application_id + review_plan_hash +
   URL + doc hashes + expiry; consumed on initiation; default forbidden;
   synthetic/real exclusivity kept).
4. Hermetic tests proving every WQ-8 safety property above.
5. Full local gate (ruff, pyright, pytest non-live/non-playwright, playwright,
   git diff --check); push deterministic milestone; verify local==origin.
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

## Changed files

- `docs/handoffs/ACTIVE_WORKPACKAGE.md` — replaced with the WQ-8 handoff
  (this commit).

## Tests and exact results

No code changes yet. Baseline (WQ-7C merged head `395b7dc`, recorded in
`docs/CURRENT_STATE.md`): 1258 non-live/non-playwright passed / 272
deselected; 269 playwright passed; ruff/pyright clean. Re-run the full gate at
the implementation milestone.

## Decisions made

- Reuse (not redesign) the existing controlled-submission stack as the WQ-8
  authorization base; the new work tightens binding to `review_plan_hash`
  + doc hashes + expiry + absolute one-submission audit rather than
  introducing a second submission pathway.
- Keep the WQ-7 submit interlock armed; the authorized submit goes through the
  existing single `SubmissionCoordinator` click path.
- WQ-8 is a two-phase owner-controlled workpackage; the agent stops at the
  Phase A gate and never self-approves Phase B.
- Do not modify JobHunter; the real target must arise from its normal
  workflow.
- Verify SHAs dynamically at every checkpoint; never trust an embedded SHA.

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

## Exact next action

Finish the WQ-8 implementation design milestone. Before editing code, read the
remaining implementation files for the WQ-7C live/orchestration paths so the
authorization change does not disturb them: `browser/mutation_plan.py`,
`form_engine/live_executor.py`, `browser/live_runner.py`,
`services/orchestration_service.py`, `application_queue/importer.py`,
`persistence/models.py` (submission/audit rows), migration `0014`,
`api/routes/orchestration.py`, and `cli.py` (`live-submit` path). Then commit
this handoff checkpoint and push:

```text
git add docs/handoffs/ACTIVE_WORKPACKAGE.md
git commit -m "docs(wq-8): initial WQ-8 handoff - controlled real submission workpackage"
git push -u origin checkpoint/wq-8-controlled-real-submission
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-8-controlled-real-submission
```

The two resolved values must match. Then implement the single-use
authorization and its hermetic tests, run the full gate, and push the next
deterministic milestone.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit what the workpackage asked for; never commit screenshots, PDFs,
  live-runs, `.uaa_data`, `.env`, browser profiles, traces, real candidate
  PII, or a local database.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.