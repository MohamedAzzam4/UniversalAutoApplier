# Controlled Real Submission — Local Test Plan

This document provides an **exact staged procedure** for testing the
controlled final submission feature locally using your existing UAA data
and browser profile.

> **WARNING**: This procedure submits a **real** job application to a
> **real** ATS. Follow every step in order. Do not skip the backup step.
> Do not enable real submission until you have verified the review-only
> snapshot.

## Prerequisites

1. **UAA data directory**: your existing `UAA_DATA_DIR` with at least one
   job in `review_ready` status.
2. **Browser profile**: your existing `UAA_BROWSER_PROFILE_DIR` with
   active login sessions for the target ATS.
3. **Checkpoint branch**: `checkpoint/controlled-final-submission`
   checked out and up to date.
4. **Database backup**: see step 3 below.
5. **Python environment**: `source .venv/bin/activate` with all
   dependencies installed.

## Stage 1 — Pull and verify the checkpoint branch

```bash
cd /path/to/UniversalAutoApplier
git fetch origin --prune
git checkout checkpoint/controlled-final-submission
git pull --ff-only origin checkpoint/controlled-final-submission
git rev-parse HEAD
# Verify the working tree is clean.
git status
```

## Stage 2 — Select one application to approve

```bash
python -m universal_auto_applier list-jobs
```

Pick one job in `review_ready` status. Note its application ID prefix
(the first 12 characters).

## Stage 3 — Back up the database and run artifacts

```bash
cp -r "$UAA_DATA_DIR" "$UAA_DATA_DIR.backup.$(date +%Y%m%d%H%M%S)"
```

This backup lets you restore the pre-submission state if anything goes
wrong.

## Stage 4 — Run review-only mode first

```bash
python -m universal_auto_applier live-dry-run \
  --application-id <PREFIX> \
  --headed
```

**Verify**:
- The runner opens the job URL.
- It fills the form (deterministic + LLM).
- It stops at `review_ready` (does NOT click submit).
- The report has `submitted: false`.
- The `data-submitted` attribute on the page body is still `"false"`.

## Stage 5 — Verify fields, documents, interventions, and snapshot

Check the latest run report under `$UAA_DATA_DIR/live-runs/`:

```bash
ls -lt "$UAA_DATA_DIR/live-runs/" | head -5
cat "$UAA_DATA_DIR/live-runs/<latest-dir>/report.json" | python -m json.tool
```

**Verify**:
- All fields are filled or have interventions.
- Documents (cv_pdf, cover_letter_pdf) are uploaded.
- No pending interventions remain (resolve them first if any).
- The report status is `review_ready`.

## Stage 6 — Enable real submission temporarily

```bash
export UAA_ENABLE_REAL_SUBMISSION=true
```

**Verify** the setting is loaded:

```bash
python -c "from universal_auto_applier.config import load_settings; s = load_settings(); print('enable_real_submission:', s.enable_real_submission)"
# Must print: enable_real_submission: True
```

## Stage 7 — Approve the exact snapshot

Start the dashboard:

```bash
python -m universal_auto_applier
```

Open the dashboard at `http://127.0.0.1:8000/`.

1. Go to the **Submit** view.
2. Enter the application ID.
3. Click **Load Snapshot**.
4. Review the snapshot: fields, documents, URL, submit control.
5. Click **Approve Snapshot**.
6. Note the **approval ID** that appears.

**Alternatively**, use the API directly:

```bash
curl -X POST http://127.0.0.1:8000/api/submit/<APPLICATION_ID>/approve \
  -H "Content-Type: application/json" \
  -d '{"snapshot": {...}, "confirm": true}'
```

## Stage 8 — Submit once

Use the CLI to execute the controlled submission:

```bash
python -m universal_auto_applier live-submit \
  --application-id <PREFIX> \
  --approval-id <APPROVAL_ID> \
  --confirm \
  --headed
```

**Verify**:
- The CLI prints "Gates passed. Snapshot hash matches approval."
- The browser opens, navigates to the job URL, fills the form.
- The coordinator clicks the submit control **once**.
- The CLI prints the result state.
- If `submitted_confirmed`: the ATS shows a confirmation/thank-you page.
- If `outcome_unknown`: the CLI prints a warning and exits with code 2.
  **Do not retry automatically.** Review the evidence manually.

## Stage 9 — Verify ATS confirmation and stored evidence

Check the post-submit artifacts:

```bash
ls -lt "$UAA_DATA_DIR/live-runs/" | head -5
cat "$UAA_DATA_DIR/live-runs/<latest-submit-dir>/report.json" | python -m json.tool
```

**Verify**:
- `pre-submit.png` exists (screenshot before the click).
- `post-submit.png` exists (screenshot after the click).
- `post-submit.html` exists (DOM snapshot after the click).
- The post-submit URL is a confirmation/thank-you page.
- The application status in the DB is now `submitted` (not `applied`
  unless the ATS provides a reference number — see step 10).

Check the DB:

```bash
python -c "
from universal_auto_applier.config import load_settings
from universal_auto_applier.persistence.db import build_engine_url, make_engine, make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import get_application_job
s = load_settings()
e = make_engine(build_engine_url(s.data_dir / 'uaa.sqlite'))
sf = make_session_factory(e)
with session_scope(sf) as session:
    job = get_application_job(session, '<APPLICATION_ID>')
    print('status:', job.status)
e.dispose()
"
```

## Stage 10 — Disable real submission again

```bash
unset UAA_ENABLE_REAL_SUBMISSION
```

**Verify**:

```bash
python -c "from universal_auto_applier.config import load_settings; s = load_settings(); print('enable_real_submission:', s.enable_real_submission)"
# Must print: enable_real_submission: False
```

## Stage 11 — Verify duplicate prevention

Try to submit the same application again:

```bash
export UAA_ENABLE_REAL_SUBMISSION=true
python -m universal_auto_applier live-submit \
  --application-id <PREFIX> \
  --approval-id <APPROVAL_ID> \
  --confirm \
  --headed
```

**Verify**:
- The CLI prints "ERROR: application status is submitted" (or
  "already_submitted").
- No second click occurs.
- The browser does not open (or opens but does not click).

```bash
unset UAA_ENABLE_REAL_SUBMISSION
```

## Rollback (if something went wrong)

If the submission failed or you want to restore the pre-submission state:

```bash
# Stop the UAA server.
# Restore the backup:
rm -rf "$UAA_DATA_DIR"
mv "$UAA_DATA_DIR.backup.<TIMESTAMP>" "$UAA_DATA_DIR"
```

**Note**: Restoring the backup does NOT un-submit the application on the
ATS side. If the click happened, the application was submitted. The
backup only restores your local UAA state.

## Confirmation

No real external submission was attempted from the GLM sandbox. All
implementation and testing used local HTML fixtures only. The real
submission must be performed by the user following this procedure on
their local machine.
