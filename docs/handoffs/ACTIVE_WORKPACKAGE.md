# Active Workpackage — WQ-7B (MERGED / COMPLETE)

- **WP ID:** WQ-7B — Real ATS Navigation Reconnaissance.
- **Status:** MERGED / COMPLETE.
- **PR:** #13 — https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/13
- **PR head:** `adc8c8dff25c8e1f4143d8db65292d223867a893`
- **Base SHA:** `6326e4e0815d2d325eccc5bf3671afefd8e5bc8b` (`origin/main`, WQ-7A squash)
- **Merge commit:** resolve dynamically (see command block below); WQ-7B is
  complete on `main`, no further branch work for this WP is tracked here.
- **Branch:** `checkpoint/wq-7b-real-ats-navigation` (left intact; no
  further commits expected)
- **Amended acceptance:** SATISFIED (see evaluation below).
- **CI:** all six required checks passed on the final PR head `adc8c8d`
  (Linux 3.11/3.12/3.13/3.14, Windows Core, Windows Playwright).
- **Applications submitted:** none.
- **WQ-7C:** NOT started.
- **Branch-head verification (run dynamically; do not trust an embedded SHA):**

  ```text
  git fetch origin
  git rev-parse HEAD
  git rev-parse origin/main
  ```

  `origin/main` contains the PR #13 merge commit (head `adc8c8d` merged as
  `cab7a13`).
- **Last updated:** 2026-08-16 (post-merge closure)

## Objective

Prove that UAA can safely navigate from real public job-detail pages to real
ATS application forms and observe their structure using the WQ-7A
infrastructure — **navigation and observation only**. Never fill fields,
never upload documents, never log into accounts, never submit applications,
never bypass CAPTCHA/anti-bot.

## Acceptance criterion history (preserved)

### Original criterion

At least five real-job attempts; every supported ATS attempted; **at least
three distinct ATS platforms must reach a real public application form**.
Unsuccessful platforms get exact evidence and classification.

### Original result — BLOCKED

Evaluating the original criterion after exhaustion of all permitted
replacement targets: only **two** distinct ATS platforms reached a real
public application form (greenhouse, lever); workday, smartrecruiters and
icims were externally gated with replacements exhausted. Result recorded as
**BLOCKED** with a full evidence manifest (see
`docs/evidence/wq-7b/MANIFEST.md`). This historical record is preserved and
**not** rewritten.

### Reviewer closure

A reviewer-closure pass on `cc79959` resolved every open item: the iCIMS/KCI
shell-vs-widget apply defect was root-caused, regression-tested, and fixed;
real KCI was rerun post-fix to `login_required`; attempt counts and
validation counts were reconciled; the Lever `form_submit=1` interlock event
was attributed to page/third-party JS, not UAA. Outcome remained BLOCKED
under the unchanged original criterion; no PR was opened.

### Reason for owner amendment

Real-world reconnaissance demonstrated that reaching ≥3 distinct ATS
application forms depends partly on external ATS availability, login policy,
and anti-automation/security behavior that UAA is explicitly forbidden to
bypass. The owner therefore approved an amendment after the completed real
reconnaissance and reviewer closure.

### Owner amendment (APPROVED)

Replacing the single original criterion with:

1. At least **TWO** distinct supported ATS platforms reach a real public
   application form.
2. Every supported ATS platform that does NOT reach a real public
   application form must: have the permitted replacement-target budget
   exhausted, have an evidence-backed external gate or externally imposed
   unsupported condition, and have **no unresolved UAA-caused defect**
   contributing to the failure.
3. An externally blocked platform MUST NOT count as successfully classified
   if an unresolved UAA defect prevents UAA from reaching or observing the
   external blocker.

All other original WQ-7B acceptance criteria remain unchanged (every
supported ATS attempted; ≥5 real-job attempts; zero typed field values; zero
uploads; zero UAA submit clicks; submit interlock active; no account login;
no CAPTCHA/anti-bot bypass; no real personal data; no secret/session
artifacts committed; truthful classification; no default CI internet
dependency).

### Decision record

| Item | Recorded |
| --- | --- |
| Original criterion | ≥3 distinct ATS reaching a real public application form |
| Original result | BLOCKED |
| Reviewer closure | `cc79959` — all closure items resolved; BLOCKED retained |
| Reason for amendment | external availability/login/anti-bot dependence outside UAA control |
| Amended criterion | ≥2 distinct reached + exhaustion/evidence/no-UAA-defect per remaining platform |
| Amended outcome | **SATISFIED** — see evaluation below |

## Final outcome — SATISFIED under owner amendment; MERGED via PR #13

### Results (unchanged evidenced facts)

All five supported ATS platforms were attempted (authoritative detail in the
reconciliation block below). Only **two** distinct ATS platforms prove
reachable to a real public application form without forbidden bypasses:

- **greenhouse** — FORM REACHED (Anthropic, 31 controls / 1 file,
  `recon_complete`)
- **lever** — FORM REACHED (Apply Digital, 24 controls / 2 files,
  `recon_complete`; interlock blocked 1 programmatic `form_submit`
  (`submit_events=0`), `submitted=false`)

The other three are externally gated, each verified across multiple tenants
with all permitted replacements exhausted (max 2 per platform):

- **workday** — account-creation gate on 3/3 tenants (Company JR-0108019,
  US Bank `2026-0024161`, Baltimore City Fire Press Officer R0018870).
- **smartrecruiters** — anti-bot/security wall on its one-click UI on 3/3
  tenants (Eurofins, Pilot Company, IT TrailBlazers); SPA boots for normal
  browsers but reproducibly fails automation contexts; WQ-7B forbids
  anti-bot bypass, so none was attempted.
- **icims** — login/account-creation gate on 3/3 tenants (LMI, MBP, **KCI**)
  plus the platform's own careers hub (`hrjobs.icims.com`), whose every
  "Apply Now" links to a `/login` URL. KCI was previously `click_failed`
  (page-shell Apply vs widget competition); the reviewer-closure pass fixed
  and live-verified it (`login_required` post-fix). No UAA-caused
  navigation failure remains on iCIMS.

Every run: `fields=[]`, `uploads=[]`, `submitted=false`, hard submit block
armed, ephemeral profile, `UAA_LIVE_RECON_ONLY=true`.

### Evaluation under the amended criterion — SATISFIED

- **Criterion 1 (≥2 distinct reached):** MET — greenhouse and lever both
  reached a real public application form (2 distinct).
- **Criterion 2 (each non-reaching platform: budget exhausted + evidence-backed
  external gate + no UAA defect):** MET —
  - workday: 2 replacements exhausted (3 tenants), evidence-backed account
    creation gate, no UAA defect.
  - smartrecruiters: 2 replacements exhausted (3 tenants), evidence-backed
    anti-bot/security wall (DataDome one-click UI), no UAA defect.
  - icims: 2 replacements exhausted (3 tenants), evidence-backed
    login/account-creation gate. The KCI apply-path UAA defect that was
    originally exposed was given a hermetic regression test and a minimal
    fix; real navigation was rerun after the fix and reached the external
    `login_required` state. Therefore **no unresolved UAA defect remains
    responsible for the iCIMS failure**.
- **Criterion 3 (no blocked platform counted while an unresolved UAA defect
  blocks observation):** MET — the one case with a UAA-facing defect (KCI)
  was fixed and re-verified live; its external blocker was then reached and
  observed (`login_required`). No platform is classified "externally blocked"
  while a UAA defect prevents reaching that blocker.

History of KCI (preserved): reproduced defect → hermetic regression test →
minimal `embed_rank` fix → real rerun post-fix → `login_required` → no
unresolved UAA defect.

## Attempt-count reconciliation (reviewer-closure pass)

The earlier "7 real-job target runs" figure was an undercount inconsistent
with the 11-row evidence matrix. Authoritative figures (see MANIFEST):

- **Distinct target URLs executed by the runner: 12** (14 unique URL
  strings minus the duplicate US Bank `2026-0024161` and LMI `14407` form
  variants).
- **Distinct real public jobs observed (runner + MCP-only spot checks): 15**
  (adds Pilot Company SR, MBP iCIMS, Baltimore City WD).
- **Evidence rows (per-platform block outcome): 11** (greenhouse 1, lever 1,
  workday 3, smartrecruiters 3, icims 3).
- **Runner invocations:** 10 summary-producing runs (5-platform `final` +
  targeted reruns); 1 chrome-profile probe without a summary; **total 11
  invocations including the probe**.

**"7" is superseded and must not be carried forward.** Formal ≥5 real-job
attempts criterion is met under every authoritative measure (12/15/11).

## Lever interlock event attribution (reviewer-closure pass)

The `form_submit=1, submit_events=0, dispatch_submit_events=0` signature on
Lever means a site script called `HTMLFormElement.prototype.submit()`
programmatically (no submit event, no click, no dispatched SubmitEvent).
Proof it is not UAA-initiated:

- The run trace contains exactly one `page.evaluate` call — UAA's own final
  counters read (`call@1266`). UAA never evaluated a script containing
  `submit()`, never filled a field (`fields=[]`), and its only action was
  clicking "APPLY FOR THIS JOB" (a link navigation).
- It is deterministic: identical counters across all 4 Lever invocations
  (liveruns, rerun, rerun2, final).

Classification: **page/third-party-initiated programmatic form submit,
blocked by interlock with `submitted=false`**. Separate counters stay
reported: UAA submit clicks = 0, `submitted=false`.

## Safety verification

- Zero typed values, zero uploads, zero UAA submit clicks in all runs.
- Login-only pages reported as `login_required`, never treated as
  application forms (`is_application_form` excludes auth gates).
- The recon-only captcha exception stays narrowly scoped
  (`recon_only and analysis.is_application_form` and no auth gate).
- Lever run captured `wq7_interlock: blocked 1 submission attempt(s)`
  (`submit_events=0, form_submit=1, request_submit=0, dispatch=0`) —
  attributed to page/third-party JS `form.submit()`, not UAA (see above).
- KCI widget-selection fix is covered by a hermetic regression test; real
  KCI rerun post-fix reached `login_required` with `submitted=false`.

## Merged state

- PR #13: https://github.com/MohamedAzzam4/UniversalAutoApplier/pull/13
- PR head: `adc8c8dff25c8e1f4143d8db65292d223867a893`
- Merge commit: resolve dynamically with the command block at the top.
  At closure time `origin/main` was `cab7a13d0e15c06ae04b4c180d11920a9e70fb97`
  (merge of PR #13).
- Amended acceptance satisfied.
- All six required CI checks passed on the final PR head `adc8c8d`
  (Linux 3.11/3.12/3.13/3.14, Windows Core, Windows Playwright).
- Applications submitted: none.
- WQ-7C: NOT started.

## Changed files (merged via PR #13)

- `src/universal_auto_applier/navigator/apply_path_finder.py` — auth-gate
  first; **added `embed_rank` to prefer child-frame (widget) SAFE_APPLY**
- `src/universal_auto_applier/services/live_dry_run_platforms.py` (json mode dict)
- `tests/playwright/test_wq7b_recon_mode.py` (recon tests; **new
  `TestReconWidgetApplyPreference::test_prefers_widget_apply_over_shell_apply`**)
- `tests/unit/test_wq7_live_dry_run_platforms.py` (serialization regression)
- `tests/fixtures/recon/login_captcha.html` (new fixture for gate-vs-captcha precedence)
- `tests/fixtures/recon/icims_outside.html`, `icims_widget.html`,
  `agency_landing.html` (new hermetic shell-vs-widget apply fixtures)
- `docs/evidence/wq-7b/MANIFEST.md` (final evidence)

## Tests and exact results

- Contract suite (`pytest -m "not live and not playwright"`): **1209 passed,
  259 deselected**.
- Playwright suite (`pytest tests/playwright`): **256 passed** (authoritative
  re-run at the final amendment SHA; a first full-suite run showed 1 failure
  in the pre-existing load-timing-sensitive `TestAllowedBehavior`
  interlock test which passed in isolation and on re-run — unrelated to
  WQ-7B code).
- Aggregate reference (historical, superseded by the split): `pytest -m "not
  live"` = 1465 passed, 3 deselected (1209 + 256).
- Recon/blocker modules: pass (incl. login_captcha precedence).
- WQ-7B recon-mode tests: 14 passed (13 existing + 1 new widget-preference
  regression).
- `git diff --check`: clean. `ruff check`, `ruff format --check` (198/198),
  `pyright` (0/0/0): pass.
- Live recon runs (opt-in, not in CI): as recorded in MANIFEST and thumbnails above.
- Owner-amendment pass (2026-08-16): all six contract gates re-run and
  green at the final SHA (see MANIFEST validation section).
- Merge (2026-08-16): all six required CI checks passed on the final PR head
  `adc8c8d` (Linux 3.11/3.12/3.13/3.14, Windows Core, Windows Playwright).

## Decisions made

- Do not bypass account creation, anti-bot, or login walls — WQ-7B forbids
  this; SmartRecruiters failures are reported as externally blocked.
- **Owner amendment accepted 2026-08-16:** the original ≥3-distinct
  criterion was replaced by the two-platforms-plus-external-gate criterion
  described above; history of the original criterion and BLOCKED result is
  preserved, not rewritten.
- The PR for the amended/finalized branch was merged with a normal merge
  commit via reviewed PR #13; no squash/rebase/amend/force-push, no direct
  push to `main`.
- Only make a production fix when deterministically reproduced: the KCI
  shell-vs-widget ambiguity was reproduced with a hermetic failing test
  before editing `choose_safe_action` (fix: `embed_rank` preference, not a
  live-site-only change). The fix does not alter `allow_apply` /
  `allow_continue` priorities or submit safety.
- Never commit live-run artifacts (screenshots, traces, HTML, PDFs).
- Fabricated job URLs were rejected (both a SmartRecruiters 400 and a
  Workday 404 case proved this wastes time); real public jobs only, verified
  before each run.
- Attack-count terminology is exact in the manifest: target attempts
  (distinct URLs, 12 via runner / 15 incl. MCP checks) vs runner
  invocations (10) vs evidence rows (11). "7" is retired.

## Blockers / risks (resolved / persistent limitations)

- **Accepted-platform constraint:** 2 of 5 supported ATS platforms reach real
  forms; the other three are externally gated despite exhausted replacements.
  This was the explicit reason for the owner amendment and remains a
  documented limitation of live ATS reachability — not an unresolved UAA
  defect.
- A working guest-apply Workday/iCIMS tenant or bypass-free SmartRecruiters
  posting is not available within the WQ-7B target pool (forbidden to
  bypass, so none attempted).
- No objectionable production risk: the embed_rank fix was regression-tested
  and validated by the full gate suite.

## Next workpackage candidate

**WQ-7C — controlled synthetic field fill + synthetic document upload, with
final submission forbidden.**

This is only a readiness marker. WQ-7C is **not** defined or implemented in
this workpackage. The detailed WQ-7C contract will be issued separately by
the owner/reviewer.

## Rules

- Never merge or push to `main` directly. Preserve all `checkpoint/*`
  branches. No reset/clean/rebase/amend/force-push.
- Only commit reviewed files; never commit live-runs data, `.uaa_data`,
  `.env`, browsers/databases, screenshots, traces, or HTML snapshots.
- Do not embed a "current HEAD" SHA in this file; resolve it dynamically.