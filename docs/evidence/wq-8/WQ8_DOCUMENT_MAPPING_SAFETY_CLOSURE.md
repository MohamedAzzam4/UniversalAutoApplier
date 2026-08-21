# WQ-8 Document Mapping Safety Closure

**Application:** `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e` — msg for banking ag — Job 411
**Form:** `https://jobs.msg.group/de/jobs/411/form` — d.vinci, anonymous, multiple file input
**No live upload performed in this closure. No owner PII re-entry.**

## 1. Exact Mapper Change

**File:** `src/universal_auto_applier/form_engine/field_mapper.py`

**Before (too broad):**
```python
_FILE_FIELD_PATTERNS = [
    (r"resume|cv|lebenslauf", "cv_pdf", ...),
    (r"cover.*letter|anschreiben", "cover_letter_pdf", ...),
    (r"bewerbungsunterlagen|unterlagen|anlage|dokumente", "cv_pdf", ...),
]
# matched against (label, nearby, name)
```

**After (narrow, high-precision):**
```python
# File field patterns — narrow, high-precision only.
# Generic document labels (Unterlagen, Dokumente, Anlagen,
# Vollständige Bewerbungsunterlagen) are NOT globally mapped to cv_pdf;
# they are ambiguous and must become interventions / owner document
# selection unless target-specific evidence proves an exact mapping.
_FILE_FIELD_PATTERNS = [
    (r"resume|cv|lebenslauf", "cv_pdf", "File field matched 'resume/cv/lebenslauf'"),
    (r"cover.*letter|anschreiben", "cover_letter_pdf", "File field matched 'cover letter/anschreiben'"),
]
# _try_match_file_field now checks ONLY (label, name) — nearby/help text
# intentionally ignored. Generic help like "Anschreiben, Lebenslauf,
# Zeugnisse" near "Vollständige Bewerbungsunterlagen" does NOT auto-map.
```

**Diff verified:** `git diff src/universal_auto_applier/form_engine/field_mapper.py` shows removal of generic pattern and docstring tightening, plus `_try_match_file_field` loop reduced from `(label, nearby, name)` to `(label, name)`.

**Preserved strong deterministic mappings:**
* Resume / CV / Lebenslauf → `cv_pdf`
* Cover Letter / Anschreiben → `cover_letter_pdf`

**Removed/narrowed:**
* `unterlagen`, `dokumente`, `anlagen`, `bewerbungsunterlagen` — no longer globally auto-map to `cv_pdf`

## 2. msg Job 411 Document-Field Semantics (investigation, no real upload)

**Sources examined (existing evidence, no new live upload):**
* `.uaa_data/live-runs/fd9a41480fc6-20260821T151127514538Z/final-page.html` (saved DOM from previous `live-dry-run --start-url /form` with `wq8_phase_a` off, deterministic_only)
* Playwright-evaluated `_FIELD_METADATA_JS` for `input#attachmentFile`

**Findings:**

* **Selector:** `input#attachmentFile`, `name=attachmentFile`, `type=file`, `multiple=""`, `data-action="/applicationForm/uploadAttachment"`, `aria-describedby="attachmentFile-error"` — **no `required` attribute** in DOM (despite visual `*` in legend), `is_enabled true`, `is_visible true`
* **Label:** `Vollständige Bewerbungsunterlagen:` (exact `label[for=attachmentFile]` text)
* **Nearby/help text:** Page contains
  * "Bitte trage deine Daten möglichst vollständig ein. Damit wir deine Bewerbung bestmöglich prüfen können, freuen wir uns über folgende Unterlagen von dir: **Anschreiben, aktueller Lebenslauf und relevante Zeugnisse** (Arbeitszeugnisse, Tätigkeitsnachweise, Qualifikationsnachweise, Urkunden, usw.)."
  * Help legend for required fields lists `Vollständige Bewerbungsunterlagen (Lebenslauf im Format: Word, PDF, jpg, gif, png)*` — contradictory: parenthesis says Lebenslauf but outer list says complete package.
  * Privacy policy block listing  `Vollständige Bewerbungsunterlagen (Lebenslauf im Format: Word, PDF, jpg, gif, png)*` alongside `Anschreiben` separately — again ambiguous whether one combined upload or separate.
* **Accepted extensions (from help):** `Word, PDF, jpg, gif, png` (for the Lebenslauf mention); input itself has no `accept=` restriction in DOM, but `multiple` suggests **one field for a combined package** (not separate CV vs transcript fields).
* **File count:** One visible `type=file` for attachments (`attachmentFile`), plus a second `type=file` `consentOfLegalGuardiansAttachmentFile` which is `disabled=""` (irrelevant, not visible/enabled). No separate `Lebenslauf`-only field, no separate `Zeugnisse` field.
* **Single vs multiple:** `multiple` attribute present → **accepts multiple files** or a single combined PDF — but the UI does not distinguish CV vs transcript; a single `attachmentFile` is expected to hold the **complete application package**.
* **Job explicit request:** JD text (4392 chars, `fetch_jd`) says nothing about transcript alone; help text above is the only explicit request and it lists **all three** (Anschreiben + Lebenslauf + Zeugnisse) as desired complete Unterlagen.

**Conclusion:** The field is **ambiguous generic-document** — it is described as "complete application documents" (including transcript/certificates), accepts multiple files, and its label does not contain `Lebenslauf`/`CV` alone. It cannot be proven to mean CV specifically. Uploading the approved real CV (`64099b2172932d15...`) automatically into this generic field would be imprecise and could misrepresent the application as complete when transcript may be expected separately, or could upload CV into a field the reviewer expects to hold a combined PDF.

## 3. Automatic vs Intervention Decision

* **Automatic (high-precision):** `Lebenslauf`, `CV`, `Resume` → `cv_pdf`; `Anschreiben`, `Cover Letter` → `cover_letter_pdf` — no owner decision needed.
* **Intervention (owner document selection):** `Vollständige Bewerbungsunterlagen` (msg 411), `Unterlagen`, `Dokumente`, `Anlagen`, `Weitere Unterlagen`, `Zeugnisse / Unterlagen` → **NOT auto-mapped** → `intervention_needed` (`Required field has no deterministic mapping` or `file field` intervention) → persisted as `InterventionRow(kind=field_answer, field_selector=lf-..., question="Vollständige Bewerbungsunterlagen:")` → owner selects exactly which approved document(s) to attach via dashboard (`POST /api/interventions/{id}/resolve` with `save_to_memory` → `job.metadata.form_answers`).

For msg 411, the eventual Phase A preparation **will** create a **document intervention** for `attachmentFile` requiring the owner to decide: attach only the approved CV (`64099b...`), attach CV + approved cover (`297060...`), or attach a combined package — never automatically substituting transcript or combining documents. No automatic upload of CV into this generic field.

If the ATS file control cannot safely distinguish CV from transcript/other documents, the preparation **stops** and reports the ambiguity (already the case — `required_unresolved`).

**Interlock and persistence unchanged:** `src/universal_auto_applier/browser/live_runner.py` (`wq8_phase_a` installs WQ-7 interlock before navigation, real data allowed, no one-shot armed), `src/universal_auto_applier/config.py`/`cli.py` `--wq8-phase-a`, and `src/universal_auto_applier/interventions/store.py` durable same-DB path remain as proven in `9dcfa76`. No regression.

## 4. Tests

**New/updated regression suite `tests/unit/test_wq8_phase_a_file_mapping.py` (7 tests, all green):**

* `test_lebenslauf_maps_to_cv` — `Lebenslauf` → `cv_pdf` ✓
* `test_cv_label_maps_to_cv` — `CV` → `cv_pdf` ✓
* `test_anschreiben_maps_to_cover_letter` — `Anschreiben` → `cover_letter_pdf` ✓
* `test_dokumente_not_automatically_cv` — `Dokumente` → `None` (intervention) ✓
* `test_weitere_unterlagen_not_automatically_cv` — `Weitere Unterlagen` → `None` ✓
* `test_zeugnisse_unterlagen_not_automatically_cv` — `Zeugnisse / Unterlagen` → `None` ✓
* `test_vollstaendige_bewerbungsunterlagen_not_automatically_cv` — `Vollständige Bewerbungsunterlagen:` (with nearby `Anschreiben, Lebenslauf, Zeugnisse` ignored) → `None` ✓ — proves msg 411 field will be an intervention, not an automatic CV upload.

Plus existing `tests/unit/test_wq8_intervention_persistence.py` (3 tests) and `tests/playwright/test_wq8_phase_a_interlock.py` (5 tests) still green — no regression in interlock/persistence.

## 5. Gates
* `ruff check src tests` — **All checks passed**
* `ruff format --check src tests` — **209 files already formatted** (1 auto-formatted)
* `pyright` — **0 errors, 0 warnings**
* `pytest tests/unit/test_wq8_phase_a_file_mapping.py tests/unit/test_wq8_intervention_persistence.py -q` — **10 passed** (including 7 new narrow-mapping tests)
* `pytest tests/unit tests/contract -q` — **1079 passed**
* `pytest tests/playwright/test_wq8_phase_a_interlock.py` — **5 passed** (deterministic fixture, no network)
* No live real-data fill was performed in this closure (`--wq8-phase-a` not invoked against real ATS).

## 6. Final SHA

`9dcfa76` + this commit (document safety narrowing, see `git rev-parse HEAD` / `origin/checkpoint/wq-8-controlled-real-submission` after push).

---

**State:** `OWNER INPUT / PHASE A COMPLETION REQUIRED` — document mapping now safe; the 17 field interventions plus the new document intervention for `Vollständige Bewerbungsunterlagen` await owner selection of approved document(s). No re-entry of 17 PII fields was requested in this gate.
