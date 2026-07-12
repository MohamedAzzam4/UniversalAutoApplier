"""Integration tests for the FastAPI app.

These tests exercise multiple internal modules (config + persistence +
services + API) but do not launch a browser unless explicitly requested via
``/api/health/detail``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from universal_auto_applier import __version__


def test_api_root_lists_endpoints(client: TestClient) -> None:
    response = client.get("/api")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "UniversalAutoApplier"
    assert body["version"] == __version__
    assert "/api/health" in body["endpoints"]


def test_health_endpoint_returns_report(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == __version__
    assert "components" in body
    component_names = {c["name"] for c in body["components"]}
    # Bootstrap must report every capability required by the health contract.
    assert {"api", "store", "worker", "browser", "jobhunter_queue", "siemens_adapter"}.issubset(
        component_names
    )


def test_health_endpoint_skips_browser_launch(client: TestClient) -> None:
    """The lightweight endpoint must NOT launch Chromium on every poll."""
    response = client.get("/api/health")
    body = response.json()
    browser = next(c for c in body["components"] if c["name"] == "browser")
    # When skipped, browser state is reported as ready with a detail note.
    assert browser["state"] == "ready"
    assert "skipped" in browser["detail"]


def test_health_detail_endpoint_launches_browser(client: TestClient) -> None:
    """The detailed endpoint actually launches Chromium once."""
    response = client.get("/api/health/detail")
    assert response.status_code == 200
    body = response.json()
    browser = next(c for c in body["components"] if c["name"] == "browser")
    # In the test environment, Chromium IS installed, so this must be ready.
    assert browser["state"] == "ready"


def test_health_endpoint_reports_jobhunter_queue_not_configured(client: TestClient) -> None:
    response = client.get("/api/health")
    body = response.json()
    queue = next(c for c in body["components"] if c["name"] == "jobhunter_queue")
    assert queue["state"] == "not_configured"


def test_health_endpoint_reports_siemens_adapter_not_configured(client: TestClient) -> None:
    response = client.get("/api/health")
    body = response.json()
    siemens = next(c for c in body["components"] if c["name"] == "siemens_adapter")
    assert siemens["state"] == "not_configured"


def test_dashboard_shell_loads_at_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    assert "UniversalAutoApplier" in body
    assert "/static/styles.css" in body
    assert "/static/app.js" in body


def test_dashboard_static_assets_served(client: TestClient) -> None:
    styles = client.get("/static/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers.get("content-type", "")
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers.get("content-type", "")
