"""Tests for :mod:`universal_auto_applier.interventions.fill_bridge`.

Tests that Phase 4 fill results correctly create interventions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormField,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.interventions.fill_bridge import (
    create_interventions_from_fill_summary,
)
from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_bridge.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"fake")
    cover.write_bytes(b"fake")
    url = "https://example.com/jobs/123"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="job-123", url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="Example Corp",
        title="Software Engineer",
        url=url,
        score=4.1,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="job-123",
    )


class TestCreateInterventionsFromFillSummary:
    def test_creates_intervention_for_unknown_required_field(
        self, session_factory, tmp_path: Path
    ) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=True),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        with session_scope(session_factory) as session:
            count = create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        assert count == 1

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")

        assert len(pending) == 1
        assert "GPA" in pending[0].question or "Unknown" in pending[0].question

    def test_creates_intervention_for_password_field(self, session_factory, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#pw", name="password", label="Password", type="unknown", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        with session_scope(session_factory) as session:
            count = create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        assert count == 1

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")

        assert len(pending) == 1
        assert "blocked" in pending[0].question.lower()

    def test_no_interventions_for_filled_fields(self, session_factory, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        with session_scope(session_factory) as session:
            count = create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        assert count == 0

    def test_idempotent_creation(self, session_factory, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="GPA", type="text", required=True),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        with session_scope(session_factory) as session:
            create_interventions_from_fill_summary(
                session, application_id="job-123", summary=summary
            )
        with session_scope(session_factory) as session:
            create_interventions_from_fill_summary(
                session, application_id="job-123", summary=summary
            )

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")

        # Should still be 1 (idempotent).
        assert len(pending) == 1

    def test_multiple_interventions_for_multiple_fields(
        self, session_factory, tmp_path: Path
    ) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="GPA", type="text", required=True),
            FormField(
                selector="#salary",
                name="salary",
                label="Expected salary",
                type="text",
                required=True,
            ),
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        with session_scope(session_factory) as session:
            count = create_interventions_from_fill_summary(
                session, application_id="job-123", summary=summary
            )

        # GPA and salary need interventions, first_name is filled.
        assert count == 2
