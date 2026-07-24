"""API contract tests for the live-review snapshot contract.

Tests the typed Pydantic response models, persistence, and safety rules
without requiring a browser. Uses direct DB setup and API calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind, Platform
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotDocument,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
)
from universal_auto_applier.submission.store import create_approval


def _make_settings(tmp_path: Path, enable: bool = True) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8300,
        data_dir=tmp_path / "uaa_api_test",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=enable,
    )


def _make_job(tmp_path: Path, url: str = "https://example.com/job/api-1") -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="api-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="api-1",
        metadata={},
    )


def _make_snapshot(
    app_id: str,
    url: str = "https://example.com/job/api-1",
    *,
    fields: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    pending: int = 0,
) -> SubmissionSnapshot:
    snap_fields = [
        SubmissionSnapshotField(
            field_token=f.get("field_token", "lf-1"),
            label=f.get("label", "Field"),
            field_type=f.get("field_type", "text"),
            filled_value=f.get("filled_value", "test"),
            selected_value=f.get("selected_value", ""),
            status=f.get("status", "filled"),
            required=f.get("required", False),
            requires_confirmation=f.get("requires_confirmation", False),
            risk_level=f.get("risk_level", ""),
        )
        for f in (fields or [{"field_token": "lf-1", "filled_value": "test"}])
    ]
    snap_docs = [
        SubmissionSnapshotDocument(
            document_kind=d.get("document_kind", "cv"),
            path=d.get("path", "/cv.pdf"),
            content_hash=d.get("content_hash", "abc123"),
        )
        for d in (documents or [])
    ]
    snap = SubmissionSnapshot(
        application_id=app_id,
        application_url=url,
        fields=snap_fields,
        documents=snap_docs,
        pending_intervention_count=pending,
        unresolved_required_field_count=derive_unresolved_required_count(snap_fields),
        high_risk_unconfirmed_count=derive_unconfirmed_high_risk_count(snap_fields),
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit", selector="button[type='submit']"
        ),
    )
    return snap.with_hashes()


def _setup(tmp_path: Path, settings: Settings, job: ApplicationJob):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    Base.metadata.create_all(engine)
    return engine, sf


def _create_app(settings: Settings, engine: Any, sf: Any) -> Any:
    """Create app reusing the existing engine to avoid unclosed connections."""
    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = sf
    app.state.review_states = {}
    from universal_auto_applier.api.routes.logs import init_log_buffer

    init_log_buffer(app)
    Base.metadata.create_all(engine)
    return app


# ---------------------------------------------------------------------------
# 1. Complete observation response (via status after manual snapshot insert)
# ---------------------------------------------------------------------------


class TestCompleteStatusResponse:
    def test_status_returns_complete_snapshot(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[{"field_token": "lf-1", "label": "First name", "filled_value": "Mohamed"}],
            documents=[{"document_kind": "cv", "path": str(tmp_path / "cv.pdf")}],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "snapshot" in data
            snap_data = data["snapshot"]
            assert snap_data["application_id"] == job.application_id
            assert snap_data["company"] == "Test Corp"
            assert snap_data["job_title"] == "Engineer"
            assert snap_data["application_url"] == "https://example.com/job/api-1"
            assert snap_data["snapshot_hash"] == snap.snapshot_hash
            assert snap_data["form_fingerprint"] == snap.form_fingerprint
            assert len(snap_data["fields"]) == 1
            assert snap_data["fields"][0]["label"] == "First name"
            assert snap_data["fields"][0]["filled_value"] == "Mohamed"
            assert len(snap_data["documents"]) == 1
            assert snap_data["documents"][0]["document_kind"] == "cv"
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. Persistence across a new database session
# ---------------------------------------------------------------------------


class TestPersistenceAcrossSession:
    def test_status_survives_new_engine(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine1, sf1 = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf1) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        engine1.dispose()
        # New engine/session
        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        app = _create_app(settings, engine2, sf2)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            assert resp.json()["snapshot"]["snapshot_hash"] == snap.snapshot_hash
        engine2.dispose()


# ---------------------------------------------------------------------------
# 3. Status without any observation
# ---------------------------------------------------------------------------


class TestStatusWithoutObservation:
    def test_status_without_snapshot(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            data = resp.json()["snapshot"]
            assert data["snapshot_hash"] == ""
            assert data["form_fingerprint"] == ""
            assert data["active_approval_id"] is None
            assert data["approval_state"] == "none"
            assert data["can_approve"] is False
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. New observation invalidates old approval
# ---------------------------------------------------------------------------


class TestNewObservationInvalidatesOld:
    def test_new_snapshot_makes_old_stale(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap1 = _make_snapshot(job.application_id, fields=[{"filled_value": "a"}])
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap1)
        snap2 = _make_snapshot(job.application_id, fields=[{"filled_value": "b"}])
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap2)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            data = resp.json()["snapshot"]
            assert data["snapshot_hash"] == snap2.snapshot_hash
            assert data["active_approval_id"] is not None
        engine.dispose()


# ---------------------------------------------------------------------------
# 6. Confirm valid high-risk field
# ---------------------------------------------------------------------------


class TestConfirmHighRisk:
    def test_confirm_valid_high_risk(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-salary",
                    "filled_value": "50000",
                    "requires_confirmation": True,
                    "risk_level": "high",
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/confirm-high-risk",
                json={
                    "snapshot_hash": snap.snapshot_hash,
                    "field_tokens": ["lf-salary"],
                    "confirm": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "lf-salary" in data["confirmed_tokens"]
            assert data["snapshot"]["fields"][0]["confirmed"] is True
            assert data["snapshot"]["unconfirmed_high_risk_count"] == 0
        engine.dispose()


# ---------------------------------------------------------------------------
# 7. Reject unknown field token
# ---------------------------------------------------------------------------


class TestRejectUnknownField:
    def test_reject_unknown_token(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/confirm-high-risk",
                json={
                    "snapshot_hash": snap.snapshot_hash,
                    "field_tokens": ["nonexistent"],
                    "confirm": True,
                },
            )
            assert resp.status_code == 400
            assert "unknown" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 8. Reject non-high-risk field token
# ---------------------------------------------------------------------------


class TestRejectNonHighRisk:
    def test_reject_non_high_risk_token(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[{"field_token": "lf-name", "filled_value": "Mohamed"}],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/confirm-high-risk",
                json={
                    "snapshot_hash": snap.snapshot_hash,
                    "field_tokens": ["lf-name"],
                    "confirm": True,
                },
            )
            assert resp.status_code == 400
            assert "not high-risk" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 9. Reject stale confirmation
# ---------------------------------------------------------------------------


class TestRejectStaleConfirmation:
    def test_reject_stale_snapshot_hash(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/confirm-high-risk",
                json={"snapshot_hash": "wrong-hash", "field_tokens": ["lf-1"], "confirm": True},
            )
            assert resp.status_code == 409
            assert "mismatch" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 10. Changed answer invalidates confirmation
# ---------------------------------------------------------------------------


class TestChangedAnswerInvalidates:
    def test_new_snapshot_does_not_inherit_confirmations(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap1 = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-salary",
                    "filled_value": "50000",
                    "requires_confirmation": True,
                    "risk_level": "high",
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap1)
            from universal_auto_applier.submission.store import (
                confirm_high_risk_fields,
                get_active_approval,
            )

            approval = get_active_approval(session, job.application_id)
            confirm_high_risk_fields(session, approval.approval_id, ["lf-salary"])
        # New snapshot with different value
        snap2 = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-salary",
                    "filled_value": "60000",
                    "requires_confirmation": True,
                    "risk_level": "high",
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap2)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            data = resp.json()["snapshot"]
            assert data["snapshot_hash"] == snap2.snapshot_hash
            assert data["fields"][0]["confirmed"] is False
            assert data["unconfirmed_high_risk_count"] == 1
        engine.dispose()


# ---------------------------------------------------------------------------
# 11. Approve complete safe snapshot
# ---------------------------------------------------------------------------


class TestApproveCompleteSnapshot:
    def test_approve_complete_snapshot(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 200
            assert resp.json()["approved"] is True
        engine.dispose()


# ---------------------------------------------------------------------------
# 12. Reject incomplete snapshot
# ---------------------------------------------------------------------------


class TestRejectIncompleteSnapshot:
    def test_reject_unresolved_fields(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-unresolved",
                    "filled_value": "",
                    "status": "intervention_needed",
                    "required": True,
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 409
            assert "unresolved" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 13. Reject unresolved required field (distinct from case 12: tests a
# specific field with status=intervention_needed that is required)
# ---------------------------------------------------------------------------


class TestRejectUnresolvedRequiredField:
    def test_reject_required_field_with_intervention_status(self, tmp_path: Path) -> None:
        """A snapshot with a required field in intervention_needed status
        must be rejected for approval."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-required-q",
                    "label": "Required Question",
                    "field_type": "text",
                    "filled_value": "",
                    "status": "intervention_needed",
                    "required": True,
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 409
            assert "unresolved" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 14. Reject pending intervention
# ---------------------------------------------------------------------------


class TestRejectPendingIntervention:
    def test_reject_with_pending_intervention(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id, pending=0)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Q?",
                field_selector="lf-x",
            )
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 409
            assert "pending" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 14. Reject unconfirmed high-risk field
# ---------------------------------------------------------------------------


class TestRejectUnconfirmedHighRisk:
    def test_reject_unconfirmed(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(
            job.application_id,
            fields=[
                {
                    "field_token": "lf-salary",
                    "filled_value": "50000",
                    "requires_confirmation": True,
                    "risk_level": "high",
                }
            ],
        )
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 409
            assert "unconfirmed" in resp.json()["detail"].lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 15. Revoke approval
# ---------------------------------------------------------------------------


class TestRevokeApproval:
    def test_revoke(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/revoke")
            assert resp.status_code == 200
            assert resp.json()["revoked"] is True
        engine.dispose()


# ---------------------------------------------------------------------------
# 16. Idempotent revoke
# ---------------------------------------------------------------------------


class TestIdempotentRevoke:
    def test_revoke_twice(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp1 = client.post(f"/api/submit/{job.application_id}/revoke")
            assert resp1.status_code == 200
            resp2 = client.post(f"/api/submit/{job.application_id}/revoke")
            assert resp2.status_code == 200
            assert resp2.json()["revoked"] is True
        engine.dispose()


# ---------------------------------------------------------------------------
# 17. Status after approval
# ---------------------------------------------------------------------------


class TestStatusAfterApproval:
    def test_status_shows_approved_state(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            client.post(
                f"/api/submit/{job.application_id}/approve",
                json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
            )
            resp = client.get(f"/api/submit/{job.application_id}/status")
            data = resp.json()["snapshot"]
            assert data["approval_state"] == "active"
            assert data["can_approve"] is True
            assert data["can_submit"] is True
        engine.dispose()


# ---------------------------------------------------------------------------
# 18. Status after submission result
# ---------------------------------------------------------------------------


class TestStatusAfterSubmissionResult:
    def test_status_shows_submission_result(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
            from universal_auto_applier.submission.models import (
                SubmissionResult,
                SubmissionResultState,
            )
            from universal_auto_applier.submission.store import record_result

            result = SubmissionResult(
                application_id=job.application_id,
                approval_id="test-approval",
                snapshot_hash_at_submit=snap.snapshot_hash,
                state=SubmissionResultState.SUBMITTED_CONFIRMED,
                clicked=True,
                confirmation_evidence="test evidence",
            )
            record_result(session, result)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            data = resp.json()["snapshot"]
            assert data["latest_submission_state"] == "submitted_confirmed"
            assert data["latest_submission_error"] is None
        engine.dispose()


# ---------------------------------------------------------------------------
# 19. No sensitive configuration in responses
# ---------------------------------------------------------------------------


class TestNoSensitiveData:
    def test_no_api_keys_in_response(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)
        app = _create_app(settings, engine, sf)
        with TestClient(app) as client:
            resp = client.get(f"/api/submit/{job.application_id}/status")
            text = resp.text.lower()
            assert "api_key" not in text
            assert "password" not in text
            assert "secret" not in text
            # "token" appears in "field_token" which is expected; check for
            # actual secret-like patterns instead.
            import json as _json

            data = _json.loads(resp.text)
            snap = data.get("snapshot", {})
            for key in snap:
                assert "key" not in key.lower() or key == "snapshot_hash"
                assert "secret" not in key.lower()
                assert "password" not in key.lower()
        engine.dispose()


# ---------------------------------------------------------------------------
# 20. Observation failure (observe without context factory)
# ---------------------------------------------------------------------------


class TestObservationFailure:
    def test_observe_without_context_factory(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)
        # No submission_context_factory registered.
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 503
            assert "no browser context factory" in resp.json()["detail"].lower()
        engine.dispose()
