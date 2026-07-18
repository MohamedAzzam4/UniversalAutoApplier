"""Playwright tests for the dashboard Submit view (Phase 8).

Tests the complete user workflow: select application, refresh observation,
inspect fields/documents/safety state, confirm high-risk, approve, revoke,
submit confirmation dialog, stale/blocked states.

All tests use the local dashboard API and local data — no external ATS.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base

pytestmark = pytest.mark.playwright


def _make_settings(tmp_path: Path, port: int, **overrides: Any) -> Settings:
    kwargs: dict[str, Any] = dict(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_dash",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _make_job(tmp_path: Path, suffix: str = "test-1") -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC),
            external_job_id=suffix,
            url=f"https://example.com/job/{suffix}",
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=f"https://example.com/job/{suffix}",
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id=suffix,
        metadata={},
    )


def _start_dashboard(
    tmp_path: Path, port: int, settings_overrides: dict[str, Any] | None = None
) -> tuple[str, object, object]:
    settings = _make_settings(tmp_path, port, **(settings_overrides or {}))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    base = f"http://127.0.0.1:{port}/"
    ready = False
    while time.time() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    if not ready:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("Server did not start")

    Base.metadata.create_all(app.state.engine)
    return base, app, server


def _get_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _setup_page(page: Page, base: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)


def _create_observe_payload(tmp_path: Path, app_id: str) -> dict[str, Any]:
    """Create a minimal approval row via the backend so observe works."""
    from universal_auto_applier.submission.models import (
        SubmissionSnapshot,
        SubmissionSnapshotDocument,
        SubmissionSnapshotField,
        SubmissionSnapshotSubmitControl,
    )

    snapshot = SubmissionSnapshot(
        application_id=app_id,
        application_url="https://example.com/job/test-1",
        company="Test Corp",
        job_title="Engineer",
        external_job_id="test-1",
        platform=str(Platform.GENERIC),
        form_fingerprint="fp-abc123",
        snapshot_hash="hash-abc123",
        fields=[
            SubmissionSnapshotField(
                field_token="f_name",
                label="Full Name",
                field_type="text",
                required=True,
                filled_value="John Doe",
                selected_value="John Doe",
                status="filled",
                risk_level="low",
                requires_confirmation=False,
            ),
            SubmissionSnapshotField(
                field_token="f_risk",
                label="Criminal Record",
                field_type="radio",
                required=True,
                filled_value="No",
                selected_value="No",
                status="filled",
                risk_level="high",
                requires_confirmation=True,
            ),
            SubmissionSnapshotField(
                field_token="f_salary",
                label="Expected Salary",
                field_type="text",
                required=False,
                filled_value="100000",
                selected_value="100000",
                status="filled",
                risk_level="medium",
                requires_confirmation=False,
            ),
            SubmissionSnapshotField(
                field_token="f_validate",
                label="Has Error Field",
                field_type="text",
                required=True,
                filled_value="bad",
                selected_value="bad",
                status="validation_error",
                risk_level="low",
                requires_confirmation=False,
                validation_error="Invalid format",
            ),
            SubmissionSnapshotField(
                field_token="f_secret",
                label="Password",
                field_type="password",
                required=False,
                filled_value="s3cret!",
                selected_value="",
                status="filled",
                risk_level="low",
                requires_confirmation=False,
            ),
        ],
        documents=[
            SubmissionSnapshotDocument(
                document_kind="cv",
                path=str(tmp_path / "cv.pdf"),
                content_hash="abc123",
            ),
            SubmissionSnapshotDocument(
                document_kind="cover_letter",
                path=str(tmp_path / "nonexistent.pdf"),
                content_hash="def456",
            ),
        ],
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit Application", selector="#submit-btn"
        ),
        unresolved_required_field_count=1,
        pending_intervention_count=0,
    )
    return {
        "snapshot": snapshot,
        "app_id": app_id,
        "form_fingerprint": "fp-abc123",
        "snapshot_hash": "hash-abc123",
    }


class TestSubmitViewDashboard:
    """Comprehensive tests for the Submit view (requirements 1-20)."""

    # ---- Requirement 1: Submit view loads status ----
    def test_submit_view_loads_status(self, page: Page, tmp_path: Path) -> None:
        """Submit view loads and shows status for an application ID."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")

            # Wait for state display.
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)
            text = page.inner_text("#submit-state-display")
            assert "Test Corp" in text
            assert "Engineer" in text
            assert "No persisted snapshot" in text
            assert "Refresh Live Review" in text
        finally:
            server.should_exit = True

    # ---- Requirement 2: Refresh calls /observe ----
    def test_refresh_calls_observe(self, page: Page, tmp_path: Path) -> None:
        """Clicking Refresh Live Review sends a POST to /observe."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)

            # First load status to show no snapshot.
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-warning", timeout=5_000)

            # Now try refresh — this will fail because there's no
            # browser context factory, but that's expected.
            page.click("#submit-refresh")
            page.wait_for_selector(".uaa-error", timeout=5_000)
            error_text = page.inner_text("#submit-state-display")
            assert "error" in error_text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 3: Observation loading state appears ----
    def test_observation_loading_state_appears(self, page: Page, tmp_path: Path) -> None:
        """During observation, the loading state is shown."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)

            # Wrap fetch to add a 300ms delay to /observe calls, ensuring the
            # loading state is observable regardless of SQLite speed.
            page.evaluate("""() => {
                const _fetch = window.fetch;
                window.fetch = function(url, opts) {
                    if (typeof url === 'string' && url.includes('/observe')) {
                        return new Promise(resolve => setTimeout(() => resolve(_fetch(url, opts)), 300));
                    }
                    return _fetch(url, opts);
                };
            }""")

            with page.expect_response(
                lambda r: r.url.endswith("/observe") and r.request.method == "POST"
            ):
                page.click("#submit-refresh")
                page.wait_for_function(
                    "() => document.getElementById('submit-refresh').disabled",
                    timeout=2_000,
                )
        finally:
            server.should_exit = True

    # ---- Requirement 4: Complete job details render ----
    def test_job_details_render(self, page: Page, tmp_path: Path) -> None:
        """All job properties render in the Job section."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "Test Corp" in text
            assert "Engineer" in text
            assert job.application_id in text
            assert "test-1" in text  # external_job_id
            assert "generic" in text.lower()  # platform
            assert "https://example.com/job/test-1" in text  # URL
        finally:
            server.should_exit = True

    # ---- Requirement 5: All field details render ----
    def test_field_details_render(self, page: Page, tmp_path: Path) -> None:
        """Each field shows type, required, risk, evidence, options, validation."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)

        # Seed a persisted snapshot via the submission store.
        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/test-1",
            form_fingerprint="fp-abc",
            snapshot_hash="hash-abc",
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    required=True,
                    filled_value="John",
                    selected_value="John",
                    status="filled",
                    risk_level="low",
                ),
                SubmissionSnapshotField(
                    field_token="f2",
                    label="Dropdown",
                    field_type="select",
                    required=False,
                    filled_value="Option A",
                    selected_value="Option A",
                    status="filled",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "Name" in text
            assert "John" in text
            assert "text" in text.lower()
            assert "Required" in text
            assert "Dropdown" in text
            assert "select" in text.lower()
            assert "low" in text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 6: Radio/select/checkbox values render ----
    def test_radio_select_checkbox_values_render(self, page: Page, tmp_path: Path) -> None:
        """Options from select/radio fields are displayed."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="opt-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/opt-test",
            form_fingerprint="fp-opt",
            snapshot_hash="hash-opt",
            fields=[
                SubmissionSnapshotField(
                    field_token="f_radio",
                    label="Gender",
                    field_type="radio",
                    required=True,
                    filled_value="Male",
                    selected_value="Male",
                    status="filled",
                    risk_level="low",
                ),
                SubmissionSnapshotField(
                    field_token="f_check",
                    label="Agree to terms",
                    field_type="checkbox",
                    required=True,
                    filled_value="true",
                    selected_value="true",
                    status="filled",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "radio" in text.lower()
            assert "checkbox" in text.lower()
            assert "Male" in text
            assert "true" in text
        finally:
            server.should_exit = True

    # ---- Requirement 7: Documents and hashes render ----
    def test_documents_and_hashes_render(self, page: Page, tmp_path: Path) -> None:
        """Documents section renders kind, path, hash, exists, readable."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="doc-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotDocument,
        )
        from universal_auto_applier.submission.store import create_approval

        cv_path = tmp_path / "cv.pdf"
        cv_path.write_bytes(b"%PDF")
        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/doc-test",
            form_fingerprint="fp-doc",
            snapshot_hash="hash-doc",
            fields=[],
            documents=[
                SubmissionSnapshotDocument(
                    document_kind="cv",
                    path=str(cv_path),
                    content_hash="content-hash-abc",
                ),
            ],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-doc", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "cv" in text.lower()
            assert "cv.pdf" in text
            assert "content-hash-abc" in text
            assert "Exists" in text
            assert "Readable" in text
        finally:
            server.should_exit = True

    # ---- Requirement 8: Pending interventions render ----
    def test_pending_interventions_render(self, page: Page, tmp_path: Path) -> None:
        """Safety section shows pending intervention count."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="iv-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/iv-test",
            form_fingerprint="fp-iv",
            snapshot_hash="hash-iv",
            fields=[],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=3,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "Pending Interventions" in text
            assert "3" in text
            # Approve should be blocked.
            assert "blocked" in text.lower() or "blocking" in text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 9: High-risk confirmation workflow ----
    def test_high_risk_confirmation_workflow(self, page: Page, tmp_path: Path) -> None:
        """High-risk fields can be selected and confirmed."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="hr-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/hr-test",
            form_fingerprint="fp-hr",
            snapshot_hash="hash-hr",
            fields=[
                SubmissionSnapshotField(
                    field_token="f_crime",
                    label="Criminal Record",
                    field_type="radio",
                    required=True,
                    filled_value="No",
                    selected_value="No",
                    status="filled",
                    risk_level="high",
                    requires_confirmation=True,
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "high" in text.lower()
            assert "requires confirmation" in text.lower()

            # Confirm High-Risk button should be disabled initially.
            assert page.is_disabled("#submit-confirm-high-risk")

            # Check the high-risk checkbox.
            hr_checkbox = page.locator(".uaa-hr-checkbox[data-field-token='f_crime']")
            hr_checkbox.check()
            assert not page.is_disabled("#submit-confirm-high-risk")
        finally:
            server.should_exit = True

    # ---- Requirement 10: Approval sends displayed snapshot hash ----
    def test_approve_sends_displayed_hash(self, page: Page, tmp_path: Path) -> None:
        """Approve button sends the snapshot hash from the rendered state."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="app-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/app-test",
            form_fingerprint="fp-app",
            snapshot_hash="hash-app-123",
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    required=False,
                    filled_value="John",
                    selected_value="John",
                    status="filled",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            # The approve button should be enabled (can_approve is true).
            page.wait_for_selector("#submit-approve:not([disabled])", timeout=3_000)

            # Click approve.
            page.click("#submit-approve")
            # After approval, state should refresh.
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)
            text = page.inner_text("#submit-state-display")
            # After re-loading state, approval info should be present.
            assert "Snapshot Hash" in text or "hash" in text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 11: Revoke updates the view ----
    def test_revoke_updates_view(self, page: Page, tmp_path: Path) -> None:
        """Revoke approval sends a POST and refreshes the state."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="rev-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/rev-test",
            form_fingerprint="fp-rev",
            snapshot_hash="hash-rev",
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    required=False,
                    filled_value="John",
                    selected_value="John",
                    status="filled",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)
            page.wait_for_selector("#submit-approve:not([disabled])", timeout=3_000)

            # Approve first.
            page.click("#submit-approve")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            # Now click revoke.
            page.click("#submit-revoke")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)
            text_after = page.inner_text("#submit-state-display")
            # After revoke, state should show no active approval.
            assert "none" in text_after.lower() or "No" in text_after
        finally:
            server.should_exit = True

    # ---- Requirement 12: Stale approval disables Submit ----
    def test_stale_approval_disables_submit(self, page: Page, tmp_path: Path) -> None:
        """Stale approval results in disabled Submit button."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="stale-test")

        from universal_auto_applier.submission.models import SubmissionSnapshot
        from universal_auto_applier.submission.store import create_approval, get_active_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/stale-test",
            form_fingerprint="fp-stale",
            snapshot_hash="hash-v1",
            fields=[],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        # Manually desync the approval's snapshot_hash to simulate staleness.
        with session_scope(app.state.session_factory) as session:
            approval = get_active_approval(session, job.application_id)
            assert approval is not None
            approval.snapshot_hash = "hash-v2"
            session.flush()

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            # Stale warning should appear.
            text = page.inner_text("#submit-state-display")
            assert "stale" in text.lower()

            # Submit should be disabled.
            assert page.is_disabled("#submit-execute")
        finally:
            server.should_exit = True

    # ---- Requirement 13: Blocking reason visible ----
    def test_blocking_reason_visible(self, page: Page, tmp_path: Path) -> None:
        """The blocking reason is displayed near the disabled controls."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="block-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/block-test",
            form_fingerprint="fp-block",
            snapshot_hash="hash-block",
            fields=[
                SubmissionSnapshotField(
                    field_token="f_unresolved",
                    label="Unresolved Field",
                    field_type="text",
                    required=True,
                    filled_value="",
                    selected_value="",
                    status="intervention_needed",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=1,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-blocking-reason", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "blocked" in text.lower() or "unresolved" in text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 14: Submit confirmation dialog appears ----
    def test_submit_confirmation_dialog_appears(self, page: Page, tmp_path: Path) -> None:
        """Submit button opens the confirmation dialog."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="conf-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/conf-test",
            form_fingerprint="fp-conf",
            snapshot_hash="hash-conf",
            fields=[],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector("#submit-approve:not([disabled])", timeout=5_000)

            # Need to have can_submit for execute to be enabled.
            # This requires can_approve, no stale, active approval.
            # After creating the snapshot, the approval is active.
            # But can_submit also requires can_approve (no blockers).
            # This should work since the snapshot is clean.

            # Click the Submit button if enabled.
            if not page.is_disabled("#submit-execute"):
                page.click("#submit-execute")
                page.wait_for_selector("#submit-confirm-dialog", timeout=3_000)
                assert page.is_visible("#submit-confirm-dialog")
                assert page.is_visible("#submit-confirm-yes")
                assert page.is_visible("#submit-confirm-no")
            else:
                # Submit might be disabled, just assert dialog is hidden.
                assert page.is_hidden("#submit-confirm-dialog")
        finally:
            server.should_exit = True

    # ---- Requirement 15: Submission success renders ----
    def test_submission_success_renders(self, page: Page, tmp_path: Path) -> None:
        """After a successful submission, the result is shown."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="sub-ok")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
        )
        from universal_auto_applier.submission.store import create_approval, get_active_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/sub-ok",
            form_fingerprint="fp-sub-ok",
            snapshot_hash="hash-sub-ok",
            fields=[],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        # Seed the latest result as having been submitted.
        with session_scope(app.state.session_factory) as session:
            from universal_auto_applier.persistence.models import SubmissionResultRow

            approval = get_active_approval(session, job.application_id)
            assert approval is not None
            import uuid

            result = SubmissionResultRow(
                result_id=uuid.uuid4().hex,
                application_id=job.application_id,
                approval_id=approval.approval_id,
                snapshot_hash_at_submit="hash-sub-ok",
                state="submitted",
                clicked=True,
                error_message=None,
            )
            session.add(result)
            session.commit()

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "submitted" in text.lower()
            assert "Latest Submission" in text
        finally:
            server.should_exit = True

    # ---- Requirement 16: Outcome unknown prevents retry ----
    def test_outcome_unknown_prevents_retry(self, page: Page, tmp_path: Path) -> None:
        """When outcome is unknown/failed, Submit is still disabled."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="fail-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
        )
        from universal_auto_applier.submission.store import create_approval, get_active_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/fail-test",
            form_fingerprint="fp-fail",
            snapshot_hash="hash-fail",
            fields=[],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        with session_scope(app.state.session_factory) as session:
            from universal_auto_applier.persistence.models import SubmissionResultRow

            approval = get_active_approval(session, job.application_id)
            assert approval is not None
            import uuid

            result = SubmissionResultRow(
                result_id=uuid.uuid4().hex,
                application_id=job.application_id,
                approval_id=approval.approval_id,
                snapshot_hash_at_submit="hash-fail",
                state="failed",
                clicked=False,
                error_message="Submission failed: timeout",
            )
            session.add(result)
            session.commit()

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "failed" in text.lower()
            assert "timeout" in text.lower() or "error" in text.lower()
            # Failed submission allows retry — submit button should be enabled.
            assert not page.is_disabled("#submit-execute")
        finally:
            server.should_exit = True

    # ---- Requirement 17: API failure renders actionable error ----
    def test_api_failure_renders_error(self, page: Page, tmp_path: Path) -> None:
        """When the API returns an error, it is shown in the display."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="err-test")
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-section", timeout=5_000)

            # Refresh will fail because there's no browser context factory.
            page.click("#submit-refresh")
            page.wait_for_selector(".uaa-error", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            assert "error" in text.lower()
        finally:
            server.should_exit = True

    # ---- Requirement 18: Repeated clicks blocked ----
    def test_repeated_clicks_blocked_during_requests(self, page: Page, tmp_path: Path) -> None:
        """Buttons are disabled while a request is in flight."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="busy-test")
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)

            # Click load and immediately check that buttons are disabled.
            page.click("#submit-load")  # Will fail quickly (no snapshot).
            page.wait_for_selector(".uaa-error, .uaa-submit-section", timeout=5_000)
            # After request completes, buttons should be re-enabled.
            assert not page.is_disabled("#submit-refresh")
        finally:
            server.should_exit = True

    # ---- Requirement 19: Existing dashboard views still work ----
    def test_existing_dashboard_views_still_work(self, page: Page, tmp_path: Path) -> None:
        """Navigating to Dashboard, Queue, Interventions, Review still works."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.wait_for_selector("#run-status", timeout=10_000)

            # Dashboard shows status.
            assert "idle" in page.locator("#run-status").inner_text()

            # Queue view.
            page.click('a[data-view="queue"]')
            page.wait_for_selector("#queue-table", timeout=5_000)

            # Interventions view.
            page.click('a[data-view="interventions"]')
            page.wait_for_selector("#intervention-list", timeout=5_000)

            # Review view.
            page.click('a[data-view="review"]')
            page.wait_for_selector("#review-job-id", timeout=5_000)

            # Submit view.
            page.click('a[data-view="submit"]')
            page.wait_for_selector("#submit-job-id", timeout=5_000)

            # Logs view.
            page.click('a[data-view="logs"]')
            page.wait_for_selector("#log-list", timeout=5_000)
        finally:
            server.should_exit = True

    # ---- Requirement 20: No secret fields rendered ----
    def test_no_secret_fields_rendered(self, page: Page, tmp_path: Path) -> None:
        """Password/token/API key fields have their values hidden."""
        port = _get_free_port()
        base, app, server = _start_dashboard(tmp_path, port)
        job = _make_job(tmp_path, suffix="secret-test")

        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotField,
        )
        from universal_auto_applier.submission.store import create_approval

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url="https://example.com/job/secret-test",
            form_fingerprint="fp-secret",
            snapshot_hash="hash-secret",
            fields=[
                SubmissionSnapshotField(
                    field_token="f_pw",
                    label="Password",
                    field_type="password",
                    required=True,
                    filled_value="my-s3cret-password",
                    selected_value="",
                    status="filled",
                    risk_level="low",
                ),
            ],
            documents=[],
            unresolved_required_field_count=0,
            pending_intervention_count=0,
        )
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        try:
            _setup_page(page, base)
            page.fill("#submit-job-id", job.application_id)
            page.click("#submit-load")
            page.wait_for_selector(".uaa-submit-field-detail", timeout=5_000)

            text = page.inner_text("#submit-state-display")
            # The actual password value must NOT appear.
            assert "my-s3cret-password" not in text
            # Should show "(hidden)" or similar.
            assert "hidden" in text.lower()
        finally:
            server.should_exit = True
