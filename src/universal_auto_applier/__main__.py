"""Module entry point: ``python -m universal_auto_applier``.

Bootstraps the local system. Default behavior:

* bind to 127.0.0.1 (never public),
* serve the dashboard and API,
* run database migrations to head,
* print the dashboard URL.

The default command starts the dashboard. Explicit subcommands can list jobs
or run one live browser dry-run. The live command fills and uploads but never
clicks final submit.
"""

from __future__ import annotations

import logging
import sys

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings, load_settings
from universal_auto_applier.persistence.db import build_engine_url
from universal_auto_applier.persistence.migrations import apply_migrations

logger = logging.getLogger("universal_auto_applier.main")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """Run the local UniversalAutoApplier system.

    Returns a process exit code. Designed to be safe to call from tests.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = "--verbose" in argv or "-v" in argv
    _configure_logging(verbose)

    settings: Settings = load_settings()
    if argv and argv[0] in {"list-jobs", "browser-session", "live-dry-run"}:
        from universal_auto_applier.cli import run_command

        return run_command(argv, settings)

    logger.info("starting UniversalAutoApplier on %s:%s", settings.host, settings.port)
    logger.info("data dir: %s", settings.data_dir)
    logger.info("submit mode: %s", settings.submit_mode)

    # Ensure the data directory exists. The SQLite database file lives inside it.
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # Apply migrations to head on every local startup. This is idempotent.
    db_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    try:
        applied = apply_migrations(db_url)
        logger.info("database migrations applied: head=%s", applied)
    except Exception as exc:  # noqa: BLE001 - log and exit nonzero on migration failure
        logger.error("migration failure: %s", exc)
        return 2

    # Import here so that lifespan startup logs see a fully configured app.
    import uvicorn  # local import keeps module import cheap for tests

    app = create_app(settings=settings)
    dashboard_url = f"http://{settings.host}:{settings.port}/"
    logger.info("dashboard URL: %s", dashboard_url)
    print(f"\nUniversalAutoApplier dashboard: {dashboard_url}\n", file=sys.stderr)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - executed via `python -m`
    raise SystemExit(main())
