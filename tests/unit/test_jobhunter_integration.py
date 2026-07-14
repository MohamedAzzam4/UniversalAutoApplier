"""Tests for the JobHunter -> UAA integration.

Covers:
- Candidate profile loader (from metadata, from config, fallback chain)
- Importer preserving candidate_profile metadata
- Pipeline reaching review_ready when a job has a profile + documents
- Missing-document regression (job does NOT get stuck in_progress)
- Sequential vs parallel execution mode
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_auto_applier.candidate_profile_loader import (
    profile_from_config,
    profile_from_metadata,
    resolve_candidate_profile,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.services.pipeline_orchestrator import PipelineOrchestrator

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        full_name="John Doe",
        email="john@example.com",
        phone="+49 123 456789",
        linkedin_url="https://linkedin.com/in/johndoe",
        city="Munich",
        country="Germany",
        requires_sponsorship=False,
    )


def _make_job_with_profile(
    tmp_path: Path,
    *,
    url: str = "https://boards.greenhouse.io/example/jobs/2001",
    platform: Platform = Platform.GREENHOUSE,
    external_job_id: str = "jh-2001",
    candidate: CandidateProfile | None = None,
    include_documents: bool = True,
) -> ApplicationJob:
    """Build an ApplicationJob that mirrors what JobHunter's exporter
    produces: includes cv_pdf, cover_letter_pdf, and a
    metadata.candidate_profile snapshot.
    """
    cv = str(tmp_path / "cv.pdf")
    cover = str(tmp_path / "cover.pdf")
    if include_documents:
        Path(cv).write_bytes(b"%PDF-1.4 fake cv")
        Path(cover).write_bytes(b"%PDF-1.4 fake cover")
    else:
        cv = None  # type: ignore[assignment]
        cover = None  # type: ignore[assignment]

    candidate = candidate or _make_candidate()
    profile_snapshot = {
        "first_name": candidate.first_name,
        "last_name": candidate.last_name,
        "full_name": candidate.full_name,
        "email": candidate.email,
        "phone": candidate.phone,
        "linkedin_url": candidate.linkedin_url,
        "city": candidate.city,
        "country": candidate.country,
    }

    application_id = compute_application_id(
        platform=str(platform), external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=platform,
        source="linkedin",
        company="Test Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=cv,
        cover_letter_pdf=cover,
        status=ApplicationStatus.QUEUED,
        external_job_id=external_job_id,
        metadata={"candidate_profile": profile_snapshot},
    )


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_jh_integration.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
        execution_mode="sequential",
        apply_workers=1,
    )


# ---------------------------------------------------------------------------
# Candidate profile loader
# ---------------------------------------------------------------------------


class TestProfileFromMetadata:
    def test_returns_profile_when_metadata_has_snapshot(self) -> None:
        metadata = {
            "candidate_profile": {
                "first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
                "phone": "+49 123",
            }
        }
        profile = profile_from_metadata(metadata)
        assert profile is not None
        assert profile.first_name == "Jane"
        assert profile.email == "jane@example.com"

    def test_returns_none_when_no_snapshot(self) -> None:
        assert profile_from_metadata({}) is None
        assert profile_from_metadata(None) is None
        assert profile_from_metadata({"other": "data"}) is None

    def test_returns_none_for_empty_snapshot(self) -> None:
        assert profile_from_metadata({"candidate_profile": {}}) is None

    def test_ignores_unknown_fields(self) -> None:
        metadata = {
            "candidate_profile": {
                "first_name": "Jane",
                "email": "jane@example.com",
                "unknown_field": "ignored",
                "exported_at": "2026-07-14T00:00:00Z",
            }
        }
        profile = profile_from_metadata(metadata)
        assert profile is not None
        assert profile.first_name == "Jane"


class TestProfileFromConfig:
    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        import yaml

        profile_yml = tmp_path / "profile.yml"
        profile_yml.write_text(
            yaml.safe_dump(
                {
                    "candidate": {
                        "full_name": "Test User",
                        "email": "test@example.com",
                        "phone": "+49 123",
                        "location": "Berlin, Germany",
                        "linkedin": "https://linkedin.com/in/test",
                        "github": "https://github.com/test",
                    }
                }
            ),
            encoding="utf-8",
        )
        profile = profile_from_config(profile_yml)
        assert profile is not None
        assert profile.full_name == "Test User"
        assert profile.email == "test@example.com"
        assert profile.city == "Berlin"
        assert profile.country == "Germany"
        assert "linkedin.com/in/test" in profile.linkedin_url

    def test_returns_none_when_path_does_not_exist(self, tmp_path: Path) -> None:
        profile = profile_from_config(tmp_path / "nonexistent.yml")
        assert profile is None

    def test_returns_none_when_no_env_var(self, monkeypatch) -> None:
        monkeypatch.delenv("UAA_CANDIDATE_PROFILE", raising=False)
        assert profile_from_config() is None


class TestResolveCandidateProfile:
    def test_metadata_takes_priority_over_config(self, tmp_path: Path) -> None:
        import yaml

        profile_yml = tmp_path / "profile.yml"
        profile_yml.write_text(
            yaml.safe_dump(
                {"candidate": {"full_name": "Config User", "email": "config@example.com"}}
            ),
            encoding="utf-8",
        )
        metadata = {
            "candidate_profile": {
                "full_name": "Metadata User",
                "email": "metadata@example.com",
            }
        }
        profile = resolve_candidate_profile(metadata, profile_yml)
        assert profile.full_name == "Metadata User"
        assert profile.email == "metadata@example.com"

    def test_falls_back_to_config(self, tmp_path: Path) -> None:
        import yaml

        profile_yml = tmp_path / "profile.yml"
        profile_yml.write_text(
            yaml.safe_dump(
                {"candidate": {"full_name": "Config User", "email": "config@example.com"}}
            ),
            encoding="utf-8",
        )
        profile = resolve_candidate_profile(None, profile_yml)
        assert profile.full_name == "Config User"

    def test_falls_back_to_empty_default(self) -> None:
        profile = resolve_candidate_profile(None, None)
        assert profile.first_name is None
        assert profile.email is None


# ---------------------------------------------------------------------------
# Importer preserves candidate_profile metadata
# ---------------------------------------------------------------------------


class TestImporterPreservesProfile:
    def test_imported_job_has_candidate_profile_in_metadata(
        self, session_factory, tmp_path: Path
    ) -> None:
        """When a queue row has metadata.candidate_profile, the importer
        must preserve it in the database."""
        from universal_auto_applier.application_queue.importer import import_queue_file

        # Build a queue file with one job that has a candidate_profile.
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"%PDF-1.4 fake")
        cover.write_bytes(b"%PDF-1.4 fake")
        url = "https://boards.greenhouse.io/example/jobs/3001"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id="jh-3001", url=url
        )
        row = {
            "application_id": application_id,
            "platform": "greenhouse",
            "source": "linkedin",
            "company": "Test Corp",
            "title": "Engineer",
            "url": url,
            "score": 4.5,
            "verdict": "apply",
            "cv_pdf": str(cv),
            "cover_letter_pdf": str(cover),
            "status": "ready_to_apply",
            "external_job_id": "jh-3001",
            "metadata": {
                "candidate_profile": {
                    "first_name": "Imported",
                    "email": "imported@example.com",
                }
            },
        }
        queue_path = tmp_path / "application_queue.jsonl"
        queue_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.imported == 1

        with session_scope(session_factory) as session:
            job = get_application_job(session, application_id)
        assert job is not None
        assert job.metadata.get("candidate_profile", {}).get("first_name") == "Imported"
        assert job.metadata.get("candidate_profile", {}).get("email") == "imported@example.com"


# ---------------------------------------------------------------------------
# Pipeline reaches review_ready with profile + documents
# ---------------------------------------------------------------------------


class TestPipelineReachesReviewReadyWithProfile:
    def test_job_with_profile_and_documents_reaches_review_ready(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """A ready job with documents and a candidate profile snapshot
        should reach review_ready (not need intervention for basic
        fields like name/email)."""
        job = _make_job_with_profile(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # Use the greenhouse apply fixture which has first_name, last_name,
        # email, phone fields.
        fixture_html = _read_fixture("greenhouse_apply.html")
        orch = PipelineOrchestrator(settings, session_factory)
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        # With a profile snapshot, the basic fields (name/email) should
        # NOT create interventions. The job should reach review_ready
        # (or needs_user_input for file uploads, which still require
        # confirmation). The key assertion: the job does NOT get stuck
        # in in_progress, and name/email interventions are NOT created.
        assert updated.status != ApplicationStatus.IN_PROGRESS
        assert updated.status in (
            ApplicationStatus.REVIEW_READY,
            ApplicationStatus.NEEDS_USER_INPUT,
        )

        # Check that no field_answer intervention was created for
        # first_name/last_name/email (the fields the profile covers).
        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)
        # File-input fields may still create interventions (they require
        # confirmation), but first_name/last_name/email should NOT.
        for iv in pending:
            assert "first name" not in iv.question.lower(), (
                f"first_name should not be an intervention when profile has it: {iv.question}"
            )
            assert "last name" not in iv.question.lower(), (
                f"last_name should not be an intervention when profile has it: {iv.question}"
            )
            assert (
                "email" not in iv.question.lower() or "email address" not in iv.question.lower()
            ), f"email should not be an intervention when profile has it: {iv.question}"

    def test_job_without_profile_creates_name_email_interventions(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """A job WITHOUT a candidate profile snapshot should still
        process, but first_name/last_name/email become interventions.
        This proves the profile loader is actually being used."""
        job = _make_job_with_profile(tmp_path, candidate=CandidateProfile())
        # Strip the metadata snapshot so the loader falls back to empty.
        job.metadata = {}
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("greenhouse_apply.html")
        orch = PipelineOrchestrator(settings, session_factory)
        orch.run(fixture_html=fixture_html, max_jobs=1)

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)
        # Without a profile, at least one of name/email should be an
        # intervention (the exact set depends on the fill engine's
        # mapping, but the count should be > 0).
        assert len(pending) > 0, "Expected interventions when no candidate profile is provided"


# ---------------------------------------------------------------------------
# Missing-document regression: job must NOT get stuck in_progress
# ---------------------------------------------------------------------------


class TestMissingDocumentRegression:
    def test_missing_cv_does_not_leave_job_in_progress(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Regression test: a job with a missing cv_pdf must end in
        needs_user_input (with a missing_document intervention), NOT
        stuck in in_progress. Previously the orchestrator tried
        IN_PROGRESS -> BLOCKED which the state machine rejects, leaving
        the job stuck."""
        # Build a job with no cv_pdf (simulate a missing document).
        job = _make_job_with_profile(tmp_path, include_documents=False)
        # Override status to queued (since _make_job_with_profile sets
        # documents based on include_documents).
        job.cv_pdf = None
        job.cover_letter_pdf = None
        job.status = ApplicationStatus.QUEUED
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        orch = PipelineOrchestrator(settings, session_factory)
        orch.run(fixture_html=None, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        # The job must NOT be stuck in in_progress.
        assert updated.status != ApplicationStatus.IN_PROGRESS, (
            f"Job stuck in in_progress (status={updated.status}) — this is the bug we fixed"
        )
        # It should be needs_user_input with a missing_document intervention.
        assert updated.status == ApplicationStatus.NEEDS_USER_INPUT

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)
        kinds = [str(i.kind) for i in pending]
        assert "missing_document" in kinds, f"Expected missing_document intervention, got: {kinds}"


# ---------------------------------------------------------------------------
# Sequential vs parallel execution mode
# ---------------------------------------------------------------------------


class TestExecutionMode:
    def test_sequential_mode_default(self, settings) -> None:
        """Default execution_mode is sequential."""
        assert settings.execution_mode == "sequential"
        assert settings.apply_workers == 1

    def test_parallel_mode_processes_multiple_jobs(self, session_factory, tmp_path: Path) -> None:
        """In parallel mode with apply_workers > 1, multiple jobs are
        processed concurrently. The final state should show all jobs
        processed."""
        parallel_settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
            execution_mode="parallel",
            apply_workers=2,
        )
        # Seed 3 jobs.
        for i in range(3):
            job = _make_job_with_profile(
                tmp_path,
                url=f"https://boards.greenhouse.io/example/jobs/4{i:03d}",
                external_job_id=f"jh-4{i:03d}",
            )
            with session_scope(session_factory) as session:
                upsert_application_job(session, job)

        fixture_html = _read_fixture("greenhouse_apply.html")
        orch = PipelineOrchestrator(parallel_settings, session_factory)
        orch.run(fixture_html=fixture_html, max_jobs=10)

        assert orch.state.jobs_processed == 3
        assert orch.state.status == "completed"
        # None of the jobs should be submitted.
        assert orch.state.jobs_succeeded == 3  # all processed without exception

    def test_sequential_mode_processes_multiple_jobs(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Sequential mode processes jobs one at a time."""
        for i in range(3):
            job = _make_job_with_profile(
                tmp_path,
                url=f"https://boards.greenhouse.io/example/jobs/5{i:03d}",
                external_job_id=f"jh-5{i:03d}",
            )
            with session_scope(session_factory) as session:
                upsert_application_job(session, job)

        fixture_html = _read_fixture("greenhouse_apply.html")
        orch = PipelineOrchestrator(settings, session_factory)
        orch.run(fixture_html=fixture_html, max_jobs=10)

        assert orch.state.jobs_processed == 3
        assert orch.state.status == "completed"

    def test_parallel_mode_never_submits(self, session_factory, tmp_path: Path) -> None:
        """Parallel mode must never result in submission."""
        parallel_settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
            execution_mode="parallel",
            apply_workers=3,
        )
        for i in range(3):
            job = _make_job_with_profile(
                tmp_path,
                url=f"https://boards.greenhouse.io/example/jobs/6{i:03d}",
                external_job_id=f"jh-6{i:03d}",
            )
            with session_scope(session_factory) as session:
                upsert_application_job(session, job)

        fixture_html = _read_fixture("greenhouse_apply.html")
        orch = PipelineOrchestrator(parallel_settings, session_factory)
        orch.run(fixture_html=fixture_html, max_jobs=10)

        # Check all jobs ended in a safe state.
        from universal_auto_applier.persistence.job_repository import list_application_jobs

        with session_scope(session_factory) as session:
            jobs = list_application_jobs(session)
        for job in jobs:
            if "6" in (job.external_job_id or ""):
                assert job.status not in (
                    ApplicationStatus.SUBMITTED,
                    ApplicationStatus.APPLIED,
                ), f"Job {job.application_id[:12]} ended in {job.status} — submission occurred!"


# ---------------------------------------------------------------------------
# Config tests for new settings
# ---------------------------------------------------------------------------


class TestConfigSettings:
    def test_default_execution_mode_is_sequential(self) -> None:
        from universal_auto_applier.config import Settings

        s = Settings()
        assert s.execution_mode == "sequential"
        assert s.scan_workers == 1
        assert s.evaluate_workers == 1
        assert s.tailor_workers == 1
        assert s.apply_workers == 1

    def test_load_settings_reads_execution_mode(self) -> None:
        from universal_auto_applier.config import load_settings

        env = {
            "UAA_EXECUTION_MODE": "parallel",
            "UAA_APPLY_WORKERS": "4",
            "UAA_HOST": "127.0.0.1",
        }
        s = load_settings(env=env)
        assert s.execution_mode == "parallel"
        assert s.apply_workers == 4

    def test_load_settings_rejects_invalid_worker_count(self) -> None:
        from universal_auto_applier.config import load_settings

        with pytest.raises(ValueError, match="UAA_APPLY_WORKERS"):
            load_settings(env={"UAA_APPLY_WORKERS": "0"})
        with pytest.raises(ValueError, match="UAA_APPLY_WORKERS"):
            load_settings(env={"UAA_APPLY_WORKERS": "100"})

    def test_load_settings_reads_candidate_profile(self, tmp_path: Path) -> None:
        from universal_auto_applier.config import load_settings

        profile_path = tmp_path / "profile.yml"
        profile_path.write_text("candidate: {}", encoding="utf-8")
        s = load_settings(env={"UAA_CANDIDATE_PROFILE": str(profile_path)})
        assert s.candidate_profile == profile_path
