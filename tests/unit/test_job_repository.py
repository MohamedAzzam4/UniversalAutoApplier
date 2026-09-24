"""Tests for :mod:`universal_auto_applier.persistence.job_repository`.

Covers idempotent upsert, timestamp preservation, and status transition guards.
"""

from __future__ import annotations

import time
from datetime import UTC
from pathlib import Path

import pytest

from universal_auto_applier.core.eligibility import repeat_processing_block_reason
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, ApplicationJobDocuments
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    count_application_jobs,
    get_application_job,
    list_application_jobs,
    set_manual_submitted,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    """Return a session factory bound to a fresh temp SQLite DB.

    Uses NullPool to avoid ResourceWarning: unclosed database on Python 3.14.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_repo.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _make_job(
    *,
    url: str = "https://example.com/jobs/123",
    external_job_id: str = "job-123",
    company: str = "Example GmbH",
    title: str = "Working Student AI",
    status: ApplicationStatus = ApplicationStatus.EVALUATED,
    cv_pdf: str | None = None,
    cover_letter_pdf: str | None = None,
    score: float = 4.1,
) -> ApplicationJob:
    application_id = compute_application_id(
        platform="greenhouse", external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company=company,
        title=title,
        url=url,
        location="Munich, Germany",
        job_description="Full JD",
        score=score,
        verdict="apply",
        cv_pdf=cv_pdf,
        cover_letter_pdf=cover_letter_pdf,
        status=status,
        external_job_id=external_job_id,
    )


class TestUpsertIdempotent:
    def test_insert_new_job(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            row = upsert_application_job(session, job)

        assert row.application_id == job.application_id
        assert row.company == "Example GmbH"
        assert row.first_seen_at is not None
        assert row.last_updated_at is not None

    def test_upsert_same_job_twice_does_not_duplicate(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        with session_scope(session_factory) as session:
            assert count_application_jobs(session) == 1

    def test_upsert_updates_descriptive_fields(self, session_factory) -> None:
        job = _make_job(company="Acme Corp", score=3.5)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        updated_job = _make_job(company="Acme Corporation", score=4.8)
        with session_scope(session_factory) as session:
            upsert_application_job(session, updated_job)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.company == "Acme Corporation"
        assert retrieved.score == 4.8

    def test_reimport_preserves_manual_submission_and_omitted_answer_maps(
        self,
        session_factory,
    ) -> None:
        job = _make_job().model_copy(
            update={
                "metadata": {
                    "candidate_profile": {"first_name": "Old"},
                    "producer_only_old": "remove me",
                    "application_answers": {"What is your role?": "Developer"},
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
            set_manual_submitted(session, job.application_id, submitted=True)
            existing = get_application_job(session, job.application_id)
            assert existing is not None
            submitted_at = existing.metadata["dashboard_submitted_at"]
            existing.metadata["form_answers"] = {"Do you know Python?": "Yes"}
            existing.metadata["question_answers"] = {"Will you relocate?": "No"}
            upsert_application_job(session, existing)

        reimported = _make_job().model_copy(
            update={
                "metadata": {
                    "candidate_profile": {"first_name": "Updated"},
                    "producer_only_new": "keep me",
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, reimported)
            persisted = get_application_job(session, job.application_id)

        assert persisted is not None
        assert persisted.metadata["dashboard_submitted"] is True
        assert persisted.metadata["dashboard_submitted_at"] == submitted_at
        assert persisted.metadata["form_answers"] == {"Do you know Python?": "Yes"}
        assert persisted.metadata["question_answers"] == {"Will you relocate?": "No"}
        assert persisted.metadata["application_answers"] == {"What is your role?": "Developer"}
        assert persisted.metadata["candidate_profile"] == {"first_name": "Updated"}
        assert persisted.metadata["producer_only_new"] == "keep me"
        assert "producer_only_old" not in persisted.metadata
        assert repeat_processing_block_reason(persisted) == (
            "application is marked submitted by the operator"
        )

    def test_reimport_replaces_answer_maps_explicitly_supplied_by_producer(
        self,
        session_factory,
    ) -> None:
        job = _make_job().model_copy(
            update={"metadata": {"form_answers": {"Local correction": "No"}}}
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        reimported = _make_job().model_copy(
            update={"metadata": {"form_answers": {"Producer answer": "Yes"}}}
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, reimported)
            persisted = get_application_job(session, job.application_id)

        assert persisted is not None
        assert persisted.metadata["form_answers"] == {"Producer answer": "Yes"}

    def test_insert_does_not_accept_producer_dashboard_submission_marker(
        self,
        session_factory,
    ) -> None:
        job = _make_job().model_copy(
            update={
                "metadata": {
                    "dashboard_submitted": True,
                    "dashboard_submitted_at": "producer-value",
                    "candidate_profile": {"first_name": "John"},
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
            persisted = get_application_job(session, job.application_id)

        assert persisted is not None
        assert "dashboard_submitted" not in persisted.metadata
        assert "dashboard_submitted_at" not in persisted.metadata
        assert persisted.metadata["candidate_profile"] == {"first_name": "John"}

    def test_reimport_preserves_manually_cleared_submission_marker(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
            set_manual_submitted(session, job.application_id, submitted=True)
            set_manual_submitted(session, job.application_id, submitted=False)
            cleared = get_application_job(session, job.application_id)
            assert cleared is not None
            cleared_at = cleared.metadata["dashboard_submitted_at"]

        reimported = _make_job().model_copy(
            update={
                "metadata": {
                    "candidate_profile": {"first_name": "Updated"},
                    "dashboard_submitted": True,
                    "dashboard_submitted_at": "producer-value",
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, reimported)
            persisted = get_application_job(session, job.application_id)

        assert persisted is not None
        assert persisted.metadata["dashboard_submitted"] is False
        assert persisted.metadata["dashboard_submitted_at"] == cleared_at
        assert persisted.metadata["candidate_profile"] == {"first_name": "Updated"}
        assert repeat_processing_block_reason(persisted) is None


class TestTimestampPreservation:
    def test_first_seen_at_preserved_on_update(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            row1 = upsert_application_job(session, job)
        first_seen_1 = row1.first_seen_at

        # Wait a moment so last_updated_at would differ.
        time.sleep(0.05)

        with session_scope(session_factory) as session:
            row2 = upsert_application_job(session, job)
        first_seen_2 = row2.first_seen_at

        # Compare as UTC-aware datetimes (SQLite may drop tzinfo on read).
        fs1 = first_seen_1 if first_seen_1.tzinfo else first_seen_1.replace(tzinfo=UTC)
        fs2 = first_seen_2 if first_seen_2.tzinfo else first_seen_2.replace(tzinfo=UTC)
        assert fs1 == fs2  # preserved

    def test_last_updated_at_refreshed_on_update(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            row1 = upsert_application_job(session, job)
        last_updated_1 = row1.last_updated_at

        time.sleep(0.05)

        with session_scope(session_factory) as session:
            row2 = upsert_application_job(session, job)
        last_updated_2 = row2.last_updated_at

        assert last_updated_2 > last_updated_1  # refreshed


class TestStatusTransitions:
    def test_reimport_does_not_downgrade_terminal_status(self, session_factory) -> None:
        # Insert a job that is already APPLIED (terminal).
        job = _make_job(status=ApplicationStatus.APPLIED)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # Re-import with a "ready_to_apply" status — must NOT downgrade.
        job_ready = _make_job(
            status=ApplicationStatus.READY_TO_APPLY,
            cv_pdf="/tmp/cv.pdf",
            cover_letter_pdf="/tmp/cover.pdf",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job_ready)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.status == ApplicationStatus.APPLIED  # not downgraded

    def test_reimport_does_not_change_status_if_transition_invalid(self, session_factory) -> None:
        # Insert a job in REVIEW_READY.
        job = _make_job(status=ApplicationStatus.REVIEW_READY)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # Re-import with "discovered" — not a valid transition from REVIEW_READY.
        job_discovered = _make_job(status=ApplicationStatus.DISCOVERED)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job_discovered)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.status == ApplicationStatus.REVIEW_READY  # unchanged


class TestListAndCount:
    def test_list_returns_all_jobs_ordered(self, session_factory) -> None:
        job1 = _make_job(external_job_id="j1", url="https://example.com/jobs/1")
        job2 = _make_job(external_job_id="j2", url="https://example.com/jobs/2")
        with session_scope(session_factory) as session:
            upsert_application_job(session, job1)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job2)

        jobs = list_application_jobs(_open_session(session_factory))
        assert len(jobs) == 2

    def test_count_returns_zero_for_empty_db(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            assert count_application_jobs(session) == 0


class TestGetApplicationJob:
    def test_get_returns_none_for_missing(self, session_factory) -> None:
        result = get_application_job(_open_session(session_factory), "nonexistent")
        assert result is None

    def test_get_returns_job(self, session_factory) -> None:
        job = _make_job()
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.application_id == job.application_id


class TestDocumentsRoundTrip:
    """Prove that the ``documents`` field (ApplicationJobDocuments) survives
    a full round-trip: import -> DB upsert -> repository get/list ->
    reconstructed ApplicationJob.

    This test exists because Phase 1 initially dropped ``documents`` during
    persistence — the field was accepted by the Pydantic model but never
    written to or read from the database. The round-trip test catches that
    class of bug.
    """

    def test_documents_survive_round_trip_via_get(self, tmp_path: Path, session_factory) -> None:
        cv_md = tmp_path / "cv.md"
        cover_md = tmp_path / "cover.md"
        job = _make_job()
        job.documents = ApplicationJobDocuments(  # type: ignore[assignment]
            cv_md=str(cv_md),
            cover_letter_md=str(cover_md),
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.documents is not None
        assert retrieved.documents.cv_md == str(cv_md)
        assert retrieved.documents.cover_letter_md == str(cover_md)

    def test_documents_survive_round_trip_via_list(self, tmp_path: Path, session_factory) -> None:
        cv_md = tmp_path / "cv.md"
        job = _make_job()
        job.documents = ApplicationJobDocuments(  # type: ignore[assignment]
            cv_md=str(cv_md),
            cover_letter_md=None,
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        jobs = list_application_jobs(_open_session(session_factory))
        assert len(jobs) == 1
        assert jobs[0].documents is not None
        assert jobs[0].documents.cv_md == str(cv_md)
        assert jobs[0].documents.cover_letter_md is None

    def test_documents_none_when_not_provided(self, session_factory) -> None:
        job = _make_job()  # no documents set
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.documents is None

    def test_documents_survive_reimport(self, tmp_path: Path, session_factory) -> None:
        cv_md = tmp_path / "cv.md"
        cover_md = tmp_path / "cover.md"
        job = _make_job()
        job.documents = ApplicationJobDocuments(  # type: ignore[assignment]
            cv_md=str(cv_md),
            cover_letter_md=str(cover_md),
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # Re-import the same job (idempotent upsert).
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        retrieved = get_application_job(_open_session(session_factory), job.application_id)
        assert retrieved is not None
        assert retrieved.documents is not None
        assert retrieved.documents.cv_md == str(cv_md)
        assert retrieved.documents.cover_letter_md == str(cover_md)


def _open_session(session_factory):
    """Open a raw session for read queries."""
    return session_factory()
