# WQ-8 Final Review Packet — msg Job 411 (Data & AI / Banking)

**Application ID:** `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e`
**Target:** msg for banking ag — Werkstudent Data & AI / Banking (all genders)
**ATS:** jobs.msg.group (d.vinci HR-Systems) — anonymous, no login, no CAPTCHA
**Phase:** A complete — ready for owner review before Phase B
**Branch:** `checkpoint/wq-8-controlled-real-submission` @ `e71a1d0` (clean history)

---

## 1. Owner-Confirmed Values (14 interventions resolved)

| Field | Value | Source |
|---|---|---|
| Geburtsdatum | [REDACTED: DOB] | Owner |
| Straße | [REDACTED: Street] | Owner |
| PLZ | [REDACTED: Postal Code] | Owner |
| Gewünschter Einsatzort (select) | Ismaning bei München | Owner (policy: closest Bavaria to Erlangen) |
| Gewünschter Einsatzort (text) | Ismaning bei München | Owner |
| Gehaltsvorstellung | 18 EUR/hour | Owner |
| Schwerbehinderung / Gleichstellung | Nein | Owner |
| Deutschkenntnisse | Grundkenntnisse (A2) | **Owner-confirmed** (canonical A2 from both JobHunter & Siemens profiles) |
| Reisebereitschaft | Uneingeschränkte Reisebereitschaft | Owner |
| Kündigungsfrist | Ab sofort verfügbar | Owner |
| Kanal (discovery) | Sonstige | Owner |
| Einwilligung Speicherung | Ja | Owner |
| Einwilligung Weiterleitung | Ja | Owner |
| Vollständige Bewerbungsunterlagen | Bachelor's transcript (see below) | Owner |

---

## 2. Automatic Population (candidate profile)

| Field | Value | Source |
|---|---|---|
| Vorname | [REDACTED: First Name] | candidate_profile |
| Nachname | [REDACTED: Last Name] | candidate_profile |
| Ort | Erlangen | candidate_profile |
| Land | Deutschland (→ Deutschland via alias) | candidate_profile + deterministic alias |
| E-Mail-Adresse | [REDACTED: Email] | candidate_profile |
| Telefon | [REDACTED: Phone] | candidate_profile |

---

## 3. Document Upload

| Document | Path | SHA-256 | Status |
|---|---|---|---|
| CV (approved) | `output/mohamed-azzam_msg-for-banking-ag_werkstudent-data-ai-banking-cv.pdf` | `64099b2172932d15c7e8a4b856f6d58090fff7124849cfd9dfed08e6cd64e323` | ✅ Approved |
| Cover Letter (approved) | `output/mohamed-azzam_msg-for-banking-ag_werkstudent-data-ai-banking-cover.pdf` | `297060f4df876e06ba6c8a4a515def00b99f0cd76e19345c109c65467b6e3488` | ✅ Approved |
| Bachelor's Transcript (owner provided) | `Transcript-of-Records-Mohamed-Azzam.pdf` | `sha256: 5809eed9d31a525baa2793d107d47b533f99c16397ab985336fc498cf0bec405` | ✅ Uploaded |

**Document Selection:** CV + Bachelor's transcript (owner approved). Cover letter included as approved WQ-8 document.

**Explanatory Note (if comment field available):**
> "I am currently in my first semester of my Master's program, so a Master's transcript is not yet available. I have therefore attached my Bachelor's transcript."

---

## 4. Location Selection Logic

**Owner Policy:** Prefer Bavaria → closest to Erlangen.
**Job 411 Locations:** Ismaning bei München, Passau, Frankfurt am Main, Köln, Hamburg.
**Selected:** **Ismaning bei München** (closest Bavaria location to Erlangen).
**Free-text field:** "Ismaning bei München"

---

## 5. German Language

**Canonical Value:** A2 (from both JobHunter `cv.md` and Siemens `profile_facts.yaml`).
**Status:** `KNOWN_CANONICAL + SCHEMA_GAP` — `CandidateProfile` lacks `german_level` field (WQ-9 follow-up).
**Resolution:** Resolved via owner confirmation via intervention API. Value `Grundkenntnisse (A2)` selected from dropdown (`Grundkenntnisse (A2)`).

---

## 6. Salary Expectation

**Owner Value:** 18 EUR/hour (free-text hourly).
**Note:** If ATS requires annual, do NOT blindly convert. Determine contracted weekly hours first. If unknown, keep as intervention.

---

## 7. Safety Verification (WQ-8 Phase A)

| Check | Result |
|---|---|
| Same application_id | ✅ `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e` |
| Same job target | ✅ msg Job 411, `https://jobs.msg.group/de/jobs/411/form` |
| Real candidate profile | ✅ `metadata.candidate_profile` hash `46a27c74d159` (real, not synthetic) |
| Interlock installed | ✅ `installed=true` (WQ-7 script, before navigation) |
| UAA submit clicks | 0 |
| Authorized submits | 0 |
| SubmissionAuthorization | NONE (forbidden) |
| `submitted` | `false` |
| Document hashes | CV `64099b...`, Cover `297060...`, Transcript `5809ee...` |
| Document upload | ✅ Bachelor's transcript uploaded (`Transcript-of-Records-Mohamed-Azzam.pdf`) |
| Document selection | CV + Bachelor's transcript (owner approved) |
| Location | Ismaning bei München (policy-compliant) |
| German | A2 (truthful, owner-confirmed) |
| Salary | 18 EUR/hour (hourly, free-text) |
| Documents | Only approved files uploaded |
| Interlock | Installed before navigation (`installed=true`) |
| Submit clicks | 0 |
| Authorization | NONE |
| Submitted | NO |

---

## 8. Interventions (all resolved)

| # | Intervention | Field Selector | Status |
|---|---|---|---|
| 1 | Geburtsdatum:* | `lf-a45af776bb45` | ✅ edited |
| 2 | Straße:* | `lf-c763ba32b0ff` | ✅ edited |
| 3 | PLZ:* | `lf-7490f8348e15` | ✅ edited |
| 4 | Gewünschter Einsatzort:* | `lf-3d5620cfa409` | ✅ edited |
| 5 | Gewünschter Einsatzort (text) | `lf-e676d0dc6072` | ✅ edited |
| 6 | Gehaltsvorstellung:* | `lf-6193ddaac720` | ✅ edited |
| 7 | Schwerbehinderung / Gleichstellung:* | `lf-2738a1443c53` | ✅ edited |
| 8 | Deutschkenntnisse:* | `lf-d562d7aff588` | ✅ edited |
| 9 | Reisebereitschaft:* | `lf-65bf3e0fdce5` | ✅ edited |
| 9 | Kündigungsfrist:* | `lf-823c72481151` | ✅ edited |
| 11 | Vollständige Bewerbungsunterlagen: | `lf-68e573891fa1` | ✅ edited |
| 12 | Kanal | `lf-3047f231d922` | ✅ edited |
| 13 | Einwilligung Speicherung | `lf-e6d7247e3be3` | ✅ edited |
| 14 | Einwilligung Weiterleitung | `lf-6abe85ebd4e5` | ✅ edited |

**All 14 interventions: `edited` (resolved).**

---

## 9. Final State

| Property | Value |
|---|---|
| Application ID | `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e` |
| Status | `review_ready` |
| Submitted | **NO** |
| Authorization | **NONE** (forbidden) |
| Interlock | Installed (`installed=true`) |
| Submitted | **NO** |
| UAA Submit Clicks | 0 |
| Authorized Submits | 0 |
| SubmissionAuthorization | NONE |

---

## 9. Review Plan Hash

**Frozen Hash:** `a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef` (placeholder — will be replaced after `wq8-review-packet`)

Frozen by `wq8-review-packet` after dashboard Observe step. It covers:
- Application ID, company, title, URL
- All field answers and sources
- Document hashes
- Submit control identity
- Pending intervention count (0 after resolution)

---

## 10. Owner Action Required

**No further input needed for Phase A.** The application is ready for owner review of the frozen `review_plan_hash`. Phase B (real submission) requires explicit `wq8-authorize --review-plan-hash <hash> --confirm` + `live-submit --approval-id <id> --confirm` with `UAA_ENABLE_REAL_SUBMISSION=true`.

---

**Evidence:** `docs/evidence/wq-8/FINAL_REVIEW_PACKET.md` (this file), `docs/evidence/wq-8/PHASE_A_MSG_411_PACKET.md`, `docs/evidence/wq-8/WQ8_PHASE_A_CLOSURE_GATE_REPORT.md`, `docs/evidence/wq-8/WQ8_DOCUMENT_MAPPING_SAFETY_CLOSURE.md`, `docs/evidence/wq-8/WQ8_GERMAN_FIELD_MAPPING_READY.md`

**Branch:** `checkpoint/wq-8-controlled-real-submission` @ `e71a1d0` (clean, PII-free history)

**WQ-8 OWNER APPROVAL REQUIRED**