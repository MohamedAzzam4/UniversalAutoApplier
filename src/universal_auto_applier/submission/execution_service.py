"""Shared controlled-submission execution service.

This is the SINGLE entry point for controlled final submission, used by
both the ``live-submit`` CLI command and the ``POST /api/submit/{id}/submit``
API endpoint. It guarantees same-page execution: observation, gate
checks, claim acquisition, and click all happen on one live ``Page``.

Dependency injection: the ``BrowserContextFactory`` protocol allows tests
to supply a fixture-based executor without launching external sites.
Production code uses :class:`PlaywrightContextFactory`.

Call paths:
- CLI ``live-submit`` → ``SubmissionExecutionService.execute_controlled_submission``
  → ``coordinator.execute_submission_from_page`` (same Page)
- Dashboard → ``POST /api/submit/{id}/submit`` → ``SubmissionExecutionService.execute_controlled_submission``
  → ``coordinator.execute_submission_from_page`` (same Page)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from playwright.sync_api import BrowserContext, sync_playwright

from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.form_engine.live_executor import execute_live_form
from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
    SubmissionSnapshot,
)
from universal_auto_applier.submission.store import (
    build_snapshot,
    get_active_approval,
)

logger = logging.getLogger("universal_auto_applier.submission.execution_service")


# ---------------------------------------------------------------------------
# Browser context factory protocol (dependency injection)
# ---------------------------------------------------------------------------


class BrowserContextFactory(Protocol):
    """Protocol for creating browser contexts.

    Production code uses :class:`PlaywrightContextFactory`. Tests use
    :class:`FixtureContextFactory` which serves local HTML fixtures.
    """

    def create_context(self) -> BrowserContext: ...

    def close(self) -> None: ...


class PlaywrightContextFactory:
    """Production factory that creates real Playwright browser contexts."""

    def __init__(
        self,
        settings: Settings,
        profile_dir: Path | None = None,
        headless: bool = True,
        channel: str | None = None,
    ) -> None:
        self._settings = settings
        self._profile_dir = profile_dir
        self._headless = headless
        self._channel = channel
        self._playwright = None
        self._browser = None

    def create_context(self) -> BrowserContext:
        self._playwright = sync_playwright().start()
        if self._profile_dir is not None:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_dir),
                headless=self._headless,
                channel=self._channel,
                accept_downloads=False,
            )
        else:
            self._browser = self._playwright.chromium.launch(
                headless=self._headless,
                channel=self._channel,
            )
            context = self._browser.new_context(accept_downloads=False)
        return context

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass


class FixtureContextFactory:
    """Test factory that creates contexts for local fixture pages.

    Creates its own Playwright instance and browser so it works in any
    thread (including the TestClient's thread). The ``fixture_url`` is
    the base URL of the local fixture server — the factory navigates
    to the job's URL which should be a fixture page on that server.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None

    def create_context(self) -> BrowserContext:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        return self._browser.new_context(accept_downloads=False)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Submission execution service
# ---------------------------------------------------------------------------


class SubmissionExecutionService:
    """Shared service for controlled final submission.

    Used by both CLI and API. Guarantees same-page execution.

    Two main methods:
    - :meth:`observe_and_persist_snapshot`: opens browser, navigates,
      fills form, builds and persists the current snapshot (for approval).
    - :meth:`execute_controlled_submission`: opens browser, navigates,
      fills form, recomputes snapshot, checks gates, clicks submit ONCE
      on the same page, detects result.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        context_factory: BrowserContextFactory | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._context_factory = context_factory
        self._coordinator = SubmissionCoordinator(settings, session_factory)

    # ------------------------------------------------------------------
    # Snapshot observation (for approval)
    # ------------------------------------------------------------------

    def observe_and_persist_snapshot(
        self,
        *,
        application_id: str,
        artifact_dir: Path | None = None,
    ) -> SubmissionSnapshot | None:
        """Open browser, navigate, fill form, observe page, build and
        persist the current snapshot.

        Returns the persisted snapshot, or None if the form could not be
        reached (e.g., login required, page not found).

        The snapshot is NOT approved — the user must explicitly approve
        it via :meth:`approve_snapshot`.
        """
        with session_scope(self._session_factory) as session:
            job = get_application_job(session, application_id)
        if job is None:
            logger.warning("[%s] job not found for snapshot observation", application_id[:12])
            return None

        context = self._context_factory.create_context() if self._context_factory else None
        if context is None:
            logger.error("[%s] no browser context factory configured", application_id[:12])
            return None

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                job.url, wait_until="domcontentloaded", timeout=self._settings.browser_timeout_ms
            )
            page.wait_for_timeout(1_000)  # Let JS settle.

            # Fill the form on this page.
            candidate = resolve_candidate_profile(job.metadata)
            execution = execute_live_form(page, candidate, job)

            # Build the snapshot from the execution results.
            with session_scope(self._session_factory) as session:
                pending_count = len(list_pending_interventions(session, application_id))

            # Find the submit control.
            from universal_auto_applier.navigator.apply_path_finder import analyze_page

            analysis = analyze_page(page)
            submit_clickables = [
                c for c in analysis.clickables if c.classification.value == "dangerous_submit"
            ]
            submit_text = submit_clickables[0].text if len(submit_clickables) == 1 else ""
            submit_selector = (
                submit_clickables[0].selector_hint if len(submit_clickables) == 1 else ""
            )
            submit_frame_url = submit_clickables[0].frame_url if len(submit_clickables) == 1 else ""

            snapshot = build_snapshot(
                application_id=application_id,
                application_url=job.url,
                fields=execution.fields,
                uploads=execution.uploads,
                pending_intervention_count=pending_count,
                submit_control_text=submit_text,
                submit_control_selector=submit_selector,
                submit_control_frame_url=submit_frame_url,
            )

            # Persist the snapshot as the "current live review snapshot"
            # by storing it on the approval row (unapproved).
            # The dashboard's approve action will read this and approve it.
            self._persist_live_snapshot(application_id, snapshot)
            return snapshot
        except Exception as exc:
            logger.exception("[%s] snapshot observation failed: %s", application_id[:12], exc)
            return None
        finally:
            if self._context_factory:
                self._context_factory.close()

    def _persist_live_snapshot(self, application_id: str, snapshot: SubmissionSnapshot) -> None:
        """Persist the live snapshot so the dashboard can display and
        approve it.

        Stores the snapshot JSON in a dedicated column on the latest
        approval row, or creates a placeholder approval row that is
        not yet approved.
        """
        # Store the snapshot in the submission_approvals table as an
        # unapproved entry. The user will approve it explicitly.
        from universal_auto_applier.submission.store import create_approval

        with session_scope(self._session_factory) as session:
            create_approval(
                session,
                application_id=application_id,
                snapshot=snapshot,
            )

    # ------------------------------------------------------------------
    # Controlled submission execution (the actual click)
    # ------------------------------------------------------------------

    def execute_controlled_submission(
        self,
        *,
        application_id: str,
        approval_id: str,
        artifact_dir: Path | None = None,
    ) -> SubmissionResult:
        """Execute the controlled final submission.

        This is the ONLY method (besides the coordinator's internal
        methods) that can trigger a submit click. It:

        1. Opens a browser context.
        2. Navigates to the application URL.
        3. Fills the form (deterministic + LLM).
        4. On the SAME page: observes fields and submit control.
        5. Recomputes the form fingerprint and snapshot.
        6. Compares against the approved snapshot.
        7. Checks every gate.
        8. Acquires the persisted claim.
        9. Re-observes the submit control (same page).
        10. Clicks submit ONCE (same page).
        11. Detects and persists the result.

        Returns a :class:`SubmissionResult`.
        """
        # Gate 1: feature disabled.
        if not self._settings.enable_real_submission:
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit="",
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                clicked=False,
                error_message="enable_real_submission is False",
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result

        # Get the job.
        with session_scope(self._session_factory) as session:
            job = get_application_job(session, application_id)
        if job is None:
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit="",
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                clicked=False,
                error_message="application not found",
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result

        # Get the approved snapshot hash.
        with session_scope(self._session_factory) as session:
            approval = get_active_approval(session, application_id)
            if approval is None:
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit="",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    clicked=False,
                    error_message="no active approval",
                )
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
                return result

            if approval.approval_id != approval_id:
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit="",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    clicked=False,
                    error_message="approval ID mismatch",
                )
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
                return result

            approved_snapshot_hash = approval.snapshot_hash

        # Open browser and execute on ONE page.
        context = self._context_factory.create_context() if self._context_factory else None
        if context is None:
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=approved_snapshot_hash,
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                clicked=False,
                error_message="no browser context factory configured",
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                job.url,
                wait_until="domcontentloaded",
                timeout=self._settings.browser_timeout_ms,
            )
            page.wait_for_timeout(1_000)  # Let JS settle.

            # Fill the form on this page.
            candidate = resolve_candidate_profile(job.metadata)
            execution = execute_live_form(page, candidate, job)

            # Build the current snapshot from the live page.
            with session_scope(self._session_factory) as session:
                pending_count = len(list_pending_interventions(session, application_id))

            from universal_auto_applier.navigator.apply_path_finder import analyze_page

            analysis = analyze_page(page)
            submit_clickables = [
                c for c in analysis.clickables if c.classification.value == "dangerous_submit"
            ]
            submit_text = submit_clickables[0].text if len(submit_clickables) == 1 else ""
            submit_selector = (
                submit_clickables[0].selector_hint if len(submit_clickables) == 1 else ""
            )
            submit_frame_url = submit_clickables[0].frame_url if len(submit_clickables) == 1 else ""

            current_snapshot = build_snapshot(
                application_id=application_id,
                application_url=job.url,
                fields=execution.fields,
                uploads=execution.uploads,
                pending_intervention_count=pending_count,
                submit_control_text=submit_text,
                submit_control_selector=submit_selector,
                submit_control_frame_url=submit_frame_url,
            )

            # Execute on the SAME page — this is the critical same-page call.
            result = self._coordinator.execute_submission_from_page(
                page=page,
                application_id=application_id,
                approval_id=approval_id,
                current_snapshot=current_snapshot,
                artifact_dir=artifact_dir,
            )
            return result
        except Exception as exc:
            logger.exception("[%s] execution service error: %s", application_id[:12], exc)
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=approved_snapshot_hash,
                state=SubmissionResultState.OUTCOME_UNKNOWN,
                clicked=False,
                error_message=f"execution service error: {exc}",
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result
        finally:
            if self._context_factory:
                self._context_factory.close()

    # ------------------------------------------------------------------
    # Approval management (delegates to coordinator)
    # ------------------------------------------------------------------

    def approve_snapshot(
        self,
        *,
        application_id: str,
        snapshot: SubmissionSnapshot,
    ) -> str:
        """Approve a snapshot. Returns the approval_id."""
        return self._coordinator.approve_snapshot(
            application_id=application_id,
            snapshot=snapshot,
        )

    def revoke_approval(self, approval_id: str) -> bool:
        """Revoke an approval."""
        return self._coordinator.revoke_approval(approval_id)


__all__ = [
    "BrowserContextFactory",
    "FixtureContextFactory",
    "PlaywrightContextFactory",
    "SubmissionExecutionService",
]
