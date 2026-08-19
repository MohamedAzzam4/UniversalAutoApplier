# WQ-7 Local Live Run Handoff

This document provides the exact procedure for running WQ-7 real-site
dry-runs locally. It is intended for the operator's machine (Windows or
Linux with full network access). The sandbox cannot run these steps.

## Prerequisites (Windows)

1. **Python 3.11+** installed and on PATH.
2. **UniversalAutoApplier** repository checked out:
   ```powershell
   git clone https://github.com/MohamedAzzam4/UniversalAutoApplier.git
   cd UniversalAutoApplier
   git checkout checkpoint/wq-7-real-ats-dry-runs
   ```
3. **Setup** (creates .venv, installs deps + Chromium, applies migrations):
   ```powershell
   .\scripts\setup.ps1 -PythonExecutable python
   ```
4. **Playwright Chromium** installed:
   ```powershell
   .\.venv\Scripts\python.exe -m playwright install chromium
   ```

## Synthetic profile and documents

WQ-7 uses entirely synthetic candidate data:

- **Name:** Test Automation
- **Email:** test.automation@example.com (RFC 2606 reserved domain)
- **Phone:** +1 555 0100 (fictional 555 prefix)
- **LinkedIn:** Empty — no real LinkedIn profile is used
- **CV PDF:** Generated with visible "TEST DATA — AUTOMATION DRY RUN — NOT A REAL APPLICATION"
- **Cover letter PDF:** Same TEST DATA marking

Synthetic documents are generated automatically in
`<data_dir>/live-runs/wq7-platforms/wq7-documents/` and are never
committed to the repository.

No real candidate PII, passwords, cookies, or API credentials are used.

## ATS selection rules

- Select 5 currently-open public job application forms:
  - At least 2 Greenhouse
  - At least 2 Lever
  - At least 1 Workday or SmartRecruiters
- Exclude LinkedIn Easy Apply (protects the user's LinkedIn account)
- Exclude sites requiring login, account creation, CAPTCHA, or MFA
- Exclude expired or obviously fake/sample listings
- Verify every URL immediately before running it
- Do not commit volatile real job URLs as permanent configuration

## Environment variables

Set these before running. Use placeholder values — never real secrets.

```powershell
# Master opt-in gate (required)
$env:UAA_ENABLE_LIVE_PLATFORM_DRY_RUN = "true"

# WQ-7 hard submit block (required — enforced by the runner)
$env:UAA_WQ7_HARD_SUBMIT_BLOCK = "true"

# Per-platform real ATS URLs (set at least 5)
$env:UAA_LIVE_GREENHOUSE_URL = "https://job-boards.greenhouse.io/<company>/jobs/<job-id>"
$env:UAA_LIVE_LEVER_URL = "https://jobs.lever.co/<company>/<job-id>"
$env:UAA_LIVE_WORKDAY_URL = "https://myworkdayjobs.com/<company>/<job-id>"
$env:UAA_LIVE_SMARTRECRUITERS_URL = "https://careers.smartrecruiters.com/<company>/<job-id>"

# Optional: subset of platforms to run
$env:UAA_LIVE_DRY_RUN_PLATFORMS = "greenhouse,lever,workday"

# Browser settings
$env:UAA_BROWSER_HEADLESS = "false"  # Set to "true" for headless
```

## Stage 1: Navigation-only reconnaissance

Run with the synthetic profile but **before any data entry**:

```powershell
.\.venv\Scripts\python.exe -m universal_auto_applier live-dry-run-platforms `
    --headed `
    --max-steps 5 `
    --timeout-ms 15000
```

For each target, observe:
- Did the page load?
- Was an "Apply" button found?
- Did clicking Apply reach a real application form?
- Were form controls (text inputs, selects, file uploads) visible?
- Was a final submit control detected?
- Did any login/CAPTCHA/MFA wall appear?

Record results in the template below.

**Stop conditions:** If login, CAPTCHA, MFA, or account creation is required,
stop that target immediately and continue with others.

## Stage 2: Synthetic fill-only dry run

After reconnaissance confirms safe targets:

```powershell
.\.venv\Scripts\python.exe -m universal_auto_applier live-dry-run-platforms `
    --headed `
    --max-steps 15 `
    --timeout-ms 30000
```

The runner will:
1. Navigate to the job page
2. Click "Apply" to reach the form
3. Fill safe fields with synthetic data
4. Upload synthetic CV and cover letter PDFs
5. Stop before the final Submit/Send/Complete Application button

## Where screenshots and evidence are saved

Evidence is saved to:
```
<data_dir>/live-runs/wq7-platforms/<platform>/<timestamp>/
```

Each directory contains:
- `report.json` — full run report
- `step-NN-observe.png` — screenshots at each step
- `step-NN-after-fill.png` — screenshots after form filling
- `before-final-submit.png` — screenshot before the detected submit control
- `final.png` — final page screenshot
- `final-page.html` — DOM snapshot
- `trace.zip` — Playwright trace (if enabled)
- `network_evidence.json` — network containment evidence (if enabled)

A summary file is written to:
```
<data_dir>/live-runs/wq7-platforms/summary-<timestamp>.json
```

## How to confirm final Submit was not intentionally clicked

Check each platform's `report.json`:
- `submitted` must be `false`
- `stopped_reason` should be one of:
  - `"final_submit_detected"` — submit control found, runner stopped
  - `"form_filled_no_submit_control"` — form filled, no submit found
  - `"required_fields_unresolved"` — some fields could not be filled
  - `"captcha_detected"` / `"login_required"` — blocker detected

## How to report drafts, autosave and uploaded synthetic files

After each run, check:
1. Did the ATS show a "draft saved" or "autosave" message?
2. Were the synthetic CV/cover letter files accepted by the file input?
3. Did any file upload trigger an immediate server-side upload?

Record these observations in the result template below.

## Cleanup commands

```powershell
# Remove synthetic documents
Remove-Item -Recurse -Force "<data_dir>/live-runs/wq7-platforms/wq7-documents"

# Remove all WQ-7 run evidence (optional)
Remove-Item -Recurse -Force "<data_dir>/live-runs/wq7-platforms"

# Clear environment variables
Remove-Item Env:UAA_ENABLE_LIVE_PLATFORM_DRY_RUN
Remove-Item Env:UAA_WQ7_HARD_SUBMIT_BLOCK
Remove-Item Env:UAA_LIVE_GREENHOUSE_URL
Remove-Item Env:UAA_LIVE_LEVER_URL
Remove-Item Env:UAA_LIVE_WORKDAY_URL
Remove-Item Env:UAA_LIVE_SMARTRECRUITERS_URL
```

## How to resume after interruption

1. Re-checkout the branch:
   ```powershell
   git fetch origin
   git checkout checkpoint/wq-7-real-ats-dry-runs
   git pull origin checkpoint/wq-7-real-ats-dry-runs
   ```
2. Re-run setup if needed:
   ```powershell
   .\scripts\setup.ps1 -PythonExecutable python -SkipSmokeTests
   ```
3. Re-set environment variables
4. Re-run the `live-dry-run-platforms` command

Previous run evidence is preserved in timestamped directories. New runs
create new directories — no data is overwritten.

## Required result template for five jobs

For each of the 5 targets, record:

```
## Target N: <Company> — <Title>
- Platform: <greenhouse|lever|workday|smartrecruiters>
- Job URL: <url>
- Form URL: <url after clicking Apply>
- Navigation required: <yes/no — describe steps>
- Login/CAPTCHA/MFA: <none/login/captcha/mfa>
- Fields discovered: <count and types>
- Fields filled: <count and which ones>
- Fields skipped: <count and reasons>
- Synthetic files uploaded: <cv.pdf, cover.pdf or none>
- Draft/autosave observed: <yes/no — describe>
- Final submit control detected: <yes/no — describe>
- Final submit intentionally clicked: NO (must always be NO)
- Final state: <review_ready|needs_user_input|failed>
- Stopped reason: <final_submit_detected|form_filled_no_submit_control|...>
- Screenshot paths: <paths>
- Errors: <none or list>
- Timestamp checked: <UTC timestamp>
```

## Safety wording (exact)

WQ-7 blocks recognized final form submissions and common browser
form-submission mechanisms as defense in depth. It does not guarantee
that no synthetic data, autosave request, upload, draft, or custom
network request reaches the ATS.

## WQ-7C addendum — controlled synthetic ATS mutation (pre-submit)

WQ-7A/B above ran branch `checkpoint/wq-7-real-ats-dry-runs`. The WQ-7C
stage runs branch `checkpoint/wq-7c-synthetic-mutation` with the
`synthetic-mutation` opt-in path (never used for real data, mutually
exclusive with real submission):

```powershell
git checkout checkpoint/wq-7c-synthetic-mutation

# Master opt-in (required, default off):
$env:UAA_ENABLE_LIVE_PLATFORM_DRY_RUN = "true"
$env:UAA_LIVE_SYNTHETIC_MUTATION = "true"

# From a JobHunter application_queue.jsonl (real discovery path):
$env:UAA_JOBHUNTER_QUEUE = "C:\path\to\application_queue.jsonl"
python -m universal_auto_applier queue-import `
  --queue-import-synthetic-mutation `
  --queue-file-foreign-key-override runs/queue-import-edge/db.sqlite3

# Or a single job URL driver:
python -m universal_auto_applier live-synthetic-mutation `
  --url "<real-public-ats-url>" `
  --code "research-profile" `
  --synthetic-cv "<synthetic-cv.pdf>" `
  --synthetic-image "<synthetic-photo.jpg>" `
  --headless
```

Preconditions enforced by the code (not by the operator): synthetic identity
+ approved-document hash allowlists, field-values DOCX—no real candidate PII,
interlock armed before any page mutation, mutation plan frozen and hashed
before execution, and `submitted` is always `false`. See
`docs/generalization/TESTING_STRATEGY.md` and
`docs/evidence/wq-7c/FULL_SAME_JOB_CLOSURE.md` for the attested run's exact
commands, environment, and results. `--threshold 1.0` /
`--german-policy accept_all` were used in the natural proof as TEST-ONLY
overrides; JobHunter code is unchanged.
