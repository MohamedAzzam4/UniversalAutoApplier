"""Contract tests for the WQ-3 queue-import service.

Covers the durable run lifecycle (success / partial / failed / skipped),
idempotent re-import, concurrent rejection, the startup runner, and the
no-browser / no-pipeline guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.persistence.db import make_session_factory
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.persistence.models import Base, QueueImportRunRow
from universal_auto_applier.services.queue_import_service import (
    QueueImportConcurrentError,
    QueueImportConfigurationError,
    QueueImportRunSummary,
    QueueImportService,
    QueueImportState,
    run_startup_import,
)


@pytest.fixture
def session_factory(tmp_path: Path):
    """Return a session factory bound to a fresh temp SQLite DB (NullPool)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_import.sqlite"
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


def _make_valid_job_line(
    *,
    url: str = "https://example.com/jobs/123",
    external_job_id: str = "job-123",
    company: str = "Example GmbH",
) -> str:
    """Return a single valid JSONL line (same shape as tests/contract/test_importer.py)."""
    application_id = compute_application_id(
        platform="greenhouse", external_job_id=external_job_id, url=url
    )
    return json.dumps(
        {
            "application_id": application_id,
            "platform": "greenhouse",
            "source": "linkedin",
            "company": company,
            "title": "Working Student AI",
            "url": url,
            "location": "Munich, Germany",
            "job_description": "Full JD",
            "score": 4.1,
            "verdict": "apply",
            "cv_pdf": None,
            "cover_letter_pdf": None,
            "status": "evaluated",
            "external_job_id": external_job_id,
        }
    )


def _make_settings(tmp_path: Path, queue_path: Path | None, *, startup: bool = False) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        queue_path=queue_path,
        import_queue_on_startup=startup,
        browser_headless=True,
        submit_mode="review",
    )


def _latest_run_row(session_factory) -> QueueImportRunRow | None:
    from sqlalchemy import select

    stmt = select(QueueImportRunRow).order_by(QueueImportRunRow.started_at.desc()).limit(1)
    with session_factory() as session:
        return session.execute(stmt).scalars().first()


class TestServiceRun:
    def test_success_run_persists_summary(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="api")

        assert isinstance(summary, QueueImportRunSummary)
        assert summary.state == QueueImportState.SUCCESS
        assert summary.total_lines == 1
        assert summary.imported == 1
        assert summary.skipped == 0
        assert summary.error_count == 0
        assert summary.source_fingerprint is not None
        assert summary.failure_reason is None

        row = _latest_run_row(session_factory)
        assert row is not None
        assert row.run_id == summary.run_id
        assert row.state == "success"
        assert row.trigger == "api"
        assert row.source_path == str(queue_path)

    def test_empty_file_is_success(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text("", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="api")

        assert summary.state == QueueImportState.SUCCESS
        assert summary.total_lines == 0
        assert summary.imported == 0

    def test_malformed_lines_produce_partial_run(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(external_job_id="j1") + "\n" + "{not valid json}\n",
            encoding="utf-8",
        )
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="api")

        assert summary.state == QueueImportState.PARTIAL
        assert summary.total_lines == 2
        assert summary.imported == 1
        assert summary.skipped == 1
        assert summary.error_count == 1
        # Only line number + message are persisted — never the raw JSONL line.
        assert summary.row_errors == [{"line_number": 2, "error": summary.row_errors[0]["error"]}]
        assert "invalid JSON" in summary.row_errors[0]["error"]

    def test_all_lines_invalid_is_failed(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text("garbage 1\ngarbage 2\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="api")

        assert summary.state == QueueImportState.FAILED
        assert summary.imported == 0
        assert summary.skipped == 2
        assert summary.error_count == 2
        assert summary.failure_reason is None

    def test_missing_file_is_failed_run(self, tmp_path: Path, session_factory) -> None:
        missing = tmp_path / "does-not-exist.jsonl"
        service = QueueImportService(_make_settings(tmp_path, missing), session_factory)

        summary = service.run(trigger="api")

        assert summary.state == QueueImportState.FAILED
        assert summary.imported == 0
        assert summary.failure_reason is not None
        assert "not found" in summary.failure_reason
        row = _latest_run_row(session_factory)
        assert row is not None
        assert row.state == "failed"

    def test_unconfigured_raises(self, tmp_path: Path, session_factory) -> None:
        service = QueueImportService(_make_settings(tmp_path, None), session_factory)
        with pytest.raises(QueueImportConfigurationError):
            service.run()

    def test_relative_path_rejected(self, tmp_path: Path, session_factory) -> None:
        service = QueueImportService(_make_settings(tmp_path, None), session_factory)
        with pytest.raises(QueueImportConfigurationError, match="absolute"):
            service.run(path=Path("relative/queue.jsonl"))

    def test_concurrent_run_rejected(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        # Hold the non-blocking lock as if another import were running.
        assert service._lock.acquire(blocking=False)  # noqa: SLF001
        try:
            with pytest.raises(QueueImportConcurrentError):
                service.run(trigger="api")
        finally:
            service._lock.release()

    def test_run_releases_lock_after_finish(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        service.run(trigger="api")
        # A second run must succeed — the lock was released.
        summary = service.run(trigger="api")
        assert summary.state == QueueImportState.SUCCESS


class TestServiceSyntheticMutation:
    """``service.run(synthetic_mutation=...)`` propagates the WQ-7C opt-in."""

    def _synthetic_line(
        self,
        *,
        full_name: str = "Test Candidate",
        email: str = "test.candidate@example.com",
        external_job_id: str = "syn-1",
    ) -> str:
        url = "https://example.com/syn/1"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id=external_job_id, url=url
        )
        return json.dumps(
            {
                "application_id": application_id,
                "platform": "greenhouse",
                "source": "greenhouse",
                "company": "Carta",
                "title": "Account Executive, Legal Services",
                "url": url,
                "location": "London",
                "job_description": "Full JD",
                "score": 5.0,
                "verdict": "apply",
                "cv_pdf": None,
                "cover_letter_pdf": None,
                "status": "evaluated",
                "external_job_id": external_job_id,
                "metadata": {
                    "candidate_profile": {
                        "full_name": full_name,
                        "first_name": full_name.split()[0],
                        "last_name": full_name.split()[-1],
                        "email": email,
                        "phone": "+1 555 0199",
                        "current_position": "Senior Account Executive",
                        "city": "Test City",
                        "country": "Syntheticland",
                    }
                },
            }
        )

    def test_opt_in_stamps_matching_identity(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(self._synthetic_line() + "\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="cli", synthetic_mutation=True)

        assert summary.state == QueueImportState.SUCCESS
        assert summary.imported == 1
        with session_factory() as session:
            job = get_application_job(
                session,
                compute_application_id(
                    platform="greenhouse", external_job_id="syn-1", url="https://example.com/syn/1"
                ),
            )
        assert job is not None
        snapshot = job.metadata["candidate_profile"]
        assert snapshot["synthetic_test"] is True
        assert snapshot["wq7_synthetic"] is True

    def test_opt_in_refuses_mismatched_identity(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            self._synthetic_line(full_name="Real Person", email="real.person@example.com") + "\n",
            encoding="utf-8",
        )
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        summary = service.run(trigger="cli", synthetic_mutation=True)

        assert summary.state == QueueImportState.FAILED
        assert summary.imported == 0
        assert summary.error_count == 1
        assert "synthetic-mutation stamp refused" in summary.row_errors[0]["error"]


class TestDurability:
    def test_runs_survive_new_service_instance(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path)

        first = QueueImportService(settings, session_factory).run(trigger="api")
        # A brand-new service instance (like after a restart) sees the run.
        latest = QueueImportService(settings, session_factory).latest_run()
        assert latest is not None
        assert latest.run_id == first.run_id

    def test_fingerprint_changes_when_file_changes(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path)
        service = QueueImportService(settings, session_factory)

        first = service.run(trigger="api")
        queue_path.write_text(_make_valid_job_line(external_job_id="j2") + "\n", encoding="utf-8")
        second = service.run(trigger="api")

        assert first.source_fingerprint != second.source_fingerprint


class TestStatusAndSummary:
    def test_status_reflects_config_and_latest_run(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path)
        service = QueueImportService(settings, session_factory)

        status = service.status()
        assert status["configured"] is True
        assert status["configured_path"] == str(queue_path)
        assert status["import_on_startup"] is False
        assert status["source_exists"] is True
        assert status["latest_run"] is None
        assert status["queue_job_summary"] == {"total": 0, "by_status": {}}

        service.run(trigger="api")
        status = service.status()
        assert status["latest_run"] is not None
        assert status["latest_run"]["state"] == "success"
        assert status["queue_job_summary"]["total"] == 1

    def test_status_unconfigured(self, tmp_path: Path, session_factory) -> None:
        service = QueueImportService(_make_settings(tmp_path, None), session_factory)
        status = service.status()
        assert status["configured"] is False
        assert status["configured_path"] is None
        assert status["source_exists"] is False


class TestStartupImport:
    def test_disabled_returns_none(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path)
        # import_queue_on_startup stays False
        assert run_startup_import(settings, session_factory) is None

    def test_enabled_imports_and_returns_summary(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path, startup=True)
        summary = run_startup_import(settings, session_factory)

        assert summary is not None
        assert summary["state"] == "success"
        assert summary["trigger"] == "startup"
        assert summary["imported"] == 1

    def test_enabled_with_missing_path_never_raises(self, tmp_path: Path, session_factory) -> None:
        missing = tmp_path / "missing.jsonl"
        settings = _make_settings(tmp_path, missing, startup=True)
        summary = run_startup_import(settings, session_factory)

        assert summary is not None
        assert summary["state"] == "failed"
        assert summary["failure_reason"] is not None
        row = _latest_run_row(session_factory)
        assert row is not None and row.state == "failed"

    def test_enabled_without_path_persists_skipped(self, tmp_path: Path, session_factory) -> None:
        settings = _make_settings(tmp_path, None, startup=True)
        summary = run_startup_import(settings, session_factory)

        assert summary is not None
        assert summary["state"] == "skipped"
        assert summary["failure_reason"] is not None
        row = _latest_run_row(session_factory)
        assert row is not None and row.state == "skipped"

    def test_concurrent_startup_returns_none(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path)
        import universal_auto_applier.services.queue_import_service as qis

        # A concurrent import already holds the service lock, so startup must
        # record nothing and return None — never raise.
        monkey = _patch_run_to_raise_concurrent(qis)
        try:
            assert run_startup_import(settings, session_factory) is None
        finally:
            monkey()


def _patch_run_to_raise_concurrent(module):
    """Make QueueImportService.run raise QueueImportConcurrentError once."""
    original = module.QueueImportService.run

    def _run(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise module.QueueImportConcurrentError("a queue import is already running")

    module.QueueImportService.run = _run

    def _restore():
        module.QueueImportService.run = original

    return _restore


class TestNoBrowserNoPipeline:
    def test_import_never_touches_browser_or_pipeline(
        self, tmp_path: Path, session_factory, monkeypatch
    ) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        service = QueueImportService(_make_settings(tmp_path, queue_path), session_factory)

        # The import path must only go through the contract importer. Any
        # browser/pipeline module import inside the service would show up here.
        import importlib
        import sys

        browser_modules = [
            "universal_auto_applier.browser.live_runner",
            "universal_auto_applier.browser.live_executor",
            "universal_auto_applier.orchestration",
        ]
        imported_before = {name: sys.modules.get(name) for name in browser_modules}

        summary = service.run(trigger="api")

        assert summary.state == QueueImportState.SUCCESS
        assert summary.imported == 1
        for name in browser_modules:
            if imported_before[name] is None:
                assert name not in sys.modules, f"{name} must not be imported by queue import"
        assert importlib.util.find_spec("playwright") is not None
