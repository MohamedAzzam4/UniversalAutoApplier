"""Contract test: a fresh database reaches the current Alembic revision.

This protects the migration boundary. If a model and a migration drift, this
test fails. Per ``TECHNICAL_BASELINE.md`` -> Technical Verification Gate
point 4: "A fresh database reaches the current Alembic revision".
"""

from __future__ import annotations

import importlib.util
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
CURRENT_HEAD = "0013_orchestration_durable_evidence"

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_0008_PATH = (
    REPO_ROOT / "migrations" / "versions" / "0008_reconcile_submission_statuses.py"
)


def _load_reconciliation_statements() -> tuple[str, ...]:
    """Import migration 0008 and return its exact reconciliation SQL."""
    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION_0008_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LEGACY_RECONCILIATION_STATEMENTS


_JOB_INSERT_SQL = """
    INSERT INTO application_jobs
        (application_id, platform, source, company, title, url, verdict,
         status, metadata_json, first_seen_at, last_updated_at)
    VALUES
        (:application_id, 'generic', 'test', 'Legacy Corp', 'Engineer',
         'https://example.com/job/legacy', 'apply', :status,
         '{}', '2026-07-01 09:00:00', '2026-07-01 09:00:00')
"""

_RESULT_INSERT_SQL = """
    INSERT INTO submission_results
        (result_id, application_id, approval_id, snapshot_hash_at_submit,
         state, clicked, confirmation_evidence, validation_errors_json,
         error_message, attempted_at, ats_reference_id)
    VALUES
        (:result_id, :application_id, :approval_id, 'snap-legacy', :state, 1,
         'evidence text', json_array(), '', :attempted_at, :ats_reference_id)
"""


def _seed_pre_reconciliation_db(
    tmp_path: Path,
    name: str,
    jobs: list[dict[str, object]],
    results: list[dict[str, object]],
) -> str:
    """Seed a pre-reconciliation DB (up to 0007) and run the reconciliation.

    The schema is upgraded to exactly ``0007_submission_results_ats_reference``
    (the reconciliation has not run yet), jobs and multi-result rows are then
    inserted, and ``apply_migrations`` runs only the 0008 repair.
    """
    url = build_engine_url(tmp_path / f"{name}.sqlite")
    _upgrade_to(url, "0007_submission_results_ats_reference")

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for job in jobs:
                conn.execute(text(_JOB_INSERT_SQL), job)
            for result in results:
                conn.execute(text(_RESULT_INSERT_SQL), result)
    finally:
        engine.dispose()

    head = apply_migrations(url)
    assert head == CURRENT_HEAD
    return url


def _job_status(url: str, application_id: str) -> str:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT status FROM application_jobs WHERE application_id = :id"),
                {"id": application_id},
            ).scalar_one()
    finally:
        engine.dispose()


def _count_jobs_with_status(url: str, status: str) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT COUNT(*) FROM application_jobs WHERE status = :status"),
                {"status": status},
            ).scalar_one()
    finally:
        engine.dispose()


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


def test_latest_result_wins_outcome_unknown_then_confirmed(tmp_path: Path) -> None:
    """1. Older outcome_unknown + newer submitted_confirmed -> SUBMITTED.

    The latest result per application is the only driver of the repair: a new
    submitted_confirmed must override an older outcome_unknown instead of the
    old row contradicting the new one.
    """
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "mix_confirmed_uaa",
        jobs=[{"application_id": "mix-confirmed", "status": "review_ready"}],
        results=[
            {
                "result_id": "res-old-unknown",
                "approval_id": "apr-1",
                "application_id": "mix-confirmed",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-new-confirmed",
                "approval_id": "apr-2",
                "application_id": "mix-confirmed",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            },
        ],
    )
    assert _job_status(url, "mix-confirmed") == "submitted"


def test_latest_result_wins_confirmed_then_outcome_unknown(tmp_path: Path) -> None:
    """2. Older submitted_confirmed + newer outcome_unknown -> NEEDS_REVIEW."""
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "mix_unknown_uaa",
        jobs=[{"application_id": "mix-unknown", "status": "review_ready"}],
        results=[
            {
                "result_id": "res-old-confirmed",
                "approval_id": "apr-1",
                "application_id": "mix-unknown",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-new-unknown",
                "approval_id": "apr-2",
                "application_id": "mix-unknown",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            },
        ],
    )
    assert _job_status(url, "mix-unknown") == "needs_review"


def test_equal_attempted_at_uses_result_id_tiebreaker(tmp_path: Path) -> None:
    """3. Equal attempted_at resolves deterministically on result_id DESC.

    With identical timestamps the largest ``result_id`` is the "latest"
    row, regardless of insertion order. Two applications with the SAME pair
    of result states but swapped result_ids must resolve to opposite statuses.
    """
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "tiebreaker_uaa",
        jobs=[
            {"application_id": "tie-unknown-wins", "status": "review_ready"},
            {"application_id": "tie-confirmed-wins", "status": "review_ready"},
        ],
        results=[
            # Insertion order is identical for both apps: outcome_unknown row
            # first, submitted_confirmed second. Only result_id differs.
            {
                "result_id": "zzz-tie-unknown",
                "approval_id": "apr-1",
                "application_id": "tie-unknown-wins",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "aaa-tie-confirmed",
                "approval_id": "apr-2",
                "application_id": "tie-unknown-wins",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "aaa-tie-unknown",
                "approval_id": "apr-3",
                "application_id": "tie-confirmed-wins",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "zzz-tie-confirmed",
                "approval_id": "apr-4",
                "application_id": "tie-confirmed-wins",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
        ],
    )
    assert _job_status(url, "tie-unknown-wins") == "needs_review"
    assert _job_status(url, "tie-confirmed-wins") == "submitted"


def test_terminal_statuses_remain_unchanged(tmp_path: Path) -> None:
    """4. Terminal statuses are never downgraded by the reconciliation."""
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "terminal_uaa",
        jobs=[
            {"application_id": "term-applied", "status": "applied"},
            {"application_id": "term-rejected", "status": "rejected"},
            {"application_id": "term-skipped", "status": "skipped"},
            {"application_id": "term-closed", "status": "closed"},
        ],
        results=[
            {
                "result_id": f"res-{app}",
                "approval_id": f"apr-{app}",
                "application_id": app,
                "state": "outcome_unknown",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            }
            for app in ("term-applied", "term-rejected", "term-skipped", "term-closed")
        ],
    )
    for app, expected in (
        ("term-applied", "applied"),
        ("term-rejected", "rejected"),
        ("term-skipped", "skipped"),
        ("term-closed", "closed"),
    ):
        assert _job_status(url, app) == expected


def test_earlier_pipeline_statuses_remain_unchanged(tmp_path: Path) -> None:
    """5. Earlier pipeline statuses are never auto-advanced."""
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "pipeline_uaa",
        jobs=[
            {"application_id": "pipe-inprogress", "status": "in_progress"},
            {"application_id": "pipe-queued", "status": "queued"},
            {"application_id": "pipe-discovered", "status": "discovered"},
        ],
        results=[
            {
                "result_id": f"res-{app}",
                "approval_id": f"apr-{app}",
                "application_id": app,
                "state": ("outcome_unknown" if app != "pipe-queued" else "submitted_confirmed"),
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            }
            for app in ("pipe-inprogress", "pipe-queued", "pipe-discovered")
        ],
    )
    for app, expected in (
        ("pipe-inprogress", "in_progress"),
        ("pipe-queued", "queued"),
        ("pipe-discovered", "discovered"),
    ):
        assert _job_status(url, app) == expected


def test_reconciliation_is_idempotent(tmp_path: Path) -> None:
    """6. Re-running the exact reconciliation SQL changes nothing."""
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "idempotent_uaa",
        jobs=[
            {"application_id": "mix-confirmed", "status": "review_ready"},
            {"application_id": "mix-unknown", "status": "review_ready"},
            {"application_id": "legacy-confirmed", "status": "review_ready"},
            {"application_id": "term-applied", "status": "applied"},
        ],
        results=[
            {
                "result_id": "res-old-unknown",
                "approval_id": "apr-1",
                "application_id": "mix-confirmed",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-new-confirmed",
                "approval_id": "apr-2",
                "application_id": "mix-confirmed",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-old-confirmed",
                "approval_id": "apr-3",
                "application_id": "mix-unknown",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-new-unknown",
                "approval_id": "apr-4",
                "application_id": "mix-unknown",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-single-confirmed",
                "approval_id": "apr-5",
                "application_id": "legacy-confirmed",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-term-unknown",
                "approval_id": "apr-6",
                "application_id": "term-applied",
                "state": "outcome_unknown",
                "attempted_at": "2026-07-02 09:00:00",
                "ats_reference_id": "",
            },
        ],
    )
    before = {
        "mix-confirmed": _job_status(url, "mix-confirmed"),
        "mix-unknown": _job_status(url, "mix-unknown"),
        "legacy-confirmed": _job_status(url, "legacy-confirmed"),
        "term-applied": _job_status(url, "term-applied"),
    }

    statements = _load_reconciliation_statements()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    finally:
        engine.dispose()

    after = {
        "mix-confirmed": _job_status(url, "mix-confirmed"),
        "mix-unknown": _job_status(url, "mix-unknown"),
        "legacy-confirmed": _job_status(url, "legacy-confirmed"),
        "term-applied": _job_status(url, "term-applied"),
    }
    assert after == before
    assert after["mix-confirmed"] == "submitted"
    assert after["mix-unknown"] == "needs_review"
    assert after["term-applied"] == "applied"


def test_legacy_confirmed_never_becomes_applied(tmp_path: Path) -> None:
    """7. APPLIED is never inferred for legacy rows.

    A legacy submitted_confirmed result without a reference only establishes
    SUBMITTED, and a legacy row carrying an unverified reference is left
    untouched. No job may gain ``applied`` from the reconciliation.
    """
    url = _seed_pre_reconciliation_db(
        tmp_path,
        "no_applied_infer_uaa",
        jobs=[
            {"application_id": "legacy-confirmed-plain", "status": "review_ready"},
            {"application_id": "legacy-confirmed-ref", "status": "review_ready"},
        ],
        results=[
            {
                "result_id": "res-plain-confirmed",
                "approval_id": "apr-1",
                "application_id": "legacy-confirmed-plain",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "",
            },
            {
                "result_id": "res-ref-confirmed",
                "approval_id": "apr-2",
                "application_id": "legacy-confirmed-ref",
                "state": "submitted_confirmed",
                "attempted_at": "2026-07-01 09:00:00",
                "ats_reference_id": "UNVERIFIED-REF",
            },
        ],
    )
    assert _job_status(url, "legacy-confirmed-plain") == "submitted"
    # Unverified reference: no inference of APPLIED, and no state change.
    assert _job_status(url, "legacy-confirmed-ref") == "review_ready"
    assert _count_jobs_with_status(url, "applied") == 0
