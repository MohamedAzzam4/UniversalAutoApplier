"""Supervisor loop — bounded orchestration over the safe tool layer.

Conceptual loop per application:

    inspect status
    if Siemens: skip (DEDICATED_SIEMENS_WORKFLOW — never prepared)
    if review_ready: queue for owner review; stop
    prepare (review-only)
    if blocked: human handoff; stop (no retry — conservative)
    if unresolved interventions: planner decides
        permitted trusted answer -> resolve -> bounded retry
        human required           -> handoff; stop
        likely code defect       -> repair ticket; stop
    bounded retries only; repeated identical failure terminates

There is NO submit transition and no submit tool: an application that
reaches REVIEW_READY stops and waits for the human owner. Every state
change is recorded as a supervisor event (audit: "why did the agent do
this?").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.supervisor.models import (
    AnswerSource,
    PlannerContext,
    ReasonCode,
    SupervisorAction,
    SupervisorDecision,
    SupervisorLimits,
    SupervisorRunSummary,
    SupervisorState,
)
from universal_auto_applier.supervisor.planner import (
    SupervisorPlanner,
    fail_closed_decision,
)
from universal_auto_applier.supervisor.policy import PolicyEngine
from universal_auto_applier.supervisor.store import (
    create_supervisor_run,
    finish_supervisor_run,
    record_supervisor_event,
    set_supervisor_application_state,
)
from universal_auto_applier.supervisor.tools import (
    SupervisorTools,
)

logger = logging.getLogger("universal_auto_applier.supervisor.service")

_TERMINAL_JOB_STATUSES = frozenset(
    {
        ApplicationStatus.APPLIED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CLOSED,
        ApplicationStatus.SKIPPED,
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.NEEDS_REVIEW,
    }
)


class SupervisorService:
    """Runs one bounded supervisor pass over the queue."""

    def __init__(
        self,
        tools: SupervisorTools,
        policy_engine: PolicyEngine,
        planner: SupervisorPlanner,
        *,
        session_factory: sessionmaker[Session],
        limits: SupervisorLimits | None = None,
    ) -> None:
        self._tools = tools
        self._policy = policy_engine
        self._planner = planner
        self._session_factory = session_factory
        self._limits = limits or SupervisorLimits()

    # ------------------------------------------------------------------
    # Run entry point
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        queue_path: str | None = None,
        application_ids: list[str] | None = None,
    ) -> SupervisorRunSummary:
        """Execute one bounded review-only supervisor run."""
        with session_scope(self._session_factory) as session:
            run_row = create_supervisor_run(session, queue_path=queue_path or "")
            run_id = run_row.run_id

        summary = self._process(run_id, queue_path=queue_path, application_ids=application_ids)

        with session_scope(self._session_factory) as session:
            finish_supervisor_run(
                session,
                run_id,
                status="completed",
                summary=dict(summary.model_dump()),
            )
        return summary

    def _process(
        self,
        run_id: str,
        *,
        queue_path: str | None,
        application_ids: list[str] | None,
    ) -> SupervisorRunSummary:
        summary = SupervisorRunSummary(run_id=run_id)
        imported_ids: list[str] | None = None
        if queue_path:
            outcome = self._tools.import_queue(Path(queue_path))
            summary.imported = outcome.imported
            imported_ids = list(outcome.imported_application_ids)

        if application_ids is not None:
            jobs = [self._tools.get_job(a) for a in application_ids]
            jobs = [j for j in jobs if j is not None]
        elif imported_ids is not None:
            # Run-scope isolation: when a queue was imported for this run,
            # only the applications (re-)imported from that queue file belong
            # to this run. This prevents unrelated pre-existing DB rows
            # (e.g. old WQ-8 jobs) from being accidentally prepared.
            jobs = [self._tools.get_job(a) for a in imported_ids]
            jobs = [j for j in jobs if j is not None]
        else:
            all_apps = {a["application_id"]: a for a in self._tools.list_applications()}
            jobs = [j for j in (self._tools.get_job(a) for a in all_apps) if j is not None]

        for job in jobs:
            try:
                self._process_application(run_id, job, summary)
            except Exception as exc:  # noqa: BLE001 — one bad application must not kill the run
                logger.exception(
                    "[%s] supervisor application processing failed", job.application_id[:12]
                )
                self._record(
                    run_id,
                    job.application_id,
                    action="stop",
                    previous_state=SupervisorState.RUNNING,
                    resulting_state=SupervisorState.FAILED,
                    reason_code=ReasonCode.UNKNOWN_FAILURE,
                    decision_source="service",
                    tool_result=f"exception: {type(exc).__name__}",
                )
                summary.failed.append(
                    {
                        "application_id": job.application_id,
                        "company": job.company,
                        "reason_code": ReasonCode.UNKNOWN_FAILURE.value,
                    }
                )
                self._set_state(
                    run_id,
                    job.application_id,
                    SupervisorState.FAILED,
                    reason_code=ReasonCode.UNKNOWN_FAILURE,
                )
        return summary

    # ------------------------------------------------------------------
    # Per-application state machine
    # ------------------------------------------------------------------

    def _process_application(
        self,
        run_id: str,
        job: ApplicationJob,
        summary: SupervisorRunSummary,
    ) -> None:
        app_id = job.application_id

        # Siemens is ALWAYS skipped — dedicated workflow, no preparation.
        if self._tools.is_siemens(job):
            self._record(
                run_id,
                app_id,
                action="skip_application",
                previous_state=SupervisorState.IMPORTED,
                resulting_state=SupervisorState.SKIPPED,
                reason_code=ReasonCode.DEDICATED_SIEMENS_WORKFLOW,
                decision_source="policy",
            )
            self._set_state(
                run_id,
                app_id,
                SupervisorState.SKIPPED,
                reason_code=ReasonCode.DEDICATED_SIEMENS_WORKFLOW,
            )
            summary.skipped_siemens += 1
            summary.skipped.append(
                {
                    "application_id": app_id,
                    "company": job.company,
                    "reason_code": ReasonCode.DEDICATED_SIEMENS_WORKFLOW.value,
                }
            )
            return

        job_status = ApplicationStatus(str(job.status))
        if job_status is ApplicationStatus.REVIEW_READY:
            self._record(
                run_id,
                app_id,
                action="mark_review_ready",
                previous_state=SupervisorState.IMPORTED,
                resulting_state=SupervisorState.REVIEW_READY,
                reason_code=ReasonCode.REVIEW_READY,
                decision_source="policy",
            )
            self._set_state(
                run_id, app_id, SupervisorState.REVIEW_READY, reason_code=ReasonCode.REVIEW_READY
            )
            summary.review_ready.append(app_id)
            return
        if job_status in _TERMINAL_JOB_STATUSES:
            state = SupervisorState.SKIPPED
            self._record(
                run_id,
                app_id,
                action="stop",
                previous_state=SupervisorState.IMPORTED,
                resulting_state=state,
                reason_code=ReasonCode.UNKNOWN_FAILURE
                if job_status is not ApplicationStatus.SKIPPED
                else ReasonCode.DEDICATED_SIEMENS_WORKFLOW,
                decision_source="policy",
                tool_result=f"job already in status {job_status.value}",
            )
            self._set_state(run_id, app_id, state, reason_code=ReasonCode.UNKNOWN_FAILURE)
            summary.skipped.append(
                {"application_id": app_id, "company": job.company, "reason_code": job_status.value}
            )
            return

        # Bounded prepare/resolve loop.
        prepare_attempts = 0
        intervention_resolutions = 0
        previous_reason: str | None = None
        same_failure_count = 0
        state = SupervisorState.IMPORTED

        while prepare_attempts < self._limits.max_application_attempts:
            self._set_state(run_id, app_id, SupervisorState.PREPARING, retry_count=prepare_attempts)
            outcome = self._tools.prepare_application(app_id)
            prepare_attempts += 1

            if outcome.blocked:
                # Conservative: an unreachable/blocked observation is a human
                # matter (CAPTCHA, login wall, moved form, cookie banner...) — NEVER
                # auto-retried, so a blocker can never loop.
                if outcome.error and "cookie_consent_blocked" in outcome.error.lower():
                    cookie_reason = ReasonCode.COOKIE_CONSENT_BLOCKED
                else:
                    cookie_reason = (
                        ReasonCode.NO_SAFE_NAVIGATION
                        if outcome.error
                        else ReasonCode.UNKNOWN_FAILURE
                    )
                self._handoff(
                    run_id,
                    job,
                    reason_code=cookie_reason,
                    question="",
                    action_required="Inspect the live application page and resume manually (observation was blocked).",
                    tool_result=outcome.error or "observation blocked",
                    resulting_state=SupervisorState.NEEDS_HUMAN,
                )
                summary.needs_human.append(
                    {
                        "application_id": app_id,
                        "company": job.company,
                        "reason_code": cookie_reason.value,
                    }
                )
                return

            snapshot = outcome.snapshot
            assert snapshot is not None
            self._tools.sync_interventions_from_snapshot(app_id)
            pending = self._tools.get_interventions(app_id)

            unresolved = snapshot.unresolved_required_field_count
            if not pending and unresolved == 0:
                # Fully prepared — stop at the review boundary.
                moved = self._tools.mark_review_ready(app_id)
                self._record(
                    run_id,
                    app_id,
                    action="mark_review_ready",
                    previous_state=SupervisorState.PREPARING,
                    resulting_state=SupervisorState.REVIEW_READY,
                    reason_code=ReasonCode.REVIEW_READY,
                    decision_source="policy",
                    tool_result="review packet ready for owner"
                    if moved
                    else "job status transition not allowed",
                    retry_count=prepare_attempts,
                )
                self._set_state(
                    run_id,
                    app_id,
                    SupervisorState.REVIEW_READY,
                    reason_code=ReasonCode.REVIEW_READY,
                    retry_count=prepare_attempts,
                )
                summary.review_ready.append(app_id)
                return

            if not pending:
                # Unresolved required fields but no pending intervention
                # (e.g. a previously resolved intervention did not fix the
                # field). Bounded retry, then escalate — never loop.
                same_failure_count += 1
                if same_failure_count > self._limits.max_same_failure_retries:
                    self._handoff(
                        run_id,
                        job,
                        reason_code=ReasonCode.UAA_EXECUTION_DEFECT,
                        question="",
                        action_required=(
                            "Required fields remain unresolved after resolution+retry — "
                            "inspect the mapping/fill behavior."
                        ),
                        tool_result=f"unresolved_required_fields={unresolved}",
                        resulting_state=SupervisorState.NEEDS_HUMAN,
                    )
                    summary.needs_human.append(
                        {
                            "application_id": app_id,
                            "company": job.company,
                            "reason_code": ReasonCode.UAA_EXECUTION_DEFECT.value,
                        }
                    )
                    return
                state = SupervisorState.RETRY_PENDING
                self._set_state(
                    run_id,
                    app_id,
                    state,
                    reason_code=ReasonCode.RETRYABLE_EXECUTION_FAILURE,
                    retry_count=prepare_attempts,
                )
                continue

            # Let the planner decide on the pending interventions.
            context = PlannerContext(
                application_id=app_id,
                company=job.company,
                title=job.title,
                platform=str(job.platform),
                job_status=str(job.status),
                supervisor_state=state,
                interventions=pending,
                unresolved_required_field_count=unresolved,
                pending_intervention_count=len(pending),
                prepare_attempts=prepare_attempts,
                intervention_resolutions=intervention_resolutions,
                last_reason_code=previous_reason,
                same_failure_count=same_failure_count,
                candidate_fact_keys=self._tools.candidate_fact_keys(job),
            )
            decision = self._planner.decide(context)
            decision = self._validate_decision(decision, pending)

            if decision.action is SupervisorAction.RESOLVE_INTERVENTION:
                if intervention_resolutions >= self._limits.max_intervention_resolutions:
                    self._handoff(
                        run_id,
                        job,
                        reason_code=ReasonCode.INTERVENTION_REQUIRED,
                        question="",
                        action_required="Intervention resolution budget exhausted — human decision required.",
                        tool_result="max_intervention_resolutions reached",
                        resulting_state=SupervisorState.NEEDS_HUMAN,
                    )
                    summary.needs_human.append(
                        {
                            "application_id": app_id,
                            "company": job.company,
                            "reason_code": ReasonCode.INTERVENTION_REQUIRED.value,
                        }
                    )
                    return
                view = next(v for v in pending if v.intervention_id == decision.intervention_id)
                resolution = "approved" if decision.answer == view.suggested_answer else "edited"
                self._tools.resolve_intervention(
                    intervention_id=str(decision.intervention_id),
                    resolution=resolution,
                    answer=decision.answer,
                    save_to_memory=(
                        decision.save_to_memory
                        and decision.answer_source is not AnswerSource.MODEL_INFERENCE
                    ),
                )
                intervention_resolutions += 1
                previous_reason = decision.reason_code.value
                self._record(
                    run_id,
                    app_id,
                    action="resolve_intervention",
                    previous_state=SupervisorState.WAITING_FOR_INTERVENTION,
                    resulting_state=SupervisorState.RETRY_PENDING,
                    reason_code=decision.reason_code,
                    decision_source=decision.answer_source.value if decision.answer_source else "",
                    confidence=decision.confidence,
                    retry_count=intervention_resolutions,
                    tool_result=f"resolution={resolution} value_redacted=true",
                    detail_json={
                        "value_redacted": True,
                        "source": decision.answer_source.value if decision.answer_source else "",
                        "policy_id": getattr(decision, "policy_id", None),
                    },
                )
                self._set_state(
                    run_id,
                    app_id,
                    SupervisorState.WAITING_FOR_INTERVENTION,
                    reason_code=decision.reason_code,
                    decision_source=decision.answer_source.value if decision.answer_source else "",
                    retry_count=intervention_resolutions,
                )
                continue

            if decision.action is SupervisorAction.REQUEST_HUMAN:
                view = next(
                    (v for v in pending if v.intervention_id == decision.intervention_id), None
                )
                self._handoff(
                    run_id,
                    job,
                    reason_code=decision.reason_code,
                    question=(view.field_label or view.question) if view is not None else "",
                    action_required="Decide the answer manually via the dashboard/CLI; the agent stopped.",
                    tool_result=decision.rationale,
                    resulting_state=SupervisorState.NEEDS_HUMAN,
                )
                summary.needs_human.append(
                    {
                        "application_id": app_id,
                        "company": job.company,
                        "reason_code": decision.reason_code.value,
                    }
                )
                return

            if decision.action is SupervisorAction.CREATE_REPAIR_TICKET:
                view = next(
                    (v for v in pending if v.intervention_id == decision.intervention_id), None
                )
                self._repair_ticket(run_id, app_id, decision.reason_code, view)
                summary.repair_needed.append(
                    {
                        "application_id": app_id,
                        "company": job.company,
                        "reason_code": decision.reason_code.value,
                    }
                )
                return

            if decision.action is SupervisorAction.SKIP_APPLICATION:
                moved = self._tools.skip_application(app_id)
                self._record(
                    run_id,
                    app_id,
                    action="skip_application",
                    previous_state=SupervisorState.PREPARING,
                    resulting_state=SupervisorState.SKIPPED,
                    reason_code=decision.reason_code,
                    decision_source="planner",
                    tool_result="skipped" if moved else "job status transition not allowed",
                    detail_json={"value_redacted": True},
                )
                self._set_state(
                    run_id, app_id, SupervisorState.SKIPPED, reason_code=decision.reason_code
                )
                summary.skipped.append(
                    {
                        "application_id": app_id,
                        "company": job.company,
                        "reason_code": decision.reason_code.value,
                    }
                )
                return

            if decision.action is SupervisorAction.MARK_REVIEW_READY:
                # Veto unless genuinely complete.
                if not pending and unresolved == 0:
                    self._tools.mark_review_ready(app_id)
                    self._set_state(
                        run_id,
                        app_id,
                        SupervisorState.REVIEW_READY,
                        reason_code=ReasonCode.REVIEW_READY,
                    )
                    summary.review_ready.append(app_id)
                    return
                decision = fail_closed_decision(
                    app_id, "mark_review_ready vetoed: unresolved work remains"
                )

            if decision.action is SupervisorAction.RETRY_APPLICATION:
                if (
                    not decision.retry_allowed
                    or prepare_attempts >= self._limits.max_application_attempts
                ):
                    decision = fail_closed_decision(app_id, "retry budget exhausted — escalating")
                else:
                    self._set_state(
                        run_id,
                        app_id,
                        SupervisorState.RETRY_PENDING,
                        reason_code=decision.reason_code,
                        retry_count=prepare_attempts,
                    )
                    continue

            # STOP or any fall-through: stop safely with a handoff.
            self._handoff(
                run_id,
                job,
                reason_code=decision.reason_code,
                question="",
                action_required="Supervisor stopped — resume manually.",
                tool_result=decision.rationale,
                resulting_state=SupervisorState.BLOCKED,
            )
            summary.needs_human.append(
                {
                    "application_id": app_id,
                    "company": job.company,
                    "reason_code": decision.reason_code.value,
                }
            )
            return

        # Attempt budget exhausted.
        self._handoff(
            run_id,
            job,
            reason_code=ReasonCode.RETRYABLE_EXECUTION_FAILURE,
            question="",
            action_required="Prepare attempt budget exhausted — human decision required.",
            tool_result=f"max_application_attempts={self._limits.max_application_attempts}",
            resulting_state=SupervisorState.FAILED,
        )
        summary.failed.append(
            {
                "application_id": app_id,
                "company": job.company,
                "reason_code": ReasonCode.RETRYABLE_EXECUTION_FAILURE.value,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_decision(
        self,
        decision: SupervisorDecision,
        pending: list[Any],
    ) -> SupervisorDecision:
        """Structural + policy validation. Any veto fails closed."""
        if not decision.is_valid_for_execution():
            return fail_closed_decision(
                decision.application_id, "structurally invalid decision — failing closed"
            )
        if decision.action is not SupervisorAction.RESOLVE_INTERVENTION:
            return decision
        view = next((v for v in pending if v.intervention_id == decision.intervention_id), None)
        candidate_keys: list[str] = []
        job = self._tools.get_job(decision.application_id)
        if job is not None:
            candidate_keys = self._tools.candidate_fact_keys(job)
        permitted = self._policy.validate_decision(
            decision,
            view,
            candidate_fact_keys=candidate_keys,
            memory_lookup=self._tools.memory_lookup,
        )
        if not permitted:
            logger.warning(
                "[%s] policy vetoed planner decision %s — failing closed",
                decision.application_id[:12],
                decision.action.value,
            )
            return fail_closed_decision(
                decision.application_id,
                f"policy vetoed {decision.action.value} — failing closed",
            )
        return decision

    def _handoff(
        self,
        run_id: str,
        job: ApplicationJob,
        *,
        reason_code: ReasonCode,
        question: str,
        action_required: str,
        tool_result: str,
        resulting_state: SupervisorState,
    ) -> None:
        app_id = job.application_id
        with session_scope(self._session_factory) as session:
            from universal_auto_applier.supervisor.handoff import record_handoff

            record_handoff(
                session,
                run_id=run_id,
                job=job,
                application_id=app_id,
                reason_code=reason_code.value,
                question=question,
                action_required=action_required,
            )
        self._record(
            run_id,
            app_id,
            action="request_human",
            previous_state=SupervisorState.PREPARING,
            resulting_state=resulting_state,
            reason_code=reason_code,
            decision_source="policy",
            tool_result=tool_result,
        )
        self._set_state(
            run_id,
            app_id,
            resulting_state,
            reason_code=reason_code,
        )

    def _repair_ticket(
        self,
        run_id: str,
        app_id: str,
        reason_code: ReasonCode,
        view: Any,
    ) -> None:
        with session_scope(self._session_factory) as session:
            from universal_auto_applier.supervisor.repair import record_repair_ticket

            record_repair_ticket(
                session,
                run_id=run_id,
                application_id=app_id,
                reason_code=reason_code.value,
                view=view,
            )
        self._record(
            run_id,
            app_id,
            action="create_repair_ticket",
            previous_state=SupervisorState.PREPARING,
            resulting_state=SupervisorState.REPAIR_NEEDED,
            reason_code=reason_code,
            decision_source="policy",
            tool_result="ticket created",
        )
        self._set_state(
            run_id,
            app_id,
            SupervisorState.REPAIR_NEEDED,
            reason_code=reason_code,
        )

    def _record(
        self,
        run_id: str,
        app_id: str,
        *,
        action: str,
        previous_state: SupervisorState | str,
        resulting_state: SupervisorState | str,
        reason_code: ReasonCode,
        decision_source: str,
        tool_result: str = "",
        confidence: float | None = None,
        retry_count: int = 0,
        detail_json: dict[str, Any] | None = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            record_supervisor_event(
                session,
                run_id=run_id,
                application_id=app_id,
                action=action,
                previous_state=str(previous_state.value)
                if isinstance(previous_state, SupervisorState)
                else str(previous_state),
                resulting_state=str(resulting_state.value)
                if isinstance(resulting_state, SupervisorState)
                else str(resulting_state),
                reason_code=reason_code.value,
                decision_source=decision_source,
                confidence=confidence,
                retry_count=retry_count,
                tool_result=tool_result,
                detail_json=detail_json or {},
            )

    def _set_state(
        self,
        run_id: str,
        app_id: str,
        state: SupervisorState,
        *,
        reason_code: ReasonCode | str = "",
        decision_source: str = "",
        retry_count: int = 0,
    ) -> None:
        code = reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
        with session_scope(self._session_factory) as session:
            set_supervisor_application_state(
                session,
                application_id=app_id,
                run_id=run_id,
                state=state.value,
                reason_code=code,
                decision_source=decision_source,
                retry_count=retry_count,
            )


__all__ = ["SupervisorService"]
