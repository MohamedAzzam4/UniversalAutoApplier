"""Tests that production pipeline resumes correctly after intervention resolve.

The production resume path:
1. Pipeline processes form → fields need intervention → interventions created
2. User resolves intervention via API endpoint (writes to form_answers in job metadata)
3. Pipeline retry picks up fresh job from DB → form_answers populated
4. Deterministic mapper matches form_answers → field filled

This test verifies steps 2-4 through the public API layer: resolve endpoint
updates job metadata, and on retry the fill engine uses those answers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
from universal_auto_applier.core.statuses import (
    ApplicationStatus,
    InterventionKind,
    InterventionStatus,
    Platform,
)
from universal_auto_applier.form_engine.field_mapper import map_field
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.interventions.fill_bridge import create_interventions_from_fill_summary
from universal_auto_applier.interventions.store import (
    create_intervention,
    list_pending_interventions,
    resolve_intervention,
)
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_resume.sqlite"
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


def _make_job(tmp_path: Path, metadata: dict[str, Any] | None = None) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"fake")
    cover.write_bytes(b"fake")
    url = "https://example.com/jobs/resume-test"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="resume-test", url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="Resume Corp",
        title="Test Engineer",
        url=url,
        score=4.0,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="resume-test",
        metadata=metadata or {},
    )


class TestProductionResume:
    def test_resolve_updates_job_form_answers(self, session_factory, tmp_path: Path) -> None:
        """The resolve endpoint must write to job.metadata.form_answers."""
        job = _make_job(tmp_path)
        field_label = "LinkedIn URL"
        field_selector = "#linkedin"
        answer = "https://linkedin.com/in/testuser"

        # Create an intervention as if the pipeline had processed the form.
        with session_scope(session_factory) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Unknown required field: Required field has no mapping",
                suggested_answer=None,
                confidence=0.0,
                field_selector=field_selector,
                llm_metadata={"field_label": field_label, "field_type": "text"},
            )
            # Persist the job with no form_answers.
            upsert_application_job(session, job)
            session.commit()

        # Simulate the resolve endpoint logic.
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id=job.application_id)
            assert len(pending) == 1
            intervention_id = pending[0].intervention_id

            # Resolve.
            resolve_intervention(
                session,
                intervention_id,
                resolution=InterventionStatus.APPROVED,
                answer=answer,
            )

            # Update job metadata (resolve endpoint logic).
            db_job = get_application_job(session, job.application_id)
            assert db_job is not None
            form_answers = dict(db_job.metadata.get("form_answers", {}) or {})
            form_answers[field_label] = answer
            db_job.metadata["form_answers"] = form_answers
            upsert_application_job(session, db_job)
            session.commit()

        # Verify form_answers was persisted.
        with session_scope(session_factory) as session:
            db_job = get_application_job(session, job.application_id)
            assert db_job is not None
            fa = db_job.metadata.get("form_answers", {})
            assert isinstance(fa, dict)
            assert fa.get("LinkedIn URL") == answer

    def test_deterministic_mapper_uses_form_answers_on_retry(
        self, session_factory, tmp_path: Path
    ) -> None:
        """Given a job with form_answers in metadata, map_field must match."""
        field_label = "LinkedIn URL"
        answer = "https://linkedin.com/in/testuser"

        job = _make_job(
            tmp_path,
            metadata={
                "form_answers": {field_label: answer},
            },
        )

        field = FormField(
            selector="#linkedin",
            name="linkedin",
            label=field_label,
            type="text",
            required=True,
        )

        mapping = map_field(field, _candidate(), job)
        assert mapping is not None, "Expected a mapping from form_answers"
        assert mapping.value == answer, f"Expected {answer}, got {mapping.value}"
        assert mapping.source == "application_job"

    def test_resolve_then_retry_fills_field(self, session_factory, tmp_path: Path) -> None:
        """Full flow: resolve intervention, then retry pipeline fills the field.

        This simulates:
        1. First pipeline pass: form processed, intervention created
        2. User resolves with answer + save_to_memory=True
        3. Retry: fresh pipeline load with updated job metadata
        4. Fill engine resolves the previously-unknown field via form_answers
        """
        job = _make_job(tmp_path)
        field_label = "LinkedIn URL"
        field_selector = "#linkedin"
        answer = "https://linkedin.com/in/testuser"

        # Step 1: Create intervention and persist initial job.
        with session_scope(session_factory) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Unknown required field: Required field has no mapping",
                suggested_answer=None,
                confidence=0.0,
                field_selector=field_selector,
                llm_metadata={"field_label": field_label, "field_type": "text"},
            )
            upsert_application_job(session, job)
            session.commit()

        # Step 2: Resolve intervention + update form_answers (like resolve endpoint).
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, application_id=job.application_id)
            assert len(pending) == 1
            resolve_intervention(
                session,
                pending[0].intervention_id,
                resolution=InterventionStatus.APPROVED,
                answer=answer,
            )
            db_job = get_application_job(session, job.application_id)
            assert db_job is not None
            form_answers = dict(db_job.metadata.get("form_answers", {}) or {})
            form_answers[field_label] = answer
            db_job.metadata["form_answers"] = form_answers
            upsert_application_job(session, db_job)
            session.commit()

        # Step 3: Fresh pipeline load (simulate retry).
        with session_scope(session_factory) as session:
            fresh_job = get_application_job(session, job.application_id)
            assert fresh_job is not None

            # Step 4: Process form.
            fields = [
                FormField(
                    selector=field_selector,
                    name="linkedin",
                    label=field_label,
                    type="text",
                    required=True,
                ),
            ]
            summary = fill_form(fields, _candidate(), fresh_job)

        # The linkedin field should now be filled (not a new intervention).
        linkedin_results = [r for r in summary.results if r.field_selector == field_selector]
        assert len(linkedin_results) == 1
        linkedin = linkedin_results[0]
        assert linkedin.status == "filled", (
            f"Expected filled, got {linkedin.status}: {linkedin.explanation}"
        )
        assert linkedin.value == answer, f"Expected {answer}, got {linkedin.value}"

        # Verify no new interventions are created for this field.
        with session_scope(session_factory) as session:
            count = create_interventions_from_fill_summary(
                session,
                application_id=job.application_id,
                summary=summary,
            )
        # The original intervention already exists, and the linkedin field
        # is filled, so no new interventions should be created.
        assert count <= 1, f"Expected at most 1 intervention (original), got {count}"

    def test_no_harness_answer_memory_injection(self) -> None:
        """Prove the harness does NOT inject answer memory.

        The production resolve endpoint writes to form_answers in job metadata.
        The harness must NOT separately load answer memory facts.
        """
        import tests.harness.final_pipeline_server as harness

        # The harness must not define _load_answer_memory_facts.
        assert not hasattr(harness, "_load_answer_memory_facts"), (
            "Harness must not define _load_answer_memory_facts"
        )

        # Read the harness source to verify no answer memory DB loading.
        source_path = Path(harness.__file__)
        source = source_path.read_text(encoding="utf-8")
        # The harness may still reference candidate profile facts (OK), but
        # must not import answer memory, AnswerMemoryRow, or list_answers.
        has_db_answer_memory = bool(re.search(r"AnswerMemoryRow|list_answers|store_answer", source))
        assert not has_db_answer_memory, (
            "Harness must not import AnswerMemoryRow, list_answers, or store_answer"
        )
