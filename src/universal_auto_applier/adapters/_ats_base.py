"""Common base for untrusted ATS platform adapters.

Per ``ROADMAP.md`` Phase 7, every platform-specific adapter (Greenhouse,
Lever, Workday, SmartRecruiters, LinkedIn Easy Apply, and the improved
Generic fallback) shares the same safety invariants:

- ``is_trusted`` is False. The pipeline orchestrator only allows
  ``submit_or_pause`` to actually submit when ``is_trusted`` is True AND
  the review gate has approved. Untrusted adapters therefore never
  submit.
- ``submit_or_pause`` ALWAYS returns ``review_ready`` and creates a
  ``review_before_submit`` intervention. It never clicks a submit
  button.
- ``navigate_to_form`` and ``fill`` use the shared Phase 3/4
  infrastructure (:func:`observe_html`, :func:`safe_explore`,
  :func:`extract_form_fields`, :func:`fill_form`) on the provided
  fixture HTML. They create interventions for login, captcha, password,
  and unknown required fields, and stop there.
- Every phase returns a structured :class:`AdapterResult` even on
  failure. Layout changes / missing selectors fail safely with
  ``AdapterResult.failed`` and an ``unknown_page`` intervention is
  created.

Subclasses provide:
- ``platform`` (the :class:`Platform` enum value)
- ``can_handle`` (URL / platform-field detection)
- optional ``platform_notes`` for documentation strings
- optional ``known_selectors`` for future platform-specific selector
  hints (currently unused by the shared infra, but reserved so adapters
  can declare their known selectors in one place)

This base is private (``_ats_base``) because it is an implementation
detail of the concrete adapters. External code should import the
concrete adapters from their named modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.core.models import (
    AdapterResult,
    ApplicationJob,
    CandidateProfile,
    FormFillSummary,
)
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    InterventionKind,
    PageState,
    Phase,
    Platform,
)
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.form_engine.schema_extractor import extract_form_fields
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import safe_explore

logger = logging.getLogger("universal_auto_applier.adapters.ats_base")


@dataclass
class ATSAdapterState:
    """Per-run mutable state tracked by an ATS adapter.

    Stored on the adapter instance so that ``submit_or_pause`` can
    inspect what ``fill`` recorded (e.g. the final observation URL, the
    fill summary) without requiring the caller to thread state through.
    State is reset at the start of every ``prepare`` call so a single
    adapter instance can be reused across jobs.
    """

    application_id: str | None = None
    last_observation_url: str | None = None
    last_page_state: PageState | None = None
    fill_summary: FormFillSummary | None = None
    notes: list[str] = field(default_factory=list[str])


class _UntrustedATSAdapter(ApplicationAdapter):
    """Common base for untrusted ATS adapters.

    Concrete adapters set ``platform`` and implement ``can_handle``.
    They may also override ``_platform_specific_prepare_notes`` to add
    platform-specific prepare-time checks (e.g. Greenhouse requires a
    cover letter PDF for some postings).

    Safety invariants (enforced here, not in subclasses):
    - ``is_trusted`` is always False.
    - ``submit_or_pause`` always returns ``review_ready`` and creates a
      ``review_before_submit`` intervention. It never submits.
    - ``navigate_to_form`` and ``fill`` use the shared infra on the
      provided fixture HTML. If no fixture HTML is provided, they
      return a structured ``dry_run`` result (planning mode).
    - On layout change / missing selector / unknown page state, the
      adapter returns ``AdapterResult.failed`` and an ``unknown_page``
      intervention is created.
    """

    platform: Platform = Platform.UNKNOWN
    is_trusted: bool = False

    def __init__(self, candidate: CandidateProfile | None = None) -> None:
        self._candidate = candidate or CandidateProfile()
        self._state = ATSAdapterState()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _reset_state(self, job: ApplicationJob) -> None:
        """Reset per-run state for ``job``."""
        self._state = ATSAdapterState(application_id=job.application_id)

    @property
    def state(self) -> ATSAdapterState:
        """Return the adapter's per-run state (for diagnostics)."""
        return self._state

    # ------------------------------------------------------------------
    # Hook for subclasses
    # ------------------------------------------------------------------

    def _platform_specific_prepare_notes(self, job: ApplicationJob) -> list[str]:
        """Return a list of platform-specific prepare notes for ``job``.

        Subclasses may override this to add platform-specific checks
        (e.g. "Greenhouse posting requires a cover letter"). Notes are
        added to the prepare result's metadata and do NOT change the
        result status — they are informational only. To block a job,
        subclasses should override ``prepare`` directly.
        """
        return []

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    def prepare(self, job: ApplicationJob) -> AdapterResult:
        """Prepare to apply to ``job``.

        Resets per-run state and records platform-specific notes. Does
        NOT launch a browser. Returns a structured ``AdapterResult``
        with ``status=success`` (or ``blocked`` if the job is missing
        required documents and the platform requires them).
        """
        self._reset_state(job)
        notes = self._platform_specific_prepare_notes(job)

        # All current ATS platforms require at least a CV. If the job
        # has no cv_pdf, we block here rather than discovering it
        # later during form filling.
        if not job.cv_pdf:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=Phase.PREPARE,
                message=(
                    f"{self.platform} adapter requires cv_pdf, but the job has none. "
                    "JobHunter must tailor a CV before this job can be applied to."
                ),
                application_id=job.application_id,
                platform=self.platform,
                errors=["missing_cv_pdf"],
                metadata={"platform_notes": notes},
            )

        return AdapterResult.success(
            phase=Phase.PREPARE,
            message=(
                f"{self.platform} adapter prepared. "
                f"Adapter is untrusted; submit will pause for review."
            ),
            application_id=job.application_id,
            platform=self.platform,
            next_action="navigate_to_form",
            metadata={"platform_notes": notes, "is_trusted": False},
        )

    def navigate_to_form(
        self,
        job: ApplicationJob,
        *,
        fixture_html: str | None = None,
    ) -> AdapterResult:
        """Navigate from the job URL to the application form.

        If ``fixture_html`` is provided (Level 0 dry-run), the adapter
        observes the page and runs the safe exploration loop on it.
        If exploration reaches a form, the result is ``success`` and
        ``next_action='fill'``. If exploration stops on login, captcha,
        review, or unknown page, the result is ``needs_user_input`` and
        an intervention is created via the navigation bridge.

        If ``fixture_html`` is None (planning mode), the adapter returns
        a structured ``dry_run`` result without observing any page.

        Safety:
        - Never clicks ``dangerous_submit``.
        - Never clicks ``unknown`` elements.
        - Stops on captcha, login, form visible, submit detected,
          unknown page, or max steps.
        """
        self._state.application_id = job.application_id

        if fixture_html is None:
            self._state.notes.append("navigate: planning mode (no fixture HTML)")
            return AdapterResult(
                status=AdapterResultStatus.DRY_RUN,
                phase=Phase.NAVIGATE,
                message=(
                    f"{self.platform} adapter navigate ran in planning mode "
                    "(no fixture HTML provided). No browser page was observed."
                ),
                application_id=job.application_id,
                platform=self.platform,
                next_action="fill",
                metadata={"planning_mode": True},
            )

        # Observe the page.
        try:
            observation = observe_html(fixture_html, url=job.url)
        except Exception as exc:
            return self._fail_safely(
                job, Phase.NAVIGATE, f"observe_html failed: {exc}", reason="observe_error"
            )

        self._state.last_observation_url = observation.url
        self._state.last_page_state = observation.page_state

        # If the page is already a form, we're done.
        if observation.page_state == PageState.FORM:
            return AdapterResult.success(
                phase=Phase.NAVIGATE,
                message=(
                    f"{self.platform} adapter reached a form page (state={observation.page_state})."
                ),
                application_id=job.application_id,
                platform=self.platform,
                next_action="fill",
                metadata={"page_state": str(observation.page_state)},
            )

        # If the page is login/captcha/review, stop with an intervention.
        if observation.page_state in (PageState.LOGIN, PageState.CAPTCHA, PageState.REVIEW):
            return self._stop_for_page_state(job, Phase.NAVIGATE, observation.page_state)

        # Otherwise, run the safe exploration loop. The click callback
        # is a no-op for fixture HTML (we cannot actually navigate a
        # static HTML string), so safe_explore will stop after the first
        # step if there is no safe_apply button to "click".
        def observe() -> Any:
            return observe_html(fixture_html, url=job.url)

        def click(selector: str) -> bool:
            # In fixture mode, "clicking" does not change the page. We
            # log the would-be click and return True so the explorer
            # records the step. The next observe() will return the same
            # page, and the explorer will stop on the next iteration
            # because no progress was made (no_safe_action or max_steps).
            logger.info(
                "[%s] %s navigate would-click: %s (fixture mode, no real navigation)",
                job.application_id[:12],
                self.platform,
                selector,
            )
            return True

        try:
            exploration = safe_explore(observe, click)
        except Exception as exc:
            return self._fail_safely(
                job, Phase.NAVIGATE, f"safe_explore failed: {exc}", reason="explore_error"
            )

        # Convert exploration stop state to interventions.
        # Note: we cannot create interventions here because we do not
        # have a SQLAlchemy session. The pipeline orchestrator's generic
        # path calls the navigation bridge directly. For direct adapter
        # callers, the structured AdapterResult carries enough info.
        if exploration.reached_form:
            self._state.last_page_state = PageState.FORM
            return AdapterResult.success(
                phase=Phase.NAVIGATE,
                message=(
                    f"{self.platform} adapter explored {exploration.step_count} step(s) "
                    "and reached a form page."
                ),
                application_id=job.application_id,
                platform=self.platform,
                next_action="fill",
                metadata={
                    "page_state": str(PageState.FORM),
                    "exploration_steps": exploration.step_count,
                    "stopped_reason": exploration.stopped_reason,
                },
            )

        # Stopped before reaching form. Return needs_user_input with
        # the stop reason so a caller (or the orchestrator) can create
        # an intervention via the navigation bridge.
        self._state.last_page_state = exploration.final_state
        return AdapterResult(
            status=AdapterResultStatus.NEEDS_USER_INPUT,
            phase=Phase.NAVIGATE,
            message=(
                f"{self.platform} adapter stopped during navigation: "
                f"{exploration.stopped_reason}. Manual intervention required."
            ),
            application_id=job.application_id,
            platform=self.platform,
            next_action="resolve_intervention",
            errors=[exploration.stopped_reason],
            metadata={
                "page_state": str(exploration.final_state),
                "stopped_reason": exploration.stopped_reason,
                "exploration_steps": exploration.step_count,
            },
        )

    def fill(
        self,
        job: ApplicationJob,
        *,
        fixture_html: str | None = None,
    ) -> AdapterResult:
        """Fill the application form.

        If ``fixture_html`` is provided, the adapter extracts form
        fields, maps them via the shared field mapper, and runs the
        shared fill engine. Interventions are NOT created here (the
        adapter has no SQLAlchemy session); instead, the structured
        ``AdapterResult`` carries the fill summary and a list of
        fields that need intervention. The pipeline orchestrator's
        generic path calls the fill bridge directly.

        If ``fixture_html`` is None (planning mode), the adapter returns
        a structured ``dry_run`` result without touching any form.

        Safety:
        - Password fields are blocked by the fill engine.
        - Unknown required fields are reported as ``intervention_needed``
          in the fill summary.
        - File inputs without a matching document path are reported as
          ``intervention_needed``.
        - The adapter NEVER clicks submit.
        """
        if fixture_html is None:
            self._state.notes.append("fill: planning mode (no fixture HTML)")
            return AdapterResult(
                status=AdapterResultStatus.DRY_RUN,
                phase=Phase.FILL,
                message=(
                    f"{self.platform} adapter fill ran in planning mode "
                    "(no fixture HTML provided). No fields were extracted or filled."
                ),
                application_id=job.application_id,
                platform=self.platform,
                next_action="review",
                metadata={"planning_mode": True},
            )

        # Extract form fields.
        try:
            fields = extract_form_fields(fixture_html)
        except Exception as exc:
            return self._fail_safely(
                job, Phase.FILL, f"extract_form_fields failed: {exc}", reason="extract_error"
            )

        if not fields:
            # No form fields on the page. This could mean the page
            # changed layout (e.g. a multi-step form navigated to a
            # non-form page). Fail safely.
            return self._fail_safely(
                job,
                Phase.FILL,
                (
                    f"{self.platform} adapter found no form fields on the page. "
                    "Page layout may have changed."
                ),
                reason="no_form_fields",
            )

        # Run the shared fill engine.
        try:
            summary = fill_form(fields, self._candidate, job)
        except Exception as exc:
            return self._fail_safely(
                job, Phase.FILL, f"fill_form failed: {exc}", reason="fill_error"
            )

        self._state.fill_summary = summary

        # Determine next action based on whether interventions are needed.
        if summary.intervention_needed > 0:
            next_action = "resolve_intervention"
            status = AdapterResultStatus.NEEDS_USER_INPUT
            message = (
                f"{self.platform} adapter filled {summary.filled} of {summary.total_fields} "
                f"fields; {summary.intervention_needed} need intervention."
            )
        else:
            next_action = "review"
            status = AdapterResultStatus.SUCCESS
            message = (
                f"{self.platform} adapter filled {summary.filled} of {summary.total_fields} "
                "fields; no interventions needed."
            )

        # Serialize fill summary for the metadata. The FillResult list
        # is converted to dicts so AdapterResult.metadata stays JSON-safe.
        return AdapterResult(
            status=status,
            phase=Phase.FILL,
            message=message,
            application_id=job.application_id,
            platform=self.platform,
            next_action=next_action,
            errors=[
                r.explanation
                for r in summary.results
                if r.status in ("intervention_needed", "blocked")
            ],
            metadata={
                "fill_summary": {
                    "total_fields": summary.total_fields,
                    "filled": summary.filled,
                    "skipped": summary.skipped,
                    "blocked": summary.blocked,
                    "intervention_needed": summary.intervention_needed,
                    "results": [
                        {
                            "field_selector": r.field_selector,
                            "field_type": r.field_type,
                            "status": r.status,
                            "source": r.source,
                            "explanation": r.explanation,
                            "confidence": r.confidence,
                        }
                        for r in summary.results
                    ],
                },
            },
        )

    def submit_or_pause(
        self,
        job: ApplicationJob,
        *,
        approved: bool = False,
    ) -> AdapterResult:
        """Submit or pause for review.

        Untrusted adapters NEVER submit. Even if ``approved`` is True
        (which would only happen if a caller bypassed the orchestrator's
        review gate), this method returns ``review_ready`` and records
        a ``review_before_submit`` intervention note.

        The actual review state and intervention are created by the
        pipeline orchestrator (which has a SQLAlchemy session). This
        method's job is to refuse to submit and to return a structured
        result.

        Safety:
        - Never clicks a submit button.
        - Always returns ``review_ready``.
        - Records the refusal in the adapter state.
        """
        self._state.notes.append(
            "submit_or_pause: refused (untrusted adapter; review_before_submit required)"
        )
        logger.info(
            "[%s] %s submit_or_pause refused: untrusted adapter pauses for review "
            "(approved=%s, but adapter is_trusted=False)",
            job.application_id[:12],
            self.platform,
            approved,
        )
        return AdapterResult(
            status=AdapterResultStatus.REVIEW_READY,
            phase=Phase.SUBMIT,
            message=(
                f"{self.platform} adapter is untrusted and never submits. "
                "Application paused at review_before_submit. "
                "A human must review and explicitly approve submission."
            ),
            application_id=job.application_id,
            platform=self.platform,
            next_action="await_review_approval",
            errors=[],
            metadata={
                "is_trusted": False,
                "submitted": False,
                "review_before_submit": True,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stop_for_page_state(
        self,
        job: ApplicationJob,
        phase: Phase,
        page_state: PageState,
    ) -> AdapterResult:
        """Build a needs_user_input result for a stop page state."""
        kind_map: dict[PageState, InterventionKind] = {
            PageState.LOGIN: InterventionKind.LOGIN_REQUIRED,
            PageState.CAPTCHA: InterventionKind.CAPTCHA,
            PageState.REVIEW: InterventionKind.REVIEW_BEFORE_SUBMIT,
        }
        kind = kind_map.get(page_state, InterventionKind.UNKNOWN_PAGE)
        question_map: dict[PageState, str] = {
            PageState.LOGIN: "Login page detected. Manual login required.",
            PageState.CAPTCHA: "CAPTCHA detected. Manual verification required.",
            PageState.REVIEW: "Review page reached. Manual review required.",
        }
        question = question_map.get(page_state, f"Unexpected page state: {page_state}")
        self._state.last_page_state = page_state
        return AdapterResult(
            status=AdapterResultStatus.NEEDS_USER_INPUT,
            phase=phase,
            message=question,
            application_id=job.application_id,
            platform=self.platform,
            next_action="resolve_intervention",
            errors=[str(kind)],
            metadata={
                "page_state": str(page_state),
                "intervention_kind": str(kind),
            },
        )

    def _fail_safely(
        self,
        job: ApplicationJob,
        phase: Phase,
        message: str,
        *,
        reason: str,
    ) -> AdapterResult:
        """Build a failed result and log it. Never raises."""
        logger.error(
            "[%s] %s %s failed safely: %s (reason=%s)",
            job.application_id[:12],
            self.platform,
            phase,
            message,
            reason,
        )
        self._state.notes.append(f"{phase}: failed safely ({reason})")
        return AdapterResult.failed(
            phase=phase,
            message=message,
            application_id=job.application_id,
            platform=self.platform,
            errors=[reason],
            metadata={"failed_safely": True, "reason": reason},
        )


__all__ = ["_UntrustedATSAdapter", "ATSAdapterState"]
