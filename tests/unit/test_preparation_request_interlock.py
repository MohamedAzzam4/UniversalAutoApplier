"""Unit coverage for preparation request-guard setup and worker wiring."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from universal_auto_applier.browser.live_models import LiveRunReport
from universal_auto_applier.browser.live_runner import LiveBrowserRunner
from universal_auto_applier.browser.request_interlock import (
    PreparationRequestInterlock,
    RequestInterlockSetupError,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.core.statuses import ApplicationStatus
from universal_auto_applier.services import pipeline_worker_runner
from universal_auto_applier.services.pipeline_worker_runner import (
    PipelineWorkerRunner,
    _build_live_config,
)


def test_request_interlock_fails_closed_when_context_route_registration_fails() -> None:
    context = MagicMock()
    context.service_workers = []
    context.pages = []
    context.route.side_effect = RuntimeError("route setup failed")

    guard = PreparationRequestInterlock(context, application_id="synthetic-application")

    with pytest.raises(RequestInterlockSetupError, match="failed to install"):
        guard.install()

    assert guard.installed is False
    context.route.assert_called_once_with("**/*", guard._handle_route)
    context.new_page.assert_not_called()


def test_request_interlock_refuses_an_active_service_worker() -> None:
    context = MagicMock()
    context.service_workers = [object()]

    guard = PreparationRequestInterlock(context, application_id="synthetic-application")

    with pytest.raises(RequestInterlockSetupError, match="active service workers"):
        guard.install()

    context.route.assert_not_called()


def test_request_interlock_refuses_a_context_with_existing_pages() -> None:
    context = MagicMock()
    context.service_workers = []
    context.pages = [object()]

    guard = PreparationRequestInterlock(context, application_id="synthetic-application")

    with pytest.raises(RequestInterlockSetupError, match="already has pages"):
        guard.install()

    context.route.assert_not_called()


def test_uncertain_safe_route_handling_fails_closed() -> None:
    context = MagicMock()
    context.service_workers = []
    guard = PreparationRequestInterlock(
        context,
        application_id="synthetic-application",
    )
    guard.installed = True
    page = MagicMock()
    guard._attached_page_ids.add(id(page))

    route = MagicMock()
    route.request = SimpleNamespace(
        method="GET",
        resource_type="document",
        url="https://synthetic.example/path?private=sentinel",
    )
    route.continue_.side_effect = RuntimeError("synthetic continue failure")
    route.abort.side_effect = RuntimeError("synthetic abort failure")

    guard._handle_route(route)

    assert guard.setup_error == "request_abort_failed"
    assert guard.blocked_request_count == 1
    assert guard.blocked_requests[0].destination_origin == "https://synthetic.example"
    assert "sentinel" not in repr(guard.blocked_requests)
    route.abort.assert_called_once_with(error_code="blockedbyclient")
    with pytest.raises(RequestInterlockSetupError, match="safety guard failed"):
        guard.verify_page(page)

    report = LiveRunReport(
        application_id="synthetic-application",
        started_at=datetime.now(UTC),
        initial_url="https://synthetic.example/job",
        status="review_ready",
    )
    LiveBrowserRunner._record_request_interlock(report, guard)
    assert report.status == "needs_user_input"
    assert report.request_outcome_unknown is True
    assert report.request_interlock_failure == "request_abort_failed"
    assert report.stopped_reason == "http_request_outcome_unknown_reconciliation_required"


def test_safe_request_continuation_failure_requires_reconciliation() -> None:
    context = MagicMock()
    context.service_workers = []
    guard = PreparationRequestInterlock(context, application_id="synthetic-application")
    guard.installed = True

    route = MagicMock()
    route.request = SimpleNamespace(
        method="GET",
        resource_type="document",
        url="https://synthetic.example/path",
    )
    route.continue_.side_effect = RuntimeError("synthetic continue failure")

    guard._handle_route(route)

    assert guard.setup_error == "request_outcome_unknown"
    route.abort.assert_called_once_with(error_code="blockedbyclient")
    report = LiveRunReport(
        application_id="synthetic-application",
        started_at=datetime.now(UTC),
        initial_url="https://synthetic.example/job",
        status="review_ready",
    )
    LiveBrowserRunner._record_request_interlock(report, guard)
    assert report.status == "needs_user_input"
    assert report.request_outcome_unknown is True
    assert report.request_interlock_failure == "request_outcome_unknown"
    assert report.stopped_reason == "http_request_outcome_unknown_reconciliation_required"


def test_abort_failure_requires_reconciliation_and_stops_pipeline_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = MagicMock()
    context.service_workers = []
    guard = PreparationRequestInterlock(context, application_id="synthetic-application")
    guard.installed = True
    route = MagicMock()
    route.request = SimpleNamespace(
        method="POST",
        resource_type="fetch",
        url="https://synthetic.example/apply?private=sentinel",
    )
    route.abort.side_effect = RuntimeError("synthetic abort failure")

    guard._handle_route(route)

    report = LiveRunReport(
        application_id="synthetic-application",
        started_at=datetime.now(UTC),
        initial_url="https://synthetic.example/job",
        status="review_ready",
    )
    LiveBrowserRunner._record_request_interlock(report, guard)

    assert report.status == "needs_user_input"
    assert report.stopped_reason == "http_request_outcome_unknown_reconciliation_required"
    assert report.request_outcome_unknown is True
    assert report.request_interlock_failure == "request_abort_failed"
    assert "Reconcile the application state before retrying." in " ".join(report.errors)
    assert "sentinel" not in report.model_dump_json()

    runner = PipelineWorkerRunner(
        settings=Settings(data_dir=tmp_path),
        session_factory=MagicMock(),
        run_id="synthetic-run",
        max_jobs=1,
        job_pulse_ms=0,
    )
    runner._live_runner = MagicMock()
    runner._live_runner.run.return_value = report
    runner._update = MagicMock()
    runner._bump = MagicMock()
    monkeypatch.setattr(pipeline_worker_runner, "session_scope", lambda _: nullcontext(object()))
    monkeypatch.setattr(pipeline_worker_runner, "upsert_application_job", MagicMock())
    monkeypatch.setattr(
        pipeline_worker_runner, "resolve_candidate_profile", MagicMock(return_value=None)
    )
    create_intervention = MagicMock()
    monkeypatch.setattr(pipeline_worker_runner, "create_intervention", create_intervention)

    job = MagicMock(application_id="synthetic-application", metadata={})
    runner._process_job_live(job)

    assert job.status == ApplicationStatus.NEEDS_USER_INPUT
    assert job.status not in {ApplicationStatus.READY_TO_APPLY, ApplicationStatus.QUEUED}
    assert create_intervention.call_count == 1
    assert runner._live_runner.run.call_count == 1


def test_pipeline_worker_config_cannot_omit_submit_block(tmp_path: Path) -> None:
    config = _build_live_config(Settings(data_dir=tmp_path))

    assert config.hard_submit_block is True
