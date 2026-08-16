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
- **Last checked-in milestone:** initial WQ-7C checkpoint (docs + handoff).
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
3. Synthetic identity contract + approved synthetic documents.
4. Opt-in synthetic-mutation mode (`UAA_LIVE_SYNTHETIC_MUTATION`), config,
   incompatibility with real submission, ephemeral browser, mutation budget and
   interlock evidence.
5. Pre-mutation machine-readable plan (frozen/hashed) + field-resolution
   correctness gate.
6. Field-mapper/embedding decision: hermetic benchmark on a labelled WQ-7C
   fixture set from real field schemas; do NOT add embeddings unless a real
   reproducible gap is proven.
7. Local/hermetic tests (unit + playwright fixture) proving every safety
   requirement from the WQ-7C contract.
8. Live real-ATS mutation proof on currently-open public forms (target policy,
   verify each target immediately, ≥2 platforms attempted, ≥1 completes).
9. End-to-end vertical slice across the JobHunter process boundary (no hand-
   fabricated queue, no DB seeding, no JobHunter code changes; STOP if a
   JobHunter change would be needed).
10. Evidence under `docs/evidence/wq-7c/`, validation gates, docs updates.
11. Final PR against `main` via GitHub REST API; six CI checks green; no merge.

## Completed work

- None (code) yet — this is the initial checkpoint.

## Changed files

- `docs/handoffs/ACTIVE_WORKPACKAGE.md` (this rewrite for WQ-7C).

## Tests and exact results

- Not run in this checkpoint.

## Decisions made

- Deliver WQ-7C as a new distinct opt-in mode; recon-only (WQ-7B) is NOT
  converted into a fill mode.
- No embeddings unless a reproducible semantic-mapping gap is proven offline.
- Preserve all pre-existing untracked debug artifacts
  (`tmp_debug_status.py`, `tmp_debug_status/`, `tmp_final_pipeline/`).

## Blockers / risks

- Live ATS availability is externally variable (proven in WQ-7B). Targets
  verified immediately before each run.
- Vertical slice depends on the JobHunter repo running a synthetic-profile
  workflow without production-code changes; if that is impossible, WQ-7C will
  STOP and report the exact cross-repository blocker.

## Exact next action

1. Add the synthetic identity contract and approved-document enforcement.
2. Add the opt-in synthetic-mutation mode to `config.py` + `execution_mode.py`.
3. Add the pre-mutation plan + mutation execution path.
4. Add hermetic tests, then run the full local gate.
5. Push after each meaningful milestone; verify local HEAD == remote HEAD.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit what the workpackage asked for; never commit screenshots, PDFs,
  live-runs, `.uaa_data`, `.env`, browser profiles, traces, or a local
  database.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.