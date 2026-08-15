# Active Workpackage

- **WP ID:** WQ-7B — Real ATS Navigation Reconnaissance.
- **Status:** BLOCKED — acceptance criterion (≥3 distinct ATS reaching a
  real public application form) not met after exhausting all permitted
  replacements. Final deliverable: evidence manifest + this handoff.
  No PR, no READY FOR REVIEW.
- **Branch:** `checkpoint/wq-7b-real-ats-navigation`
- **Base SHA:** `6326e4e0815d2d325eccc5bf3671afefd8e5bc8b` (`origin/main`, WQ-7A squash)
- **PR:** none (blocked outcome; reviewer decides whether the ≥3-distinct
  acceptance criterion should later be amended).
- **Last completed/checkpoint SHA:** `a71525e6e6f2551612b181a16181c723262e27bf`
  (fix commit, pushed and verified against origin). Resolve the head dynamically.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7b-real-ats-navigation
  ```

  The two values must match before handoff/review.
- **Last updated:** 2026-08-16

## Objective

Prove that UAA can safely navigate from real public job-detail pages to real
ATS application forms and observe their structure using the WQ-7A
infrastructure — **navigation and observation only**. Never fill fields,
never upload documents, never log into accounts, never submit applications,
never bypass CAPTCHA/anti-bot.

## Acceptance criterion (unchanged, per reviewer)

At least five real-job attempts; every supported ATS attempted; **at least
three distinct ATS platforms must reach a real public application form**.
Unsuccessful platforms get exact evidence and classification.

## Outcome — BLOCKED

All five supported ATS platforms were attempted (≥5 real-job attempts: 7
total target runs incl. replacements). Only **two** distinct ATS platforms
prove reachable to a real public application form without forbidden bypasses:

- **greenhouse** — FORM REACHED (Anthropic, 31 controls / 1 file,
  `recon_complete`)
- **lever** — FORM REACHED (Apply Digital, 24 controls / 2 files,
  `recon_complete`; interlock blocked 1 form_submit, `submitted=false`)

The other three are externally gated, each verified across multiple tenants
with all permitted replacements exhausted (max 2 per platform):

- **workday** — account-creation gate on 3/3 tenants (Company JR-0108019,
  US Bank, Baltimore City Fire Press Officer R0018870).
- **smartrecruiters** — anti-bot/security wall on its one-click UI on 3/3
  tenants (Eurofins, Pilot Company, IT TrailBlazers); SPA boots for normal
  browsers but reproducibly fails automation contexts; WQ-7B forbids
  anti-bot bypass, so none was attempted.
- **icims** — login/account-creation gate on 2/2 tenants (LMI, MBP) plus
  the platform's own careers hub (`hrjobs.icims.com`), whose every "Apply
  Now" links to a `/login` URL; a third replacement (KCI) failed earlier at
  click resolution (UAA observation on an iframe layout; `click_failed`,
  still no form reached).

Every run: `fields=[]`, `uploads=[]`, `submitted=false`, hard submit block
armed, ephemeral profile, `UAA_LIVE_RECON_ONLY=true`.

## Safety verification

- Zero typed values, zero uploads, zero UAA submit clicks in all runs.
- Login-only pages reported as `login_required`, never treated as
  application forms (`is_application_form` excludes auth gates).
- The recon-only captcha exception stays narrowly scoped
  (`recon_only and analysis.is_application_form` and no auth gate).
- Lever run captured `wq7_interlock: blocked 1 submission attempt(s)`
  (`submit_events=0, form_submit=1, request_submit=0, dispatch=0`).

## Completed work

- Implemented navigation-only recon mode (`UAA_LIVE_RECON_ONLY`),
  iCIMS support, auth-gate-first `_detect_blocker`, serialization fixes,
  and hermetic tests (committed as `9f56b19`, `a71525e`).
- Ran live recons of every supported ATS against real public jobs,
  exhausting replacements per the original WQ-7B prompt.
- Captured full evidence (`uaa_wq7b_final`, `uaa_wq7b_icims_kci`,
  `uaa_wq7b_rerun*` under the temp opencode output dirs; not committed).
- Wrote `docs/evidence/wq-7b/MANIFEST.md` (sanitized).

## Changed files

- `src/universal_auto_applier/navigator/apply_path_finder.py` (auth-gate-first)
- `src/universal_auto_applier/services/live_dry_run_platforms.py` (json mode dict)
- `tests/playwright/test_wq7b_recon_mode.py` (recon tests, incl. new login+captcha precedence)
- `tests/unit/test_wq7_live_dry_run_platforms.py` (serialization regression)
- `tests/fixtures/recon/login_captcha.html` (new fixture for gate-vs-captcha precedence)
- `docs/evidence/wq-7b/MANIFEST.md` (final evidence)

## Tests and exact results

- Non-live suite: 1208 passed (ruff + pyright + pytest available).
- Playwright suite: 253 passed.
- Recon/blocker modules: 112 passed (incl. new login_captcha precedence tests).
- WQ-7B recon-mode tests: 13 passed.
- `git diff --check`: clean. `ruff check`, `ruff format --check`, `pyright`: pass.
- Live recon runs (opt-in, not in CI): as recorded in MANIFEST and thumbnails above.

## Decisions made

- Do not bypass account creation, anti-bot, or login walls — WQ-7B forbids
  this; SmartRecruiters failures are reported as externally blocked.
- Do not amend the acceptance gate; finalize BLOCKED and let the reviewer
  decide the criterion's future.
- Never commit live-run artifacts (screenshots, traces, HTML, PDFs).
- Fabricated job URLs were rejected (both a SmartRecruiters 400 and a
  Workday 404 case proved this wastes time); real public jobs only, verified
  before each run.

## Blockers / risks

- **Acceptance gate unmet:** 2 of 5 supported ATS platforms reach real
  forms; mean, non-Greenhouse/Lever candidates are externally gated.
- All allowed replacements (2 per platform) are exhausted.
- A working guest-apply Workday/iCIMS tenant or bypass-free SmartRecruiters
  posting is not available within the WQ-7B target pool.

## Exact next action

1. Reviewer decides whether the ≥3-distinct acceptance criterion is amended.
2. If amended to ≥2 distinct + evidence of external gating, re-open the PR on
   this branch and run the six CI checks.
3. If not amended, WQ-7B stays BLOCKED; document the outcome (this handoff +
   MANIFEST) and close no PR.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit reviewed WQ-7B files; never commit live-runs data, `.uaa_data`,
  `.env`, browsers/databases, screenshots, traces, or HTML snapshots.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.