"""Playwright test: dashboard shell loads at desktop and mobile viewports.

Per ``docs/generalization/TESTING_STRATEGY.md`` -> Playwright Tests:

    Required tests:
    - Dashboard loads.

Per ``UI_UX_SPEC.md`` -> UI Tests:

    Every UI change must be tested with Playwright.

These tests are marked ``playwright`` and excluded from the default
regression command. Run them with::

    python -m pytest -m playwright
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def test_dashboard_loads_at_desktop_viewport(page, server_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(server_url)
    page.wait_for_selector("h1", timeout=10_000)
    expect_text = page.locator("h1").inner_text()
    assert "UniversalAutoApplier" in expect_text

    # The status card must exist and not be empty.
    page.wait_for_selector("#overall-status", timeout=10_000)
    status_text = page.locator("#overall-status").inner_text()
    assert status_text, "overall status pill was empty"


def test_dashboard_loads_at_small_viewport(page, server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server_url)
    page.wait_for_selector("h1", timeout=10_000)
    expect_text = page.locator("h1").inner_text()
    assert "UniversalAutoApplier" in expect_text


def test_dashboard_loads_at_medium_viewport(page, server_url: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(server_url)
    page.wait_for_selector("h1", timeout=10_000)
    expect_text = page.locator("h1").inner_text()
    assert "UniversalAutoApplier" in expect_text


def test_dashboard_health_polls_and_renders_components(page, server_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(server_url)
    # Wait for the JS poll to render component rows. Allow up to 10s.
    page.wait_for_selector("#component-list li", timeout=10_000)
    items = page.locator("#component-list li").all_inner_texts()
    joined = " ".join(items)
    # At least the api/store/worker rows must be present.
    assert "api" in joined
    assert "store" in joined
    assert "worker" in joined


def test_dashboard_has_no_visible_overlap_at_desktop(page, server_url: str) -> None:
    """Smoke check that no two top-level cards overlap at desktop size.

    This is a coarse check: we collect bounding boxes for the .uaa-card
    elements and assert no two cards have intersecting rectangles.
    """
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(server_url)
    page.wait_for_selector(".uaa-card", timeout=10_000)

    boxes = page.eval_on_selector_all(
        ".uaa-card",
        """(els) => els.map(el => {
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height};
        })""",
    )

    def intersects(a, b) -> bool:
        return not (
            a["x"] + a["w"] <= b["x"]
            or b["x"] + b["w"] <= a["x"]
            or a["y"] + a["h"] <= b["y"]
            or b["y"] + b["h"] <= a["y"]
        )

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not intersects(boxes[i], boxes[j]), (
                f"cards {i} and {j} overlap: {boxes[i]} {boxes[j]}"
            )
