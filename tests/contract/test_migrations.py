"""Contract test: a fresh database reaches the current Alembic revision.

This protects the migration boundary. If a model and a migration drift, this
test fails. Per ``TECHNICAL_BASELINE.md`` -> Technical Verification Gate
point 4: "A fresh database reaches the current Alembic revision".
"""

from __future__ import annotations

from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from universal_auto_applier.persistence.db import build_engine_url
from universal_auto_applier.persistence.migrations import apply_migrations

# The current head revision. Update this when adding a new migration.
CURRENT_HEAD = "0005_submission_tables"


def test_apply_migrations_creates_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "contract_uaa.sqlite"
    url = build_engine_url(db_path)
    head = apply_migrations(url)

    assert head == CURRENT_HEAD

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        actual = set(inspector.get_table_names())
    finally:
        engine.dispose()

    expected = {
        "application_jobs",
        "application_attempts",
        "phase_results",
        "interventions",
        "answer_memories",
        "artifacts",
        "system_runs",
        "submission_approvals",
        "submission_claims",
        "submission_results",
    }
    assert expected.issubset(actual), f"missing tables: {expected - actual}"


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    """Re-running migrations on an already-upgraded DB must be a no-op."""
    url = build_engine_url(tmp_path / "idempotent_uaa.sqlite")
    head_first = apply_migrations(url)
    head_second = apply_migrations(url)
    assert head_first == head_second == CURRENT_HEAD


def test_apply_migrations_sets_current_revision(tmp_path: Path) -> None:
    url = build_engine_url(tmp_path / "revision_uaa.sqlite")
    apply_migrations(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            current = ctx.get_current_revision()
    finally:
        engine.dispose()

    assert current == CURRENT_HEAD


def test_phase1_columns_exist(tmp_path: Path) -> None:
    """Phase 1 adds optional identity and descriptive columns."""
    url = build_engine_url(tmp_path / "phase1_columns.sqlite")
    apply_migrations(url)

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("application_jobs")}
    finally:
        engine.dispose()

    expected_new_columns = {
        "job_id",
        "external_job_id",
        "date_posted",
        "evaluated_at",
        "tailored_at",
        "evaluation_reason",
        "german_filter_result",
        "documents_json",
    }
    assert expected_new_columns.issubset(columns), (
        f"missing columns: {expected_new_columns - columns}"
    )
