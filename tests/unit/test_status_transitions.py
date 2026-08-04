"""Unit tests for the authoritative post-submit status transition policy.

Covers WQ-1: ``submission/status_transitions.py`` and its wiring into
``submission/store.record_result``. Verifies:

- the typed mapping from :class:`SubmissionResult` states to
  :class:`ApplicationStatus` (including the structured-ATS-reference path
  to ``APPLIED``);
- that pre-click/failed/blocked/stale/validation outcomes never set
  ``SUBMITTED``/``APPLIED``;
- idempotent replay and terminal-status protection (never downgrade);
- that the transition is persisted in the same transaction as the result.
"""

from __future__ import annotations

from pathlib import Path

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
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
)
from universal_auto_applier.submission.status_transitions import (
    apply_result_status_transition,
    target_status_for_result,
)
from universal_auto_applier.submission.store import record_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    tmp_path: Path,
    status: ApplicationStatus = ApplicationStatus.REVIEW_READY,
) -> ApplicationJob:
    url = "https://example.com/job/status-transition"
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="wq1-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        status=status,
        external_job_id="wq1-1",
        metadata={},
    )


def _setup_db(tmp_path: Path, job: ApplicationJob):
    """Fresh DB with the job seeded."""
    settings = Settings(
        host="127.0.0.1",
        port=8050,
        data_dir=tmp_path / "uaa_transition",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    return engine, sf


def _make_result(
    application_id: str,
    state: SubmissionResultState,
    *,
    clicked: bool = False,
) -> SubmissionResult:
    return SubmissionResult(
        application_id=application_id,
        approval_id="apr-wq1-test",
        snapshot_hash_at_submit="snap-hash-1",
        state=state,
        clicked=clicked,
    )


def _job_status(sf, application_id: str) -> ApplicationStatus:
    with session_scope(sf) as session:
        job = get_application_job(session, application_id)
        assert job is not None
        return job.status


# ---------------------------------------------------------------------------
# 1. Pure policy mapping
# ---------------------------------------------------------------------------


class TestTargetStatusForResult:
    def test_submitted_confirmed_maps_to_submitted(self) -> None:
        result = _make_result("a", SubmissionResultState.SUBMITTED_CONFIRMED, clicked=True)
        assert target_status_for_result(result) == ApplicationStatus.SUBMITTED

    def test_submitted_confirmed_with_structured_ref_maps_to_applied(self) -> None:
        result = _make_result("a", SubmissionResultState.SUBMITTED_CONFIRMED, clicked=True)
        assert (
            target_status_for_result(result, ats_reference_id="ATS-REF-2024-001")
            == ApplicationStatus.APPLIED
        )

    def test_outcome_unknown_maps_to_needs_review(self) -> None:
        result = _make_result("a", SubmissionResultState.OUTCOME_UNKNOWN, clicked=True)
        assert target_status_for_result(result) == ApplicationStatus.NEEDS_REVIEW

    def test_pre_click_and_failed_states_never_submitted(self) -> None:
        for state in (
            SubmissionResultState.VALIDATION_FAILED,
            SubmissionResultState.BLOCKED_USER_ACTION,
            SubmissionResultState.APPROVAL_STALE,
            SubmissionResultState.SUBMISSION_NOT_ALLOWED,
            SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS,
            SubmissionResultState.ALREADY_SUBMITTED,
        ):
            result = _make_result("a", state)
            assert target_status_for_result(result) is None, state.value

    def test_no_transition_ever_applied_without_confirmation(self) -> None:
        for state in (
            SubmissionResultState.VALIDATION_FAILED,
            SubmissionResultState.BLOCKED_USER_ACTION,
            SubmissionResultState.APPROVAL_STALE,
            SubmissionResultState.SUBMISSION_NOT_ALLOWED,
            SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS,
        ):
            result = _make_result("a", state)
            assert target_status_for_result(result, ats_reference_id="ATS-REF") is None, state.value


# ---------------------------------------------------------------------------
# 2. record_result wiring (transactional persistence)
# ---------------------------------------------------------------------------


class TestRecordResultTransitions:
    def test_submitted_confirmed_sets_submitted(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.SUBMITTED
        finally:
            engine.dispose()

    def test_structured_ref_sets_applied(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                    ),
                    ats_reference_id="ATS-REF-2024-001",
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
        finally:
            engine.dispose()

    def test_outcome_unknown_sets_needs_review(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.OUTCOME_UNKNOWN,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.NEEDS_REVIEW
        finally:
            engine.dispose()

    def test_validation_failed_keeps_review_ready(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.VALIDATION_FAILED,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
        finally:
            engine.dispose()

    def test_blocked_user_action_keeps_review_ready(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.BLOCKED_USER_ACTION,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
        finally:
            engine.dispose()

    def test_submission_not_allowed_keeps_review_ready(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
        finally:
            engine.dispose()

    def test_approval_stale_keeps_review_ready(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.APPROVAL_STALE,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
        finally:
            engine.dispose()

    def test_submit_control_ambiguous_keeps_review_ready(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
        finally:
            engine.dispose()

    def test_replay_is_idempotent(self, tmp_path: Path) -> None:
        """Recording the same result twice must not change the status twice."""
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            result = _make_result(
                job.application_id,
                SubmissionResultState.SUBMITTED_CONFIRMED,
                clicked=True,
            )
            with session_scope(sf) as session:
                record_result(session, result)
            assert _job_status(sf, job.application_id) == ApplicationStatus.SUBMITTED
            with session_scope(sf) as session:
                record_result(session, result)  # replay: no-op
            assert _job_status(sf, job.application_id) == ApplicationStatus.SUBMITTED
        finally:
            engine.dispose()

    def test_terminal_applied_never_downgraded(self, tmp_path: Path) -> None:
        """A replay/outcome after APPLIED must never downgrade the status."""
        job = _make_job(tmp_path, status=ApplicationStatus.APPLIED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            # outcome_unknown on an already-applied job must NOT downgrade.
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.OUTCOME_UNKNOWN,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
        finally:
            engine.dispose()

    def test_terminal_rejected_never_downgraded(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.REJECTED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.REJECTED
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 3. Direct apply function edge cases
# ---------------------------------------------------------------------------


class TestApplyFunctionEdgeCases:
    def test_missing_job_returns_none(self, tmp_path: Path) -> None:
        engine, sf = _setup_db(tmp_path, _make_job(tmp_path))
        try:
            with session_scope(sf) as session:
                result = _make_result(
                    "nonexistent-application-id",
                    SubmissionResultState.SUBMITTED_CONFIRMED,
                    clicked=True,
                )
                assert apply_result_status_transition(session, result) is None
        finally:
            engine.dispose()

    def test_already_submitted_result_keeps_submitted(self, tmp_path: Path) -> None:
        """already_submitted outcome never moves the job off SUBMITTED."""
        job = _make_job(tmp_path, status=ApplicationStatus.SUBMITTED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.ALREADY_SUBMITTED,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.SUBMITTED
        finally:
            engine.dispose()

    def test_submitted_job_with_unknown_outcome_becomes_needs_review(self, tmp_path: Path) -> None:
        """A submitted job with a later ambiguous outcome -> needs_review."""
        job = _make_job(tmp_path, status=ApplicationStatus.SUBMITTED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.OUTCOME_UNKNOWN,
                        clicked=True,
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.NEEDS_REVIEW
        finally:
            engine.dispose()
