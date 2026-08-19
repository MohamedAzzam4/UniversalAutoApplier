# WQ-7C Full Same-Job System Closure Proof (natural normal-workflow)

Status: **READY for review**
Date: 2026-08-19 (same-day natural run + UAA live proof; a second discovery
attempt the same day re-proved the bounded search but its evaluation stage was
externally rate-limited — see "Fresh re-attempt note").
Branch: `checkpoint/wq-7c-synthetic-mutation`
Head: resolve dynamically (`git rev-parse HEAD`).

This closes the two remaining gaps of the previous closure report:

1. **Normal discovery proven.** Everything starts from JobHunter's normal
   bounded full-workflow **search entry** `run_all.py` (scan → evaluate →
   tailor → export) with the test-only overrides
   `--threshold 1.0 --german-policy accept_all`. No `run_evaluate(mode="url")`,
   no manually injected `pipeline.md` URLs. JobHunter production code unchanged
   (branch `main`, commit `0e8ba2f`).
2. **SAME-JOB identity end-to-end.** One naturally discovered job stays
   identical through the whole chain to `submitted=false` (Robco/Ashby). No
   company/job is substituted after queue import.

## Sample: where the work ran

- Natural run workdir (produced the queue): `C:\Users\LOQ\AppData\Local\Temp\opencode\jh_ws_natural_20260819`
  (throwaway copy of JobHunter — code only, `.git`/`tests` removed; synthetic
  `config/profile.yml` + `cv.md` inside; nothing committed to either repo).
- UAA used the standard opt-in paths only: orchestration
  `start(synthetic_orchestration=True)` and the `live-synthetic-mutation` CLI.
  No orchestrator redesign, no single-command wrapping.

## 1. Normal JobHunter discovery + evaluation + tailoring + export

- Entry: `python run_all.py --threshold 1.0 --german-policy accept_all`
  (normal pipeline: `run_scan()` → `run_evaluate(mode="all")` →
  `export_queue(threshold=1.0)`).
- Scan (live, bounded config): 2 JobSpy search terms (`Working Student AI`,
  `Werkstudent Data`) × 1 location (Munich, Germany) × `indeed`+`linkedin`,
  `results_wanted=8`; Bosch SmartRecruiters direct API returned 0 jobs.
- Discovered 28 raw candidates → after title/location filters + dedup
  **7 naturally discovered jobs evaluated** (all real live postings):
  Allianz (3.5/2.5 consider), ClearOps (1.8 skip), Robco (4.7 **apply**),
  Celonis (2.3 skip), Siemens (4.7 **apply**), Infineon (2.6 consider).
- Tailored CV + cover letter PDFs generated for every scored job
  (`output/test-candidate_*`), synthetic persona only.

### Exported queue — `data/application_queue.jsonl`

SHA-256: `3032c933b55352e5954cd1c14f9db301a54859939c32cab7032dfc0cc684a2b2`

| application_id | platform | source | company | title | URL | score | verdict |
|---|---|---|---|---|---|---|---|
| `735df4e79a37c1bcdd608fd5dbc46ac6739ebf5372ce03eb5d10bff286ad43d8` | unknown | indeed | Robco | Working Student - Robot Pilot / Data Collection (m/f/d) | https://de.indeed.com/viewjob?jk=ee5808aafe77c034 | 4.7 | apply |
| `e794e41d295b9391e22444dc8636598e64d69fa00579c6cfa8d2b36b181608dc` | unknown | indeed | Siemens | Working Student (f/m/d) Simulation for Physical AI | https://de.indeed.com/viewjob?jk=711dc466890f4b3d | 4.7 | apply |

- German policy `accept_all` (override, recorded in queue `german_filter_result=none`).
- `external_job_id=null` for both → canonical-URL identity (UAA contract).
- `status=ready_to_apply`; both carry the whitelisted synthetic candidate
  snapshot (Test Candidate / test.candidate@example.com).

### Tailored CV hashes

- Robco: `output/test-candidate_robco_working-student-robot-pil-cv.pdf`
  SHA-256 `4f0b4ae14e1689909a566efe0948e7f8231ade39ad91dc4d24032a3beca46ad6`
  (+ cover `6315f71a...`).
- Siemens: `output/test-candidate_siemens_working-student-f-m-d-sim-cv.pdf`
  SHA-256 `6de2a5c64dab23d52064f05f49b3742240f0268352ed5b9987c1c1a9a134790f`.

## 2. UAA import and targeting (same application_id)

- Fresh `uaa_data`; server 127.0.0.1:8029 (`UAA_ENABLE_REAL_SUBMISSION=false`).
- POST `/api/orchestration/start`
  `{mode:"sequential", synthetic_orchestration:true, max_jobs:5}` →
  run `d1d72c1b-6a69-44ae-95d2-37e1f7d5a44b`, status `completed`.
- `queue_hash_before == queue_hash_after == 3032c933…` (queue unedited);
  `jobhunter_pid=null`, message "production workflow not re-run".
- `queue_import_state=success`, `queue_imported=2`, `queue_skipped=0`,
  `queue_published=true`, `pass_count=1`, `errors=[]`.
- `newly_eligible == targeted == processed ==`
  [`735df4e7…`, `e794e41d…`], `remaining=[]`.
- DB after: both `application_jobs` → `status=needs_user_input`, verdict
  `apply`, `metadata.candidate_profile.synthetic_test=true`;
  `submission_results` count = **0**.

## 3. Candidate A — Robco → Ashby (SAME-JOB chain complete)

- ATS/platform: **Ashby** (`jobs.ashbyhq.com`), anonymous application form (no
  login). Proof URLs/company/title/location unchanged: RobCo careers
  `https://www.rob.co/careers` board → job
  `https://jobs.ashbyhq.com/robco/9c4d43d5-f2b6-48b5-97f8-f5b0a11f58fa`
  ("Working Student – Robot Pilot / Data Collection (m/f/d)", Munich,
  Engineering, Full-time) ↔ source Indeed URL `jk=ee5808aafe77c034`.
- UAA target URL (same job): `https://jobs.ashbyhq.com/robco/9c4d43d5-f2b6-48b5-97f8-f5b0a11f58fa`
  for application_id `735df4e7…`.
- Command: `live-synthetic-mutation --application-id 735df4e7… --start-url
  https://jobs.ashbyhq.com/robco/9c4d43d5-f2b6-48b5-97f8-f5b0a11f58fa`
  → EXIT=3 (`needs_user_input`).
- Result: 1 `safe_apply` click ("Apply for this Job") → `/application` reached;
  **12 fields extracted; 5 filled; 2 `intervention_needed`; 5 skipped**:
  - filled: First Name=Test, Last Name=Candidate, Email=test.candidate@example.com,
    Phone=+1 555 0199, Resume=`synthetic-docs/wq7c-test-cv.pdf`.
  - intervention_needed (NOT fabricated): Salary Expectations, LinkedIn URL.
  - skipped (never auto-answered): autofill-resume file, Gender radio,
    Available-from, Location combobox, Supporting Documents.
- **Synthetic upload confirmed**: `uploads[0]` document_kind=cv, status
  `uploaded`, "approved synthetic document"; uploaded file SHA-256
  `e5dd0dd56797280dcbc1b426a7a10e97ff2576b5bbd96a42b466d544cebeac27` ∈
  `document_hashes_approved`.
- Mutation-plan chain: `plan_hash=11dac7183134f83a5cacec4c736c2f58a18e946dc3fbda540a4da11ce74c2d1a`;
  `plan_chain_hashes` = [`11dac718…`, `29fca8ac6920b058c74f30b22c97cfb82e4ccde754f615be0132e57d4a27f502`];
  chain hash `ffa0f63b9e356b5eca28c500cdcb90fbbb3eb4b65d91773959c971ad9ee40f55`.
- Submit interlock: `installed=true`, all counters 0, `blocked=0` →
  **`submitted=false`**; stopped `required_fields_unresolved`.
- Artifacts: `...\jh_ws_natural_20260819\live_evidence\735df4e79a37-20260819T173619755092Z\`
  (report.json, mutation-plan.json + pass-1, final-page.html, trace.zip,
  step-01/step-02/step-02-after-mutation/final pngs).

## 4. Candidate B — Siemens → Siemens careers portal (blocked, honest record)

- ATS/platform: Siemens new careers portal (own portal, not Indeed).
  Proof same job: source `jk=711dc466890f4b3d` → official portal
  `https://jobs.siemens.com/en_US/externaljobs/JobDetail/516786`
  ("Working Student (f/m/d) Simulation for Physical AI", Garching/Bayern,
  Job ID 516786) for application_id `e794e41d…`.
- Result: `live-synthetic-mutation --application-id e794e41d… --start-url
  .../JobDetail/516786` → `needs_user_input` / `click_failed`: the UserCentrics
  consent overlay (`div#usercentrics-root`) intercepted the "Apply" click to
  `ApplicationMethods?folderId=516786` (30s timeout) in the ephemeral profile
  (no consent cookies). Security/consent gate before mutation → policy STOP,
  no bypass. clicks=0 fields=0 uploads=0, interlock `installed=true blocked=0`,
  **`submitted=false`**. (Invocation's shell exit 1 was a cp1252 console encode
  crash while printing the error list; persisted `report.json` is
  authoritative — rerun not needed.)
- Artifacts: `...\jh_ws_natural_20260819\live_evidence\e794e41d295b-20260819T173335889321Z\`.

## 5. SAME-JOB traceability assertion

- **Robco**: exported row `735df4e7…` (JobHunter natural discovery on Indeed
  `jk=ee5808aafe77c034`, company/title/location RobCo / Working Student -
  Robot Pilot / Data Collection / Munich) ←→ same job on RobCo's Ashby board
  (same company/title/location) → imported into UAA under the same
  application_id (identity-guarded synthetic path) → **that same** Ashby
  application form mutated (5 fields filled + approved synthetic CV uploaded)
  → interlock armed → `submitted=false`. No substitution at any stage.
- **Siemens**: exported row `e794e41d…` (Indeed `jk=711dc466890f4b3d`) ←→
  same job on Siemens' own portal (JobDetail/516786, same company/title/
  location) → imported under the same application_id → apply flow blocked by a
  consent-banner overlay before mutation → `submitted=false`. Recorded
  honestly; no bypass attempted.

## Fresh re-attempt note (2026-08-19, later same day)

A second, fully independent bounded normal run was started from a fresh
throwaway workdir (`...\jh_ws_closure_20260819`) via
`run_all.py --threshold 1.0 --german-policy accept_all` to re-prove the
closure. The scan re-proved normal **search/discovery** against the live web:
16 raw candidates → **12 naturally discovered** AI/Data Working-Student jobs
(Allianz, Infineon, Temedica — Working Student AI Engineer, Avelios Medical —
Working Student ML, DLR — Working student Machine Learning,
Giesecke+Devrient — Computer Vision & Deep Learning, BMW — Werkstudent Data
Science/KI, Allianz Digital Health — Working student Data Engineer, etc.).
The evaluation stage however returned **HTTP 429 on every call** for the single
configured OpenRouter key: `"Rate limit exceeded: free-models-per-day. Add 10
credits to unlock 1000 free model requests per day"` — the key's 50
free-requests/day quota was already consumed by earlier same-day runs (incl.
the successful natural run above). This is an external provider quota, not a
pipeline defect; no bypass was attempted. The discovery stage of the closure
therefore rests on the identical bounded config, and the evaluation→export
stage rests on the same-day successful natural run recorded above (both gaps
satisfied end-to-end).

## Required evidence checklist

- discovery source/run — JobHunter `run_all.py` normal scan (JobSpy indeed+
  linkedin, bounded; workdir above), 7 evaluated.
- company/title/location — RobCo / Working Student - Robot Pilot / Munich;
  Siemens / Working Student (f/m/d) Simulation for Physical AI / Garching.
- source URL — Robust `https://de.indeed.com/viewjob?jk=ee5808aafe77c034`;
  Siemens `https://de.indeed.com/viewjob?jk=711dc466890f4b3d`.
- evaluation score/recommendation — 4.7/5 apply (both).
- threshold/German policy — `--threshold 1.0 --german-policy accept_all`.
- tailored CV hash — Robco `4f0b4ae1…`; Siemens `6de2a5c6…`.
- queue row + queue hash — above; queue SHA-256 `3032c933…`.
- application_id / UAA imported application_id — identical (735df4e7…,
  e794e41d…), verified via orchestration final state + DB markers.
- UAA target URL — same-job real ATS URLs above.
- proof URLs/company/title remain same job — SAME-JOB assertion above.
- ATS/platform — Ashby (RobCo); Siemens careers portal.
- blocker or form reached — form reached (Ashby) / consent overlay (Siemens).
- mutation-plan chain — plan_hash + 2-pass chain hashes above.
- fields mutated — 5 filled (name/email/phone/CV) + 2 interventions + 5 skips.
- synthetic upload — 1 (approved `wq7c-test-cv.pdf`, hash `e5dd0dd5…`).
- submit counters — interlock all-zero, `blocked=0`.
- submitted state — `submitted=false` (both candidates).

## Rules honored

- JobHunter production files: none changed.
- No manual queue fabrication/editing, no manual DB seeding, no manually
  substituted ATS job, no real personal data, no real CV, no submission, no
  anti-bot bypass, no embeddings/mapper optimization.