"""Tests for :mod:`universal_auto_applier.interventions.navigation_bridge`.

Tests that Phase 3 navigation stop states are correctly converted to
intervention records.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.statuses import PageState
from universal_auto_applier.interventions.navigation_bridge import (
    create_interventions_from_exploration,
)
from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.navigator.safe_explorer import (
    ExplorationResult,
    ExplorationStep,
)
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_nav_bridge.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _make_result(
    stopped_reason: str,
    final_state: PageState = PageState.UNKNOWN,
    url: str = "https://example.com/page",
    reached_form: bool = False,
) -> ExplorationResult:
    return ExplorationResult(
        steps=[
            ExplorationStep(
                step_number=1,
                url=url,
                page_state=final_state,
                action=f"stop:{stopped_reason}",
            )
        ],
        final_state=final_state,
        stopped_reason=stopped_reason,
        final_observation=None,
    )


class TestNavigationBridge:
    def test_login_required_creates_intervention(self, session_factory) -> None:
        result = _make_result("login_required", PageState.LOGIN)
        with session_scope(session_factory) as session:
            count = create_interventions_from_exploration(
                session, application_id="job-1", result=result
            )
        assert count == 1
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1
        assert pending[0].kind == "login_required"

    def test_captcha_creates_intervention(self, session_factory) -> None:
        result = _make_result("captcha_detected", PageState.CAPTCHA)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1
        assert pending[0].kind == "captcha"

    def test_review_page_creates_intervention(self, session_factory) -> None:
        result = _make_result("review_page", PageState.REVIEW)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1
        assert pending[0].kind == "review_before_submit"

    def test_submit_detected_creates_intervention(self, session_factory) -> None:
        result = _make_result("submit_detected")
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1
        assert pending[0].kind == "review_before_submit"

    def test_no_safe_action_creates_unknown_page(self, session_factory) -> None:
        result = _make_result("no_safe_action", PageState.UNKNOWN)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1
        assert pending[0].kind == "unknown_page"

    def test_error_page_creates_intervention(self, session_factory) -> None:
        result = _make_result("error_page", PageState.ERROR)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert pending[0].kind == "unknown_page"

    def test_expired_creates_intervention(self, session_factory) -> None:
        result = _make_result("expired", PageState.EXPIRED)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert pending[0].kind == "unknown_page"

    def test_max_steps_creates_intervention(self, session_factory) -> None:
        result = _make_result("max_steps_reached")
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert pending[0].kind == "unknown_page"

    def test_click_failed_creates_intervention(self, session_factory) -> None:
        result = _make_result("click_failed")
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert pending[0].kind == "unknown_page"

    def test_reached_form_creates_no_intervention(self, session_factory) -> None:
        result = _make_result("form_visible", PageState.FORM, reached_form=True)
        with session_scope(session_factory) as session:
            count = create_interventions_from_exploration(
                session, application_id="job-1", result=result
            )
        assert count == 0

    def test_already_submitted_creates_no_intervention(self, session_factory) -> None:
        result = _make_result("already_submitted", PageState.SUBMITTED)
        with session_scope(session_factory) as session:
            count = create_interventions_from_exploration(
                session, application_id="job-1", result=result
            )
        assert count == 0

    def test_idempotent(self, session_factory) -> None:
        result = _make_result("login_required", PageState.LOGIN)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert len(pending) == 1

    def test_captures_page_url(self, session_factory) -> None:
        result = _make_result("login_required", PageState.LOGIN, url="https://example.com/login")
        with session_scope(session_factory) as session:
            create_interventions_from_exploration(session, application_id="job-1", result=result)
        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, "job-1")
        assert pending[0].page_url == "https://example.com/login"
