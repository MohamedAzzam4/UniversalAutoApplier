# UniversalAutoApplier V2 — implementation review and delivery plan

Review date: 2026-09-24. Scope: UAA only. JobHunter was neither modified nor run. AutoApplierAgent workflow, answer-policy, and test-report documents were read as reference; its claims were not independently re-tested.

Owner refinement incorporated 2026-09-24: dashboard usability, live failure visibility, self-service correction and automatic scoped saving are core V2 requirements. Parallel application preparation is a deferred capability, with architectural support planned now. These are planned behaviors, not implemented features.

Additional owner requirements incorporated: concrete regression/E2E/live test cases and release gates (section 14); an explicit JobHunter output contract and UAA-owned state boundary (section 15); interchangeable LLM provider configuration and adapters (section 16). New test names and configuration fields below are specifications, not implemented interfaces.

Plan self-audit completed 2026-09-24. This is a revised planning artifact; V2-00 records the adopted execution contracts in section 17 and checkpoints this copy on `checkpoint/v2-00-baseline`. Critical inconsistencies were corrected in place; section 17 specifies the missing execution contracts, narrower delivery cuts and remaining decision gates. The companion `UAA_V2_PLAN_CRITIQUE.md` explains the shortcomings of the previous revision. Test results in section 10 belong to the original code review; no new product validation is implied by this document revision.

## 1. Recommendation

Develop V2 by consolidating the existing UAA into one reliable execution system. Preserve its persistence, queue contract, intervention stores, browser primitives, and controlled-submission machinery. Do not start a wholesale rewrite or make an external chat agent the production runtime.

The intended product is a local application that imports prepared jobs, knows the candidate's approved facts and documents, navigates supported forms, completes and verifies them, and resumes after necessary owner interventions. Its internal resolution sequence is:

**Deterministic rules and approved answers → local semantic retrieval/classification → API LLM → explicit unresolved state.**

This incorporates the owner's clarified preference. An external MCP client can remain an optional operator interface to the same UAA services. It should not create a separate application history or browser execution path.

“Any job on any website with no intervention” should remain an aspiration, not a release criterion. V2 needs measured support levels per application flow. Logins, MFA, active CAPTCHA challenges, missing candidate facts, and materially new consent choices remain explicit exceptions. Automation should reduce repeated questions by remembering scoped, approved answers.

Autonomous preparation and autonomous final submission are separate capabilities. The current repository requires approval of the exact current snapshot and prohibits generic adapter auto-submit. V2.0 retains that contract while making preparation independent of a chat agent. The application may execute an explicitly approved submission through the existing coordinator. Unattended policy-based final submission is a later product decision requiring a deliberate change to that contract; adding an LLM does not authorize it.

## 2. Reviewed baseline and confidence

- Current checkout: `checkpoint/dashboard-history`.
- Reviewed local commit: `68014659ea7df5d2c1c632e27891ffbe478d978b`.
- Fetched `origin/main`: `76b2e1f166dd56398e7234c733ca24d703d0194a`.
- At review start, `origin/feature/agent-supervisor-mode-v0` was `bc24a153cd4eb7f1e486b0fcdfc63ed9699f571b`; the checkout had one additional dashboard-history commit and no matching remote checkpoint.
- **V2-00 baseline resolution (2026-09-24):** the selected checkpoint is `checkpoint/v2-00-baseline`, created from `68014659ea7df5d2c1c632e27891ffbe478d978b` and pushed to origin before edits. It descends from the WQ-8 checkpoint at `18783ffc1da216709d6a36d010157ce3430fd7e3`, then contains the supervisor work and dashboard-history commit. Its merge-base with `origin/main` is `76b2e1f166dd56398e7234c733ca24d703d0194a`. This is one selected line of history; the V2-00 task does not need to merge divergent WQ-8, supervisor and dashboard trees. Resolve branch heads dynamically before review; later V2-00 commits advance this checkpoint.
- Working tree was clean at review start. No source, tests, project documentation, operational database, or Git branch was changed by this review. Test runs use temporary/synthetic state.
- `CURRENT_STATE.md` contains conflicting historical state descriptions, old migration/test counts, and obsolete claims that the dashboard pipeline is synchronous/fixture-only. The implementation has a background worker. `ACTIVE_WORKPACKAGE.md` still identifies WQ-8 while the checkout includes subsequent supervisor and history work.

Findings below distinguish reproduced defects from source-inspected limitations. Historical test counts and real-site reports are evidence of earlier bounded runs, not a current broad reliability score.

## 3. What should be retained

| Existing asset | V2 treatment |
|---|---|
| JSONL queue import, deterministic job identity, SQLite/Alembic persistence | Preserve and add versioned UAA-side normalization; no JobHunter changes required. |
| Attempts, phase results, interventions, answer memory | Extend with consistent provenance, scope and resumable state. |
| Live browser runner, field extraction, iframe support, conditional-field handling | Consolidate and strengthen; do not replace everything with model-generated browser code. |
| Controlled submission, snapshot/hash binding, one-use claims, unknown-outcome handling | Preserve as the only final-submit path; strengthen its inputs and common eligibility gates. |
| Background workers, recovery and heartbeat mechanisms | Reuse for a single execution owner and browser session manager. |
| Typed supervisor tools, bounded retries and repair tickets | Reuse as orchestration facilities; route them through the unified executor. |
| Large synthetic regression suite | Preserve; add adversarial behavioral cases and repair shared browser fixture ownership. |
| Siemens boundary | Preserve the external adapter boundary; no copying/reimplementing Siemens internals. |

ATS adapter names should not be presented as evidence of complete live support. For example, `adapters/workday_adapter.py` explicitly describes fixture-backed behavior and limited multi-step support. Actual live navigation primarily lives in the generic browser implementation.

## 4. Findings that determine implementation priority

P1 means a correctness or reliability issue that should block broader autonomous operation. P2 means a material capability/operational gap. These are review priorities, not claims that a real application was incorrectly submitted.

### F1 — P1: deterministic mapping can assert unsupported facts [reproduced]

`form_engine/field_mapper.py:347–375` treats a skill-subject substring in profile/CV text as affirmative evidence. With synthetic evidence `No experience with Kubernetes`, the question `Do you have experience with Kubernetes?` returns `Yes`, confidence 0.85, without confirmation.

The broad `visa` → `requires_sponsorship` rule at line 84 also conflates different questions. `Do you currently hold a valid visa?` returns `No` when only `requires_sponsorship=False` is provided; visa possession is unknown.

Fix: exact typed fact IDs, polarity/negation, country and time scope, and evidence references. Reject unsupported assertions. Review broad rules such as `experience` → total years as well. A local embedding or LLM must not legitimise an answer whose source fact is absent.

### F2 — P1: reports can describe intended values rather than actual browser state [reproduced / source-inspected]

`form_engine/live_executor.py:695–720` calls `fill(value)` and returns the requested value. A loopback fixture with an input handler that clears the email produced `status=filled`, the requested email in the report, an empty actual field, and no reported validation error.

File execution records `uploaded` after `set_input_files` and a fixed delay (`live_executor.py:931` and the scalar path). This proves that files were selected, not that asynchronous ATS upload succeeded. `submission/models.py:311–341` drops field requiredness and does not retain upload status in snapshot documents. Failed/skipped fields have conservative gates, but the snapshot cannot fully express observed completion.

Fix: separate proposed, written and read-back values; verify after blur/stability; retain requiredness, constraints, source, confidence and validation. Distinguish asynchronous uploads from native file inputs sent only at final submission. Track selection_verified, uploading, remote_accepted, rejected and unknown, with observed evidence. A native input can be document-ready before remote acceptance when its exact file selection and constraints are verified; an asynchronous uploader requires its acceptance evidence. Never call file selection an accepted upload. Verify required documents according to the flow's declared upload contract, not one universal receipt rule.

### F3 — P1: the submit interlock does not establish a universal no-network-submission guarantee [reproduced]

`browser/submit_interlock.py` intercepts form events and form APIs. A synthetic loopback `Continue` button issuing a direct `fetch('/application-submit', {method:'POST'})` reached the server while every interlock counter remained zero. No real ATS endpoint was used.

`browser/live_models.py` already explicitly reports `network_submission_detector='not_instrumented'`. The defect is the gap between this limited mechanism and stronger “submission impossible”/zero-counter proof claims elsewhere. This probe demonstrates the boundary; it does not prove any real ATS was submitted by UAA.

Fix: central action authorization; capability-specific request monitoring/blocking for known ATS submit endpoints; browser request evidence; fail closed on unverified mutation flows. Distinguish document upload, autosave and next-step requests from final application creation. A blanket block of POST requests would break legitimate forms, and generic endpoint classification cannot offer an absolute guarantee. Document supported guarantees honestly.

Interim V2-02 safety slice: `LiveBrowserRunner.run` and its synthetic-mutation
path block every routed HTTP method except GET, HEAD and OPTIONS before
navigation. This intentionally pauses flows that need POST/PUT/PATCH/DELETE
for uploads, autosave or intermediate steps; no flow-specific exception is
enabled in this slice. The report labels coverage as Playwright context HTTP
routes. WebSocket frames, GET endpoints with server-side effects, and dormant
service-worker registrations in caller-owned contexts remain outside what this
guard can prove. The review-only `SubmissionExecutionService` observation/fill
path is reachable through the API observe endpoint and supervisor
prepare/retry actions, and is not covered yet; it remains a P1 for the next
shared-executor slice. The ordinary pipeline worker's `LiveBrowserRunner` path
is covered. This slice narrows the known page-request gap but does not complete
the capability-specific or universal no-side-effect contract above.

If Playwright cannot continue an allowed request or abort a denied request,
the remote outcome is uncertain.
The run reports `needs_user_input` with
`http_request_outcome_unknown_reconciliation_required` and requires an
operator reconciliation before retry. The pipeline stores the job as
`NEEDS_USER_INPUT` with an intervention, outside automatic queue eligibility.

### F4 — P1: manual submission markers are not checked consistently [reproduced]

`persistence/job_repository.py:227–260` stores `dashboard_submitted` separately from canonical status. Pipeline selection checks it; `submission/coordinator.py:150–319` does not. With a synthetic review-ready job, valid snapshot approval, and manual marker set true, `check_gates` returned `GateResult(allowed=True)`. This was a gate-only check; no submit browser execution occurred. Supervisor code also lacks the marker check.

Fix: one eligibility/duplicate service used by import scheduling, supervisor, retry, prepare and final submission. Retain the distinction between owner-reported and ATS-confirmed submission, but both block repeat applications. Clearing a manual marker should be an explicit audited correction. Re-check eligibility under the claim/lease immediately before mutation and final submit.

### F5 — P1: execution paths disagree about completion [source-inspected]

`SubmissionExecutionService.observe_and_persist_snapshot` stops discovery at the first application form, calls `execute_live_form` once, and builds a snapshot (`submission/execution_service.py:332–460`). Supervisor preparation uses this method (`supervisor/tools.py:185–217`). It does not share the runner's complete multi-page loop.

The supervisor can mark review-ready when there are no unresolved fields/interventions without requiring an actual final submit control (`supervisor/service.py:434–466`). Separately, `browser/live_runner.py:339–342` calls a filled form with no safe next action and no submit control `review_ready`.

Fix: one executor and one readiness predicate. Review-ready requires verified job identity, current complete form state, all required answers, document readiness under the flow's upload contract, no blockers/errors, and an unambiguous final submission boundary. A one-page form may itself be the final boundary; a separate review screen is not required. An intermediate page is `in_progress` or `blocked`, never implicitly ready. Propagate structured navigation/fill failure reasons instead of returning `None` and collapsing them into a generic preparation failure.

### F6 — P1: valid same-URL multi-step flows are detected as loops [reproduced]

`browser/live_runner.py:354–359` fingerprints an action using URL, selector and text. In a synthetic three-step form, the first Continue moved from First name to Last name. The second Continue had the same URL/selector/text, so UAA stopped with `navigation_loop_detected` despite genuine progress; City/final review was never reached.

Fix: include observed step/schema identity and progress in loop detection; distinguish a repeated action from a repeated state. Add bounds on steps, elapsed time and repeated unchanged observations. Replace the single conditional re-observation pass (`live_executor.py:1077`) with a bounded convergence loop that can handle nested reveals and rerenders.

### F7 — P2: existing answer intelligence is not consistently wired [source-inspected]

CLI live dry-run constructs the QA service. The background pipeline explicitly passes `qa_service=None` (`services/pipeline_worker_runner.py:369`); submission observation uses deterministic filling; the supervisor has another planner/configuration surface. Enabling an LLM therefore does not have consistent effects across the product.

Fix: one answer-resolution service, one provider interface, one configuration/preflight report and one per-job budget. All entry points call the same service. Separate decision-making from browser control.

### F8 — P2: the canonical candidate profile is too small [source-inspected]

`core/models.py:592` still defines a minimal Phase-4 profile. It has no structured education/employment histories, address components, language levels, availability, graduation dates or scoped consent policies. `candidate_profile_loader.py` filters per-job metadata to a small allowlist and returns that snapshot without merging omitted facts from an approved baseline.

Fix: a UAA-owned, versioned candidate record plus per-job overrides, with explicit precedence/conflict rules. Parse CV data as suggestions for owner validation; do not promote generated CV prose or a job requirement into a verified candidate fact. Unknown differs from false. Dates/availability need validity periods.

### F9 — P2: authentication handoff is a partial feature [source-inspected]

Attachable browser support was partial: the CLI selected an existing page, but the runner's fresh-context request guard refuses contexts that already have pages. V2-02 now rejects `browser-session --attachable` and `live-dry-run --browser-session-file` / `--cdp-endpoint` before launching or connecting and directs the owner to the standard UAA-launched browser. This is an intentional temporary compatibility break so the guard is not silently weakened. The attached mode stays unavailable until V2-04 can verify session/tab identity, ownership and safe replay. A DATEV-specific warning also remains in generic CLI code.

Fix: session identity, tab/frame binding, application identity checks, explicit pause/resume and ownership. Reuse an authenticated context and the correct verified tab when available. After browser loss, replay only safe, verified steps; do not silently clear/restart a draft or replay final submit.

### F10 — P2: answer memory and document lineage need scope [source-inspected]

Answer memory is keyed by normalized question text. Identical wording may have different employers, countries, consent scopes or validity periods. Supervisor document validation accepts missing `document_hashes` as a legacy case (`supervisor/service.py:194–198`); content hashes alone also cannot prove the document belongs to the intended job.

Fix: scoped memory keyed by intent, relevant employer/job/country, answer schema and expiry. Documents need job identity, kind, source, content hash and owner-approved provenance. Accept legacy queues through compatibility normalization, but mark provenance unknown rather than claiming it verified. Never infer document identity only from filename similarity.

### F11 — P2: observability and data handling do not match an unattended product [source-inspected]

`candidate_profile_loader.py:92,191` logs candidate names/emails; `interventions/answer_memory.py:99,112,184` logs answer values. Dashboard logs are a ring buffer; repository search found `add_log_entry` defined but no production callers. The live worker mainly stores a job status and a generic UNKNOWN_PAGE intervention, losing useful field-level recovery detail.

Fix: durable structured events, reason codes, evidence links, field-level interventions and default redaction. Keep sensitive browser evidence local with retention/deletion controls. Redact the exact outbound evidence sent to API models and record which fact IDs were sent. Add database/evidence backup and restore checks. Keep all operational mutations inside store/repository methods.

### F12 — P2: branch, documentation and regression health need a fresh baseline [observed]

The active documents lag behind the checkout. The checkout contains a local-only dashboard-history commit. Formatting checks fail on `api/routes/queue.py` and `cli.py`. A combined browser test run has lifecycle failures that disappear when the consent tests run alone; see validation below.

Fix: preserve local work, select/review the intended release baseline, update authoritative state docs, and restore a single-session green test gate before feature expansion. Do not reinterpret historical green counts as current acceptance.

## 5. V2 architecture and contracts

```text
Existing JobHunter JSONL + prepared documents (read only)
    → UAA compatibility import + local profile/document preflight
    → durable application scheduler / per-application lease
    → ONE application executor
        → session and tab manager
        → observe current page + identify step + extract schema
        → answer resolver: deterministic → local model → API LLM
        → policy/type/evidence validation
        → execute allowed actions → read back + verify → checkpoint
    → needs owner / blocked / verified review-ready
    → exact-snapshot approval
    → existing submission coordinator → confirmed / unknown outcome
```

The dashboard, CLI, supervisor and optional MCP facade invoke the same services. The model never receives a raw browser/evaluate tool, arbitrary filesystem paths, or submit authority. Model choices reference observed field/action IDs; the executor rejects stale, invisible, unknown or disallowed targets. Page text is untrusted data, including instructions embedded in labels, help text or job descriptions.

Refactor incrementally: introduce the common executor behind an opt-in V2 mode, compare old/new implementations on controlled fixtures, and move one entry point at a time. Correctness gates must apply to both modes before enabling either for live execution; the legacy switch cannot restore a known unsafe behavior. Preserve intended behavior, not known bugs, and retire duplicate paths after migration tests pass. Do not shadow-run two mutating executors against one live application. Keep schema changes additive initially and explicitly reject incompatible older binaries. Restoring an older backup cannot recover lost later history by itself: enter recovery mode, revoke restored approvals, and block external mutation/submission until the history gap is reconciled (section 17).

Extend existing contracts rather than introduce competing job/attempt histories:

- `CandidateProfile`: typed facts, repeating education/work records, provenance, verification and effective dates; owner policy stored separately.
- `FormField`: semantic intent, type, constraints, option IDs, requiredness, frame and repeat-group/step identity.
- `FieldMapping`: source fact IDs, proposed value, resolver tier/version, evidence, calibrated confidence, confirmation requirement and reason.
- `ApplicationAttempt` / `PageObservation`: browser session/tab/step, schema fingerprint, progress, retry budget and durable phase result.
- `SubmissionSnapshot`: read-back values, document-selection/remote-acceptance evidence under the upload contract, full workflow completeness, application identity, validation results and approval-binding hashes.
- `AnswerMemory`: explicit scope, source, confirmation time, expiry and revision.

JobHunter remains the search/evaluation/tailoring owner. UAA imports its current queue unchanged. New metadata is created locally by UAA; missing upstream information is a preflight issue, not a reason to modify JobHunter during this work.

## 6. Resolver design and Laya evaluation

| Tier | Responsibility | Acceptance rule |
|---|---|---|
| Deterministic | Exact labels/aliases, verified typed facts, exact option mapping and scoped approved answers | Correct intent/type/scope; evidence exists; no contradiction. |
| Local semantic retrieval | Retrieve a few likely intents/fact keys from labels, nearby context and section | Similarity only proposes candidates; include a no-match result. |
| Local classifier/reranker | Select among candidates or classify a bounded page/field state; a BERT-family model or Laya is a candidate | Evaluate on held-out employer/layout data; calibrated abstention; independent policy checks. |
| API LLM | Resolve remaining ambiguity or draft text from approved facts/documents | Structured result, source fact IDs, type/option checks, no fabricated facts, explicit unresolved output. |
| Owner | Supply unknown facts; resolve authentication or policy choices | Save reusable decisions only with the correct scope. |

Embeddings, classifiers and generative models solve different problems. Do not add all of them to every field. Cache stable intent mappings; batch unresolved fields; use a single small local runtime initially. API fallback must have timeout, quota, cost and retry limits. LLM unavailability should yield a clear partial result and preserve progress.

Laya is a plausible local classification/choice component. The author's model card describes non-generative typed decisions, with an English model and a multilingual variant. It also reports weak base zero-shot performance on its typed-decision benchmark, overconfidence, and the need for domain tuning/calibration. Those are author-reported results, not UAA measurements. A non-generative model can still select the wrong answer. Source: [official Laya model card](https://huggingface.co/convaiinnovations/laya).

Evaluate Laya in shadow mode against a compact multilingual embedding baseline and a classifier/reranker. Use German and English questions, negation, current/future sponsorship, option inversions, same labels in different sections, and unknown facts. Split by employer/form family to avoid memorizing templates. Measure accepted-decision precision, abstention/coverage, calibration, API fallback rate, cold/warm latency and actual RAM/VRAM on the user's Windows machine. No model was downloaded or benchmarked during this review. Select the smallest model that meets the agreed quality gate; Laya is optional, not a prerequisite for repairing the executor.

## 7. Ordered implementation workpackages

The table below is a component backlog, not execution order or authorization to start every item. The four acceptance checkpoints in section 18 control delivery order. Inspect actual inputs and select the first flow before broad implementation; preserve existing branches, use the assigned integration branch and do not push or merge directly to main.

| WP | Scope and implementation | Completion criteria | Depends on |
|---|---|---|---|
| V2-00: baseline and execution contracts | Preserve/publish the existing dashboard checkpoint through the normal process; reconcile main, WQ-8 and supervisor changes; fix formatting and any narrowly attributable browser test-lifecycle defect; promote this plan into the repository; specify the state/ownership, re-import, upload, mutation and approval contracts in section 17. | One explicitly chosen, remotely preserved baseline; documented migration head; combined non-live gate result recorded; reviewed contracts and a documented first-flow decision gate; no user artifacts committed. Select the live form family only when an owner-approved current queue target is available. | None |
| V2-01: immediate correctness blockers | Fix unsupported fact mappings, missing read-back, destructive metadata re-import and inconsistent duplicate gates; propagate requiredness and honest document/request evidence. Disable unverified mutation flows rather than claim universal request blocking is solved. | Negative skill evidence and visa ambiguity abstain; cleared values fail read-back; rejected uploads are not accepted; manual-submitted state survives import and blocks execution through all paths; readiness follows native/asynchronous upload contracts. | V2-00 contracts; no dependency on a new local model |
| V2-02: shared preparation core | Continue the bounded shared preparation/readiness path behind thin CLI, dashboard-worker and supervisor entry points; retain one source of readiness decisions and structured progress. Do not create a parallel engine or framework. Controlled multi-step submit replay is a later, separately accepted slice. | The selected entry points agree on preparation state; same-URL and nested-step progress is preserved; production app/worker E2E reaches an honest review boundary without clicking final submit. Existing approved-submit safety regressions remain green. | V2-01 |
| V2-03: candidate and documents | Extend candidate facts, owner policies, document manifest and scoped answer memory; UAA-only queue compatibility layer and preflight. | Common address/study/language/availability fields resolve without repeated questions; unknown/false/conflict/expiry distinguished; wrong-job, stale and missing documents block; legacy queue fixtures still import. | V2-01; integrates with V2-02 |
| V2-04: durable browser resume | Session/tab manager, single application lease, checkpoints, login/CAPTCHA handoff, restart recovery and safe replay. | Owner login resumes correct application/tab; expired session is detected; crash/restart preserves state; concurrent entry points cannot operate one job; no automatic retry after unknown submission outcome. | V2-02 |
| V2-05: hybrid resolver | Common resolver/provider API; deterministic then local semantic tier then API LLM; Laya benchmark, provenance, abstention, budgets and caching. | Held-out German/English evaluation meets precision/coverage gates; zero unsupported sensitive assertions in acceptance cases; identical wiring through CLI/dashboard/supervisor; outages degrade safely. | V2-02, V2-03 |
| V2-06: supported ATS flows | Discover the actual owner-selected first application flow early from a completed JobHunter export, then qualify only its concrete page/variant capabilities as the shared preparation core supports them. Unknown mutation patterns stay default-deny. | A per-flow capability/evidence record states what was observed and allowed; unknown patterns hand off safely. Other families are added only against demonstrated demand. | V2-02; additional dependencies only for capabilities actually required |
| V2-07A: dashboard interaction design | Define and prototype the main user journeys, information hierarchy, truthful state labels, live attention queue and correction flow before backend contracts are finalized. | User can identify what is happening, why a job stopped and the correct next action in the prototype; event/action contracts feed V2-02–V2-04. | Starts with V2-00; no dependency on broad ATS coverage |
| V2-07B: operator dashboard implementation | Candidate/document readiness, queue run controls, live per-job updates, grouped owner decisions, open/resume browser, correction saving, verified review diff, durable events and evidence, manual-submitted audit. | User can import → prepare → resolve → resume → review without shell commands or external chat; UI checked at 1440×900 and 390×844; no ambiguous completed/submitted badges; section 12 acceptance checks pass. | V2-07A; incrementally integrated with V2-02–V2-04 and later resolver/ATS work |
| V2-08: release qualification | Qualify the declared intake, first-flow preparation, correction/reuse and operator journeys on the production UAA app/worker path. Assess upgrade/restore and operational limits when release scope requires them. Qualify controlled submission separately under its existing authorization contract. | A capability-by-capability acceptance record separates supported, unsupported and unverified behavior. Preparation acceptance does not consume WQ-8 authorization; no unverified final-submit capability is advertised. | Completed capabilities in the declared release; no optional model or broad ATS work is a prerequisite |

The first operator surface needs truthful status, reason, next action and review state, with accessible keyboard and mobile behavior. Build that slice alongside the capability and use existing polling if it meets the local update target. The broader dashboard specification and visual polish remain backlog; push/replay infrastructure, bulk parallel browsing, additional adapter names, new agent frameworks and model fine-tuning do not gate the first supported flow.

V2-08 is not permission to submit during this review or to finish WQ-8 automatically. The existing WQ-8 single-application authorization requirements remain in force until explicitly superseded by the owner.

Delivery cuts are outcome-based: (1) intake evidence from a completed export; (2) one supported preparation flow through the production app/worker with honest review readiness; (3) scoped correction and reuse with durable recovery; (4) release qualification for each declared capability. Optional models, broad ATS coverage, generalized recovery, parallel preparation and controlled-submission proof remain separate additions and cannot silently block useful preparation.

## 8. Measurable acceptance and rollout

These are proposed release targets, not achieved results:

- Start a labeled benchmark with 500 field decisions and 50 complete synthetic/replay flows as dataset-construction targets, not sufficient proof by themselves. Partition training, calibration and held-out evaluation by employer/layout; do not tune against the held-out set. Expand samples based on uncertainty and supported categories.
- For low-risk model-accepted field mappings, initially target at least 99% precision; publish accepted count, coverage, per-category errors, confidence intervals and whole-application correctness. This percentage alone is not a release guarantee: errors compound over many fields and correlated templates reduce effective sample size. Any observed unsupported sensitive assertion blocks promotion. Define a useful coverage floor from baseline data before model promotion; unsupported local/model tasks abstain.
- Require all deterministic release-regression fixtures to pass. An initial 90% review-ready target applies only to a separately labeled exploratory/held-out coverage study, not permission for 10% of regression tests to fail. Report all selected jobs as well as the eligible-form denominator, with every exclusion visible.
- Never label a job review-ready without the readiness predicate. Never label submitted from a generic thank-you phrase or an attempted click alone. Distinguish owner-reported, ATS-confirmed and unknown results.
- Exercise crash at navigation, upload, next-step and post-click boundaries; profile/document updates; stale approvals; duplicate source URLs; concurrent dashboard/CLI attempts; model timeout; API quota exhaustion; malicious page instructions and hidden fields.
- The real pilot starts with owner-authorized preparation on a small supported batch, then increases only after measured results. Preparation can itself disclose data or save drafts, so live mutation is a separate authorized test, not part of this review's synthetic checks.
- Release checks: lint, format, types, complete non-live suite, combined browser suite, representative UI inspection, fresh/upgrade migration, backup/restore and sanitized evidence. Run actual production services, not only injected mocks.

Track review-ready rate, field correction rate, interventions per application, repeated interventions, document acceptance failures, resume success, duplicate attempts prevented, API calls/cost per job, elapsed time and unknown submission outcomes. Use these measurements to decide the next ATS or mapping improvement.

## 9. Lessons from AutoApplierAgent

Its workflow documents emphasize exact job/document identity, checking reused Workday attachments, completing multiple pages, holding authenticated tabs, scoped approved answers and a strict final review checklist. Those behaviors should become UAA contracts and tests.

Its small-model report also shows that using MCP does not remove authentication and CAPTCHA blockers. A report based on a visible CAPTCHA badge alone is not enough to prove an active challenge blocks navigation; the browser should distinguish passive anti-bot notices from actual required interaction. Do not copy a report's interpretation into a production rule without a fixture or observed behavior.

Keep the useful operational learning. Do not import its real profile values or a second YAML application history automatically. A future owner-reviewed history import should reconcile duplicate identities and preserve submission provenance.

## 10. Validation performed in this review

Final results below refer to the reviewed local commit. No real ATS traffic, real JobHunter execution or application submission was performed. Orchestration regression tests use UAA's fake JobHunter fixtures.

- Source review across queue, persistence, browser, mapping, interventions, supervisor, submission, API and operational documents.
- `git fetch origin`: completed; branch preservation mismatch described above.
- `ruff check src tests migrations`: passed.
- `ruff format --check src tests migrations`: failed; 2 files would reformat (`api/routes/queue.py`, `cli.py`), 236 already formatted.
- `pyright`: 0 errors, 0 warnings.
- Full `pytest -m "not live and not playwright" -q`: **1463 passed, 295 deselected in 750.51 seconds**.
- Combined selected browser tests (`test_wq8_interlock.py`, `test_live_browser_executor.py`, `test_consent_banner.py`): **17 passed, 7 failed**. All failures were consent tests raising the Playwright sync-API-inside-asyncio-loop error.
- Consent-only retry: **7 passed in 14.06 seconds**. This supports an order/lifecycle interference diagnosis for the combined run; it does not make the combined gate green.
- Six synthetic review probes reproduced: negated skill → Yes; visa possession inferred from sponsorship; filled-value report disagrees with DOM; fetch request passes form-event interlock; legitimate repeated Continue blocked as loop; manual-submitted marker does not reject the submission gate.
- Probe source: `uaa_review_probes.py` alongside this document. Uses only synthetic values, loopback HTTP and temporary SQLite/browser artifacts. It prints diagnostic observations; it is not a substitute for repository regression tests.
- `git diff --check`: clean; final source working-tree status remained clean.
- Not performed: full browser suite, live ATS pilot, interactive dashboard acceptance, model benchmark, install/upgrade, source fixes, release/merge. This is a review and implementation plan.

## 11. Concrete first implementation slice

After baseline and contract reconciliation, turn the six diagnostic probes into repository regression tests and add re-import ownership protection. Fix the immediate correctness defects first. The next slice is one anonymous three-step fixture through the unified executor, exposed through the dashboard and shared by CLI/supervisor, with truthful review readiness and stale-approval rejection. Authenticated pause/restart is a subsequent V2-04 slice; it is not a hidden requirement of the first fixture milestone.

This establishes the foundation on which local embeddings, BERT/Laya and API LLM fallback can improve coverage without amplifying incorrect state or answers.

## 12. Core dashboard, intervention and learning requirements

This is a product backlog specification, not a first-flow delivery gate.
Section 18 defines the smaller required operator slice for the first supported
flow; broader dashboard journeys can follow measured needs.

### Product goal: clear status and direct control

The dashboard should answer four questions at a glance: **What is running? What needs me? What happened? What can I do next?** Reducing the gulf of evaluation means making actual system state understandable. Reducing the gulf of execution means making the appropriate action obvious and easy to perform. Visual polish, consistent spacing/type, accessibility and reliable feedback support these goals.

Use the existing HTML/CSS/JavaScript dashboard as the starting point. A framework migration is not assumed or required by this plan. Design the interaction model before committing to a visual theme or a component rewrite.

| Surface | Default information | Primary control |
|---|---|---|
| Overview | Active jobs, Needs your input, Ready for review, completed outcomes; current run and connection state | Start preparation / Pause run, with exact scope stated |
| Applications | Company/role, current stage, plain-language status, latest meaningful event and next action | A contextual action for that job |
| Needs your input | Original question or failure, reason, affected job(s), suggested supported answer and recovery choices | Save & resume / Open browser / Retry, as appropriate |
| Job detail | Stage timeline, current question/page, verified documents, answer sources, outcome evidence | Resume, review or open the bound browser tab |
| Profile & answers | Saved facts, preferences, scoped policies, documents, source, last confirmation and expiry | Edit, correct, narrow scope or remove a saved answer |

These are an information hierarchy, not five required top-level navigation tabs. Keep navigation compact; put raw logs, selectors, model diagnostics and trace downloads behind an expandable technical-details area. Prefer meaningful stage names to unexplained percentages. Review-ready, owner-reported submitted, ATS-confirmed submitted and outcome unknown must remain visually and semantically distinct.

### Live visibility at every stage

Every phase emits a durable structured event: application/attempt ID, ordered event ID, stage, state, timestamp, reason, evidence reference and allowed next actions. The UI consumes updates while jobs run, including preflight, navigation, mapping, upload, validation, review and approved submission. One failed application must appear in Needs your input immediately without hiding progress elsewhere.

Use ordered durable events with reconnect/replay and a current-state refresh after gaps. Commit state and its event in one DB transaction; delivery is repeatable and the UI deduplicates by event ID. Start with the existing shared polling approach if it meets the two-second 95th-percentile local update target; a push transport is an optimization, not a new release dependency. Show disconnected/stale state and last successful update instead of implying activity is current.

Preserve the user's focused field, draft answer, scroll position and selected job during background updates. Use quiet row/status changes for routine progress; announce actionable errors accessibly. Avoid a toast for every field or forcibly reopening drawers. Group repeated issues only when their semantic meaning and required scope truly match.

### Recover in place

| Failure type | What the user sees | Recovery |
|---|---|---|
| Missing candidate fact | Exact question, why no answer exists, typed input with inline validation | Enter the fact, Save & resume |
| Document rejected/missing | Which document, rejection reason and accepted format/size where observed | Select/replace the correct document, verify selection or remote acceptance as required by that uploader, resume |
| Login / active CAPTCHA / browser interaction | Which job and browser step needs attention | Open the exact application tab, take control, then Resume & verify |
| Site/connection problem | Last successful step, failure reason, whether retry is safe | Retry from checkpoint, pause or skip |
| Unsupported widget / software defect | A plain explanation and available manual workaround | Take control, capture a repair ticket, or skip; do not request an irrelevant candidate fact |
| Uncertain submission outcome | What was attempted and what evidence is missing | Reconcile outcome; ordinary retry remains unavailable |

Buttons reflect backend capabilities. A disabled action explains the reason. Save/resume shows separate saving, saved, resuming and verified/blocked states. Persist the correction before scheduling a single idempotent resume; if saving fails, retain the draft and do not resume. A manual browser edit must be freshly observed before it is considered resolved. Final approval remains separate from Save & resume.

### Automatic persistence with appropriate reuse

Every user-supplied correction is saved durably for its current application. When its semantics are known, also save it automatically to the appropriate reusable record, without requiring the user to re-enter it in another settings screen. Show a compact destination/scope summary before saving, with an easy override:

| Information | Default destination and reuse |
|---|---|
| Verified stable fact, e.g. postal code or language level | Versioned candidate fact; reuse on equivalent future questions |
| Availability, salary preference or location preference | Owner preference with units, geographic/job scope and effective/expiry dates |
| Employer-specific answer or motivation | This application/employer scope, never silently global |
| Consent or sensitive declaration | Current application unless an explicit owner policy covers equivalent future wording and scope |
| Document selection | Job-bound document manifest; reusable document only when its kind and relevance allow it |
| Browser/login intervention or site defect | Attempt/recovery event, not an answer-memory entry |

Example: entering a missing postal code displays “Saved to your profile · used for future address questions,” with a “This application only” option. A motivation answer displays “Saved for this application.” If the system cannot determine intent/scope confidently, save for this job and offer the reuse choice; it must not guess a global policy.

Persist source=user, original question, canonical intent/fact ID, answer type, value, scope, timestamp, profile/policy revision and applicable expiry. Exact semantic matching includes polarity, country, time and options. Never automatically promote a model suggestion into a user-confirmed fact without the user's actual save action.

When an answer conflicts with an existing fact, show the conflict instead of silently overwriting it. All answers remain editable/deletable through the UI. Version running attempts against the facts they used: a profile correction should identify affected applications, invalidate relevant review approvals, and revalidate affected fields and documents before resuming. A corrected fact does not automatically update an already-tailored CV/cover letter; document inconsistency blocks affected applications until the owner supplies a corrected bundle or a later JobHunter export. UAA does not silently re-tailor documents. Submitted history remains historical evidence. Do not broadcast a new value into every active form while the user is reviewing it.

### User control and acceptance criteria

- Provide run-level and per-job pause/resume/skip controls, with a clear distinction between “stop starting new jobs” and “pause active jobs at the next safe checkpoint.” Manual takeover pauses automation for that job before transferring control. A queued cancellation cannot undo a request already sent; display the truthful outcome.
- Use one clear primary action in the current context. Require additional confirmation for consequential actions such as final submission, not every reversible field correction.
- A new user should be able to identify the blocked job, its cause and next action within 10 seconds in a usability test. The owner should complete enter missing fact → save → resume without a terminal or assistance.
- Tests must prove that a saved stable fact answers an equivalent future question, does not leak into a different-scope question, survives restart, can be corrected, and invalidates affected stale approvals.
- Validate keyboard-only operation, visible focus, text/icon status cues in addition to color, readable contrast, 200% zoom, reduced motion, inline error announcements, and responsive layouts at 1440×900 and 390×844.
- Test loading, empty, disconnected, stale, failed-save, retrying, partial-success and concurrent-update states. No loss of unsaved input or unexpected focus/scroll jumps.
- Use consistent design tokens, typography, spacing, controls and status vocabulary. Assess quality through task completion and confusion/error rates as well as visual review; “perfect” is a quality ambition, not an untestable release claim.

## 13. Deferred V2.x: parallel application preparation

Parallel applications are explicitly optional for the first V2 release. Keep
stable job/attempt identifiers and single-owner state so later isolation is
possible, but do not build a worker pool, lease framework or generalized
parallel scheduler now. Launch with one active application.

**Proposed V2-09 workpackage:** a bounded worker pool with owner-configurable concurrency and conservative per-employer/site limits. Each job has an independent attempt, lease, browser/tab binding, document bundle, progress and intervention state. A blocked job yields execution capacity so unrelated eligible jobs continue; paused browser retention and eviction use an explicit resource budget.

Do not launch multiple writers against one persistent browser profile. The session manager must provide exclusive ownership or a validated isolation strategy; credential/session sharing never implies shared form state. Queue fairness, cancellation, memory limits, API/model rate limits and per-site backoff are part of this workpackage.

The UI shows concurrent activity through the same job rows, timelines and Needs your input queue already implemented for one job. Resolving one blocker resumes only the relevant application(s), after scope and state checks. A run failure, job failure, browser failure and disconnected dashboard are different states.

Keep final submissions serialized initially and separately authorized per current snapshot. Increasing preparation concurrency grants no additional submission authority. Per-application eligibility and duplicate checks remain atomic across workers and all entry points.

Acceptance example: three synthetic jobs run together; one awaits login, one needs a missing profile fact, and one reaches review-ready. The dashboard reflects all three promptly. The user supplies the missing fact once, the appropriate job resumes, a later equivalent question reuses it, and the login job remains paused. Tests also prove no cross-job document/answer/tab leakage, no duplicate execution, controlled shared-session access, stable editing during events, and correct recovery after one worker crashes.

Dependency: unified executor, scoped knowledge, durable session/lease recovery and core dashboard must be accepted first. Parallelism is not required to complete V2-08.

## 14. Concrete verification plan

Testing protects each delivered behavior. Freeze acceptance contracts and
inventory fixtures and test commands before broad test expansion. The latest
V2-02 progress-fingerprint checkpoint recorded 1,854 passed and 3 deselected in
the combined non-live gate; it is historical evidence for that exact tree, not
a result for later code.

### Test layers and when they run

| Layer | Concrete coverage | Execution / gate |
|---|---|---|
| Unit | Field intent, negation, typed facts, option mapping, scoped answer reuse, readiness predicate, progress detection, eligibility and provider routing | Every code PR; deterministic, no external services |
| Contract | Existing JobHunter JSONL, identity algorithm, document paths, legacy/versioned normalization, UAA API/event shapes, provider request/response normalization | Every code PR; compatible old fixtures remain supported |
| Persistence/integration | Import → store → resolver → interventions → snapshot; atomic save/resume, concurrent updates, document revisions, leases, migration and backup/restore | Every code PR; real temporary DB and services |
| Browser component | Native/custom controls, repeaters, iframes, upload acceptance/rejection, conditional fields, consent overlays, login handoff, same-URL steps | Combined non-live browser gate; controlled local HTTP fixtures |
| Production E2E | Real app startup, import API/UI, subprocess worker, browser, owner correction, DB persistence, live dashboard updates and review | At the intake, preparation, correction/reuse and release acceptance checkpoints; no mock app/worker/store/executor on the critical path; fixture server confirms zero final-application requests during preparation |
| Regression | Previously supported flows, reproduced defects, duplicate protection, controlled-submit gates, Siemens adapter boundary, old queue formats and dashboard journeys | Focused affected checks per iteration; one combined full non-live gate at the accepted integration checkpoint before merge |
| UI/accessibility | Keyboard navigation, focus, inline errors, live updates while typing, disconnected states, responsive layout, visual comparison and task completion | Every UI/browser milestone; automated checks plus required user-perspective browser inspection |
| Model evaluation | Held-out German/English field decisions, unfamiliar wording, negation, factual grounding, abstention, injection, local/API routing and provider comparisons | Before changing model, prompt, retrieval index or decision thresholds; fixed labeled dataset, report precision AND coverage |
| Fault/recovery | Kill worker/browser, restart API, interrupt upload/save, DB write failure, lost event connection, API timeout/rate limit, uncertain post-click result | Relevant PRs and release gate; deterministic injection at named checkpoints |
| Performance/soak | Many queued synthetic jobs, stable resource usage, no lost events, UI latency, worker/session leaks and later bounded concurrency | Release candidate; compare against recorded baseline and agreed budgets |
| Live provider smoke | Authentication, configured model availability, normalized structured response, usage/error reporting | Opt-in after provider configuration changes; synthetic prompts, small budget, no candidate data by default |
| Live ATS checks | Public navigation, separately authorized field mutation/upload, and separately approved real submission | Explicitly opt-in; never part of default CI; see live ladder below |

Keep the repository's existing non-live/full-browser checks and Windows/Linux Python matrix. New tests should use its existing marker vocabulary; if additional markers are introduced, register them and update the scripts explicitly. Never rely on a bare command accidentally excluding live tests. Cache model artifacts separately from the fast suite; CI does not need real API keys or ATS accounts.

### Required named behavioral cases

Names below are proposed regression cases; they may be grouped into existing test modules rather than create one file each.

| ID / proposed test | Setup and required assertions | WP |
|---|---|---|
| T01 `test_negated_skill_evidence_never_maps_yes` | Profile says no Kubernetes experience; a yes/no question must not become an affirmative answer. Include positive, absent, negated and contradictory evidence. | 01, 05 |
| T02 `test_visa_possession_is_not_sponsorship` | Only sponsorship need is known; visa possession remains unknown. Repeat for current/future and country-specific questions. | 01, 03 |
| T03 `test_report_uses_dom_readback_after_site_mutation` | Site clears/normalizes an input after fill/blur; report and readiness reflect the observed value, with a useful intervention on mismatch. | 01, 02 |
| T04 `test_upload_rejection_never_becomes_accepted_document` | Delayed async upload success/rejection/timeout, wrong file and reused previous-job document; async upload requires remote acceptance. Native send-on-submit input requires correct local file selection/constraints and remains labeled selection_verified until sent. | 01, 06 |
| T05 `test_mutation_request_policy_observes_fetch_submit` | Local fixture issues fetch/XHR/native submits through different controls; known final endpoints block without authority, allowed uploads/next steps work, unknown routes follow the declared fail-closed policy; counters alone cannot claim proof. | 01 |
| T06 `test_manual_submission_blocks_every_entry_point` | Owner marks submitted; pipeline, supervisor, direct prepare/retry and final coordinator all decline a repeat. Correction/removal is audited. | 01 |
| T07 `test_same_url_three_step_flow_reaches_final_review` | Identical Continue selector/text across changing schemas advances; an unchanged-state loop stops within budget; nested reveals/rerenders are included. | 02 |
| T08 `test_intermediate_form_is_not_review_ready` | Filled first step without final review/submit boundary cannot be marked ready from CLI, dashboard or supervisor. All entry points agree. | 02 |
| T09 `test_correction_survives_restart_and_future_equivalent_question` | Save a missing fact in UI; persist before resume; restart app; next equivalent job uses it; employer-specific/expired/inverted questions do not. | 03, 07B |
| T10 `test_reimport_preserves_uaa_answers_and_submission_markers` | Import, add UAA answers/manual-submitted marker, re-import unchanged and updated export; local corrections, attempts, history and duplicate protection survive. | 01, 03 |
| T11 `test_document_change_invalidates_review_approval` | Replace document bytes at the same path or alter profile/target/form after review; old approval cannot submit; explain the changed items. | 01, 03, 08 |
| T12 `test_browser_handoff_resumes_bound_application` | Pause for login/manual edit; correct browser tab is resumed and re-observed; wrong tab/job or expired session is rejected; no draft is silently restarted. | 04 |
| T13 `test_worker_crash_recovers_without_duplicate_action` | Inject crash before/after fill, upload, step transition and fixture final click; recover known state; unknown submission stays blocked with no retry. | 04, 08 |
| T14 `test_dashboard_events_preserve_unsaved_answer` | While typing a correction, other jobs update; typed input/focus persist; disconnect/reconnect replays missing events without duplicate actions. | 07B |
| T15 `test_provider_switch_preserves_resolution_contract` | Equivalent canned responses from provider adapters normalize to the same validated answer/evidence contract; changing provider requires no executor/store changes. | 05 |
| T16 `test_provider_failures_preserve_application_progress` | 401/429/5xx, timeout, refusal, malformed output, unknown fact IDs and unsupported options become structured failures/abstentions within budget; no invented value or fallback to an unapproved provider. | 05 |
| T17 `test_import_snapshot_and_partial_rows_are_auditable` | Malformed line, missing document, changed/incomplete file, unknown schema, duplicate IDs; usable rows are reported separately and scheduling uses only a validated completed import snapshot. | 03 |
| T18 `test_parallel_jobs_do_not_share_answers_documents_or_tabs` | Three jobs diverge to login/missing fact/review-ready; resume only intended job; no duplicate claims or shared-profile contention; other jobs keep progressing. | 09, deferred |

### End-to-end acceptance journeys

1. **Ordinary application:** completed JobHunter export → UAA dashboard import → validated documents/profile → production background executor → three-step local ATS fixture → verified final snapshot. Assert UI state, DB history, document identity, actual DOM answers and that the fixture server receives zero final-application requests during preparation.
2. **Learn once:** first application lacks a known type of fact → intervention appears live → owner enters it and sees save scope → Save & resume → review-ready → second equivalent job reuses it. A third differently scoped question must still ask. Repeat after restart.
3. **Recover:** login or upload failure mid-flow → plain next action in dashboard → manual browser correction/document replacement → resume same application → revalidation → review. No repeating already-completed unsafe actions.
4. **Approved local submission:** exact snapshot approved against a local fake ATS → one coordinator-authorized submission request → confirmation/reference persisted → dashboard history updated → repeat attempt blocked. Separate stale/unknown-result variants must never silently retry.
5. **Upgrade:** old synthetic DB and queue → migration → existing terminal statuses, answers, documents and history remain usable; profile/doc changes invalidate stale approvals. Restoring an older backup enters recovery mode and blocks external actions until later submission history is reconciled from surviving evidence or owner verification; missing history is never assumed safe.

The local fixture server records actual requests and uploaded file identities. This independently checks browser reports. Tests must launch the production app/worker/factories for critical integration journeys; testing an injected success snapshot alone cannot satisfy E2E acceptance.

### Live testing ladder

| Level | Permitted scope | Evidence and stop condition |
|---|---|---|
| L0 | Entirely local synthetic ATS, candidate and documents | Mandatory before any live pilot; deterministic gate |
| L1 | Owner-selected public live pages, navigation/observation only | Verify current job identity, form reachability and blockers; no typing/upload/final submit; rate-limit and stop at active challenge/login |
| L2 | Specifically authorized live preparation with the correct candidate/documents, or an explicitly allowed sandbox | Verify actual controls, document receipts and review state. Uploads/autosave can disclose data, so authorization is distinct from L1. Do not send fake identities to arbitrary production employers. |
| L3 | One explicitly approved real submission | Follow `docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md` plus the active WQ-8 owner contract; bind approval to current job/snapshot/documents; capture outcome; stop on ambiguity and never auto-retry |

Run L1 after relevant ATS changes when authorized; L2 before declaring a supported live preparation flow; L3 before declaring live submission proven. None is automatically triggered by a PR, a model switch, or this planning request. JobHunter itself remains untouched; a future actual-export compatibility check consumes an owner-provided completed export without launching JobHunter.

### Merge and release gates

For each code iteration: focused static/unit/contract checks → affected integration/browser checks → supervisor review. At the accepted integration checkpoint/workpackage, run one combined full non-live gate before merge, followed by applicable lint/format/type and scope checks. Do not run redundant full non-browser and full browser-inclusive gates back to back. Later code edits invalidate affected evidence; final accepted code receives the applicable gate. Documentation-only edits use consistency and whitespace checks, not code suites.

At release, qualify production E2E for the declared capabilities and assess upgrade/restore, fault recovery, model/provider evaluation or soak evidence only where those capabilities are in scope. L0 is local synthetic qualification. L1 observation, L2 live preparation and L3 real submission are separate live levels, each requiring its own explicit scope authorization; authorization for one level does not authorize another. If real/live evidence is unavailable, label the capability unverified; synthetic success is not live proof. Preserve supported regressions, including the Siemens boundary, without modifying its repository.

## 15. JobHunter → UAA integration contract

### Current boundary to preserve

UAA already reads a configured absolute `UAA_QUEUE_PATH` through `QueueImportService`, then validates `application_queue.jsonl` with the existing importer. It has API/CLI/startup import mechanisms and durable import history. It also has optional process-level orchestration that can invoke JobHunter. For this work, use **consumer-only integration**: UAA reads JobHunter's completed output; it does not run or modify JobHunter, share its database, or import its internal Python modules.

```text
JobHunter: search → evaluate → tailor → completed application_queue.jsonl + files
                                             ↓ read-only handoff
UAA: capture/validate import → documents + local facts → queue → prepare → review → approved submit
                                                        ↓
                                      UAA-owned answers, history, evidence and status
```

Configure the actual queue path once; do not guess it or scan unrelated output directories. Inspect one real completed JobHunter export and its owner-selected application flow early. Keep queue/profile/document data private and ignored; commit no personal export, browser trace or screenshot. If real inputs are unavailable, use synthetic fixtures for contract and integration checks and label the actual-input acceptance as pending. This plan authorizes no live navigation, mutation, upload or submission. The dashboard's intake surface reports the source snapshot, row counts and actionable errors; import alone does not launch a browser or submit.

### Data ownership and fields

| Data | Owner / handling |
|---|---|
| Job identity, canonical source URL, company/title, ATS/source, external ID | JobHunter exports; UAA validates and retains the existing deterministic identity algorithm. Actual application-form URL is tracked separately. |
| Evaluation, verdict/score, timestamps and job description | JobHunter owns selection/evaluation; UAA checks eligibility/freshness without reimplementing the search/evaluation pipeline. |
| Tailored CV/cover paths and optional markdown sources | JobHunter produces; UAA validates existence/readability/type, captures content hashes and binds the selected bundle to the job. Current ready-to-apply contract requires both PDF paths; any relaxation must be an explicit compatibility decision. |
| Candidate snapshot in metadata | Imported as a versioned source; UAA reconciles with owner-verified facts and explicit per-job overrides according to declared precedence. Conflicts surface; export freshness alone cannot overwrite an owner correction. |
| User answers, scoped memory, interventions, browser checkpoints and submission history | UAA owns; re-import must never erase them. |

Existing exports are accepted through a legacy contract adapter. Introduce a UAA internal schema version without requiring JobHunter to change immediately. If upstream later adds an explicit version/manifest, support it through version-specific adapters and contract tests; reject unsupported incompatible formats with an actionable error. Hashes calculated by UAA prove the captured bytes, not upstream authorship or semantic job relevance.

### Reliable intake and re-import

1. Prefer completed, atomically published exports. Until the producer contract is verified, initial imports run after JobHunter has finished. Capture a stable local read of the exact file; hash the same bytes that are parsed. Detect source changes/incomplete reads and defer intake rather than scheduling partial in-flight data. A merely unchanged file size is not proof the producer finished. Atomic publication of JSONL alone does not make referenced mutable documents a consistent generation. Without an upstream generation manifest/hashes or documented immutable artifact semantics, UAA can prove only the bytes it captured; record generation provenance as unverified and require owner verification of the job/document bundle before mutation. Do not claim consumer-only code can reconstruct missing upstream provenance.
2. Persist a UAA import snapshot/run and per-row validation results. Invalid rows do not erase valid existing jobs; show partial success explicitly. No queue deletion implies deleting application history.
3. Validate artifact paths and freeze document bytes into a private, ignored UAA application bundle, with source path/hash and application identity. This prevents later JobHunter output changes from silently altering an approved upload. Missing/ambiguous provenance becomes an intervention. Never write into JobHunter's output files.
4. Upsert using canonical identity. Alias resolution for source/detail/ATS URLs must be evidence-based; never merge different requisitions merely because company/title look alike. Detect aliases before duplicate execution.
5. Keep imported metadata and UAA operational state separate. Source inspection found `upsert_application_job` currently does `existing.metadata_json = job.metadata`, so re-import can discard UAA-added metadata such as per-job answers/manual submission markers. V2 must fix this ownership boundary and cover it with T10, not merely add another import button.
6. A new document/profile/source revision does not mutate an active attempt's frozen inputs. Flag affected reviews as stale and require revalidation. Previously submitted or unknown-outcome applications remain protected; changed exports never authorize another submission.

No feedback write into JobHunter is required for V2. If future search deduplication needs UAA outcomes, define a separate explicit outcome-export contract later. It must not become shared database ownership.

Workpackage mapping: V2-01 fixes destructive re-import/duplicate-state risks; actual-export and first-flow discovery move to the earliest V2-02 intake checkpoint; V2-03 handles only compatibility, source/local ownership and document-snapshot gaps demonstrated by that evidence; V2-07B owns the needed import/status and correction surface. V2-08 records release qualification, not first discovery. Existing orchestration stays optional, not a dependency of UAA.

## 16. Changing LLM APIs without changing the application engine

### Current limitation

`llm/qa_service.py` has a useful abstract QA interface, but `create_qa_service` only branches for `mock`; every other provider setting constructs `GemmaQuestionAnsweringService`. Supervisor planning has a separate compatible-chat-endpoint implementation/configuration. Setting an arbitrary provider name today is therefore not a complete provider switch. Consolidation belongs in V2-05.

### Planned provider boundary

```text
Deterministic → local retrieval/classifier → common resolution request
                                                ↓
                          configured API provider adapter
                                                ↓
                      normalized result → shared policy/type/evidence validation
                                                ↓
                                  executor or unresolved intervention
```

The common request contains the task type, field/option IDs, selected approved evidence, required response schema and budget. The common result contains proposed answer, fact/evidence IDs, reason, confirmation requirement, provider/model/config revision, usage and a typed error/abstention. All providers pass the same validator. A provider adapter cannot execute browser actions or bypass submission gates.

Support a compatible chat protocol adapter plus explicitly implemented native API adapters as needed. A new model/endpoint using a supported protocol should be a configuration change. A genuinely different API protocol requires one new tested adapter; promising that every API is interchangeable by changing a URL would be inaccurate. Unknown provider IDs must fail configuration validation, never silently select another provider.

### Planned user workflow

In **Settings → AI providers**, select a provider/protocol, endpoint where applicable, model and credential reference; set timeout, budget and permitted fallback providers. Test connection and structured output with a synthetic example, run the grounding/abstention smoke pack, then activate the revision in suggestion-only mode. Qualified provider/model/task profiles may automatically resolve only their approved low-risk categories. Domain qualification is required for a new model to gain automatic acceptance; switching for experimentation must not require the owner to run a full benchmark before seeing suggestions.

Illustrative future configuration contract — these are proposed fields, not currently supported settings:

```yaml
resolution:
  order: [deterministic, local_semantic, api_llm]
  local_profile: multilingual_local
  api_profile: primary
providers:
  primary:
    protocol: compatible_chat
    base_url: <your-supported-provider-endpoint>
    model: <your-model-id>
    credential_ref: env:UAA_PRIMARY_API_KEY
    timeout_seconds: 30
    max_retries: 2
    fallback_profiles: []
```

Credentials stay in environment variables or the OS credential store, never in Git, normal settings exports, browser local storage, event payloads or logs. The dashboard receives masked configuration/status, not the stored secret. Native-provider settings may differ behind their adapter; normalize differences in schema support, refusals, usage reporting and rate limits.

Changing providers preserves candidate facts, approved answers, documents, application history and the deterministic/local tiers. Pin the provider/model/prompt/config revision for an in-flight resolution; apply changes at a safe boundary rather than midway through a request. Cache keys include task, evidence/profile version, model/provider and prompt/schema revision. Already approved snapshots are not silently rewritten by a provider switch; any later answer/content change requires revalidation and invalidates the relevant approval.

Fallback is only to providers explicitly configured by the owner, under a total timeout/cost budget. Authentication failures, unsupported models, malformed outputs and semantic uncertainty are distinct results; retries do not blindly treat every failure as transient. Local/API unavailability preserves application progress and presents a clear intervention. Switching provider never grants permission to send a broader set of candidate data.

Acceptance: T15/T16 and provider contract tests run for every adapter; a live synthetic smoke test validates configured availability when authorized; the held-out benchmark measures answer correctness/abstention before automatic use. CLI, dashboard and supervisor must display and use the same active provider configuration.

## 17. Self-audit corrections: contracts needed before implementation

This section closes omissions from the earlier plan. It defines proposed implementation requirements, not evidence that these guarantees already exist. Cross-cutting contract choices are written and reviewed in V2-00; implementation follows the workpackage indicated below. Any departure from the repository's technical baseline or approval policy needs the appropriate explicit architecture/owner decision.

### A. Scope and delivery cuts

The initial plan accumulated too much scope in one release and hid dependencies inside broad workpackages. Use the four outcome checkpoints in section 18 rather than treating the following historical increments as a serial scope bundle.

1. **Intake and discovery:** inspect an actual completed export and owner-selected first flow early; do not authorize live action through this plan. Synthetic evidence remains provisional if real inputs are unavailable.
2. **Preparation:** complete the bounded shared preparation core and one production app/worker E2E through a verified review boundary, with single-owner and default-deny safety.
3. **Correction and reuse:** atomically save provenance, resolve the targeted intervention, invalidate only affected approvals and enqueue one scoped resume; reuse known facts only at the right scope.
4. **Capability release:** report the supported/unsupported/unverified matrix and qualify declared capabilities. Controlled submission, optional models, broad ATS coverage and parallel execution remain separate work.

Independent product readiness labels are required: preparation supported, controlled submission supported, and unsupported/unverified. Do not block all preparation value on a WQ-8 real-submit authorization; do not advertise final submission without its proof. No unattended policy-based final submit is included in these increments. That remains an explicit unresolved product-scope decision relative to the original ambition.

The plan is already inside UAA durable documentation. The prior V2-02 active handoff is archived byte-for-byte before the active file describes this documentation revision. Preserve the divergent checkpoint/project-rebaseline branch; this revision uses the documented codex/v2-delivery-plan exception from the latest integrated V2-02 checkpoint. WQ-8 submission authority remains in its archived handoff and controlled-submission plan.

### B. Canonical state, atomic commands and event ownership (V2-00/02/04/07B)

- `ApplicationJob` owns the canonical lifecycle and duplicate eligibility. `ApplicationAttempt` owns execution progress and checkpoints; run rows aggregate scheduling. Supervisor state is a projection/orchestration concern and cannot independently declare a conflicting job outcome.
- Retain existing public status names initially. Specify a legal transition table that covers pause, cancel/skip, stale review, manual submission and unknown outcome through appropriate attempt state/reason fields. A UI button must map to an allowed repository command; no direct row edits or a second status authority.
- Commands carry application/attempt identity, expected state revision and an idempotency key. Reject stale UI actions with the fresh state; retrying the same Save & resume request cannot create two retries or store two conflicting answers.
- Save correction, record provenance, resolve the targeted intervention revision, invalidate affected approvals and enqueue one resume command in the same DB transaction. A browser resumes only after that transaction commits. Durable command processing handles a crash between save and execution.
- State changes and their event records commit atomically. Deliver events at least once, deduplicate in the UI, and refresh from canonical state after gaps. Events are an audit/delivery facility, not a new event-sourced replacement for the database.
- Do not hold a DB transaction while waiting for a browser or model. Single-owner execution remains necessary even at concurrency=1 because dashboard, CLI, supervisor and recovery can race.

### C. Browser ownership, manual takeover and action uncertainty (V2-02/04/08)

A lease timeout alone is insufficient to transfer control: a stale worker may still be alive. Use an ownership generation and a browser-session owner that rejects old-generation commands. Before another worker or the owner takes over, the old worker must acknowledge suspension or its access/session must be revoked and verified closed. If neither can be established, stop with a recovery intervention.

Manual takeover sequence: request pause → finish/interrupt the current action at a documented safe boundary → acknowledge exclusive owner control → user acts → Resume → re-observe target, schema, answers, documents and effects → invalidate stale approval → continue with a new ownership revision. An automation pause request is not an immediate cancellation of a request already sent. Keep page/context ownership consistent with the technical baseline; do not attach indiscriminately to the owner's unrelated browsing tabs.

Classify actions by replay semantics. Observation is repeatable; filling a stable field may be repeatable after read-back; adding repeated work-history rows, creating an account, saving a draft, uploading a document and submitting can have external side effects. Before such an action, persist an intent ID and relevant preconditions; afterward persist observed outcome. If a crash leaves the result unknown, observe/reconcile before retrying. Local DB idempotency does not make a remote website operation idempotent.

Final submission provides at most one UAA-authorized attempt per approval, not a universal exactly-once guarantee at the ATS. Connection loss after the request may leave the application accepted remotely but unknown locally; that state must block a second attempt. Manual submissions outside the coordinator require recorded provenance and reconciliation. Taking control for login/correction does not automatically grant final-submit permission or disable the interlock.

### D. Supported mutations and review evidence (V2-01/02/06)

Use a capability record per flow/variant: permitted navigation, safe preparation mutations, upload protocol/evidence, final action identification, request-observation coverage, authentication mode and observed limitations. A platform hostname alone is insufficient qualification.

Default unknown/unqualified flows to observation or owner handoff when side effects cannot be bounded. A generic recipe may support known control patterns, but “generic” cannot imply a verified no-submission guarantee for arbitrary site scripts. Request monitoring supplements action guards; it is not proof against every possible channel, service worker or server-side effect. Lack of observed traffic is not evidence of absence when coverage is incomplete. Learn unsupported patterns in local/replayed fixtures or expressly authorized site testing, not by guessing in a live mutation session.

Document contracts distinguish native selection from asynchronous remote acceptance, as corrected in F2/T04. Enforce actual `accept`, size and multiple-file restrictions where observable, and surface unsupported format conversion as a document intervention. Never auto-merge/transcode signed or tailored documents merely to fit a widget. All actions refer to approved bundle entries; page/model output cannot select arbitrary local paths.

Review snapshots cover the complete attempt, not only currently visible fields. Record each step's last verified values, origin/frame, semantic field/repeat-group identity, relevant page revision and available server/UI evidence. Revisit or reconcile when later steps can change earlier answers. If the required earlier state cannot be verified for a flow, label readiness incomplete. An app-generated hash attests the recorded snapshot, not unseen remote storage.

Final review must reveal why an answer was used and which facts/documents are bound. Keep the existing exact approved target behavior; changing URL canonicalization to ignore volatile session parameters is a separate tested contract decision, not an implicit relaxation. A reconstructed session never silently inherits approval of different observed state.

### E. Facts, documents and upstream generation (V2-03)

Use explicit owner-verified, valid per-job facts first, then valid owner-verified profile facts and scoped owner policies. Imported candidate data is a versioned source; inferred/generated prose remains evidence requiring validation. Conflicting verified sources or a scope mismatch create a conflict; recency alone does not choose truth. Separate derived facts (such as duration/availability) from stored dates and recompute using explicit timezone, units and policy.

Automatic reusable saving requires a known semantic destination and visible save scope. A general model confidence score is insufficient for global promotion. Ambiguous answers are persisted job-locally; sensitive policies retain explicit scoped approval. Reusing knowledge also checks validity period, question polarity, country/employer context and option semantics. Neither “unknown” nor an optional blank becomes No.

Record which candidate/document revisions contributed to answers and review snapshots. Corrected facts mark affected reviews and potentially inconsistent document bundles stale. Existing tailored documents are not rewritten by UAA; obtain a corrected owner-selected bundle or later JobHunter export. Unaffected applications remain runnable; previously submitted records remain immutable historical observations.

JobHunter queue atomicity does not establish queue-plus-document generation consistency. Preserve any upstream manifest/hash/generation identity that exists; otherwise require a one-time verification of an unproven bundle rather than invent its provenance. Re-export compatibility never rewrites UAA-owned facts/answers/history. Model-generated document text is not automatically the original CV ground truth.

The existing requirement for both CV and cover letter is a V1 import constraint, not a universal form requirement. Preserve compatibility initially and expose missing-document preflight clearly. Supporting legitimate CV-only/no-document applications needs an explicit versioned eligibility/contract extension, not fabricated cover paths or silent import weakening. Cross-source aliases retain the original ID; authoritative requisition/tenant identity is required to link duplicates, with an audited correction path for bad links.

### F. Restore and local data boundary (V2-00/03/04/08)

Backups include DB, document manifests/bundles and relevant evidence with a consistent backup ID, schema version and event watermark; credentials/browser secrets use their separate local storage policy. A backup predating submission cannot contain that later fact. Restoring starts a new recovery epoch, revokes restored approvals/leases, and blocks external application mutations until the missing interval is reconciled against surviving audit/evidence or explicit owner verification. Unknown history is not permission to retry. Incompatible schema/binary combinations stop before mutation.

Localhost is a deployment choice, not the full trust boundary. Define allowed Host/Origin behavior, request authentication/CSRF protection for mutating browser/API requests, and authorization of provider/credential settings. Validate configured file roots and canonical paths, including symlink/junction escape; do not serve or upload arbitrary paths carried by untrusted queue metadata. Treat page content and imported text as untrusted, and separate fixture-only loopback targets from live target policy. These are design requirements, not a claim that this audit reproduced a local API exploit.

Define evidence retention and storage budgets. Redact outbound model context and routine logs; keep only required sensitive values in protected local stores. User deletion removes reusable knowledge and its semantic-index/cache entries, while submission evidence follows an explicit retention policy. Forgotten facts must not reappear from stale embeddings. Local-first does not imply that API fallback keeps data on-device; the settings UI must show which configured provider receives which categories of evidence.

### G. Model/runtime and provider behavior (V2-05)

First distinguish missing facts from unfamiliar wording. A missing candidate fact goes directly to an intervention; escalating through embeddings and several APIs cannot make it known. Deterministic rules, cached answers, local predictions and API output all pass the same scope/type/evidence gates. Rule confidence is not inherently safer than model confidence, as the reproduced mapping defects demonstrate.

Select the local runtime after a CPU/RAM/VRAM and packaging spike on the user's actual Windows environment. Preserve supported core Python versions; do not force all users to install a GPU stack to start the dashboard. An optional isolated local inference process is allowed if justified, with loopback binding, versioned messages and visible health; keep it off until qualified. Pin tested model/tokenizer revisions and support missing/corrupt/offline model files. A model upgrade invalidates only the caches/qualifications that depend on it.

Normalize provider transport and schemas while retaining task-specific contracts: answering a field, classifying a page and suggesting an orchestration decision do not require identical prompts or authority. Provider capability checks cover structured outputs, token/context limits, refusals and accounting. Self-reported confidence is not portable between models; evaluate acceptance by model/task profile. Configuration switching remains easy for suggestion-only use, while automatic application of answers is limited to qualified profiles.

Limit total calls/time/cost across an attempt, not just per request; cache keys include scope, evidence/profile revision, model/provider and prompt/schema revision. A fallback never broadens data-disclosure scope. A future provider protocol needs one tested adapter, not an executor rewrite.

### H. Additional acceptance cases found by the audit

| ID | Required behavior | Owner WP |
|---|---|---|
| T19 | Native file sent only at submit can reach truthful selection_verified readiness; async rejection still blocks. | 01/06 |
| T20 | Repeated Save & resume, two open dashboard tabs, and crash after save produce one revision and one queued resume; stale edits show a conflict. | 02/03/07B |
| T21 | Expired lease with a still-live worker cannot create two browser owners; manual takeover waits for acknowledged/revoked ownership. | 04 |
| T22 | Crash after add-row/upload/draft creation reconciles before replay; no duplicated employment entries or attachments. | 02/04 |
| T23 | Profile correction conflicting with tailored CV pauses only affected applications, invalidates their approvals, and does not rewrite past submissions. | 03 |
| T24 | Queue remains unchanged while a referenced document is replaced; intake detects change or records provenance unverified instead of claiming a matching export generation. | 03 |
| T25 | Restore backup taken before a fixture submission; external mutations remain blocked until reconciliation and no old approval becomes active. | 04/08 |
| T26 | State/event commit failure or lost event delivery cannot create false dashboard success; reconnect deduplicates and recovers committed state. | 02/07B |
| T27 | Untrusted page/model/file-path content cannot select credentials or arbitrary files; unauthorized cross-origin mutation is rejected under the chosen local API contract. | 01/03/05/07B |
| T28 | Deleting/changing a fact removes stale retrieval/cache reuse; model absence or provider change degrades to a clear supported mode. | 03/05 |
| T29 | Legacy/new mode toggles share duplicate/mutation/approval gates and cannot restore the known unsafe path; supported old behavior still passes. | 01/02 |

### I. Decisions still open, with explicit owners and timing

| Decision | Why it remains open | Resolve by |
|---|---|---|
| First real form family and supported variant | Inspect the actual owner-selected completed export and identify one candidate flow early; discovery does not authorize live browser actions. Mutation qualification still requires its own explicit authorization. | V2-02 intake/discovery; qualify before claiming live support |
| Upstream generation evidence | Inspect an actual completed export and referenced documents; fixtures alone cannot establish producer behavior. If unavailable, synthetic checks remain provisional. | V2-02 intake checkpoint; compatibility fixes as needed in V2-03 |
| Local model/runtime and useful coverage floor | Requires actual hardware, baseline error distribution and held-out benchmark. Laya remains a candidate. | V2-05 promotion gate |
| Evidence retention, backup destination and resource budgets | User preference and machine capacity affect defaults; propose reviewable defaults in settings design. | V2-03/04 before live data rollout |
| Unattended final submission scope | Original ambition exceeds current repository approval contract. No silent policy change is planned. | Separate explicit owner decision after controlled submission is proven |

These decisions do not block focused synthetic regression fixes. This audit did not execute new live tests, modify source code, benchmark models or certify the future contracts.

### J. V2-00 adopted baseline and execution contracts

These decisions are the V2-00 documentation contract. They describe the intended behavior for later workpackages; they do not claim that the behavior is already implemented or tested.

**Chosen baseline and migration state.** V2-00 starts from the remotely preserved `checkpoint/v2-00-baseline` checkpoint described in section 2. It descends from the WQ-8 checkpoint, then includes supervisor work and the dashboard-history commit above `origin/main`; this is a single selected history, not a request to merge three divergent trees. The Alembic head at the selected baseline is `0016_supervisor`. The V1 `CURRENT_STATE.md` snapshot predates this checkout and is identified as historical while the V2-00 baseline is recorded at its top. This is a mixed documentation/quality-baseline workpackage, so it uses its own `checkpoint/v2-00-baseline` branch rather than the documentation-only rebaseline branch.

**WQ-8 preservation and submission authority.** The WQ-8 active handoff is archived under `docs/handoffs/archive/ACTIVE_WORKPACKAGE_WQ-8.md` before V2-00 replaces the active handoff. Its one-submission maximum, exact application/snapshot approval binding, real-data restrictions, and Phase-A/Phase-B owner gate remain in force. V2-00 performs no authorization, live submission, or real ATS mutation. No V2 workpackage silently authorizes unattended final submission; any later change to that policy requires a separate explicit owner decision.

**Canonical state and commands.** `ApplicationJob` remains the authority for lifecycle and duplicate eligibility; `ApplicationAttempt` owns execution progress and checkpoint state; pipeline/run rows aggregate scheduling; supervisor state and dashboard state are projections. Commands carry application/attempt identity, an expected revision and an idempotency key. A stale revision is rejected with current state. State mutation goes through repository/store commands. Saving a correction, recording provenance, resolving the specific intervention revision, invalidating affected approval, and enqueueing one resume command happen in one database transaction. Browser/model work starts after commit. Durable state and its event commit together; event delivery may repeat, with deduplication and canonical-state refresh after gaps. No database transaction stays open while a browser or model runs.

**JobHunter re-import ownership.** JobHunter remains the producer for source/job identity, evaluation/tailoring output and produced artifact references. UAA remains the owner for its operational decisions and history, including manual-submitted markers (`dashboard_submitted` and `dashboard_submitted_at`), UAA corrections/answer provenance, attempts, interventions, approvals, submission outcomes and evidence. Re-import may update producer-owned job fields, but must not erase UAA-owned operational keys or lower duplicate protection. V2-01 will implement an explicit interim allowlist of UAA-owned operational metadata keys, initially including the two manual-submission keys, and test unchanged plus updated queue re-imports. It must not apply a blind deep merge to arbitrary producer metadata. Full upstream/local fact separation, versioned provenance and expanded compatibility ownership belong to V2-03; UAA will not modify the JobHunter repository as part of this contract.

**Document and upload evidence.** Each qualified flow declares its upload protocol and what evidence satisfies document readiness. For a native file input sent only with the final form request, `selection_verified` means the selected path/content hash and observable file constraints match the approved bundle; it does not mean the server accepted the file. An asynchronous uploader uses distinct `uploading`, `remote_accepted`, `rejected` and `unknown` states with observed evidence. An asynchronous rejection or unknown outcome blocks readiness. Selection alone counts only for a qualified native-input flow whose declared contract sends the file with final submission. UI and snapshots use these exact distinctions; they never describe local selection as remote acceptance.

**Readiness and final boundary.** A job cannot become `review_ready` solely because an intermediate page was filled or a submit-like control was found. Readiness requires a verified end-of-form review boundary for the qualified flow, the complete attempt snapshot (including values verified across earlier steps), required-field read-back, document evidence that satisfies that flow's declared contract, an identified and guarded final action, and no unresolved intervention, stale approval or unknown side effect. The final action remains unclicked until the existing exact-snapshot approval path permits a controlled attempt. A flow without a verifiable final boundary remains incomplete and is handed to the owner.

**Browser ownership and uncertain effects.** A lease expiry does not transfer browser control by itself. Each owner generation rejects commands from an older worker; takeover requires acknowledged suspension or verified revocation/closure. If exclusive ownership cannot be established, the flow stops for recovery. Observation may be repeated; field writes require read-back before replay; repeated-row creation, draft saving, uploads and submit attempts persist intent and reconcile the observed outcome before retry. An unknown final-submit result blocks another attempt and is never described as exactly-once delivery to the ATS.

**Initial form family decision.** The V2-00 record that no real form family had been selected is historical. Inspect an actual completed export and identify the owner's intended first flow at the earliest intake/discovery checkpoint. This planning change selects no employer and authorizes no live action; until the owner selects available inputs, synthetic/replay fixtures remain provisional and no broader live-coverage claim is made.

**Dashboard interaction design follow-up (V2-07A).** V2-07A remains a separate, unmerged design checkpoint. Its broad prototype and V2-07B production redesign are backlog. The first usable operator slice needs truthful job status, reason, next action and review evidence, scoped save/resume feedback, and keyboard/mobile accessibility. Keep atomic state/event ownership and audit history; use existing polling if it meets the local freshness target. Push transport, full replay infrastructure and visual polish are not prerequisites for the first flow.

## 18. Owner-directed delivery-plan update (2026-09-26)

This section is the current controlling plan for delivery order, test cadence and scope. It supersedes conflicting workpackage timing and acceptance language above; the detailed field, upload, state ownership and submission-safety contracts remain in force.

**Acceptance checkpoints.** Work in this order: (1) inspect one actual completed JobHunter export and its referenced owner-selected application flow; (2) prove intake and first-flow preparation through the production UAA app, API and background worker, using the real inputs where available; (3) prove a correction is persisted with provenance, resolves only its targeted intervention, invalidates only affected approval, and enqueues one scoped resume atomically, then verify correct future reuse while uncertain answers remain job-local; (4) qualify release behavior for the declared capabilities. The first-flow discovery is early; broad ATS implementation is not.

For the preparation end-to-end acceptance, start the production app and worker, import the completed queue snapshot, process one selected job through the real browser/executor path, persist observed form state and history, surface a blocker with a reason and next action, and reach review-ready only at the verified final boundary with required fields and documents reconciled. Verify persisted state and dashboard status/review evidence; assert that no final action was clicked and the fixture server observed zero final-application requests during preparation. Critical-path app, worker, store and executor must be real. A local deterministic fixture may stand in for a real site. If the completed export or real target is unavailable, synthetic evidence is useful but the corresponding actual-input or live-flow acceptance remains pending. This plan itself grants no live authorization.

**V2-02 boundary.** Continue the current bounded shared preparation/readiness core behind thin entry points. Keep the existing service/store, single browser owner, exact review snapshot and WQ-8 approval gates. Do not introduce a second executor, broad workflow framework, pool/lease framework or duplicate history. Controlled multi-step submission replay is a separate capability; it does not block correct multi-step preparation. Keep the approved-submit safety regressions intact while preparing.

**Flow safety and state.** Qualify concrete form variants and mutation capabilities early. Unknown or unqualified actions remain default-deny and hand off with an explanation; do not use a blanket POST allow rule or claim universal network safety. A crash or request with unknown remote outcome stops for reconciliation; never clear a lock or retry blindly. Preserve candidate facts, documents, attempts, interventions, history and outcomes. Saving a correction, recording source/scope, resolving the exact intervention revision, invalidating affected approvals and enqueueing one idempotent resume happen atomically. Known facts may be reused only at the right scope; uncertain answers stay job-local unless explicitly confirmed for wider reuse.

**Operator surface and optional intelligence.** Keep the first dashboard slice to timely status, reason, next action, verified review state and accessible correction/recovery controls. Preserve keyboard and mobile requirements. Existing polling is acceptable if it meets the observed local freshness target; a new push/replay subsystem is not a first-flow dependency. Resolution order remains deterministic rules, optional local semantic suggestions, then configured API provider, all under the same evidence and confirmation gates. Defer Laya/model bakeoffs, broad ATS expansion, generalized recovery, parallel execution and late visual-polish loops until measured demand.

**Test and review cadence.** Before broad test expansion, freeze the acceptance contracts and inventory the fixtures/commands. Each code iteration runs focused static, unit and contract checks; run affected integration/browser selections before supervisor review. Run one combined full non-live gate at the accepted integration checkpoint/workpackage before merge, not redundant full non-browser and full-browser-inclusive gates back to back. Any later code change invalidates affected evidence; the final accepted code receives the gate. Documentation-only changes require document consistency and whitespace checks, not code suites. Production E2E uses the real app/worker and store on its critical path. L0 is local synthetic qualification and needs no live authorization; L1 observation, L2 live preparation and L3 real submission remain separate live levels, each requiring its own explicit authorization under the controlled-submission plan. When Playwright MCP is unavailable, an equivalent Python Playwright inspection may be recorded with its method and evidence; do not label it MCP.

**Evidence, metrics and integration.** Preserve checkpoints independently from acceptance: a clearly labelled WIP checkpoint may record known failures or unverified gates before a long run or context reset; only a gate-passing accepted milestone is called accepted. Resolve SHA values dynamically from command output; never write a commit's self-SHA into its own handoff. The base is the actual source/integration parent and need not be `main`. Use one integration branch per workpackage, with optional bounded worktrees only for independent paths and one named integration owner. Report a capability acceptance matrix and measured baseline for cycle time, rework and runtime when collected; do not publish aggregate progress percentages, fabricated baselines or speedup claims. Defer duration profiling and marker-classification changes to separately scoped work.

**Conditional migration decision.** Invalidating approvals during a schema migration is a proposed safeguard only. First inventory live and in-flight authorizations and determine whether the migration changes the approved snapshot semantics. Do not adopt or execute blanket migration-time invalidation silently; preserve the existing explicit approval contract until an owner-reviewed decision is recorded.
