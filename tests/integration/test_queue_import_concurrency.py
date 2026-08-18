"""Deterministic concurrency tests for the queue-import API lock contract.

These tests prove the production lock contract deterministically using
``threading.Event`` to block request A inside ``_run_import`` AFTER
the lock is acquired, then send request B while A is blocked.

The lock is a ``threading.Lock`` on the ``QueueImportService`` instance
(application-instance-local, non-blocking acquire, released in ``finally``).

Contract proven here:
- B gets 409 while A owns the lock
- A gets 200 after release
- C gets 200 after A completes
- Importer exception releases the lock
- Direct exception releases the lock
- Rejected B does not release A's lock (no deadlock)
- Repeated requests on one app instance work
- Separate app instances have independent locks
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.services.queue_import_service import QueueImportService


def _make_valid_job_line(*, external_job_id: str = "job-123") -> str:
    application_id = compute_application_id(
        platform="greenhouse", external_job_id=external_job_id, url="https://example.com/jobs/123"
    )
    return json.dumps(
        {
            "application_id": application_id,
            "platform": "greenhouse",
            "source": "linkedin",
            "company": "Example GmbH",
            "title": "Working Student AI",
            "url": "https://example.com/jobs/123",
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


def _make_settings(tmp_path: Path, queue_path: Path | None) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        queue_path=queue_path,
        browser_headless=True,
        submit_mode="review",
    )


@contextmanager
def _make_client(settings: Settings) -> Iterator[TestClient]:
    from universal_auto_applier.api.app import create_app
    from universal_auto_applier.persistence.db import build_engine_url
    from universal_auto_applier.persistence.migrations import apply_migrations

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def queue_client(tmp_path: Path):
    """A client with a configured queue file containing one valid job."""
    queue_path = tmp_path / "queue.jsonl"
    queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
    settings = _make_settings(tmp_path, queue_path)
    with _make_client(settings) as client:
        yield client


class _BlockingImport:
    """Context manager that patches _run_import to block after lock acquisition.

    Sets ``acquired_event`` when the lock is held, then blocks on
    ``block_event`` until the test releases it.
    """

    def __init__(self, service: QueueImportService) -> None:
        self._service = service
        self._original = service._run_import  # noqa: SLF001
        self.block_event = threading.Event()
        self.acquired_event = threading.Event()

    def __enter__(self) -> _BlockingImport:
        original = self._original

        def blocking_run_import(
            source: Path, trigger: str, synthetic_mutation: bool = False
        ) -> Any:  # noqa: ARG002
            self.acquired_event.set()
            self.block_event.wait(timeout=10)
            return original(source, trigger, synthetic_mutation)

        self._service._run_import = blocking_run_import  # type: ignore[method-assign]  # noqa: SLF001
        return self

    def __exit__(self, *exc: object) -> None:
        self._service._run_import = self._original  # type: ignore[method-assign]  # noqa: SLF001
        self.block_event.set()


def _start_blocked_request_a(
    queue_client: TestClient,
) -> tuple[threading.Thread, list[int], list[Exception]]:
    """Start request A in a background thread. Returns (thread, results, errors)."""
    results: list[int] = []
    errors: list[Exception] = []

    def _post_a() -> None:
        try:
            results.append(queue_client.post("/api/queue/import").status_code)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=_post_a, daemon=True)
    thread.start()
    return thread, results, errors


class TestDeterministicConcurrency:
    """Deterministic concurrency contract tests using Event-based synchronization."""

    def test_b_gets_409_and_a_gets_200_after_release(self, queue_client: TestClient) -> None:
        """B gets 409 while A owns the lock; A gets 200 after release."""
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        with _BlockingImport(service) as blocker:
            thread_a, result_a, error_a = _start_blocked_request_a(queue_client)
            try:
                assert blocker.acquired_event.wait(timeout=10), "A did not acquire lock"

                # B gets 409 — A holds the lock.
                response_b = queue_client.post("/api/queue/import")
                assert response_b.status_code == 409

                # Release A.
                blocker.block_event.set()
                thread_a.join(timeout=30)
                assert not thread_a.is_alive()
                assert error_a == []
                assert result_a == [200]
            finally:
                blocker.block_event.set()
                if thread_a.is_alive():
                    thread_a.join(timeout=5)

    def test_c_succeeds_after_a_releases_lock(self, queue_client: TestClient) -> None:
        """C gets 200 after A releases the lock — no lock leak."""
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        with _BlockingImport(service) as blocker:
            thread_a, result_a, _ = _start_blocked_request_a(queue_client)
            try:
                assert blocker.acquired_event.wait(timeout=10)

                # B gets 409.
                assert queue_client.post("/api/queue/import").status_code == 409

                # Release A.
                blocker.block_event.set()
                thread_a.join(timeout=30)
                assert result_a == [200]
            finally:
                blocker.block_event.set()
                if thread_a.is_alive():
                    thread_a.join(timeout=5)

        # C gets 200 — lock was released.
        response_c = queue_client.post("/api/queue/import")
        assert response_c.status_code == 200

    def test_lock_releases_after_importer_exception(self, queue_client: TestClient) -> None:
        """Lock is released when _run_import raises (finally block)."""
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]
        original = service._run_import  # noqa: SLF001
        call_count = 0

        def failing_then_ok(source: Path, trigger: str, synthetic_mutation: bool = False) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated crash")
            return original(source, trigger, synthetic_mutation)

        service._run_import = failing_then_ok  # type: ignore[method-assign]  # noqa: SLF001
        try:
            with pytest.raises((RuntimeError, Exception)):  # noqa: B017
                queue_client.post("/api/queue/import")

            # Lock released — second request succeeds.
            response = queue_client.post("/api/queue/import")
            assert response.status_code == 200
        finally:
            service._run_import = original  # type: ignore[method-assign]  # noqa: SLF001

    def test_lock_releases_after_direct_exception(self, queue_client: TestClient) -> None:
        """Lock is released when _run_import raises directly."""
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]
        original = service._run_import  # noqa: SLF001

        def always_raise(source: Path, trigger: str, synthetic_mutation: bool = False) -> Any:  # noqa: ARG001, ARG002
            raise RuntimeError("direct crash")

        service._run_import = always_raise  # type: ignore[method-assign]  # noqa: SLF001
        try:
            with pytest.raises((RuntimeError, Exception)):  # noqa: B017
                queue_client.post("/api/queue/import")
        finally:
            service._run_import = original  # type: ignore[method-assign]  # noqa: SLF001

        # Lock released — next request succeeds.
        response = queue_client.post("/api/queue/import")
        assert response.status_code == 200

    def test_repeated_calls_same_instance(self, queue_client: TestClient) -> None:
        """Repeated sequential calls on the same app instance all succeed."""
        for i in range(5):
            response = queue_client.post("/api/queue/import")
            assert response.status_code == 200, f"Call {i + 1} failed: {response.status_code}"

    def test_no_deadlock_when_b_rejected(self, queue_client: TestClient) -> None:
        """B's 409 rejection does not deadlock — A completes and C succeeds."""
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        with _BlockingImport(service) as blocker:
            thread_a, result_a, _ = _start_blocked_request_a(queue_client)
            try:
                assert blocker.acquired_event.wait(timeout=10)

                # B gets 409 — must not deadlock.
                assert queue_client.post("/api/queue/import").status_code == 409

                # Release A.
                blocker.block_event.set()
                thread_a.join(timeout=30)
                assert result_a == [200]
            finally:
                blocker.block_event.set()
                if thread_a.is_alive():
                    thread_a.join(timeout=5)

        # C succeeds — no deadlock.
        assert queue_client.post("/api/queue/import").status_code == 200

    def test_lock_is_per_app_instance(self, tmp_path: Path) -> None:
        """Two separate app instances have independent locks."""
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")

        with _make_client(_make_settings(tmp_path, queue_path)) as client_a:
            with _make_client(_make_settings(tmp_path, queue_path)) as client_b:
                client_a.get("/api/queue/status")
                service_a: QueueImportService = client_a.app.state.queue_import_service  # type: ignore[union-attr]
                assert service_a._lock.acquire(blocking=False)  # noqa: SLF001
                try:
                    client_b.get("/api/queue/status")
                    service_b: QueueImportService = client_b.app.state.queue_import_service  # type: ignore[union-attr]
                    assert service_b is not service_a
                    assert service_b._lock is not service_a._lock  # noqa: SLF001

                    # B succeeds even though A's lock is held.
                    assert client_b.post("/api/queue/import").status_code == 200
                finally:
                    service_a._lock.release()  # noqa: SLF001
