"""Unit tests: snapshot safety state is derived from field data, not aggregates.

These 16 tests prove that every safety gate (unresolved required fields,
unconfirmed high-risk answers, completeness, consistency) is computed from
the ACTUAL field records, never from the persisted static aggregates.

If a persisted aggregate contradicts the field-level data, the system
MUST reject the state (cannot approve, cannot submit) and return an
actionable blocking reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
    check_snapshot_consistency,
    derive_is_complete,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
)
from universal_auto_applier.submission.store import (
    create_approval,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_safety",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )


def _job(tmp_path: Path, suffix: str = "safety-1") -> ApplicationJob:
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC),
            external_job_id=suffix,
            url=f"https://example.com/job/{suffix}",
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test",
        title="Engineer",
        url=f"https://example.com/job/{suffix}",
        verdict="apply",
        cv_pdf=str(tmp_path / "cv.pdf"),
        cover_letter_pdf=str(tmp_path / "cover.pdf"),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id=suffix,
        metadata={},
    )


def _setup(tmp_path: Path, settings: Settings, job: ApplicationJob):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    Base.metadata.create_all(engine)
    return engine, sf


def _field(**kw: Any) -> SubmissionSnapshotField:
    defaults = dict(
        field_token="lf-1",
        label="Field",
        field_type="text",
        filled_value="",
        selected_value="",
        status="filled",
        required=False,
        requires_confirmation=False,
        risk_level="",
    )
    defaults.update(kw)
    return SubmissionSnapshotField(**defaults)


def _snapshot(
    app_id: str, *, fields: list[SubmissionSnapshotField], **kw: Any
) -> SubmissionSnapshot:
    snap = SubmissionSnapshot(
        application_id=app_id,
        application_url="https://example.com/job/safety",
        fields=fields,
        documents=[],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit", selector="button[type='submit']"
        ),
        **kw,
    )
    return snap.with_hashes()


def _make_app_client(settings: Settings, engine: Any, sf: Any):
    app = create_app(settings=settings)
    app.state.settings = settings
    app.state.session_factory = sf
    app.state.engine = engine
    return TestClient(app)


# ===================================================================
# Group A: Derivation functions (tests 1-4)
# ===================================================================


class TestDeriveUnresolvedRequiredCount:
    """Tests 1-4: derive_unresolved_required_count from field data."""

    def test_empty_fields_returns_zero(self) -> None:
        assert derive_unresolved_required_count([]) == 0

    def test_filled_fields_not_counted(self) -> None:
        fields = [_field(status="filled", required=True)]
        assert derive_unresolved_required_count(fields) == 0

    def test_intervention_needed_counted(self) -> None:
        fields = [_field(status="intervention_needed", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_validation_error_counted(self) -> None:
        fields = [_field(status="validation_error", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_failed_counted(self) -> None:
        fields = [_field(status="failed", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_blocked_counted(self) -> None:
        fields = [_field(status="blocked", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_unfilled_counted(self) -> None:
        fields = [_field(status="unfilled", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_unsupported_counted(self) -> None:
        fields = [_field(status="unsupported", required=True)]
        assert derive_unresolved_required_count(fields) == 1

    def test_non_required_unresolved_also_counts(self) -> None:
        """Conservative: even non-required unresolved fields block."""
        fields = [_field(status="intervention_needed", required=False)]
        assert derive_unresolved_required_count(fields) == 1

    def test_multiple_fields(self) -> None:
        fields = [
            _field(status="filled", required=True),
            _field(status="intervention_needed", required=True),
            _field(status="validation_error", required=False),
        ]
        assert derive_unresolved_required_count(fields) == 2


class TestDeriveIsComplete:
    def test_complete_when_no_unresolved(self) -> None:
        assert derive_is_complete([_field(status="filled")]) is True

    def test_incomplete_when_unresolved_exists(self) -> None:
        assert derive_is_complete([_field(status="failed", required=True)]) is False


class TestDeriveUnconfirmedHighRiskCount:
    def test_no_high_risk_returns_zero(self) -> None:
        fields = [_field(risk_level="low", requires_confirmation=False)]
        assert derive_unconfirmed_high_risk_count(fields, frozenset()) == 0

    def test_high_risk_unconfirmed_counted(self) -> None:
        fields = [_field(risk_level="high", requires_confirmation=True)]
        assert derive_unconfirmed_high_risk_count(fields, frozenset()) == 1

    def test_confirmed_not_counted(self) -> None:
        fields = [_field(field_token="f1", risk_level="high", requires_confirmation=True)]
        assert derive_unconfirmed_high_risk_count(fields, {"f1"}) == 0


# ===================================================================
# Group B: Consistency check (tests 5-8)
# ===================================================================


class TestCheckSnapshotConsistency:
    """Tests 5-8: check_snapshot_consistency detects stale aggregates."""

    def test_consistent_returns_empty(self) -> None:
        fields = [_field(status="filled")]
        snap = _snapshot(
            "app", fields=fields, unresolved_required_field_count=0, high_risk_unconfirmed_count=0
        )
        assert check_snapshot_consistency(snap) == ""

    def test_inconsistent_unresolved_detected(self) -> None:
        """Aggregate says 0 but field has validation_error."""
        fields = [_field(status="validation_error", required=True)]
        snap = _snapshot(
            "app", fields=fields, unresolved_required_field_count=0, high_risk_unconfirmed_count=0
        )
        err = check_snapshot_consistency(snap)
        assert "Snapshot inconsistency" in err
        assert "unresolved_required_field_count" in err
        assert "but field data shows 1" in err

    def test_inconsistent_high_risk_detected(self) -> None:
        """Aggregate says 0 but field has high risk."""
        fields = [_field(risk_level="high", requires_confirmation=True)]
        snap = _snapshot(
            "app", fields=fields, unresolved_required_field_count=0, high_risk_unconfirmed_count=0
        )
        err = check_snapshot_consistency(snap)
        assert "Snapshot inconsistency" in err
        assert "high_risk_unconfirmed_count" in err

    def test_consistency_passes_with_matching_data(self) -> None:
        fields = [_field(status="validation_error", required=True)]
        snap = _snapshot(
            "app", fields=fields, unresolved_required_field_count=1, high_risk_unconfirmed_count=0
        )
        assert check_snapshot_consistency(snap) == ""


# ===================================================================
# Group C: Response builder derivation (tests 9-11)
# ===================================================================


class TestResponseBuilderDerivation:
    """Tests 9-11: _build_snapshot_response returns derived values."""

    def test_unresolved_derived_from_fields(self, tmp_path: Path) -> None:
        """Snapshot with aggregate=0 but field=validation_error shows is_complete=False."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="resp-1")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            fields = [_field(field_token="f1", status="validation_error", required=True)]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            with session_scope(sf) as session:
                create_approval(session, application_id=job.application_id, snapshot=snap)
            app = create_app(settings=settings)
            app.state.settings = settings
            app.state.session_factory = sf
            app.state.engine = engine
            with TestClient(app) as client:
                resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            data = resp.json()["snapshot"]
            # is_complete should be False because field has validation_error
            assert data["is_complete"] is False
            # unresolved_required_field_count should be derived from fields
            assert data["unresolved_required_field_count"] == 1
            # can_approve should be False
            assert data["can_approve"] is False
            # blocking reason should mention inconsistency (aggregate=0 != derived=1)
            assert "Snapshot inconsistency" in data["approve_blocking_reason"]
        finally:
            engine.dispose()

    def test_consistent_aggregate_shows_correct(self, tmp_path: Path) -> None:
        """When aggregate matches derived, no inconsistency."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="resp-2")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            fields = [_field(status="filled")]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            with session_scope(sf) as session:
                create_approval(session, application_id=job.application_id, snapshot=snap)
            app = create_app(settings=settings)
            app.state.settings = settings
            app.state.session_factory = sf
            app.state.engine = engine
            with TestClient(app) as client:
                resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            data = resp.json()["snapshot"]
            assert data["is_complete"] is True
            assert data["unresolved_required_field_count"] == 0
            assert data["can_approve"] is True
            assert data["approve_blocking_reason"] == ""
        finally:
            engine.dispose()

    def test_high_risk_derived_from_fields(self, tmp_path: Path) -> None:
        """unconfirmed_high_risk_count is derived from field data, not aggregate."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="resp-3")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            fields = [_field(field_token="f1", risk_level="high", requires_confirmation=True)]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            with session_scope(sf) as session:
                create_approval(session, application_id=job.application_id, snapshot=snap)
            app = create_app(settings=settings)
            app.state.settings = settings
            app.state.session_factory = sf
            app.state.engine = engine
            with TestClient(app) as client:
                resp = client.get(f"/api/submit/{job.application_id}/status")
            assert resp.status_code == 200
            data = resp.json()["snapshot"]
            # Aggregate says 0, field says high-risk → inconsistency, can_approve=False
            assert data["unconfirmed_high_risk_count"] == 1
            assert data["can_approve"] is False
            assert "Snapshot inconsistency" in data["approve_blocking_reason"]
        finally:
            engine.dispose()


# ===================================================================
# Group D: Approval endpoint (tests 12-13)
# ===================================================================


class TestApprovalEndpointConsistency:
    """Tests 12-13: approval endpoint rejects inconsistent aggregates."""

    def test_rejects_inconsistent_unresolved(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="appr-1")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            fields = [_field(status="intervention_needed", required=True)]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            with session_scope(sf) as session:
                create_approval(session, application_id=job.application_id, snapshot=snap)
            app = _make_app_client(settings, engine, sf)
            with app as client:
                resp = client.post(
                    f"/api/submit/{job.application_id}/approve",
                    json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
                )
            assert resp.status_code == 409
            assert "Snapshot inconsistency" in resp.json()["detail"]
        finally:
            engine.dispose()

    def test_passes_with_consistent_data(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="appr-2")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            fields = [_field(status="filled")]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            with session_scope(sf) as session:
                create_approval(session, application_id=job.application_id, snapshot=snap)
            app = _make_app_client(settings, engine, sf)
            with app as client:
                resp = client.post(
                    f"/api/submit/{job.application_id}/approve",
                    json={"snapshot_hash": snap.snapshot_hash, "confirm": True},
                )
            assert resp.status_code == 200
        finally:
            engine.dispose()


# ===================================================================
# Group E: Coordinator gates (tests 14-16)
# ===================================================================


class TestCoordinatorGateConsistency:
    """Tests 14-16: coordinator gates derive from fields, not aggregates."""

    def test_unresolved_derived_from_fields(self, tmp_path: Path) -> None:
        """Gate 4b uses derived count, not persisted aggregate."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="coord-1")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            fields = [_field(status="intervention_needed", required=True)]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=1,
                high_risk_unconfirmed_count=0,
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            # Should block due to unresolved fields (derived from field data)
            assert "unresolved required fields" in gate.reason
        finally:
            engine.dispose()

    def test_consistency_check_blocks_with_wrong_aggregate(self, tmp_path: Path) -> None:
        """Snapshot with wrong aggregate blocked by consistency check, not field check."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="coord-2")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            fields = [_field(status="filled")]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=2,
                high_risk_unconfirmed_count=0,
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            # Should block due to inconsistency (aggregate says 2, derived says 0)
            assert "Snapshot inconsistency" in gate.reason
            assert "unresolved_required_field_count" in gate.reason
        finally:
            engine.dispose()

    def test_consistent_data_passes_all_gates(self, tmp_path: Path) -> None:
        """When aggregates match fields, all gates pass."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="coord-3")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            fields = [_field(status="filled")]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=0,
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert gate.allowed
        finally:
            engine.dispose()

    def test_high_risk_unconfirmed_blocks_when_not_confirmed(self, tmp_path: Path) -> None:
        """Gate 4c: high-risk field w/o confirmation blocks, even if aggregate=0."""
        settings = _settings(tmp_path)
        job = _job(tmp_path, suffix="coord-4")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            fields = [_field(field_token="f1", risk_level="high", requires_confirmation=True)]
            snap = _snapshot(
                job.application_id,
                fields=fields,
                unresolved_required_field_count=0,
                high_risk_unconfirmed_count=1,
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            assert "high-risk" in gate.reason
        finally:
            engine.dispose()
