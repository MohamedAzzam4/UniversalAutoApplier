"""Core Pydantic v2 contracts.

This module deliberately keeps a *minimal* surface area for the bootstrap
phase. The full :class:`ApplicationJob`, :class:`ApplicationAttempt`,
:class:`AdapterResult`, :class:`PageObservation`, :class:`FormField`,
:class:`FieldMapping`, :class:`Intervention`, and :class:`AnswerMemory`
contracts will be added in Phase 1+. They are listed in
``DATA_CONTRACTS.md`` and must not be invented here prematurely.

What we DO define here is the :class:`HealthReport` model: the contract
returned by ``GET /api/health``. It is needed for the bootstrap technical
verification gate (``TECHNICAL_BASELINE.md`` -> Technical Verification Gate
point 2).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from universal_auto_applier import __version__
from universal_auto_applier.core.statuses import HealthState


class ComponentHealth(BaseModel):
    """Health of one capability listed in DEPLOYMENT_AND_REPO_STRATEGY.md."""

    name: str = Field(..., description="Capability name, e.g. 'api', 'store'.")
    state: HealthState
    detail: str = Field(default="", description="Optional human-readable note.")


class HealthReport(BaseModel):
    """Aggregated system health returned by ``GET /api/health``."""

    status: HealthState = Field(
        default=HealthState.READY,
        description="Top-level status. 'ready' only when all required capabilities are ready.",
    )
    version: str = Field(default=__version__)
    components: list[ComponentHealth] = Field(default_factory=list[ComponentHealth])

    def find(self, name: str) -> ComponentHealth | None:
        """Return the component with ``name`` or ``None`` if absent."""
        for component in self.components:
            if component.name == name:
                return component
        return None
