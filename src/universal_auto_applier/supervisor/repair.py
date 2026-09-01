"""Repair tickets — sanitized implementation-defect reports.

The supervisor never rewrites production code while an application attempt
is running. Suspected defects (e.g. a field the candidate profile could
answer but the mapper reports unresolved) become structured tickets that a
human — or a later coding agent — can act on.

Never contains: full candidate PII, raw CV contents, passwords, cookies,
session tokens.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from universal_auto_applier.persistence.models import RepairTicketRow
from universal_auto_applier.supervisor.models import InterventionView
from universal_auto_applier.supervisor.store import create_repair_ticket


def record_repair_ticket(
    session: Session,
    *,
    run_id: str,
    application_id: str,
    reason_code: str,
    view: InterventionView | None = None,
    ats_family: str = "",
    page_fingerprint: str = "",
    expected_source: str = "",
    actual_failure: str = "",
    selector_metadata: dict[str, Any] | None = None,
    retry_history: list[dict[str, Any]] | None = None,
    suggested_reproduction: str = "",
) -> RepairTicketRow:
    """Persist a sanitized repair ticket."""
    field_label = view.field_label if view is not None else ""
    field_type = view.field_type if view is not None else ""
    if not actual_failure and view is not None:
        actual_failure = view.reason or "mapper reported the field unresolved"
    if not expected_source and view is not None:
        expected_source = "candidate_profile"
    return create_repair_ticket(
        session,
        run_id=run_id,
        application_id=application_id,
        reason_code=reason_code,
        ats_family=ats_family,
        page_fingerprint=page_fingerprint,
        field_label=field_label or "",
        field_type=field_type or "",
        expected_source=expected_source,
        actual_failure=actual_failure,
        selector_metadata=selector_metadata or {},
        retry_history=retry_history or [],
        suggested_reproduction=suggested_reproduction,
    )


__all__ = ["record_repair_ticket"]
