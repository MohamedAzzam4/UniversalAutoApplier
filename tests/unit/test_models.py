"""Unit tests for :mod:`universal_auto_applier.core.models`.

Validates the :class:`HealthReport` / :class:`ComponentHealth` Pydantic v2
models used by the bootstrap health endpoint.
"""

from __future__ import annotations

from universal_auto_applier import __version__
from universal_auto_applier.core.models import ComponentHealth, HealthReport
from universal_auto_applier.core.statuses import HealthState


def test_health_report_defaults_to_ready() -> None:
    report = HealthReport()
    assert report.status == HealthState.READY
    assert report.version == __version__
    assert report.components == []


def test_component_health_requires_name_and_state() -> None:
    component = ComponentHealth(name="api", state=HealthState.READY)
    assert component.name == "api"
    assert component.state == HealthState.READY
    assert component.detail == ""


def test_health_report_find_returns_matching_component() -> None:
    report = HealthReport(
        components=[
            ComponentHealth(name="api", state=HealthState.READY),
            ComponentHealth(name="store", state=HealthState.UNAVAILABLE, detail="boom"),
        ]
    )
    found = report.find("store")
    assert found is not None
    assert found.state == HealthState.UNAVAILABLE
    assert found.detail == "boom"


def test_health_report_find_returns_none_when_missing() -> None:
    report = HealthReport()
    assert report.find("api") is None
