"""Authoritative post-submit job status transition policy (WQ-1).

This module is the ONE named production home for the mapping between a
structured :class:`SubmissionResult` and the resulting
:class:`ApplicationStatus` of the :class:`ApplicationJob`.

Policy (typed, no human-log parsing):

- ``submitted_confirmed`` -> ``SUBMITTED``.
- ``APPLIED`` is returned ONLY when a reliable structured ATS
  application/reference ID accompanies the confirmation (as the persisted
  ``ats_reference_id`` field of the result). Page text and human-readable
  ``confirmation_evidence`` are NEVER parsed into a reference ID, so without
  structured ATS data the policy stops at ``SUBMITTED``.
- ``outcome_unknown`` -> ``NEEDS_REVIEW`` (explicit human review of an
  ambiguous submission).
- Every other state (validation_failed, blocked_user_action,
  approval_stale, submission_not_allowed, submit_control_ambiguous,
  already_submitted) yields NO job-status change: a pre-click or failed
  attempt never transitions the job to ``SUBMITTED``/``APPLIED``.

Transition application is EXPLICIT and monotone. There is no graph walk:

- Only these edges are ever applied, keyed on the job's current status:

  +--------------------------+--------------------------+------------------------------------+
  | current status           | result state             | explicit transition(s)              |
  +==========================+==========================+====================================+
  | review_ready             | submitted_confirmed      | review_ready -> submitted           |
  | review_ready             | submitted_confirmed+ref  | review_ready -> submitted -> applied|
  | submitted                | submitted_confirmed+ref  | submitted -> applied                |
  | review_ready             | outcome_unknown          | review_ready -> needs_review (direct)|
  | submitted                | outcome_unknown          | submitted -> needs_review           |
  +--------------------------+--------------------------+------------------------------------+

- Earlier pipeline statuses (``discovered``...``in_progress``,
  ``needs_user_input``, ``failed``, ``blocked``, ``queued``, ``skipped``,
  ``closed``, ``rejected``) are NEVER auto-advanced by a result. A result
  can only move a job from ``review_ready`` or ``submitted``.
- Same-status replays are no-ops. Terminal statuses (``APPLIED``,
  ``REJECTED``, ``SKIPPED``, ``CLOSED``) are never downgraded or overwritten.
- Invariant failures (missing allowed edge) raise :class:`ValueError` from
  the persistence store; they are NOT swallowed here so the caller's
  transaction rolls back the result row and the status change together.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from universal_auto_applier.core.statuses import (
    TERMINAL_STATUSES,
    ApplicationStatus,
)
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    update_application_status,
)
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
)

logger = logging.getLogger("universal_auto_applier.submission.status_transitions")

# The explicit post-submission transition table. Keyed on
# (current job status, policy target). Each value is the exact sequence of
# validated edges to apply. Missing keys mean "no transition" — earlier
# pipeline statuses are never auto-advanced, and failed/blocked results never
# touch the job status.
POST_SUBMIT_TRANSITIONS: dict[
    tuple[ApplicationStatus, ApplicationStatus], tuple[ApplicationStatus, ...]
] = {
    (ApplicationStatus.REVIEW_READY, ApplicationStatus.SUBMITTED): (ApplicationStatus.SUBMITTED,),
    (ApplicationStatus.REVIEW_READY, ApplicationStatus.APPLIED): (
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.APPLIED,
    ),
    (ApplicationStatus.REVIEW_READY, ApplicationStatus.NEEDS_REVIEW): (
        ApplicationStatus.NEEDS_REVIEW,
    ),
    (ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED): (ApplicationStatus.APPLIED,),
    (ApplicationStatus.SUBMITTED, ApplicationStatus.NEEDS_REVIEW): (
        ApplicationStatus.NEEDS_REVIEW,
    ),
}


def target_status_for_result(result: SubmissionResult) -> ApplicationStatus | None:
    """Map a structured :class:`SubmissionResult` to the post-submission status.

    Returns ``None`` when the result must NOT change the job status
    (pre-click failures, validation failures, blocked actions, stale
    approvals, already-submitted replays).

    ``result.ats_reference_id`` is the ONLY path to ``APPLIED``: it must be a
    reliable structured ATS application/reference ID, never text parsed from
    page content or logs.
    """
    if result.state == SubmissionResultState.SUBMITTED_CONFIRMED:
        if result.ats_reference_id:
            return ApplicationStatus.APPLIED
        return ApplicationStatus.SUBMITTED
    if result.state == SubmissionResultState.OUTCOME_UNKNOWN:
        return ApplicationStatus.NEEDS_REVIEW
    return None


def apply_result_status_transition(
    session: Session,
    result: SubmissionResult,
) -> ApplicationStatus | None:
    """Apply the post-submission status transition for ``result``.

    Returns the job's final status when a transition was applied (or was
    already satisfied), the unchanged status when the policy keeps it, or
    ``None`` when the job is missing.

    Rules:
    - The policy target comes from :func:`target_status_for_result`.
    - Terminal statuses are never downgraded.
    - Only the explicit edges in :data:`POST_SUBMIT_TRANSITIONS` are applied;
      there is no graph-walking, so earlier pipeline statuses are never
      auto-advanced by a result.
    - Invariant failures raise (no best-effort swallowing): the caller's
      transaction rolls back together with the result row.
    - Runs inside the caller's session; the caller controls commit.
    """
    target = target_status_for_result(result)
    job = get_application_job(session, result.application_id)
    if job is None:
        logger.warning("[%s] job not found; no status transition", result.application_id[:12])
        return None
    current = job.status
    if current in TERMINAL_STATUSES:
        logger.info(
            "[%s] status %s is terminal; no post-submission transition applied",
            result.application_id[:12],
            current.value,
        )
        return current
    if target is None:
        return current
    if current == target:
        return current

    steps = POST_SUBMIT_TRANSITIONS.get((current, target))
    if steps is None:
        logger.info(
            "[%s] no post-submission transition %s -> %s; status left unchanged",
            result.application_id[:12],
            current.value,
            target.value,
        )
        return current

    for step in steps:
        update_application_status(session, result.application_id, step)

    logger.info(
        "[%s] post-submission status transitioned %s -> %s (state=%s)",
        result.application_id[:12],
        current.value,
        target.value,
        result.state.value,
    )
    return target


__all__ = [
    "POST_SUBMIT_TRANSITIONS",
    "apply_result_status_transition",
    "target_status_for_result",
]
