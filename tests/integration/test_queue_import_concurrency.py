"""Deterministic concurrency tests for the queue-import API lock contract.

These tests replace the nondeterministic ``test_concurrent_import_returns_409_when_started_by_other_request``
which used ``threading.Barrier`` with ``TestClient`` — but ``TestClient`` processes
requests through a portal that may serialize them, making the overlap non-deterministic.

The deterministic approach:
1. Use ``monkeypatch`` to inject a ``threading.Event`` into ``_run_import`` so
   request A blocks at a stable boundary AFTER lock acquisition.
2. Send request A in a background thread; it acquires the lock and blocks.
3. Send request B from the test thread; it must get 409 because A holds the lock.
4. Release the event; A completes with 200.
5. Send request C; it must get 200 because the lock was released.

No arbitrary sleeps, no retries, no relaxed assertions.
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
from universal_auto_applier.services.queue_import_service import (
    QueueImportService,
)


def _make_valid_job_line(
    *,
    url: str = "https://example.com/jobs/123",
    external_job_id: str = "job-123",
    company: str = "Example GmbH",
) -> str:
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


class TestDeterministicConcurrency:
    """Deterministic concurrency contract tests using Event-based synchronization.

    These tests prove the 409 contract without relying on thread timing.
    The production lock is a ``threading.Lock`` on the ``QueueImportService``
    instance, acquired non-blocking in ``run()`` and released in ``finally``.

    Contract:
    - When the lock is held, any subsequent ``run()`` call raises
      ``QueueImportConcurrentError`` → HTTP 409.
    - When the lock is released, the next ``run()`` call succeeds → HTTP 200.
    - The lock is always released, even on exception (``finally`` block).
    """

    def test_request_b_gets_409_while_a_holds_lock(
        self,
        queue_client: TestClient,
    ) -> None:
        """Request B returns 409 while request A holds the lock.

        Uses monkeypatch to block request A inside ``_run_import`` AFTER
        the lock is acquired. Request B is sent from the test thread while
        A is blocked. B must get 409.

        Sequence:
        1. Start request A in a background thread.
        2. A acquires the lock, enters _run_import, blocks on the event.
        3. Test thread sends request B → must get 409.
        4. Release the event → A completes with 200.
        """
        # Trigger lazy service creation
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        # Create synchronization primitives
        block_event = threading.Event()
        acquired_event = threading.Event()
        original_run_import = service._run_import  # noqa: SLF001

        def blocking_run_import(source: Path, trigger: str) -> Any:
            """Wrapper that signals acquisition and blocks until released."""
            acquired_event.set()
            block_event.wait(timeout=10)  # Block until test releases
            return original_run_import(source, trigger)

        # Patch _run_import to block after lock acquisition
        service._run_import = blocking_run_import  # type: ignore[method-assign]  # noqa: SLF001

        result_a: list[int] = []
        error_a: list[Exception] = []

        def _post_a() -> None:
            try:
                result_a.append(queue_client.post("/api/queue/import").status_code)
            except Exception as exc:  # noqa: BLE001
                error_a.append(exc)

        thread_a = threading.Thread(target=_post_a, daemon=True)
        thread_a.start()

        try:
            # Wait until A has acquired the lock (inside _run_import)
            assert acquired_event.wait(timeout=10), "Request A did not acquire lock in time"

            # Request B: must get 409 because A holds the lock
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 409, (
                f"Expected 409 while lock held, got {response_b.status_code}"
            )
            assert "already running" in response_b.json()["detail"]

            # Release A
            block_event.set()
            thread_a.join(timeout=30)
            assert not thread_a.is_alive(), "Request A did not complete"

            # A must have succeeded
            assert len(error_a) == 0, f"Request A raised: {error_a}"
            assert result_a == [200], f"Expected [200], got {result_a}"
        finally:
            # Restore original method and ensure cleanup
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001
            block_event.set()
            if thread_a.is_alive():
                thread_a.join(timeout=5)

    def test_request_c_succeeds_after_a_releases_lock(
        self,
        queue_client: TestClient,
    ) -> None:
        """Request C returns 200 after request A releases the lock.

        Proves the lock is released after successful import, allowing
        subsequent requests to proceed.

        Sequence:
        1. Start request A in a background thread with blocking.
        2. A acquires the lock, blocks.
        3. Send B → 409.
        4. Release A → A completes with 200.
        5. Send C → 200 (lock was released).
        """
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        block_event = threading.Event()
        acquired_event = threading.Event()
        original_run_import = service._run_import  # noqa: SLF001

        def blocking_run_import(source: Path, trigger: str) -> Any:
            acquired_event.set()
            block_event.wait(timeout=10)
            return original_run_import(source, trigger)

        service._run_import = blocking_run_import  # type: ignore[method-assign]  # noqa: SLF001

        result_a: list[int] = []
        error_a: list[Exception] = []

        def _post_a() -> None:
            try:
                result_a.append(queue_client.post("/api/queue/import").status_code)
            except Exception as exc:  # noqa: BLE001
                error_a.append(exc)

        thread_a = threading.Thread(target=_post_a, daemon=True)
        thread_a.start()

        try:
            assert acquired_event.wait(timeout=10)

            # B gets 409
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 409

            # Release A
            block_event.set()
            thread_a.join(timeout=30)
            assert not thread_a.is_alive()
            assert result_a == [200]

            # C gets 200 — lock was released
            response_c = queue_client.post("/api/queue/import")
            assert response_c.status_code == 200
            assert response_c.json()["run"]["state"] == "success"
        finally:
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001
            block_event.set()
            if thread_a.is_alive():
                thread_a.join(timeout=5)

    def test_lock_releases_after_importer_exception(
        self,
        queue_client: TestClient,
    ) -> None:
        """Lock is released even when _run_import raises an exception.

        Proves the ``finally: self._lock.release()`` in ``run()`` works
        correctly when the importer crashes. The exception propagates as
        a 500 from FastAPI, but the lock must still be released.
        """
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        original_run_import = service._run_import  # noqa: SLF001
        call_count = 0

        def failing_run_import(source: Path, trigger: str) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated importer crash")
            return original_run_import(source, trigger)

        service._run_import = failing_run_import  # type: ignore[method-assign]  # noqa: SLF001

        try:
            # First request: _run_import raises → 500 from FastAPI,
            # but run()'s finally block releases the lock.
            with pytest.raises((RuntimeError, Exception)):  # noqa: B017
                queue_client.post("/api/queue/import")

            # Second request: lock must be released, so it succeeds
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 200
        finally:
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001

    def test_lock_releases_after_direct_exception(
        self,
        queue_client: TestClient,
    ) -> None:
        """Lock is released when _run_import raises directly (not caught internally).

        Patches _run_import to raise immediately, bypassing its internal
        try/except. The run() method's finally block must release the lock.
        """
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        original_run_import = service._run_import  # noqa: SLF001

        def raising_run_import(source: Path, trigger: str) -> Any:
            raise RuntimeError("direct crash in _run_import")

        service._run_import = raising_run_import  # type: ignore[method-assign]  # noqa: SLF001

        try:
            # First request: should raise (500 from FastAPI), but lock must be released
            with pytest.raises((RuntimeError, Exception)):  # noqa: B017
                queue_client.post("/api/queue/import")

            # Second request: lock must be released
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 200
        finally:
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001

    def test_repeated_calls_on_same_instance(self, queue_client: TestClient) -> None:
        """Repeated sequential calls on the same app instance all succeed.

        Proves no lock leak across multiple successful imports.
        """
        for i in range(5):
            response = queue_client.post("/api/queue/import")
            assert response.status_code == 200, f"Call {i + 1} failed: {response.status_code}"
            assert response.json()["run"]["state"] == "success"

    def test_no_deadlock_when_b_rejected(
        self,
        queue_client: TestClient,
    ) -> None:
        """Request B's 409 rejection does not cause a deadlock.

        After B is rejected with 409, A can still complete normally,
        and subsequent requests can acquire the lock.
        """
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        block_event = threading.Event()
        acquired_event = threading.Event()
        original_run_import = service._run_import  # noqa: SLF001

        def blocking_run_import(source: Path, trigger: str) -> Any:
            acquired_event.set()
            block_event.wait(timeout=10)
            return original_run_import(source, trigger)

        service._run_import = blocking_run_import  # type: ignore[method-assign]  # noqa: SLF001

        result_a: list[int] = []

        def _post_a() -> None:
            result_a.append(queue_client.post("/api/queue/import").status_code)

        thread_a = threading.Thread(target=_post_a, daemon=True)
        thread_a.start()

        try:
            assert acquired_event.wait(timeout=10)

            # B gets 409 — this must not deadlock
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 409

            # Release A — must complete normally
            block_event.set()
            thread_a.join(timeout=30)
            assert not thread_a.is_alive()
            assert result_a == [200]

            # C succeeds — no deadlock
            response_c = queue_client.post("/api/queue/import")
            assert response_c.status_code == 200
        finally:
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001
            block_event.set()
            if thread_a.is_alive():
                thread_a.join(timeout=5)

    def test_manual_lock_acquisition_causes_409(
        self,
        queue_client: TestClient,
    ) -> None:
        """Directly acquiring the service lock causes 409 (existing test contract).

        This is the deterministic version of the original concurrent test:
        manually acquire the lock, send a request, verify 409, release.
        """
        queue_client.get("/api/queue/status")
        service: QueueImportService = queue_client.app.state.queue_import_service  # type: ignore[union-attr]

        assert service._lock.acquire(blocking=False)  # noqa: SLF001
        try:
            response = queue_client.post("/api/queue/import")
            assert response.status_code == 409
            assert "already running" in response.json()["detail"]
        finally:
            service._lock.release()  # noqa: SLF001

        # After release, next request succeeds
        response = queue_client.post("/api/queue/import")
        assert response.status_code == 200


class TestLockScope:
    """Verify the lock scope is application-instance-local."""

    def test_lock_is_per_app_instance(self, tmp_path: Path) -> None:
        """Two separate app instances have independent locks.

        The lock is on the ``QueueImportService`` instance stored on
        ``app.state``. Two separate apps have separate service instances
        with separate locks. A request to app B does not get 409 when
        app A holds its lock.
        """
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")

        with _make_client(_make_settings(tmp_path, queue_path)) as client_a:
            with _make_client(_make_settings(tmp_path, queue_path)) as client_b:
                # Acquire lock on app A
                client_a.get("/api/queue/status")
                service_a: QueueImportService = client_a.app.state.queue_import_service  # type: ignore[union-attr]
                assert service_a._lock.acquire(blocking=False)  # noqa: SLF001
                try:
                    # App B has its own service with its own lock
                    client_b.get("/api/queue/status")
                    service_b: QueueImportService = client_b.app.state.queue_import_service  # type: ignore[union-attr]
                    assert service_b is not service_a
                    assert service_b._lock is not service_a._lock  # noqa: SLF001

                    # Request to B succeeds even though A's lock is held
                    response_b = client_b.post("/api/queue/import")
                    assert response_b.status_code == 200
                finally:
                    service_a._lock.release()  # noqa: SLF001
