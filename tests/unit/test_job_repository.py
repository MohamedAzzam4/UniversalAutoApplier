"""Tests for :mod:`universal_auto_applier.persistence.job_repository`.

Covers idempotent upsert, timestamp preservation, and status transition guards.
"""

from __future__ import annotations

import time
from datetime import UTC
from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import make_engine, make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    count_application_jobs,
    get_application_job,
    list_application_jobs,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test_repo.sqlite"
    engine = make_engine(f"sqlite:///{db_path}")
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


def _open_session(session_factory):
    """Open a raw session for read queries."""
    return session_factory()
