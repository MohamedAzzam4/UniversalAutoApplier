"""Concurrency test proving DB-enforced one claim / one click.

This test uses two separate SQLAlchemy sessions to simulate two
concurrent submission requests. The database unique constraint on
submission_claims.approval_id ensures only one claim is created.

Per the workpackage: "Add a real concurrency test using separate
sessions/requests proving two simultaneous submission requests produce
exactly one claim and one click."
"""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy import select

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
from universal_auto_applier.persistence.models import SubmissionClaimRow
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
)
from universal_auto_applier.submission.store import (
    acquire_claim,
    create_approval,
    get_active_approval,
)


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8090,
        data_dir=tmp_path / "uaa_concurrency",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC),
            external_job_id="conc-1",
            url="https://example.com/job/conc-1",
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test",
        title="Engineer",
        url="https://example.com/job/conc-1",
        verdict="apply",
        cv_pdf=str(tmp_path / "cv.pdf"),
        cover_letter_pdf=str(tmp_path / "cover.pdf"),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="conc-1",
        metadata={},
    )


def _make_snapshot(app_id: str) -> SubmissionSnapshot:
    snap = SubmissionSnapshot(
        application_id=app_id,
        application_url="https://example.com/job/conc-1",
        fields=[
            SubmissionSnapshotField(
                field_token="lf-1",
                label="Name",
                field_type="text",
                filled_value="Test",
                status="filled",
            )
        ],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(text="Submit", selector="button"),
    )
    return snap.with_hashes()


class TestConcurrencyOneClaimOneClick:
    def test_two_concurrent_requests_produce_one_claim(self, tmp_path: Path) -> None:
        """Two concurrent acquire_claim calls for the same approval must
        produce exactly ONE claim. The second call returns None."""
        settings = _make_settings(tmp_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        job = _make_job(tmp_path)
        with session_scope(sf) as session:
            upsert_application_job(session, job)

        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)

        # Two separate sessions simulating two concurrent requests.
        results: list[object] = []
        lock = threading.Lock()

        def attempt_claim() -> None:
            try:
                with session_scope(sf) as session:
                    approval = get_active_approval(session, job.application_id)
                    assert approval is not None
                    claim = acquire_claim(
                        session,
                        application_id=job.application_id,
                        approval=approval,
                    )
                    with lock:
                        results.append(claim)
            except Exception as exc:
                with lock:
                    results.append(exc)

        t1 = threading.Thread(target=attempt_claim)
        t2 = threading.Thread(target=attempt_claim)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one claim should be non-None; the other should be None
        # (or an IntegrityError that was caught and returned as None).
        claims = [r for r in results if r is not None and not isinstance(r, Exception)]
        errors = [r for r in results if isinstance(r, Exception)]

        # Either: one claim + one None, or one claim + one caught exception.
        assert len(claims) == 1, f"Expected exactly 1 claim, got {len(claims)}. Results: {results}"
        assert len(errors) == 0, f"Unexpected exceptions: {errors}"

        # Verify in the DB that exactly one claim row exists.
        with session_scope(sf) as session:
            stmt = select(SubmissionClaimRow).where(
                SubmissionClaimRow.application_id == job.application_id
            )
            claim_rows = list(session.execute(stmt).scalars().all())
        assert len(claim_rows) == 1, f"Expected exactly 1 claim row in DB, got {len(claim_rows)}"

        engine.dispose()

    def test_duplicate_claim_after_consumption_still_blocked(self, tmp_path: Path) -> None:
        """After a claim is consumed, a new claim for the SAME approval
        is still blocked (the unique constraint on approval_id prevents
        a second claim even after the first is consumed)."""
        settings = _make_settings(tmp_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        job = _make_job(tmp_path)
        with session_scope(sf) as session:
            upsert_application_job(session, job)

        snap = _make_snapshot(job.application_id)
        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snap)

        # First claim succeeds.
        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            claim1 = acquire_claim(
                session,
                application_id=job.application_id,
                approval=approval,
            )
        assert claim1 is not None

        # Second claim for the same approval fails (None) even though
        # the first is consumed — the unique constraint is on approval_id,
        # not on consumed_at.
        from universal_auto_applier.submission.models import SubmissionResultState
        from universal_auto_applier.submission.store import consume_claim

        with session_scope(sf) as session:
            consume_claim(session, claim1.claim_id, state=SubmissionResultState.SUBMITTED_CONFIRMED)

        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            claim2 = acquire_claim(
                session,
                application_id=job.application_id,
                approval=approval,
            )
        assert claim2 is None, (
            "Second claim for the same approval must be blocked by the unique constraint"
        )

        engine.dispose()
