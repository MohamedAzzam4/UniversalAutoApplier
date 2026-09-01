"""WQ-8 URL fallback hardening — no silent job.url fallback for WQ-8."""

from pathlib import Path

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.submission.models import SubmissionSnapshot
from universal_auto_applier.submission.store import create_approval


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8330,
        data_dir=tmp_path / "uaa_url_fallback",
        browser_headless=True,
    )


def _job(url: str) -> ApplicationJob:
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="url-fallback", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Co",
        title="Role",
        url=url,
        verdict="apply",
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="url-fallback",
    )


def test_review_packet_fails_when_snapshot_has_no_url(tmp_path: Path) -> None:
    """WQ-8 review packet must fail closed if snapshot has empty application_url."""
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    detail_url = "https://example.test/jobs/1"
    job = _job(detail_url)
    # Snapshot with empty URL (legacy empty or corrupted)
    snap = SubmissionSnapshot(
        application_id=job.application_id,
        application_url="",  # empty!
        fields=[],
        documents=[],
        pending_intervention_count=0,
    ).with_hashes()
    with session_scope(sf) as s:
        upsert_application_job(s, job)
        create_approval(s, application_id=job.application_id, snapshot=snap)
    # CLI should fail closed, not fallback to job.url
    from universal_auto_applier.cli import run_command

    rc = run_command(["wq8-review-packet", "--application-id", job.application_id], settings)
    assert rc == 2
    engine.dispose()


def test_authorize_fails_when_snapshot_has_no_url(tmp_path: Path) -> None:
    """WQ-8 authorize must fail closed if snapshot has empty URL."""
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    detail_url = "https://example.test/jobs/1"
    job = _job(detail_url)
    snap = SubmissionSnapshot(
        application_id=job.application_id,
        application_url="",  # empty
        fields=[],
        documents=[],
        pending_intervention_count=0,
    ).with_hashes()
    with session_scope(sf) as s:
        upsert_application_job(s, job)
        create_approval(s, application_id=job.application_id, snapshot=snap)

    from universal_auto_applier.cli import run_command

    # Need to compute a fake hash to pass --review-plan-hash, but the code should fail before hash check
    # because snapshot has no URL
    settings_real = settings.model_copy(update={"enable_real_submission": True})
    rc = run_command(
        [
            "wq8-authorize",
            "--application-id",
            job.application_id,
            "--review-plan-hash",
            "deadbeef",
            "--confirm",
        ],
        settings_real,
    )
    assert rc == 2
    engine.dispose()


def test_coordinator_does_not_fallback_to_job_url(tmp_path: Path) -> None:
    """Coordinator early gate must not compare auth URL against job.url."""
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    detail_url = "https://example.test/jobs/detail"
    form_url = "https://example.test/jobs/1/form"
    job = _job(detail_url)
    # Create a snapshot at form_url and authorize it
    from universal_auto_applier.submission.models import SubmissionSnapshotField

    snap = SubmissionSnapshot(
        application_id=job.application_id,
        application_url=form_url,
        fields=[
            SubmissionSnapshotField(
                field_token="lf-abc",
                label="Name",
                field_type="text",
                filled_value="A",
                status="filled",
            )
        ],
        documents=[],
        pending_intervention_count=0,
    ).with_hashes()
    with session_scope(sf) as s:
        upsert_application_job(s, job)
        create_approval(s, application_id=job.application_id, snapshot=snap)
        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )

        plan = build_review_plan(
            application_id=job.application_id,
            company=job.company,
            job_title=job.title,
            application_url=form_url,
            fields=snap.fields,
            documents=snap.documents,
            submit_control_text="",
            submit_control_selector="",
            submit_control_frame_url="",
            pending_intervention_count=0,
        )
        h = compute_review_plan_hash(plan)
        from datetime import UTC, datetime, timedelta

        from universal_auto_applier.submission.authorization_store import create_authorization

        create_authorization(
            s,
            application_id=job.application_id,
            application_url=form_url,
            job_company=job.company,
            job_title=job.title,
            review_plan_hash=h,
            document_hashes=[],
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    # Coordinator early gate with no current_snapshot should NOT use job.url (detail) to compare
    # It should either defer or compare against approved snapshot's URL (form_url), not detail_url
    from universal_auto_applier.submission.coordinator import SubmissionCoordinator

    # Early gate with no snapshot — should be allowed to proceed (auth is active, but URL check deferred)
    # It should NOT return APPROVAL_STALE due to job.url mismatch
    # Enable real submission to test URL logic
    settings2 = settings.model_copy(update={"enable_real_submission": True})
    coord2 = SubmissionCoordinator(settings2, sf)
    gate2 = coord2.check_gates(application_id=job.application_id, current_snapshot=None)
    # With enable_real_submission true and no current_snapshot, the WQ-8 URL gate should NOT fail due to detail_url mismatch
    # It should either be None (deferred) or not stale. We check it is not ApprovalStale due to job.url.
    if not gate2.allowed:
        assert (
            "authorization URL does not match the current application form URL" not in gate2.reason
        )
        assert "job.url" not in gate2.reason
    engine.dispose()
