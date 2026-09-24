# Active Workpackage — V2-01 Immediate Correctness Blockers (IN PROGRESS)

- **Repository:** `MohamedAzzam4/UniversalAutoApplier`.
- **WP ID / objective:** V2-01 — close the immediate correctness blockers in the V2 plan. The first authorized implementation milestone is F1 / T01–T02 only: skill evidence must abstain on negation, contradiction, or absence; visa possession must not be inferred from sponsorship need.
- **Status:** **F1 IMPLEMENTED; PENDING SUPERVISOR REVIEW BEFORE COMMIT.** The V2-01 workpackage remains in progress; F2–F6 are not implemented. The consent-test fixture lifecycle repair is a separate, supervisor-reviewed change and is intentionally excluded from the F1 checkpoint.
- **Branch:** `checkpoint/v2-01-correctness`.
- **Base SHA:** `0e21adb43b400dd90a91a3ba754269e1a061375e` (pushed V2-00 baseline checkpoint).
- **Last successful checkpoint SHA/time:** `0e21adb43b400dd90a91a3ba754269e1a061375e` at `2026-09-24T16:05:54+02:00`; this is the pushed V2-00 commit at V2-01 branch creation. The F1 milestone is not committed yet.
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

## Changed files in the F1 review package

- `src/universal_auto_applier/form_engine/field_mapper.py`
- `tests/unit/test_field_mapper.py`
- `docs/NEXT_WORKPACKAGES.md`
- `docs/handoffs/ACTIVE_WORKPACKAGE.md`
- `docs/handoffs/archive/ACTIVE_WORKPACKAGE_V2-00.md`

`tests/playwright/test_consent_banner.py` is a separate fixture-lifecycle checkpoint. It is not staged with F1. No JobHunter or Siemens repository was changed.

## Validation results

- `.venv/Scripts/python.exe -m pytest tests/unit/test_field_mapper.py tests/unit/test_live_browser_policy.py -q` → **33 passed in 0.28s**.
- `.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/integration tests/pipeline -m "not live" -q` → **1,474 passed in 742.51s (12:22)**.
- `.venv/Scripts/python.exe -m ruff check src tests migrations` → passed, all checks passed.
- `.venv/Scripts/python.exe -m ruff format --check src tests migrations` → passed, 238 files already formatted.
- `.venv/Scripts/pyright.exe` → 0 errors, 0 warnings, 0 informations.
- `git diff --check` → passed before staging.
- Separate browser-lifecycle diagnostic after the consent fixture change: ordered dashboard + consent reproducer → 8 passed; consent module alone → 7 passed. The focused combined non-live Playwright gate has not been rerun as a whole.
- No live tests, real ATS actions, or real submissions were run.

## Decisions

- F1 is intentionally limited to `field_mapper.py` and its focused unit tests. It does not implement the remaining V2-01 acceptance cases for read-back, import ownership, duplicate gates, upload evidence, or readiness.
- Any explicit per-job answer continues through its existing explicit-answer path; automatic evidence inference abstains when a skill claim is negative, contradictory, or unsupported.
- The negation detector is a conservative safety guard for English and German patterns exercised by tests; it is not represented as a general language-understanding system.
- The consent-test lifecycle change is separately reviewable and will be committed only after the F1 checkpoint. Its tests use pytest-playwright's page fixture in place of nested sync Playwright managers.
- The V2-00 baseline checkpoint is committed and supervisor-reviewed. Its original full combined non-live run had an incomplete failure; the focused lifecycle reproduction now passes, and the completed V2-01 non-browser gate passes. A full combined Playwright-inclusive gate remains unverified.
- Existing WQ-8 owner approval remains required for any real target/live action. This code milestone performed none.

## Blockers / risks

- Supervisor review of the exact staged F1 diff is pending before commit/push.
- The consent-test fix has separate focused evidence but still needs its own staged review, focused rerun, commit, and push.
- No full Playwright-inclusive non-live suite result is claimed. F2–F6 and the rest of V2-01 remain future work.

## Exact next action

Review the staged F1 paths and test results. After supervisor approval, commit and push only the F1 implementation, unit tests, and V2-01 handoff files; then verify `HEAD` equals `origin/checkpoint/v2-01-correctness`. Update this handoff and stage the consent fixture repair separately, rerun its ordered 8-test reproducer plus standalone 7-test module, then commit/push that second checkpoint. Stop for supervisor review before beginning another V2-01 implementation slice.

- **Last updated:** 2026-09-24T14:55:28Z.
