"""AI Supervisor — agent-assisted operator mode V0.

Architecture::

    AI Supervisor (planner)
        ↓
    typed UAA tools (``supervisor.tools.SupervisorTools``)
        ↓
    existing deterministic UAA services
        ↓
    browser (review-only; submit interlock always armed)

The supervisor never receives raw browser control, never submits, never
authorizes a submission. Siemens applications are always skipped
(dedicated workflow).
"""

from universal_auto_applier.supervisor.models import (
    AnswerSource,
    InterventionView,
    OwnerPolicy,
    PlannerContext,
    ReasonCode,
    SupervisorAction,
    SupervisorDecision,
    SupervisorLimits,
    SupervisorRunSummary,
    SupervisorState,
)
from universal_auto_applier.supervisor.policy import PolicyEngine, load_owner_policies
from universal_auto_applier.supervisor.service import SupervisorService
from universal_auto_applier.supervisor.tools import SupervisorTools

__all__ = [
    "AnswerSource",
    "InterventionView",
    "OwnerPolicy",
    "PlannerContext",
    "PolicyEngine",
    "ReasonCode",
    "SupervisorAction",
    "SupervisorDecision",
    "SupervisorLimits",
    "SupervisorRunSummary",
    "SupervisorService",
    "SupervisorState",
    "SupervisorTools",
    "load_owner_policies",
]
