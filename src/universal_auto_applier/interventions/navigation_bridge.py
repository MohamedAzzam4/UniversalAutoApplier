"""Bridge from Phase 3 navigation stop states to Phase 5 interventions.

Per ROADMAP.md Phase 5, the intervention queue should cover all cases where
automation cannot safely continue. Phase 3's SafeExplorer already stops on
login, captcha, review, unknown page, etc. — this module converts those
stop states into Intervention records so the user can see and resolve them.

Supported stop states -> intervention kinds:
  login_required  -> login_required
  captcha_detected -> captcha
  review_page     -> review_before_submit
  no_safe_action  -> unknown_page
  submit_detected -> review_before_submit
  already_submitted -> (no intervention needed)
  error_page      -> unknown_page
  expired         -> unknown_page
  click_failed    -> unknown_page
  max_steps_reached -> unknown_page
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from universal_auto_applier.core.statuses import InterventionKind
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.navigator.safe_explorer import ExplorationResult

logger = logging.getLogger("universal_auto_applier.interventions.navigation_bridge")


# Mapping from SafeExplorer stop reasons to intervention kinds and questions.
_STOP_REASON_MAP: dict[str, tuple[InterventionKind, str]] = {
    "login_required": (
        InterventionKind.LOGIN_REQUIRED,
        "Login page detected. Manual login required.",
    ),
    "captcha_detected": (
        InterventionKind.CAPTCHA,
        "CAPTCHA detected. Manual verification required.",
    ),
    "review_page": (
        InterventionKind.REVIEW_BEFORE_SUBMIT,
        "Review page reached. Manual review required before submission.",
    ),
    "submit_detected": (
        InterventionKind.REVIEW_BEFORE_SUBMIT,
        "Submit button detected. Manual review required before submission.",
    ),
    "no_safe_action": (
        InterventionKind.UNKNOWN_PAGE,
        "Unknown page with no safe clickable actions.",
    ),
    "error_page": (
        InterventionKind.UNKNOWN_PAGE,
        "Error page detected.",
    ),
    "expired": (
        InterventionKind.UNKNOWN_PAGE,
        "Job posting appears to be expired.",
    ),
    "click_failed": (
        InterventionKind.UNKNOWN_PAGE,
        "Click action failed during exploration.",
    ),
    "max_steps_reached": (
        InterventionKind.UNKNOWN_PAGE,
        "Maximum exploration steps reached without reaching a form.",
    ),
}


def create_interventions_from_exploration(
    session: Session,
    *,
    application_id: str,
    result: ExplorationResult,
    page_url: str | None = None,
    screenshot: str | None = None,
) -> int:
    """Create interventions from a SafeExplorer exploration result.

    For each stop reason that maps to an intervention kind, creates an
    intervention in the store. If the exploration reached a form (success),
    no intervention is created. Interventions are idempotent.

    Args:
        session: An open SQLAlchemy session.
        application_id: The job/application ID.
        result: The ExplorationResult from safe_explore().
        page_url: URL of the page where the stop occurred.
        screenshot: Path to a screenshot, if available.

    Returns:
        The number of interventions created (0 or 1).
    """
    # If exploration reached a form, no intervention needed.
    if result.reached_form:
        return 0

    # If already submitted, no intervention needed.
    if result.stopped_reason == "already_submitted":
        return 0

    # Look up the stop reason in the mapping.
    mapping = _STOP_REASON_MAP.get(result.stopped_reason)
    if mapping is None:
        # Unknown stop reason — create a generic unknown_page intervention.
        kind = InterventionKind.UNKNOWN_PAGE
        question = f"Exploration stopped: {result.stopped_reason}"
    else:
        kind, question = mapping

    # Use the URL from the final observation if available, or the last step.
    url = page_url
    if url is None and result.final_observation is not None:
        url = result.final_observation.url
    if url is None and result.steps:
        url = result.steps[-1].url

    # Use the screenshot from the final observation if available.
    shot = screenshot
    if shot is None and result.final_observation is not None:
        shot = result.final_observation.screenshot

    create_intervention(
        session,
        application_id=application_id,
        kind=kind,
        question=question,
        options=[],
        suggested_answer=None,
        confidence=None,
        field_selector=None,
        page_url=url,
        screenshot=shot,
    )

    logger.info(
        "[%s] navigation intervention created: reason=%s kind=%s",
        application_id[:12],
        result.stopped_reason,
        kind,
    )
    return 1


__all__ = ["create_interventions_from_exploration"]
