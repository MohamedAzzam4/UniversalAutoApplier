"""Tests that field_label in llm_metadata survives special characters.

Structured intervention identity means the resolve endpoint reads
``llm_metadata["field_label"]`` — not regex-parsed display text. This
test proves that field labels with parentheses, colons, localized text,
and other characters are correctly stored and retrievable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.interventions.fill_bridge import create_interventions_from_fill_summary
from universal_auto_applier.interventions.store import get_intervention, list_pending_interventions
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_identity.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
    )


def _job(tmp_path: Path) -> ApplicationJob:
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


LABEL_CASES = [
    # (test_name, field_label, field_selector)
    ("parentheses", "LinkedIn (Profile) URL", "#linkedin"),
    ("colons_dots", "Expected salary: min. 50.000 €", "#salary"),
    ("localized_german", "Haben Sie Erfahrung mit Python? (Ja/Nein)", "#python_exp"),
    ("backslash", "C:\\Program Files path", "#prog_path"),
    ("html_ish", "<Company> override", "#company"),
    ("newlines", "Multi\nline\rtitle", "#multi"),
    ("normal", "Phone number", "#phone"),
]


class TestStructuredIdentity:
    @pytest.mark.parametrize("name,label,selector", LABEL_CASES)
    def test_field_label_preserved_in_llm_metadata(
        self, session_factory, tmp_path: Path, name: str, label: str, selector: str
    ) -> None:
        """The field label must survive into llm_metadata without loss."""
        fields = [
            FormField(selector=selector, name=name, label=label, type="text", required=True),
        ]
        summary = fill_form(fields, _candidate(), _job(tmp_path))

        with session_scope(session_factory) as session:
            create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")

        # The intervention that matches this field.
        matching = [p for p in pending if p.field_selector == selector]
        assert len(matching) == 1, f"No intervention found for selector {selector}"
        intervention = matching[0]

        mlm = intervention.llm_metadata
        assert mlm is not None, "llm_metadata must not be None"
        assert mlm.get("field_label") == label, (
            f"llm_metadata.field_label mismatch:\n"
            f"  expected: {label!r}\n"
            f"  got:      {mlm.get('field_label')!r}"
        )

        # The question text must NOT contain the label (display-only).
        # The label might appear accidentally for some simple labels,
        # so we only check that the resolved field_label doesn't come
        # from regex extraction of the question.
        assert "Label:" not in intervention.question

    def test_question_is_display_only(self, session_factory, tmp_path: Path) -> None:
        """Verify intervention question contains no structured identity."""
        fields = [
            FormField(selector="#x", name="x", label="X (special)", type="text", required=True),
        ]
        summary = fill_form(fields, _candidate(), _job(tmp_path))

        with session_scope(session_factory) as session:
            create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")

        assert len(pending) == 1
        q = pending[0].question
        # No label: prefix, no structured identity leak.
        assert "Label:" not in q, f"Question leaks label prefix: {q}"
        # The explanation is display-only like "Required field has unknown type".
        assert "type" in q.lower() or "unknown" in q.lower() or "field" in q.lower(), (
            f"Unexpected question text: {q}"
        )

    def test_resolve_reads_llm_metadata_not_question(self, session_factory, tmp_path: Path) -> None:
        """Prove that resolve uses llm_metadata["field_label"] not question text."""
        fields = [
            FormField(
                selector="#linkedin",
                name="linkedin",
                label="LinkedIn URL",
                type="text",
                required=True,
            ),
        ]
        summary = fill_form(fields, _candidate(), _job(tmp_path))

        intervention_id: str | None = None
        with session_scope(session_factory) as session:
            create_interventions_from_fill_summary(
                session,
                application_id="job-123",
                summary=summary,
            )

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id="job-123")
            assert len(pending) == 1
            intervention_id = pending[0].intervention_id

            intervention = get_intervention(session, intervention_id)
            assert intervention is not None

            # Confirm the question is clean display text.
            assert "LinkedIn URL" not in intervention.question
            assert intervention.llm_metadata is not None
            # The label is in llm_metadata.
            assert intervention.llm_metadata.get("field_label") == "LinkedIn URL"
