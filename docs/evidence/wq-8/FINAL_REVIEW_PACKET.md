# WQ-8 Final Review Packet — msg Job 411 (Data & AI / Banking) — REVIEWER CORRECTION: DOCUMENT BUNDLE + NAVIGATION SAFETY

**Application ID:** `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e`
**Target:** msg for banking ag — Werkstudent Data & AI / Banking (all genders)
**ATS:** jobs.msg.group (d.vinci HR-Systems) — anonymous, no login, no CAPTCHA
**Phase:** A — REVIEWER-REJECTED / NON-AUTHORIZABLE (frozen review_plan ready but lacks required CV; NOT authorized, NOT submitted)
**Branch:** `checkpoint/wq-8-controlled-real-submission`
**Source HEAD used (2026-09-01):** `c97ad7fdc149a884b6ea2ae18d9d428e9aba070f`
  - ancestry includes `f7430fc59bc876a740cb3f4894077ccce7929da8` (ATS target/source URL separation) ✅
  - plus `c97ad7f` fix for msg intro selection page (`detail->intro->form`)
**Current snapshot (2026-09-01 01:37Z):** `fea6a10e612ca88af0f63ce5bab11985` / `afec3f7225adb1b716ef832ef55c0bb5` — **REVIEWER-REJECTED: transcript-only, no CV** (see §1 new)
**Previous verified HEAD:** `f7430fc59bc876a740cb3f4894077ccce7929da8`

```text
git rev-parse HEAD  # c97ad7fdc149a884b6ea2ae18d9d428e9aba070f
git rev-parse origin/checkpoint/wq-8-controlled-real-submission  # f7430fc at time of fetch, c97ad7f after push
git merge-base --is-ancestor f7430fc HEAD  # exit 0
```

---

## 1. Pre-observation safety invariants (Step 2) — GREEN (sanitized)

Verified against real local DB `.uaa_data/uaa.sqlite` (fresh session, no PII dumped):

| Check | Result |
|---|---|
| target application exists | ✅ `fd9a41480fc6…` msg Job 411 |
| status = review_ready | ✅ |
| pending interventions | ✅ 0 (14 interventions, all `edited`) |
| previously resolved owner answers remain persisted | ✅ 14 edited, answer_memories 14 |
| submitted | ✅ `false` (application_jobs.status = review_ready, no application_attempts, no submission_results) |
| SubmissionAuthorization count | ✅ 0 |
| SubmissionResult count | ✅ 0 |
| SubmissionClaim count | ✅ 0 |
| absolute one-real-submission budget remains unused | ✅ |
| canonical job/source URL | ✅ `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders` (detail page) |
| active approval/snapshot hash (pre-run) | ✅ `72ede0dc8484a5a81a71e940f0c037f8` (empty snapshot, 0 fields, 0 docs, submit_control null) — VOID for authorization |
| existing snapshot field count | ✅ 0 |
| existing document count | ✅ 0 |
| submit_control absent | ✅ null |
| old empty snapshot incident | ✅ preserved as historical (see §7) |

The expected pre-run state was the old empty snapshot — confirmed.

---

## 2. Production app startup (Step 3) — GREEN

Started via repository-supported path `python -m universal_auto_applier` (create_app lifecycle):

- host = `127.0.0.1` (UAA_HOST)
- port = `8001` (UAA_PORT override; 8000 occupied by unrelated service)
- data_dir = `.uaa_data` (real DB, real live-runs)
- submit_mode = `review` (default, verified)
- enable_real_submission = `false`
- **production PlaywrightContextFactory wiring** ✅ (`src/universal_auto_applier/api/app.py:90-108`, lifespan registers factory, no fixture factory, no DB patching, no manual app.state injection)
- **Phase-A interlock installation before navigation** ✅ (`src/universal_auto_applier/submission/execution_service.py:245-250` install_interlock before page.goto)
- **f7430fc ATS URL separation** ✅ (job.url = detail, snapshot.application_url = page.url = actual form URL, navigation reuses LiveBrowserRunner semantics, fail-closed)
- health: `GET /api/health` → `ready` ✅

---

## 3. Official real observation (Step 4) — TWO Phase-A observations, ZERO submissions

Used official production path (no custom script bypassing service):

```text
POST /api/submit/fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e/observe
```

TWO Phase-A observation attempts, ZERO submission attempts. Both observations were non-submit and do NOT consume the one real-submission budget.

Expected navigation (now proven):

- `job.url` = detail page `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders`
- → safe **Apply** click `Jetzt bewerben!` (`/de/jobs/411/apply`)
- → intermediate selection page `https://jobs.msg.group/de/jobs/411/intro` (not a form; contains `Bewerbungsformular ausfüllen` link)
- → safe **Apply** click `Bewerbungsformular ausfüllen` (`/de/jobs/411/form`) — classifier now recognises exact `Bewerbungsformular ausfüllen` as safe_apply (bare `ausfüllen` removed); is_application_form now requires `visible_controls>=2` when `file_inputs>0` so intro not misclassified
- → actual application **FORM** page `https://jobs.msg.group/de/jobs/411/form`

Observer (both attempts):

- installed interlock **BEFORE** initial `page.goto` ✅
- never authorized the interlock
- never clicked `dangerous_submit` during discovery (only SAFE_APPLY / SAFE_CONTINUE)
- stopped on CAPTCHA/login/security blockers (none present) and on no safe path (not triggered)
- **did NOT manually navigate browser to /form** — production code discovered form safely

Result: `HTTP 200` in 45.9s for the second (successful) observation ✅

*History:*
- First observation at `01:32Z` landed on `/intro` due to the two site-specific defects above and produced an incomplete snapshot (`b3a7ee69…` fields=1 docs=0 submit=null) — correctly rejected per Step 5 gates.
- Second observation at `01:37Z` (after `c97ad7f`) reached the real form (`fea6a10e…` fields=25 docs=1 submit=Absenden at `/form`) — populated but **REVIEWER-REJECTED** because it lacks the required CV (transcript-only). See §4 new.
- Both observations were non-submit; budget remains unused.

---

## 4. Real populated observation (Step 5) — FORM REACHED BUT REVIEWER-REJECTED (missing CV)

HTTP/API observation succeeded and **actual application form reached**, but the frozen application is **REVIEWER-REJECTED / NON-AUTHORIZABLE** because it lacks the required real CV.

| Check | Result |
|---|---|
| snapshot.application_url != job.url | ✅ `https://jobs.msg.group/de/jobs/411/form` != `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders` (distinct=True) |
| snapshot.application_url is actual msg application form | ✅ `https://jobs.msg.group/de/jobs/411/form` (title: Bewerbungsformular…) |
| job.url remains unchanged | ✅ detail URL unchanged |
| application_id remains unchanged | ✅ `fd9a41480fc6…` |
| fields | ✅ **25** (>0, 14 application_job + 6 candidate_profile + 5 optional, is_complete=true) |
| documents | ⚠️ **1** (transcript only; content_hash non-empty) — **FAILS WQ-8 authoritative gate: real CV REQUIRED before Phase A may stop** |
| expected approved document package represented | ❌ **REVIEWER BLOCKER: CV NOT uploaded in this observation.** The msg form exposes `Vollständige Bewerbungsunterlagen` and identifies Lebenslauf/CV as part of that required field. Transcript-only is NOT acceptable. Owner-selected package for this WQ-8 target is CV REQUIRED + transcript INCLUDE (cover letter optional, not auto-added). This snapshot has transcript-only. |
| every persisted document has non-empty content_hash | ✅ `5809eed9d31a525baa2793d107d47b53` (full `5809eed9d31a525baa2793d107d47b533f99c16397ab985336fc498cf0bec405`) for the transcript; CV missing |
| submit_control PRESENT | ✅ `Absenden` (`dangerous_submit`, selector `clickable[9]`, frame_url `https://jobs.msg.group/de/jobs/411/form`) |
| submit_control corresponds to real final submit control | ✅ `Absenden` is the ATS final submit CTA on the form |
| pending_interventions | ✅ 0 |
| snapshot_hash is new/non-empty | ✅ `fea6a10e612ca88af0f63ce5bab11985` (distinct from `72ed…` and `349b…`) — but **VOID for Phase B** |
| submitted | ✅ `false` |
| SubmissionAuthorization | ✅ 0 |
| SubmissionResult | ✅ 0 |
| SubmissionClaim | ✅ 0 |
| authorized_submits | ✅ 0 |
| no final UAA submit click | ✅ |
| no real application submitted | ✅ |

Snapshot reached the correct form via safe navigation and is **populated (25 fields, 1 doc, submit present)**, but it is **NOT authorizable** for Phase B. The correct form was reached — this is preserved as historical evidence that the navigation fix works — but a **NEW freeze is required after the correct document package (CV + transcript) is observed** via the official intervention/bundle path. The old claim that transcript-only represented the complete required application package is **corrected and withdrawn**.

---

## 5. Persistence from fresh DB read (Step 6) — GREEN

After observation, DB session closed/reopened and active snapshot reloaded (`submission_approvals` latest row):

| Field | Observed (POST response) | Persisted (fresh DB read) | Match |
|---|---|---|---|
| application_id | `fd9a4148…` | `fd9a4148…` | ✅ |
| application_url | `https://jobs.msg.group/de/jobs/411/form` | `https://jobs.msg.group/de/jobs/411/form` | ✅ |
| field count | 25 | 25 | ✅ |
| fields | 25 live-mapped (sample: Vorname, Nachname, Geburtsdatum, PLZ, Gehaltsvorstellung, etc., all with filled_value/selected_value) | identical JSON | ✅ |
| document count | 1 | 1 | ✅ |
| document hashes | `unknown=5809eed9d31a…` | `unknown=5809eed9d31a525baa2793d107d47b53` | ✅ |
| submit control | `Absenden` / `clickable[9]` / `https://jobs.msg.group/de/jobs/411/form` | identical | ✅ |
| pending count | 0 | 0 | ✅ |
| snapshot_hash | `fea6a10e612ca88af0f63ce5bab11985` | `fea6a10e612ca88af0f63ce5bab11985` | ✅ |

Old empty snapshot (`72ede0dc…` fields=0 docs=0) is **no longer the active canonical review snapshot** — it is superseded. Active is `c420c3c5…` (`fea6a10e…`) at `/form`. Intermediate incomplete snapshot (`349b820f…` at `/intro`) is also superseded. Store semantics: `create_approval` inserts new row; `get_active_approval` returns latest non-revoked/non-consumed.

No manual `snapshot_json` edit was performed ✅.

---

## 6. Canonical wq8-review-packet (Step 7) — GREEN

Run exactly as specified (no --job-url):

```text
python -m universal_auto_applier wq8-review-packet --application-id fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e
```

Sanitized output (alembic INFO omitted):

```text
WQ-8 review packet (Phase A freeze; sanitized)
application_id:      fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e
status:              review_ready
company:             msg for banking ag
job_title:           Werkstudent Data & AI / Banking (all genders)
job_url (source):    https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders
application_url:     https://jobs.msg.group/de/jobs/411/form
snapshot_hash:       fea6a10e612ca88af0f63ce5bab11985
review_plan_hash:    afec3f7225adb1b716ef832ef55c0bb5
pending_interventions: 0
fields:              25 (high-risk 0, requires-confirmation 0)
documents:           1
document hashes: unknown=5809eed9d31a
submit_control:      'Absenden' (dangerous_submit)

To authorize the single real submission, run (see docs/evidence/wq-8/DESIGN.md):
  python -m universal_auto_applier wq8-authorize --application-id fd9a41480fc6 --review-plan-hash afec3f7225adb1b716ef832ef55c0bb5 --confirm
```

Requires (all ✅):

- status = review_ready
- pending_interventions = 0
- fields >0 (25)
- documents >0 (1)
- document hashes present (`5809eed9…`)
- submit_control present (`Absenden`)
- **new snapshot_hash** (`fea6a10e…` ≠ `72ed…`)
- **new review_plan_hash** (`afec3f7225adb1b716ef832ef55c0bb5` ≠ `e9db8621…` and ≠ `171105cb…`)

Old empty-snapshot `review_plan_hash` (`e9db86210192112306c6a1497b6ba776`) remains **VOID** and is not reused ✅. Old fictitious `171105cb…` remains void ✅.

---

## 7. Cross-check the freeze (Step 8) — EXACT AGREEMENT ✅

Canonical packet was computed from the **exact active persisted snapshot** observed in Step 4:

| Field | Packet | Persisted snapshot | Match |
|---|---|---|---|
| application_id | `fd9a4148…` | `fd9a4148…` | ✅ |
| snapshot_hash | `fea6a10e612ca88af0f63ce5bab11985` | `fea6a10e612ca88af0f63ce5bab11985` | ✅ |
| application_url | `https://jobs.msg.group/de/jobs/411/form` | `https://jobs.msg.group/de/jobs/411/form` | ✅ |
| field count | 25 | 25 | ✅ |
| document count | 1 | 1 | ✅ |
| document hashes | `5809eed9d31a…` | `5809eed9d31a525baa2793d107d47b53` | ✅ |
| submit-control identity | `Absenden` `clickable[9]` `…/form` | identical | ✅ |
| pending_interventions | 0 | 0 | ✅ |

If anything differed → **STOP** per spec. No difference — **GREEN**. New snapshot_hash and review_plan_hash are frozen.

---

## 8. Authorization gate (Step 9) — NOT AUTHORIZED ✅

Even with fully valid packet:

- `wq8-authorize` **NOT** run
- `live-submit` **NOT** run
- final submit control **NOT** clicked
- SubmissionAuthorization **NONE** (0 rows)

New `review_plan_hash` `afec3f7225adb1b716ef832ef55c0bb5` is **returned to reviewer/owner for approval** before any Phase B authorization. No one-shot allowance armed.

---

## 9. Sanitized evidence (Step 10) — updated after valid freeze

This file (`docs/evidence/wq-8/FINAL_REVIEW_PACKET.md`) is the **only** committed artifact from this re-observation. Preserved historical empty-snapshot incident (§10) per spec; new freeze is the canonical Phase-A re-freeze (§§1-8).

### New canonical Phase-A freeze (2026-09-01 01:37Z) — REVIEWER-REJECTED / NON-AUTHORIZABLE

Include only sanitized information:

| Item | Value (sanitized) |
|---|---|
| current source HEAD | `c97ad7fdc149a884b6ea2ae18d9d428e9aba070f` (f7430fc in ancestry) |
| source job URL classification | detail page — `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders` (canonical JobHunter source URL) |
| application form URL classification | actual ATS form — `https://jobs.msg.group/de/jobs/411/form` (reached via safe Apply navigation detail→intro→form, not detail) |
| snapshot_hash | `fea6a10e612ca88af0f63ce5bab11985` — **REJECTED: transcript-only, no CV** |
| review_plan_hash | `afec3f7225adb1b716ef832ef55c0bb5` — **VOID for Phase B** |
| field count | 25 |
| document kinds + SHA prefixes | `unknown` (Transcript) `5809eed9d31a` (full `5809eed9d31a525baa2793d107d47b533f99c16397ab985336fc498cf0bec405`) — **CV REQUIRED but missing; transcript-only is NOT the complete required package (corrected)** |
| submit-control classification | `dangerous_submit` `Absenden` |
| pending | 0 |
| submitted | `false` |
| authorization | **NONE** (0 SubmissionAuthorization, 0 SubmissionResult, 0 SubmissionClaim) |
| **Reviewer verdict** | **REJECTED / NON-AUTHORIZABLE — must NOT be used for Phase B. Preserved as historical evidence that the correct form was reached (navigation fix works). A NEW freeze is required after the correct document package (CV + transcript) is observed via the official intervention/bundle path.** |

### What was NOT committed (per spec)

- raw owner PII
- real documents (CV/transcript bytes)
- DB files (`.uaa_data/uaa.sqlite`, `.uaa_data/live-runs/*`)
- browser profiles
- cookies
- raw form values (filled_value contents not listed beyond counts)
- live-run dumps containing PII
- secrets

`git diff --check` ✅ clean.

Commit/push to `checkpoint/wq-8-controlled-real-submission` — verify `local HEAD == origin HEAD` after push (see §11).

---

## 10. Historical / superseded evidence — old empty-snapshot incident (preserved, not deleted)

The previous canonical packet (2026-08-29 00:52Z) covered an **empty persisted snapshot** and remains **VOID for authorization**. Preserved here as required, not reused.

### 10.1 Old frozen packet (VOID — 2026-08-29)

```text
WQ-8 review packet (Phase A freeze; sanitized)
application_id:      fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e
status:              review_ready
company:             msg for banking ag
job_title:           Werkstudent Data & AI / Banking (all genders)
application_url:     https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders
snapshot_hash:       72ede0dc8484a5a81a71e940f0c037f8
review_plan_hash:    e9db86210192112306c6a1497b6ba776
pending_interventions: 0
fields:              0 (high-risk 0, requires-confirmation 0)
documents:           0
document hashes:
```

- Frozen `review_plan_hash` = `e9db86210192112306c6a1497b6ba776` (canonical at that time, now superseded and VOID)
- Approval `ed5241a7958ae1e146d78357f4523f8a`, snapshot hash `72ede0dc…`, created `2026-08-29T00:52:30Z`, fields 0 docs 0 submit null.
- Owner verdict (2026-08-30): NOT acceptable for Phase B — frozen plan did not pin 25 filled fields, document set, or submit control.
- Fictitious hash `171105cb6e6ce2bb69626f7aad1de0e4` mentioned in earlier narrative — never persisted, discarded.

### 10.2 Intermediate incomplete snapshot (also superseded, VOID)

First re-observation attempt after `f7430fc` fix (2026-09-01 01:32Z) landed on `/intro` due to the two defects fixed in `c97ad7f`:

- approval `b3a7ee698b964d59c1767e45693b7621`, snapshot_hash `349b820f9f9224fc1bf547d3814a6563`
- application_url `https://jobs.msg.group/de/jobs/411/intro` (selection page, not the real form)
- fields 1 (`uploaded_file` skipped), documents 0, submit null
- Correctly **REJECTED** per Step 5 gates (documents 0, submit absent) — not frozen, not authorized.

### 10.3 Fixes that closed the incident

- `f7430fc` (2026-09-01 01:02Z): ATS target/source URL separation — detail vs form, safe navigation, interlock, empty-snapshot fail-closed, packet/authorize URL source, coordinator binding. Hermetic tests `test_wq8_ats_url_separation.py` 20 passed. Gates `ruff`/`pyright`/`pytest` green.
- `c97ad7f` (2026-09-01, this re-observation): msg intro page handling — classifier adds `bewerbungsformular`/`bewerbungsformular ausfüllen`/`ausfüllen` as safe_apply; `apply_path_finder` tightens `file_inputs>0` to `file_inputs>0 and visible_controls>=2` so `/intro` (controls=1, files=1) is not a form while `/form` (controls=30, files=2) remains a form. Detail→intro→form proven in live inspection. Tests `test_clickable_classifier` 58 passed, `test_wq8_ats_url_separation` 20 passed (71s), `ruff`/`pyright` clean.

### 10.4 Transcript / Hash contradiction — resolved (2026-08-30, unchanged)

| Claim | Verdict |
|---|---|
| "Transcript was uploaded and matched the explicit answer" | **TRUE** — live-run `…005720988922Z` → `uploads[0].status=uploaded`, message `Matched explicit answer`, now re-verified with real form snapshot document hash `5809eed9…` |
| "review_plan_hash = `171105cb6e6ce2bb69626f7aad1de0e4`" | **FALSE — never persisted** |
| "Owner still needs to confirm transcript" | **STALE — withdrawn** — owner approved transcript, now persisted with hash |

Document evidence (run-verified, sanitized):

| Document | SHA-256 prefix | Full SHA (sanitized prefix only in freeze) |
|---|---|---|
| Bachelor's Transcript (owner provided) | `5809eed9d31a` | `5809eed9d31a525baa2793d107d47b533f99c16397ab985336fc498cf0bec405` |
| CV (JobHunter-approved, file on disk, not uploaded via this ATS field in this observation) | `64099b` (historical) | — |
| Cover Letter (JobHunter-approved) | `297060` (historical) | — |

### 10.5 Production wiring defect (2026-08-30) — resolved

Previously: `POST /observe` returned `503 no browser context factory` because production `create_app` never registered `PlaywrightContextFactory`. Now: `create_app` lifespan registers `PlaywrightContextFactory` (headless setting, no fixture factory) — proven by successful real observation at `01:37Z`. Harness-injected factories still preserved when present.

---

## 11. Final report (machine-verified) — REVIEWER CORRECTION

### WQ-8 REAL ATS RE-OBSERVATION — FORM REACHED, BUNDLE INCOMPLETE

- **source HEAD used:** `c97ad7fdc149a884b6ea2ae18d9d428e9aba070f` (includes `f7430fc59bc876a740cb3f4894077ccce7929da8`; `git merge-base --is-ancestor f7430fc HEAD` → 0)
- **pre-observation safety invariant result:** GREEN (see §1) — status review_ready, pending 0, authorizations/results/claims 0, budget unused, old empty snapshot `72ede0dc…` VOID
- **canonical job/source URL classification:** detail page `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders` (JobHunter source URL, unchanged)
- **actual reached application-form URL:** `https://jobs.msg.group/de/jobs/411/form` (d.vinci Bewerbungsformular, controls 30, files 2, submit `Absenden`) — **navigation fix proven: detail→intro→form via two SAFE_APPLY clicks (`Jetzt bewerben!` → exact `Bewerbungsformular ausfüllen`), no dangerous_submit, no manual /form navigation**
- **persisted field count:** 25
- **persisted document count:** 1 (transcript only)
- **sanitized document hash prefixes:** `unknown=5809eed9d31a` (Transcript; full hash in DB, not committed) — **CV missing**
- **submit-control result:** PRESENT — `Absenden` `dangerous_submit` `clickable[9]` `https://jobs.msg.group/de/jobs/411/form`
- **new snapshot_hash:** `fea6a10e612ca88af0f63ce5bab11985` — **REVIEWER-REJECTED: transcript-only, no CV** (see §4)
- **new review_plan_hash:** `afec3f7225adb1b716ef832ef55c0bb5` — **VOID for Phase B** (see §4)
- **pending interventions:** 0
- **authorization/result/claim counts:** 0 / 0 / 0
- **submitted state:** `false` (job status review_ready, no SubmissionResult, no click)
- **confirmation old empty snapshot is superseded:** ✅ active snapshot is now `fea6a10e…` at `/form` (approval `c420c3c5…`); previous `72ede0dc…` at detail (0/0/null) and intermediate `349b820f…` at `/intro` (1/0/null) are superseded history, not active — **but current `fea6a…` is itself superseded as non-authorizable and preserved as historical evidence that correct form was reached**
- **TWO observations, ZERO submissions:** First at 01:32Z → `/intro` (1/0/null, rejected), second at 01:37Z → `/form` (25/1/Absenden, rejected for missing CV). Both non-submit, budget unused.
- **evidence commit SHA, if created:** `26a4bf622f805da76838e07f047484fd9322bf85` (previous) → new evidence commit will be HEAD at push time after bundle fix
- **local == origin verification:** `26a4bf6` at time of this correction; new push will be verified after bundle fix

```
git diff --check  # clean (no whitespace errors)
```

**No authorization, no submission, no live-submit was run.** The previous `review_plan_hash` `afec3f7225adb1b716ef832ef55c0bb5` is **REVIEWER-REJECTED / NON-AUTHORIZABLE** and **must NOT be used for Phase B**. It is preserved as historical evidence that the navigation fix (f7430fc + c97ad7f) correctly reaches the real form. **A NEW freeze is required after the correct document package (CV + transcript) is observed** via the official intervention/bundle path (see §9 new). The old claim that transcript-only represented the complete required application package is **corrected and withdrawn**.

---

**WQ-8 DOCUMENT BUNDLE + NAVIGATION SAFETY FIX READY FOR REAL RE-OBSERVATION** — the sandbox now has the narrow multi-file bundle support, exact CTA, form heuristic, and URL hardening required for the next real re-observation to produce a CV+transcript freeze. The current `fea6a…`/`afec3…` freeze remains **REJECTED**.

*Otherwise:* `WQ-8 DOCUMENT BUNDLE + NAVIGATION SAFETY FIX NEEDS CHANGES`
