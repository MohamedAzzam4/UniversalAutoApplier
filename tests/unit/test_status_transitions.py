"""Unit tests for the authoritative post-submit status transition policy.

Covers WQ-1: ``submission/status_transitions.py`` and its wiring into
``submission/store.record_result``. Verifies:

- the typed mapping from :class:`SubmissionResult` states to
  :class:`ApplicationStatus` (including the structured-ATS-reference path
  to ``APPLIED``);
- that pre-click/failed/blocked/stale/validation outcomes never set
  ``SUBMITTED``/``APPLIED``;
- that only the EXPLICIT post-submission edges are applied and earlier
  pipeline statuses are never auto-advanced by a result;
- idempotent replay, terminal-status protection (never downgrade), and
  persistence of the structured ``ats_reference_id`` across restarts;
- transactional rollback when a transition invariant fails.
"""

from __future__ import annotations

from pathlib import Path

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
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
)
from universal_auto_applier.submission.status_transitions import (
    POST_SUBMIT_TRANSITIONS,
    apply_result_status_transition,
    target_status_for_result,
)
from universal_auto_applier.submission.store import get_latest_result, record_result

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
    ats_reference_id: str = "",
    approval_id: str = "apr-wq1-test",
) -> SubmissionResult:
    return SubmissionResult(
        application_id=application_id,
        approval_id=approval_id,
        snapshot_hash_at_submit="snap-hash-1",
        state=state,
        clicked=clicked,
        ats_reference_id=ats_reference_id,
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
        result = _make_result(
            "a",
            SubmissionResultState.SUBMITTED_CONFIRMED,
            clicked=True,
            ats_reference_id="ATS-REF-2024-001",
        )
        assert target_status_for_result(result) == ApplicationStatus.APPLIED

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
            result = _make_result("a", state, ats_reference_id="ATS-REF")
            assert target_status_for_result(result) is None, state.value

    def test_applied_unreachable_without_structured_ref(self) -> None:
        """A structured ref is the ONLY path to APPLIED."""
        for state in (
            SubmissionResultState.SUBMITTED_CONFIRMED,
            SubmissionResultState.OUTCOME_UNKNOWN,
            SubmissionResultState.SUBMISSION_NOT_ALLOWED,
            SubmissionResultState.VALIDATION_FAILED,
            SubmissionResultState.BLOCKED_USER_ACTION,
            SubmissionResultState.APPROVAL_STALE,
            SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS,
            SubmissionResultState.ALREADY_SUBMITTED,
        ):
            result = _make_result("a", state, clicked=True, ats_reference_id="")
            assert target_status_for_result(result) != ApplicationStatus.APPLIED, state.value

    def test_explicit_transition_table_keyed_on_current_and_target(self) -> None:
        """The policy is a hard-coded explicit table, not a graph walk."""
        assert POST_SUBMIT_TRANSITIONS == {
            (ApplicationStatus.REVIEW_READY, ApplicationStatus.SUBMITTED): (
                ApplicationStatus.SUBMITTED,
            ),
            (ApplicationStatus.REVIEW_READY, ApplicationStatus.APPLIED): (
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.APPLIED,
            ),
            (ApplicationStatus.REVIEW_READY, ApplicationStatus.NEEDS_REVIEW): (
                ApplicationStatus.NEEDS_REVIEW,
            ),
            (ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED): (ApplicationStatus.APPLIED,),
            (ApplicationStatus.SUBMITTED, ApplicationStatus.NEEDS_REVIEW): (
                ApplicationStatus.NEEDS_REVIEW,
            ),
        }


# ---------------------------------------------------------------------------
# 2. Explicit transitions through record_result (transactional persistence)
# ---------------------------------------------------------------------------


class TestExplicitPostSubmitTransitions:
    def test_review_ready_submitted_confirmed_sets_submitted(self, tmp_path: Path) -> None:
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

    def test_review_ready_with_ref_walks_to_applied(self, tmp_path: Path) -> None:
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
                        ats_reference_id="ATS-REF-2024-001",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
        finally:
            engine.dispose()

    def test_submitted_job_with_ref_becomes_applied(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.SUBMITTED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                        ats_reference_id="ATS-REF-2024-002",
                        approval_id="apr-submitted-ref",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
        finally:
            engine.dispose()

    def test_submitted_job_same_state_ref_absent_is_noop(self, tmp_path: Path) -> None:
        """SUBMITTED + submitted_confirmed without ref must NOT downgrade."""
        job = _make_job(tmp_path, status=ApplicationStatus.SUBMITTED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                        approval_id="apr-submitted-noop",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.SUBMITTED
        finally:
            engine.dispose()

    def test_review_ready_outcome_unknown_sets_needs_review_direct(self, tmp_path: Path) -> None:
        """review_ready + outcome_unknown -> needs_review (NOT via submitted)."""
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

    def test_submitted_job_with_unknown_outcome_becomes_needs_review(self, tmp_path: Path) -> None:
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

    def test_already_submitted_result_keeps_submitted(self, tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# 3. Regression: earlier pipeline statuses are never auto-advanced
# ---------------------------------------------------------------------------


class TestEarlierStatusesNeverAutoAdvanced:
    def test_submitted_confirmed_never_jumps_from_discovered(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.DISCOVERED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                        ats_reference_id="ATS-REF",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.DISCOVERED
        finally:
            engine.dispose()

    def test_submitted_confirmed_never_jumps_from_queued(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.QUEUED)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                        ats_reference_id="ATS-REF",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.QUEUED
        finally:
            engine.dispose()

    def test_submitted_confirmed_on_in_progress_is_noop(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.IN_PROGRESS)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                record_result(
                    session,
                    _make_result(
                        job.application_id,
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        clicked=True,
                        ats_reference_id="ATS-REF",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.IN_PROGRESS
        finally:
            engine.dispose()

    def test_outcome_unknown_never_walks_from_in_progress(self, tmp_path: Path) -> None:
        """No graph walk: IN_PROGRESS + outcome_unknown must NOT become needs_review."""
        job = _make_job(tmp_path, status=ApplicationStatus.IN_PROGRESS)
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
            assert _job_status(sf, job.application_id) == ApplicationStatus.IN_PROGRESS
        finally:
            engine.dispose()

    def test_outcome_unknown_never_walks_from_queued(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.QUEUED)
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
            assert _job_status(sf, job.application_id) == ApplicationStatus.QUEUED
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 4. Terminal protection, idempotency, ATS ref persistence
# ---------------------------------------------------------------------------


class TestTerminalAndPersistence:
    def test_terminal_applied_never_downgraded(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, status=ApplicationStatus.APPLIED)
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

    def test_replay_is_idempotent(self, tmp_path: Path) -> None:
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

    def test_ats_reference_survives_restart(self, tmp_path: Path) -> None:
        """The structured ref is durable and derives APPLIED across restarts."""
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
                        ats_reference_id="ATS-REF-DURABLE",
                    ),
                )
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
            engine.dispose()

            engine2 = make_engine(build_engine_url(tmp_path / "uaa_transition" / "uaa.sqlite"))
            sf2 = make_session_factory(engine2)
            try:
                assert _job_status(sf2, job.application_id) == ApplicationStatus.APPLIED
                with session_scope(sf2) as session:
                    latest = get_latest_result(session, job.application_id)
                    assert latest is not None
                    assert latest.ats_reference_id == "ATS-REF-DURABLE"
            finally:
                engine2.dispose()
        finally:
            engine.dispose()

    def test_applied_replayed_from_persisted_row_is_stable(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            result = _make_result(
                job.application_id,
                SubmissionResultState.SUBMITTED_CONFIRMED,
                clicked=True,
                ats_reference_id="ATS-REF-REPLAY",
            )
            with session_scope(sf) as session:
                record_result(session, result)
            with session_scope(sf) as session:
                record_result(session, result)  # replay from persisted data
            assert _job_status(sf, job.application_id) == ApplicationStatus.APPLIED
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 5. Transactional rollback on invariant failure
# ---------------------------------------------------------------------------


class TestTransactionalRollback:
    def test_transition_failure_rolls_back_result_and_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the status write fails, the result row must ALSO not persist."""
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:

            def _boom(*_args, **_kwargs) -> None:
                raise ValueError("status write failed (injected)")

            monkeypatch.setattr(
                "universal_auto_applier.submission.status_transitions.update_application_status",
                _boom,
            )
            with pytest.raises(ValueError):
                with session_scope(sf) as session:
                    record_result(
                        session,
                        _make_result(
                            job.application_id,
                            SubmissionResultState.SUBMITTED_CONFIRMED,
                            clicked=True,
                        ),
                    )

            # The transaction rolled back: status unchanged and NO result row.
            assert _job_status(sf, job.application_id) == ApplicationStatus.REVIEW_READY
            with session_scope(sf) as session:
                assert get_latest_result(session, job.application_id) is None
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 6. Direct apply function edge cases
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

    def test_pre_click_result_leaves_review_ready_untouched(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        engine, sf = _setup_db(tmp_path, job)
        try:
            with session_scope(sf) as session:
                result = _make_result(
                    job.application_id,
                    SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                )
                assert (
                    apply_result_status_transition(session, result)
                    == ApplicationStatus.REVIEW_READY
                )
        finally:
            engine.dispose()
