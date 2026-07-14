"""Playwright browser execution package.

Import concrete types from ``browser.live_models`` or ``browser.live_runner``.
Keeping package initialization light avoids loading Playwright from pure-model
and fixture-only code paths.
"""

from __future__ import annotations
