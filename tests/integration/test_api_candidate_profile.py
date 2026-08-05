"""API-level proof that POST /api/pipeline/start loads the candidate
profile and does NOT create first_name/last_name/email interventions
when the profile exists.

This test uses the real FastAPI TestClient and the durable WQ-4 worker
subprocess in deterministic fixture mode. Every assertion is meaningful:

1. The API endpoint resolves the candidate profile from the job's metadata.
2. With a profile, name/email fields are filled and do NOT become
   interventions (checked via the structured ``llm_metadata.field_label``,
   the authoritative field identity — never parsed question text).
3. Without a profile, name/email fields DO become interventions, proving the
   profile loader is actually used (no silent empty-profile bypass).
4. The run never produces SUBMITTED / APPLIED.
5. Run state is durable: counters, run_id, and terminal status are surfaced
   through GET /api/pipeline/status.

Must not require a browser or any network: fixture mode only.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"

_PROFILE_FIELDS = {"first name", "last name", "email address"}


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_app(tmp_path: Path, suffix: str = "") -> tuple[Any, TestClient]:
    """Build an app with a clean temp data dir; enter TestClient context."""
    settings = Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / f"uaa_data{suffix}",
        browser_headless=True,
        submit_mode="review",
        pipeline_job_pulse_ms=200,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings=settings)
    client = TestClient(app)
    client.__enter__()
    Base.metadata.create_all(app.state.engine)
    return app, settings, client


def _seed_job_with_profile(tmp_path: Path, external_id: str) -> str:
    """Seed a job carrying a full candidate profile snapshot; return app id."""
    cv = tmp_path / f"{external_id}-cv.pdf"
    cover = tmp_path / f"{external_id}-cover.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv")
    cover.write_bytes(b"%PDF-1.4 fake cover")

    url = f"https://boards.greenhouse.io/example/jobs/{external_id}"
    application_id = compute_application_id(
        platform=Platform.GREENHOUSE.value, external_job_id=external_id, url=url
    )
    job = ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="API Proof Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.QUEUED,
        external_job_id=external_id,
        metadata={
            "candidate_profile": {
                "first_name": "Mohamed",
                "last_name": "Azzam",
                "full_name": "Mohamed Azzam",
                "email": "mohamed@example.com",
                "phone": "+49 152 5617 2336",
                "city": "Erlangen",
                "country": "Germany",
            }
        },
    )
    return application_id, job


def _seed_job_without_profile(tmp_path: Path) -> str:
    cv = tmp_path / "no-profile-cv.pdf"
    cover = tmp_path / "no-profile-cover.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv")
    cover.write_bytes(b"%PDF-1.4 fake cover")

    url = "https://boards.greenhouse.io/example/jobs/api-proof-002"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="api-proof-002", url=url
    )
    job = ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="No Profile Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.QUEUED,
        external_job_id="api-proof-002",
        metadata={},  # NO candidate_profile snapshot
    )
    return application_id, job


def _wait_for_terminal(client: TestClient, timeout: float = 30.0) -> dict[str, Any]:
    """Poll GET /api/pipeline/status until the run reaches a terminal state."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/pipeline/status").json()
        if last["status"] in ("completed", "cancelled", "failed"):
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Pipeline did not reach terminal state in {timeout}s: {last}")


class TestAPIPipelineStartLoadsCandidateProfile:
    """Prove POST /api/pipeline/start loads the candidate profile from
    job metadata and honors/misses profile fields accordingly."""

    def test_api_start_with_profile_does_not_create_name_email_interventions(
        self, tmp_path: Path
    ) -> None:
        """With a profile snapshot, name/email fields are filled and do NOT
        become interventions."""
        application_id, job = _seed_job_with_profile(tmp_path, "api-proof-001")

        app, settings, client = _make_app(tmp_path, "-prof")
        try:
            with session_scope(app.state.session_factory) as session:
                upsert_application_job(session, job)

            fixture_html = _read_fixture("greenhouse_apply.html")
            response = client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "running"
            assert body["mode"] == "fixture_dry_run"
            assert body["run_id"]

            final = _wait_for_terminal(client)
            assert final["status"] == "completed"
            assert final["run_id"] == body["run_id"]

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, application_id)
            assert updated is not None
            # Never submitted, never stuck.
            assert updated.status not in (
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.APPLIED,
            )
            # The profile fills first/last/email, but the required resume
            # file upload still needs confirmation -> needs_user_input.
            assert updated.status in (
                ApplicationStatus.REVIEW_READY,
                ApplicationStatus.NEEDS_USER_INPUT,
            )

            with session_scope(app.state.session_factory) as session:
                pending = list_pending_interventions(session, application_id)

            field_labels = {
                (iv.llm_metadata or {}).get("field_label", "").lower() for iv in pending
            }
            assert not (field_labels & _PROFILE_FIELDS), (
                f"name/email fields must not be interventions when the profile "
                f"has them; got labels: {field_labels}"
            )
        finally:
            client.__exit__(None, None, None)

    def test_api_start_without_profile_creates_name_email_interventions(
        self, tmp_path: Path
    ) -> None:
        """With NO profile snapshot, name/email fields DO become
        interventions — proving the loader is not bypassed."""
        application_id, job = _seed_job_without_profile(tmp_path)

        app, settings, client = _make_app(tmp_path, suffix="noprof")
        try:
            with session_scope(app.state.session_factory) as session:
                upsert_application_job(session, job)

            fixture_html = _read_fixture("greenhouse_apply.html")
            response = client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )
            assert response.status_code == 200

            final = _wait_for_terminal(client)
            assert final["status"] == "completed"

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, application_id)
            assert updated is not None
            assert updated.status == ApplicationStatus.NEEDS_USER_INPUT

            with session_scope(app.state.session_factory) as session:
                pending = list_pending_interventions(session, application_id)

            field_labels = {
                (iv.llm_metadata or {}).get("field_label", "").lower() for iv in pending
            }
            assert field_labels & _PROFILE_FIELDS, (
                f"expected name/email interventions without a profile; got {field_labels}"
            )
            # Prove each such intervention carries its structured field.
            for iv in pending:
                label = (iv.llm_metadata or {}).get("field_label")
                if label and label.lower() in _PROFILE_FIELDS:
                    assert iv.llm_metadata.get("field_type"), f"field_type missing for {label}"
        finally:
            client.__exit__(None, None, None)

    def test_api_start_never_submits(self, tmp_path: Path) -> None:
        """Regardless of profile presence, the pipeline never submits."""
        application_id, job = _seed_job_with_profile(tmp_path, "api-proof-003")

        app, settings, client = _make_app(tmp_path, suffix="nosub")
        try:
            with session_scope(app.state.session_factory) as session:
                upsert_application_job(session, job)

            fixture_html = _read_fixture("greenhouse_apply.html")
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )

            final = _wait_for_terminal(client)
            assert final["status"] == "completed"
            assert final["jobs_completed"] == 1

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, application_id)
            assert updated is not None
            assert updated.status not in (
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.APPLIED,
            )
        finally:
            client.__exit__(None, None, None)
