"""Contract test proving the harness tests are selected by CI markers.

The CI runs: python -m pytest -m "not live" (when INCLUDE_PLAYWRIGHT=1)
or python -m pytest -m "not playwright and not live" (default).

This test verifies that the harness tests are NOT excluded by either
marker expression — they must run in every CI configuration.
"""

from __future__ import annotations

from pathlib import Path


def test_harness_tests_not_marked_live() -> None:
    """The harness test file must NOT contain the 'live' marker."""
    harness_path = Path(__file__).parent.parent / "integration" / "test_submission_harness.py"
    content = harness_path.read_text(encoding="utf-8")
    assert "pytest.mark.live" not in content, (
        "Harness tests must not be marked 'live' — they are local fixture tests."
    )


def test_harness_tests_not_marked_playwright() -> None:
    """The harness test file must NOT be marked 'playwright'."""
    harness_path = Path(__file__).parent.parent / "integration" / "test_submission_harness.py"
    content = harness_path.read_text(encoding="utf-8")
    assert "pytest.mark.playwright" not in content, (
        "Harness tests must not be marked 'playwright' — they use a subprocess, "
        "not the pytest-playwright plugin."
    )


def test_harness_tests_marked_integration() -> None:
    """The harness test file must be marked 'integration'."""
    harness_path = Path(__file__).parent.parent / "integration" / "test_submission_harness.py"
    content = harness_path.read_text(encoding="utf-8")
    assert "pytest.mark.integration" in content, (
        "Harness tests must be marked 'integration' so they are selected by "
        "the normal CI marker expression."
    )


def test_harness_tests_have_no_version_skip() -> None:
    """The harness test file must NOT contain any Python version skip."""
    harness_path = Path(__file__).parent.parent / "integration" / "test_submission_harness.py"
    content = harness_path.read_text(encoding="utf-8")
    assert "sys.version_info" not in content, "Harness tests must not skip based on Python version."
    assert "pytest.mark.skip" not in content, "Harness tests must not use pytest.mark.skip."
