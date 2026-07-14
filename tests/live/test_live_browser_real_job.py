"""Opt-in live dry-run against one imported real job.

Enable explicitly with ``UAA_ENABLE_LIVE_TEST=1`` and provide
``UAA_LIVE_APPLICATION_ID``. The runner still stops before final submit.
"""

from __future__ import annotations

import os

import pytest

from universal_auto_applier.cli import run_command
from universal_auto_applier.config import load_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("UAA_ENABLE_LIVE_TEST") != "1",
        reason="opt-in live browser test is disabled",
    ),
]


def test_real_imported_job_reaches_safe_terminal_state() -> None:
    application_id = os.environ.get("UAA_LIVE_APPLICATION_ID", "").strip()
    if not application_id:
        pytest.skip("UAA_LIVE_APPLICATION_ID is not configured")
    exit_code = run_command(
        ["live-dry-run", "--application-id", application_id, "--headless"],
        load_settings(),
    )
    assert exit_code in {0, 3}
