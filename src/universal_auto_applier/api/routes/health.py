"""Health endpoints.

    GET /api/health         - lightweight status, skips Chromium launch
    GET /api/health/detail  - includes a real Chromium smoke check

The lightweight endpoint is safe to poll frequently. The detailed endpoint
matches the bootstrap technical verification gate (``TECHNICAL_BASELINE.md``
point 2: "The local API starts and responds to /api/health").
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from universal_auto_applier.core.models import HealthReport
from universal_auto_applier.services.health_service import make_health_report

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthReport)
def health(request: Request) -> HealthReport:
    """Lightweight health check (no browser launch)."""
    return make_health_report(request.app, skip_browser=True)


@router.get("/health/detail", response_model=HealthReport)
def health_detail(request: Request) -> HealthReport:
    """Detailed health check that actually launches Chromium once."""
    return make_health_report(request.app, skip_browser=False)
