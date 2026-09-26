"""Persist field interventions discovered by live review observation."""

from __future__ import annotations

from typing import Any

from universal_auto_applier.core.statuses import InterventionKind
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.submission.models import SubmissionSnapshot

_UNRESOLVED_FIELD_STATUSES = frozenset(
    {"intervention_needed", "validation_error", "failed", "blocked", "unfilled", "unsupported"}
)


def sync_field_interventions_from_snapshot(
    session_factory: Any,
    *,
    application_id: str,
    snapshot: SubmissionSnapshot | None,
) -> int:
    """Create idempotent FIELD_ANSWER interventions for unresolved fields.

    The submission observer persists the snapshot but intentionally leaves
    intervention persistence to its caller. Both the pipeline worker and the
    supervisor use this store-backed bridge so each unresolved field keeps its
    own stable field token and recovery context.
    """
    if snapshot is None:
        return 0

    created = 0
    with session_scope(session_factory) as session:
        for field in snapshot.fields:
            if field.status not in _UNRESOLVED_FIELD_STATUSES:
                continue
            create_intervention(
                session,
                application_id=application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question=field.label,
                suggested_answer=None,
                field_selector=field.field_token,
                llm_metadata={
                    "field_label": field.label,
                    "field_token": field.field_token,
                    "field_type": field.field_type,
                    "unresolved_reason": field.status,
                    "required": field.required,
                },
            )
            created += 1
    return created


__all__ = ["sync_field_interventions_from_snapshot"]
