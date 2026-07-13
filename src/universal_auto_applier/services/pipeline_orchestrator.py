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
from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormFillSummary,
)
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
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
        """Build the default adapter registry from settings."""
        registry = AdapterRegistry()
        siemens_config = SiemensAdapterConfig(
            repo_path=self.settings.siemens_repo,
            dry_run=True,  # Always dry-run by default
            headless=self.settings.browser_headless,
        )
        registry.register(SiemensAdapter(siemens_config))
        registry.register(GenericAdapter())
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
        """
        self.state.status = "running"
        self.state.started_at = datetime.now(UTC)
        self._log("info", "pipeline started", phase="init")

        try:
            with session_scope(self.session_factory) as session:
                jobs = list_application_jobs(session)

            queued_jobs = [
                j
                for j in jobs
                if j.status
                in (
                    ApplicationStatus.QUEUED,
                    ApplicationStatus.FAILED,
                    ApplicationStatus.BLOCKED,
                    ApplicationStatus.NEEDS_REVIEW,
                )
            ]

            for job in queued_jobs[:max_jobs]:
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

    def _process_job(self, job: ApplicationJob, fixture_html: str | None) -> None:
        """Process a single job through the pipeline."""
        self.state.current_job_id = job.application_id
        self.state.current_phase = "prepare"
        self._log("info", f"processing job: {job.company} - {job.title}")

        try:
            # Update job status to in_progress.
            self._update_job_status(job, ApplicationStatus.IN_PROGRESS)

            # Select adapter.
            self.state.current_phase = "adapter_selection"
            adapter = self._select_adapter(job)
            self._log("info", f"adapter selected: {adapter.__class__.__name__}")

            # Prepare.
            self.state.current_phase = "prepare"
            prepare_result = adapter.prepare(job)
            if prepare_result.status == AdapterResultStatus.BLOCKED:
                self._handle_blocked(job, prepare_result.message)
                return

            # Route: trusted adapter vs generic.
            if adapter.__class__.__name__ == "SiemensAdapter":
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

        self.state.last_action = "fill_form"
        summary = fill_form(fields, self.candidate, job)
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

        For Phase 7, this calls the adapter's methods in sequence with
        dry-run safety. The adapter itself handles the subprocess invocation
        and dry-run flag enforcement.
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
        self._create_review_state_for_job(job, summary)

        # Submit phase — always check review gate.
        self.state.current_phase = "submit"
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

    def _handle_blocked(self, job: ApplicationJob, message: str) -> None:
        """Handle a blocked job."""
        self.state.jobs_skipped += 1
        self.state.last_error = message
        self._update_job_status(job, ApplicationStatus.BLOCKED)
        self._log("warning", f"job blocked: {message}")

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
