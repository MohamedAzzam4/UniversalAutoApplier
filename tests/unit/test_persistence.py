"""Unit tests for the persistence layer.

These tests do not launch a browser. They use a fresh temp SQLite database
per test (see ``engine`` fixture in ``conftest.py``).
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.models import (
    AnswerMemoryRow,
    ApplicationAttemptRow,
    ApplicationJobRow,
    ArtifactRow,
    Base,
    InterventionRow,
    PhaseResultRow,
    SystemRunRow,
)


def test_build_engine_url_absolute(tmp_path) -> None:
    url = build_engine_url(tmp_path / "uaa.sqlite")
    assert url.startswith("sqlite:///")
    assert "uaa.sqlite" in url


def test_make_engine_enables_sqlite_foreign_keys(engine) -> None:
    with engine.connect() as connection:
        result = connection.execute(text("PRAGMA foreign_keys"))
        value = result.scalar()
    assert value == 1


def test_create_all_creates_all_required_tables(engine) -> None:
    inspector = inspect(engine)
    actual = set(inspector.get_table_names())
    expected = {
        "application_jobs",
        "application_attempts",
        "phase_results",
        "interventions",
        "answer_memories",
        "artifacts",
        "system_runs",
    }
    assert expected.issubset(actual), actual


def test_session_scope_commits_on_success(engine) -> None:
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        session.add(
            SystemRunRow(
                run_id="run-1",
                submit_mode="review",
                headless=False,
            )
        )

    # In a separate transaction, verify the row is persisted.
    factory2 = make_session_factory(engine)
    with session_scope(factory2) as session:
        rows = session.query(SystemRunRow).all()
    assert len(rows) == 1
    assert rows[0].run_id == "run-1"


def test_session_scope_rolls_back_on_exception(engine) -> None:
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as session:
            session.add(
                SystemRunRow(
                    run_id="run-2",
                    submit_mode="review",
                    headless=False,
                )
            )
            raise RuntimeError("intentional")
    except RuntimeError:
        pass

    factory2 = make_session_factory(engine)
    with session_scope(factory2) as session:
        rows = session.query(SystemRunRow).all()
    assert rows == []


def test_application_attempt_requires_existing_job_via_foreign_key(engine) -> None:
    """Inserting an attempt for a non-existent application_id must fail.

    SQLite enforces this because we enable PRAGMA foreign_keys=ON per
    connection in :func:`make_engine`.
    """
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as session:
            session.add(
                ApplicationAttemptRow(
                    attempt_id="att-1",
                    application_id="nonexistent",
                    run_id="run-1",
                    adapter="generic",
                    mode="review",
                    status="in_progress",
                )
            )
    except Exception as exc:  # noqa: BLE001 - any SQLAlchemy error is acceptable here
        assert "foreign key" in str(exc).lower() or "integrity" in str(exc).lower()
    else:
        raise AssertionError("expected foreign-key violation was not raised")


def test_models_can_roundtrip_a_minimal_attempt(engine) -> None:
    factory: sessionmaker = make_session_factory(engine)
    with session_scope(factory) as session:
        job = ApplicationJobRow(
            application_id="job-1",
            platform="greenhouse",
            source="linkedin",
            company="Example GmbH",
            title="Working Student AI",
            url="https://example.com/jobs/123",
            score=4.1,
            verdict="apply",
            status="queued",
        )
        attempt = ApplicationAttemptRow(
            attempt_id="att-1",
            application_id="job-1",
            run_id="run-1",
            adapter="greenhouse",
            mode="review",
            status="in_progress",
            last_phase="navigate",
        )
        phase = PhaseResultRow(
            attempt_id="att-1",
            sequence=1,
            phase="navigate",
            status="success",
            message="clicked Apply now",
        )
        intervention = InterventionRow(
            intervention_id="iv-1",
            application_id="job-1",
            status="pending",
            kind="field_answer",
            question="Do you require visa sponsorship?",
            options=["Yes", "No"],
            suggested_answer="No",
            confidence=0.62,
        )
        memory = AnswerMemoryRow(
            normalized_question="do you require visa sponsorship",
            answer="No",
            source="user_confirmed",
        )
        artifact = ArtifactRow(
            attempt_id="att-1",
            kind="screenshot",
            path="artifacts/att-1/001.png",
        )
        for obj in (job, attempt, phase, intervention, memory, artifact):
            session.add(obj)

    # Verify everything is readable in a new session.
    factory2 = make_session_factory(engine)
    with session_scope(factory2) as session:
        assert session.query(ApplicationJobRow).count() == 1
        assert session.query(ApplicationAttemptRow).count() == 1
        assert session.query(PhaseResultRow).count() == 1
        assert session.query(InterventionRow).count() == 1
        assert session.query(AnswerMemoryRow).count() == 1
        assert session.query(ArtifactRow).count() == 1


def test_base_metadata_covers_required_tables() -> None:
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "application_jobs",
        "application_attempts",
        "phase_results",
        "interventions",
        "answer_memories",
        "artifacts",
        "system_runs",
    }
    assert expected.issubset(table_names)
