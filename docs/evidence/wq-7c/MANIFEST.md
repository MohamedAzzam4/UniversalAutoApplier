# WQ-7C Evidence Manifest — Live Real-ATS Synthetic Mutation Proof (pre-submit)

> **Index note (final acceptance):** this manifest is the component-level
> real-ATS evidence (Greenhouse + Lever). The authoritative WQ-7C acceptance
> evidence is `FULL_SAME_JOB_CLOSURE.md` (full-system Robco/Ashby trace) and
> the final acceptance summary is `FINAL_ACCEPTANCE.md`, both in this
> directory.

Status: **READY for review** (both supported ATS platforms reached, mutated,
and stopped pre-submit; two UAA detector defects found, fixed, regression-tested,
and pushed during the proof).

Date: 2026-08-17 (runs 01:15–02:41 UTC)
Branch: `checkpoint/wq-7c-synthetic-mutation`
Head: resolve dynamically (`git rev-parse HEAD` / `git rev-parse origin/<branch>`)

## Acceptance criterion

- Two distinct real public ATS application forms are exercised by the WQ-7C
  synthetic mutation path (`live-synthetic-mutation`) **pre-submit**:
  - Greenhouse (Carta — Account Executive, Legal Services)
  - Lever (Apply Digital — Agentic Product Design Lead)
- Synthetic-only values (flagged candidate profile) and approved
  banner-labelled synthetic documents are used; **no real data**.
- The run must create a mutation plan with deterministic field mapping, fill
  fields, upload approved synthetic documents, and stop **before final
  submit** with `submitted=false` and zero non-zero submission counters other
  than interlock-blocked page-initiated events.
- Any UAA-caused defect discovered during the proof is fixed with a hermetic
  regression test and pushed before continuing.

## Environment for every run

- `UAA_LIVE_SYNTHETIC_MUTATION=true`
- `UAA_ENABLE_REAL_SUBMISSION=false` (mutually exclusive with the above)
- ephemeral headless browser profile
- synthetic candidate profile (Test Candidate / test.candidate@example.com)
- synthetic CV `wq7c-test-cv.pdf` with the banner
  "SYNTHETIC TEST DOCUMENT / NOT A REAL CANDIDATE / DO NOT PROCESS AS AN
  APPLICATION", hash-approved via `approved_document_hashes`
- artifact runs kept under `$env:LOCALAPPDATA\Temp\opencode\uaa_wq7c_*`
  (outside the repository). Reports summarized here contain no screenshots,
  traces, HTML snapshots, or candidate data.

## Queue / store seeding

- Synthetic probe queue:
  `$env:LOCALAPPDATA\Temp\opencode\uaa_wq7c_queue\synthetic_probe_queue.jsonl`
- Imported via `python -m universal_auto_applier queue-import --path <abs>`
  into `UAA_DATA_DIR=$env:LOCALAPPDATA\Temp\opencode\uaa_wq7c_data`
- Final import run: `run_id b032d6d04e364e36b7ce606cdc395414`, total 3
  imported 3, skipped 0, errors 0
- Deterministic application IDs:
  - Greenhouse Carta: `fdda3f191ee1b1994f4ed6c9246824feee9c1a51e5eca7f1aa77b38ebfd69600`
  - Lever Apply Digital: `f02ef0fb6ce1a5e1c7df13952e559c872a9dbbfe502fc4f00e967c6b9927408a`
  - (Greenhouse Anthropic: `0367d1d84ebd...` — superseded by Carta, see
    Defect 1 below)

## Defects found and fixed during the proof (hermetic + gates + pushed)

### Defect 1 — Greenhouse universal invisible reCAPTCHA badge misclassified as `captcha_detected`

- Symptom: every Greenhouse job board rendered a fixed off-screen
  `div.grecaptcha-badge` (`size=invisible`, `right:-186px`). Playwright's
  `is_visible()` returns true for it (non-empty bounding box), and its anchor
  iframe body text is "protected by reCAPTCHA". `_detect_blocker` therefore
  returned `captcha_detected` on **every** Greenhouse board — first observed
  on Anthropic, reproduced on Carta.
- Root cause (two parts):
  1. `apply_path_finder.py` captcha widget selector matched
     `iframe[src*='recaptcha']` without excluding `size=invisible` badges.
  2. `analyze_page` aggregated `_frame_text` from the recaptcha anchor frame,
     whose body text contains the substring "captcha".
- Fix (commits `fd155f6`, `aae24d0`):
  1. Widget selector now excludes invisible-badge iframes:
     `iframe[src*='recaptcha']:not([src*='size=invisible'])` while keeping
     `.g-recaptcha`, `.h-captcha`, `[data-sitekey]`, and hcaptcha iframes.
  2. `analyze_page` skips text from anti-bot widget frames
     (`_CAPTCHA_WIDGET_URL_MARKS`).
- Regression (hermetic):
  `tests/fixtures/live_browser/invisible_recaptcha_badge.html` +
  `www.recaptcha.net/recaptcha_anchor.html` +
  `test_live_browser_executor.py::test_invisible_recaptcha_badge_is_not_a_captcha_blocker`
  (form reaches `review_ready`), and
  `test_visible_recaptcha_widget_still_blocks_after_badge_fix` (`.g-recaptcha`
  still `captcha_detected`).
- Safety note: real user-facing challenges are NOT weakened — only the
  invisible badge pattern is excluded.

### Defect 2 — Lever `cards[<uuid>][fieldN]` section names misread as `payment_required`

- Symptom: Lever names every application section
  `name="cards[<uuid>][field0]"`. `_detect_blocker` matched
  `input[name*='card' i]` and returned `payment_required`, blocking the form
  before any mutation.
- Fix (commit `162233d`): payment selector now excludes the `cards[`
  array-group convention:
  `input[name*='card' i]:not([name*='cards[' i])`. Real payment fields
  (`autocomplete^='cc-'`, `card_number`, `card_cvv`) still block.
- Regression (hermetic):
  `tests/fixtures/live_browser/lever_cards_groups.html` +
  `payment_wall.html` +
  `test_lever_cards_named_groups_are_not_a_payment_wall` (reaches
  `review_ready`) and `test_real_card_field_still_blocks_after_cards_fix`
  (`payment_required`).

### Verification gates after the fixes

- `ruff check` / `ruff format --check` / `pyright`: all pass (0 errors).
- Non-live suite: **1507 passed** (was 1505 baseline; +2 regression tests in
  the payment/cards area; earlier full-suite runs 1505 passed).
- Playwright suite: **267 passed** (265 baseline + 2 new captcha tests) before
  the cards commit; related suites (live_browser_executor, wq7b_recon_mode,
  wq7c_synthetic_mutation, wq7c_mutation_plan) all green.
- `git diff --check` clean. Commits pushed; local == origin each time.

## Target matrix

### greenhouse — Carta — MUTATION REACHED (pre-submit)

- Initial URL: `https://job-boards.greenhouse.io/carta/jobs/7822002003`
- Final URL: unchanged
- Result: `needs_user_input`, `stopped_reason=required_fields_unresolved`
- Mutation plan: `mutation-plan.json`, `plan_hash=c45c3a53...`
- Plan chain: 2 passes, `plan_chain_hash=d3fbef5f...`
- Fields: 19 planned; 10 filled (first name, last name, email, country,
  phone, …), 9 not-filled
- Uploads: 1 (cv, `wq7c-test-cv.pdf`, status uploaded, "approved synthetic
  document")
- Interlock: installed=true, all counters 0, `submitted=false`
- Not-filled (correct, safe):
  - `failed` — Location (City): autocomplete field, Playwright fill refused
    ("Input of type …")
  - `failed` — Attach cover_letter: `set_input_files` timeout on the dynamic
    field
  - `intervention_needed` — LinkedIn Profile, "Have you worked for Carta",
    etc.: no deterministic mapping; not fabricated
  - `skipped` — visa sponsorship, Gender, Hispanic/Latino, Veteran, Disability:
    sensitive/demographic categories never auto-answered in synthetic mode

### lever — Apply Digital — MUTATION REACHED (pre-submit)

- Initial URL: `https://jobs.lever.co/applydigital/e67e06b6-48e7-471d-8050-34127416dcf8`
- Final URL: `https://jobs.lever.co/applydigital/.../apply`
- Click path: 1 step — "APPLY FOR THIS JOB" (`safe_apply`) → `/apply`
- Result: `needs_user_input`, `stopped_reason=required_fields_unresolved`
- Mutation plan: `plan_hash=e3c2cfe6...`
- Plan chain: 2 passes, `plan_chain_hash=6cc80e9b...`
- Fields: 21 filled (name, email, phone, …)
- Uploads: 2 (cv twice — Lever's form exposes two resume inputs;
  both `wq7c-test-cv.pdf`, status uploaded)
- Interlock: installed=true; `form_submit_calls=1`,
  `blocked_submissions=1`, `submitted=false`
- Interlock event attribution: same signature as WQ-7B — Lever's SPA calls
  `HTMLFormElement.prototype.submit()` programmatically during its own load.
  `submit_events=0`, `request_submit_calls=0`, `dispatch_submit_events=0`,
  `uaa_submit_clicks=0`; UAA performed no submit (no final-submit click, no
  fill of a submit control). The interlock blocked it with zero effect.
- Not-filled (correct, safe): location option mismatch (intervention),
  Full name/LinkedIn/Twitter/GitHub (no deterministic mapping, not
  fabricated), `cards[...]` custom questions (intervention_needed), current
  company (skipped), consent checkbox (consent_signature never auto-answered).

### greenhouse — Anthropic — SUPERSEDED by Carta (Defect 1)

- Initial URL: `https://job-boards.greenhouse.io/anthropic/jobs/4949336008`
- First probe blocked at `captcha_detected` with 0 mutations — the defect
  above. After Defect 1 fix, Carta (same platform) reached mutation, so the
  Greenhouse requirement is satisfied by Carta. Anthropic remains usable as a
  replacement pool target if desired.

## Evidence-keeping

- Per-run `report.json`, `mutation-plan.json`, screenshots, `trace.zip`, and
  `final-page.html` remain in the local `uaa_wq7c_*` artifact directories
  (outside the repo, never committed).
- This manifest records only sanitized summaries and per-attempt safety
  counters as required by the WQ-7C plan.

## Safety attestation

- Every run: `submitted=false`, interlock installed, `uaa_submit_clicks=0`.
- Only deterministic candidate-fact values and approved synthetic docs were
  used; no real PII, no real CV.
- Never clicked a final-submit/review/completion control.
- Stopped at `required_fields_unresolved` precisely for the fields UAA must
  not auto-answer (demographics, work authorization, consent, unmapped free
  text), and for genuinely unresolvable autocomplete/upload UI.
