"""Hermetic tests for the WQ-8 single-use real-submission authorization.

Cover:
- Review-plan hash determinism (identical plans hash identically,
  generated_at excluded, machine-independent paths).
- Authorization store (create/refuse/idempotent/revoke/consume, absolute
  one-submission limit, expiry, wrong-app refusal).
- Coordinator DB-side WQ-8 gate (no-op without authorization, block when
  authorization is consumed/revoked/expired/mismatched).

These tests never click a submit control and never contact a real site.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

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
from universal_auto_applier.persistence.job_repository import (
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.authorization import (
    build_review_plan,
    compute_frozen_review_plan_hash,
    compute_review_plan_hash,
)
from universal_auto_applier.submission.authorization_store import (
    consume_authorization,
    create_authorization,
    get_active_authorization,
    get_authorization_for_plan,
    revoke_authorization,
)
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
        data_dir=tmp_path / "uaa_wq8",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=enable,
    )


def _make_job(tmp_path: Path, *, external_id: str = "wq8-1") -> ApplicationJob:
    url = f"https://ats.example.com/jobs/{external_id}"
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4 wq8 cv")
    cover_pdf.write_bytes(b"%PDF-1.4 wq8 cover")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="WQ8 Company",
        title="Working Student Data",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id=external_id,
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
    url: str = "https://ats.example.com/jobs/wq8-1",
    fields: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
    pending: int = 0,
    submit_text: str = "Submit Application",
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
            content_hash=d.get("content_hash", "doc-hash"),
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


def _future(hours: float = 24) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _plan_hash_for_snapshot(job: ApplicationJob, snapshot: SubmissionSnapshot) -> str:
    """Compute the frozen plan hash exactly as the coordinator binds it
    (submit-control + intervention params included)."""
    return compute_frozen_review_plan_hash(
        application_id=snapshot.application_id,
        company=job.company,
        job_title=job.title,
        application_url=snapshot.application_url,
        fields=snapshot.fields,
        documents=snapshot.documents,
        submit_control_text=(snapshot.submit_control.text if snapshot.submit_control else ""),
        submit_control_selector=(
            snapshot.submit_control.selector if snapshot.submit_control else ""
        ),
        submit_control_frame_url=(
            snapshot.submit_control.frame_url if snapshot.submit_control else ""
        ),
        pending_intervention_count=snapshot.pending_intervention_count,
    )


# ---------------------------------------------------------------------------
# Review-plan hash determinism
# ---------------------------------------------------------------------------


class TestReviewPlanHashDeterminism:
    def _fields(self) -> list[dict[str, Any]]:
        return [
            {"field_token": "b", "filled_value": "x", "status": "filled"},
            {"field_token": "a", "filled_value": "y", "status": "filled"},
        ]

    def _docs(self) -> list[dict[str, Any]]:
        return [
            {"document_kind": "cv", "path": "/home/user/cv.pdf", "content_hash": "h1"},
            {"document_kind": "cover", "path": "/home/user/cover.pdf", "content_hash": "h2"},
        ]

    def test_identical_plans_hash_identically(self) -> None:
        """Different call order / generated_at must produce the same hash."""
        p1 = build_review_plan(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=self._docs(),
            submit_control_text="Apply",
            submit_control_selector="button.a",
        )
        p2 = build_review_plan(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=list(reversed(self._fields())),
            documents=list(reversed(self._docs())),
            submit_control_text="Apply",
            submit_control_selector="button.a",
        )
        assert compute_review_plan_hash(p1) == compute_review_plan_hash(p2)

    def test_generated_at_is_excluded(self) -> None:
        p1 = build_review_plan(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=self._docs(),
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        p2 = build_review_plan(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=self._docs(),
            generated_at=datetime(2025, 6, 6, tzinfo=UTC),
        )
        assert compute_review_plan_hash(p1) == compute_review_plan_hash(p2)

    def test_different_plan_hashes_differently(self) -> None:
        a = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=self._docs(),
            submit_control_text="Apply",
            submit_control_selector="button.a",
        )
        b = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job2",
            fields=self._fields(),
            documents=self._docs(),
            submit_control_text="Apply",
            submit_control_selector="button.a",
        )
        assert a != b

    def test_document_path_reduced_to_filename(self) -> None:
        """Different absolute paths for the same file must not change the hash."""
        docs_a = [
            {"document_kind": "cv", "path": "/a/b/cv.pdf", "content_hash": "h1"},
        ]
        docs_b = [
            {"document_kind": "cv", "path": "C:/different/cv.pdf", "content_hash": "h1"},
        ]
        a = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=docs_a,
        )
        b = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=docs_b,
        )
        assert a == b

    def test_changed_document_content_hash_changes_hash(self) -> None:
        docs_a = [
            {"document_kind": "cv", "path": "/cv.pdf", "content_hash": "h1"},
        ]
        docs_b = [
            {"document_kind": "cv", "path": "/cv.pdf", "content_hash": "h1-CHANGED"},
        ]
        a = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=docs_a,
        )
        b = compute_frozen_review_plan_hash(
            application_id="app-1",
            company="Acme",
            job_title="Engineer",
            application_url="https://x/job",
            fields=self._fields(),
            documents=docs_b,
        )
        assert a != b


# ---------------------------------------------------------------------------
# Authorization store
# ---------------------------------------------------------------------------


class TestAuthorizationStore:
    def _create(self, session, job: ApplicationJob, app_url: str) -> Any:
        snapshot = _make_snapshot(job.application_id, url=app_url)
        plan_hash = _plan_hash_for_snapshot(job, snapshot)
        doc_hashes = sorted(d.content_hash for d in snapshot.documents if d.content_hash)
        return (
            create_authorization(
                session,
                application_id=job.application_id,
                application_url=app_url,
                job_company=job.company,
                job_title=job.title,
                review_plan_hash=plan_hash,
                document_hashes=doc_hashes,
                expires_at=_future(),
            ),
            plan_hash,
            doc_hashes,
        )

    def test_create_and_get(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                auth, plan_hash, _ = self._create(session, job, job.url)
                assert auth.application_id == job.application_id
                assert auth.review_plan_hash == plan_hash
                assert get_active_authorization(session, job.application_id) is not None
                assert (
                    get_authorization_for_plan(session, job.application_id, plan_hash) is not None
                )
        finally:
            engine.dispose()

    def test_idempotent_create(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                auth1, _, _ = self._create(session, job, job.url)
                auth2, _, _ = self._create(session, job, job.url)
                assert auth1.authorization_id == auth2.authorization_id
        finally:
            engine.dispose()

    def test_refuses_past_expiry(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = compute_frozen_review_plan_hash(
                    application_id=job.application_id,
                    company=job.company,
                    job_title=job.title,
                    application_url=job.url,
                    fields=snapshot.fields,
                    documents=snapshot.documents,
                )
                with pytest.raises(ValueError):
                    create_authorization(
                        session,
                        application_id=job.application_id,
                        application_url=job.url,
                        job_company=job.company,
                        job_title=job.title,
                        review_plan_hash=plan_hash,
                        document_hashes=sorted(d.content_hash for d in snapshot.documents),
                        expires_at=datetime.now(UTC) - timedelta(hours=1),
                    )
        finally:
            engine.dispose()

    def test_refuses_second_app_after_converted(self, tmp_path: Path) -> None:
        """The absolute one-submission limit: after a clicked attempt exists
        anywhere, no new authorization can be created."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        job2 = _make_job(tmp_path, external_id="wq8-2")
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                upsert_application_job(session, job2)
            with session_scope(sf) as session:
                auth, plan_hash, doc_hashes = self._create(session, job, job.url)
                assert auth is not None
                # Simulate a converted submission for job2 so the absolute
                # limit registers.
                from universal_auto_applier.submission.models import SubmissionResult
                from universal_auto_applier.submission.store import record_result

                record_result(
                    session,
                    SubmissionResult(
                        application_id=job2.application_id,
                        approval_id="approval-other",
                        snapshot_hash_at_submit="hash",
                        state=SubmissionResultState.OUTCOME_UNKNOWN,
                        clicked=True,
                        error_message="simulated converted attempt",
                    ),
                )
                snapshot = _make_snapshot(job2.application_id, url=job2.url)
                with pytest.raises(ValueError):
                    create_authorization(
                        session,
                        application_id=job2.application_id,
                        application_url=job2.url,
                        job_company=job2.company,
                        job_title=job2.title,
                        review_plan_hash=compute_frozen_review_plan_hash(
                            application_id=job2.application_id,
                            company=job2.company,
                            job_title=job2.title,
                            application_url=job2.url,
                            fields=snapshot.fields,
                            documents=snapshot.documents,
                        ),
                        document_hashes=sorted(
                            d.content_hash for d in snapshot.documents if d.content_hash
                        ),
                        expires_at=_future(),
                    )
        finally:
            engine.dispose()

    def test_consume_is_single_use(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                auth, _, _ = self._create(session, job, job.url)
                assert consume_authorization(session, auth.authorization_id) is True
                assert consume_authorization(session, auth.authorization_id) is False
                assert get_active_authorization(session, job.application_id) is None
        finally:
            engine.dispose()

    def test_expired_authorization_is_not_active(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = compute_frozen_review_plan_hash(
                    application_id=job.application_id,
                    company=job.company,
                    job_title=job.title,
                    application_url=job.url,
                    fields=snapshot.fields,
                    documents=snapshot.documents,
                )
                doc_hashes = sorted(d.content_hash for d in snapshot.documents if d.content_hash)
                auth = create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=doc_hashes,
                    expires_at=_future(0.0001),
                )
                # Backdate past expiry by rewriting the row directly (store
                # never sets it, but the DB value governs get_active_authorization).
                auth.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                session.flush()
                assert get_active_authorization(session, job.application_id) is None
                assert consume_authorization(session, auth.authorization_id) is False
        finally:
            engine.dispose()

    def test_revoke_makes_inactive(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                auth, plan_hash, _ = self._create(session, job, job.url)
                revoke_authorization(session, auth.authorization_id)
                assert get_active_authorization(session, job.application_id) is None
                assert (
                    get_authorization_for_plan(session, job.application_id, plan_hash) is not None
                )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Coordinator DB-side WQ-8 gate
# ---------------------------------------------------------------------------


class TestCoordinatorWQ8Gate:
    def test_no_authorization_is_no_op(self, tmp_path: Path) -> None:
        """Without an authorization the gate is a no-op — existing behavior."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            coord = SubmissionCoordinator(settings, sf)
            snap = _make_snapshot(job.application_id, url=job.url)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snap)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snap)
            assert gate.allowed
        finally:
            engine.dispose()

    def test_consumed_authorization_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = _plan_hash_for_snapshot(job, snapshot)
                auth = create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=sorted(
                        d.content_hash for d in snapshot.documents if d.content_hash
                    ),
                    expires_at=_future(),
                )
            coord = SubmissionCoordinator(settings, sf)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snapshot)
            with session_scope(sf) as session:
                consume_authorization(session, auth.authorization_id)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snapshot)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.SUBMISSION_NOT_ALLOWED
        finally:
            engine.dispose()

    def test_revoked_authorization_blocks(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = _plan_hash_for_snapshot(job, snapshot)
                auth = create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=sorted(
                        d.content_hash for d in snapshot.documents if d.content_hash
                    ),
                    expires_at=_future(),
                )
                revoke_authorization(session, auth.authorization_id)
            coord = SubmissionCoordinator(settings, sf)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snapshot)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snapshot)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.SUBMISSION_NOT_ALLOWED
        finally:
            engine.dispose()

    def test_mismatched_plan_hash_blocks(self, tmp_path: Path) -> None:
        """A changed plan (e.g. URL override) invalidates the authorization."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            # Authorize against job.url.
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = _plan_hash_for_snapshot(job, snapshot)
                create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=sorted(
                        d.content_hash for d in snapshot.documents if d.content_hash
                    ),
                    expires_at=_future(),
                )
            # The current snapshot differs (different URL) => plan hash changes.
            coord = SubmissionCoordinator(settings, sf)
            changed = _make_snapshot(job.application_id, url="https://ats.example.com/diff")
            coord.approve_snapshot(application_id=job.application_id, snapshot=changed)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=changed)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()

    def test_active_matching_authorization_passes(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = _plan_hash_for_snapshot(job, snapshot)
                create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=sorted(
                        d.content_hash for d in snapshot.documents if d.content_hash
                    ),
                    expires_at=_future(),
                )
            coord = SubmissionCoordinator(settings, sf)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snapshot)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snapshot)
            assert gate.allowed
        finally:
            engine.dispose()

    def test_validate_binding_uses_live_job_identity(self, tmp_path: Path) -> None:
        """The binding must match job company + title from the LIVE page/job."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        engine, sf = _setup(tmp_path, settings, job)
        try:
            with session_scope(sf) as session:
                snapshot = _make_snapshot(job.application_id, url=job.url)
                plan_hash = _plan_hash_for_snapshot(job, snapshot)
                create_authorization(
                    session,
                    application_id=job.application_id,
                    application_url=job.url,
                    job_company="DIFFERENT-COMPANY",
                    job_title=job.title,
                    review_plan_hash=plan_hash,
                    document_hashes=sorted(
                        d.content_hash for d in snapshot.documents if d.content_hash
                    ),
                    expires_at=_future(),
                )
            coord = SubmissionCoordinator(settings, sf)
            coord.approve_snapshot(application_id=job.application_id, snapshot=snapshot)
            gate = coord.check_gates(application_id=job.application_id, current_snapshot=snapshot)
            assert not gate.allowed
            assert gate.state == SubmissionResultState.APPROVAL_STALE
        finally:
            engine.dispose()
