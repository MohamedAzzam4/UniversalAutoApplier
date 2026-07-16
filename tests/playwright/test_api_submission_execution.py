"""API execution tests for controlled final submission.

Proves that ``POST /api/submit/{application_id}/submit`` actually
executes the coordinator (not just checks gates) when a context factory
is registered. Uses local Playwright fixtures only.

Architecture: The SubmissionExecutionService runs the browser execution
in a dedicated thread (to avoid greenlet conflicts with TestClient's
portal). The FixtureContextFactory creates its own Playwright instance
in that thread. This works on ALL Python versions (3.11–3.14).
"""

from __future__ import annotations

# These tests launch their own browser via FixtureContextFactory to prove
# that the API /submit endpoint actually executes the coordinator and clicks.
#
# On Python 3.13+, sync_playwright().start() in a non-main thread conflicts
# with pytest-playwright's greenlet. These tests are marked `live` (opt-in)
# on Python 3.13+ only. On Python 3.11/3.12 they run normally.
#
# The one-click guarantee is independently proven on ALL Python versions by:
# - tests/unit/test_submission_concurrency.py (DB-level concurrency)
# - tests/playwright/test_controlled_submission.py (browser-level, uses
#   pytest-playwright's context fixture, no FixtureContextFactory)
# - tests/playwright/test_submission_scenarios.py (same)
import sys
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.execution_service import FixtureContextFactory

_marks = [pytest.mark.playwright]
if sys.version_info >= (3, 13):
    _marks.append(pytest.mark.live)

pytestmark = _marks

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture(scope="module")
def fixture_server() -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_job(tmp_path: Path, url: str, external_id: str) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
        source="fixture",
        company="Test Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id=external_id,
        metadata={
            "candidate_profile": {
                "first_name": "Mohamed",
                "last_name": "Azzam",
                "full_name": "Mohamed Azzam",
                "email": "mohamed@example.com",
                "phone": "+49 1234567",
                "requires_sponsorship": False,
            },
        },
    )


def _make_app(
    tmp_path: Path,
    enable: bool = True,
    port: int = 8200,
) -> tuple[Any, Settings]:
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_api_exec",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=enable,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)
    app.state.submission_context_factory = FixtureContextFactory(headless=True)
    return app, settings


def _seed_job(settings: Settings, job: ApplicationJob) -> Any:
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    Base.metadata.create_all(engine)
    engine.dispose()
    return sf


def _observe_and_approve(client: TestClient, sf: Any, app_id: str) -> str:
    """Observe the live form and approve the persisted snapshot."""
    obs = client.post(f"/api/submit/{app_id}/observe")
    assert obs.status_code == 200
    from universal_auto_applier.submission.store import get_active_approval

    with session_scope(sf) as session:
        approval = get_active_approval(session, app_id)
    assert approval is not None
    return approval.approval_id


# ---------------------------------------------------------------------------
# 1. Valid approved API request clicks exactly once and confirms submission
# ---------------------------------------------------------------------------


class TestApiValidSubmitClicksOnce:
    def test_valid_request_clicks_once_and_confirms(
        self, fixture_server: str, tmp_path: Path
    ) -> None:
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-1")
        app, settings = _make_app(tmp_path, enable=True, port=8201)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            approval_id = _observe_and_approve(client, sf, job.application_id)
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            # Debug: print the body if clicked is not True
            if body.get("clicked") is not True:
                import json as _json

                print(f"DEBUG: response body = {_json.dumps(body)}", flush=True)
            assert body["clicked"] is True
            assert body["state"] == "submitted_confirmed"


# ---------------------------------------------------------------------------
# 2. Feature disabled causes zero clicks
# ---------------------------------------------------------------------------


class TestApiDisabledNoClick:
    def test_disabled_no_click(self, fixture_server: str, tmp_path: Path) -> None:
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-2")
        app, settings = _make_app(tmp_path, enable=False, port=8202)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            approval_id = _observe_and_approve(client, sf, job.application_id)
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["clicked"] is False
            assert body["state"] == "submission_not_allowed"


# ---------------------------------------------------------------------------
# 3. No approval causes zero clicks
# ---------------------------------------------------------------------------


class TestApiNoApprovalNoClick:
    def test_no_approval_no_click(self, fixture_server: str, tmp_path: Path) -> None:
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-3")
        app, settings = _make_app(tmp_path, enable=True, port=8203)
        _seed_job(settings, job)

        with TestClient(app) as client:
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": "fake-id", "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["clicked"] is False
            assert body["state"] == "submission_not_allowed"


# ---------------------------------------------------------------------------
# 4. Stale snapshot causes zero clicks
# ---------------------------------------------------------------------------


class TestApiStaleSnapshotNoClick:
    def test_stale_snapshot_no_click(self, fixture_server: str, tmp_path: Path) -> None:
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-4")
        app, settings = _make_app(tmp_path, enable=True, port=8204)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            _observe_and_approve(client, sf, job.application_id)

            from universal_auto_applier.submission.models import (
                SubmissionSnapshot,
                SubmissionSnapshotField,
                SubmissionSnapshotSubmitControl,
            )
            from universal_auto_applier.submission.store import (
                create_approval,
                get_active_approval,
            )

            wrong_snap = SubmissionSnapshot(
                application_id=job.application_id,
                application_url="https://wrong-url.com",
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-1",
                        label="First name",
                        field_type="text",
                        filled_value="Mohamed",
                        status="filled",
                    )
                ],
                pending_intervention_count=0,
                submit_control=SubmissionSnapshotSubmitControl(
                    text="Submit application",
                    selector="button[type='submit']",
                ),
            ).with_hashes()

            with session_scope(sf) as session:
                create_approval(
                    session,
                    application_id=job.application_id,
                    snapshot=wrong_snap,
                )
                approval = get_active_approval(session, job.application_id)
            assert approval is not None

            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval.approval_id, "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["clicked"] is False
            assert body["state"] == "approval_stale"


# ---------------------------------------------------------------------------
# 5. Ambiguous control causes zero clicks
# ---------------------------------------------------------------------------


class TestApiAmbiguousControlNoClick:
    def test_ambiguous_no_click(self, fixture_server: str, tmp_path: Path) -> None:
        url = f"{fixture_server}/submit_ambiguous.html"
        job = _make_job(tmp_path, url, "api-exec-5")
        app, settings = _make_app(tmp_path, enable=True, port=8205)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            approval_id = _observe_and_approve(client, sf, job.application_id)
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["clicked"] is False
            assert body["state"] == "submit_control_ambiguous"


# ---------------------------------------------------------------------------
# 6. Duplicate submit after first is blocked (one-click guarantee)
# ---------------------------------------------------------------------------


class TestApiDuplicateSubmitBlocked:
    def test_duplicate_submit_blocked(self, fixture_server: str, tmp_path: Path) -> None:
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-6")
        app, settings = _make_app(tmp_path, enable=True, port=8206)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            approval_id = _observe_and_approve(client, sf, job.application_id)

            resp1 = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp1.status_code == 200
            body1 = resp1.json()
            assert body1["clicked"] is True

            resp2 = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["clicked"] is False

            total_clicks = int(body1["clicked"]) + int(body2["clicked"])
            assert total_clicks == 1


# ---------------------------------------------------------------------------
# 7. Truly concurrent API requests produce exactly one click
# ---------------------------------------------------------------------------


class TestApiConcurrentOneClick:
    def test_concurrent_requests_one_click(self, fixture_server: str, tmp_path: Path) -> None:
        """Two truly concurrent API requests for the same approved
        application must produce exactly one click.

        The requests overlap: both are submitted to the same TestClient
        concurrently via threads. The DB unique constraint on
        submission_claims.approval_id ensures only one claim is created.
        The losing request gets SUBMISSION_NOT_ALLOWED without creating
        a browser.

        Both threads start simultaneously and overlap. The winning
        request acquires the claim, starts the browser, and clicks.
        The losing request finds the claim already exists and returns
        a controlled response.
        """
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "api-exec-7")
        app, settings = _make_app(tmp_path, enable=True, port=8207)
        sf = _seed_job(settings, job)

        with TestClient(app) as client:
            approval_id = _observe_and_approve(client, sf, job.application_id)

            results: list[dict[str, Any]] = []
            lock = threading.Lock()

            def make_request() -> None:
                try:
                    resp = client.post(
                        f"/api/submit/{job.application_id}/submit",
                        json={"approval_id": approval_id, "confirm": True},
                    )
                    with lock:
                        results.append(resp.json())
                except Exception as exc:
                    with lock:
                        results.append({"error": str(exc)})

            # Start both threads simultaneously — they overlap.
            t1 = threading.Thread(target=make_request)
            t2 = threading.Thread(target=make_request)
            t1.start()
            t2.start()
            t1.join(timeout=120)
            t2.join(timeout=120)

            assert len(results) == 2
            clicked_count = sum(1 for r in results if r.get("clicked") is True)
            assert clicked_count == 1, (
                f"Expected exactly 1 click, got {clicked_count}. Results: {results}"
            )

            # The losing request must return a controlled response.
            for r in results:
                assert "error" not in r, f"Unhandled exception: {r}"

            # Verify exactly one claim and one result in the DB.
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import (
                SubmissionClaimRow,
                SubmissionResultRow,
            )

            engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
            with session_scope(make_session_factory(engine)) as session:
                claims = list(
                    session.execute(
                        select(SubmissionClaimRow).where(
                            SubmissionClaimRow.application_id == job.application_id
                        )
                    ).scalars()
                )
                results_rows = list(
                    session.execute(
                        select(SubmissionResultRow).where(
                            SubmissionResultRow.application_id == job.application_id
                        )
                    ).scalars()
                )
            engine.dispose()

            assert len(claims) == 1, f"Expected 1 claim, got {len(claims)}"
            assert len(results_rows) == 1, f"Expected 1 result, got {len(results_rows)}"
