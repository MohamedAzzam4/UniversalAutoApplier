"""Tests for Phase 6 dashboard API endpoints.

Covers status, queue, interventions, review, and logs endpoints.
Also tests submit safety: approval cannot be bypassed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.core.models import FormFillSummary
from universal_auto_applier.interventions.review import (
    create_review_state,
)


@pytest.fixture
def client(settings) -> TestClient:
    from universal_auto_applier.api.app import create_app
    from universal_auto_applier.persistence.models import Base

    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        # Create tables in the lifespan-created engine.
        Base.metadata.create_all(app.state.engine)
        yield test_client


@pytest.fixture
def client_with_data(settings, tmp_path: Path) -> TestClient:
    """A client with a job in the database for testing queue/history."""
    from universal_auto_applier.api.app import create_app
    from universal_auto_applier.core.identity import compute_application_id
    from universal_auto_applier.core.models import ApplicationJob
    from universal_auto_applier.core.statuses import ApplicationStatus, Platform
    from universal_auto_applier.persistence.db import (
        session_scope,
    )
    from universal_auto_applier.persistence.job_repository import upsert_application_job
    from universal_auto_applier.persistence.models import Base

    app = create_app(settings=settings)

    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        session_factory = app.state.session_factory

        url = "https://example.com/jobs/1"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id="job-1", url=url
        )
        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"fake")
        cover.write_bytes(b"fake")

        job = ApplicationJob(
            application_id=application_id,
            platform=Platform.GREENHOUSE,
            source="linkedin",
            company="Test Corp",
            title="Software Engineer",
            url=url,
            score=4.5,
            verdict="apply",
            cv_pdf=str(cv),
            cover_letter_pdf=str(cover),
            status=ApplicationStatus.QUEUED,
            external_job_id="job-1",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        yield test_client


class TestStatusEndpoint:
    def test_status_returns_pipeline_status(self, client: TestClient) -> None:
        response = client.get("/api/status")
        assert response.status_code == 200
        body = response.json()
        assert body["run_status"] == "idle"
        assert "version" in body
        assert "jobs_total" in body
        assert "pending_interventions" in body
        assert body["submit_mode"] == "review"

    def test_status_shows_job_counts(self, client_with_data: TestClient) -> None:
        response = client_with_data.get("/api/status")
        body = response.json()
        assert body["jobs_total"] == 1
        assert "queued" in body["jobs_by_status"]


class TestQueueEndpoint:
    def test_queue_returns_jobs(self, client_with_data: TestClient) -> None:
        response = client_with_data.get("/api/queue")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["jobs"][0]["company"] == "Test Corp"

    def test_queue_filter_by_status(self, client_with_data: TestClient) -> None:
        response = client_with_data.get("/api/queue?status=queued")
        body = response.json()
        assert body["total"] == 1

        response = client_with_data.get("/api/queue?status=applied")
        body = response.json()
        assert body["total"] == 0

    def test_job_detail(self, client_with_data: TestClient) -> None:
        from universal_auto_applier.core.identity import compute_application_id

        application_id = compute_application_id(
            platform="greenhouse", external_job_id="job-1", url="https://example.com/jobs/1"
        )
        response = client_with_data.get(f"/api/queue/{application_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["company"] == "Test Corp"
        assert body["title"] == "Software Engineer"

    def test_job_detail_404(self, client: TestClient) -> None:
        response = client.get("/api/queue/nonexistent")
        assert response.status_code == 404


class TestInterventionsEndpoint:
    def test_list_empty(self, client: TestClient) -> None:
        response = client.get("/api/interventions")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["interventions"] == []

    def test_list_all_includes_resolved(self, client: TestClient) -> None:
        # First check with pending_only=false on empty DB.
        response = client.get("/api/interventions?pending_only=false")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0


class TestReviewEndpoint:
    def test_review_state_not_found(self, client: TestClient) -> None:
        response = client.get("/api/review/job-123")
        assert response.status_code == 200
        body = response.json()
        assert body["approved"] is False
        assert body["can_submit"] is False

    def test_submit_check_without_review(self, client: TestClient) -> None:
        response = client.get("/api/review/job-123/submit-check")
        assert response.status_code == 200
        body = response.json()
        assert body["can_submit"] is False
        assert "No review state" in body["reason"]

    def test_approve_nonexistent_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/review/job-123/approve",
            json={"approval_id": "test-approval"},
        )
        assert response.status_code == 404

    def test_deny_nonexistent_returns_404(self, client: TestClient) -> None:
        response = client.post("/api/review/job-123/deny")
        assert response.status_code == 404


class TestReviewSubmitSafety:
    """Prove that the dashboard API cannot bypass the submit safety gate."""

    def test_submit_blocked_without_approval(self, client: TestClient) -> None:
        """Even with a review state, submit is blocked without approval."""
        # Manually set a review state on app.state.

        # Access the app through the TestClient.
        # Set review state directly on app.state.
        app = client.app
        summary = FormFillSummary(total_fields=1, filled=1, intervention_needed=0)
        state = create_review_state(application_id="job-safe", fill_summary=summary)
        app.state.review_states["job-safe"] = state

        # Check: not approved -> can_submit is False.
        response = client.get("/api/review/job-safe/submit-check")
        body = response.json()
        assert body["can_submit"] is False

    def test_submit_allowed_after_approval(self, client: TestClient) -> None:

        # Set review state directly on app.state.
        app = client.app
        summary = FormFillSummary(total_fields=1, filled=1, intervention_needed=0)
        state = create_review_state(application_id="job-safe2", fill_summary=summary)
        app.state.review_states["job-safe2"] = state

        # Approve.
        response = client.post(
            "/api/review/job-safe2/approve",
            json={"approval_id": "approval-1"},
        )
        assert response.status_code == 200

        # Now can_submit should be True.
        response = client.get("/api/review/job-safe2/submit-check")
        body = response.json()
        assert body["can_submit"] is True

    def test_submit_blocked_with_interventions(self, client: TestClient) -> None:

        # Set review state directly on app.state.
        app = client.app
        summary = FormFillSummary(total_fields=2, filled=1, intervention_needed=1)
        state = create_review_state(application_id="job-int", fill_summary=summary)
        app.state.review_states["job-int"] = state

        # Try to approve -> should be 409 (conflict).
        response = client.post(
            "/api/review/job-int/approve",
            json={"approval_id": "approval-2"},
        )
        assert response.status_code == 409

        # can_submit should still be False.
        response = client.get("/api/review/job-int/submit-check")
        body = response.json()
        assert body["can_submit"] is False

    def test_deny_revokes_approval(self, client: TestClient) -> None:

        # Set review state directly on app.state.
        app = client.app
        summary = FormFillSummary(total_fields=1, filled=1, intervention_needed=0)
        state = create_review_state(application_id="job-deny", fill_summary=summary)
        app.state.review_states["job-deny"] = state

        # Approve.
        client.post("/api/review/job-deny/approve", json={"approval_id": "a1"})

        # Deny.
        response = client.post("/api/review/job-deny/deny")
        assert response.status_code == 200

        # can_submit should be False again.
        response = client.get("/api/review/job-deny/submit-check")
        body = response.json()
        assert body["can_submit"] is False


class TestLogsEndpoint:
    def test_empty_logs(self, client: TestClient) -> None:
        response = client.get("/api/logs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["entries"] == []

    def test_empty_errors(self, client: TestClient) -> None:
        response = client.get("/api/errors")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0


class TestApiRootListsEndpoints:
    def test_api_root_lists_all_endpoints(self, client: TestClient) -> None:
        response = client.get("/api")
        body = response.json()
        endpoints = body["endpoints"]
        assert "/api/status" in endpoints
        assert "/api/queue" in endpoints
        assert "/api/interventions" in endpoints
        assert "/api/logs" in endpoints
        assert "/api/errors" in endpoints


class TestPhase3Regression:
    """Phase 3 safety regression."""

    def test_dangerous_submit_never_clicked(self) -> None:
        from universal_auto_applier.navigator.page_observer import observe_html
        from universal_auto_applier.navigator.safe_explorer import safe_explore

        submit_html = '<html><body><button type="submit">Submit application</button></body></html>'
        clicked: list[str] = []

        def observe():
            return observe_html(submit_html, url="https://example.com/submit")

        def click(selector: str) -> bool:
            clicked.append(selector)
            return True

        safe_explore(observe, click)
        assert len(clicked) == 0


class TestPhase4Regression:
    """Phase 4 safety regression."""

    def test_fill_engine_never_submits(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.identity import compute_application_id
        from universal_auto_applier.core.models import (
            ApplicationJob,
            CandidateProfile,
            FormField,
        )
        from universal_auto_applier.core.statuses import ApplicationStatus, Platform
        from universal_auto_applier.form_engine.fill_engine import fill_form

        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"fake")
        cover.write_bytes(b"fake")
        url = "https://example.com/jobs/123"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id="job-123", url=url
        )
        job = ApplicationJob(
            application_id=application_id,
            platform=Platform.GREENHOUSE,
            source="linkedin",
            company="Test",
            title="Test",
            url=url,
            score=4.0,
            verdict="apply",
            cv_pdf=str(cv),
            cover_letter_pdf=str(cover),
            status=ApplicationStatus.READY_TO_APPLY,
            external_job_id="job-123",
        )
        candidate = CandidateProfile(first_name="John", last_name="Doe", email="john@example.com")
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            ),
        ]
        summary = fill_form(fields, candidate, job)

        for result in summary.results:
            assert "submit" not in result.status


class TestPhase5Regression:
    """Phase 5 safety regression."""

    def test_password_field_blocked(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.identity import compute_application_id
        from universal_auto_applier.core.models import (
            ApplicationJob,
            CandidateProfile,
            FormField,
        )
        from universal_auto_applier.core.statuses import ApplicationStatus, Platform
        from universal_auto_applier.form_engine.fill_engine import fill_form

        cv = tmp_path / "cv.pdf"
        cover = tmp_path / "cover.pdf"
        cv.write_bytes(b"fake")
        cover.write_bytes(b"fake")
        url = "https://example.com/jobs/123"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id="job-123", url=url
        )
        job = ApplicationJob(
            application_id=application_id,
            platform=Platform.GREENHOUSE,
            source="linkedin",
            company="Test",
            title="Test",
            url=url,
            score=4.0,
            verdict="apply",
            cv_pdf=str(cv),
            cover_letter_pdf=str(cover),
            status=ApplicationStatus.READY_TO_APPLY,
            external_job_id="job-123",
        )
        candidate = CandidateProfile(first_name="John")
        fields = [
            FormField(
                selector="#pw", name="password", label="Password", type="unknown", required=True
            ),
        ]
        summary = fill_form(fields, candidate, job)

        assert summary.blocked == 1
        assert summary.results[0].field_type == "password"

    def test_check_submit_approval_blocks_without_state(self) -> None:
        from universal_auto_applier.interventions.review import check_submit_approval

        assert check_submit_approval(None) is False
