"""WQ-8 document bundle — browser execution with multiple file inputs."""

import hashlib
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.live_executor import execute_live_form
from universal_auto_applier.submission.models import build_snapshot_from_report


def _job_with_bundle(tmp_path: Path, bundle: dict) -> ApplicationJob:
    url = "https://example.test/jobs/bundle-exec"
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="bundle-exec", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Example",
        title="Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.QUEUED,
        external_job_id="bundle-exec",
        metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}},
    )


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


@pytest.mark.playwright
def test_bundle_with_multiple_input_uploads_both_in_one_call(tmp_path: Path) -> None:
    """With <input multiple>, CV+transcript are uploaded in ONE set_input_files call."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"cv bundle exec")
    tr = tmp_path / "transcript.pdf"
    tr.write_bytes(b"transcript bundle exec")
    bundle = {"files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr), "kind": "transcript"}]}
    job = _job_with_bundle(tmp_path, bundle)
    candidate = CandidateProfile()

    html = """
    <html><body>
      <form>
        <label>Vollständige Bewerbungsunterlagen</label>
        <input type="file" name="unterlagen" multiple />
        <button type="submit">Absenden</button>
      </form>
    </body></html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(html)
        execution = execute_live_form(page, candidate, job)
        # Both files must be uploaded
        assert len(execution.uploads) == 2
        paths = {u.path for u in execution.uploads}
        assert str(cv) in paths
        assert str(tr) in paths
        kinds = {u.document_kind for u in execution.uploads}
        assert "cv" in kinds
        assert "transcript" in kinds
        # Both have non-empty hashes via snapshot
        snap = build_snapshot_from_report(
            application_id=job.application_id,
            application_url=job.url,
            fields=execution.fields,
            uploads=execution.uploads,
            pending_intervention_count=0,
            submit_control_text="Absenden",
        )
        assert len(snap.documents) == 2
        hashes = {d.content_hash for d in snap.documents}
        assert _hash(cv) in hashes
        assert _hash(tr) in hashes
        # Review plan binds both hashes
        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )

        plan = build_review_plan(
            application_id=job.application_id,
            company=job.company,
            job_title=job.title,
            application_url=job.url,
            fields=snap.fields,
            documents=snap.documents,
            submit_control_text="Absenden",
            submit_control_selector="button",
            submit_control_frame_url="",
            pending_intervention_count=0,
        )
        h1 = compute_review_plan_hash(plan)
        browser.close()
    # Changing one document must change hash
    tr2 = tmp_path / "transcript2.pdf"
    tr2.write_bytes(b"different transcript")
    bundle2 = {"files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr2), "kind": "transcript"}]}
    job2 = _job_with_bundle(tmp_path, bundle2)
    with sync_playwright() as p2:
        browser2 = p2.chromium.launch(headless=True)
        ctx2 = browser2.new_context()
        page2 = ctx2.new_page()
        page2.set_content(html)
        exec2 = execute_live_form(page2, candidate, job2)
        snap2 = build_snapshot_from_report(
            application_id=job2.application_id,
            application_url=job2.url,
            fields=exec2.fields,
            uploads=exec2.uploads,
            pending_intervention_count=0,
            submit_control_text="Absenden",
        )
        plan2 = build_review_plan(
            application_id=job2.application_id,
            company=job2.company,
            job_title=job2.title,
            application_url=job2.url,
            fields=snap2.fields,
            documents=snap2.documents,
            submit_control_text="Absenden",
            submit_control_selector="button",
            submit_control_frame_url="",
            pending_intervention_count=0,
        )
        h2 = compute_review_plan_hash(plan2)
        assert h1 != h2
        browser2.close()


@pytest.mark.playwright
def test_bundle_without_multiple_is_rejected(tmp_path: Path) -> None:
    """Without multiple, two-file bundle is rejected, no partial upload."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"cv")
    tr = tmp_path / "transcript.pdf"
    tr.write_bytes(b"transcript")
    bundle = {"files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr), "kind": "transcript"}]}
    job = _job_with_bundle(tmp_path, bundle)
    candidate = CandidateProfile()
    html = """
    <html><body>
      <form>
        <label>Vollständige Bewerbungsunterlagen</label>
        <input type="file" name="unterlagen" />
        <button type="submit">Absenden</button>
      </form>
    </body></html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(html)
        execution = execute_live_form(page, candidate, job)
        # Must be intervention_needed, not filled, and no successful uploads
        file_fields = [f for f in execution.fields if f.field_type == "file"]
        assert len(file_fields) == 1
        assert file_fields[0].status == "intervention_needed"
        # No uploaded records for failed bundle (no partial)
        uploaded = [u for u in execution.uploads if u.status == "uploaded"]
        assert len(uploaded) == 0
        # No snapshot falsely claims complete package — snapshot will have 0 docs
        snap = build_snapshot_from_report(
            application_id=job.application_id,
            application_url=job.url,
            fields=execution.fields,
            uploads=execution.uploads,
            pending_intervention_count=0,
            submit_control_text="Absenden",
        )
        # Documents should be 0 because uploads were not successful
        assert len(snap.documents) == 0
        browser.close()
