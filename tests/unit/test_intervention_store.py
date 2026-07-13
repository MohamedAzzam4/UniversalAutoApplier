"""Tests for :mod:`universal_auto_applier.interventions.store`.

Covers intervention creation, idempotency, resolution, listing, and filtering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.statuses import InterventionKind, InterventionStatus
from universal_auto_applier.interventions.store import (
    count_pending_interventions,
    create_intervention,
    get_intervention,
    list_pending_interventions,
    resolve_intervention,
)
from universal_auto_applier.persistence.db import make_session_factory
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_intervention.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


class TestCreateIntervention:
    def test_create_new_intervention(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you require visa sponsorship?",
                options=["Yes", "No"],
                suggested_answer="No",
                confidence=0.62,
                field_selector="input[name='sponsorship']",
            )

        assert row.application_id == "job-123"
        assert row.kind == "field_answer"
        assert row.status == "pending"
        assert row.question == "Do you require visa sponsorship?"
        assert row.options == ["Yes", "No"]
        assert row.suggested_answer == "No"
        assert row.confidence == 0.62
        assert row.resolved_at is None

    def test_create_is_idempotent(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row1 = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you require visa sponsorship?",
                field_selector="input[name='sponsorship']",
            )
        with session_scope(session_factory) as session:
            row2 = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you require visa sponsorship?",
                field_selector="input[name='sponsorship']",
            )

        assert row1.intervention_id == row2.intervention_id

    def test_different_fields_create_different_interventions(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row1 = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="First name",
                field_selector="#first_name",
            )
            row2 = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Last name",
                field_selector="#last_name",
            )

        assert row1.intervention_id != row2.intervention_id


class TestResolveIntervention:
    def test_approve_intervention(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Sponsorship?",
                field_selector="#sponsorship",
            )
            resolved = resolve_intervention(
                session,
                row.intervention_id,
                resolution=InterventionStatus.APPROVED,
                answer="No",
            )

        assert resolved is not None
        assert resolved.status == "approved"
        assert resolved.resolved_at is not None
        assert resolved.suggested_answer == "No"

    def test_skip_intervention(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="GPA?",
            )
            resolved = resolve_intervention(
                session, row.intervention_id, resolution=InterventionStatus.SKIPPED
            )

        assert resolved.status == "skipped"

    def test_block_intervention(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Password field",
            )
            resolved = resolve_intervention(
                session, row.intervention_id, resolution=InterventionStatus.BLOCKED
            )

        assert resolved.status == "blocked"

    def test_invalid_resolution_raises(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-123",
                kind=InterventionKind.FIELD_ANSWER,
                question="Test?",
            )
            with pytest.raises(ValueError, match="Invalid resolution"):
                resolve_intervention(
                    session, row.intervention_id, resolution=InterventionStatus.PENDING
                )

    def test_resolve_nonexistent_returns_none(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            result = resolve_intervention(
                session, "nonexistent", resolution=InterventionStatus.APPROVED
            )
        assert result is None


class TestListInterventions:
    def test_list_pending(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            create_intervention(
                session, application_id="job-1", kind=InterventionKind.FIELD_ANSWER, question="Q1"
            )
            create_intervention(
                session, application_id="job-2", kind=InterventionKind.CAPTCHA, question="Q2"
            )
            pending = list_pending_interventions(session)

        assert len(pending) == 2

    def test_list_pending_filtered_by_application(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            create_intervention(
                session, application_id="job-1", kind=InterventionKind.FIELD_ANSWER, question="Q1"
            )
            create_intervention(
                session, application_id="job-2", kind=InterventionKind.CAPTCHA, question="Q2"
            )
            pending = list_pending_interventions(session, application_id="job-1")

        assert len(pending) == 1
        assert pending[0].application_id == "job-1"

    def test_resolved_not_in_pending(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session, application_id="job-1", kind=InterventionKind.FIELD_ANSWER, question="Q1"
            )
            resolve_intervention(
                session, row.intervention_id, resolution=InterventionStatus.APPROVED
            )
            pending = list_pending_interventions(session)

        assert len(pending) == 0

    def test_count_pending(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            create_intervention(
                session, application_id="job-1", kind=InterventionKind.FIELD_ANSWER, question="Q1"
            )
            create_intervention(
                session, application_id="job-1", kind=InterventionKind.CAPTCHA, question="Q2"
            )
            count = count_pending_interventions(session, application_id="job-1")

        assert count == 2


class TestGetIntervention:
    def test_get_existing(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session, application_id="job-1", kind=InterventionKind.FIELD_ANSWER, question="Q1"
            )
            intervention = get_intervention(session, row.intervention_id)

        assert intervention is not None
        assert intervention.question == "Q1"

    def test_get_nonexistent(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            result = get_intervention(session, "nonexistent")
        assert result is None


class TestAllInterventionKinds:
    """Verify all intervention kinds from DATA_CONTRACTS.md can be created."""

    @pytest.mark.parametrize(
        "kind",
        [
            InterventionKind.FIELD_ANSWER,
            InterventionKind.LOGIN_REQUIRED,
            InterventionKind.CAPTCHA,
            InterventionKind.UNKNOWN_PAGE,
            InterventionKind.REVIEW_BEFORE_SUBMIT,
            InterventionKind.MISSING_DOCUMENT,
            InterventionKind.VALIDATION_ERROR,
            InterventionKind.MANUAL_UPLOAD_REQUIRED,
        ],
    )
    def test_create_each_kind(self, session_factory, kind: InterventionKind) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = create_intervention(
                session,
                application_id="job-1",
                kind=kind,
                question=f"Test {kind}",
            )
        assert row.kind == str(kind)
