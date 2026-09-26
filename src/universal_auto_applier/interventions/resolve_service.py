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

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from universal_auto_applier.core.models import DocumentBundleEntry, Intervention
from universal_auto_applier.core.statuses import (
    ApplicationStatus,
    InterventionKind,
    InterventionStatus,
)

FIELD_CORRECTIONS_METADATA_KEY = "_uaa_field_corrections"

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


def field_intervention_revision(intervention: Intervention, snapshot_hash: str) -> str:
    """Hash the pending machine context and exact persisted snapshot for stale checks."""
    payload = {
        "intervention_id": intervention.intervention_id,
        "application_id": intervention.application_id,
        "kind": str(intervention.kind),
        "status": str(intervention.status),
        "question": intervention.question,
        "field_selector": intervention.field_selector,
        "options": intervention.options,
        "suggested_answer": intervention.suggested_answer,
        "confidence": intervention.confidence,
        "llm_metadata": intervention.llm_metadata or {},
        "created_at": intervention.created_at.isoformat() if intervention.created_at else "",
        "snapshot_hash": snapshot_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def correct_and_resume_field_answer(
    session: Any,
    *,
    intervention: Intervention,
    answer: str,
    expected_revision: str,
    expected_snapshot_hash: str,
) -> dict[str, Any]:
    """Persist one exact job-local correction and queue only after blockers clear.

    The caller owns the SQLAlchemy transaction. Repeating the same correction
    returns its original receipt without changing job state.
    """
    from universal_auto_applier.core.eligibility import repeat_processing_block_reason
    from universal_auto_applier.core.models import FieldOption
    from universal_auto_applier.interventions.store import (
        count_pending_interventions,
        list_pending_interventions,
        resolve_intervention,
    )
    from universal_auto_applier.persistence.job_repository import (
        get_application_job,
        set_uaa_field_corrections,
        update_application_status,
    )
    from universal_auto_applier.persistence.pipeline_run_repository import get_active_pipeline_run
    from universal_auto_applier.submission.models import SubmissionSnapshot
    from universal_auto_applier.submission.store import (
        get_latest_approval,
        get_latest_result,
        has_unconsumed_claim,
        revoke_approval,
    )

    if intervention.kind != InterventionKind.FIELD_ANSWER:
        raise ValueError("Only FIELD_ANSWER interventions accept scalar corrections")

    normalized_answer = answer.strip()
    if not normalized_answer:
        raise ValueError("Correction answer must not be blank")

    job = get_application_job(session, intervention.application_id)
    if job is None:
        raise ValueError("Intervention job no longer exists")

    correction_key = hashlib.sha256(
        f"{intervention.intervention_id}:{expected_revision}".encode()
    ).hexdigest()
    corrections_raw: Any = job.metadata.get(FIELD_CORRECTIONS_METADATA_KEY, {})
    corrections: dict[str, Any] = (
        cast(dict[str, Any], corrections_raw) if isinstance(corrections_raw, dict) else {}
    )
    previous: Any = corrections.get(correction_key)
    if isinstance(previous, dict):
        previous_record = cast(dict[str, Any], previous)
        receipt: Any = previous_record.get("receipt")
        if (
            previous_record.get("answer") == normalized_answer
            and previous_record.get("snapshot_hash") == expected_snapshot_hash
            and isinstance(receipt, dict)
        ):
            return dict(cast(dict[str, Any], receipt))
        raise ValueError("Correction revision was already used with different data")

    if str(job.status) != ApplicationStatus.NEEDS_USER_INPUT.value:
        raise ValueError(f"Correction requires needs_user_input status, found {job.status}")
    if repeat_processing_block_reason(job) is not None:
        raise ValueError("Application is blocked from repeat processing")
    pending = list_pending_interventions(session, intervention.application_id)
    unsafe_kinds = {
        InterventionKind.PREPARATION_HTTP_MUTATION_BLOCKED,
        InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN,
    }
    if any(item.kind in unsafe_kinds for item in pending):
        raise ValueError("HTTP preparation or outcome blockers require reconciliation first")
    if get_active_pipeline_run(session) is not None:
        raise ValueError("A pipeline run is active; wait until it reaches a safe stop")
    latest_result = get_latest_result(session, intervention.application_id)
    if latest_result is not None and latest_result.state == "outcome_unknown":
        raise ValueError("Submission outcome is unknown; reconcile it before correction or retry")
    if has_unconsumed_claim(session, intervention.application_id):
        raise ValueError(
            "A submission claim is unresolved; reconcile it before correction or retry"
        )
    if str(intervention.status) != InterventionStatus.PENDING.value:
        raise ValueError("Intervention is no longer pending")

    latest_approval = get_latest_approval(session, intervention.application_id)
    if latest_approval is None or not latest_approval.snapshot_json:
        raise ValueError("No persisted snapshot is available for this intervention")
    if latest_approval.snapshot_hash != expected_snapshot_hash:
        raise ValueError("The persisted snapshot changed; reload the intervention")
    if (
        field_intervention_revision(intervention, latest_approval.snapshot_hash)
        != expected_revision
    ):
        raise ValueError("The intervention changed; reload the correction form")

    snapshot = SubmissionSnapshot.model_validate(latest_approval.snapshot_json)
    metadata = intervention.llm_metadata or {}
    if metadata.get("snapshot_hash") != snapshot.snapshot_hash:
        raise ValueError("The intervention belongs to an older snapshot")
    field_type = str(metadata.get("field_type", "")).lower()
    if field_type not in {
        "text",
        "textarea",
        "select",
        "radio",
        "checkbox",
        "email",
        "phone",
        "number",
        "date",
    }:
        raise ValueError("This intervention is not a supported scalar field")

    field_token = metadata.get("field_token")
    source_field_token = metadata.get("source_field_token")
    step_identity = metadata.get("step_identity")
    progress_fingerprint = metadata.get("form_progress_fingerprint")
    if not all(
        isinstance(value, str) and value
        for value in (field_token, source_field_token, step_identity)
    ):
        raise ValueError("The intervention lacks a complete field identity")
    matching_fields = [
        field
        for field in snapshot.fields
        if field.field_token == field_token
        and field.source_field_token == source_field_token
        and field.step_identity == step_identity
        and field.status
        in {
            "intervention_needed",
            "validation_error",
            "failed",
            "blocked",
            "unfilled",
            "unsupported",
        }
    ]
    if len(matching_fields) != 1:
        raise ValueError("The field identity is missing or ambiguous in the persisted snapshot")

    options = [FieldOption(value=value, label=value) for value in intervention.options]
    if field_type in {"select", "radio"} and not options:
        raise ValueError("Cannot correct an option field without its current option set")
    if field_type in {"select", "radio", "checkbox", "number", "date"}:
        from universal_auto_applier.form_engine.live_executor import validate_typed_answer

        valid, reason = validate_typed_answer(field_type, normalized_answer, options)
        if not valid:
            raise ValueError(f"Correction is invalid for this field: {reason}")

    resolve_intervention(
        session,
        intervention.intervention_id,
        resolution=InterventionStatus.EDITED,
        answer=normalized_answer,
    )
    correction_id = correction_key[:32]
    pending_count = count_pending_interventions(session, intervention.application_id)
    resume_status = "queued" if pending_count == 0 else "awaiting_interventions"
    target_field = matching_fields[0]
    correction = {
        "application_id": intervention.application_id,
        "intervention_id": intervention.intervention_id,
        "revision": expected_revision,
        "snapshot_hash": expected_snapshot_hash,
        "answer": normalized_answer,
        "source": "owner_supplied",
        "saved_at": datetime.now(UTC).isoformat(),
        "field_identity": {
            "field_token": target_field.field_token,
            "source_field_token": target_field.source_field_token,
            "step_identity": target_field.step_identity,
            "form_progress_fingerprint": progress_fingerprint,
        },
        "resume_status": resume_status,
    }
    receipt = {
        "application_id": intervention.application_id,
        "intervention_id": intervention.intervention_id,
        "correction_id": correction_id,
        "status": "resume_queued" if pending_count == 0 else "saved_waiting_for_interventions",
        "resume_status": resume_status,
        "pending_intervention_count": pending_count,
    }
    correction["receipt"] = receipt
    corrections[correction_key] = correction
    stored = set_uaa_field_corrections(
        session,
        intervention.application_id,
        cast(dict[str, object], corrections),
    )
    if stored is None:
        raise ValueError("Intervention job no longer exists")

    # Only the current job's exact snapshot approval is affected. Revoke it
    # before queueing, in the same transaction as the correction and resolve.
    if latest_approval.consumed_at is None and latest_approval.revoked_at is None:
        revoke_approval(session, latest_approval.approval_id)
    if pending_count == 0:
        update_application_status(
            session,
            intervention.application_id,
            ApplicationStatus.QUEUED,
        )
    return receipt


__all__ = [
    "ACCEPTING_RESOLUTIONS",
    "audit_answer_for",
    "correct_and_resume_field_answer",
    "field_intervention_revision",
    "parse_structured_bundle",
    "resolve_with_persistence",
]
