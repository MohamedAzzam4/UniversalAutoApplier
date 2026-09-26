# V2-07A — Dashboard interaction design

**Status:** reviewable design deliverable and static interaction prototype. This is not production UI or evidence that backend contracts are implemented.

**Prototype:** [prototype/index.html](prototype/index.html). It uses synthetic records and local scripts only; every action is simulated in the browser.

**Design base:** checkpoint/v2-02-safety at 93a45055f96f00a0d4217f37e4da01b639d73695.

## What the operator should understand

The overview answers these questions without opening logs:

1. What is running, and how fresh is this view?
2. Which applications need an owner decision?
3. What happened most recently?
4. What is the next safe action?

A blocked application always explains the cause, the evidence that supports that state, and one primary recovery action. Routine progress updates should not steal focus or reset the operator's work.

## Current dashboard findings

The current dashboard has separate Dashboard, Results & history, Interventions, Review, Submit, and Logs views. It presents some useful queue and health data, but the operator has to move between views to understand a job. Answer edits use a browser prompt, the remember-answer checkbox has no visible destination or scope, and the global Resume / Retry handler selects the first resolved intervention. That can resume a different job from the one the owner just corrected. The shared refresh loop is ten seconds, so a dashboard can continue to look idle after the worker has advanced.

These are interaction-design observations from the V2-02 safety baseline. V2-07B should replace the prompt and global resume behavior only after the executor command/revision/idempotency and durable-event contracts are ready.

## Information architecture

Use four persistent destinations:

- **Overview** — run health, four queue counts, top two attention items, latest meaningful activity, and one exact-scope run control.
- **Applications** — searchable/filterable job cards with current phase, plain-language state, last verified event, next action, and a link to job detail.
- **Needs your input** — an attention queue ordered by impact/age, grouped only when the same answer and reuse scope genuinely apply. Each item opens its own bound application and attempt.
- **Answers & documents** — owner-confirmed facts, preferences, scoped answers, document bundles, provenance, revision, expiry and affected applications.

History, review, health, and technical evidence belong in the selected application's detail or in secondary/expandable panels. Do not make raw logs, selectors, model traces, or hashes part of the first-screen hierarchy. Keep controlled submission visibly separate from preparation.

## Visual direction

Use an operational pattern that keeps dense status information readable: quiet slate surfaces, a dark navigation rail, a clear blue primary action, and restrained amber/green/red state accents. Pair every color with a written state. Use the operating system sans-serif stack so the prototype remains legible offline and does not contact a font service. Reserve motion for brief cause-and-effect feedback; honor reduced-motion settings. The ui-ux-pro-max operations pattern informed this direction; its exaggerated display-style recommendation is not suitable for a work queue.
## State vocabulary and evidence

Never use a bare “Done,” “Applied,” or green check to imply an ATS outcome. Use the canonical state plus a sentence and next action.

| UI label | Meaning shown to the owner | Primary next action |
|---|---|---|
| Queue preflight needed | Imported job or document checks have not completed | Review preflight |
| Blocked before start | A specific eligibility, duplicate, queue, or document gate prevents scheduling | Resolve the listed blocker or exclude this job |
| Queued | Eligible and waiting for a worker | View queue position |
| Preparing · {stage} | Worker is active at the named stage; show last event time | Open job details |
| Waiting for login / owner action | Automation is paused at a safe boundary and needs the named action | Open the bound application tab or resolve this intervention |
| Saving correction | Save command is in flight; retain the draft | Wait; do not send a different revision |
| Saved · resume queued | The correction, provenance, resolution, approval invalidation, and one resume command committed | View job |
| Rechecking | Worker is re-observing and verifying after correction/takeover | Follow progress |
| Review ready · approval required | Full final-review boundary and evidence are present; no final submit occurred | Review snapshot |
| Owner marked submitted | Owner recorded a manual submission; ATS receipt has not been independently observed | Reconcile/record evidence |
| ATS confirmation observed | A named site receipt or confirmation signal was recorded | View evidence |
| Outcome unknown · reconcile first | A submission request may have reached the ATS but confirmation is missing | Reconcile outcome; ordinary retry stays unavailable |
| Failed / skipped / cancelled | Show whether a safe retry exists, the last checkpoint, and the reason | Retry only if authorized, or inspect/skip |

“Preparing” is an attempt state, not a statement that the ATS accepted anything. Show document states using the evidence contract: selection_verified means local selection matched the approved bundle; remote_accepted requires qualified remote evidence; rejected and unknown block readiness. The job detail identifies which kind of evidence supports each badge.

### Per-job detail

Keep a stable header with company, role, source identity, attempt number, current state, and one primary action. Below it show:

1. A chronological stage timeline with timestamps, current/last verified checkpoint, and reason for pauses.
2. The answer list with field/question, value, source, scope, confidence/reason when applicable, verification result, and profile/document revision used.
3. A document section with display name, kind, approved bundle revision, privacy-safe hash suffix, and exact evidence state. Do not expose a full local path by default.
4. A review section that says why the form is or is not review-ready, what became stale, and what approval authority remains. Review-ready is separate from controlled final submission.

## Operator journeys

### 1. JobHunter queue → preflight → prepare

1. The owner explicitly imports the latest JobHunter queue into UAA. JobHunter remains the producer of evaluated jobs and tailored artifact references; UAA reads the exported queue and owns application attempts, local corrections, events, and evidence. UAA does not mutate JobHunter data.
2. Show the import generation/time, accepted, duplicate, rejected, and changed counts. Keep source identity visible so a re-import is distinguishable from a new application.
3. Before scheduling, show the exact eligible and blocked job counts. For each blocked row, name the missing/invalid CV or cover letter, duplicate/submitted guard, inaccessible artifact, or unverified provenance. Provide a per-job exclude option; do not silently weaken eligibility.
4. Show the selected bundle by safe display name and kind. Check file existence, canonical allowed root, manifest/revision, and relevant format/size constraints where observable. Do not make a missing cover letter look like a valid document or open a browser as an import side effect.
5. The primary action states its scope, for example **Prepare 3 eligible applications**. Run-level pause means “stop starting new jobs and pause active jobs at their next safe checkpoint.” Per-job pause affects only that job. A run-level cancel does not claim to undo an external request already sent.
6. After start, each application remains a separate card/timeline. A failure in one application immediately enters Needs your input while another eligible application can keep its own state. V2-09 controls whether the scheduler can run concurrently; the UI must not imply concurrency is available when the scheduler limit is one.

### 2. Resolve a missing fact and save it for future jobs

The intervention view names the job, exact question, form step, reason it paused, whether an answer exists, and whether a shown suggestion is only a suggestion. An absent fact is never filled by an embedding/model fallback.

The owner enters the answer in a labeled field. Show a visible **Save for** scope choice before submission:

- A stable fact such as a postal code may default to a versioned profile fact only if the semantic destination is known and no verified conflict exists.
- Preferences retain units, geographic/job scope, effective date, and expiry as needed.
- Employer-specific prose stays within the application/employer scope.
- Consent, sensitive declarations, and unknown-intent questions default to this application.
- If intent or destination is ambiguous, save only to this application and offer future reuse as a separate explicit choice.

Display the destination summary beside the choice (example: “Postal code · profile fact · reused for equivalent address questions”). The user can change it to “This application only.” Never make a model suggestion look like owner-confirmed truth. A conflict shows both revisions and their provenance and requires an explicit correction choice.

The primary action is **Save answer & resume this application**. It carries the application, attempt, intervention revision, expected state revision, an idempotency key, the answer, and selected scope. The command transaction records the user source/provenance, updates the scoped fact, resolves the targeted intervention, invalidates any affected approval, and enqueues at most one resume command. The browser starts only after that transaction commits.

The UI renders the acknowledgement sequence:

**Saving correction → Saved → Resume queued → Rechecking → Verified / blocked again**

The first three labels may be rendered from one committed command response; they must not imply separate successful database commits. If persistence fails, retain the draft, show the error by the field and keep Resume disabled. If the expected revision is stale, retain the draft and show the current server answer/state beside it. Offer **Reload current** and **Apply my draft to the new revision** as deliberate choices; never silently overwrite. Repeat clicks/retries reuse the same idempotency key for the same intent.

Saving a stable fact for future use can affect other active applications only after their revision/scope checks; it must not inject a value into another live form. Mark affected applications for revalidation and stale any approval based on an older profile revision. Do not rewrite a JobHunter-tailored CV or cover letter; flag possible inconsistency and request a corrected upstream bundle when required.

### 3. Login, document, or platform blocker

Login/CAPTCHA/manual-browser work binds to the exact UAA-owned page/session. Show the last safe checkpoint and a single **Open application tab** action. Transfer control only after the worker acknowledges pause or its access is revoked and verified closed. On resume, observe the page, form schema, answers, document state, and external effects again; invalidate stale review approval.

For document rejection, show the exact document kind, observed constraint/reason, selected bundle revision, and whether evidence means local selection or server acceptance. Offer replace/reselect only from an approved UAA-owned bundle. A raw ATS page string never selects an arbitrary local path.

A site defect offers a repair ticket/manual workaround or skip, not a candidate-data question. An unknown submission outcome offers reconciliation and blocks ordinary retry.

### 4. Review

Review shows a complete snapshot of all steps, not only the currently visible one. Each answer links to its evidence/source and each document names its bundle revision and truthful upload state. Show changed/stale fields and why they require a new observation. The primary action is **Review snapshot** or **Return to unresolved items**. Approval and any controlled final submission remain a separate, explicitly gated flow; preparation controls never submit.

## UI command and event boundary for V2-07B

The prototype uses a local sample state only. The following is the design boundary for later implementation, not an assertion that these API types exist at the V2-02 base.

Every read model is a projection of canonical store state and carries application ID, attempt ID, monotonic state revision, current stage/status, last meaningful timestamp, reason/evidence references, and allowed actions. A run summary adds run ID and exact scope. The dashboard never becomes a second state authority.

A command request includes:

- application_id, attempt_id, expected_state_revision;
- a stable idempotency_key for the user's intent;
- command kind and typed payload (for correction: intervention ID/revision, answer, provenance source=user, reuse scope);
- command-specific confirmation where the backend policy requires it.

Commands include queue import, queue preflight, start run, pause/cancel run, pause/resume/skip/retry one job, open/take over a bound browser session, save-and-resume correction, refresh/re-observe, and request a review snapshot. The server returns committed state revision, saved answer/profile revision where relevant, approval invalidation, and resume-command identity. A stale expected_state_revision returns a conflict with current canonical state; it does not save or enqueue.

A durable event envelope contains ordered event ID/sequence, event type, application/attempt/run IDs, canonical revision, timestamp, stage/status, plain-language reason code/message, sanitized evidence reference, and allowed next action IDs. Delivery is at least once; the client deduplicates IDs, replays from the last cursor, and refreshes the canonical read model after a sequence gap or reconnect. Per-job events must not be mistaken for run-level outcomes.

The plan target is local state freshness at p95 ≤2 seconds. The current ten-second shared polling loop misses that target. V2-02/07B should measure a shorter shared poll versus a durable push transport and select the simpler transport that meets the target while supporting reconnect/replay. Show a persistent **Updated {time} / Reconnecting / Stale since {time}** indicator; do not show an animated “live” dot while stale.

Only render actions returned as allowed by the canonical read model. Each action is scoped to its application or run, includes its destructive/consequence level, and is disabled with a clear reason when blocked. UI retries must preserve the original idempotency key; a new user intent gets a new key. No direct row edits or page-supplied URLs/paths become commands.

## Gulf of evaluation and execution

- Put current state, reason, evidence level, last update, and next action together in the same job row/card.
- Use words and icons as well as color. Keep a compact state legend close to the queue.
- Show the focused job and selected filter through background refreshes; preserve draft, focus, scroll, and selected application.
- Group only semantically identical interventions; a shared answer can resolve multiple jobs only when every target, scope, revision, and evidence check is explicit. Show the affected job list before a bulk action.
- One primary action per context. Move Logs, request traces, model provider diagnostics, raw IDs, and technical evidence behind “Details.”
- Report progress by verified stage (e.g. “Questionnaire · 3 of 5 steps verified”), never an unexplained percentage.
- Use inline cause → fix feedback. Avoid browser prompts, generic alerts, silent retries, toast-only errors, and global resume buttons that pick an arbitrary job.

## Responsiveness, keyboard and screen-reader behavior

The prototype and V2-07B target 1440×900 and 390×844. Use a compact desktop sidebar; collapse it to a labeled four-destination mobile bottom navigation with safe-area padding. Reflow job cards instead of requiring a horizontally scrolling table. Keep action targets at least 44×44 CSS px and separate adjacent controls by 8 px.

Provide a skip link, semantic landmarks, sequential headings, visible 2–4 px keyboard focus, labels independent of placeholders, useful accessible names, and text labels beside status icons. Dialogs announce their title, move focus into the first field on open, contain Tab focus, close with Escape when safe, and restore focus to their trigger. After navigation, move focus to the view heading. Announce actionable state changes in a polite live region; do not announce every routine field event. On a save error, focus/associate the first invalid field and retain its text. Keep input and status contrast at WCAG AA, support 200% zoom and reduced motion, and do not use hover-only affordances.

## Privacy and evidence presentation

The dashboard is local-first but its evidence can contain personal information. Use synthetic data in the prototype. In production, keep routine event messages short and redacted; show sensitive answer values only in an owner-selected detail view. Avoid full file paths, raw page HTML, screenshots, session cookies, credentials, and tokens in ordinary UI/log views. Evidence details need explicit retention/export/delete behavior from V2-03/04. The model-provider settings must disclose which evidence categories may leave the device; API fallback is not local processing. A deleted fact must also disappear from reusable indexes/caches under the knowledge contract.

## Prototype coverage and acceptance

The static prototype demonstrates import/preflight, state clarity, attention queue, correction scope selection, the acknowledgement sequence, one-job follow-up, and a review/evidence summary. It has no backend, makes no network calls, uses no candidate data, and contains no final-submit mutation control.

Review manually at desktop 1440×900 and mobile 390×844. Confirm:

- import and preflight counts match the per-job list;
- starting preparation names its exact eligible scope and does not claim submission;
- each job gives its own cause, evidence, next action, and status;
- correction scope is visible and editable before Save & resume;
- the simulated path reaches Saving → Saved → Resume queued → Rechecking;
- a simulated conflict preserves the owner's draft and requires an explicit choice;
- failed persistence retains the draft and never resumes;
- keyboard navigation reaches all controls in visual order with visible focus;
- mobile has no horizontal page scroll and the bottom navigation does not cover content;
- the detail/review view distinguishes selection from remote acceptance and review-ready from submitted.

V2-07B acceptance should add UI behavior tests for those transitions plus regression coverage for existing dashboard/history, queue import, intervention, recovery, and controlled-submission gates. Browser automation should test both viewports, keyboard focus, reconnect/stale state, concurrent tab revision conflicts, and action gating. A prototype check is not a substitute for backend integration, release qualification, or an authorized live submission.
