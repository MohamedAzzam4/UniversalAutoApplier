# Cookie/CMP Pilot Defect — 2026-09-02

## Finding

First real review-only pilot against `https://jobs.msg.group/de/jobs/411/form` (msg for banking ag, `fd9a41480fc6`):

- **Detail → form discovery succeeded**: `SubmissionExecutionService.observe_and_persist_snapshot` followed safe Apply/Continue from `https://jobs.msg.group/de/jobs/411/werkstudent-data-ai-banking-all-genders` (detail) to `https://jobs.msg.group/de/jobs/411/form` (ATS form) via `analyze_page` + `choose_safe_action` + `click_action` loop (max 20 steps, no dangerous_submit clicked).
- **Visible browser remained blocked** by full-page Usercentrics privacy/cookie-consent screen (`Privatsphäre-Einstellungen`, buttons `Einstellungen verwalten`, `Einstellungen speichern`, `Nur technisch notwendige Cookies akzeptieren`, `Alle akzeptieren`) — observed manually via screenshot, not via DOM automation.
- **Existing system failed to classify/resolve it**: `LivePageAnalysis` (`analyze_page`) and `SubmissionExecutionService` treated the underlying application DOM as ready because 25 fields were extractable (`visible_control_count 25`, `is_application_form true`). No `blocker` field was set for the CMP overlay, so the system proceeded to analyze `Geburtsdatum` as first intervention even though the visible application was **not interaction-ready**.
- **Therefore interaction-readiness was not proven**: The snapshot with 25 fields and `Geburtsdatum` intervention was persisted, but the form was behind a blocking overlay. The pilot correctly stopped at `needs_human` (`unknown_required_field` for `Geburtsdatum`) due to policy, and **no submission occurred** — review-only boundaries held.

This is a real-world Supervisor/UAA navigation defect, not a safety violation.

## Root Cause

Page-readiness contract conflated `FORM_DISCOVERED` (underlying DOM has form controls) with `FORM_INTERACTION_READY` (no blocking overlay intercepts the UI). The CMP handler was not part of the deterministic browser/navigation layer, so a blocking cookie banner was invisible to `analyze_page`.

## Interaction-Ready Contract (Fixed)

- `FORM_DISCOVERED` = underlying DOM contains application form controls.
- `FORM_INTERACTION_READY` = `FORM_DISCOVERED` **and** no blocking cookie/CMP overlay detected. Only then may analysis/fill proceed.

## Fix Summary

- **New deterministic component**: `src/universal_auto_applier/browser/consent_banner.py` — `ConsentBannerHandler` (`handle_consent_banner`) with `ConsentPolicy` `necessary_only` (default) / `accept_all` / `human`. Not an LLM tool.
- **Default policy** `necessary_only` prefers `Nur technisch notwendige Cookies akzeptieren` / `Reject all` / `necessary only` variants, never `Alle akzeptieren` / `Accept all` under that policy.
- **Application consent disambiguation**: Handler requires CMP context (known selectors `usercentrics/root`, `onetrust`, `cookiebot` or visible `role=dialog`/`overlay`/`modal`/`banner` containing cookie semantics `cookie`/`privatsphäre`/`privacy settings`). Form fields `Einwilligung in die Speicherung...` without CMP context are never auto-clicked.
- **Usercentrics support**: `Privatsphäre-Einstellungen` detected via body text fallback, `Nur technisch notwendige Cookies akzeptieren` clicked exactly once under `necessary_only`, `Alle akzeptieren` not clicked, overlay disappearance verified via `_is_overlay_still_visible`.
- **Generic safe CMP**: Supports `OneTrust`/`Cookiebot`/generic `Reject all` / `Necessary only` patterns; unknown CMP with no suitable button fails closed (`blocked`, no click, no retry loop).
- **Integration**: `browser/live_runner.py` (both `run_in_context` and `run_in_context_synthetic`) calls `_handle_cmp_preflight` at top of each navigation step (A: after detail, B: after form, C: before fill), `submission/execution_service.py` calls preflight after initial `page.goto` and after form navigation before `execute_live_form`. Already-cleared banners are idempotent (`absent`).
- **Failure semantics**: `COOKIE_CONSENT_BLOCKED` (new `ReasonCode`) when CMP remains after bounded attempt, `COOKIE_CONSENT_RESOLVED` audit when cleared; `SupervisorService` maps `cookie_consent_blocked` error to `COOKIE_CONSENT_BLOCKED` handoff, no infinite retry.
- **Audit**: Sanitized `cmp=Usercentrics policy=necessary_only action=necessary_only result=resolved clicked=true` (no cookie values), logged via `logger.info`.
- **No submission**: Handler never clicks `dangerous_submit` (`Bewerbung absenden`), never authorizes.

## Tests

- `tests/fixtures/consent/usercentrics.html` (full-page Usercentrics with JS hide on click, underlying 25-field form), `generic.html` (OneTrust), `unknown_cmp.html` (no necessary button), `application_consent.html` (form-only Einwilligung).
- `tests/playwright/test_consent_banner.py` (7 tests, all passed):
  - `test_usercentrics_necessary_only` — Usercentrics resolved, `Alle akzeptieren` not clicked, banner gone, form interaction-ready.
  - `test_generic_necessary_only` — OneTrust `Reject all` resolved.
  - `test_unknown_cmp_blocked` — no suitable button → `blocked`, overlay remains, fail closed.
  - `test_application_consent_not_clicked` — form Einwilligung not auto-clicked, `absent`.
  - `test_pilot_defect_regression_old_vs_new` — old `analyze_page` sees form under overlay (`is_application_form true`, `visible_control_count >=20`), new handler detects CMP → `resolved` → form still ready after, `absent` second call.
  - `test_unresolved_consent_blocks_form` — blocked CMP stops preparation.
  - `test_handler_never_clicks_dangerous_submit` — `Reject all` clicked, `Bewerbung absenden` untouched.
- Supervisor: `test_u_cookie_consent_blocked_handoff` (PrepareOutcome `cookie_consent_blocked` → `COOKIE_CONSENT_BLOCKED` handoff).
- `tests/unit/test_supervisor_v0.py` now 28 tests (was 27), all passed.
- Full non-live gate: `pytest tests/unit tests/contract tests/integration -m "not playwright and not live"` → **1430 passed, 4 deselected**.
- Playwright relevant: `test_consent_banner` 7, `test_wq8_interlock` 7, `test_wq8_phase_a_interlock` 5, `test_controlled_submission` 5, `test_wq7_production_safety` 23 — all passed individually (batch greenlet issue pre-existing, not this change).

## Safety

- No `dangerous_submit` clicked by handler (heuristic + classification check).
- No application `Einwilligung` auto-clicked (CMP context required).
- No source-code modification during pilot before this workpackage.
- Review-only boundaries held in pilot (no submit, no authorization, no WQ-8 DB access).

## Changed Files (this workpackage)

- `src/universal_auto_applier/browser/consent_banner.py` (new)
- `src/universal_auto_applier/browser/live_runner.py` (integration, `cookie_consent_policy` field, preflight calls)
- `src/universal_auto_applier/submission/execution_service.py` (preflight on detail and form)
- `src/universal_auto_applier/config.py` (`UAA_COOKIE_CONSENT_POLICY`, validation)
- `src/universal_auto_applier/supervisor/models.py` (`COOKIE_CONSENT_BLOCKED/RESOLVED`)
- `src/universal_auto_applier/supervisor/service.py` (cookie reason mapping)
- `tests/fixtures/consent/*.html` (4 fixtures)
- `tests/playwright/test_consent_banner.py` (new, 7 tests)
- `tests/unit/test_supervisor_v0.py` (+1 cookie test)
- `docs/agent_supervisor/AGENT_SUPERVISOR_V0.md` (add cookie section)
- This evidence file.

## Next

Do not rerun real pilot yet — awaiting reviewer. Handler ready for real review-only repilot.
