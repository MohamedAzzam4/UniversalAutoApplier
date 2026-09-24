# UAA V2 — critique of the plan and corrective decisions

Audit date: 2026-09-24. Subject: the accumulated V2 plan, including dashboard, parallelism, testing, JobHunter integration and LLM-provider follow-ups. This is a design review; source/test results from the earlier project review were not re-run. No UAA or JobHunter source was changed.

## Verdict

The previous plan described the intended features better than it specified the contracts between them. It was not ready to hand to an implementation agent and expect consistent results. It contained two overstatements (pre-submit upload acceptance and preservation of history newer than a restored backup), an ambiguous release metric, and missing ownership/transaction rules that could reintroduce the same fragmentation identified in V1.

I have corrected the affected passages in `UAA_V2_REVIEW_AND_PLAN.md`, narrowed the first delivery, and added section 17 with explicit contracts and unresolved decisions. The detailed workpackages are a backlog, not a promise to ship every ATS, model option and recovery feature together.

## Ranked findings

### 1. P1 — "Verified upload" was defined too strongly for some forms

**Previous assumption:** every required file must be remotely accepted before review-ready.

**Counterexample:** a native HTML file field only transfers its selected file with the final form submission. A pre-submit server receipt cannot exist. The original rule would either block a perfectly valid application forever or encourage invented acceptance evidence.

**Correction:** distinguish verified local selection from asynchronous remote acceptance. Each flow declares which evidence makes its documents ready. UI wording and snapshots retain that distinction. T04/T19 cover both protocols.

### 2. P1 — Restore protection promised information an old backup cannot contain

**Previous assumption:** restoring a backup must preserve later submission knowledge.

**Counterexample:** the only surviving backup predates a submission. Restoring it makes that job appear unsubmitted unless newer evidence survives elsewhere.

**Correction:** restored data enters a recovery epoch; approvals/leases are revoked and external mutations stop until the missing interval is reconciled. Unknown history is not safe history. This can require owner input and cannot be solved by a stronger database transaction. T25 makes the limitation explicit.

### 3. P1 — "Save & resume" was a UI promise without a transaction contract

**Failure scenario:** a fact is saved but its intervention remains pending; a crash loses the resume request; two open dashboard tabs enqueue duplicate retries; a delayed form overwrites a newer correction.

**Correction:** expected revisions and idempotency keys; one transaction for answer/provenance, intervention resolution, approval invalidation and resume enqueue; browser execution only after commit. State/events commit together, delivery may repeat, and UI deduplicates. T20/T26 cover these boundaries.

### 4. P1 — Paused/expired workers could still own the browser

**Previous assumption:** a pause or expired lease permits takeover/resume.

**Failure scenario:** the worker stalls, its lease expires, a second worker starts, and the original worker wakes up and clicks. The same conflict can happen when the owner edits the browser while automation is still running. This matters before parallel jobs exist.

**Correction:** acknowledged ownership transfer, ownership generations, revocation and a single browser-session owner. If exclusive control cannot be established, intervention replaces automatic recovery. Safe replay depends on action type; add-row/upload/draft actions need reconciliation. T21/T22 cover this.

### 5. P1 — Improving facts can make the application contradict its documents

**Failure scenario:** the owner corrects graduation date or language level in UAA. Forms now use the corrected value, while the JobHunter-tailored CV still states the old value. Reusing the corrected answer alone produces inconsistent applications.

**Correction:** versioned dependencies between facts, answers, bundles and review snapshots. Flag affected bundles for owner replacement or a new JobHunter export; UAA does not take over tailoring. Preserve submitted history. T23 covers the mismatch.

### 6. P1 — File import consistency was stronger than the available producer evidence

**Previous assumption:** a stable JSONL file and locally computed document hashes establish a consistent JobHunter handoff.

**Counterexample:** the JSONL is unchanged while JobHunter overwrites a referenced CV path. UAA can consistently copy the wrong generation. Hashing it only proves which bytes were copied.

**Correction:** distinguish immutable local capture from upstream generation provenance. Use an upstream manifest/immutable artifact contract when available; otherwise record the limitation and verify the bundle before mutation. Inspect an actual completed export from the repaired JobHunter before claiming compatibility. No JobHunter modification is required for the immediate UAA fixes. T24 covers the gap.

### 7. P1 — Unknown-site automation conflicts with a universal no-submission guarantee

**Previous tension:** automatically handle unfamiliar forms while refusing every unverified side effect. Upload, autosave, Continue and final submission may all be requests to the same endpoint, with site-specific semantics.

**Correction:** qualify concrete flow capabilities, not just ATS names. Unknown mutation behavior causes observation/handoff until a recipe is validated. Request monitoring is supplementary evidence with declared coverage; it cannot establish a universal guarantee for arbitrary scripts. This reduces initial coverage honestly rather than presenting an unsolved inference problem as a completed safety feature.

### 8. P1 — A "single executor" did not settle competing state authorities

**Gap:** job lifecycle, attempt progress, supervisor state, pipeline run state, dashboard markers and approval records could still disagree even if browser code was shared. Retry/cancel/review invalidation did not have a canonical transition contract.

**Correction:** job lifecycle/duplicate eligibility are authoritative; attempts own execution progress; runs aggregate; supervisor/UI expose projections. Every action goes through an allowed repository command and revision check. The transition table is a V2-00 prerequisite, using existing public status names initially.

### 9. P1 — Migration flags could revive the defects being fixed

**Previous assumption:** keep the old path behind a switch while introducing V2 and prove parity.

**Failure scenario:** fallback to V1 bypasses the new duplicate/request/readiness guard. Alternatively, a parity test insists on preserving an existing incorrect answer because the old implementation produced it.

**Correction:** common gates protect both paths, and intended behavior is the compatibility target. Known bugs intentionally change; their tests document that change. Shadow comparisons use local fixtures, never duplicate live mutation. T29 covers mode-switch bypass.

### 10. P2 — Quality metrics could hide errors or reward excessive abstention

**Problems:** 99% field precision is not 99% application correctness; related fields/templates have correlated errors. Five hundred total cases may provide few accepted cases in important categories. A 90% readiness target on "supported fixtures" could be read as permitting regression failures. No minimum useful coverage had been defined.

**Correction:** all release regressions pass. Exploratory coverage has a separate denominator and target. Report whole-application correctness, accepted counts, coverage, category errors and confidence intervals. Separate training, calibration and holdout employers/layouts. Set the useful-coverage floor after measuring the baseline. Dataset counts are collection targets, not mathematical proof of reliability.

### 11. P2 — Provider configurability was mixed with model qualification

**Previous tension:** switching APIs should be easy, but each change required full benchmark qualification before use. One common provider interface also risked conflating field answering with supervisor planning.

**Correction:** a new configuration may operate in suggestion-only mode after connectivity/schema checks. Automatic acceptance is granted per qualified model/task profile. Normalize transport while retaining task-specific schemas and authority. Different protocols need adapters; model-reported confidence cannot be treated as calibrated across providers. Missing facts skip model escalation entirely.

### 12. P2 — Local model deployment was absent from "local-first"

**Gap:** no confirmed hardware/runtime fit, installation footprint, model revision strategy, CPU fallback or compatibility boundary for the repository's Python matrix.

**Correction:** an optional inference-runtime spike precedes model selection. Core dashboard/executor startup remains independent of GPU availability/model downloads. Pin model/tokenizer versions, report local inference health and test corrupted/offline assets. Laya is an evaluated candidate, not an architectural dependency. No claim of current hardware compatibility has been made.

### 13. P2 — Local security and deletion semantics were underspecified

**Gap:** localhost binding alone does not define authorized browser/API requests. Imported paths and browser/model content could reach document/provider operations without an explicit trust boundary. Deleting a saved fact did not specify deletion from embeddings/caches. Copying bundles and keeping traces increases storage/privacy cost.

**Correction:** specify origin/host/request protection, credential handling, approved file roots and path escape checks; untrusted content cannot select arbitrary files or secrets. Define evidence retention and cache/index invalidation. These are required design controls; this audit did not reproduce an API exploit or assert a new external compromise.

### 14. P2 — The delivery plan was becoming another ambitious multi-phase project

**Problems:** multi-ATS support, a hybrid model stack, provider UI, a polished dashboard, migration/restore, login recovery and live submission all accumulated as one release. The "first slice" already included later recovery work. A real submission authorization could block delivery of useful preparation features. The plan itself lives outside repository checkpoints.

**Correction:** ship four capability increments: correctness baseline; one-job product; qualified assistance; release qualification. Design the dashboard early and implement it per journey. Choose one first flow from the actual queue; expand by measured demand. Qualify preparation separately from controlled submission, leave parallelism deferred, and promote the revised plan into repository memory during V2-00. The original unattended-submission ambition remains an explicit product decision, not something already satisfied by review-only preparation.

## Decisions the audit cannot honestly settle

- Which real form family should be first, until an available owner-approved target/queue is chosen.
- Whether the repaired JobHunter publishes sufficient generation evidence, until an actual completed export is inspected.
- Which local model and acceptance thresholds are appropriate, until actual-machine benchmarks and held-out evaluation exist.
- Evidence retention, backup location and resource budgets, until reviewable defaults are chosen for the owner's machine.
- Whether to permit unattended policy-based final submission in a later version; the current snapshot approval contract remains unchanged.

These do not block immediate synthetic regression fixes. They have named decision gates in section 17 of the revised plan so implementation cannot quietly assume an answer.

## What changed as a result

- Corrected the contradictory upload, restore, migration, metrics and first-slice passages directly.
- Narrowed initial ATS/release scope and separated preparation from submission qualification.
- Added transaction, ownership, side-effect, state, trust and runtime contracts with workpackage ownership.
- Added T19–T29: eleven missing acceptance scenarios, bringing the named scenario catalog to 29. T18 is still deferred with parallelism; the catalog is not a count of implemented test functions.
- Left the underlying UAA/JobHunter repositories untouched. The audit revised planning artifacts only; it did not rerun the code suite or establish the future guarantees through tests.

The next implementation step is V2-00's baseline and contract decisions, followed by immediate correctness fixes. It is not to begin all 29 scenarios, every ATS and every model integration simultaneously.
