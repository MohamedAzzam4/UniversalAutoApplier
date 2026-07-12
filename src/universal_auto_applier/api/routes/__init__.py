"""API route modules.

Each route module owns a thin FastAPI router. Routes validate input, call a
service method, and serialize the result. They never touch the database
directly.
"""

from __future__ import annotations
