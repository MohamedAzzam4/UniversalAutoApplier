"""Core contracts shared by every layer.

``core`` is the lowest layer. Per ``IMPLEMENTATION_RULES.md``:

* ``core`` may import only the standard library and Pydantic.
* ``core`` must NOT import FastAPI, SQLAlchemy, Playwright, or UI modules.

Anything that requires a framework lives in ``api``, ``persistence``,
``browser``, or ``ui``.
"""

from __future__ import annotations
