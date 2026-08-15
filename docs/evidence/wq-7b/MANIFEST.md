# WQ-7B Evidence Manifest

Status: **BLOCKED** — acceptance criterion not met

Date: 2026-08-16 (runs on 2026-08-15 22:42–23:00 UTC)
Branch: `checkpoint/wq-7b-real-ats-navigation`
Head: `a71525e` (fix commit; resolve head dynamically)

## Acceptance criterion

- At least five real-job attempts — **met (7 attempts)**
- Every supported ATS attempted (greenhouse, lever, workday, smartrecruiters, icims) — **met**
- At least three distinct ATS platforms must reach a real public application form — **NOT MET (only 2 of 5)**

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
screen or fails to navigate to the hosted widget; replacements exhausted.

| # | Tenant / Role | URL (initial) | Result | Stopped reason | Gate observed |
| --- | --- | --- | --- | --- | --- |
| 1 (original) | LMI — Cloud Systems Engineer (14407) | `https://careers-lmi.icims.com/jobs/14407/job` | needs_user_input | login_required | "Apply for this job online" → `/jobs/14407/.../login` |
| 2 (repl 1) | MBP (2639) | `https://careers-mbpce.icims.com/jobs/2639/...` | needs_user_input | login_required | "Apply for this job online" → `/jobs/2639/.../login` |
| 3 (repl 2) | KCI — AI/ML Program Manager (7938) | `https://careers-kci.icims.com/jobs/7938/ai-ml-program-manager-consulting/job` | needs_user_input | click_failed | runner selected page-level "Apply" (`careers.kci.com`) instead of iframe widget control |

- The LMI and MBP entries are **platform gating**: iCIMS candidate apply
  always requires account creation/login ("create a login and password",
  "Submit Profile"). Verified on two tenants and consistent with iCIMS
  public documentation and its jobs aggregator (`hrjobs.icims.com`), whose
  every "Apply Now" link points directly to a `/login` URL.
- The KCI entry adds a secondary **UAA observation**: on this specific
  layout the apply control lives in an iCIMS iframe and the locator
  resolved to the page-level "Apply" link first; no navigation was
  attempted beyond the failed click. It does not reach a form either
  way, so it does not change the acceptance outcome.

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
- iCIMS: login/account-creation gate reproduced on 2/2 tenants plus its
  own careers hub; aggregator confirms `/login` on every customer page.
- SmartRecruiters: anti-bot wall reproduced on 3/3 tenants; no bypass
  attempted (forbidden by WQ-7B).

The failures are external/platform gating, verified by direct observation,
not in-repository defects.

## Artifact locations (sanitized note)

- `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_final\` — full
  5-platform run (2026-08-15T22:42Z)
- `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_icims_kci\` — iCIMS
  KCI replacement run (2026-08-15T22:59Z)
- Earlier reruns in `C:\Users\LOQ\AppData\Local\Temp\opencode\uaa_wq7b_rerun*`
  (MBP iCIMS, Baltimore City Workday, Pilot/IT TrailBlazers SmartRecruiters,
  headed chrome-channel spot checks).

No artifacts from these directories are committed to the repository.