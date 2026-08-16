# WQ-7B Evidence Manifest

Status: **BLOCKED** — acceptance criterion not met (reviewer-closure verdict:
OWNER DECISION REQUIRED; the ≥3-distinct gate stays unchanged by this pass)

Date: 2026-08-16 (runs on 2026-08-15–16 22:42–23:18 UTC)
Branch: `checkpoint/wq-7b-real-ats-navigation`
Head: resolve dynamically (`git rev-parse HEAD` / `git rev-parse origin/<branch>`)

## Acceptance criterion

- At least five real-job attempts — **met** (see Attempt-count reconciliation
  below; authoritative distinct-target totals are larger than the earlier
  "7" figure)
- Every supported ATS attempted (greenhouse, lever, workday, smartrecruiters, icims) — **met**
- At least three distinct ATS platforms must reach a real public application form — **NOT MET (only 2 of 5)**

## Attempt-count reconciliation (reviewer-closure pass)

The earlier "7 real-job target runs" figure was an undercount of a subset
of runs and contradicted the 11-row target matrix. Authoritative totals:

| Metric | Count | Basis |
| --- | --- | --- |
| Runner invocations with summary | **10** | `uaa_wq7b_liveruns`, `rerun`, `rerun2`, `rerun3`, `rerun3_sr`, `rerun3_sr2`, `rerun3_sr3`, `final`, `icims_kci`, `icims_kci_fix` (each has exactly 1 `summary-*.json`) |
| `report.json` files (executed target entries) | **33** | summed across all `uaa_wq7b_*` run dirs |
| Distinct initial URLs executed by the runner | **12** | 14 unique URL strings minus 2 duplicate-form variants of the same jobs (US Bank `2026-0024161`; LMI `14407`) |
| Distinct real public jobs observed (runner + MCP spot checks) | **15** | plus Pilot Company (SR), MBP (iCIMS), Baltimore City (WD) verified via MCP browser |
| Documented evidence rows (blocked-outcome matrix) | **11** | greenhouse 1, lever 1, workday 3, smartrecruiters 3, icims 3 — the target-matrix below |
| Chrome-profile probe without summary | **1** | `uaa_wq7b_chrome_profile` (no `summary-*.json`) |
| Runner invocations total (incl. probe) | **11** | 10 summary-producing + 1 probe |

- **"7" is superseded.** It was derived from a partial enumeration of runs
  and did not reconcile with either the target matrix (11 rows) or the
  distinct executed URL inventory (12). Do not carry it forward.
- Terminology used in this manifest and the closure report:
  - *target attempt* = one distinct real public job URL exercised by the
    live runner (12), plus MCP-only verification (3) = 15 observed.
  - *runner invocation* = one `live-dry-run-platforms` process (10).
  - *evidence row* = one platform-level outcome row in the matrix below (11):
    the final approach taken per platform after exhausting replacements.

## Purpose

This manifest is the sanitized record for the navigation/observation-only
reconnaissance performed in WQ-7B. It contains no screenshots, no traces,
no HTML snapshots, no PDFs, and no candidate data. Original artifacts
(report.json, screenshots, trace.zip, final-page.html) remain in local
`live-runs`/`uaa_wq7b_*` output directories outside the repository.

Every run was executed with:

- `UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=true`
- `UAA_LIVE_RECON_ONLY=true` (navigation/observation only)
- `UAA_WQ7_HARD_SUBMIT_BLOCK=true`
- ephemeral browser profile, headless (recon runs) or headed (spot checks)

For every run: `fields=[]`, `uploads=[]`, `submitted=false`.

## Target matrix

### greenhouse — FORM REACHED (real-navigation verified)

| # | Role | URL (initial) | Result | Reason | Entry |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | ML/Research Engineer, Safeguards — Anthropic | `https://job-boards.greenhouse.io/anthropic/jobs/4949336008` | recon_complete | first_application_form_reached | report.json status=recon_complete |

- Final URL: `https://job-boards.greenhouse.io/anthropic/jobs/4949336008`
- Click path: none (form present on job page)
- Recon observation: 31 visible controls, 1 file input, 25 field labels
  (first_name…disability_status, including question_* items), dangerous
  submit present, embedded blocker classified captcha-like
- `submitted=false`

### lever — FORM REACHED (real-navigation verified)

| # | Role | URL (initial) | Result | Reason | Entry |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | Agentic Product Design Lead — Apply Digital | `https://jobs.lever.co/applydigital/e67e06b6-48e7-471d-8050-34127416dcf8` | recon_complete | first_application_form_reached | report.json status=recon_complete |

- Clicked "APPLY FOR THIS JOB" (safe_apply) → `/apply`
- Recon observation: 24 visible controls, 2 file inputs (resume + cover),
  19 field labels (opportunityLocationId, resume, name, email, phone,
  location, org, urls[LinkedIn|Twitter|GitHub|Portfolio|Other],
  cards[*][fieldN], consent[marketing])
- WQ-7 interlock blocked 1 `form_submit` submission attempt —
  runner captured it as an error entry; `submitted=false`
- **Interlock event attribution (reviewer-closure pass):** the
  `form_submit_calls=1, submit_events=0, dispatch_submit_events=0`
  signature means a site JS script called `HTMLFormElement.prototype.submit()`
  programmatically — no `submit` event, no button click, no dispatched
  SubmitEvent. The run trace contains exactly one `page.evaluate` call
  (UAA's own final counters read, call@1266); UAA never evaluated any
  script containing `submit()`, never filled a field (`fields=[]`), and
  performed exactly one action (clicking "APPLY FOR THIS JOB", a link
  navigation). The event is therefore **page/third-party-initiated** —
  Lever's apply SPA calls `form.submit()` as part of its own load/behavior
  in this automation context — and it is **deterministic** (identical
  counters on all 4 runner invocations: liveruns, rerun, rerun2, final).
  The interlock blocked it with zero network/navigation effect
  (`submitted=false`).
- `submitted=false`

### workday — EXTERNALLY GATED (account creation required)

All three targets require account creation/sign-in before any application
form; replacements exhausted (2 replacement URLs used).

| # | Tenant / Role | URL (initial) | Result | Stopped reason | Gate observed |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | Company (JR-0108019) | `myworkdayjobs.com/...` company board | needs_user_input | login_required | Sign-In modal before apply |
| 2 (repl 1) | US Bank — Float Client Relationship Consultant 3 | `https://usbank.wd1.myworkdayjobs.com/en-US/US_Bank_Careers/job/Anaheim-Hills-CA/Float-Client-Relationship-Consultant-3--Banker----North-Orange-County--CA_2026-0024161/apply` | needs_user_input | no_safe_apply_path | Create Account flow |
| 3 (repl 2) | Baltimore City — Fire Press Officer (R0018870) | `https://baltimorecity.wd1.myworkdayjobs.com/External/.../Fire-Press-Officer---Fire-Department_R0018870/apply` | needs_user_input | login_required | "Apply Manually" → Create Account (step 1 of 6 = Create Account/Sign In) |

- Failure is **platform gating**: Workday external candidate apply always
  starts with account creation ("Sign in or create a Workday candidate
  account"). Verified on three different tenants and consistent with
  public Workday behavior. Not a UAA defect.

### smartrecruiters — EXTERNALLY GATED (anti-bot wall on one-click UI)

All three targets route the "I'm interested" control to
`jobs.smartrecruiters.com/oneclick-ui/...` and never boot the application
SPA for the automation context; replacements exhausted.

| # | Tenant / Role | URL (initial) | Result | Stopped reason | Gate observed |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | Eurofins | `jobs.smartrecruiters.com/...` | needs_user_input | security_wall | one-click UI / no form |
| 2 (repl 1) | Pilot Company (744000143647559) | `https://jobs.smartrecruiters.com/PilotCompany/744000143647559-janitorial-maintenance` | needs_user_input | security_wall | one-click UI / no form |
| 3 (repl 2) | IT TrailBlazers — R Programmer (84418799) | `https://jobs.smartrecruiters.com/ITTrailBlazers1/84418799-r-programmer` | needs_user_input | security_wall | "I'm interested" → oneclick-ui → security_wall |

- Failure is **external anti-bot / automation-fingerprint gating**. The
  SPA boots with the interactive form (16 visible inputs incl.
  firstName/lastName/email) in a normal browser context, but the
  Playwright-launched contexts (bundled Chromium and `chrome` channel,
  ephemeral and persistent profiles, headless and headed) reproducibly
  show the recovery/error fallback ("an error occurred, please try again
  later"). The page bundle carries DataDome integration. WQ-7B forbids
  anti-bot/CAPTCHA bypass, so no bypass was attempted. Not a UAA defect.

### icims — EXTERNALLY GATED (account creation required)

All three targets reach an iCIMS "apply" entry that lands on a login
screen; replacements exhausted.

| # | Tenant / Role | URL (initial) | Result | Stopped reason | Gate observed |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | LMI — Cloud Systems Engineer (14407) | `https://careers-lmi.icims.com/jobs/14407/job` | needs_user_input | login_required | "Apply for this job online" → `/jobs/14407/.../login` |
| 2 (repl 1) | MBP (2639) | `https://careers-mbpce.icims.com/jobs/2639/...` | needs_user_input | login_required | "Apply for this job online" → `/jobs/2639/.../login` |
| 3 (repl 2) | KCI — AI/ML Program Manager (7938) | `https://careers-kci.icims.com/jobs/7938/ai-ml-program-manager-%E2%80%93-consulting/job` | needs_user_input | login_required | "Apply for this job online" (widget iframe) → `/jobs/7938/.../login` |

- All three entries are **platform gating**: iCIMS candidate apply always
  requires account creation/login ("create a login and password",
  "Submit Profile"). Verified on three tenants and consistent with iCIMS
  public documentation and its jobs aggregator (`hrjobs.icims.com`), whose
  every "Apply Now" link points directly to a `/login` URL.
- **KCI secondary observation (reviewer-closure pass):** an earlier run had
  failed at click resolution — `choose_safe_action` preferred the
  page-shell "Apply" link (`http://careers.kci.com/`, exact-match HIGH)
  over the embedded iCIMS widget's "Apply for this job online" control
  (substring-match MEDIUM). This was a reproducible UAA defect, not a
  live-site difference. It was fixed and regression-tested:
  - Fix: `choose_safe_action` now prefers child-frame (widget) `SAFE_APPLY`
    over page-shell `SAFE_APPLY` via an `embed_rank` key (rank 0 vs 1)
    before confidence. See
    `src/universal_auto_applier/navigator/apply_path_finder.py`.
  - Hermetic fixtures: `tests/fixtures/recon/icims_outside.html`,
    `icims_widget.html`, `agency_landing.html` reproduce the
    shell-vs-widget competition.
  - Regression test: `TestReconWidgetApplyPreference::test_prefers_widget_apply_over_shell_apply`
    in `tests/playwright/test_wq7b_recon_mode.py` (failed pre-fix, passes
    post-fix).
  - Real KCI rerun post-fix (`uaa_wq7b_icims_kci_fix`, 2026-08-16 23:1x UTC):
    `status=needs_user_input`, `stopped_reason=login_required`, final URL
    `https://careers-kci.icims.com/jobs/7938/ai-ml-program-manager-%E2%80%93-consulting/login`,
    click_path step 1 = "Apply for this job online" → widget frame,
    `fields=[]`, `uploads=[]`, `submitted=false`.
  - iCIMS is therefore **3/3 tenants externally gated with no
    UAA-caused navigation failure remaining**.

## Distinct ATS platforms that REACHED a real public application form

| Platform | Count | Verdict |
| --- | --- | --- |
| greenhouse | 1 (Anthropic) | FORM REACHED |
| lever | 1 (Apply Digital) | FORM REACHED |
| workday | 0 | externally gated |
| smartrecruiters | 0 | externally gated |
| icims | 0 | externally gated |

**Distinct platforms reaching forms: 2 of 5. Acceptance criterion
(≥ 3 distinct) NOT met.**

## Why the outcome is BLOCKED (not a UAA defect)

Without bypassing account creation (workday, icims), bypassing
anti-bot/CAPTCHA walls (smartrecruiters), or logging in (forbidden), no
third distinct ATS platform can be made to reach a real public application
form. The original WQ-7B target pool for this unit supports exactly the
five platforms tested.

- Workday: account-creation gate reproduced on 3/3 tenants.
- iCIMS: login/account-creation gate reproduced on 3/3 tenants (LMI, MBP,
  KCI) plus its own careers hub; aggregator confirms `/login` on every
  customer page. No UAA-caused navigation failure remains (embed_rank fix
  verified live on KCI).
- SmartRecruiters: anti-bot wall reproduced on 3/3 tenants; no bypass
  attempted (forbidden by WQ-7B).

The failures are external/platform gating, verified by direct observation,
not in-repository defects.

## Artifact locations (sanitized note)

- `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_final\` — full
  5-platform run (2026-08-15T22:42Z)
- `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_icims_kci\` — iCIMS
  KCI replacement run pre-fix (2026-08-15T22:59Z, `click_failed`)
- `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_icims_kci_fix\` —
  iCIMS KCI rerun post-embed_rank-fix (2026-08-16T23:1xZ,
  `login_required`) — proves widget selection + external gate
- Earlier reruns in `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_rerun*`
  (MBP iCIMS, Baltimore City Workday, Pilot/IT TrailBlazers SmartRecruiters,
  headed chrome-channel spot checks).

## Validation (post-fix; final pre-decision cleanup 2026-08-16)

Authoritative contract gates, run exactly as specified:

- `pytest -m "not live and not playwright"`: **1209 passed, 259 deselected**
- `pytest tests/playwright`: **256 passed**
- `ruff check src tests migrations`: pass
- `ruff format --check src tests migrations`: pass (198/198 formatted)
- `pyright`: 0 errors / 0 warnings / 0 informations
- `git diff --check`: clean

Additional historical validation (kept for reference; does not substitute
for the required split above): `pytest -m "not live"` = 1465 passed,
3 deselected (that run aggregated the 1209 non-browser suite with the
256 browser tests).

- New regression test coverage: `TestReconWidgetApplyPreference` (hermetic
  shell-vs-widget apply competition) in `tests/playwright/test_wq7b_recon_mode.py`.

No artifacts from these directories are committed to the repository.