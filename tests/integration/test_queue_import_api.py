"""Integration tests for the WQ-3 queue-import API endpoints.

Covers ``POST /api/queue/import`` and ``GET /api/queue/status`` against a
real FastAPI app with a temp SQLite store: valid import, durable partial
runs, missing file (200 with failed run), unconfigured (400), concurrent
(409), opt-in startup import, and health integration.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id


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

    # Migrate the DB file before the lifespan starts so an opt-in startup
    # import runs against the real schema (queue_import_runs table exists).
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


class TestPostImport:
    def test_imports_configured_queue(self, queue_client: TestClient) -> None:
        response = queue_client.post("/api/queue/import")
        assert response.status_code == 200
        body = response.json()
        run = body["run"]
        assert run["state"] == "success"
        assert run["total_lines"] == 1
        assert run["imported"] == 1
        assert run["error_count"] == 0
        assert run["trigger"] == "api"
        assert run["run_id"]
        assert run["source_fingerprint"]

    def test_import_is_durable_and_idempotent(self, queue_client: TestClient) -> None:
        first = queue_client.post("/api/queue/import").json()["run"]
        second = queue_client.post("/api/queue/import").json()["run"]

        assert first["state"] == "success"
        assert second["state"] == "success"
        # Only one job in history, two durable runs recorded.
        status = queue_client.get("/api/queue/status").json()
        assert status["queue_job_summary"]["total"] == 1
        assert first["run_id"] != second["run_id"]

    def test_partial_run_persists_row_errors(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(
            _make_valid_job_line(external_job_id="j1") + "\n" + "{bad json}\n",
            encoding="utf-8",
        )
        with _make_client(_make_settings(tmp_path, queue_path)) as client:
            response = client.post("/api/queue/import")
            assert response.status_code == 200
            run = response.json()["run"]
            assert run["state"] == "partial"
            assert run["imported"] == 1
            assert run["error_count"] == 1
            assert run["row_errors"][0]["line_number"] == 2
            # Never the raw JSONL line.
            assert "{bad json}" not in json.dumps(run)

    def test_missing_file_returns_200_with_failed_run(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.jsonl"
        with _make_client(_make_settings(tmp_path, missing)) as client:
            response = client.post("/api/queue/import")
            assert response.status_code == 200
            run = response.json()["run"]
            assert run["state"] == "failed"
            assert run["failure_reason"] is not None
            assert "not found" in run["failure_reason"]

    def test_unconfigured_returns_400(self, tmp_path: Path) -> None:
        with _make_client(_make_settings(tmp_path, None)) as client:
            response = client.post("/api/queue/import")
            assert response.status_code == 400
            assert "UAA_QUEUE_PATH" in response.json()["detail"]

    def test_never_accepts_browser_supplied_path(self, queue_client: TestClient) -> None:
        """The API must not accept a path from the caller — only the configured one."""
        response = queue_client.post("/api/queue/import?path=/tmp/evil.jsonl")
        # FastAPI ignores unknown query params by default; the import still
        # uses the configured path. No arbitrary-path endpoint exists.
        assert response.status_code == 200
        run = response.json()["run"]
        assert run["source_path"] != "/tmp/evil.jsonl"

    def test_concurrent_import_returns_409(self, queue_client: TestClient) -> None:
        # Trigger lazy service creation, then hold the shared app-scoped
        # service lock and POST → 409.
        queue_client.get("/api/queue/status")
        service = queue_client.app.state.queue_import_service
        assert service._lock.acquire(blocking=False)  # noqa: SLF001
        try:
            response = queue_client.post("/api/queue/import")
            assert response.status_code == 409
            assert "already running" in response.json()["detail"]
        finally:
            service._lock.release()

    def test_concurrent_import_returns_409_when_started_by_other_request(
        self, queue_client: TestClient
    ) -> None:
        """Two overlapping requests: only one wins, the other gets 409.

        This test uses ``threading.Event`` to deterministically guarantee
        that request A owns the production lock before request B is sent.

        The old test synchronized thread launch but did not guarantee that
        request A owned the production lock while request B executed.
        Therefore both requests could execute sequentially and legitimately
        return 200.
        """
        import threading

        # Trigger lazy service creation to get the shared lock-bearing service.
        queue_client.get("/api/queue/status")
        service = queue_client.app.state.queue_import_service
        original_run_import = service._run_import  # noqa: SLF001

        block_event = threading.Event()
        acquired_event = threading.Event()

        def blocking_run_import(
            source: Path, trigger: str, synthetic_mutation: bool = False
        ) -> Any:  # noqa: ARG002
            """Block inside _run_import after the lock is acquired."""
            acquired_event.set()
            block_event.wait(timeout=10)
            return original_run_import(source, trigger, synthetic_mutation)

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
            # Wait until A has acquired the lock (inside _run_import).
            assert acquired_event.wait(timeout=10), "Request A did not acquire lock in time"

            # Request B: must get 409 because A holds the lock.
            response_b = queue_client.post("/api/queue/import")
            assert response_b.status_code == 409
            assert "already running" in response_b.json()["detail"]

            # B's 409 rejection must NOT release A's lock. A is still inside
            # _run_import (blocked on block_event), so it still owns the lock;
            # a non-blocking acquire from this thread must therefore fail.
            assert not service._lock.acquire(blocking=False), (  # noqa: SLF001
                "B's 409 rejection released A's lock"
            )

            # Release A.
            block_event.set()
            thread_a.join(timeout=30)
            assert not thread_a.is_alive(), "Request A did not complete"
            assert len(error_a) == 0, f"Request A raised: {error_a}"
            assert result_a == [200]

            # Request C: sent after A finishes, must get 200 (lock released).
            response_c = queue_client.post("/api/queue/import")
            assert response_c.status_code == 200
            assert response_c.json()["run"]["state"] == "success"

            # Lock is not left acquired after A and C both completed: a
            # non-blocking acquire from this thread must succeed, then we
            # release it again to leave the service in a clean state.
            assert service._lock.acquire(blocking=False), "Lock was left acquired after C"  # noqa: SLF001
            service._lock.release()  # noqa: SLF001
        finally:
            service._run_import = original_run_import  # type: ignore[method-assign]  # noqa: SLF001
            block_event.set()
            if thread_a.is_alive():
                thread_a.join(timeout=5)

        # Monkeypatch was restored in the finally block above.
        assert service._run_import is original_run_import, (  # noqa: SLF001
            "Monkeypatch was not restored in finally"
        )


class TestGetStatus:
    def test_status_initial_state(self, queue_client: TestClient) -> None:
        response = queue_client.get("/api/queue/status")
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["import_on_startup"] is False
        assert body["source_exists"] is True
        assert body["latest_run"] is None
        assert body["queue_job_summary"] == {"total": 0, "by_status": {}}

    def test_status_after_import(self, queue_client: TestClient) -> None:
        queue_client.post("/api/queue/import")
        body = queue_client.get("/api/queue/status").json()
        assert body["latest_run"] is not None
        assert body["latest_run"]["state"] == "success"
        assert body["queue_job_summary"]["total"] == 1

    def test_status_unconfigured(self, tmp_path: Path) -> None:
        with _make_client(_make_settings(tmp_path, None)) as client:
            body = client.get("/api/queue/status").json()
            assert body["configured"] is False
            assert body["configured_path"] is None
            assert body["source_exists"] is False
            assert body["latest_run"] is None


class TestStartupImport:
    def test_startup_import_opt_in_runs_once(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line(external_job_id="j1") + "\n", encoding="utf-8")
        with _make_client(_make_settings(tmp_path, queue_path, startup=True)) as client:
            # The startup import already ran during the TestClient lifespan.
            body = client.get("/api/queue/status").json()
            assert body["import_on_startup"] is True
            assert body["latest_run"] is not None
            assert body["latest_run"]["trigger"] == "startup"
            assert body["latest_run"]["state"] == "success"
            assert body["latest_run"]["imported"] == 1

    def test_startup_import_opt_out_runs_nothing(self, queue_client: TestClient) -> None:
        body = queue_client.get("/api/queue/status").json()
        assert body["import_on_startup"] is False
        assert body["latest_run"] is None

    def test_startup_import_missing_file_does_not_crash(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.jsonl"
        with _make_client(_make_settings(tmp_path, missing, startup=True)) as client:
            # Server still answers; the failed run is visible in status.
            body = client.get("/api/queue/status").json()
            assert body["latest_run"] is not None
            assert body["latest_run"]["state"] == "failed"


class TestHealthIntegration:
    def test_health_reports_queue_import_component(self, queue_client: TestClient) -> None:
        response = queue_client.get("/api/health")
        assert response.status_code == 200
        components = {c["name"]: c for c in response.json()["components"]}
        assert "queue_import" in components
        assert components["queue_import"]["state"] == "ready"

    def test_health_queue_import_unconfigured(self, tmp_path: Path) -> None:
        with _make_client(_make_settings(tmp_path, None)) as client:
            components = {c["name"]: c for c in client.get("/api/health").json()["components"]}
            assert components["queue_import"]["state"] == "not_configured"

    def test_health_queue_import_failed_run_shows_invalid(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.jsonl"
        with _make_client(_make_settings(tmp_path, missing)) as client:
            client.post("/api/queue/import")
            components = {c["name"]: c for c in client.get("/api/health").json()["components"]}
            assert components["queue_import"]["state"] == "invalid"

    def test_api_root_lists_queue_import_endpoints(self, queue_client: TestClient) -> None:
        body = queue_client.get("/api").json()
        assert "/api/queue/import" in body["endpoints"]
        assert "/api/queue/status" in body["endpoints"]
