"""Supervisor planner abstraction.

``SupervisorPlanner.decide(context) -> SupervisorDecision`` is the single
decision interface. Two implementations:

- :class:`DeterministicPlanner` — policy-driven, no LLM; used for tests and
  as the conservative default.
- :class:`OpenAICompatiblePlanner` — provider-neutral model-backed planner
  (OpenAI-compatible chat-completions endpoint), configured only from
  environment variables; never committed, never required for tests.

ANY model output must validate into :class:`SupervisorDecision`; invalid
output fails closed to ``REQUEST_HUMAN``. Raw model-generated commands are
never executed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from pydantic import ValidationError

from universal_auto_applier.supervisor.models import (
    InterventionView,
    PlannerContext,
    ReasonCode,
    SupervisorAction,
    SupervisorDecision,
)
from universal_auto_applier.supervisor.policy import PolicyClassification, PolicyEngine

logger = logging.getLogger("universal_auto_applier.supervisor.planner")


def fail_closed_decision(application_id: str, rationale: str) -> SupervisorDecision:
    """The fail-closed decision for invalid/unsafe planner output."""
    return SupervisorDecision(
        action=SupervisorAction.REQUEST_HUMAN,
        application_id=application_id,
        reason_code=ReasonCode.UNKNOWN_FAILURE,
        rationale=rationale,
        confidence=0.0,
    )


class SupervisorPlanner(Protocol):
    """Decision interface. Implementations must be side-effect free."""

    def decide(self, context: PlannerContext) -> SupervisorDecision: ...


class DeterministicPlanner:
    """Policy-driven planner (no LLM).

    Selection order is safety-first: if any pending intervention classifies
    as Class D (hard blocker), B (human required) or C (likely defect), the
    planner acts on the first such intervention (handoff / repair ticket /
    stop). Only when every pending intervention is auto-resolvable does it
    resolve the first one.
    """

    def __init__(self, policy_engine: PolicyEngine) -> None:
        self._policy = policy_engine

    def decide(self, context: PlannerContext) -> SupervisorDecision:
        for view in context.interventions:
            classification = self._policy.classify_intervention(
                view, candidate_fact_keys=context.candidate_fact_keys
            )
            if classification.decision_class == "D":
                return self._hard_blocker_decision(context, view, classification)
            if classification.decision_class == "B":
                return self._human_decision(context, view, classification)
            if classification.decision_class == "C":
                return SupervisorDecision(
                    action=SupervisorAction.CREATE_REPAIR_TICKET,
                    application_id=context.application_id,
                    intervention_id=view.intervention_id,
                    reason_code=classification.reason_code,
                    rationale=(
                        f"Candidate fact likely exists for field "
                        f"{view.field_label or view.question!r} but the mapper "
                        f"reported it unresolved — likely UAA mapping defect."
                    ),
                    confidence=0.8,
                )
        # All pending interventions (if any) are auto-resolvable.
        for view in context.interventions:
            classification = self._policy.classify_intervention(
                view, candidate_fact_keys=context.candidate_fact_keys
            )
            if classification.auto_resolvable:
                return SupervisorDecision(
                    action=SupervisorAction.RESOLVE_INTERVENTION,
                    application_id=context.application_id,
                    intervention_id=view.intervention_id,
                    reason_code=classification.reason_code,
                    rationale=(
                        f"Exact trusted source for "
                        f"{view.field_label or view.question!r}: "
                        f"{classification.answer_source.value if classification.answer_source else 'unknown'}"
                        + (
                            f" (policy {classification.policy_id})"
                            if classification.policy_id
                            else ""
                        )
                    ),
                    confidence=0.95,
                    answer=classification.answer,
                    answer_source=classification.answer_source,
                    save_to_memory=False,
                )
        # No interventions left to act on — ask for one more bounded retry
        # if the run still has budget; otherwise stop for a human.
        if context.prepare_attempts < 1:
            return SupervisorDecision(
                action=SupervisorAction.RETRY_APPLICATION,
                application_id=context.application_id,
                reason_code=ReasonCode.RETRYABLE_EXECUTION_FAILURE,
                rationale="No pending interventions; one bounded re-prepare.",
                confidence=0.5,
                retry_allowed=True,
            )
        return fail_closed_decision(
            context.application_id,
            "No pending interventions and no retry budget left — escalate.",
        )

    @staticmethod
    def _hard_blocker_decision(
        context: PlannerContext,
        view: InterventionView,
        classification: PolicyClassification,
    ) -> SupervisorDecision:
        return SupervisorDecision(
            action=SupervisorAction.REQUEST_HUMAN,
            application_id=context.application_id,
            intervention_id=view.intervention_id,
            reason_code=classification.reason_code,
            rationale=f"Hard blocker ({classification.reason_code.value}) — no retry.",
            confidence=1.0,
        )

    @staticmethod
    def _human_decision(
        context: PlannerContext,
        view: InterventionView,
        classification: PolicyClassification,
    ) -> SupervisorDecision:
        return SupervisorDecision(
            action=SupervisorAction.REQUEST_HUMAN,
            application_id=context.application_id,
            intervention_id=view.intervention_id,
            reason_code=classification.reason_code,
            rationale=(
                f"No owner-approved policy or trusted source for "
                f"{view.field_label or view.question!r} — human decision "
                f"required (no fabrication)."
            ),
            confidence=1.0,
        )


class OpenAICompatiblePlanner:
    """Provider-neutral model-backed planner (OpenAI chat-completions API).

    Configured ONLY from environment variables:

    - ``UAA_SUPERVISOR_MODEL_BASE_URL`` (e.g. an OpenRouter/OpenAI URL)
    - ``UAA_SUPERVISOR_MODEL_NAME``
    - ``UAA_SUPERVISOR_MODEL_API_KEY``

    The API key is read from the environment at call time and never
    logged, stored, or committed. Any transport error, malformed JSON, or
    schema-invalid decision fails closed to ``REQUEST_HUMAN``.
    """

    SYSTEM_PROMPT = (
        "You are the AI supervisor of a job-application system. You receive a "
        "JSON context describing one application and its pending interventions. "
        "Reply with ONE JSON object with exactly these keys: action "
        "(one of resolve_intervention, retry_application, request_human, "
        "create_repair_ticket, mark_review_ready, skip_application, stop), "
        "intervention_id (string or null), reason_code, rationale, confidence "
        "(0..1), answer (string or null), answer_source (one of candidate_fact, "
        "owner_policy, job_specific, answer_memory, human, model_inference, or "
        "null), save_to_memory (bool). NEVER invent answers to questions about "
        "salary, legal declarations, demographics, consent, work authorization, "
        "or availability — use request_human for those. Never attempt to "
        "submit an application; no such action exists."
    )

    def __init__(
        self,
        policy_engine: PolicyEngine,
        *,
        base_url: str | None = None,
        model_name: str | None = None,
        api_key_env: str = "UAA_SUPERVISOR_MODEL_API_KEY",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._policy = policy_engine
        self._base_url = base_url or os.environ.get("UAA_SUPERVISOR_MODEL_BASE_URL", "")
        self._model_name = model_name or os.environ.get("UAA_SUPERVISOR_MODEL_NAME", "")
        self._api_key_env = api_key_env
        self._timeout_seconds = timeout_seconds

    def decide(self, context: PlannerContext) -> SupervisorDecision:
        try:
            raw = self._call_model(context)
        except Exception as exc:  # noqa: BLE001 — fail closed on any transport error
            logger.warning("supervisor model call failed: %s — failing closed", exc)
            return fail_closed_decision(
                context.application_id, f"model transport error: {type(exc).__name__}"
            )
        return self.parse_decision(raw, context.application_id)

    def parse_decision(self, raw: str, application_id: str) -> SupervisorDecision:
        """Parse model output through the typed schema; fail closed."""
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("model output is not a JSON object")
            return SupervisorDecision.model_validate(payload | {"application_id": application_id})
        except (ValueError, ValidationError) as exc:
            logger.warning("invalid supervisor model output: %s", exc)
            return fail_closed_decision(
                application_id, f"invalid model output: {type(exc).__name__}"
            )

    def _call_model(self, context: PlannerContext) -> str:
        import urllib.request

        api_key = os.environ.get(self._api_key_env, "")
        if not self._base_url or not self._model_name or not api_key:
            raise RuntimeError("supervisor model is not configured")
        body = json.dumps(
            {
                "model": self._model_name,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": context.model_dump_json()},
                ],
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]


__all__ = [
    "DeterministicPlanner",
    "OpenAICompatiblePlanner",
    "SupervisorPlanner",
    "fail_closed_decision",
]
