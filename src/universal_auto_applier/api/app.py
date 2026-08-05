"""FastAPI application factory.

Exposes :func:`create_app` which wires up the lifespan context, the API
router, and the dashboard static files. The lifespan owns the SQLAlchemy
engine and session factory; route handlers receive them through
``request.app.state``.

The app is constructed without binding. The caller (``__main__`` or a test
helper) chooses the host and port.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from universal_auto_applier import __version__
from universal_auto_applier.api.routes.health import router as health_router
from universal_auto_applier.api.routes.interventions import router as interventions_router
from universal_auto_applier.api.routes.logs import init_log_buffer, router as logs_router
from universal_auto_applier.api.routes.pipeline import router as pipeline_router
from universal_auto_applier.api.routes.queue import router as queue_router
from universal_auto_applier.api.routes.queue_import import router as queue_import_router
from universal_auto_applier.api.routes.retry import router as retry_router
from universal_auto_applier.api.routes.review import router as review_router
from universal_auto_applier.api.routes.status import router as status_router
from universal_auto_applier.api.routes.submit import router as submit_router
from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"

logger = logging.getLogger("universal_auto_applier.api.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create shared resources on startup; close them on shutdown.

    If ``app.state`` already has an ``engine`` (e.g. set by a test helper),
    it is reused — no unnecessary extra engine is created.  The lifespan
    only disposes engines it creates itself; pre-provided engines remain
    the caller's responsibility.
    """
    settings: Settings = app.state.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    _owns_engine = False
    engine: Any = getattr(app.state, "engine", None)
    if engine is None:
        db_url = build_engine_url(settings.data_dir / "uaa.sqlite")
        engine = make_engine(db_url)
        _owns_engine = True
        app.state.db_url = db_url
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
    else:
        if not getattr(app.state, "session_factory", None):
            app.state.session_factory = make_session_factory(engine)

    if not getattr(app.state, "review_states", None):
        app.state.review_states = {}

    # Initialize the background pipeline worker service (WQ-4).
    from universal_auto_applier.services.pipeline_worker_service import (
        PipelineWorkerService,
    )

    if not getattr(app.state, "pipeline_worker", None):
        app.state.pipeline_worker = PipelineWorkerService(settings, app.state.session_factory)

    init_log_buffer(app)

    # Optional, opt-in startup queue import. When UAA_IMPORT_QUEUE_ON_STARTUP
    # is false (the default) nothing is imported. When enabled, one import
    # runs against the configured UAA_QUEUE_PATH; every outcome is persisted
    # durably and failures are surfaced through /api/queue/status and health
    # without crashing the server.
    if settings.import_queue_on_startup:
        from universal_auto_applier.services.queue_import_service import run_startup_import

        app.state.startup_import_summary = run_startup_import(settings, app.state.session_factory)
        if app.state.startup_import_summary is not None:
            logger.info(
                "startup queue import: %s",
                app.state.startup_import_summary.get("state"),
            )

    try:
        yield
    finally:
        if _owns_engine and engine is not None:
            engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI application configured for local-first use.

    The instance stores ``settings`` and the SQLAlchemy engine/factory on
    ``app.state`` so route handlers can access them without global mutable
    state.
    """
    if settings is None:
        from universal_auto_applier.config import load_settings

        settings = load_settings()

    app = FastAPI(
        title="UniversalAutoApplier",
        version=__version__,
        description=(
            "Local-first generalized job application system. "
            "Owns queue import, adapter routing, generic navigation, form "
            "filling, interventions, review-before-submit, evidence, "
            "application history, and the operational dashboard."
        ),
        lifespan=lifespan,
        # The OpenAPI docs are useful locally; do not expose them publicly
        # without auth in a later version.
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.state.settings = settings

    app.include_router(health_router, prefix="/api")
    app.include_router(status_router, prefix="/api")
    # queue_import must register BEFORE the dynamic /api/queue/{application_id}
    # route so the fixed /api/queue/import and /api/queue/status paths win.
    app.include_router(queue_import_router, prefix="/api")
    app.include_router(queue_router, prefix="/api")
    app.include_router(interventions_router, prefix="/api")
    app.include_router(review_router, prefix="/api")
    app.include_router(logs_router, prefix="/api")
    app.include_router(retry_router, prefix="/api")
    app.include_router(pipeline_router, prefix="/api")
    app.include_router(submit_router, prefix="/api")

    # Serve the dashboard static assets.
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        """Serve the dashboard shell at the root URL."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api", include_in_schema=False)
    def api_root() -> dict[str, Any]:
        """Tiny API root so callers can confirm the API is up without /health."""
        return {
            "name": "UniversalAutoApplier",
            "version": __version__,
            "endpoints": [
                "/api/health",
                "/api/health/detail",
                "/api/status",
                "/api/queue",
                "/api/queue/status",
                "/api/queue/import",
                "/api/interventions",
                "/api/review/{id}/submit-check",
                "/api/logs",
                "/api/errors",
            ],
        }

    return app


__all__ = ["create_app"]
