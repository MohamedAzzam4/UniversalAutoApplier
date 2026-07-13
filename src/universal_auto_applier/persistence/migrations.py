"""Alembic migration helpers.

The migration directory lives at ``<repo>/migrations`` (configured in
``alembic.ini``). For tests and ``python -m universal_auto_applier`` startup,
we run ``alembic upgrade head`` programmatically against a target SQLite URL.

All SQLAlchemy engines created here are disposed in ``finally`` blocks to
avoid leaking ``sqlite3.Connection`` objects. On Python 3.14, the sqlite3
module emits ``ResourceWarning: unclosed database`` when a connection is
garbage-collected without being closed, which pytest surfaces as
``PytestUnraisableExceptionWarning``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _make_config(database_url: str) -> Config:
    """Build an Alembic :class:`Config` pointing at ``database_url``."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Inject the URL at the config layer so the migrations env.py does not
    # need to read from a separate file.
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _get_current_revision(engine: Engine) -> str:
    """Return the current Alembic revision using ``engine``.

    The caller is responsible for disposing ``engine`` after this returns.
    """
    with engine.connect() as connection:
        ctx = MigrationContext.configure(connection)
        return ctx.get_current_revision() or "head"


def apply_migrations(database_url: str) -> str:
    """Run ``alembic upgrade head`` against ``database_url``.

    Returns the current revision after the upgrade.

    This function disposes every engine it creates. ``command.upgrade`` runs
    ``migrations/env.py`` which creates and disposes its own engine; the
    revision-check engine created here is disposed in a ``finally`` block.
    """
    config = _make_config(database_url)
    command.upgrade(config, "head")

    # Check the current revision using a fresh engine. We MUST dispose this
    # engine — SQLite's default StaticPool holds the connection open, and on
    # Python 3.14 an undisposed engine triggers
    # ``ResourceWarning: unclosed database`` during garbage collection.
    engine = create_engine(database_url)
    try:
        return _get_current_revision(engine)
    finally:
        engine.dispose()
