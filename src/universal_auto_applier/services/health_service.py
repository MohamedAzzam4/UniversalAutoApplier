"""Health service.

Aggregates the per-capability health states required by
``DEPLOYMENT_AND_REPO_STRATEGY.md`` -> Startup and Health Contract:

    api: ready | unavailable
    store: ready | unavailable
    worker: idle | running | paused | unavailable
    browser: ready | unavailable
    jobhunter_queue: ready | not_configured | invalid
    siemens_adapter: ready | not_configured | unavailable

The bootstrap phase only needs ``api``, ``store``, ``browser``, and the
optional integrations. ``worker`` is reported as ``idle`` because no worker
exists yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import ComponentHealth, HealthReport
from universal_auto_applier.core.statuses import HealthState

if TYPE_CHECKING:
    from fastapi import FastAPI


def _check_store(engine: Engine) -> ComponentHealth:
    """Verify that the database can answer a trivial query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return ComponentHealth(name="store", state=HealthState.READY)
    except SQLAlchemyError as exc:
        return ComponentHealth(
            name="store",
            state=HealthState.UNAVAILABLE,
            detail=str(exc),
        )


def _check_browser(headless: bool) -> ComponentHealth:
    """Verify Chromium is installed and launchable.

    The check is intentionally cheap: we launch, read the version, and close.
    The bootstrap technical verification gate requires that a smoke test
    launches and closes Chromium.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            try:
                _ = browser.version
            finally:
                browser.close()
        return ComponentHealth(name="browser", state=HealthState.READY)
    except Exception as exc:  # noqa: BLE001 - any failure here is a health failure
        return ComponentHealth(
            name="browser",
            state=HealthState.UNAVAILABLE,
            detail=str(exc),
        )


def _check_jobhunter_queue(path: Path | None) -> ComponentHealth:
    """Validate the JobHunter queue file if configured."""
    if path is None:
        return ComponentHealth(
            name="jobhunter_queue",
            state=HealthState.NOT_CONFIGURED,
            detail="UAA_JOBHUNTER_QUEUE not set",
        )
    if not path.exists():
        return ComponentHealth(
            name="jobhunter_queue",
            state=HealthState.INVALID,
            detail=f"file not found: {path}",
        )
    if not path.is_file():
        return ComponentHealth(
            name="jobhunter_queue",
            state=HealthState.INVALID,
            detail=f"not a file: {path}",
        )
    return ComponentHealth(
        name="jobhunter_queue",
        state=HealthState.READY,
        detail=str(path),
    )


def _check_siemens_adapter(path: Path | None) -> ComponentHealth:
    """Validate the SiemensAutoApplier repo path if configured."""
    if path is None:
        return ComponentHealth(
            name="siemens_adapter",
            state=HealthState.NOT_CONFIGURED,
            detail="UAA_SIEMENS_REPO not set",
        )
    if not path.exists():
        return ComponentHealth(
            name="siemens_adapter",
            state=HealthState.UNAVAILABLE,
            detail=f"repo not found: {path}",
        )
    return ComponentHealth(
        name="siemens_adapter",
        state=HealthState.READY,
        detail=str(path),
    )


def build_health_report(
    settings: Settings,
    engine: Engine,
    *,
    skip_browser: bool = False,
) -> HealthReport:
    """Return the aggregated system health.

    ``skip_browser`` is used by the API's lightweight health endpoint to avoid
    launching Chromium on every poll. The detailed health endpoint and the
    startup check do launch Chromium.
    """
    api = ComponentHealth(name="api", state=HealthState.READY)
    store = _check_store(engine)
    worker = ComponentHealth(name="worker", state=HealthState.IDLE)
    browser = (
        _check_browser(settings.browser_headless)
        if not skip_browser
        else ComponentHealth(name="browser", state=HealthState.READY, detail="check skipped")
    )
    queue = _check_jobhunter_queue(settings.jobhunter_queue)
    siemens = _check_siemens_adapter(settings.siemens_repo)

    components = [api, store, worker, browser, queue, siemens]

    overall = HealthState.READY
    for component in components:
        if component.state in {HealthState.UNAVAILABLE, HealthState.INVALID}:
            overall = HealthState.UNAVAILABLE
            break

    return HealthReport(status=overall, components=components)


def make_health_report(app: FastAPI, *, skip_browser: bool = False) -> HealthReport:
    """Build a fresh health report from a FastAPI app's state.

    Routes call this helper instead of importing the app factory directly,
    which avoids a circular import (``api.app`` imports the router; the
    router imports this service).
    """
    settings: Settings = app.state.settings
    engine: Engine = app.state.engine
    return build_health_report(settings, engine, skip_browser=skip_browser)
