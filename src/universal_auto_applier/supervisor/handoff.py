"""Human handoff queue — structured tasks for the owner.

Created whenever the supervisor hits a condition only a human may decide.
Sanitized by design: company/role and reason codes are fine; no candidate
PII, no filled values, no raw documents.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.persistence.models import HumanHandoffRow
from universal_auto_applier.supervisor.store import create_human_handoff


def record_handoff(
    session: Session,
    *,
    run_id: str,
    job: ApplicationJob | None,
    application_id: str,
    reason_code: str,
    question: str = "",
    action_required: str = "",
    detail: dict[str, Any] | None = None,
) -> HumanHandoffRow:
    """Persist a sanitized human handoff for one application."""
    detail_payload: dict[str, Any] = {"value_redacted": True}
    if detail:
        # Only pass through explicitly whitelisted, non-PII keys.
        for key in ("policy_id", "attempt", "reason_code", "field_label"):
            if key in detail:
                detail_payload[key] = detail[key]
    return create_human_handoff(
        session,
        run_id=run_id,
        application_id=application_id,
        reason_code=reason_code,
        company=job.company if job is not None else "",
        role=job.title if job is not None else "",
        question=question,
        action_required=action_required,
        detail_json=detail_payload,
    )


__all__ = ["record_handoff"]
