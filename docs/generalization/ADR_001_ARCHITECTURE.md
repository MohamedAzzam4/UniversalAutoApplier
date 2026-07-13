# ADR-001: Architecture — Repository Ownership, Local-First v1, and Adapter Boundaries

- **Status:** Accepted
- **Date:** 2026-07-12
- **Phase:** 0 — Architecture Baseline (Workpackage 0.2)
- **Supersedes:** None
- **Superseded by:** None
- **Related docs:** `DEPLOYMENT_AND_REPO_STRATEGY.md`, `TECHNICAL_BASELINE.md`, `ROADMAP.md`, `CURRENT_SYSTEM_MAP.md`

---

## Context

The project started as `SiemensAutoApplier`, a working but Siemens-specific
job application automation. As the product generalizes to multiple ATS
platforms (Greenhouse, Lever, Workday, SmartRecruiters, LinkedIn Easy Apply,
and unknown sites), three forces are in tension:

1. **Preserve what works.** The Siemens application flow
   (`workflows/apply_workflow.py`) and its page objects are proven. Rewriting
   them as "generic" code risks breaking a working pipeline and duplicates
   effort.

2. **Generalize safely.** Unknown sites must never auto-submit. Generic
   navigation and form filling need review-before-submit as a hard default,
   not an opt-in.

3. **Keep repos focused.** `JobHunter` owns search/evaluation/tailoring.
   `SiemensAutoApplier` owns Siemens-specific automation. A new
   `UniversalAutoApplier` owns the generalized application layer. Mixing
   these responsibilities in one repo creates coupling that makes independent
   testing and evolution impossible.

The bootstrap phase created `UniversalAutoApplier` as a sibling repository
with a runnable skeleton (health endpoints, dashboard shell, persistence,
CI) but no application behavior. This ADR records the architectural decisions
that govern all subsequent phases.

---

## Decision

### D1. Three independent repositories with separated ownership

```
JobHunter
  Owns: job discovery, evaluation, ranking, CV tailoring,
         cover-letter generation, export of ready-to-apply jobs.

UniversalAutoApplier
  Owns: queue import, application state, adapter routing, generic
         navigation, generic form filling, interventions, answer memory,
         review-before-submit, evidence, application history, dashboard.

SiemensAutoApplier
  Owns: existing Siemens-specific discovery, evaluation, tailoring, and
         application automation. Remains independently runnable.
```

No repository directly edits another repository's private persistence
files. Integration uses the contracts in `DATA_CONTRACTS.md` and the
adapter boundaries in section D4.

### D2. The generalized product must not be built inside SiemensAutoApplier

Generalized production code lives **only** in `UniversalAutoApplier`. The
Siemens repository keeps its Siemens-specific behavior and is integrated
through a narrow adapter boundary (D4). This is non-negotiable.

### D3. Version 1 is local-first

Version 1 runs entirely on the user's local machine (Windows reference;
Linux/macOS supported for development). Specifically:

- The dashboard binds to `127.0.0.1` by default. Public bind is rejected
  at config load time (`config.py` refuses `0.0.0.0` / `::`).
- No VPS, container platform, Cloudflare Tunnel, or hosted database is
  required.
- No authentication on the localhost API. Any future public deployment must
  add authentication before binding publicly.
- All artifacts (queue, history DB, logs, screenshots, traces) live under
  one configurable local data directory (`UAA_DATA_DIR`).

Cloud deployment is explicitly out of scope for v1. The architecture must
not block a later move, but no cloud abstraction is added without a current
need.

### D4. Siemens invocation boundary

`SiemensAdapter` lives inside `UniversalAutoApplier`
(`src/universal_auto_applier/adapters/siemens_adapter.py`, Phase 2). It
integrates with the existing Siemens workflow through a **narrow boundary**:

- **Mechanism (to be finalized in Phase 2):** a documented CLI subprocess
  command, or an importable entry point when both repositories are
  configured locally. The exact mechanism is selected in Phase 2 after
  mapping the current Siemens entry points (`main.py`, `main_pipeline.py`).
- **Contract:** the adapter converts a generic `ApplicationJob` into a
  typed Siemens adapter request, calls the Siemens entry point, and
  converts the response into a structured `AdapterResult`. It does **not**
  parse human-readable log text to determine success.
- **What stays in Siemens:** selectors, page objects, and application stage
  logic. These are never copied into `UniversalAutoApplier`.
- **Shared code policy:** code moves into a shared package only after tests
  prove both callers need it. No premature sharing.

### D5. Generic unknown-site automation must use review-before-submit

Any adapter that handles an unknown or generic platform **must not
auto-submit**. The default submit mode is `review` (or `dry_run`). The
generic adapter stops at the final submit button and creates a
`review_before_submit` intervention. Only explicit user approval advances
to `submitted`.

Trusted adapters (e.g., `SiemensAdapter` if explicitly configured) may
submit without a review pause, but only when all of these are true:

- `auto_submit_enabled` is true in config
- the adapter is marked trusted
- the job passed the eligibility gate
- no unresolved interventions exist
- review evidence was captured

### D6. Testing and regression expectations for future phases

Every phase must pass the regression gate defined in
`TESTING_STRATEGY.md`:

1. All old tests pass.
2. All new tests pass.
3. The full dry-run pipeline fixture test passes (once it exists).
4. UI smoke tests pass if UI was touched.
5. No unrelated files changed.
6. A short implementation report is written.

Additionally:

- **Siemens regression is a blocker.** Existing Siemens tests must continue
  to pass after every phase. If a phase changes the Siemens integration
  entry point, that change is a separate commit and report.
- **No live ATS tests in default CI.** Tests marked `live` never run in the
  default regression command.
- **ResourceWarning is an error.** `pyproject.toml` configures
  `filterwarnings = ["error", "error::ResourceWarning", ...]` so unclosed
  SQLite connections, file handles, and similar leaks fail the test suite
  on all Python versions.

---

## Alternatives Considered

### A1. Build the generalized product inside SiemensAutoApplier

**Rejected.** This would couple the generalized layer to Siemens-specific
code, make independent testing impossible, and risk breaking the working
Siemens pipeline. The planning pack explicitly rejects this: *"Create
UniversalAutoApplier as a new repository. Do not implement the generalized
product inside SiemensAutoApplier."*

### A2. Rewrite Siemens as generic code as the first step

**Rejected.** Rewriting `ApplyWorkflow`, page objects, and selectors as
"generic" code before proving the adapter boundary would discard proven
behavior and create a large, risky change with no fallback. The adapter
boundary (D4) lets us generalize incrementally while keeping Siemens
working.

### A3. Single monorepo for all three repos

**Rejected.** A monorepo would merge three currently-independent
repositories with different release cadences, testing setups, and ownership
models. The current separation reflects real organizational boundaries
(search vs. apply vs. Siemens-specific). A monorepo would also make the
"do not modify Siemens" rule harder to enforce.

### A4. Cloud-first deployment for v1

**Rejected.** The user's actual deployment target is their local Windows
machine. A cloud-first v1 would add authentication, hosting, database
management, and network reliability concerns before the core application
logic is proven. Local-first v1 keeps the feedback loop tight. Cloud
deployment remains possible later without architectural blocking.

### A5. HTTP API contract between JobHunter and UniversalAutoApplier

**Rejected for v1; possible later.** An HTTP API would add a server
process, port management, and serialization concerns to v1. The file
contract (`application_queue.jsonl`) is simpler, reproducible in tests, and
works with the user's existing local file workflow. A later version may add
an HTTP API, but the file contract remains supported for reproducible
testing.

### A6. Auto-submit for generic sites after a confidence threshold

**Rejected.** Even high-confidence generic navigation can hit login walls,
captchas, or unexpected form layouts. Auto-submitting on unknown sites
risks duplicate applications, wrong answers, or submissions to the wrong
job. Review-before-submit is the only safe default for generic sites.

---

## Consequences

### Positive

- **Siemens keeps working.** The proven Siemens flow is untouched and
  independently runnable. Regression risk is isolated to the adapter
  boundary.
- **Generalization is incremental.** Each phase adds a layer (queue,
  adapters, navigator, form engine, interventions) without rewriting
  existing layers.
- **Local-first is simple.** No cloud credentials, no network reliability
  concerns, no authentication surface in v1. The user runs one command and
  the dashboard opens.
- **Testing is deterministic.** The file contract and fixture-based tests
  mean no live ATS calls in regression. CI runs on every push.
- **Safety is the default.** Generic sites cannot auto-submit. The status
  machine and review-before-submit gate make accidental submission
  impossible without explicit approval.

### Negative

- **Two repos must be configured locally for Siemens.** The user needs both
  `UniversalAutoApplier` and `SiemensAutoApplier` checked out, with
  `UAA_SIEMENS_REPO` pointing at the latter. This is documented in
  `.env.example` and the health endpoint reports
  `siemens_adapter: not_configured` when the path is missing.
- **No live coordination between JobHunter and UniversalAutoApplier.** The
  file contract means JobHunter must export the queue file before
  UniversalAutoApplier can import it. This is acceptable for v1 but may
  need an API later for real-time workflows.
- **Shared code duplication in the short term.** Until the adapter boundary
  proves which utilities are genuinely shared, both repos may have similar
  helpers (evidence capture, safe actions). This is intentional;
  premature sharing is worse than short-term duplication.
- **Python version sensitivity.** Pinned exact dependencies and
  `error::ResourceWarning` filtering mean Python 3.14's stricter
  finalization catches leaks that Python 3.12 ignores. This is a feature
  (catches bugs earlier) but requires the engine-disposal discipline
  established in the bootstrap.

### Neutral

- **The Siemens invocation mechanism is deferred to Phase 2.** D4 records
  the behavioral boundary (typed request/response, no log parsing) but
  leaves the exact mechanism (CLI vs. import) to Phase 2 after mapping
  current entry points. This is intentional — the boundary is fixed even
  if the mechanism changes.

---

## Verification

This ADR is Phase 0 WP 0.2. It changes no runtime code. The decisions it
records are enforced by:

- `config.py` — rejects public bind (D3)
- `core/statuses.py` — `ALLOWED_TRANSITIONS` gates submission behind
  `review_ready` (D5)
- `pyproject.toml` — `filterwarnings = ["error", "error::ResourceWarning"]`
  (D6)
- `.github/workflows/verify-windows-py314.yml` and `verify-linux.yml` —
  CI gate on every push (D6)
- `DEPLOYMENT_AND_REPO_STRATEGY.md` — source of truth for D1, D2, D3, D4
- `IMPLEMENTATION_RULES.md` — source of truth for D5, D6
- `TESTING_STRATEGY.md` — source of truth for the regression gate (D6)
