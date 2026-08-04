"""Authoritative post-submit job status transition policy (WQ-1).

This module is the ONE named production home for the mapping between a
structured :class:`SubmissionResult` and the resulting
:class:`ApplicationStatus` of the :class:`ApplicationJob`.

Policy (typed, no human-log parsing):

- ``submitted_confirmed`` -> ``SUBMITTED``.
- ``APPLIED`` is returned ONLY when a reliable structured ATS
  application/reference ID accompanies the confirmation. Page text and
  human-readable ``confirmation_evidence`` are NEVER parsed into a
  reference ID, so without structured ATS data the policy stops at
  ``SUBMITTED``.
- ``outcome_unknown`` -> ``NEEDS_REVIEW`` (explicit human review of an
  ambiguous submission).
- Every other state (validation_failed, blocked_user_action,
  approval_stale, submission_not_allowed, submit_control_ambiguous,
  already_submitted) yields NO job-status change: a pre-click or failed
  attempt never transitions the job to ``SUBMITTED``/``APPLIED``.

Application is idempotent and monotone:

- Re-applying the same result leaves the job unchanged.
- Terminal statuses (``APPLIED``, ``REJECTED``, ``SKIPPED``, ``CLOSED``)
  are never downgraded or overwritten.
- Transitions are applied by walking the allowed-transition graph from
  the job's current status to the target, one validated edge at a time
  (e.g. ``review_ready -> submitted -> needs_review``). If no path
  exists, the job status is left untouched and the reason is logged.
"""

from __future__ import annotations

import logging
from collections import deque

from sqlalchemy.orm import Session

from universal_auto_applier.core.statuses import (
    ALLOWED_TRANSITIONS,
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


def target_status_for_result(
    result: SubmissionResult,
    *,
    ats_reference_id: str = "",
) -> ApplicationStatus | None:
    """Map a structured :class:`SubmissionResult` to the post-submit status.

    Returns ``None`` when the result must NOT change the job status
    (pre-click failures, validation failures, blocked actions, stale
    approvals, already-submitted replays).

    ``ats_reference_id`` is the ONLY path to ``APPLIED``: it must be a
    reliable structured ATS application/reference ID, never text parsed
    from page content or logs.
    """
    if result.state == SubmissionResultState.SUBMITTED_CONFIRMED:
        if ats_reference_id:
            return ApplicationStatus.APPLIED
        return ApplicationStatus.SUBMITTED
    if result.state == SubmissionResultState.OUTCOME_UNKNOWN:
        return ApplicationStatus.NEEDS_REVIEW
    return None


def _find_transition_path(
    current: ApplicationStatus,
    target: ApplicationStatus,
) -> list[ApplicationStatus] | None:
    """BFS a path from ``current`` to ``target`` along allowed transitions.

    Returns the list of intermediate statuses INCLUDING ``target`` (may be
    empty when ``current == target``), or ``None`` when no path exists.
    """
    if current == target:
        return []
    queue: deque[tuple[ApplicationStatus, list[ApplicationStatus]]] = deque([(current, [])])
    seen: set[ApplicationStatus] = {current}
    while queue:
        node, path = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS.get(node, frozenset()):
            if nxt in seen:
                continue
            new_path = path + [nxt]
            if nxt == target:
                return new_path
            seen.add(nxt)
            queue.append((nxt, new_path))
    return None


def apply_result_status_transition(
    session: Session,
    result: SubmissionResult,
    *,
    ats_reference_id: str = "",
) -> ApplicationStatus | None:
    """Apply the post-submit status transition for ``result``.

    Returns the job's final status when a transition was applied (or was
    already satisfied), the unchanged status when the policy keeps it, or
    ``None`` when the job is missing.

    Rules:
    - The policy target comes from :func:`target_status_for_result`.
    - Terminal statuses are never downgraded.
    - The transition graph is walked one validated edge at a time; a
      missing edge/path leaves the status untouched (idempotent).
    - Runs inside the caller's session; the caller controls commit.
    """
    target = target_status_for_result(result, ats_reference_id=ats_reference_id)
    job = get_application_job(session, result.application_id)
    if job is None:
        logger.warning("[%s] job not found; no status transition", result.application_id[:12])
        return None
    if target is None:
        return job.status
    current = job.status
    if current in TERMINAL_STATUSES:
        logger.info(
            "[%s] status %s is terminal; no post-submit transition applied",
            result.application_id[:12],
            current.value,
        )
        return current
    if current == target:
        return current

    path = _find_transition_path(current, target)
    if path is None:
        logger.warning(
            "[%s] no allowed transition path %s -> %s; status left unchanged",
            result.application_id[:12],
            current.value,
            target.value,
        )
        return current

    try:
        for step in path:
            update_application_status(session, result.application_id, step)
    except ValueError as exc:
        # The result row is persisted by the caller in the same session;
        # do NOT roll back the whole transaction. Best-effort: the job
        # status stays unchanged and the reason is logged for review.
        logger.error(
            "[%s] transition walk failed (%s); status left unchanged",
            result.application_id[:12],
            exc,
        )
        return current

    logger.info(
        "[%s] post-submit status transitioned %s -> %s (state=%s)",
        result.application_id[:12],
        current.value,
        target.value,
        result.state.value,
    )
    return target


__all__ = [
    "apply_result_status_transition",
    "target_status_for_result",
]
