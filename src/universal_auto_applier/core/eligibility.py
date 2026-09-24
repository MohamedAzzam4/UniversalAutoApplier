"""Shared eligibility rules for repeating application preparation or submission."""

from __future__ import annotations

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus


def repeat_processing_block_reason(job: ApplicationJob) -> str | None:
    """Return why ``job`` must not be prepared or submitted again, if blocked.

    Canonical workflow states and the operator-owned dashboard marker both
    represent an application that must not re-enter preparation or final
    submission. The marker is accepted only as the boolean ``True`` so an
    arbitrary producer value cannot accidentally claim or clear this state.
    """
    status = str(job.status)
    if status in {ApplicationStatus.SUBMITTED.value, ApplicationStatus.APPLIED.value}:
        return f"application status is {status}"
    if job.metadata.get("dashboard_submitted") is True:
        return "application is marked submitted by the operator"
    return None


def is_repeat_processing_eligible(job: ApplicationJob) -> bool:
    """Return whether repeat preparation/submission is permitted by job state."""
    return repeat_processing_block_reason(job) is None


__all__ = ["is_repeat_processing_eligible", "repeat_processing_block_reason"]
