# Live Browser Dry-Run

## Purpose

The live runner is UAA's own Playwright execution path. It does not depend on
Codex, a browser extension, or Chrome's file-URL permission. It opens one
imported job, follows the apply path, fills known fields, uploads the queued
documents, and stops before final submit.

## One-Time Setup

Install the pinned dependencies and browser:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\python.exe -m playwright install chromium
```

Set `UAA_DATA_DIR` to the data directory containing the imported UAA database.
The optional variables are:

```text
UAA_BROWSER_PROFILE_DIR=<persistent Playwright profile>
UAA_BROWSER_CHANNEL=chrome
UAA_BROWSER_HEADLESS=false
UAA_BROWSER_TIMEOUT_MS=30000
UAA_BROWSER_MAX_STEPS=20
```

If no profile path is configured, the CLI uses
`UAA_DATA_DIR/browser-profile`. Login cookies created in that dedicated
profile can be reused by later runs. A login page remains a blocker; UAA does
not type passwords or bypass authentication challenges.

To establish or refresh a login session in UAA's own profile:

```powershell
.\.venv\Scripts\python.exe -m universal_auto_applier browser-session `
  --url https://www.linkedin.com/login `
  --channel chrome
```

Log in inside the opened browser and press Enter in the terminal. Credentials
are entered directly into the website; UAA only retains the browser profile.

## Run One Job

```powershell
.\.venv\Scripts\python.exe -m universal_auto_applier list-jobs
.\.venv\Scripts\python.exe -m universal_auto_applier live-dry-run `
  --application-id <UNAMBIGUOUS_ID_PREFIX> `
  --headed
```

Use `--ephemeral-profile` for an isolated browser context, `--headless` for a
background run, or `--channel chrome` to use installed Google Chrome.

For diagnosis, `--start-url <DIRECT_ATS_URL>` overrides the first URL for that
run without changing the stored job. This is useful when a source site is
blocked by login but a verified direct company/ATS URL is already known. It is
not a substitute for automatic apply-path navigation.

## Behavior

The runner:

1. Opens `ApplicationJob.url`.
2. Detects login, CAPTCHA, payment, security, expired, and submitted pages.
3. Clicks only controls classified as safe apply or safe continue.
4. Handles same-tab navigation, redirects, and new tabs.
5. Detects rendered application forms in the main page or iframes.
6. Maps fields from the candidate snapshot, explicit question answers, and
   positive candidate/CV evidence.
7. Uploads `cv_pdf` and `cover_letter_pdf` with Playwright file APIs.
8. Stops when final submit appears. It has no final-submit click path.

Missing candidate evidence never becomes an invented `No`. Add a confirmed
answer to one of these metadata dictionaries when needed:

```json
{
  "question_answers": {
    "Do you have experience with SPSS?": "No"
  }
}
```

The aliases `application_answers` and `form_answers` are also supported.

## Evidence

Each run creates a directory under `UAA_DATA_DIR/live-runs` containing:

- `report.json`
- step screenshots
- `before-final-submit.png` when review is reached
- `final.png`
- `final-page.html`
- `trace.zip`

`report.json` contains the final URL, complete click path, field outcomes,
upload outcomes, blocker reason, and `submitted=false`.

## Result Meanings

- `review_ready`: fields are filled and UAA stopped before final submit (or
  after filling when no submit control was present).
- `needs_user_input`: login/CAPTCHA/security/payment blocker, required unknown
  field, validation error, unknown apply path, timeout, or navigation loop.
- `failed`: browser startup or an unexpected execution error failed safely.

CLI exit codes are 0 for `review_ready`, 3 for `needs_user_input`, and 2 for
`failed` or invalid input.

## Opt-In Real-Site Test

This test is marked `live` and is disabled unless explicitly enabled:

```powershell
$env:UAA_ENABLE_LIVE_TEST="1"
$env:UAA_LIVE_APPLICATION_ID="<ID_PREFIX>"
.\.venv\Scripts\python.exe -m pytest `
  tests/live/test_live_browser_real_job.py -m live -s
```

Even in this mode, the runner never clicks final submit.
