# WQ-7C — Final Acceptance Record

Status: **ACCEPTED** (full-system proof reviewed and accepted by the owner;
PR to `main` opened but NOT merged; WQ-8 not started).

Branch: `checkpoint/wq-7c-synthetic-mutation`
Final documented SHA at acceptance: resolve dynamically
(`git rev-parse HEAD` / `git rev-parse origin/checkpoint/wq-7c-synthetic-mutation`).

## Authoritative evidence (in precedence order)

1. `FULL_SAME_JOB_CLOSURE.md` (this directory) — the final same-job full-system
   proof: normal JobHunter discovery through pre-submit on one identical job
   (Robco / Ashby). **This is the authoritative WQ-7C acceptance evidence.**
2. `MANIFEST.md` (this directory) — component-level real-ATS mutation evidence
   (Greenhouse Carta + Lever Apply Digital) plus the detector defects found
   and fixed during the proof. Component evidence, preserved.
3. `docs/handoffs/ACTIVE_WORKPACKAGE.md` — running history, including
   intermediate milestones now marked SUPERSEDED for final acceptance.

## Superseded intermediate experiments (kept as audit history)

The following earlier WQ-7C milestones were valid intermediate experiments but
are **NOT** the final acceptance evidence:

- Senior Account Executive synthetic persona (used to score the Carta
  full-time AE sheet). **SUPERSEDED** by the realistic AI/Data Working-Student
  persona used in the final natural run.
- `pipeline.md`-driven Carta row + `run_evaluate.py --next` flow.
  **SUPERSEDED** by the natural `run_all.py` discovery flow.
- Greenhouse Carta / Lever Apply Digital live mutations — kept as valid
  component-level real-ATS evidence (platform coverage), not as the full-system
  trace.

The authoritative final proof is the Robco/Ashby same-job trace described in
`FULL_SAME_JOB_CLOSURE.md`.

## A. Synthetic safety mode

- Opt-in only: `UAA_LIVE_SYNTHETIC_MUTATION` (default **off**); the CLI and
  orchestration refuse when the mode is disabled.
- Synthetic candidate enforcement: the mutation path refuses any job whose
  `metadata.candidate_profile` does not carry the whitelisted synthetic
  identity (Test Candidate / test.candidate@example.com + markers).
- Approved-document hash enforcement: only documents whose SHA-256 is in the
  `approved_document_hashes` set may be uploaded; the CV is banner-labelled
  "SYNTHETIC TEST DOCUMENT / NOT A REAL CANDIDATE".
- Incompatible with real submission: `UAA_LIVE_SYNTHETIC_MUTATION` and
  `UAA_ENABLE_REAL_SUBMISSION` are mutually exclusive by config validation;
  the orchestration opt-in independently rejects both real-submission and
  recon-only modes.
- Ephemeral browser profile always (never reuses cookies/session state).

## B. Real mutation proven

- **Greenhouse (Carta)** — 19 fields planned / 10 filled, 1 approved synthetic
  CV upload, 2-pass plan chain, interlock all-zero, `submitted=false`,
  stopped `required_fields_unresolved`.
- **Lever (Apply Digital)** — 21 fields filled, 2 approved synthetic CV
  uploads, 2-pass plan chain, 1 Lever page-initiated `form.submit()` blocked by
  the interlock, `submitted=false`.
- **Ashby (RobCo) — full-system same-job** — 12 fields extracted, 5 filled,
  1 approved synthetic CV upload, 2-pass plan chain, interlock all-zero,
  `submitted=false`. This is the authoritative end-to-end trace.

## C. Field mapping

- Frozen, hashed mutation plan produced **before** any mutation; the persisted
  plan re-verifies (`generated_at` excluded from hash).
- Unresolved/ambiguous fields are skipped or recorded as interventions
  (`needs_intervention`, "not fabricated").
- Never-auto-answered categories: availability; salary; legal declarations;
  consent/signatures; demographic/sensitive; work-authorization — plus any
  field without a deterministic mapping at confidence < 0.7.
- Known mapping weaknesses from live runs are documented in
  `ACTIVE_WORKPACKAGE.md` (deferred): cover-letter upload locator timeout;
  mis-targeted "Location" field on Greenhouse; Lever cards naming; etc.
- Embeddings/field-mapping optimization is **deliberately deferred** (no
  embeddings, no mapper optimization in WQ-7C).

## D. Submission safety

- Zero real applications submitted across all WQ-7C runs.
- Robco full-system proof: submit interlock installed before first mutation,
  all counters zero, `blocked_submissions=0`, **`submitted=false`**.
- Previous Lever page-initiated programmatic `form.submit()` was successfully
  blocked and attributed to the page (not UAA): `uaa_submit_clicks=0`,
  `submit_events=0`.
- Interlock counters include: `uaa_submit_clicks`, `submit_events`,
  `form_submit_calls`, `request_submit_calls`, `dispatch_submit_events`,
  `blocked_submissions`, `navigation_attempts`.
- Honest stated limitation: the network-level
  `network_submission_detector` is `not_instrumented` in the reported runs —
  interaction-level interlock only.

## E. Full-system proof (authoritative)

- Normal JobHunter search proven: `run_all.py --threshold 1.0
  --german-policy accept_all` (test-only overrides) — bounded live scan
  (JobSpy indeed + linkedin) → 7 naturally discovered Working-Student AI/Data
  jobs evaluated → 2 `apply` rows exported.
- Tailoring/export proven: tailored CV + cover letter PDFs generated
  (synthetic persona only); `data/application_queue.jsonl` exported with
  deterministic `application_id`s (Robco `735df4e7…`, Siemens `e794e41d…`).
- Queue NOT manually fabricated: it came from the immediately preceding normal
  JobHunter run, unedited, hash/path recorded (SHA-256 `3032c933…`).
- Same application identity preserved through UAA import/orchestration
  (run `d1d72c1b…`, `queue_hash_before == after`, markers stamped,
  `submission_results=0`).
- UAA real-ATS mutation + upload proven on the **same** naturally discovered
  Robco job at RobCo's official Ashby board (same company/title/location),
  stopping pre-submit.
- Siemens same-job traceable but stopped safely at the consent overlay before
  mutation (no bypass).

## External limitation (non-invalidating)

A later independent JobHunter re-attempt the same day re-proved normal
discovery (12 AI/Data Working-Student jobs discovered) but the evaluation
stage returned OpenRouter HTTP 429 (`free-models-per-day`, 50/day quota
exhausted on the single key). This is an external provider quota; it does not
invalidate the earlier successful same-day full-system proof and is not a
reason to re-run.

## What is NOT yet proven (candid)

- Real application submission (WQ-8).
- Production operation with the owner's real candidate data.
- Broad long-run reliability across many jobs / ATS variants.
- Optional field-resolution/embedding optimizations.

No claim is made that any of the above is complete.