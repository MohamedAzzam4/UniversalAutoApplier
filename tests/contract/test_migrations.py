"""Contract test: a fresh database reaches the current Alembic revision.

This protects the migration boundary. If a model and a migration drift, this
test fails. Per ``TECHNICAL_BASELINE.md`` -> Technical Verification Gate
point 4: "A fresh database reaches the current Alembic revision".
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from universal_auto_applier.persistence.db import build_engine_url
from universal_auto_applier.persistence.migrations import (
    ALEMBIC_INI,
    MIGRATIONS_DIR,
    apply_migrations,
)

# The current head revision. Update this when adding a new migration.
CURRENT_HEAD = "0008_reconcile_submission_statuses"


def _upgrade_to(url: str, revision: str) -> None:
    """Upgrade ``url`` to exactly ``revision`` (for legacy-DB seeding)."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


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


def test_submission_results_has_ats_reference_column(tmp_path: Path) -> None:
    """0007 adds the structured ATS reference column to submission_results."""
    url = build_engine_url(tmp_path / "ats_ref_uaa.sqlite")
    apply_migrations(url)

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("submission_results")}
    finally:
        engine.dispose()

    assert "ats_reference_id" in columns


def test_legacy_submission_rows_reconciled_on_upgrade(tmp_path: Path) -> None:
    """0008 repairs pre-WQ-1 inconsistent job statuses, with safety bounds."""
    url = build_engine_url(tmp_path / "legacy_uaa.sqlite")

    # Simulate a pre-WQ-1 database: schema up to 0006 only, with rows that
    # were persisted without any job-status transition.
    _upgrade_to(url, "0006_submission_unique_constraints")

    insert_job = text(
        """
        INSERT INTO application_jobs
            (application_id, platform, source, company, title, url, verdict,
             status, metadata_json, first_seen_at, last_updated_at)
        VALUES
            (:application_id, 'generic', 'test', 'Legacy Corp', 'Engineer',
             'https://example.com/job/legacy', 'apply', :status,
             '{}', '2026-07-01 09:00:00', '2026-07-01 09:00:00')
        """
    )
    insert_result = text(
        """
        INSERT INTO submission_results
            (result_id, application_id, approval_id, snapshot_hash_at_submit,
             state, clicked, confirmation_evidence, validation_errors_json,
             error_message, attempted_at)
        VALUES
            (:result_id, :application_id, :approval_id, 'snap-legacy', :state, 1,
             'evidence text', json_array(), '', '2026-07-01 09:00:00')
        """
    )

    cases: list[tuple[str, str, str, str]] = [
        # (application_id, job status, result state, expected after upgrade)
        ("legacy-confirmed", "review_ready", "submitted_confirmed", "submitted"),
        ("legacy-unknown-rr", "review_ready", "outcome_unknown", "needs_review"),
        ("legacy-unknown-sub", "submitted", "outcome_unknown", "needs_review"),
        ("legacy-applied", "applied", "submitted_confirmed", "applied"),
        ("legacy-inprogress", "in_progress", "outcome_unknown", "in_progress"),
    ]

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for app_id, status, _state, _expected in cases:
                conn.execute(
                    insert_job,
                    {"application_id": app_id, "status": status},
                )
            i = 0
            for app_id, _status, state, _expected in cases:
                i += 1
                conn.execute(
                    insert_result,
                    {
                        "result_id": f"res-legacy-{i}",
                        "application_id": app_id,
                        "approval_id": f"apr-legacy-{i}",
                        "state": state,
                    },
                )
    finally:
        engine.dispose()

    head = apply_migrations(url)
    assert head == CURRENT_HEAD

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            for app_id, _status, _state, expected in cases:
                row = conn.execute(
                    text("SELECT status FROM application_jobs WHERE application_id = :id"),
                    {"id": app_id},
                ).one()
                assert row.status == expected, (
                    f"{app_id}: expected {expected!r}, got {row.status!r}"
                )
            # Never infer APPLIED for legacy rows: no row may end up applied
            # that was not applied before the upgrade.
            applied = conn.execute(
                text("SELECT COUNT(*) FROM application_jobs WHERE status = 'applied'")
            ).scalar_one()
            assert applied == 1
    finally:
        engine.dispose()
