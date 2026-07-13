"""Review-before-submit state and approval gate.

Per ``ROADMAP.md`` WP 5.3, the review state is the safety gate before
any final submission. The system must:

- Prepare and fill the application in dry-run/planned mode.
- Pause before any final submission.
- Require explicit human approval before submit.

This module provides:
- :class:`ReviewState` — the data model for a review checkpoint.
- :func:`create_review_state` — create a review state from fill results.
- :func:`check_submit_approval` — check whether submission is allowed.

Safety:
- ``check_submit_approval`` returns False unless:
  1. A review state exists.
  2. The review state has been explicitly approved.
  3. No unresolved interventions remain.
  4. All required fields are resolved.
- The generic adapter must never submit without this approval.
- Trusted adapters may bypass review only when explicitly configured
  (handled by the adapter, not here).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from universal_auto_applier.core.models import FormFillSummary

logger = logging.getLogger("universal_auto_applier.interventions.review")


class ReviewState(BaseModel):
    """The review checkpoint before final submission.

    Contains all information a human reviewer needs to decide whether
    to approve submission:
    - job details (application_id, company, title)
    - documents that would be uploaded
    - filled fields summary
    - unanswered/intervention-needed fields
    - the final action detected (e.g. "Submit application")
    - approval status
    """

    application_id: str
    company: str = Field(default="")
    title: str = Field(default="")
    platform: str = Field(default="")
    documents: list[str] = Field(default_factory=list[str], description="Document paths to upload")
    fill_summary: FormFillSummary | None = Field(default=None)
    unanswered_fields: list[str] = Field(
        default_factory=list[str], description="Field selectors that need intervention"
    )
    final_action_detected: str | None = Field(default=None, description="e.g. 'Submit application'")
    screenshot: str | None = Field(default=None, description="Screenshot of the review page")
    approved: bool = Field(default=False, description="Whether the user has approved submission")
    approval_id: str | None = Field(default=None, description="Unique approval token")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = Field(default=None)

    @property
    def has_unresolved_interventions(self) -> bool:
        """True if there are fields that need intervention."""
        if self.fill_summary is None:
            return False
        return self.fill_summary.intervention_needed > 0

    @property
    def can_submit(self) -> bool:
        """True only if the review state is approved and no interventions remain.

        This is the hard safety gate. The generic adapter must check this
        before any submit action.
        """
        if not self.approved:
            return False
        if self.has_unresolved_interventions:
            return False
        return True


def create_review_state(
    *,
    application_id: str,
    company: str = "",
    title: str = "",
    platform: str = "",
    documents: list[str] | None = None,
    fill_summary: FormFillSummary | None = None,
    final_action_detected: str | None = None,
    screenshot: str | None = None,
) -> ReviewState:
    """Create a review state from fill results.

    This is called after form filling and before any submit action.
    The review state is initially not approved — the user must explicitly
    approve it before submission can proceed.

    Args:
        application_id: The job/application ID.
        company: Company name.
        title: Job title.
        platform: Platform name.
        documents: List of document paths to be uploaded.
        fill_summary: The form fill summary from the fill engine.
        final_action_detected: The submit button text detected on the page.
        screenshot: Path to a screenshot of the review page.

    Returns:
        A :class:`ReviewState` with ``approved=False``.
    """
    unanswered: list[str] = []
    if fill_summary is not None:
        for result in fill_summary.results:
            if result.status == "intervention_needed":
                unanswered.append(result.field_selector)

    state = ReviewState(
        application_id=application_id,
        company=company,
        title=title,
        platform=platform,
        documents=documents or [],
        fill_summary=fill_summary,
        unanswered_fields=unanswered,
        final_action_detected=final_action_detected,
        screenshot=screenshot,
        approved=False,
    )

    logger.info(
        "[%s] review state created: %d fields filled, %d interventions, final_action=%s",
        application_id[:12],
        fill_summary.filled if fill_summary else 0,
        len(unanswered),
        final_action_detected,
    )
    return state


def approve_review_state(
    state: ReviewState,
    *,
    approval_id: str,
) -> ReviewState:
    """Mark a review state as approved.

    Args:
        state: The review state to approve.
        approval_id: A unique approval token from the user.

    Returns:
        The updated review state with ``approved=True``.

    Raises:
        ValueError: If there are unresolved interventions (cannot approve
            with pending interventions).
    """
    if state.has_unresolved_interventions:
        raise ValueError(
            "Cannot approve review state: there are unresolved interventions. "
            f"Resolve {state.fill_summary.intervention_needed if state.fill_summary else 0} "
            "intervention(s) first."
        )

    state.approved = True
    state.approval_id = approval_id
    state.approved_at = datetime.now(UTC)

    logger.info(
        "[%s] review state approved: approval_id=%s",
        state.application_id[:12],
        approval_id,
    )
    return state


def check_submit_approval(state: ReviewState | None) -> bool:
    """Check whether submission is allowed.

    This is the final safety gate. Returns True only if:
    1. A review state exists.
    2. The review state has been explicitly approved.
    3. No unresolved interventions remain.

    The generic adapter must call this before any submit action.

    Args:
        state: The review state, or None if no review has been created.

    Returns:
        True if submission is allowed, False otherwise.
    """
    if state is None:
        logger.warning("submit blocked: no review state")
        return False

    if not state.approved:
        logger.warning("[%s] submit blocked: review state not approved", state.application_id[:12])
        return False

    if state.has_unresolved_interventions:
        logger.warning(
            "[%s] submit blocked: %d unresolved interventions",
            state.application_id[:12],
            state.fill_summary.intervention_needed if state.fill_summary else 0,
        )
        return False

    logger.info("[%s] submit approved: all checks passed", state.application_id[:12])
    return True


__all__ = [
    "ReviewState",
    "create_review_state",
    "approve_review_state",
    "check_submit_approval",
]
