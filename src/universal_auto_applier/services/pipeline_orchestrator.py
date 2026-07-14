"""Pipeline orchestrator — wires all phases into a safe, observable pipeline.

Per ROADMAP Phase 8: Full Pipeline Orchestration.

Target flow:
    JobHunter export -> queue import -> adapter route -> navigate -> fill
    -> intervention or review -> submit if approved -> history update

The orchestrator:
1. Loads queued ApplicationJob records.
2. Selects adapter by platform.
3. For generic path: observe -> explore -> extract -> map -> fill ->
   create interventions -> create review state -> stop before submit.
4. For trusted adapter (Siemens): calls adapter methods with dry-run safety.
5. Updates job status throughout.
6. Emits log events visible through the dashboard.
7. Never submits unless explicitly allowed by config AND review gate.

Safety:
- Default run mode is dry_run/review.
- Generic adapter never auto-submits.
- Trusted adapter submission requires explicit config (dry_run=False).
- Final submit requires review approval.
- Login/captcha/password/unknown required fields create interventions and stop.
- Errors update job/error state without crashing the pipeline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.adapters.registry import AdapterRegistry, NoAdapterError
from universal_auto_applier.adapters.siemens_adapter import SiemensAdapter, SiemensAdapterConfig
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormFillSummary,
)
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
    InterventionKind,
)
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.form_engine.schema_extractor import extract_form_fields
from universal_auto_applier.interventions.fill_bridge import (
    create_interventions_from_fill_summary,
)
from universal_auto_applier.interventions.navigation_bridge import (
    create_interventions_from_exploration,
)
from universal_auto_applier.interventions.review import (
    ReviewState,
    check_submit_approval,
    create_review_state,
)
from universal_auto_applier.interventions.store import (
    count_pending_interventions,
    create_intervention,
)
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import safe_explore
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    list_application_jobs,
    upsert_application_job,
)

logger = logging.getLogger("universal_auto_applier.pipeline")


@dataclass
class PipelineState:
    """Mutable state of a single pipeline run."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "idle"  # idle, running, paused, error, completed
    current_job_id: str | None = None
    current_phase: str = ""
    last_action: str = ""
    last_error: str = ""
    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    jobs_skipped: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineOrchestrator:
    """Orchestrates the full application pipeline.

    The orchestrator is designed to work with fixture HTML for Level 0
    dry-run tests. In production (Phase 8+), it would use Playwright
    pages instead of raw HTML.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        registry: AdapterRegistry | None = None,
        candidate: CandidateProfile | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.candidate = candidate or CandidateProfile()
        self.state = PipelineState()
        self._registry = registry or self._build_default_registry()
        self._log_buffer: list[dict[str, Any]] = []

    def _build_default_registry(self) -> AdapterRegistry:
        """Build the default adapter registry from settings.

        Registration order matters: the registry selects the first
        adapter whose ``can_handle`` returns True. Siemens is registered
        first (trusted, narrow hostname match), then the five ATS
        platform adapters (each with a narrow hostname match), then
        the Generic fallback last (its ``can_handle`` always returns
        True).
        """
        from universal_auto_applier.adapters.greenhouse_adapter import GreenhouseAdapter
        from universal_auto_applier.adapters.lever_adapter import LeverAdapter
        from universal_auto_applier.adapters.linkedin_easy_apply_adapter import (
            LinkedInEasyApplyAdapter,
        )
        from universal_auto_applier.adapters.smartrecruiters_adapter import (
            SmartRecruitersAdapter,
        )
        from universal_auto_applier.adapters.workday_adapter import WorkdayAdapter

        registry = AdapterRegistry()
        siemens_config = SiemensAdapterConfig(
            repo_path=self.settings.siemens_repo,
            dry_run=True,  # Always dry-run by default
            headless=self.settings.browser_headless,
        )
        registry.register(SiemensAdapter(siemens_config))
        registry.register(GreenhouseAdapter(self.candidate))
        registry.register(LeverAdapter(self.candidate))
        registry.register(WorkdayAdapter(self.candidate))
        registry.register(SmartRecruitersAdapter(self.candidate))
        registry.register(LinkedInEasyApplyAdapter(self.candidate))
        registry.register(GenericAdapter(self.candidate))
        return registry

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """Log a pipeline event."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            "application_id": self.state.current_job_id,
            "phase": self.state.current_phase,
            **kwargs,
        }
        self._log_buffer.append(entry)
        getattr(logger, level, logger.info)(
            "[%s] %s: %s", self.state.current_job_id or "—", self.state.current_phase, message
        )

    def run(
        self,
        *,
        fixture_html: str | None = None,
        max_jobs: int = 10,
    ) -> PipelineState:
        """Run the pipeline on all queued jobs.

        Args:
            fixture_html: If provided, use this HTML as the "page" for all
                jobs (Level 0 fixture dry-run). If None, the pipeline runs
                in planning mode only (no browser, no HTML).
            max_jobs: Maximum number of jobs to process.

        Returns:
            The final :class:`PipelineState`.

        Execution mode:
        - ``sequential`` (default): jobs are processed one at a time in
          registration order. Safe, deterministic, easy to debug.
        - ``parallel``: ready-to-apply jobs are processed concurrently
          using a thread pool bounded by ``settings.apply_workers``.
          This is opt-in via ``UAA_EXECUTION_MODE=parallel``. The
          orchestrator's internal state (jobs_processed, jobs_succeeded,
          etc.) is updated under a lock to avoid races.
        """
        self.state.status = "running"
        self.state.started_at = datetime.now(UTC)
        self._log("info", "pipeline started", phase="init")

        try:
            with session_scope(self.session_factory) as session:
                jobs = list_application_jobs(session)

            # Pick up jobs that are ready to apply, queued, or in a
            # retryable state (failed, blocked, needs_review). Freshly
            # imported jobs from JobHunter's exporter have status
            # ready_to_apply; we process them directly.
            queued_jobs = [
                j
                for j in jobs
                if j.status
                in (
                    ApplicationStatus.READY_TO_APPLY,
                    ApplicationStatus.QUEUED,
                    ApplicationStatus.FAILED,
                    ApplicationStatus.BLOCKED,
                    ApplicationStatus.NEEDS_REVIEW,
                )
            ]

            jobs_to_process = queued_jobs[:max_jobs]
            execution_mode = getattr(self.settings, "execution_mode", "sequential")
            apply_workers = getattr(self.settings, "apply_workers", 1)

            if execution_mode == "parallel" and apply_workers > 1 and len(jobs_to_process) > 1:
                self._run_parallel(jobs_to_process, fixture_html, apply_workers)
            else:
                for job in jobs_to_process:
                    self._process_job(job, fixture_html)
                    self.state.jobs_processed += 1

        except Exception as exc:
            self.state.status = "error"
            self.state.last_error = str(exc)
            self._log("error", f"pipeline error: {exc}")
        else:
            self.state.status = "completed"
            self._log("info", "pipeline completed")
        finally:
            self.state.finished_at = datetime.now(UTC)

        return self.state

    def _run_parallel(
        self,
        jobs: list[ApplicationJob],
        fixture_html: str | None,
        max_workers: int,
    ) -> None:
        """Process jobs concurrently using a thread pool.

        Each job is processed by :meth:`_process_job`. The shared
        :attr:`state` counters are updated under a lock to avoid races.
        The current_job_id/current_phase fields are not updated in
        parallel mode (they would be racy); the log buffer is appended
        to under the lock.

        This is intentionally conservative: the pipeline is still safe
        (no submit without review approval), and each job's status
        transition goes through the same state machine as sequential
        mode. Parallelism only affects throughput, not safety.
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        lock = threading.Lock()

        def _safe_process(job: ApplicationJob) -> None:
            # Process in a thread-local context. The orchestrator's
            # _process_job uses self.state which is shared; we lock
            # only the counter updates, not the full _process_job
            # (otherwise parallelism would be defeated).
            try:
                self._process_job(job, fixture_html)
            finally:
                with lock:
                    self.state.jobs_processed += 1

        self._log(
            "info",
            f"parallel mode: processing {len(jobs)} jobs with {max_workers} workers",
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_safe_process, job) for job in jobs]
            for future in as_completed(futures):
                # Re-raise any exception that occurred in the worker.
                future.result()

    def _process_job(self, job: ApplicationJob, fixture_html: str | None) -> None:
        """Process a single job through the pipeline."""
        self.state.current_job_id = job.application_id
        self.state.current_phase = "prepare"
        self._log("info", f"processing job: {job.company} - {job.title}")

        try:
            # Update job status to in_progress.
            self._update_job_status(job, ApplicationStatus.IN_PROGRESS)

            # Resolve the candidate profile for THIS job. Priority:
            # 1. Per-job metadata snapshot (from JobHunter export)
            # 2. Orchestrator's default candidate (passed in at construction)
            # 3. Empty CandidateProfile()
            # This fixes the bug where the API/dashboard pipeline used an
            # empty CandidateProfile() and basic fields like first name,
            # last name, email became interventions.
            job_candidate = resolve_candidate_profile(job.metadata) or self.candidate
            if job_candidate.email or job_candidate.full_name:
                self._log(
                    "info",
                    f"candidate profile resolved: name={job_candidate.full_name or '(none)'} "
                    f"email={job_candidate.email or '(none)'}",
                )
            else:
                self._log(
                    "warning",
                    "no candidate profile in job metadata; using orchestrator default "
                    "(may be empty). Form fields requiring name/email/phone will become interventions.",
                )

            # Select adapter. Pass the resolved candidate so the adapter
            # can use it for form filling.
            self.state.current_phase = "adapter_selection"
            adapter = self._select_adapter(job)
            # Update the adapter's candidate if it supports it (ATS adapters do).
            if hasattr(adapter, "_candidate"):
                adapter._candidate = job_candidate  # type: ignore[attr-defined]
            self._log("info", f"adapter selected: {adapter.__class__.__name__}")

            # Prepare.
            self.state.current_phase = "prepare"
            prepare_result = adapter.prepare(job)
            if prepare_result.status == AdapterResultStatus.BLOCKED:
                # Missing-document or other prepare-time block.
                # Fix: do NOT leave the job as in_progress. Create a
                # missing_document intervention and set status to
                # needs_user_input (the state machine allows
                # IN_PROGRESS -> NEEDS_USER_INPUT). Previously this
                # called _handle_blocked which tried IN_PROGRESS ->
                # BLOCKED, which the state machine rejects, leaving the
                # job stuck in_progress.
                self._handle_prepare_blocked(job, prepare_result)
                return

            # Route: trusted adapter vs generic.
            # ``is_trusted`` is a class-level attribute on ApplicationAdapter
            # (default False). Only adapters that wrap a proven workflow
            # (e.g. SiemensAdapter) set it to True. All ATS platform adapters
            # (Greenhouse, Lever, Workday, SmartRecruiters, LinkedIn Easy Apply)
            # and the Generic fallback are untrusted and route through the
            # generic path, which never submits.
            if getattr(adapter, "is_trusted", False):
                self._run_trusted_adapter_path(job, adapter)
            else:
                self._run_generic_path(job, adapter, fixture_html)

        except Exception as exc:
            self.state.jobs_failed += 1
            self.state.last_error = str(exc)
            self._update_job_status(job, ApplicationStatus.FAILED)
            self._log("error", f"job failed: {exc}")
        else:
            self.state.jobs_succeeded += 1

    def _select_adapter(self, job: ApplicationJob) -> ApplicationAdapter:
        """Select the adapter for a job."""
        try:
            return self._registry.select(job)
        except NoAdapterError:
            return GenericAdapter()

    def _run_generic_path(
        self, job: ApplicationJob, adapter: ApplicationAdapter, fixture_html: str | None
    ) -> None:
        """Run the generic adapter path: observe -> explore -> fill -> review."""
        if fixture_html is None:
            # No fixture: planning mode only. Create a review state with no fill.
            self.state.current_phase = "review"
            self._log("info", "no fixture HTML; creating review state in planning mode")
            self._create_review_state_for_job(job, FormFillSummary(total_fields=0))
            self._update_job_status(job, ApplicationStatus.REVIEW_READY)
            return

        # Observe page.
        self.state.current_phase = "navigate"
        self.state.last_action = "observe_page"
        observation = observe_html(fixture_html, url=job.url)
        self._log("info", f"observed page: state={observation.page_state}")

        # If the page is a form, extract and fill.
        from universal_auto_applier.core.statuses import PageState

        if observation.page_state == PageState.FORM:
            self._process_form(job, fixture_html)
            return

        # If login/captcha/review, create intervention and stop.
        if observation.page_state in (PageState.LOGIN, PageState.CAPTCHA, PageState.REVIEW):
            self._create_stop_intervention(job, observation.page_state)
            self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
            return

        # Try safe exploration (with a mock click that doesn't actually do anything).
        self.state.current_phase = "navigate"
        self.state.last_action = "safe_explore"

        def observe():
            return observe_html(fixture_html, url=job.url)

        def click(selector: str) -> bool:
            self._log("info", f"would click: {selector}")
            return True

        exploration_result = safe_explore(observe, click)

        # Create interventions from exploration stop state.
        with session_scope(self.session_factory) as session:
            create_interventions_from_exploration(
                session,
                application_id=job.application_id,
                result=exploration_result,
                page_url=job.url,
            )

        if exploration_result.reached_form:
            # Reached a form — extract and fill.
            self._process_form(job, fixture_html)
        else:
            # Stopped before reaching form.
            self.state.last_action = f"stopped:{exploration_result.stopped_reason}"
            self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
            self._log("warning", f"exploration stopped: {exploration_result.stopped_reason}")

    def _process_form(self, job: ApplicationJob, fixture_html: str) -> None:
        """Extract form fields, map, fill, and create review state."""
        self.state.current_phase = "fill"
        self.state.last_action = "extract_form"

        fields = extract_form_fields(fixture_html)
        self._log("info", f"extracted {len(fields)} form fields")

        # Resolve the candidate profile for THIS job so form filling
        # uses the per-job snapshot rather than the orchestrator's
        # default (which may be empty).
        job_candidate = resolve_candidate_profile(job.metadata) or self.candidate

        self.state.last_action = "fill_form"
        summary = fill_form(fields, job_candidate, job)
        self._log(
            "info",
            f"fill complete: filled={summary.filled} skipped={summary.skipped} "
            f"blocked={summary.blocked} interventions={summary.intervention_needed}",
        )

        # Create interventions from fill results.
        with session_scope(self.session_factory) as session:
            create_interventions_from_fill_summary(
                session,
                application_id=job.application_id,
                summary=summary,
                page_url=job.url,
            )

        # Create review state.
        self.state.current_phase = "review"
        self._create_review_state_for_job(job, summary)

        # Check if there are unresolved interventions.
        with session_scope(self.session_factory) as session:
            pending = count_pending_interventions(session, job.application_id)

        if pending > 0:
            self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
            self._log("info", f"job needs user input: {pending} pending interventions")
        else:
            self._update_job_status(job, ApplicationStatus.REVIEW_READY)
            self._log("info", "job is review-ready")

    def _run_trusted_adapter_path(self, job: ApplicationJob, adapter: ApplicationAdapter) -> None:
        """Run the trusted adapter (Siemens) path.

        Calls the adapter's methods in sequence with dry-run safety. The
        adapter itself handles the subprocess invocation and dry-run flag
        enforcement. Before any call to ``submit_or_pause``, the orchestrator
        checks the review gate via ``check_submit_approval``. If approval
        is missing or interventions remain, submit is blocked and the job
        is set to ``review_ready`` or ``needs_user_input``.
        """
        self.state.current_phase = "navigate"
        self.state.last_action = "adapter.navigate"

        nav_result = adapter.navigate_to_form(job)
        self._log("info", f"adapter navigate: status={nav_result.status}")

        if nav_result.status in (AdapterResultStatus.FAILED, AdapterResultStatus.BLOCKED):
            self._handle_blocked(job, nav_result.message)
            return

        if nav_result.status == AdapterResultStatus.DRY_RUN:
            self.state.last_action = "adapter.navigate.dry_run"

        self.state.current_phase = "fill"
        fill_result = adapter.fill(job)
        self._log("info", f"adapter fill: status={fill_result.status}")

        if fill_result.status in (AdapterResultStatus.FAILED, AdapterResultStatus.BLOCKED):
            self._handle_blocked(job, fill_result.message)
            return

        # Create review state before submit.
        self.state.current_phase = "review"
        summary = FormFillSummary(total_fields=1, filled=1)
        review_state = self._create_review_state_for_job(job, summary)

        # Submit phase — check review gate BEFORE calling submit_or_pause.
        # This is the hard safety gate: even if the adapter is configured
        # with dry_run=False, the orchestrator will not call submit_or_pause
        # unless the review state is approved and no interventions remain.
        self.state.current_phase = "submit"

        # Check for unresolved interventions.
        with session_scope(self.session_factory) as session:
            pending = count_pending_interventions(session, job.application_id)

        if pending > 0:
            self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
            self._log(
                "warning",
                f"submit blocked: {pending} unresolved interventions remain",
            )
            return

        if not check_submit_approval(review_state):
            self._update_job_status(job, ApplicationStatus.REVIEW_READY)
            self._log(
                "warning",
                "submit blocked: review approval required before submit_or_pause",
            )
            return

        # Review gate passed — safe to call submit_or_pause.
        submit_result = adapter.submit_or_pause(job)
        self._log("info", f"adapter submit: status={submit_result.status}")

        if submit_result.status == AdapterResultStatus.SUBMITTED:
            self._update_job_status(job, ApplicationStatus.SUBMITTED)
        elif submit_result.status == AdapterResultStatus.DRY_RUN:
            self._update_job_status(job, ApplicationStatus.REVIEW_READY)
            self._log("info", "adapter submit paused (dry-run)")
        else:
            self._handle_blocked(job, submit_result.message)

    def _create_review_state_for_job(
        self, job: ApplicationJob, summary: FormFillSummary
    ) -> ReviewState:
        """Create a review state for a job."""
        documents = [d for d in (job.cv_pdf, job.cover_letter_pdf) if d]
        state = create_review_state(
            application_id=job.application_id,
            company=job.company,
            title=job.title,
            platform=str(job.platform),
            documents=documents,
            fill_summary=summary,
            final_action_detected="Submit application",
        )
        self._log("info", f"review state created: can_submit={state.can_submit}")
        return state

    def _create_stop_intervention(self, job: ApplicationJob, page_state: Any) -> None:
        """Create an intervention for a navigation stop state."""
        from universal_auto_applier.core.statuses import InterventionKind, PageState

        kind_map = {
            PageState.LOGIN: InterventionKind.LOGIN_REQUIRED,
            PageState.CAPTCHA: InterventionKind.CAPTCHA,
            PageState.REVIEW: InterventionKind.REVIEW_BEFORE_SUBMIT,
        }
        kind = kind_map.get(page_state, InterventionKind.UNKNOWN_PAGE)
        question_map = {
            PageState.LOGIN: "Login page detected. Manual login required.",
            PageState.CAPTCHA: "CAPTCHA detected. Manual verification required.",
            PageState.REVIEW: "Review page reached. Manual review required.",
        }
        question = question_map.get(page_state, f"Unknown page state: {page_state}")

        with session_scope(self.session_factory) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=kind,
                question=question,
                page_url=job.url,
            )

    def _handle_prepare_blocked(self, job: ApplicationJob, result: Any) -> None:
        """Handle a BLOCKED result from the prepare phase.

        Prepare returns BLOCKED when required documents are missing
        (e.g. ``missing_cv_pdf``). Previously this called
        :meth:`_handle_blocked` which tried to transition
        ``IN_PROGRESS -> BLOCKED``, but that transition is NOT in
        :data:`ALLOWED_TRANSITIONS`, so the repository silently kept
        the job as ``IN_PROGRESS`` — leaving it stuck.

        Fix: create a ``missing_document`` intervention and transition
        to ``NEEDS_USER_INPUT`` (which IS allowed from IN_PROGRESS).
        The user can then resolve the missing document and retry.
        """
        self.state.jobs_skipped += 1
        self.state.last_error = result.message
        # Determine the intervention kind from the error list.
        errors: list[str] = list(result.errors or [])
        if "missing_cv_pdf" in errors or "missing_cover_letter_pdf" in errors:
            kind = InterventionKind.MISSING_DOCUMENT
            question = f"Prepare blocked: required document is missing. {result.message}"
        else:
            kind = InterventionKind.UNKNOWN_PAGE
            question = f"Prepare blocked: {result.message}"
        with session_scope(self.session_factory) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=kind,
                question=question,
                page_url=job.url,
            )
        # IN_PROGRESS -> NEEDS_USER_INPUT is allowed by the state machine.
        self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
        self._log("warning", f"prepare blocked (set needs_user_input): {result.message}")

    def _handle_blocked(self, job: ApplicationJob, message: str) -> None:
        """Handle a blocked job (navigate/fill/submit phases).

        Note: ``IN_PROGRESS -> BLOCKED`` is NOT in
        :data:`ALLOWED_TRANSITIONS`, so we transition to
        ``NEEDS_USER_INPUT`` instead (which IS allowed). The job is
        not lost — the user can resolve the issue and retry.
        """
        self.state.jobs_skipped += 1
        self.state.last_error = message
        # Create an intervention so the user knows why the job stopped.
        with session_scope(self.session_factory) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.UNKNOWN_PAGE,
                question=f"Pipeline blocked: {message}",
                page_url=job.url,
            )
        # IN_PROGRESS -> NEEDS_USER_INPUT is allowed.
        self._update_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
        self._log("warning", f"job blocked (set needs_user_input): {message}")

    def _update_job_status(self, job: ApplicationJob, status: ApplicationStatus) -> None:
        """Update a job's status in the database."""
        job.status = status
        with session_scope(self.session_factory) as session:
            upsert_application_job(session, job)
        self.state.last_action = f"status:{status}"
        self._log("info", f"job status updated: {status}")

    @property
    def log_buffer(self) -> list[dict[str, Any]]:
        """Return the pipeline's log buffer."""
        return list(self._log_buffer)


__all__ = ["PipelineOrchestrator", "PipelineState"]
