# Live Browser Dry-Run

## Purpose

The live runner is UAA's own Playwright execution path. It does not depend on
Codex, a browser extension, or Chrome's file-URL permission. Its
`LiveBrowserRunner.run` path opens one imported job, follows the apply path,
fills known fields and can select queued documents in supported file inputs.
This guard does not protect every UAA observation/preparation entry point, and
selecting a local file does not prove that the ATS accepted an upload.

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
The `browser-session --attachable` option and live-dry-run CDP/browser-session
attachment are temporarily unavailable while the safety guard requires a
fresh UAA-owned browser context. Use the standard UAA-launched browser path.
Verified same-tab resume is planned for V2-04.

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
7. Selects `cv_pdf` and `cover_letter_pdf` in supported file inputs with
   Playwright file APIs. For native inputs, `selection_verified` means only
   that the expected local filename was selected. An asynchronous uploader is
   `remote_accepted` only when its declared acceptance status was observed.
8. Stops when final submit appears. It has no final-submit click path.

Before any page is created or target navigation begins, the `LiveBrowserRunner`
context guard permits HTTP `GET`, `HEAD` and `OPTIONS`, and blocks all other or
unknown HTTP methods, including form/fetch POSTs and `sendBeacon`. A blocked
mutation stops the run for owner input. Reports and CLI diagnostics retain
only method, resource type, origin and reason; they omit request paths, query
strings, headers and bodies. If route continuation or abort handling fails,
the remote outcome is unknown: reconcile the application state before
retrying.

This is scoped HTTP-route coverage, not a universal no-side-effect guarantee.
WebSocket messages and GET endpoints with server-side effects are outside the
guard. Internally created runner contexts block service workers; caller-owned
contexts with existing pages or active workers fail closed, but dormant worker
registrations cannot be proven absent. The API review-observation endpoint and
supervisor prepare/retry paths still use a separate execution service and are
not covered yet (unresolved V2-02 P1). Do not infer that all UAA preparation is
protected by this runner guard.

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
upload outcomes, blocker reason, and `submitted=false`. Here, `false` means
UAA did not confirm a submission; when `request_outcome_unknown=true`, it does
not prove that the remote site received no application. Reconcile the remote
state before retrying.

## Result Meanings

- `review_ready`: the runner claims it reached review without submitting. A
  known V2-02 readiness bug can also produce this status after filling when no
  final submit control was found. In that case the final review boundary is
  unverified; do not treat the status as proof that the form is ready. This
  false-readiness path remains a V2-02 follow-up.
- `needs_user_input`: login/CAPTCHA/security/payment blocker, required unknown
  field, validation error, unknown apply path, timeout, navigation loop, or a
  blocked non-read-only HTTP request. Review the page and report before
  continuing manually; reconcile remote state before retry if request handling
  was uncertain.
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
