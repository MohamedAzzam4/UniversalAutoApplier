# Active Workpackage — V2-01 Immediate Correctness Blockers (IN PROGRESS)

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 — close the immediate correctness blockers in the V2 plan. The first authorized implementation milestone is F1 / T01–T02 only: skill evidence must abstain on negation, contradiction, or absence; visa possession must not be inferred from sponsorship need.
- **Status:** **F1 CHECKPOINTED; CONSENT FIX APPROVED AS A SEPARATE MILESTONE.** The V2-01 workpackage remains in progress; F2–F6 are not implemented. The consent-test fixture lifecycle repair has supervisor approval and is included only as a separate test-only checkpoint.
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `0e21adb43b400dd90a91a3ba754269e1a061375e` (pushed V2-00 baseline checkpoint).
- **Last successful checkpoint SHA/time:** `e77940b4fb7ee2d8d22e8a09adc887734ac59edb` at `2026-09-24T16:56:55+02:00`; F1 / T01–T02 was the preceding committed and pushed checkpoint. The separately reviewed consent-test repair is the current checkpoint package.
- **Branch-head verification (required before handoff/review):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/checkpoint/v2-01-correctness
  ```

  The two resolved values must match before a checkpoint handoff. Do not embed the commit that contains this handoff as a “current HEAD” value.

## Completed work

- Created and pushed `checkpoint/v2-01-correctness` from the clean V2-00 checkpoint before implementation.
- Archived the previous active V2-00 handoff verbatim at `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-00.md`; the WQ-8 handoff and its existing real-submission gates remain archived and unchanged.
- Implemented the bounded F1 field-mapping correction: a same-skill negated statement in profile/CV evidence suppresses automatic Yes even when another statement is positive; evidence matching respects token boundaries; an absent/negated skill question cannot fall through to the broad years-of-experience mapping.
- Removed the generic `visa` → `requires_sponsorship` label rule. The explicit sponsorship question continues to use the existing candidate-profile value; possession of a valid visa remains unmapped without direct evidence.
- Added T01 coverage for positive, absent, negated, contradictory, and German negated CV evidence; added T02 coverage for visa possession. Existing sponsorship-No behavior remains covered.

## Checkpointed F1 files

- `src/universal_auto_applier/form_engine/field_mapper.py`
- `tests/unit/test_field_mapper.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-00.md`

F1 was pushed as `e77940b4fb7ee2d8d22e8a09adc887734ac59edb`. `tests/playwright/test_consent_banner.py` is the only code file in the separately reviewed consent fixture-lifecycle checkpoint. No JobHunter or Siemens repository was changed.

## Validation results

- `.venv/Scripts/python.exe -m pytest tests/unit/test_field_mapper.py tests/unit/test_live_browser_policy.py -q` → **33 passed in 0.28s**.
- `.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/integration tests/pipeline -m "not live" -q` → **1,474 passed in 742.51s (12:22)**.
- `.venv/Scripts/python.exe -m ruff check src tests migrations` → passed, all checks passed.
- `.venv/Scripts/python.exe -m ruff format --check src tests migrations` → passed, 238 files already formatted.
- `.venv/Scripts/pyright.exe` → 0 errors, 0 warnings, 0 informations.
- `git diff --check` → passed before staging.
- `.venv/Scripts/python.exe -m pytest tests/playwright/test_dashboard.py::test_dashboard_loads_at_desktop_viewport tests/playwright/test_consent_banner.py -m "not live" -q` → **8 passed in 15.37s**.
- `.venv/Scripts/python.exe -m pytest tests/playwright/test_consent_banner.py -q` → **7 passed in 12.36s**.
- `.venv/Scripts/python.exe -m ruff check tests/playwright/test_consent_banner.py` → passed; `ruff format --check` on that file → 1 file already formatted.
- The focused combined non-live Playwright gate has not been rerun as a whole.
- No live tests, real ATS actions, or real submissions were run.

## Decisions

- F1 is intentionally limited to `field_mapper.py` and its focused unit tests. It does not implement the remaining V2-01 acceptance cases for read-back, import ownership, duplicate gates, upload evidence, or readiness.
- Any explicit per-job answer continues through its existing explicit-answer path; automatic evidence inference abstains when a skill claim is negative, contradictory, or unsupported.
- The negation detector is a conservative safety guard for English and German patterns exercised by tests; it is not represented as a general language-understanding system.
- The consent-test lifecycle change is separate from F1. It replaces each nested sync Playwright manager with pytest-playwright's per-test `page` fixture and preserves the existing assertions.
- The V2-00 baseline checkpoint is committed and supervisor-reviewed. Its original full combined non-live run had an incomplete failure; the focused lifecycle reproduction now passes, and the completed V2-01 non-browser gate passes. A full combined Playwright-inclusive gate remains unverified.
- Existing WQ-8 owner approval remains required for any real target/live action. This code milestone performed none.

## Blockers / risks

- F2–F6 and the rest of V2-01 remain future work. The combined Playwright-inclusive non-live suite remains unverified.

## Exact next action

After pushing this approved consent-test checkpoint, verify `HEAD` equals `origin/checkpoint/v2-01-correctness`, then wait for the next bounded supervisor assignment. Do not begin F2 or another V2-01 implementation slice before it is assigned.

- **Last updated:** 2026-09-24T15:02:45Z.
