"""Safe UAA agent tool layer for the AI supervisor.

The supervisor NEVER receives raw browser tools (no click(selector), no
evaluate(js), no goto(arbitrary_url), no set_input_files, no submit()).
Every operation here is a business-level action wrapping an EXISTING UAA
service — queue import, the review-only observation path, the official
intervention resolve service, and the review snapshot store.

There is deliberately NO submit tool and NO authorization tool in this
module. The existing UAA submit interlock remains authoritative and is not
reachable from here.

Dependency injection: ``prepare_fn`` replaces the browser-backed prepare
path in tests (hermetic — no real ATS traffic, no real browser needed).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker

from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    list_application_jobs,
    update_application_status,
)
from universal_auto_applier.submission.models import SubmissionSnapshot
from universal_auto_applier.supervisor.models import InterventionView

logger = logging.getLogger("universal_auto_applier.supervisor.tools")

# Snapshot field statuses that mean "not resolved" (same set the submission
# snapshot model uses).
_UNRESOLVED_FIELD_STATUSES = frozenset(
    {"intervention_needed", "validation_error", "failed", "blocked", "unfilled", "unsupported"}
)


@dataclass
class PrepareOutcome:
    """Result of one review-only prepare/retry attempt."""

    application_id: str
    snapshot: SubmissionSnapshot | None = None
    error: str | None = None

    @property
    def blocked(self) -> bool:
        return self.snapshot is None


@dataclass
class ImportOutcome:
    """Structured result of a queue import (no raw lines — they may carry PII)."""

    total_lines: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


class ApplicationNotFoundError(LookupError):
    pass


class SupervisorTools:
    """Typed, safe operations the supervisor planner can drive.

    Each public method corresponds to one business action. Nothing here can
    click a submit control, create an authorization, or disable the
    interlock.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        *,
        context_factory: Any | None = None,
        prepare_fn: Callable[[str], PrepareOutcome] | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._context_factory = context_factory
        self._prepare_fn = prepare_fn

    # ------------------------------------------------------------------
    # Queue import (JobHunter bridge)
    # ------------------------------------------------------------------

    def import_queue(self, path: Path) -> ImportOutcome:
        """Import a JobHunter application_queue.jsonl through the existing
        importer. Returns structured counts; raw lines are never surfaced
        (they may carry candidate PII)."""
        from universal_auto_applier.application_queue.importer import import_queue_file

        result = import_queue_file(path, self._session_factory)
        return ImportOutcome(
            total_lines=result.total_lines,
            imported=result.imported,
            skipped=result.skipped,
            errors=[{"line_number": e.line_number, "error": e.error} for e in result.errors],
        )

    # ------------------------------------------------------------------
    # Listing / status
    # ------------------------------------------------------------------

    def list_applications(self) -> list[dict[str, str]]:
        """Minimal structured metadata — no candidate PII dumped."""
        with session_scope(self._session_factory) as session:
            jobs = list_application_jobs(session)
            return [
                {
                    "application_id": job.application_id,
                    "company": job.company,
                    "title": job.title,
                    "platform": str(job.platform),
                    "status": str(job.status),
                }
                for job in jobs
            ]

    def get_job(self, application_id: str) -> ApplicationJob | None:
        with session_scope(self._session_factory) as session:
            return get_application_job(session, application_id)

    def get_application_status(self, application_id: str) -> dict[str, Any]:
        """Machine-readable status for one application."""
        job = self.get_job(application_id)
        if job is None:
            raise ApplicationNotFoundError(application_id)
        pending = self.get_interventions(application_id)
        snapshot = self.load_review_snapshot(application_id)
        return {
            "application_id": application_id,
            "company": job.company,
            "title": job.title,
            "platform": str(job.platform),
            "job_status": str(job.status),
            "pending_intervention_count": len(pending),
            "snapshot_present": snapshot is not None,
            "unresolved_required_field_count": (
                snapshot.unresolved_required_field_count if snapshot is not None else None
            ),
            "snapshot_hash": snapshot.snapshot_hash if snapshot is not None else None,
        }

    # ------------------------------------------------------------------
    # Review-only prepare / retry (the ONLY browser path, review-mode)
    # ------------------------------------------------------------------

    def prepare_application(self, application_id: str) -> PrepareOutcome:
        """Run the existing REVIEW-ONLY observation workflow for one job.

        Uses the canonical ``observe_and_persist_snapshot`` path: interlock
        installed before navigation, safe detail→form navigation, fill,
        snapshot persisted UNAPPROVED. The submit interlock and every UAA
        safety gate remain in force; the supervisor cannot bypass them.
        """
        return self._prepare(application_id)

    def retry_application(self, application_id: str) -> PrepareOutcome:
        """Re-run the same review-only preparation path (bounded by the
        service layer). No parallel browser runner exists."""
        return self._prepare(application_id)

    def _prepare(self, application_id: str) -> PrepareOutcome:
        if self._prepare_fn is not None:
            return self._prepare_fn(application_id)
        return self._default_prepare(application_id)

    def _default_prepare(self, application_id: str) -> PrepareOutcome:
        """Browser-backed prepare via SubmissionExecutionService (review only)."""
        from universal_auto_applier.submission.execution_service import (
            PlaywrightContextFactory,
            SubmissionExecutionService,
        )

        context_factory = self._context_factory
        if context_factory is None:
            context_factory = PlaywrightContextFactory(
                settings=self._settings,
                profile_dir=self._settings.browser_profile_dir,
                headless=self._settings.browser_headless,
                channel=self._settings.browser_channel,
            )
        service = SubmissionExecutionService(
            self._settings,
            self._session_factory,
            context_factory=context_factory,
        )
        try:
            snapshot = service.observe_and_persist_snapshot(application_id=application_id)
        except Exception as exc:  # noqa: BLE001 — surface as a blocked outcome
            logger.exception("[%s] supervisor prepare failed", application_id[:12])
            return PrepareOutcome(application_id=application_id, snapshot=None, error=str(exc))
        return PrepareOutcome(
            application_id=application_id,
            snapshot=snapshot,
            error=None if snapshot is not None else "observation did not reach an application form",
        )

    # ------------------------------------------------------------------
    # Interventions
    # ------------------------------------------------------------------

    def sync_interventions_from_snapshot(self, application_id: str) -> int:
        """Persist FIELD_ANSWER interventions for unresolved snapshot fields.

        The observe path builds the snapshot but does not persist field
        interventions; the supervisor syncs them through the OFFICIAL
        ``create_intervention`` store (deterministic ids — idempotent, no
        duplicates). Returns the number of now-pending field interventions.
        """
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention

        snapshot = self.load_review_snapshot(application_id)
        if snapshot is None:
            return 0
        created = 0
        with session_scope(self._session_factory) as session:
            for f in snapshot.fields:
                if f.status not in _UNRESOLVED_FIELD_STATUSES:
                    continue
                create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question=f.label,
                    suggested_answer=None,
                    field_selector=f.field_token,
                    llm_metadata={
                        "field_label": f.label,
                        "field_token": f.field_token,
                        "field_type": f.field_type,
                        "unresolved_reason": f.status,
                        "required": f.required,
                    },
                )
                created += 1
        return created

    def get_interventions(self, application_id: str) -> list[InterventionView]:
        """Structured, sanitized pending interventions for decisions."""
        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(self._session_factory) as session:
            rows = list_pending_interventions(session, application_id)
            return [self._intervention_view(row) for row in rows]

    @staticmethod
    def _intervention_view(row: Any) -> InterventionView:
        meta: dict[str, Any] = dict(row.llm_metadata or {})
        return InterventionView(
            intervention_id=row.intervention_id,
            kind=str(row.kind),
            question=row.question,
            field_label=meta.get("field_label"),
            field_type=meta.get("field_type"),
            options=list(row.options or []),
            reason=meta.get("unresolved_reason"),
            required=bool(meta.get("required", True)),
            suggested_answer=row.suggested_answer,
            confidence=row.confidence,
            risk_level=meta.get("risk_level"),
            category=meta.get("category"),
        )

    def resolve_intervention(
        self,
        *,
        intervention_id: str,
        resolution: str,
        answer: str | None = None,
        file_bundle: list[dict[str, str]] | None = None,
        save_to_memory: bool = False,
    ) -> dict[str, str]:
        """Resolve through the OFFICIAL shared resolve service (never manual
        DB mutation). Relies on the corrected Phase-0 semantics."""
        from universal_auto_applier.core.statuses import InterventionStatus
        from universal_auto_applier.interventions.resolve_service import (
            parse_structured_bundle,
            resolve_with_persistence,
        )
        from universal_auto_applier.interventions.store import get_intervention

        with session_scope(self._session_factory) as session:
            existing = get_intervention(session, intervention_id)
            if existing is None:
                raise ApplicationNotFoundError(intervention_id)
            structured_bundle = parse_structured_bundle(answer, file_bundle)
            resolve_with_persistence(
                session,
                intervention=existing,
                resolution=InterventionStatus(resolution),
                answer=answer,
                structured_bundle=structured_bundle,
                save_to_memory=save_to_memory,
            )
            session.commit()
        return {
            "status": "resolved",
            "intervention_id": intervention_id,
            "resolution": resolution,
        }

    def memory_lookup(self, normalized_question: str) -> str | None:
        """Exact-match reusable answer memory (used by the policy engine)."""
        from universal_auto_applier.interventions.answer_memory import retrieve_answer

        with session_scope(self._session_factory) as session:
            memory = retrieve_answer(session, normalized_question)
            return memory.answer if memory is not None else None

    # ------------------------------------------------------------------
    # Review packet
    # ------------------------------------------------------------------

    def load_review_snapshot(self, application_id: str) -> SubmissionSnapshot | None:
        """Load the persisted (unapproved) live review snapshot."""
        from universal_auto_applier.submission.store import get_active_approval

        with session_scope(self._session_factory) as session:
            approval = get_active_approval(session, application_id)
            if approval is None or not approval.snapshot_json:
                return None
            try:
                return SubmissionSnapshot.model_validate(approval.snapshot_json)
            except Exception:  # noqa: BLE001
                return None

    def get_review_packet(self, application_id: str) -> dict[str, Any] | None:
        """Canonical sanitized review packet (same plan hash as wq8-review-packet)."""
        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )

        job = self.get_job(application_id)
        snapshot = self.load_review_snapshot(application_id)
        if job is None or snapshot is None:
            return None
        plan = build_review_plan(
            application_id=application_id,
            company=job.company,
            job_title=job.title,
            application_url=snapshot.application_url,
            fields=[
                {
                    "field_token": f.field_token,
                    "label": f.label,
                    "field_type": f.field_type,
                    "status": f.status,
                    "required": f.required,
                }
                for f in snapshot.fields
            ],
            documents=[
                {"document_kind": d.document_kind, "content_hash": d.content_hash}
                for d in snapshot.documents
            ],
            submit_control_text=snapshot.submit_control.text if snapshot.submit_control else "",
            submit_control_selector=snapshot.submit_control.selector
            if snapshot.submit_control
            else "",
            submit_control_frame_url=snapshot.submit_control.frame_url
            if snapshot.submit_control
            else "",
            pending_intervention_count=snapshot.pending_intervention_count,
        )
        return {
            "application_id": application_id,
            "company": job.company,
            "title": job.title,
            "application_url": snapshot.application_url,
            "review_plan_hash": compute_review_plan_hash(plan),
            "pending_intervention_count": snapshot.pending_intervention_count,
            "unresolved_required_field_count": snapshot.unresolved_required_field_count,
            "documents": [d.document_kind for d in snapshot.documents],
        }

    # ------------------------------------------------------------------
    # Job-level actions
    # ------------------------------------------------------------------

    def mark_review_ready(self, application_id: str) -> bool:
        """Transition the job to REVIEW_READY via the repository (guarded by
        the allowed-transitions map). Returns False when not allowed."""
        return self._transition(application_id, ApplicationStatus.REVIEW_READY)

    def skip_application(self, application_id: str) -> bool:
        """Persist a structured skip: job status SKIPPED when the transition
        is allowed; the supervisor state row records the skip regardless."""
        return self._transition(application_id, ApplicationStatus.SKIPPED)

    def _transition(self, application_id: str, target: ApplicationStatus) -> bool:
        try:
            with session_scope(self._session_factory) as session:
                row = update_application_status(session, application_id, target)
                return row is not None
        except ValueError:
            # Illegal transition from the current status — the job-level
            # lifecycle stays untouched; the supervisor state still records
            # the outcome.
            return False

    def candidate_fact_keys(self, job: ApplicationJob) -> list[str]:
        """Flattened candidate-profile KEY names (never values) for the
        Class-C mapping-defect check."""
        profile = cast(dict[str, Any], job.metadata.get("candidate_profile") or {})
        keys: list[str] = []

        def _walk(node: dict[str, Any], prefix: str = "") -> None:
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    _walk(cast(dict[str, Any], value), path)
                else:
                    keys.append(path)

        _walk(profile)
        return keys

    def is_siemens(self, job: ApplicationJob) -> bool:
        """Siemens jobs are ALWAYS skipped — dedicated workflow, never the
        supervisor."""
        if str(job.platform).lower() == str(Platform.SIEMENS):
            return True
        # URL-based fallback (same host patterns as the adapter registry).
        try:
            from universal_auto_applier.adapters.registry import detect_platform

            return detect_platform(job.url) is Platform.SIEMENS
        except Exception:  # noqa: BLE001
            return False


__all__ = [
    "ApplicationNotFoundError",
    "ImportOutcome",
    "PrepareOutcome",
    "SupervisorTools",
]
