"""Alembic migration helpers.

The migration directory lives at ``<repo>/migrations`` (configured in
``alembic.ini``). For tests and ``python -m universal_auto_applier`` startup,
we run ``alembic upgrade head`` programmatically against a target SQLite URL.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

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


def apply_migrations(database_url: str) -> str:
    """Run ``alembic upgrade head`` against ``database_url``.

    Returns the current revision after the upgrade.
    """
    config = _make_config(database_url)
    command.upgrade(config, "head")
    from alembic.runtime.migration import MigrationContext

    engine = config.attributes.get("connection")
    if engine is not None:
        # Reuse the connection Alembic already opened.
        with engine.connect() as connection:
            ctx = MigrationContext.configure(connection)
            return ctx.get_current_revision() or "head"

    # Fall back to opening a fresh connection.
    from sqlalchemy import create_engine

    with create_engine(database_url).connect() as connection:
        ctx = MigrationContext.configure(connection)
        return ctx.get_current_revision() or "head"
