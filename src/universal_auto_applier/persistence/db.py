"""Engine and session factory.

* SQLite is the version 1 database.
* Foreign keys are enabled for every connection.
* Timestamps are timezone-aware UTC (see :mod:`persistence.models`).
* Sessions are created through context managers; service methods define
  transaction boundaries and repositories do not commit secretly.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from universal_auto_applier.persistence.models import Base


def build_engine_url(database_path: Path) -> str:
    """Return a SQLite URL for ``database_path``.

    Uses four slashes for an absolute path and three for a relative path.
    """
    absolute = database_path.resolve()
    return f"sqlite:///{absolute.as_posix()}"


def make_engine(database_url: str, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine with SQLite foreign keys enabled."""
    engine = create_engine(database_url, echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        # SQLite needs PRAGMA foreign_keys=ON per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Context manager that commits on success and rolls back on exception.

    Service methods should call this to define a transaction boundary.
    Repositories must not commit on their own.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(engine: Engine) -> None:
    """Create every table. Used by tests that need a fresh schema fast.

    Production code paths use Alembic migrations via :func:`apply_migrations`.
    """
    Base.metadata.create_all(engine)
