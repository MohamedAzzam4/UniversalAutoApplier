# Active Workpackage

- **WP ID:** WQ-7B — Real ATS Navigation Reconnaissance.
- **Status:** IN PROGRESS — initial handoff checkpoint (post branch creation, pre live navigations).
- **Branch:** `checkpoint/wq-7b-real-ats-navigation`
- **Base SHA:** `6326e4e0815d2d325eccc5bf3671afefd8e5bc8b` (`origin/main`, WQ-7A squash)
- **PR:** none yet (to be opened at the end of the workpackage)
- **Last completed/checkpoint SHA:** base `6326e4e` + initial handoff commit (this file). Resolve the head dynamically.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/wq-7b-real-ats-navigation
  ```

  The two values must match before handoff/review.
- **Last updated:** 2026-08-15

## Objective

Prove that UAA can safely navigate from real public job-detail pages to real
ATS application forms and observe their structure using the WQ-7A
infrastructure — **navigation and observation only**. Never fill fields,
never upload documents, never log into accounts, never submit applications,
never bypass CAPTCHA/anti-bot.

## Safety rules (non-negotiable for this workpackage)

- No typing into application fields. No autofill. No file uploads.
- No account login. No LinkedIn Easy Apply or other authenticated
  in-platform applications. No CAPTCHA/anti-bot bypass.
- No clicking final submit/review/continue controls once an application
  form is reached — the reconnaissance STOPS at first application-form
  detection.
- No use of the user's real CV, profile, cookies, browser profile, or
  personal data. Synthetic/recon-only identity is used for the job record.
- No real application submission. Submit interlocks and network safety
  controls are never weakened.
- Never store cookies, authorization headers, session tokens, browser
  profiles, user data, or full third-party HTML with sensitive content.

## Planned targets (one currently-open public job per supported ATS)

- Greenhouse (`job-boards.greenhouse.io/<company>`)
- Lever (`jobs.lever.co/<company>`)
- Workday (`myworkdayjobs.com` / Workday ATS-hosted job pages)
- SmartRecruiters (`careers.smartrecruiters.com/<company>`)
- iCIMS (iCIMS-hosted or branded career pages)

At least five real-job attempts; every supported ATS attempted; at least
three distinct ATS platforms must reach a real public application form.
Unsuccessful platforms get exact evidence and classification. Zero values
typed, zero files uploaded, zero final submit clicks; submit interlock stays
active through every run.

## Implementation approach (per the prompt's policy)

1. First try WQ-7A exactly as implemented (reuse `analyze_page`,
   `click_action`, `choose_safe_action`, `SubmitSafetyGuard`
   `REAL_SITE_DRY_RUN`, `install_interlock`/`read_counters`,
   `LiveBrowserRunner` fixture behavior).
2. WQ-7B scope difference: the WQ-7A fill path (`execute_live_form`) types
   into fields and uploads files once a form is detected. WQ-7B forbids
   that, so a **navigation/observation-only reconnaissance mode** is being
   added (a real WQ-7B capability, not a "make it pass" change): it opens,
   follows the apply path, and stops at the first application form, typed=0,
   uploaded=0, submit clicks=0, interlock armed.
3. iCIMS is not yet a WQ-7A configured platform; it is being added to this
   branch's recon matrix (+ unit/playwright fixture coverage, opt-in live).
4. Only send at most up to two replacement URLs per platform when a real
   recorded URL is closed/inaccessible/login-only.
5. No default-CI test depends on internet access. Live recon tests are
   marked `live` and skipped unless explicitly enabled.

## Initial verification (2026-08-15)

- `origin/main` == `6326e4e...` (exact, matches the required base).
- Branch created from `origin/main`; untracked debug artifacts preserved and
  never staged (`tmp_debug_status.py`, `tmp_debug_status/`,
  `tmp_final_pipeline/`).
- WQ-7A infra reviewed: `live_dry_run_platforms`, `live_runner`,
  `execution_mode` (`SubmitSafetyGuard`), `submit_interlock`, `live_models`,
  `apply_path_finder` (`analyze_page`/`choose_safe_action`/`click_action`),
  `config` (WQ-7 env settings), `cli` (`live-dry-run-platforms`).
- No documented WQ-7B requirement conflicts with this prompt
  (`DRY_RUN_LEVELS.md` Level 2 = live external dry-run, never submits;
  `WQ7_LOCAL_LIVE_RUN.md` Stage 1 = navigation-only reconnaissance).
- Network reachability spot check from this machine: greenhouse 200, icims
  200, lever 308 (redirect), smartrecruiters root 404 (platform-normal for
  root path).

## Exact next action

1. Identify a real, currently-open public job per ATS (web; prefer employer
   or ATS-hosted URLs over aggregators).
2. Implement the navigation-only recon mode + iCIMS support + tests.
3. Register the recon targets in env and run the opt-in live navigation
   command; record evidence; write `docs/evidence/wq-7b/`.
4. Run the full validation gate; push checkpoints after each milestone.
5. Open one PR against `main`; wait for the six CI checks.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit reviewed WQ-7B files; never commit live-runs data, `.uaa_data`,
  `.env`, browsers/databases, screenshots with real data, or the tmp debug
  dirs. `git diff --check` before committing.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.