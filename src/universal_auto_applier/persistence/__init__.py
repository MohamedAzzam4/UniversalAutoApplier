"""Persistence layer.

Owns SQLAlchemy ORM models, the engine factory, session context managers, and
migration helpers. Per ``IMPLEMENTATION_RULES.md``:

* ``persistence`` may import ``core`` contracts and SQLAlchemy only.
* Service methods define transaction boundaries; repositories do not commit
  secretly inside reusable operations.
* SQLite foreign keys must be enabled for every connection.
* Timestamps are stored as timezone-aware UTC.
"""

from __future__ import annotations
