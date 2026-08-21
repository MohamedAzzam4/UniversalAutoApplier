# WQ-8 Phase A — Owner Review Packet (SANITIZED)

**Status: STOPPED — awaits owner answers. No submit.**

## Target
- **Company:** msg for banking ag
- **Title:** Werkstudent Data & AI / Banking (all genders)
- **ATS:** jobs.msg.group (d.vinci HR-Systems), anonymous, no login, no CAPTCHA
- **URLs:**
  - Detail: https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders
  - Intro: https://jobs.msg.group/de/jobs/411/intro
  - Form: https://jobs.msg.group/de/jobs/411/form
- **Locations per JD:** Frankfurt, Hamburg, **Ismaning bei München** (Munich-select), Köln, Passau. No Nuremberg variant (Nuremberg posting 4374080022 is Software Engineering — different role).
- **Application ID:** `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e` (sha256 canonical URL; no external_job_id)
- **Queue:** `D:\Programming\Antigravity-Projects\JobHunter\data\application_queue.jsonl` — 1 row, platform `unknown`, source `unknown`, verdict `apply` (WQ-8 owner exception)

## Discovery → Evaluation (canonical, honest)
- **Fetch:** `fetch_jd(https://jobs.msg.group/de/jobs/411/...)` → success=True, 4392 chars, expired=False
- **JD cache seeded:** real JD + title + company `msg for banking ag` (truthful, no invention)
- **Evaluation model:** `google/gemma-4-26b-a4b-it` (Google AI Studio primary)
- **German policy:** default `reject_b1_plus` would → `skip_german` (JD: "Verhandlungssichere Deutsch- und Englischkenntnisse (mind. C1)" → B2+). Owner authorized `accept_all` for this job.
- **Scores:** skills 5 / education 5 / location 2 / language 1 / growth 5 → **global 3.6**
- **Recommendation (honest):** `consider` (language gap: C1 required vs profile German A2 + location spread)
- **Gaps:** German C1 vs A2; Frankfurt/Hamburg/Cologne outside target zone (Ismaning OK)
- **Threshold 3.5:** passes, but exporter requires `apply` → otherwise skipped `not_recommended`
- **CV/cover:** OpenRouter tailors exhausted (5 keys 404/429). Fallback truthfully generated from `cv.md` via WeasyPrint:
  - `output/mohamed-azzam_msg-for-banking-ag_werkstudent-data-ai-banking-cv.pdf` (28930 bytes)
  - `output/mohamed-azzam_msg-for-banking-ag_werkstudent-data-ai-banking-cover.pdf` (11704 bytes)
  - `cv.md` / `cover.md` fallbacks created for exporter `documents` check
- **Queue export exception (owner-authorized, audited):** `consider(3.6)` → exported as `apply` for WQ-8 Phase A only. Original evaluation untouched (`consider`). Added `_wq8_exception: consider_3.6_german_C1_vs_A2_owner_authorized_accept_all` and reason suffix. No score edit.

## UAA Import
- `queue-import --path .../JobHunter/data/application_queue.jsonl` → state success, fingerprint `fd10bd51...`, imported 1, skipped 0
- `list-jobs` → `fd9a41480fc6 ready_to_apply msg for banking ag | Werkstudent Data & AI / Banking`
- **Platform:** `unknown` → generic adapter (`is_trusted=False`) — adapter never auto-submits; Phase A `live-dry-run` always stops before submit. `live-submit` blocked without authorization.

## Phase A Live Fill (deterministic_only — no LLM key)
- Run 1 (detail→intro): `live-dry-run fd9a41480fc6` → `review_ready`, 1 field, stopped at intro selector (needs manual choice) — evidence `.uaa_data/live-runs/fd9a41480fc6-20260821T001904510480Z/`
- Run 2 (direct form): `live-dry-run fd9a41480fc6 --start-url https://jobs.msg.group/de/jobs/411/form --ephemeral-profile --headless` → **`needs_user_input` / `required_fields_unresolved`** — evidence `.uaa_data/live-runs/fd9a41480fc6-20260821T001956392794Z/`
  - `initial_url` form, `final_url` form, clicks 0, fields 25, uploads 0, submitted false, interlock not installed (dry-run)
  - **Filled truthfully (2):** E-Mail-Adresse, Telefon (from profile snapshot)
  - **Skipped optional (5):** Adresszusatz, msg-Mitarbeitende, Staatsangehörigkeit, Kommentar, Bewerbungsunterlagen file (optional per engine)
  - **Interventions (17 pending, all `field_answer pending`):**
    - Vorname*, Nachname*, Geburtsdatum*, Land*, Straße*, PLZ*, Ort*, Gewünschter Einsatzort* (select+text), Gehaltsvorstellung*, Schwerbehinderung/Gleichstellung, Deutschkenntnisse*, Reisebereitschaft*, Kündigungsfrist*, Kanal (Wo hast du von uns erfahren*), Datenschutz Einwilligung (2 radios) — all `"Required field has no deterministic mapping"` → created per `form_engine/live_executor` → persisted via `interventions.store.create_intervention`
  - **High-risk per owner instruction (explicit review, never inferred):** Geburtsdatum, Gehaltsvorstellung, Deutschkenntnisse (German level), plus any DOB/salary/transcript/work-auth — all correctly surfaced as interventions. Engine also left Vorname/Nachname/Address as interventions because German labels have no deterministic English mapper — owner reviews truthfully via dashboard.
- **Intervention store:** 17 rows in `uaa.sqlite` InterventionRow (select from `where application_id=fd9a41...`), all pending

## WQ-8 Authorization Gate
- `wq8-status fd9a41480fc60a...` → **NONE — Real submission is FORBIDDEN** (authorization table empty, `UAA_ENABLE_REAL_SUBMISSION=false` by default)
- `wq8-review-packet` requires a dashboard `SubmissionSnapshot` (created via observe endpoint). No snapshot yet — live-dry-run report is not a snapshot. Packet hash not frozen. **Phase B unavailable until snapshot + review_ready.**

## Sanitized Packet (no filled values, no PII, no paths)
- application_id: fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e
- status: needs_user_input (honest Phase A terminal; review_ready after owner answers)
- company/title/url as above
- snapshot_hash: (none — awaiting dashboard observe)
- review_plan_hash: (not frozen)
- pending_interventions: 17
- fields: 25 (2 filled, 17 intervention_needed, 6 skipped) — all high-risk/sensitive flagged for confirmation
- documents: 2 (CV + cover PDFs, hashes approved via WeasyPrint) — `wq8-authorize` will bind `document_hashes` when frozen
- submit_control: none on form (generic form — submit via `Absenden`; blocked in dry-run)

## Owner Action Required (exact next command)
1. Open dashboard: `.\scripts\run_local.ps1` → http://127.0.0.1:8000
2. In **Interventions** for `fd9a4148`, answer truthfully (no invention):
   - Vorname/Nachname (from profile), Geburtsdatum, Straße/PLZ/Ort/Land, Gewünschter Einsatzort (Ismaning bei München), Gehaltsvorstellung, Deutschkenntnisse (A2 truthfully), Kündigungsfrist, Reisebereitschaft, Schwerbehinderung, Kanal, Datenschutz consents
3. Re-run dry-run: `& .venv\Scripts\python.exe -m universal_auto_applier live-dry-run --application-id fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e --start-url https://jobs.msg.group/de/jobs/411/form --headless --ephemeral-profile`
4. Then dashboard **Observe** to create `SubmissionSnapshot` → `wq8-review-packet --application-id fd9a41...` to freeze `review_plan_hash`
5. Only then may owner run `wq8-authorize --review-plan-hash <hash> --confirm` + `live-submit --approval-id <id> --confirm` (requires `UAA_ENABLE_REAL_SUBMISSION=true`)

## Rules Honored
- No invented candidate data; German A2 truthfully recorded (language 1/5)
- No auto-submit (generic untrusted, `UAA_ENABLE_REAL_SUBMISSION=false`)
- No JobHunter production code change (only data: JD cache + evaluation record + queue — all real/owner-authorized)
- No PII committed (this file is sanitized)
- Duplicate/submission_history checks pending at authorize time (absolute limit 1)

## Evidence Pointers
- JobHunter: `data/evaluations.json` (1 record, 3.6 consider), `data/application_queue.jsonl` (fd9a41...), `output/mohamed-azzam_msg-for-banking-ag_werkstudent-data-ai-banking-*` (pdf+md), `reports/001-msg-for-banking-ag-2026-08-21.md`
- UAA: `.uaa_data/uaa.sqlite` (1 job ready_to_apply, 17 interventions pending, 0 authorizations), `.uaa_data/live-runs/fd9a41480fc6-20260821T001956392794Z/report.json` (25 fields), `final-page.html`, `trace.zip`, pngs
- Branch: `checkpoint/wq-8-controlled-real-submission` at `d97080d` (inspection) + uncommitted Phase A artifacts above
