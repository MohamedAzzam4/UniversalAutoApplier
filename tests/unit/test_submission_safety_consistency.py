"""Unit tests: snapshot safety state is derived from field data, not aggregates.

These 16 tests prove that every safety gate (unresolved required fields,
unconfirmed high-risk answers, completeness, consistency) is computed from
the ACTUAL field records, never from the persisted static aggregates.

If a persisted aggregate contradicts the field-level data, the system
MUST reject the state (cannot approve, cannot submit) and return an
actionable blocking reason.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from universal_auto_applier.api.app import create_app
from universal_auto_applier.browser.live_models import LiveFieldRecord, LiveUploadRecord
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
    SubmissionSnapshotDocument,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
    build_snapshot_from_report,
    check_snapshot_consistency,
    derive_is_complete,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
    derive_unresolved_upload_count,
    has_final_boundary_evidence,
    has_progress_metadata,
    is_review_ready_snapshot,
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
        final_boundary_confirmed=True,
        completed_form_step_count=1,
        form_progress_fingerprint="safety-test-form-progress",
        **kw,
    )
    return snap.with_hashes()


def _make_app_client(settings: Settings, engine: Any, sf: Any):
    app = create_app(settings=settings)
    app.state.settings = settings
    app.state.session_factory = sf
    app.state.engine = engine
    return TestClient(app)


def test_legacy_snapshot_hash_shape_survives_new_boundary_metadata_defaults() -> None:
    """Pre-progress approvals retain the exact old canonical hash payload."""
    original = _snapshot("legacy-boundary", fields=[_field(field_token="legacy-field")])
    original = original.model_copy(
        update={
            "final_boundary_confirmed": False,
            "completed_form_step_count": 0,
            "form_progress_fingerprint": "",
        }
    )

    # Independently reconstruct the pre-progress canonical contract from the
    # base model: no boundary keys and no step/source identity field keys.
    old_structure = sorted(
        [
            {
                "token": field.field_token,
                "type": field.field_type,
                "label": field.label,
                "required": field.required,
            }
            for field in original.fields
        ],
        key=lambda item: item.get("token", ""),
    )
    old_form = {
        "fields": old_structure,
        "doc_kinds": sorted(document.document_kind for document in original.documents),
        "submit_control": (
            {
                "text": original.submit_control.text,
                "selector": original.submit_control.selector,
                "frame_url": original.submit_control.frame_url,
            }
            if original.submit_control
            else None
        ),
    }
    old_snapshot = {
        "application_id": original.application_id,
        "application_url": original.application_url,
        "fields": sorted(
            [
                field.model_dump(exclude={"source_field_token", "step_identity"})
                for field in original.fields
            ],
            key=lambda item: item.get("field_token", ""),
        ),
        "documents": sorted(
            [document.model_dump() for document in original.documents],
            key=lambda item: (item.get("document_kind", ""), item.get("path", "")),
        ),
        "pending_intervention_count": original.pending_intervention_count,
        "unresolved_required_field_count": original.unresolved_required_field_count,
        "unresolved_upload_count": original.unresolved_upload_count,
        "high_risk_unconfirmed_count": original.high_risk_unconfirmed_count,
        "submit_control": original.submit_control.model_dump() if original.submit_control else None,
    }
    expected_form_fingerprint = hashlib.sha256(
        json.dumps(old_form, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]
    expected_snapshot_hash = hashlib.sha256(
        json.dumps(old_snapshot, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:32]

    legacy_json = original.model_dump()
    for key in (
        "final_boundary_confirmed",
        "completed_form_step_count",
        "form_progress_fingerprint",
    ):
        legacy_json.pop(key, None)
    for field in legacy_json["fields"]:
        field.pop("source_field_token", None)
        field.pop("step_identity", None)

    reloaded = SubmissionSnapshot.model_validate(legacy_json)

    assert reloaded.final_boundary_confirmed is False
    assert reloaded.completed_form_step_count == 0
    assert reloaded.form_progress_fingerprint == ""
    assert reloaded.compute_form_fingerprint() == expected_form_fingerprint
    assert reloaded.compute_hash() == expected_snapshot_hash
    assert has_final_boundary_evidence(reloaded) is False
    assert is_review_ready_snapshot(reloaded) is False


def test_snapshot_scopes_reused_field_tokens_by_step_and_hashes_the_identity() -> None:
    fields = [
        LiveFieldRecord(
            page_url="https://example.test/apply",
            selector="input[name='answer']",
            label="Name",
            field_type="text",
            status="filled",
            field_token="reused-token",
            step_identity=step_identity,
            filled_value=value,
        )
        for step_identity, value in (("a" * 64, "First"), ("b" * 64, "Second"))
    ]
    snapshot = build_snapshot_from_report(
        application_id="step-scoped-snapshot",
        application_url="https://example.test/apply",
        fields=fields,
        uploads=[],
        pending_intervention_count=0,
        submit_control_text="Submit Application",
        submit_control_selector="#submit",
        final_boundary_confirmed=True,
        completed_form_step_count=2,
        form_progress_fingerprint="c" * 64,
    )

    assert len(snapshot.fields) == 2
    assert len({field.field_token for field in snapshot.fields}) == 2
    assert {field.source_field_token for field in snapshot.fields} == {"reused-token"}
    assert {field.step_identity for field in snapshot.fields} == {"a" * 64, "b" * 64}

    changed_identity_fields = [
        field.model_copy(update={"step_identity": "d" * 64}) for field in snapshot.fields
    ]
    changed_identity = snapshot.model_copy(update={"fields": changed_identity_fields})
    assert changed_identity.compute_hash() != snapshot.snapshot_hash
    assert changed_identity.compute_form_fingerprint() != snapshot.form_fingerprint


def test_partial_source_token_metadata_is_hashed_and_not_legacy() -> None:
    observed = _snapshot("partial-identity", fields=[_field(field_token="lf-1")])
    baseline = observed.model_copy(
        update={
            "final_boundary_confirmed": False,
            "completed_form_step_count": 0,
            "form_progress_fingerprint": "",
        }
    ).with_hashes()
    source_only_field = baseline.fields[0].model_copy(
        update={"source_field_token": "original-token", "step_identity": ""}
    )
    source_only = baseline.model_copy(update={"fields": [source_only_field]}).with_hashes()
    changed_source_field = source_only_field.model_copy(
        update={"source_field_token": "changed-token"}
    )
    changed_source = baseline.model_copy(update={"fields": [changed_source_field]}).with_hashes()

    assert has_progress_metadata(baseline) is False
    assert has_progress_metadata(source_only) is True
    assert source_only.snapshot_hash != baseline.snapshot_hash
    assert source_only.snapshot_hash != changed_source.snapshot_hash
    assert source_only.form_fingerprint != changed_source.form_fingerprint


def test_review_readiness_requires_a_bound_final_boundary() -> None:
    incomplete = _snapshot("missing-boundary", fields=[_field(field_token="field")])
    incomplete = incomplete.model_copy(
        update={
            "final_boundary_confirmed": False,
            "completed_form_step_count": 0,
            "form_progress_fingerprint": "",
        }
    ).with_hashes()
    complete = SubmissionSnapshot(
        application_id="complete-boundary",
        application_url="https://example.com/apply",
        fields=[_field(field_token="field")],
        documents=[],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit Application", selector="#submit"
        ),
        final_boundary_confirmed=True,
        completed_form_step_count=3,
        form_progress_fingerprint="a" * 64,
    ).with_hashes()

    assert is_review_ready_snapshot(incomplete) is False
    assert has_final_boundary_evidence(complete) is True
    assert is_review_ready_snapshot(complete) is True


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


class TestLiveUploadEvidenceContract:
    @pytest.mark.parametrize(
        ("status", "selected_file_names", "evidence_source"),
        [
            ("remote_accepted", ["cv.pdf"], "native_selection"),
            ("remote_accepted", [], "declared_site_status"),
            ("selection_verified", [], "native_selection"),
        ],
    )
    def test_success_status_requires_matching_selection_and_source(
        self,
        status: str,
        selected_file_names: list[str],
        evidence_source: str,
    ) -> None:
        with pytest.raises(ValidationError):
            LiveUploadRecord(
                page_url="https://uaa.test/apply",
                selector="input[type=file]",
                document_kind="cv",
                path="/candidate/cv.pdf",
                status=status,
                selected_file_names=selected_file_names,
                evidence_source=evidence_source,
                evidence_detail="Untrusted upload claim.",
            )

    def test_native_selection_is_visible_but_unqualified_selection_is_unresolved(self) -> None:
        upload = LiveUploadRecord(
            page_url="https://uaa.test/apply",
            selector="#cv",
            document_kind="cv",
            path="/candidate/cv.pdf",
            status="selection_verified",
            selected_file_names=["cv.pdf"],
            evidence_source="native_selection",
            evidence_detail="The native input exposes cv.pdf.",
        )
        document = SubmissionSnapshotDocument(
            document_kind=upload.document_kind,
            path=upload.path,
            status=upload.status,
            upload_contract=upload.upload_contract,
            selected_file_names=upload.selected_file_names,
            evidence_source=upload.evidence_source,
            evidence_detail=upload.evidence_detail,
        )
        assert upload.status == "selection_verified"
        assert upload.upload_contract is None
        assert derive_unresolved_upload_count([document]) == 1

    def test_declared_native_final_submit_contract_resolves_selection(self) -> None:
        upload = LiveUploadRecord(
            page_url="https://uaa.test/apply",
            selector="#cv",
            document_kind="cv",
            path="/candidate/cv.pdf",
            status="selection_verified",
            upload_contract="native_final_submit",
            selected_file_names=["cv.pdf"],
            evidence_source="native_selection",
            evidence_detail="The qualified final-submit flow includes this file input.",
        )
        document = SubmissionSnapshotDocument(
            document_kind=upload.document_kind,
            path=upload.path,
            status=upload.status,
            upload_contract=upload.upload_contract,
            selected_file_names=upload.selected_file_names,
            evidence_source=upload.evidence_source,
            evidence_detail=upload.evidence_detail,
        )
        assert derive_unresolved_upload_count([document]) == 0

    def test_native_contract_cannot_qualify_remote_acceptance(self) -> None:
        with pytest.raises(ValidationError):
            LiveUploadRecord(
                page_url="https://uaa.test/apply",
                selector="#cv",
                document_kind="cv",
                path="/candidate/cv.pdf",
                status="remote_accepted",
                upload_contract="native_final_submit",
                selected_file_names=["cv.pdf"],
                evidence_source="declared_site_status",
                evidence_detail="Remote accepted.",
            )


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
