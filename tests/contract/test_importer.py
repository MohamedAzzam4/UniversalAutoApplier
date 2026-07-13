"""Tests for :mod:`universal_auto_applier.application_queue.importer`.

Covers valid JSONL, malformed JSON, duplicate jobs, and missing artifact paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_auto_applier.application_queue.importer import (
    ImportResult,
    ImportRowError,
    import_queue_file,
)
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.persistence.db import make_engine, make_session_factory
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    """Return a session factory bound to a fresh temp SQLite DB."""
    db_path = tmp_path / "test_import.sqlite"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _make_valid_job_line(
    *,
    url: str = "https://example.com/jobs/123",
    platform: str = "greenhouse",
    external_job_id: str = "job-123",
    company: str = "Example GmbH",
    title: str = "Working Student AI",
    cv_pdf: str | None = "/tmp/example-cv.pdf",
    cover_letter_pdf: str | None = "/tmp/example-cover.pdf",
    status: str = "evaluated",
    score: float = 4.1,
) -> str:
    """Return a single valid JSONL line."""
    application_id = compute_application_id(
        platform=platform, external_job_id=external_job_id, url=url
    )
    data = {
        "application_id": application_id,
        "platform": platform,
        "source": "linkedin",
        "company": company,
        "title": title,
        "url": url,
        "location": "Munich, Germany",
        "job_description": "Full JD",
        "score": score,
        "verdict": "apply",
        "cv_pdf": cv_pdf,
        "cover_letter_pdf": cover_letter_pdf,
        "status": status,
        "external_job_id": external_job_id,
    }
    return json.dumps(data)


class TestImportValidQueue:
    def test_imports_valid_jsonl(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(url="https://example.com/jobs/1", external_job_id="j1")
            + "\n"
            + _make_valid_job_line(url="https://example.com/jobs/2", external_job_id="j2")
            + "\n",
            encoding="utf-8",
        )

        result = import_queue_file(queue_path, session_factory)

        assert result.total_lines == 2
        assert result.imported == 2
        assert result.skipped == 0
        assert len(result.errors) == 0
        assert not result.has_errors

    def test_imported_jobs_appear_in_db(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(
                url="https://example.com/jobs/1",
                external_job_id="j1",
                company="Acme Corp",
            )
            + "\n",
            encoding="utf-8",
        )

        import_queue_file(queue_path, session_factory)

        application_id = compute_application_id(
            platform="greenhouse", external_job_id="j1", url="https://example.com/jobs/1"
        )
        job = get_application_job(_open_session(session_factory), application_id)
        assert job is not None
        assert job.company == "Acme Corp"
        assert job.cv_pdf is not None  # path retained

    def test_blank_lines_skipped(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            "\n"
            + _make_valid_job_line(external_job_id="j1")
            + "\n\n"
            + _make_valid_job_line(external_job_id="j2")
            + "\n\n",
            encoding="utf-8",
        )

        result = import_queue_file(queue_path, session_factory)
        assert result.total_lines == 2
        assert result.imported == 2


class TestImportMalformed:
    def test_invalid_json_skipped(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(external_job_id="j1")
            + "\n"
            + "{not valid json}\n"
            + _make_valid_job_line(external_job_id="j2")
            + "\n",
            encoding="utf-8",
        )

        result = import_queue_file(queue_path, session_factory)

        assert result.total_lines == 3
        assert result.imported == 2
        assert result.skipped == 1
        assert len(result.errors) == 1
        assert result.errors[0].line_number == 2
        assert "invalid JSON" in result.errors[0].error

    def test_validation_error_skipped(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        # A line with an invalid verdict
        bad_line = _make_valid_job_line(external_job_id="j1").replace(
            '"verdict": "apply"', '"verdict": "maybe"'
        )
        queue_path.write_text(
            bad_line + "\n" + _make_valid_job_line(external_job_id="j2") + "\n",
            encoding="utf-8",
        )

        result = import_queue_file(queue_path, session_factory)

        assert result.imported == 1
        assert result.skipped == 1
        assert "validation failed" in result.errors[0].error

    def test_non_object_json_skipped(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            "[1, 2, 3]\n" + _make_valid_job_line(external_job_id="j1") + "\n",
            encoding="utf-8",
        )

        result = import_queue_file(queue_path, session_factory)
        assert result.skipped == 1
        assert "expected JSON object" in result.errors[0].error

    def test_does_not_crash_on_completely_bad_file(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text("garbage line 1\ngarbage line 2\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.imported == 0
        assert result.skipped == 2
        assert len(result.errors) == 2


class TestImportDuplicate:
    def test_idempotent_reimport(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        line = _make_valid_job_line(
            external_job_id="j1", company="Acme Corp", url="https://example.com/jobs/1"
        )
        queue_path.write_text(line + "\n", encoding="utf-8")

        # Import twice
        result1 = import_queue_file(queue_path, session_factory)
        result2 = import_queue_file(queue_path, session_factory)

        assert result1.imported == 1
        assert result2.imported == 1  # second import also "imports" (upsert)

        # Only one job in DB
        from universal_auto_applier.persistence.job_repository import count_application_jobs

        session = _open_session(session_factory)
        assert count_application_jobs(session) == 1

    def test_reimport_updates_descriptive_fields(self, tmp_path: Path, session_factory) -> None:
        application_id = compute_application_id(
            platform="greenhouse", external_job_id="j1", url="https://example.com/jobs/1"
        )

        # First import with company "Acme Corp"
        queue1 = tmp_path / "q1.jsonl"
        queue1.write_text(
            _make_valid_job_line(external_job_id="j1", company="Acme Corp") + "\n",
            encoding="utf-8",
        )
        import_queue_file(queue1, session_factory)

        # Second import with updated company name
        queue2 = tmp_path / "q2.jsonl"
        queue2.write_text(
            _make_valid_job_line(external_job_id="j1", company="Acme Corporation") + "\n",
            encoding="utf-8",
        )
        import_queue_file(queue2, session_factory)

        job = get_application_job(_open_session(session_factory), application_id)
        assert job is not None
        assert job.company == "Acme Corporation"


class TestImportArtifactPaths:
    def test_relative_cv_pdf_rejected(self, tmp_path: Path, session_factory) -> None:
        line = _make_valid_job_line(cv_pdf="relative/path/cv.pdf")
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(line + "\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.skipped == 1
        assert "absolute path" in result.errors[0].error
        assert "cv_pdf" in result.errors[0].error

    def test_relative_cover_letter_rejected(self, tmp_path: Path, session_factory) -> None:
        line = _make_valid_job_line(cover_letter_pdf="relative/cover.pdf")
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(line + "\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.skipped == 1
        assert "absolute path" in result.errors[0].error
        assert "cover_letter_pdf" in result.errors[0].error

    def test_ready_to_apply_verifies_file_existence(self, tmp_path: Path, session_factory) -> None:
        line = _make_valid_job_line(
            cv_pdf="/nonexistent/cv.pdf",
            cover_letter_pdf="/nonexistent/cover.pdf",
            status="ready_to_apply",
        )
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(line + "\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.skipped == 1
        assert "does not exist" in result.errors[0].error

    def test_ready_to_accepts_existing_files(self, tmp_path: Path, session_factory) -> None:
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"fake pdf")
        cover.write_bytes(b"fake pdf")

        line = _make_valid_job_line(
            cv_pdf=str(cv),
            cover_letter_pdf=str(cover),
            status="ready_to_apply",
        )
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(line + "\n", encoding="utf-8")

        result = import_queue_file(queue_path, session_factory)
        assert result.imported == 1
        assert result.skipped == 0


class TestImportResultStructure:
    def test_result_has_counts(self, tmp_path: Path, session_factory) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(external_job_id="j1") + "\n" + "bad line\n",
            encoding="utf-8",
        )
        result = import_queue_file(queue_path, session_factory)

        assert isinstance(result, ImportResult)
        assert result.total_lines == 2
        assert result.imported == 1
        assert result.skipped == 1
        assert len(result.errors) == 1
        assert isinstance(result.errors[0], ImportRowError)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _open_session(session_factory):
    """Open a session from the factory for read queries in tests."""

    # Return a session-like context that stays open for reads.
    return session_factory()
