# WQ-8 Final Review Packet — msg Job 411 (Data & AI / Banking)

**Application ID:** `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e`
**Target:** msg for banking ag — Werkstudent Data & AI / Banking (all genders)
**ATS:** jobs.msg.group (d.vinci HR-Systems) — anonymous, no login, no CAPTCHA
**Phase:** A bridge/regression closure ACCEPTED by owner; snapshot re-freeze BLOCKED by a production wiring defect (§2.2). NOT ready for Phase B.
**Branch:** `checkpoint/wq-8-controlled-real-submission`
**Closure base HEAD:** `95401c0f88025c6e1330b201bdb5471fe7dd5d0d`
(resolve dynamically — do not trust embedded SHAs:)

```text
git rev-parse HEAD
git rev-parse origin/checkpoint/wq-8-controlled-real-submission
```

---

## 1. Transcript / Hash Contradiction — RESOLVED

The previous closure report contained three mutually inconsistent claims. Verified
against persisted state (`.uaa_data/uaa.sqlite` + final live-run report
`fd9a41480fc6-20260829T005720988922Z/report.json`):

| Claim | Verdict | Evidence |
|---|---|---|
| "Transcript was uploaded and matched the explicit answer" | **TRUE** | `report.json` → `uploads[0].status="uploaded"`, message `"Matched explicit answer from metadata.form_answers"`, selector `input[id='attachmentFile']` |
| "review_plan_hash = `171105cb6e6ce2bb69626f7aad1de0e4`" | **FALSE — never persisted** | `171105cb…` appears in NO database row, NO live-run artifact, and NO committed evidence file. It exists only in the prior report narrative and is discarded. |
| "Owner still needs to confirm transcript before the hash freezes" | **STALE — withdrawn** | The owner already approved the Bachelor's transcript for this application and the transcript was uploaded + matched in the final live run. No redundant confirmation is requested. |

The ONLY hash treated as frozen is the one produced by the canonical
`wq8-review-packet` command below.

---

## 2. Canonical Freeze — actual `wq8-review-packet` output

Command run exactly as specified:

```text
wq8-review-packet --application-id fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e
```

Actual sanitized output (alembic INFO lines omitted):

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

To authorize the single real submission, run (see docs/evidence/wq-8/DESIGN.md):
  python -m universal_auto_applier wq8-authorize --application-id fd9a41480fc6 --review-plan-hash e9db86210192112306c6a1497b6ba776 --confirm
```

- **Frozen `review_plan_hash` = `e9db86210192112306c6a1497b6ba776`** (canonical).
- `status = review_ready` ✅
- `pending_interventions = 0` ✅
- `submitted = false` ✅ (job + final run report)
- `SubmissionAuthorization` = **NONE** (0 rows in `submission_authorizations`; also
  0 submission results, 0 claims) ✅

**Owner verdict (2026-08-30): this freeze is NOT acceptable for Phase B.** The
frozen plan covers the empty persisted snapshot (§2.1), so
`e9db86210192112306c6a1497b6ba776` **must NOT be used for owner authorization.**

### 2.1 Required honesty caveat — the frozen plan covers an EMPTY snapshot

The persisted review snapshot the packet reads (active approval
`ed5241a7958ae1e146d78357f4523f8a`, snapshot hash `72ede0dc8484a5a81a71e940f0c037f8`,
created `2026-08-29T00:52:30Z`) contains **0 fields, 0 documents, and no submit
control** — it was observed empty. The final live dry-run
(`…T005720988922Z`, finished 00:59:17Z) reached `review_ready` with **25 mapped
fields** (14 `application_job`, 6 `candidate_profile`, 5 no-value optional) and the
transcript upload, but the CLI dry-run flow does not persist a new approval
snapshot — the empty one is the only persisted review snapshot.

Consequences, stated plainly:

1. The frozen hash `e9db8621…` is authentic but does **not** pin the 25 filled
   field answers, the document set, or the submit control.
2. Phase B binding recomputes the plan hash from the CURRENT snapshot at submit
   time and fails closed on mismatch. If the owner re-observes via the dashboard
   (creating a full snapshot), the hash changes and a NEW `wq8-review-packet`
   freeze must be issued before `wq8-authorize`.
3. Resolution required before Phase B: persist a full snapshot through the
   official dashboard review observation flow, then re-freeze. Attempted
   2026-08-30 — blocked by a production wiring defect (§2.2).

### 2.2 Official re-observation attempt (2026-08-30) — BLOCKED: production wiring defect

The owner-directed action was to create a NEW persisted review snapshot via the
existing official dashboard/review observation flow (no manual `snapshot_json`
construction, no DB patching). What exists and what happened:

- **Official flow (code path is correct):** dashboard "Refresh Live Review"
  button → `POST /api/submit/{application_id}/observe`
  (`src/universal_auto_applier/ui/static/app.js:748`) →
  `observe_snapshot_endpoint` (`src/universal_auto_applier/api/routes/submit.py:281`)
  → `SubmissionExecutionService.observe_and_persist_snapshot`
  (`src/universal_auto_applier/submission/execution_service.py:199`) → real
  navigation + `execute_live_form` + `analyze_page` (submit control) →
  `build_snapshot` (fields, uploads with SHA-256 content hashes, submit control,
  pending count) → `create_approval` persistence (revokes the stale empty
  approval automatically).
- **Defect:** the endpoint requires `app.state.submission_context_factory`, but
  the production `create_app` lifespan (`src/universal_auto_applier/api/app.py`)
  never registers it. Only test harnesses register a factory
  (`tests/harness/submission_server.py:207`,
  `tests/harness/final_pipeline_server.py:316` — both `FixtureContextFactory`).
  In the real local deployment the official observe flow is unreachable dead
  code, which is exactly why the only persisted snapshot is the stale empty one.
- **Empirical proof (2026-08-30):** real local server started with
  `python -m universal_auto_applier` against the real `.uaa_data/uaa.sqlite`
  (127.0.0.1:8477, `/api/health` → `ready`), then:

  ```text
  POST /api/submit/fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e/observe
  → HTTP 503
    {"detail":"no browser context factory registered; cannot observe live form"}
  ```

- **Stopped there per owner instruction:** no DB patch, no manual snapshot
  construction, no `wq8-authorize`, no `live-submit`. Safety invariants
  re-verified unchanged after the attempt: `submission_authorizations`=0,
  `submission_results`=0, `submission_claims`=0, job status `review_ready`,
  `submitted=false`, stale empty approval row (`ed5241a7…`) untouched.

**Classification: snapshot-persistence implementation defect (production
wiring), not a data problem.** Fix direction for the follow-up sandbox
workpackage: register a real `PlaywrightContextFactory` on
`app.state.submission_context_factory` in the production lifespan
(settings-gated, local-only) and/or persist the review snapshot from the Phase-A
CLI dry-run when it reaches `review_ready` (via `create_approval`); add
hermetic regression tests proving the production app exposes a working observe
flow (stub factory) and that a dry-run reaching `review_ready` persists a
non-empty snapshot. After that fix is merged, re-run the real observation on
this machine and re-freeze with `wq8-review-packet`.

---

## 3. Document Evidence (run-verified, NOT in the persisted snapshot)

| Document | SHA-256 prefix | Verification |
|---|---|---|
| CV (approved) | `64099b2172932d15…` | Recorded from approved JobHunter output document (file not present in this workspace; not re-verifiable here) |
| Cover Letter (approved) | `297060f4df876e06…` | Recorded from approved JobHunter output document (file not present in this workspace) |
| Bachelor's Transcript (owner provided) | `5809eed9d31a525b…` | **Re-verified 2026-08-30** by hashing the owner-provided file: full `5809eed9d31a525baa2793d107d47b533f99c16397ab985336fc498cf0bec405` |

Document selection: CV + Bachelor's transcript (owner approved; cover letter part
of the approved WQ-8 document set). Upload of the transcript in the final live run
verified (see §1). These hashes are run/owner evidence — they are **not** part of
the currently persisted review snapshot (see §2.1).

---

## 4. Safety Verification (WQ-8 Phase A)

| Check | Result |
|---|---|
| Same application_id | ✅ `fd9a41480fc60a33486a1e338422e8a040e9069f802d1afa92d5849300679b0e` |
| Same job target | ✅ msg Job 411, `https://jobs.msg.group/de/jobs/411/form` |
| Real candidate profile | ✅ real (not synthetic; synthetic guard `e71a1d0` in place) |
| Interlock installed | ✅ `installed=true` (before navigation) |
| UAA submit clicks | 0 (`uaa_submit_clicks=0`, all submit counters 0) |
| Authorized submits | 0 |
| SubmissionAuthorization | NONE (forbidden) |
| `submitted` | `false` (job status `review_ready`, run report `submitted=false`, `stopped_reason=final_submit_detected`) |
| Status | `review_ready` |
| Pending interventions | 0 (all 14 `edited`) |
| Frozen review plan hash | `e9db86210192112306c6a1497b6ba776` |
| Persisted snapshot hash | `72ede0dc8484a5a81a71e940f0c037f8` (empty observation — see §2.1) |

Final live-run facts (`report.json`, sanitized): 25 fields observed, sources
`application_job` ×14 / `candidate_profile` ×6 / none ×5 (optional), 1 upload
(transcript, matched explicit answer), 0 errors, submitted=false, interlock
installed with zero submit clicks/events.

---

## 5. Bridge Regression Coverage (added before Phase B)

Hermetic synthetic tests (no owner PII; fixtures use `Test Candidate` /
`test.candidate@example.com` / `Teststadt`):

`tests/unit/test_wq8_intervention_bridge_regression.py`
- PLZ without explicit owner answer → no mapping (intervention guard holds)
- PLZ with explicit owner answer → maps, `source=application_job`
- Straße without explicit owner answer → no mapping
- Straße with explicit owner answer → maps, `source=application_job`
- candidate city never leaks into PLZ/Straße

`tests/unit/test_wq8_intervention_api_bridge.py`
- Intervention API with missing `field_label` falls back to `question`
  (exercises the REAL `POST /api/interventions/{id}/resolve` endpoint, not a
  copy of its logic), survives a full engine dispose + fresh DB reload, and the
  persisted answer is consumed by the deterministic mapper as
  `source=application_job`.

## 6. Quality Gates (actual results, 2026-08-30)

| Gate | Result |
|---|---|
| `pytest tests/unit tests/contract` | **1116 passed** (2:12) |
| `pytest tests/playwright/test_wq8_phase_a_interlock.py tests/playwright/test_wq8_interlock.py` | **12 passed** |
| `ruff check src tests migrations` | All checks passed |
| `ruff format --check src tests migrations` | 214 files already formatted |
| `pyright` | 0 errors, 0 warnings |
| `git diff --check` | clean |

---

## 7. Status: BLOCKED — snapshot persistence defect

1. The official re-observation flow cannot run in the production wiring (§2.2),
   so no new full snapshot and no new frozen `review_plan_hash` could be issued.
2. `e9db86210192112306c6a1497b6ba776` is **void for authorization purposes**.
3. Next action: fix and hermetically test the production context-factory wiring
   (GLM sandbox workpackage), merge, then re-run the real observation on this
   machine and re-freeze via `wq8-review-packet`. A NEW `snapshot_hash` and NEW
   `review_plan_hash` are expected at that point.
4. Phase B remains **locked**: no `wq8-authorize`, no `live-submit` was run.
   Phase B requires `UAA_ENABLE_REAL_SUBMISSION=true` +
   `wq8-authorize --review-plan-hash <new frozen hash> --confirm` +
   `live-submit --approval-id <id> --confirm`, owner-driven only.

---

**Evidence:** `docs/evidence/wq-8/FINAL_REVIEW_PACKET.md` (this file),
`docs/evidence/wq-8/PHASE_A_MSG_411_PACKET.md`,
`docs/evidence/wq-8/WQ8_PHASE_A_CLOSURE_GATE_REPORT.md`,
`docs/evidence/wq-8/WQ8_DOCUMENT_MAPPING_SAFETY_CLOSURE.md`,
`docs/evidence/wq-8/WQ8_GERMAN_FIELD_MAPPING_READY.md`,
live-run report `.uaa_data/live-runs/fd9a41480fc6-20260829T005720988922Z/report.json`
(local, untracked)

**WQ-8 SNAPSHOT PERSISTENCE NEEDS CHANGES**
