"""Alembic environment for UniversalAutoApplier.

Runs migrations in ``online`` mode against the URL injected by
``universal_auto_applier.persistence.migrations`` or set in ``alembic.ini`` /
``--x sqlalchemy.url``. Imports ``Base.metadata`` from the application so
autogenerate has access to the full ORM model graph.

Foreign keys are enforced via a SQLAlchemy ``connect`` event listener on the
migration engine; this avoids opening an auto-begin transaction before
Alembic's own transaction context, which in SQLAlchemy 2.x would otherwise
swallow the ``alembic_version`` INSERT on connection close.

The migration engine is disposed in a ``finally`` block after migrations
finish. Without ``dispose()``, the engine's connection pool holds
``sqlite3.Connection`` objects open, and Python 3.14's stricter resource
finalization emits ``ResourceWarning: unclosed database`` during garbage
collection.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Engine, engine_from_config, event, pool

from universal_auto_applier.persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emit SQL to a script)."""
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url must be set before running migrations")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection.

    Creates a SQLAlchemy engine, runs migrations within a transaction, and
    disposes the engine in a ``finally`` block to avoid leaking
    ``sqlite3.Connection`` objects.
    """
    section = config.get_section(config.config_ini_section, {})
    if section is None:
        section = {}
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url must be set before running migrations")
    section["sqlalchemy.url"] = url

    connectable: Engine = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Enable SQLite foreign keys via an event listener. Executing PRAGMA
    # inline before context.begin_transaction() would open an auto-begin
    # transaction in SQLAlchemy 2.x and cause the alembic_version insert to
    # be rolled back when the connection closes.
    if connectable.dialect.name == "sqlite":

        @event.listens_for(connectable, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        # Dispose the engine to release any pooled connections. Without this,
        # Python 3.14 emits ResourceWarning: unclosed database when the
        # sqlite3.Connection is garbage-collected.
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
