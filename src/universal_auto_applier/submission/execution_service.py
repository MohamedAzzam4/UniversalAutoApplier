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
import threading
import uuid
from pathlib import Path
from typing import Any, Protocol, cast

from playwright.sync_api import BrowserContext, sync_playwright

from universal_auto_applier.browser.progress import (
    ProgressTracker,
    consolidated_uploads,
    final_boundary_metadata,
    form_progress_fingerprint,
    form_step_has_unresolved_work,
    has_final_review_boundary,
    has_visible_answer_controls,
    is_form_step_candidate,
    step_scoped_fields,
    uniquely_safe_form_continue,
)
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.core.eligibility import repeat_processing_block_reason
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import InterventionKind
from universal_auto_applier.form_engine.live_executor import consolidate_fields, execute_live_form
from universal_auto_applier.interventions.store import (
    create_intervention,
    list_pending_interventions,
)
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
    SubmissionSnapshot,
    has_progress_metadata,
)
from universal_auto_applier.submission.store import (
    build_snapshot,
    get_active_approval,
    revoke_approval,
)

logger = logging.getLogger("universal_auto_applier.submission.execution_service")

REQUEST_OUTCOME_UNKNOWN_ERROR_CODE = "http_request_outcome_unknown_reconciliation_required"
PREPARATION_HTTP_MUTATION_BLOCKED_ERROR_CODE = "preparation_http_mutation_blocked"
PREPARATION_INTERLOCK_PERSISTENCE_FAILED_ERROR_CODE = "preparation_interlock_persistence_failed"
_UNCERTAIN_REQUEST_INTERLOCK_ERRORS = frozenset({"request_abort_failed", "request_outcome_unknown"})
_REQUEST_INTERLOCK_LATCH_LOCK = threading.Lock()
_REQUEST_INTERLOCK_LATCH: dict[str, tuple[str, bool]] = {}


class PreparationHttpMutationBlockedError(RuntimeError):
    """A preparation mutation was blocked, so the snapshot needs review."""

    error_code = PREPARATION_HTTP_MUTATION_BLOCKED_ERROR_CODE

    def __init__(self) -> None:
        super().__init__(
            f"{self.error_code}: a preparation HTTP mutation was blocked; review the request "
            "evidence and resolve the blocker before retrying preparation"
        )


class PreparationRequestOutcomeUnknownError(RuntimeError):
    """A preparation request may have reached the remote application."""

    error_code = REQUEST_OUTCOME_UNKNOWN_ERROR_CODE

    def __init__(self, interlock_failure: str) -> None:
        self.interlock_failure = interlock_failure
        super().__init__(
            f"{self.error_code}: remote application state is unknown after a preparation "
            f"HTTP request ({interlock_failure}); reconcile with the owner before any retry"
        )


class PreparationInterlockPersistenceError(RuntimeError):
    """The request blocker was detected but durable recording was incomplete."""

    error_code = PREPARATION_INTERLOCK_PERSISTENCE_FAILED_ERROR_CODE

    def __init__(
        self,
        blocker_error_code: str,
        *,
        stage: str,
        blocker_persisted: bool,
    ) -> None:
        self.blocker_error_code = blocker_error_code
        self.stage = stage
        self.blocker_persisted = blocker_persisted
        recovery = (
            "a durable blocker is recorded, but the prior approval may still be active"
            if blocker_persisted
            else "no durable blocker could be recorded; this process is latched fail-closed"
        )
        super().__init__(
            f"{self.error_code}: request blocker {blocker_error_code} could not be fully "
            f"persisted at {stage}; {recovery}. Restore storage, reconcile remote state, "
            "and do not retry preparation or submission."
        )


def _set_request_interlock_latch(
    application_id: str, blocker_error_code: str, *, persistence_failed: bool
) -> None:
    with _REQUEST_INTERLOCK_LATCH_LOCK:
        _REQUEST_INTERLOCK_LATCH[application_id] = (blocker_error_code, persistence_failed)


def _clear_request_interlock_latch(application_id: str) -> None:
    with _REQUEST_INTERLOCK_LATCH_LOCK:
        _REQUEST_INTERLOCK_LATCH.pop(application_id, None)


def _get_request_interlock_latch(application_id: str) -> tuple[str, bool] | None:
    with _REQUEST_INTERLOCK_LATCH_LOCK:
        return _REQUEST_INTERLOCK_LATCH.get(application_id)


def preparation_interlock_latch_error_code(application_id: str) -> str | None:
    """Return the fail-closed persistence error latched for this process, if any."""
    latch = _get_request_interlock_latch(application_id)
    if latch is None or not latch[1]:
        return None
    return PREPARATION_INTERLOCK_PERSISTENCE_FAILED_ERROR_CODE


def raise_for_request_interlock_latch(application_id: str) -> None:
    latch = _get_request_interlock_latch(application_id)
    if latch is None or not latch[1]:
        return
    blocker_error_code, _persistence_failed = latch
    raise PreparationInterlockPersistenceError(
        blocker_error_code,
        stage="blocker_write",
        blocker_persisted=False,
    )


def pending_preparation_http_interlock_kind(
    session: Any, application_id: str
) -> InterventionKind | None:
    pending_kinds = {
        intervention.kind for intervention in list_pending_interventions(session, application_id)
    }
    # Uncertain delivery always takes precedence over a safely aborted mutation.
    if InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN in pending_kinds:
        return InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN
    if InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED in pending_kinds:
        return InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED
    return None


def _raise_if_request_interlock_needs_human(interlock: Any) -> None:
    if interlock.setup_error in _UNCERTAIN_REQUEST_INTERLOCK_ERRORS:
        raise PreparationRequestOutcomeUnknownError(interlock.setup_error)
    if interlock.blocked_request_count > 0:
        raise PreparationHttpMutationBlockedError()


def _settle_request_interlock_routes(interlock: Any | None) -> None:
    if interlock is None:
        return
    settle = getattr(interlock, "wait_for_routes_to_settle", None)
    if callable(settle):
        settle()


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
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        import gc

        gc.collect()


class FixtureContextFactory:
    """Test factory that creates contexts for local fixture pages.

    Creates its own Playwright instance per call to ``create_context()``
    and tears it down on ``close()``. This avoids greenlet conflicts
    because each browser execution gets a fresh Playwright instance
    scoped to the calling thread.
    """

    def __init__(self, headless: bool = True, **kwargs: Any) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None

    def create_context(self) -> BrowserContext:
        # Run sync_playwright().start() in a subprocess to completely
        # isolate the Playwright greenlet from the calling thread's
        # greenlet state. This avoids the "Cannot switch to a different
        # thread" error on Python 3.13+ when called from within a
        # TestClient portal.
        #
        # Actually, we just create the Playwright instance in the
        # current thread. The execution service already runs us in a
        # dedicated thread (see execute_controlled_submission), so the
        # greenlet is scoped to that thread and should not conflict
        # with the TestClient's portal.
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        return self._browser.new_context(accept_downloads=False)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        import gc

        gc.collect()


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
        """Open browser, navigate from ``job.url`` to the actual application
        form, fill it, observe the submit control, build and persist the
        current snapshot.

        Returns the persisted snapshot, or None if the application form
        could not be reached (e.g., login required, CAPTCHA, no safe apply
        path, navigation loop, max steps exceeded, or the page remained a
        non-form detail page).

        The snapshot is NOT approved — the user must explicitly approve
        it via :meth:`approve_snapshot`.

        WQ-8 ATS target URL separation: ``job.url`` is the canonical
        source/job identity URL (often a job DETAIL page). The snapshot's
        ``application_url`` is the actual ATS application FORM URL reached
        by following safe Apply/Continue actions. ``job.url`` is never
        mutated; ``application_id`` is never mutated.
        """
        raise_for_request_interlock_latch(application_id)
        with session_scope(self._session_factory) as session:
            job = get_application_job(session, application_id)
            pending_http_interlock_kind = pending_preparation_http_interlock_kind(
                session, application_id
            )
        if job is None:
            logger.warning("[%s] job not found for snapshot observation", application_id[:12])
            return None
        if pending_http_interlock_kind == InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN:
            raise PreparationRequestOutcomeUnknownError("reconciliation_intervention_pending")
        if pending_http_interlock_kind == InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED:
            raise PreparationHttpMutationBlockedError()
        repeat_block = repeat_processing_block_reason(job)
        if repeat_block is not None:
            logger.info("[%s] snapshot observation blocked: %s", application_id[:12], repeat_block)
            return None

        context = self._context_factory.create_context() if self._context_factory else None
        if context is None:
            logger.error("[%s] no browser context factory configured", application_id[:12])
            return None

        request_interlock: Any | None = None
        try:
            # Both context-level guards must be installed before any page is
            # created. PreparationRequestInterlock intentionally rejects a
            # context with any pre-existing page, including about:blank.
            from universal_auto_applier.browser.request_interlock import (
                PreparationRequestInterlock,
            )
            from universal_auto_applier.browser.submit_interlock import (
                install_interlock,
                is_interlock_installed,
            )

            request_interlock = PreparationRequestInterlock(
                context,
                application_id=application_id,
            )
            request_interlock.install()
            install_interlock(context)
            logger.info(
                "[%s] observe: submit and HTTP request interlocks installed before page creation",
                application_id[:12],
            )

            page = context.new_page()
            request_interlock.attach_page(page)
            request_interlock.verify_page(page)
            page.goto("about:blank", wait_until="domcontentloaded")
            _raise_if_request_interlock_needs_human(request_interlock)
            request_interlock.verify_page(page)
            request_interlock.verify_service_worker_registration_guard(page)
            if not is_interlock_installed(page):
                raise RuntimeError("submit interlock did not execute before target navigation")
            request_interlock.verify_page(page)

            # Initial navigation is to job.url (the canonical source/detail URL).
            page.goto(
                job.url, wait_until="domcontentloaded", timeout=self._settings.browser_timeout_ms
            )
            _raise_if_request_interlock_needs_human(request_interlock)
            request_interlock.verify_page(page)
            page.wait_for_timeout(1_000)  # Let JS settle.
            _raise_if_request_interlock_needs_human(request_interlock)
            request_interlock.verify_page(page)

            # Cookie/CMP preflight after initial navigation (detail page).
            # A blocking Usercentrics/Cookiebot overlay must be resolved
            # before any form discovery. Unknown CMP fails closed.
            try:
                from universal_auto_applier.browser.consent_banner import handle_consent_banner

                policy = getattr(self._settings, "cookie_consent_policy", "necessary_only")
                _cmp_result = handle_consent_banner(page, policy=policy, timeout_ms=4000)  # type: ignore[arg-type]
                _raise_if_request_interlock_needs_human(request_interlock)
                request_interlock.verify_page(page)
                if _cmp_result.result in ("blocked", "human_required"):
                    logger.warning(
                        "[%s] observe: cookie consent blocked (cmp=%s policy=%s result=%s)",
                        application_id[:12],
                        _cmp_result.cmp,
                        _cmp_result.policy,
                        _cmp_result.result,
                    )
                    raise RuntimeError("cookie_consent_blocked")
                if _cmp_result.result == "resolved":
                    logger.info(
                        "[%s] observe: cookie consent resolved (cmp=%s policy=%s)",
                        application_id[:12],
                        _cmp_result.cmp,
                        _cmp_result.policy,
                    )
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] CMP preflight failed: %s", application_id[:12], exc)

            # Navigate from the detail page to the actual application form
            # using the EXISTING safe navigation semantics (analyze_page +
            # choose_safe_action + click_action) — the same proven loop used
            # by LiveBrowserRunner. Never click dangerous_submit during
            # discovery. Fail closed on blockers, loops, and max-step.
            from universal_auto_applier.navigator.apply_path_finder import (
                analyze_page,
                choose_safe_action,
                click_action,
            )

            max_nav_steps = max(self._settings.browser_max_steps, 1)
            progress = ProgressTracker()
            form_reached = False
            final_boundary = False
            completed_form_step_count = 0
            accumulated_fields: list[Any] = []
            accumulated_uploads: list[Any] = []
            candidate = resolve_candidate_profile(job.metadata)
            final_analysis: Any | None = None
            for _step in range(max_nav_steps):
                step_completed = False
                _raise_if_request_interlock_needs_human(request_interlock)
                request_interlock.verify_page(page)
                analysis = analyze_page(page)
                logger.info(
                    "[%s] observe nav url=%s form=%s blocker=%s controls=%d files=%d",
                    application_id[:12],
                    analysis.url,
                    analysis.is_application_form,
                    analysis.blocker,
                    analysis.visible_control_count,
                    analysis.file_input_count,
                )

                # Fail closed on blockers (CAPTCHA, login wall, external blocker).
                if analysis.blocker:
                    logger.warning(
                        "[%s] observe navigation blocked: %s at %s",
                        application_id[:12],
                        analysis.blocker,
                        analysis.url,
                    )
                    return None
                if analysis.expired:
                    logger.warning(
                        "[%s] observe navigation: job expired at %s",
                        application_id[:12],
                        analysis.url,
                    )
                    return None
                if analysis.submitted:
                    logger.warning(
                        "[%s] observe navigation: already submitted at %s",
                        application_id[:12],
                        analysis.url,
                    )
                    return None

                if is_form_step_candidate(page, analysis):
                    form_reached = True
                    # Cookie/CMP preflight is repeated after same-page step
                    # rerenders; it only resolves the configured consent
                    # policy and never weakens the request guard.
                    try:
                        from universal_auto_applier.browser.consent_banner import (
                            ConsentPolicy,
                            handle_consent_banner,
                        )

                        form_policy = cast(
                            ConsentPolicy,
                            getattr(self._settings, "cookie_consent_policy", "necessary_only"),
                        )
                        _cmp_form = handle_consent_banner(page, policy=form_policy, timeout_ms=4000)
                        _raise_if_request_interlock_needs_human(request_interlock)
                        request_interlock.verify_page(page)
                        if _cmp_form.result in ("blocked", "human_required"):
                            logger.warning(
                                "[%s] observe: cookie consent blocked on form (cmp=%s result=%s)",
                                application_id[:12],
                                _cmp_form.cmp,
                                _cmp_form.result,
                            )
                            break
                    except RuntimeError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[%s] CMP preflight on form failed: %s", application_id[:12], exc
                        )

                    execution = execute_live_form(page, candidate, job)
                    accumulated_fields.extend(step_scoped_fields(execution.fields, page))
                    accumulated_uploads.extend(execution.uploads)
                    _raise_if_request_interlock_needs_human(request_interlock)
                    request_interlock.verify_page(page)
                    final_analysis = analyze_page(page)
                    if final_analysis.blocker:
                        logger.warning(
                            "[%s] observe form step blocked: %s at %s",
                            application_id[:12],
                            final_analysis.blocker,
                            final_analysis.url,
                        )
                        break
                    if has_final_review_boundary(
                        final_analysis,
                        answer_controls_present=has_visible_answer_controls(page),
                    ):
                        final_boundary = True
                        if not form_step_has_unresolved_work(
                            execution.fields,
                            execution.uploads,
                            required_unresolved=execution.required_unresolved,
                            validation_errors=execution.validation_errors,
                        ):
                            completed_form_step_count += 1
                        break

                    action = choose_safe_action(
                        final_analysis,
                        allow_apply=False,
                        allow_continue=True,
                    )
                    if action is None:
                        logger.warning(
                            "[%s] observe form has no final boundary or safe Continue at %s",
                            application_id[:12],
                            final_analysis.url,
                        )
                        break
                    if not uniquely_safe_form_continue(final_analysis):
                        logger.warning(
                            "[%s] observe form has an ambiguous Continue control at %s",
                            application_id[:12],
                            final_analysis.url,
                        )
                        break
                    if form_step_has_unresolved_work(
                        execution.fields,
                        execution.uploads,
                        required_unresolved=execution.required_unresolved,
                        validation_errors=execution.validation_errors,
                    ):
                        logger.warning(
                            "[%s] observe form step has unresolved work; Continue blocked at %s",
                            application_id[:12],
                            final_analysis.url,
                        )
                        break
                    step_completed = True
                else:
                    # Not yet an application form — follow only safe Apply/Continue.
                    action = choose_safe_action(analysis, allow_apply=True, allow_continue=True)
                    if action is None:
                        logger.warning(
                            "[%s] observe navigation: no safe apply path at %s",
                            application_id[:12],
                            analysis.url,
                        )
                        return None

                logger.info(
                    "[%s] observe nav click %s text=%r",
                    application_id[:12],
                    action.classification,
                    action.text,
                )
                if not progress.register(page, action):
                    logger.warning(
                        "[%s] observe navigation: unchanged DOM/action repeated at %s",
                        application_id[:12],
                        analysis.url,
                    )
                    break
                page = click_action(
                    context,
                    page,
                    action,
                    timeout_ms=self._settings.browser_timeout_ms,
                )
                if step_completed:
                    completed_form_step_count += 1
                _raise_if_request_interlock_needs_human(request_interlock)
                request_interlock.verify_page(page)
                final_analysis = None
            else:
                # Loop exhausted without a confirmed final review boundary.
                logger.warning(
                    "[%s] observe navigation: max steps (%d) reached without a final boundary",
                    application_id[:12],
                    max_nav_steps,
                )

            if not form_reached:
                logger.warning("[%s] observe: application form not reached", application_id[:12])
                return None

            # Capture the final observed URL/control and the accumulated
            # evidence from every completed same-URL or cross-URL step.
            actual_form_url = page.url
            if final_analysis is None:
                final_analysis = analyze_page(page)
                final_boundary, submit_text, submit_selector, submit_frame_url = (
                    False,
                    "",
                    "",
                    "",
                )
            else:
                final_boundary, submit_text, submit_selector, submit_frame_url = (
                    final_boundary_metadata(
                        final_analysis,
                        answer_controls_present=has_visible_answer_controls(page),
                    )
                )
            progress_fingerprint = form_progress_fingerprint(page)
            accumulated_fields = consolidate_fields(accumulated_fields)
            accumulated_uploads = consolidated_uploads(accumulated_uploads)

            # Build the snapshot from the execution results.
            with session_scope(self._session_factory) as session:
                pending_count = len(list_pending_interventions(session, application_id))

            # A non-final observation can be preserved for intervention and
            # diagnostics, but never treated as an approvable review packet.
            if not accumulated_fields and not final_boundary:
                logger.warning(
                    "[%s] observe: form has no captured fields or final boundary at %s",
                    application_id[:12],
                    actual_form_url,
                )
                return None

            snapshot = build_snapshot(
                application_id=application_id,
                # WQ-8 ATS URL separation: snapshot.application_url is the
                # actual ATS application FORM URL, NOT job.url (which may be
                # a detail page). The owner reviews and authorizes this
                # exact form URL.
                application_url=actual_form_url,
                fields=accumulated_fields,
                uploads=accumulated_uploads,
                pending_intervention_count=pending_count,
                submit_control_text=submit_text,
                submit_control_selector=submit_selector,
                submit_control_frame_url=submit_frame_url,
                final_boundary_confirmed=final_boundary,
                completed_form_step_count=completed_form_step_count,
                form_progress_fingerprint=progress_fingerprint,
            )

            # Persist the snapshot as the "current live review snapshot"
            # by storing it on the approval row (unapproved).
            # The dashboard's approve action will read this and approve it.
            self._persist_live_snapshot(application_id, snapshot)
            logger.info(
                "[%s] observe: snapshot persisted form_url=%s job_url=%s (distinct=%s)",
                application_id[:12],
                actual_form_url,
                job.url,
                actual_form_url != job.url,
            )
            return snapshot
        except (PreparationHttpMutationBlockedError, PreparationRequestOutcomeUnknownError) as exc:
            _settle_request_interlock_routes(request_interlock)
            effective_error = exc
            if (
                request_interlock is not None
                and request_interlock.setup_error in _UNCERTAIN_REQUEST_INTERLOCK_ERRORS
                and not isinstance(exc, PreparationRequestOutcomeUnknownError)
            ):
                effective_error = PreparationRequestOutcomeUnknownError(
                    request_interlock.setup_error
                )
            has_new_interlock_evidence = request_interlock is not None and (
                request_interlock.setup_error in _UNCERTAIN_REQUEST_INTERLOCK_ERRORS
                or request_interlock.blocked_request_count > 0
            )
            if has_new_interlock_evidence:
                self._record_preparation_http_interlock_blocker(
                    application_id=application_id,
                    interlock=request_interlock,
                    error=effective_error,
                )
            if effective_error is not exc:
                raise effective_error from exc
            raise
        except Exception as exc:
            if (
                request_interlock is not None
                and request_interlock.setup_error in _UNCERTAIN_REQUEST_INTERLOCK_ERRORS
            ):
                unknown = PreparationRequestOutcomeUnknownError(request_interlock.setup_error)
                self._record_preparation_http_interlock_blocker(
                    application_id=application_id,
                    interlock=request_interlock,
                    error=unknown,
                )
                raise unknown from exc
            if request_interlock is not None and request_interlock.blocked_request_count > 0:
                blocked = PreparationHttpMutationBlockedError()
                self._record_preparation_http_interlock_blocker(
                    application_id=application_id,
                    interlock=request_interlock,
                    error=blocked,
                )
                raise blocked from exc
            logger.exception("[%s] snapshot observation failed: %s", application_id[:12], exc)
            return None
        finally:
            if self._context_factory:
                self._context_factory.close()

    def _record_preparation_http_interlock_blocker(
        self,
        *,
        application_id: str,
        interlock: Any | None,
        error: PreparationHttpMutationBlockedError | PreparationRequestOutcomeUnknownError,
    ) -> None:
        """Persist sanitized request evidence and revoke any now-stale approval."""
        _settle_request_interlock_routes(interlock)
        effective_error: PreparationHttpMutationBlockedError | PreparationRequestOutcomeUnknownError
        interlock_failure = getattr(interlock, "setup_error", None)
        if interlock_failure in _UNCERTAIN_REQUEST_INTERLOCK_ERRORS:
            effective_error = PreparationRequestOutcomeUnknownError(interlock_failure)
        else:
            effective_error = error
        uncertain = isinstance(effective_error, PreparationRequestOutcomeUnknownError)
        kind = (
            InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN
            if uncertain
            else InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED
        )
        blocked_requests = list(getattr(interlock, "blocked_requests", []))[:20]
        sanitized_requests = [
            {
                "method": request.method,
                "resource_type": request.resource_type,
                "destination_origin": request.destination_origin,
                "reason": request.reason,
            }
            for request in blocked_requests
        ]
        blocked_request_count = int(getattr(interlock, "blocked_request_count", 0))

        try:
            # Commit the human blocker first. If approval revocation fails in its
            # separate transaction, the durable pending intervention still gates
            # preparation, approval, and controlled submission.
            with session_scope(self._session_factory) as session:
                create_intervention(
                    session,
                    application_id=application_id,
                    kind=kind,
                    question=(
                        "A preparation HTTP request may have reached the application. Reconcile "
                        "the target state with the owner before any retry."
                        if uncertain
                        else "Preparation blocked an application HTTP mutation. Review the "
                        "sanitized request evidence before continuing."
                    ),
                    field_selector=f"preparation-request-outcome:{uuid.uuid4().hex}",
                    llm_metadata={
                        "error_code": effective_error.error_code,
                        "interlock_failure": (
                            effective_error.interlock_failure
                            if uncertain
                            else "mutating_request_blocked"
                        ),
                        "blocked_request_count": blocked_request_count,
                        "blocked_requests": sanitized_requests,
                    },
                )
        except Exception as exc:  # noqa: BLE001 — latch before surfacing storage failure
            _set_request_interlock_latch(
                application_id,
                effective_error.error_code,
                persistence_failed=True,
            )
            raise PreparationInterlockPersistenceError(
                effective_error.error_code,
                stage="blocker_write",
                blocker_persisted=False,
            ) from exc

        try:
            # The pending intervention has committed. Revoke any old approval
            # in a new transaction so failure cannot erase the primary blocker.
            with session_scope(self._session_factory) as session:
                approval = get_active_approval(session, application_id)
                if approval is not None:
                    revoke_approval(session, approval.approval_id)
        except Exception as exc:  # noqa: BLE001 — the durable blocker remains authoritative
            raise PreparationInterlockPersistenceError(
                effective_error.error_code,
                stage="approval_revocation",
                blocker_persisted=True,
            ) from exc
        _clear_request_interlock_latch(application_id)

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
            pending_http_interlock_kind = pending_preparation_http_interlock_kind(
                session, application_id
            )
            if pending_http_interlock_kind == InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN:
                raise PreparationRequestOutcomeUnknownError("reconciliation_intervention_pending")
            if pending_http_interlock_kind == InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED:
                raise PreparationHttpMutationBlockedError()
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

        The browser execution runs in a dedicated thread to avoid
        greenlet conflicts when called from within a TestClient portal.
        The claim is acquired BEFORE starting the browser so the losing
        request in a concurrent scenario never creates a browser.
        """
        import threading

        try:
            raise_for_request_interlock_latch(application_id)
        except PreparationInterlockPersistenceError as exc:
            return SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit="",
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                clicked=False,
                error_message=str(exc),
            )

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
            pending_http_interlock_kind = pending_preparation_http_interlock_kind(
                session, application_id
            )
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

        if pending_http_interlock_kind is not None:
            error_code = (
                REQUEST_OUTCOME_UNKNOWN_ERROR_CODE
                if pending_http_interlock_kind == InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN
                else PREPARATION_HTTP_MUTATION_BLOCKED_ERROR_CODE
            )
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit="",
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                clicked=False,
                error_message=(
                    f"{error_code}: resolve the preparation HTTP request intervention before "
                    "any controlled submission attempt"
                ),
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result

        repeat_block = repeat_processing_block_reason(job)
        if repeat_block is not None:
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit="",
                state=SubmissionResultState.ALREADY_SUBMITTED,
                clicked=False,
                error_message=repeat_block,
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
            return result

        # Get the approved snapshot hash and acquire claim BEFORE starting browser.
        with session_scope(self._session_factory) as session:
            pending_http_interlock_kind = pending_preparation_http_interlock_kind(
                session, application_id
            )
            if pending_http_interlock_kind is not None:
                error_code = (
                    REQUEST_OUTCOME_UNKNOWN_ERROR_CODE
                    if pending_http_interlock_kind == InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN
                    else PREPARATION_HTTP_MUTATION_BLOCKED_ERROR_CODE
                )
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit="",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    clicked=False,
                    error_message=(
                        f"{error_code}: resolve the preparation HTTP request intervention before "
                        "any controlled submission attempt"
                    ),
                )
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
                return result

            # Re-read at the claim boundary so a dashboard marker/status
            # change or a preparation HTTP blocker cannot acquire a claim.
            current_job = get_application_job(session, application_id)
            repeat_block = (
                repeat_processing_block_reason(current_job) if current_job is not None else None
            )
            if repeat_block is not None:
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit="",
                    state=SubmissionResultState.ALREADY_SUBMITTED,
                    clicked=False,
                    error_message=repeat_block,
                )
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
                return result

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

            # Acquire the claim BEFORE starting the browser.
            from universal_auto_applier.submission.store import acquire_claim

            claim = acquire_claim(
                session,
                application_id=application_id,
                approval=approval,
            )
            if claim is None:
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit=approved_snapshot_hash,
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    clicked=False,
                    error_message="could not acquire submission claim (concurrent attempt?)",
                )
                from universal_auto_applier.submission.store import record_result

                record_result(session, result)
                return result
            claim_id = claim.claim_id

        # Run the browser execution in a dedicated thread to avoid
        # greenlet conflicts with TestClient's portal.
        result_holder: dict[str, SubmissionResult | Exception] = {}

        def _run_browser() -> None:
            try:
                result_holder["result"] = self._execute_in_browser(
                    application_id=application_id,
                    approval_id=approval_id,
                    approved_snapshot_hash=approved_snapshot_hash,
                    claim_id=claim_id,
                    job=job,
                    artifact_dir=artifact_dir,
                )
            except Exception as exc:
                result_holder["error"] = exc

        thread = threading.Thread(target=_run_browser, daemon=True)
        thread.start()
        thread.join(timeout=120)

        if "error" in result_holder:
            exc = result_holder["error"]
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
                from universal_auto_applier.submission.models import (
                    SubmissionResultState as S,
                )
                from universal_auto_applier.submission.store import (
                    consume_claim,
                    record_result,
                )

                record_result(session, result)
                consume_claim(session, claim_id, state=S.OUTCOME_UNKNOWN)
            return result

        result = cast(SubmissionResult, result_holder["result"])
        if result.state == SubmissionResultState.ALREADY_SUBMITTED:
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.store import consume_claim

                consume_claim(
                    session,
                    claim_id,
                    state=SubmissionResultState.ALREADY_SUBMITTED,
                )
        return result

    def _execute_in_browser(
        self,
        *,
        application_id: str,
        approval_id: str,
        approved_snapshot_hash: str,
        claim_id: str,
        job: ApplicationJob,
        artifact_dir: Path | None = None,
    ) -> SubmissionResult:
        """Run the browser execution in the current thread.

        The claim has already been acquired — this method only does the
        browser work and records the result.
        """
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
                from universal_auto_applier.submission.models import (
                    SubmissionResultState as S,
                )
                from universal_auto_applier.submission.store import (
                    consume_claim,
                    record_result,
                )

                record_result(session, result)
                consume_claim(session, claim_id, state=S.SUBMISSION_NOT_ALLOWED)
            return result

        # WQ-8: install the submit interlock BEFORE any navigation, but ONLY
        # when an active single-use authorization exists for this application.
        # Without an authorization the controlled-submit path stays
        # byte-for-byte unchanged (no interlock install).
        # WQ-8 ATS URL separation: when a WQ-8 authorization is active, Phase B
        # must navigate to the APPROVED application form URL
        # (snapshot.application_url), NOT job.url (which may be a detail page).
        # The owner approved one exact form target; Phase B must not rediscover
        # or guess another URL.
        approved_form_url: str | None = None
        approved_snapshot: SubmissionSnapshot | None = None
        with session_scope(self._session_factory) as session:
            from universal_auto_applier.submission.authorization_store import (
                get_active_authorization,
            )

            wq8_auth_row = get_active_authorization(session, application_id)
            wq8_active = wq8_auth_row is not None
            # Load the exact approved snapshot for progress-aware hash
            # reconstruction on all controlled-submit routes. WQ-8 alone
            # uses the approved application URL to choose navigation; generic
            # controlled submission keeps its established job.url behavior.
            from universal_auto_applier.submission.store import get_active_approval

            approval = get_active_approval(session, application_id)
            if approval is not None and approval.snapshot_json:
                from universal_auto_applier.submission.models import SubmissionSnapshot

                try:
                    approved_snapshot = SubmissionSnapshot.model_validate(approval.snapshot_json)
                    if wq8_auth_row is not None:
                        approved_form_url = approved_snapshot.application_url
                except Exception:  # noqa: BLE001
                    pass
            if wq8_auth_row is not None:
                # Fall back to the authorization's own URL if the snapshot
                # could not be loaded.
                if not approved_form_url and wq8_auth_row.application_url:
                    approved_form_url = wq8_auth_row.application_url
        log_extra = ""
        if wq8_active:
            from universal_auto_applier.browser.submit_interlock import (
                install_interlock,
            )

            install_interlock(context)
            log_extra = " (WQ-8 interlock installed)"
        logger.info(
            "[%s] controlled submission browser start%s",
            application_id[:12],
            log_extra,
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            # WQ-8 ATS URL separation: when a WQ-8 authorization is active,
            # navigate directly to the approved application form URL, NOT
            # job.url. The owner approved one exact form target; Phase B must
            # not return to the detail page or rediscover another form URL.
            # Without an active WQ-8 authorization, preserve the existing
            # behavior (navigate to job.url) for non-WQ-8 controlled submissions.
            nav_url = approved_form_url if (wq8_active and approved_form_url) else job.url
            logger.info(
                "[%s] controlled submission navigate to %s (job.url=%s, wq8=%s)",
                application_id[:12],
                nav_url,
                job.url,
                wq8_active,
            )
            page.goto(
                nav_url,
                wait_until="domcontentloaded",
                timeout=self._settings.browser_timeout_ms,
            )
            page.wait_for_timeout(1_000)

            candidate = resolve_candidate_profile(job.metadata)
            execution = execute_live_form(page, candidate, job)

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
            final_boundary, boundary_text, boundary_selector, boundary_frame_url = (
                final_boundary_metadata(
                    analysis,
                    answer_controls_present=has_visible_answer_controls(page),
                )
            )

            # New WQ-8 approvals bind their observed wizard progress. Preserve
            # the legacy snapshot hash shape for approvals created before that
            # evidence existed; a fresh observation will replace them with the
            # new boundary metadata explicitly.
            progress_metadata: dict[str, Any] = {}
            if approved_snapshot is not None and has_progress_metadata(approved_snapshot):
                progress_metadata = {
                    "final_boundary_confirmed": final_boundary,
                    "completed_form_step_count": approved_snapshot.completed_form_step_count,
                    "form_progress_fingerprint": form_progress_fingerprint(page),
                }
                submit_text = boundary_text
                submit_selector = boundary_selector
                submit_frame_url = boundary_frame_url

            snapshot_fields = execution.fields
            if progress_metadata:
                snapshot_fields = step_scoped_fields(execution.fields, page)

            # WQ-8 ATS URL separation: the current snapshot's application_url
            # is the actual page.url the browser is on, NOT job.url.
            current_snapshot = build_snapshot(
                application_id=application_id,
                application_url=page.url,
                fields=snapshot_fields,
                uploads=execution.uploads,
                pending_intervention_count=pending_count,
                submit_control_text=submit_text,
                submit_control_selector=submit_selector,
                submit_control_frame_url=submit_frame_url,
                **progress_metadata,
            )

            # WQ-8 post-fill URL guard: when a WQ-8 authorization is active,
            # the actual page URL after navigation+fill MUST equal the approved
            # application form URL. If the ATS redirected to a different URL
            # (e.g. the form moved, a session expired and redirected to login,
            # or an intermediate page intervened), the approval is stale — fail
            # closed with APPROVAL_STALE rather than submitting to the wrong
            # form.
            if wq8_active and approved_form_url and page.url != approved_form_url:
                logger.warning(
                    "[%s] WQ-8 post-fill URL mismatch: actual %s != approved %s",
                    application_id[:12],
                    page.url,
                    approved_form_url,
                )
                result = SubmissionResult(
                    application_id=application_id,
                    approval_id=approval_id,
                    snapshot_hash_at_submit=approved_snapshot_hash,
                    state=SubmissionResultState.APPROVAL_STALE,
                    clicked=False,
                    error_message=(
                        f"WQ-8 approved application URL changed: "
                        f"actual {page.url!r} != approved {approved_form_url!r}"
                    ),
                )
                with session_scope(self._session_factory) as session:
                    from universal_auto_applier.submission.models import (
                        SubmissionResultState as S,
                    )
                    from universal_auto_applier.submission.store import (
                        consume_claim,
                        record_result,
                    )

                    record_result(session, result)
                    consume_claim(session, claim_id, state=S.APPROVAL_STALE)
                return result

            result = self._coordinator.execute_submission_from_page(
                page=page,
                application_id=application_id,
                approval_id=approval_id,
                current_snapshot=current_snapshot,
                artifact_dir=artifact_dir,
            )
            return result
        except Exception as exc:
            logger.exception("[%s] browser execution error: %s", application_id[:12], exc)
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=approved_snapshot_hash,
                state=SubmissionResultState.OUTCOME_UNKNOWN,
                clicked=False,
                error_message=f"browser execution error: {exc}",
            )
            with session_scope(self._session_factory) as session:
                from universal_auto_applier.submission.models import (
                    SubmissionResultState as S,
                )
                from universal_auto_applier.submission.store import (
                    consume_claim,
                    record_result,
                )

                record_result(session, result)
                consume_claim(session, claim_id, state=S.OUTCOME_UNKNOWN)
            return result
        finally:
            if self._context_factory:
                self._context_factory.close()

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
    "PreparationHttpMutationBlockedError",
    "PreparationInterlockPersistenceError",
    "PreparationRequestOutcomeUnknownError",
    "SubmissionExecutionService",
    "preparation_interlock_latch_error_code",
]
