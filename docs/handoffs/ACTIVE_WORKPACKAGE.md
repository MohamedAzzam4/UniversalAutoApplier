# Active Workpackage

- **WP ID:** WQ-7B — Real ATS Navigation Reconnaissance.
- **Status:** REVIEWER CLOSURE COMPLETE — OWNER DECISION REQUIRED. The
  closure pass (this branch head) resolved every open item (KCI navigation
  ambiguity fixed + verified live, attempt-count and validation counts
  reconciled, Lever interlock event attributed). The acceptance criterion
  (≥3 distinct ATS reaching a real public application form) still shows
  **2 of 5** and is **unchanged per reviewer instruction** — the owner must
  decide whether to amend it. No PR, no READY FOR REVIEW.
- **Branch:** `checkpoint/wq-7b-real-ats-navigation`
- **Base SHA:** `6326e4e0815d2d325eccc5bf3671afefd8e5bc8b` (`origin/main`, WQ-7A squash)
- **PR:** none (blocked outcome; reviewer decides whether the ≥3-distinct
  acceptance criterion should later be amended).
- **Last completed/checkpoint SHA:** `563a6530abf782613a427ce4ffc8a2ba32fd24fe`
  (docs(wq-7b): finalize BLOCKED outcome with evidence manifest). Resolve the head dynamically.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7b-real-ats-navigation
  ```

  The two values must match before handoff/review.
- **Last updated:** 2026-08-16 (reviewer-closure pass)

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

## Outcome — BLOCKED (acceptance gate unmet; closure pass complete)

All five supported ATS platforms were attempted (authoritative detail in the
reconciliation block below). Only **two** distinct ATS platforms prove
reachable to a real public application form without forbidden bypasses:

- **greenhouse** — FORM REACHED (Anthropic, 31 controls / 1 file,
  `recon_complete`)
- **lever** — FORM REACHED (Apply Digital, 24 controls / 2 files,
  `recon_complete`; interlock blocked 1 programmatic `form_submit`
  (`submit_events=0`), `submitted=false`)

The other three are externally gated, each verified across multiple tenants
with all permitted replacements exhausted (max 2 per platform):

- **workday** — account-creation gate on 3/3 tenants (Company JR-0108019,
  US Bank `2026-0024161`, Baltimore City Fire Press Officer R0018870).
- **smartrecruiters** — anti-bot/security wall on its one-click UI on 3/3
  tenants (Eurofins, Pilot Company, IT TrailBlazers); SPA boots for normal
  browsers but reproducibly fails automation contexts; WQ-7B forbids
  anti-bot bypass, so none was attempted.
- **icims** — login/account-creation gate on 3/3 tenants (LMI, MBP, **KCI**)
  plus the platform's own careers hub (`hrjobs.icims.com`), whose every
  "Apply Now" links to a `/login` URL. KCI was previously `click_failed`
  (page-shell Apply vs widget competition); the reviewer-closure pass fixed
  and live-verified it (`login_required` post-fix). No UAA-caused
  navigation failure remains on iCIMS.

Every run: `fields=[]`, `uploads=[]`, `submitted=false`, hard submit block
armed, ephemeral profile, `UAA_LIVE_RECON_ONLY=true`.

## Attempt-count reconciliation (reviewer-closure pass)

The earlier "7 real-job target runs" figure was an undercount inconsistent
with the 11-row evidence matrix. Authoritative figures (see MANIFEST):

- **Distinct target URLs executed by the runner: 12** (14 unique URL
  strings minus the duplicate US Bank `2026-0024161` and LMI `14407` form
  variants).
- **Distinct real public jobs observed (runner + MCP-only spot checks): 15**
  (adds Pilot Company SR, MBP iCIMS, Baltimore City WD).
- **Evidence rows (per-platform block outcome): 11** (greenhouse 1, lever 1,
  workday 3, smartrecruiters 3, icims 3).
- **Runner invocations:** 10 summary-producing runs (5-platform `final` +
  targeted reruns) + 1 chrome-profile probe without a summary.

**"7" is superseded and must not be carried forward.** Formal ≥5 real-job
attempts criterion is met under every authoritative measure (12/15/11).

## Lever interlock event attribution (reviewer-closure pass)

The `form_submit=1, submit_events=0, dispatch_submit_events=0` signature on
Lever means a site script called `HTMLFormElement.prototype.submit()`
programmatically (no submit event, no click, no dispatched SubmitEvent).
Proof it is not UAA-initiated:

- The run trace contains exactly one `page.evaluate` call — UAA's own final
  counters read (`call@1266`). UAA never evaluated a script containing
  `submit()`, never filled a field (`fields=[]`), and its only action was
  clicking "APPLY FOR THIS JOB" (a link navigation).
- It is deterministic: identical counters across all 4 Lever invocations
  (liveruns, rerun, rerun2, final).

Classification: **page/third-party-initiated programmatic form submit,
blocked by interlock with `submitted=false`**. Separate counters stay
reported: UAA submit clicks = 0, `submitted=false`.

## Safety verification

- Zero typed values, zero uploads, zero UAA submit clicks in all runs.
- Login-only pages reported as `login_required`, never treated as
  application forms (`is_application_form` excludes auth gates).
- The recon-only captcha exception stays narrowly scoped
  (`recon_only and analysis.is_application_form` and no auth gate).
- Lever run captured `wq7_interlock: blocked 1 submission attempt(s)`
  (`submit_events=0, form_submit=1, request_submit=0, dispatch=0`) —
  attributed to page/third-party JS `form.submit()`, not UAA (see above).
- KCI widget-selection fix is covered by a hermetic regression test; real
  KCI rerun post-fix reached `login_required` with `submitted=false`.

## Completed work

- Implemented navigation-only recon mode (`UAA_LIVE_RECON_ONLY`),
  iCIMS support, auth-gate-first `_detect_blocker`, serialization fixes,
  and hermetic tests (committed as `9f56b19`, `a71525e`, `563a653`).
- Ran live recons of every supported ATS against real public jobs,
  exhausting replacements per the original WQ-7B prompt.
- Captured full evidence (`uaa_wq7b_final`, `uaa_wq7b_icims_kci`,
  `uaa_wq7b_icims_kci_fix`, `uaa_wq7b_rerun*` under the temp opencode
  output dirs; not committed).
- Wrote `docs/evidence/wq-7b/MANIFEST.md` (sanitized).
- **Reviewer-closure pass (2026-08-16):**
  - Root-caused and fixed the iCIMS/KCI shell-vs-widget apply ambiguity
    (`embed_rank` key in `choose_safe_action`).
  - Added hermetic fixtures + failing regression test, then verified the
    test passes post-fix and re-ran real KCI to `login_required`
    (`uaa_wq7b_icims_kci_fix`).
  - Reconciled attempt counts (7 → authoritative 15/12/11 per MANIFEST) and
    re-ran the full validation gates after the fix.
  - Attributed the Lever `form_submit=1` interlock event
    (page/third-party-initiated; deterministic).
  - Updated MANIFEST + this handoff.

## Changed files

- `src/universal_auto_applier/navigator/apply_path_finder.py` — auth-gate
  first; **added `embed_rank` to prefer child-frame (widget) SAFE_APPLY**
- `src/universal_auto_applier/services/live_dry_run_platforms.py` (json mode dict)
- `tests/playwright/test_wq7b_recon_mode.py` (recon tests; **new
  `TestReconWidgetApplyPreference::test_prefers_widget_apply_over_shell_apply`**)
- `tests/unit/test_wq7_live_dry_run_platforms.py` (serialization regression)
- `tests/fixtures/recon/login_captcha.html` (new fixture for gate-vs-captcha precedence)
- `tests/fixtures/recon/icims_outside.html`, `icims_widget.html`,
  `agency_landing.html` (new hermetic shell-vs-widget apply fixtures)
- `docs/evidence/wq-7b/MANIFEST.md` (final evidence)

## Tests and exact results

- Non-live suite: **1465 passed, 3 deselected** (`pytest -m "not live"`).
- Playwright suite: **256 passed** (`pytest tests/playwright`).
- Recon/blocker modules: pass (incl. login_captcha precedence).
- WQ-7B recon-mode tests: 14 passed (13 existing + 1 new widget-preference
  regression).
- `git diff --check`: clean. `ruff check`, `ruff format --check` (198/198),
  `pyright` (0/0/0): pass.
- Live recon runs (opt-in, not in CI): as recorded in MANIFEST and thumbnails above.

## Decisions made

- Do not bypass account creation, anti-bot, or login walls — WQ-7B forbids
  this; SmartRecruiters failures are reported as externally blocked.
- Do not amend the acceptance gate; finalize BLOCKED and let the reviewer
  decide the criterion's future.
- **Do not amend the ≥3-distinct criterion during the reviewer-closure
  pass** (explicit reviewer instruction); the closure report states OWNER
  DECISION REQUIRED.
- Do not open the PR or start WQ-7C in this pass.
- Only make a production fix when deterministically reproduced: the KCI
  shell-vs-widget ambiguity was reproduced with a hermetic failing test
  before editing `choose_safe_action` (fix: `embed_rank` preference, not a
  live-site-only change). The fix does not alter `allow_apply` /
  `allow_continue` priorities or submit safety.
- Never commit live-run artifacts (screenshots, traces, HTML, PDFs).
- Fabricated job URLs were rejected (both a SmartRecruiters 400 and a
  Workday 404 case proved this wastes time); real public jobs only, verified
  before each run.
- Attack-count terminology is exact in the manifest: target attempts
  (distinct URLs, 12 via runner / 15 incl. MCP checks) vs runner
  invocations (10) vs evidence rows (11). "7" is retired.

## Blockers / risks

- **Acceptance gate unmet:** 2 of 5 supported ATS platforms reach real
  forms; non-Greenhouse/Lever candidates are externally gated.
- All allowed replacements (2 per platform) are exhausted.
- A working guest-apply Workday/iCIMS tenant or bypass-free SmartRecruiters
  posting is not available within the WQ-7B target pool.
- No objectionable production risk: the embed_rank fix was regression-tested
  and validated by the full gate suite (1465 non-live + 256 playwright).

## Exact next action

1. Reviewer/owner decides whether the ≥3-distinct acceptance criterion is
   amended.
2. If amended to ≥2 distinct + evidence of external gating, re-open the PR on
   this branch and run the six CI checks.
3. If not amended, WQ-7B stays BLOCKED; the reviewer-closure report (with
   all six sections) is the final handoff; document the decision here.
4. Either way, do **not** start WQ-7C until this closure decision lands on
   origin/main.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit reviewed WQ-7B files; never commit live-runs data, `.uaa_data`,
  `.env`, browsers/databases, screenshots, traces, or HTML snapshots.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.