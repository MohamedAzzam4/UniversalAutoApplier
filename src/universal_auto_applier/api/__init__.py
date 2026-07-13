"""FastAPI HTTP API and dashboard serving layer.

Per ``TECHNICAL_BASELINE.md``:

* FastAPI provides the local HTTP API and serves the dashboard assets.
* Bind to 127.0.0.1 by default.
* Use a FastAPI lifespan context manager to create and close shared resources.
* Keep route handlers thin; they validate input, call an application service,
  and serialize a result.
* No authentication for the localhost-only version 1 API.
"""

from __future__ import annotations
