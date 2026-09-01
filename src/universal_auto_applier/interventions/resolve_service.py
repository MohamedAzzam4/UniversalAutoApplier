"""Shared intervention-resolution service.

Single home for the resolution persistence semantics (supervisor V0
Phase-0 correction). Both the ``POST /api/interventions/{id}/resolve``
endpoint and the supervisor tool layer call these functions — there is no
second implementation and no manual DB mutation anywhere else.

Semantics (decoupled):

1. Accepting resolutions (``approved``/``edited``/``resolved``) ALWAYS
   persist the supplied answer to the job's ``metadata.form_answers`` —
   job-specific persistence does not depend on ``save_to_memory``.
2. ``save_to_memory=True`` additionally stores a REUSABLE scalar answer in
   global AnswerMemory. File bundles are NEVER written to AnswerMemory
   (global memory does not support structured/scoped document bundles yet;
   a first-file-only entry would be lossy).
3. Rejecting resolutions (``skipped``/``blocked``) never persist supplied
   data.
"""

from __future__ import annotations

from typing import Any, cast

from universal_auto_applier.core.models import DocumentBundleEntry, Intervention
from universal_auto_applier.core.statuses import InterventionStatus

ACCEPTING_RESOLUTIONS: frozenset[InterventionStatus] = frozenset(
    {
        InterventionStatus.APPROVED,
        InterventionStatus.EDITED,
        InterventionStatus.RESOLVED,
    }
)


def parse_structured_bundle(
    answer: Any,
    file_bundle: list[dict[str, str]] | None,
) -> list[dict[str, str]] | None:
    """Normalize a resolve request into a structured file bundle, if any.

    The bundle can arrive via ``file_bundle`` (preferred typed form) or as
    a structured ``answer`` (dict with files/path keys, or a list of
    {path, kind} dicts / plain path strings). Scalar text answers return
    ``None``.
    """
    if file_bundle is not None:
        return [{"path": f["path"], "kind": f.get("kind", "unknown")} for f in file_bundle]

    if isinstance(answer, dict) and answer:
        data = cast(dict[str, Any], answer)
        if not any(k in data for k in ("files", "paths", "path")):
            return None
        entries: list[DocumentBundleEntry] = []
        path_val = data.get("path")
        if isinstance(path_val, str) and path_val.strip():
            p = str(path_val).strip()
            kind_raw = data.get("kind")
            k = (
                str(kind_raw).strip()
                if isinstance(kind_raw, str) and str(kind_raw).strip()
                else "unknown"
            )
            entries.append(DocumentBundleEntry(path=p, kind=k))
        elif isinstance(data.get("files"), list):
            files_val = cast(list[Any], data["files"])
            for item in files_val:
                if isinstance(item, dict) and "path" in cast(dict[str, Any], item):
                    d = cast(dict[str, Any], item)
                    p_raw = d["path"]
                    p = str(p_raw).strip() if isinstance(p_raw, str) else ""
                    if p:
                        k_raw = d.get("kind")
                        k = (
                            str(k_raw).strip()
                            if isinstance(k_raw, str) and str(k_raw).strip()
                            else "unknown"
                        )
                        entries.append(DocumentBundleEntry(path=p, kind=k))
                elif isinstance(item, str) and item.strip():
                    entries.append(DocumentBundleEntry(path=item.strip(), kind="unknown"))
        return [{"path": e.path, "kind": e.kind} for e in entries] if entries else None

    if isinstance(answer, list) and answer:
        lst = cast(list[Any], answer)
        bundle_like = all(
            (isinstance(item, dict) and "path" in cast(dict[str, Any], item))
            or isinstance(item, str)
            for item in lst
        )
        if not bundle_like:
            return None
        result: list[dict[str, str]] = []
        for item in lst:
            if isinstance(item, dict) and "path" in cast(dict[str, Any], item):
                d = cast(dict[str, Any], item)
                p_raw = d["path"]
                p = str(p_raw).strip() if isinstance(p_raw, str) else ""
                if p:
                    k_raw = d.get("kind")
                    k = (
                        str(k_raw).strip()
                        if isinstance(k_raw, str) and str(k_raw).strip()
                        else "unknown"
                    )
                    result.append({"path": p, "kind": k})
            elif isinstance(item, str) and item.strip():
                result.append({"path": item.strip(), "kind": "unknown"})
        return result or None

    return None


def audit_answer_for(answer: Any, structured_bundle: list[dict[str, str]] | None) -> str | None:
    """Return the scalar audit string stored on the intervention row.

    For bundles only the first path is kept as the audit trail — the
    canonical structured bundle lives in the job's ``form_answers``.
    """
    if structured_bundle:
        return structured_bundle[0]["path"]
    if isinstance(answer, str):
        return answer
    if answer is not None:
        return str(answer)
    return None


def resolve_with_persistence(
    session: Any,
    *,
    intervention: Intervention,
    resolution: InterventionStatus,
    answer: Any,
    structured_bundle: list[dict[str, str]] | None,
    save_to_memory: bool,
) -> None:
    """Resolve an intervention and apply the persistence semantics above.

    ``session`` is an open SQLAlchemy session; the caller owns the
    commit/rollback (``session_scope`` or the endpoint session).
    """
    from universal_auto_applier.interventions.answer_memory import store_answer
    from universal_auto_applier.interventions.store import resolve_intervention
    from universal_auto_applier.persistence.job_repository import (
        get_application_job,
        upsert_application_job,
    )

    has_bundle = structured_bundle is not None and len(structured_bundle) > 0
    has_scalar = isinstance(answer, str) and answer.strip()
    accepting = resolution in ACCEPTING_RESOLUTIONS

    audit = audit_answer_for(answer, structured_bundle)
    resolve_intervention(
        session,
        intervention.intervention_id,
        resolution=resolution,
        answer=audit,
    )

    field_label: str | None = None
    if intervention.llm_metadata:
        field_label = intervention.llm_metadata.get("field_label")
    answer_key = field_label if field_label else intervention.question

    if accepting and (has_bundle or has_scalar):
        job = get_application_job(session, intervention.application_id)
        if job is not None:
            form_answers = dict(job.metadata.get("form_answers", {}) or {})
            if has_bundle:
                # Canonical structured bundle — never stringified.
                form_answers[answer_key] = {"files": structured_bundle}
            else:
                form_answers[answer_key] = answer
            job.metadata["form_answers"] = form_answers
            upsert_application_job(session, job)

    # Global reusable memory: scalar answers only, only when explicitly
    # requested. File bundles never create a lossy first-file memory entry.
    if save_to_memory and accepting and has_scalar and not has_bundle:
        store_answer(
            session,
            question=answer_key,
            answer=answer if isinstance(answer, str) else str(answer),
            source="user_confirmed",
        )


__all__ = [
    "ACCEPTING_RESOLUTIONS",
    "audit_answer_for",
    "parse_structured_bundle",
    "resolve_with_persistence",
]
