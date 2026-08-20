# WQ-8 Target Inspection — all shortlisted roles (2026-08-20)

Dry-run / navigation-only inspection. **No evaluation, tailoring, export,
form fill, upload, or submission was performed.** This is the deliverable of
the owner-selected "complete remaining inspection first" path.

## Ranking

| Rank | Role / company | ATS | Login | CAPTCHA | UAA-proven adapter | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Siemens 517336 — AI/Automation Pipeline (Erlangen) | jobs.siemens.com | Register first | None on detail | **YES (trusted)** | 5-step apply wizard; step 1 = Login/Registration. Highest role fit; only role on a proven adapter. Needs account. |
| 2 | Agile Robots — Sim2Real (Munich) | Personio | No | No | No (generic) | Fully anonymous short form; bio/certificates upload; Munich-area enrollment filter. Personio not in registry. |
| 3 | msg for banking — Data & AI / Banking (Munich/Ismaning) | jobs.msg.group (d.vinci) | No | No | No (generic) | Anonymous full form incl. Geburtsdatum/salary; CV upload required; no email apply. ATS not in registry. |
| 4 | Maisel — Data Engineering (Bayreuth) | karriere.maisel.com (BITE) | No | No | No (generic) | Anonymous; Datenschutz checkbox gates form; 5-min application; not in registry. |
| 5 | MEAG — Data Enablement (Munich) | karriere.meag.com (rexx) | No | No | No (generic) | Anonymous short form but FAQ says portal registration; street + DOB high-risk fields; not in registry. |
| 6 | AIBE@FAU — FAUstairs SHK (Erlangen) | fau-jobs.de (BITE) | No | No | No (generic) | Anonymous 4-step; needs LaTeX + German C1 + enrollment; deadline 2026-08-30. Weaker fit (SHK not Werkstudent). |
| 7 | qwitto — Student Assistant (Munich) | Personio | No | No | No (generic) | Anonymous; stack is Java/TS/Angular, not Python-data; not in registry. |
| 8 | WeSort.AI — Data & AI (Würzburg) | JOIN | No | **Yes** | No | JOIN apply reCAPTCHA; LinkedIn guest apply gated. |
| 9 | MDT — IoT & Cloud (Neumarkt) | JOIN | No | **Yes** | No | JOIN reCAPTCHA-protected apply; directApply=true but reCAPTCHA site-wide. |
| 10 | Thieme — BI/Data Analytics (Erlangen) | via Indeed | **Yes** | n/a | No | Indeed employer relies on Indeed apply; guest requires account. |
| 11 | PwC — AI Adoption & Enablement (Nuremberg) | jobs.pwc.de (`PWFPGKGB2669`) | **Yes** | No | No | Email-verification gate to Bewerber-tool (`step=1&stepname=username`); 21 locations, Berlin slug; part-time; SAP-success factors-style canvas. |
| 12 | forensica datalytics — Business Dev & AI (Munich) | none | n/a | n/a | n/a | No ATS/jobs page; email-only apply (`bewerbung@fd.team`); not automatable. |

## Tiers

- **Tier 1 — proven adapter, requires account:** Siemens 517336
  (`is_trusted=True`). Only candidate on a WQ-7C-proven adapter.
- **Tier 2 — anonymous, no login, no CAPTCHA (generic/untrusted):** Agile
  Robots (Personio), msg 411 (d.vinci), Maisel (BITE), MEAG (rexx), FAU
  (BITE), qwitto (Personio). All are UNPROVEN registries → adapter
  `is_trusted=False` → adapter-driven submit will always return
  `review_ready`; a manual controlled submission remains possible per AGENTS.md
  submission contract when all gates pass.
- **Tier 3 — blocked / not automatable:** WeSort.AI + MDT (JOIN reCAPTCHA),
  Thieme (Indeed), PwC (email gate), forensica (no ATS).

## Notes per candidate

### Siemens 517336 (Erlangen) — RECOMMENDED for Phase A target
- URL `https://jobs.siemens.com/en_US/externaljobs/ApplicationMethods?folderId=517336`;
  posted 2026-08-17. Five-step apply: Login/Registration → Experience →
  Contact → Job-specific → Review.
- Only `is_trusted=True` adapter path for adapter-driven submit. Downside:
  requires candidate account registration on jobs.siemens.com (UAA cannot
  auto-create accounts). This makes the submit path owner-assisted through the
  registration step, or pushes us to Tier 2.

### Agile Robots — Sim2Real (Munich)
- `https://agile-robots-se.jobs.personio.de/job/2727397/apply?language=en`.
  Straight anonymous Personio form: name/email/tel, CV + certificates
  upload, "tell us about yourself" field. No login, no CAPTCHA. Hard filter:
  **must be enrolled at a Munich-area university**. Personio = generic
  adapter (not in registry) → `is_trusted=False`.

### msg for banking — Werkstudent Data & AI / Banking (Munich/Ismaning)
- `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders`
  (ref 2025-0143; locations Frankfurt, Hamburg, **Ismaning bei München**,
  Köln, Passau — Ismaning is the msg HQ near Munich; job detail also lists
  Nürnberg). Employment: Werkstudent; metadata "Vollzeit" per feed.
- Apply via d.vinci portal `/de/jobs/411/apply` → intro ("Auswahl der
  Bewerbungsart": manual form / XING / LinkedIn / CV import via Textkernel)
  → `/de/jobs/411/form`: anonymous, no login, **no reCAPTCHA**. Requires:
  Anrede, Vorname*, Nachname*, Geburtsdatum*, Land*, Straße*, PLZ*, Ort*,
  E-Mail*, Telefon*, Gewünschter Einsatzort*, Gehaltsvorstellung*,
  Frage Schwerbehinderung, Deutschkenntnisse*, Reisebereitschaft*,
  Kündigungsfrist*, msg-Kontakt, Staatsangehörigkeit (optional),
  Kommentar, Documents* (Anschreiben/Lebenslauf/Zeugnisse), Channel
  erfahren*, consent radios (Datenschutz Ja/Nein), Absenden. Note field:
  "Bitte lege eine aktuelle Leistungsübersicht aus deinem Studium deinen
  Bewerbungsunterlagen bei. Bewerbungen, die nicht über unser
  Bewerbermanagementsystem eingehen, können wir nicht berücksichtigen."
  Cookie notice: browser must accept cookies (standard). NOT in proven
  registry; `is_trusted=False`.

### Brauerei Gebr. Maisel — Werkstudent Data Engineering/Dateninfrastruktur (Bayreuth)
- Posting `https://karriere.maisel.com/jobposting/4489256de2a6fb40c6292a4063ca6cd415f970460`;
  apply `…/apply` (anonymous; BITE portal). Eligible (§8): enrolled students,
  CS/data/infrastructure. "Bewerbungsprozess max. 5 Minuten." Fields unlocked
  only after Datenschutzhinweise consent checkbox (Pflichtfeld). Asks
  Nationalität/Aufenthaltstitel; auto-fill from CV upload; document uploads.
  No login/CAPTCHA. NOT in proven registry; `is_trusted=False`.

### MEAG — Werkstudent Data Enablement (Munich)
- `https://karriere.meag.com/werkstudent-data-enablement-mwd-de-f2146.html?agid=23`.
  rexx ATS; anonymous short form loads (name/email/tel/address/DOB, message,
  CV). No CAPTCHA seen at load. But a rexx "How it works" FAQ states
  applicants must create an account ("Bewerbung über Bewerberportal" /
  portal registration) — contradiction to be re-verified live. Street + DOB =
  high-risk fields → intervention path. NOT in proven registry.

### AIBE@FAU — Studentische Hilfskraft FAUstairs (Erlangen)
- `https://www.jobs.fau.de/jobs/…(1837)/` and apply
  `https://www.fau-jobs.de/de/jobposting/fb8d413cd7a073de5eb2e00b9bb5e00f32323b280/apply`
  — 4-step anonymous portal (Schritt 1 von 4), BITE GmbH data-processor
  notice, Datenschutzinformationen checkbox gates the form. Requires
  enrollment at FAU or another German university, German C1, LaTeX + web-dev
  basics; tasks are admin/LaTeX teaching-material oriented. Apply materials:
  Motivationsschreiben + tabular CV. Deadline 2026-08-30; start 2026-10-15.
  No login/CAPTCHA. Weaker role fit (SHK academic support, not Python-data
  Werkstudent). NOT in proven registry.

### qwitto — Werkstudent/Studentische Aushilfe (Munich)
- `https://qwitto-gmbh.jobs.personio.de/`; anonymous Personio, no login/
  CAPTCHA. Role is Java/TypeScript/Angular (finance SaaS); not Python-data.
  Email apply alternative `hr@qwitto.com`. NOT in proven registry.

### WeSort.AI — Werkstudent Data & AI (Würzburg)
- LinkedIn `4428958207` guest apply requires sign-in; JOIN
  `https://join.com/companies/wesort/16516581…` — **JOIN applies are
  reCAPTCHA-protected** ("Diese Seite ist durch reCAPTCHA geschützt").
  Blocked for automated controlled submit.

### Modern Drive Technology — Werkstudent Softwareentwicklung IoT & Cloud (Neumarkt)
- Official JOIN posting
  `https://join.com/companies/moderndrivetechnology/16444713-…`
  (page also lists Job ID 16553143; `directApply: true`; hybrid Neumarkt;
  Angular/TS/JS focused; Anschreiben optional; contact Johannes Stephan).
  JOIN apply = reCAPTCHA. Blocked.

### Thieme — student worker BI/Data Analytics (Erlangen)
- Discovered via Indeed; Thieme employer careers route redirects to Indeed
  apply (guest account required) → login-gated → blocked.

### PwC — Werkstudent AI Adoption & Enablement (w/m/d) (Nuremberg)
- LinkedIn `4454987006` (Nuremberg, posted ~2026-08-19). Official page is
  `https://jobs.pwc.de/de/de/job/2669/Werkstudent-AI-Adoption-Enablement-w-m-d`
  (Teilzeit, Business Services, 21 Standorte offer the job — Berlin slug,
  Nuremberg among the 21). Apply URL redirects to
  `?step=1&stepname=username` → **email verification / account creation**
  before the Bewerbungstool → blocked for automated anonymous submission.
  NOT in proven registry.

### forensica datalytics — Werkstudent Business Development & AI (Munich)
- No ATS and no jobs page on `https://forensica-datalytics.com/` (nav: Home,
  Kontakt, Impressum, Datenschutz). Company careers info points to e-mail
  application only (`bewerbung@fd.team`). Not automatable via UAA controlled
  submission → **discarded**.

## Conclusion

- All 12 shortlist roles are now inspected. No single role simultaneously
  satisfies "proven adapter + no login + no CAPTCHA".
- **Recommendation:** Siemens 517336 remains the strongest single candidate
  (only UAA-proven trusted adapter, freshest posting, highest role fit), at
  the cost of a one-time account registration step.
- **Anonymous no-login no-CAPTCHA alternatives** (all generic/untrusted —
  submit requires the manual approved controlled route): Agile Robots
  Sim2Real, msg 411, Maisel, MEAG.
- PwC/Thieme/WeSort/MDT/forensica are blocked or not automatable.