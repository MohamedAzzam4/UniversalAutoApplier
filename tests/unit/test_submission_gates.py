"""Unit tests for the explicit direct gates in the SubmissionCoordinator.

These tests prove that every gate is checked DIRECTLY, not inferred from
other state. Per the workpackage requirement: "Do not rely only on the
assumption that unresolved/high-risk fields always create interventions."

Each test creates a snapshot with a specific gate-failing condition and
verifies that check_gates() returns the correct state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    SubmissionResultState,
    SubmissionSnapshot,
    SubmissionSnapshotDocument,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
)


def _make_settings(tmp_path: Path, enable: bool = True) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        data_dir=tmp_path / "uaa_gates",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=enable,
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC),
            external_job_id="gates-1",
            url="https://example.com/job/gates-1",
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test",
        title="Engineer",
        url="https://example.com/job/gates-1",
        verdict="apply",
        cv_pdf=str(tmp_path / "cv.pdf"),
        cover_letter_pdf=str(tmp_path / "cover.pdf"),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="gates-1",
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


def _make_snapshot(
    app_id: str,
    *,
    fields: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    url: str = "https://example.com/job/gates-1",
    pending: int = 0,
    submit_text: str = "Submit",
) -> SubmissionSnapshot:
    snap_fields = [
        SubmissionSnapshotField(
            field_token=f.get("field_token", "lf-1"),
            label=f.get("label", "Field"),
            field_type=f.get("field_type", "text"),
            filled_value=f.get("filled_value", ""),
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
            content_hash=d.get("content_hash", "abc"),
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
            text=submit_text, selector="button[type='submit']"
        ),
    )
    return snap.with_hashes()


class TestGateFeatureDisabled:
    def test_feature_disabled_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, enable=False)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            gate = coord.check_gates(application_id=job.application_id)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.SUBMISSION_NOT_ALLOWED
            assert "enable_real_submission" in gate.reason
        finally:
            engine.dispose()


class TestGateNoApproval:
    def test_no_approval_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            gate = coord.check_gates(application_id=job.application_id)
            assert not gate.allowed
            assert "no active approval" in gate.reason
        finally:
            engine.dispose()


class TestGateSnapshotHashMismatch:
    def test_snapshot_hash_mismatch_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(job.application_id, fields=[{"filled_value": "a"}])
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            snap2 = _make_snapshot(job.application_id, fields=[{"filled_value": "b"}])
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()


class TestGateFormFingerprintMismatch:
    def test_form_structure_change_blocks(self, tmp_path: Path) -> None:
        """Changing the form STRUCTURE (adding/removing a field) changes
        the form_fingerprint even if the snapshot_hash also changes."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(
                job.application_id,
                fields=[{"field_token": "lf-a", "filled_value": "1"}],
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            # Different structure: added a field.
            snap2 = _make_snapshot(
                job.application_id,
                fields=[
                    {"field_token": "lf-a", "filled_value": "1"},
                    {"field_token": "lf-b", "filled_value": "2"},
                ],
            )
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()


class TestGatePendingInterventions:
    def test_pending_interventions_block(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap = _make_snapshot(job.application_id, pending=1)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            from universal_auto_applier.core.statuses import InterventionKind
            from universal_auto_applier.interventions.store import create_intervention

            with session_scope(sf) as session:
                create_intervention(
                    session,
                    application_id=job.application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Q?",
                    field_selector="lf-x",
                )
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            assert "pending interventions" in gate.reason
        finally:
            engine.dispose()


class TestGateUnresolvedRequiredFields:
    def test_unresolved_required_fields_block(self, tmp_path: Path) -> None:
        """Direct gate: an unresolved-status required field blocks
        submission, even if no pending interventions exist.
        The count is derived from field data, not the persisted aggregate."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(
                job.application_id,
                fields=[{"field_token": "lf-1", "filled_value": "ok", "status": "filled"}],
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            # Create a snapshot with a field that has an unresolved status.
            snap2 = _make_snapshot(
                job.application_id,
                fields=[
                    {
                        "field_token": "lf-1",
                        "filled_value": "bad",
                        "status": "intervention_needed",
                        "required": True,
                    }
                ],
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap2)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert "unresolved required fields" in gate.reason
        finally:
            engine.dispose()


class TestGateHighRiskUnconfirmed:
    def test_high_risk_unconfirmed_blocks(self, tmp_path: Path) -> None:
        """Direct gate: a high-risk field without explicit confirmation
        blocks submission, even if no pending interventions exist."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            # Create a snapshot with a high-risk field that is NOT confirmed.
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
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            # No confirmations registered → gate blocks.
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            assert "high-risk" in gate.reason
        finally:
            engine.dispose()

    def test_high_risk_confirmed_passes(self, tmp_path: Path) -> None:
        """When the user explicitly confirms the high-risk field, the
        gate passes."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
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
            approval_id = coord.approve_snapshot(application_id=job.application_id, snapshot=snap)

            # Confirm the high-risk field.
            from universal_auto_applier.submission.store import confirm_high_risk_fields

            with session_scope(sf) as session:
                confirm_high_risk_fields(session, approval_id, ["lf-salary"])

            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert gate.allowed, f"Expected gates to pass after confirmation, got: {gate.reason}"
        finally:
            engine.dispose()

    def test_changing_answer_invalidates_confirmation(self, tmp_path: Path) -> None:
        """Changing the high-risk answer produces a new snapshot hash,
        which invalidates the entire approval (including its confirmations).
        The user must re-approve and re-confirm."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
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
            approval_id = coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            from universal_auto_applier.submission.store import confirm_high_risk_fields

            with session_scope(sf) as session:
                confirm_high_risk_fields(session, approval_id, ["lf-salary"])

            # Change the answer — new snapshot hash.
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
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()

    def test_old_confirmation_cannot_carry_over(self, tmp_path: Path) -> None:
        """Confirmations from an old approval do not carry over to a new
        approval. Approving a new snapshot revokes the old approval."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
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
            approval_id_1 = coord.approve_snapshot(
                application_id=job.application_id, snapshot=snap1
            )
            from universal_auto_applier.submission.store import confirm_high_risk_fields

            with session_scope(sf) as session:
                confirm_high_risk_fields(session, approval_id_1, ["lf-salary"])

            # Approve a new snapshot with the same field but different value.
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
            approval_id_2 = coord.approve_snapshot(
                application_id=job.application_id, snapshot=snap2
            )
            assert approval_id_1 != approval_id_2

            # The new approval has NO confirmed fields.
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert "high-risk" in gate.reason
        finally:
            engine.dispose()


class TestGateChangedDocuments:
    def test_document_change_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(
                job.application_id,
                documents=[{"path": "/old.pdf", "content_hash": "aaa"}],
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            snap2 = _make_snapshot(
                job.application_id,
                documents=[{"path": "/new.pdf", "content_hash": "bbb"}],
            )
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()


class TestGateChangedURL:
    def test_url_change_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(job.application_id, url="https://a.com")
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            snap2 = _make_snapshot(job.application_id, url="https://b.com")
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()


class TestGateChangedSubmitControl:
    def test_submit_control_change_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap1 = _make_snapshot(job.application_id, submit_text="Submit")
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap1)
            snap2 = _make_snapshot(job.application_id, submit_text="Send Application")
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap2)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()


class TestGateAlreadySubmitted:
    def test_already_submitted_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        job = job.model_copy(update={"status": ApplicationStatus.SUBMITTED})
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap = _make_snapshot(job.application_id)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.ALREADY_SUBMITTED
        finally:
            engine.dispose()


class TestGateUnknownOutcome:
    def test_unknown_outcome_blocks_retry(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap = _make_snapshot(job.application_id)
            approval_id = coord.approve_snapshot(application_id=job.application_id, snapshot=snap)

            from universal_auto_applier.submission.models import SubmissionResult
            from universal_auto_applier.submission.store import consume_approval, record_result

            result = SubmissionResult(
                application_id=job.application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=snap.snapshot_hash,
                state=SubmissionResultState.OUTCOME_UNKNOWN,
                clicked=True,
            )
            with session_scope(sf) as session:
                record_result(session, result)
                consume_approval(session, approval_id)

            gate = coord.check_gates(application_id=job.application_id)
            assert not gate.allowed
        finally:
            engine.dispose()


class TestGateAllPass:
    def test_all_gates_pass(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap = _make_snapshot(
                job.application_id,
                pending=0,
            )
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert gate.allowed, f"Expected all gates to pass, got: {gate.reason}"
        finally:
            engine.dispose()


class TestFormFingerprintIndependentOfValues:
    def test_value_change_does_not_change_fingerprint(self) -> None:
        """The form_fingerprint represents STRUCTURE only. Changing a
        field's VALUE changes the snapshot_hash but NOT the form_fingerprint."""
        snap1 = _make_snapshot("app-1", fields=[{"field_token": "lf-1", "filled_value": "a"}])
        snap2 = _make_snapshot("app-1", fields=[{"field_token": "lf-1", "filled_value": "b"}])
        assert snap1.form_fingerprint == snap2.form_fingerprint
        assert snap1.snapshot_hash != snap2.snapshot_hash

    def test_structure_change_changes_fingerprint(self) -> None:
        snap1 = _make_snapshot(
            "app-1",
            fields=[{"field_token": "lf-1", "filled_value": "a"}],
        )
        snap2 = _make_snapshot(
            "app-1",
            fields=[
                {"field_token": "lf-1", "filled_value": "a"},
                {"field_token": "lf-2", "filled_value": "b"},
            ],
        )
        assert snap1.form_fingerprint != snap2.form_fingerprint
        assert snap1.snapshot_hash != snap2.snapshot_hash
