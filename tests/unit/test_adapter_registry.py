"""Unit tests for :mod:`universal_auto_applier.adapters.registry`.

Covers platform detection and adapter registry routing.
"""

from __future__ import annotations

import pytest

from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.adapters.registry import (
    AdapterRegistry,
    NoAdapterError,
    detect_platform,
)
from universal_auto_applier.adapters.siemens_adapter import (
    SiemensAdapter,
    SiemensAdapterConfig,
)
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform


def _make_job(
    *,
    url: str = "https://example.com/jobs/123",
    platform: Platform = Platform.UNKNOWN,
    external_job_id: str = "job-123",
    status: ApplicationStatus = ApplicationStatus.EVALUATED,
    cv_pdf: str | None = None,
    cover_letter_pdf: str | None = None,
) -> ApplicationJob:
    # For application_id computation, pass the actual platform string.
    # The ApplicationJob model validates application_id against the platform
    # field, so we must use the same value here.
    application_id = compute_application_id(
        platform=str(platform),
        external_job_id=external_job_id,
        url=url,
    )
    return ApplicationJob(
        application_id=application_id,
        platform=platform,
        source="linkedin",
        company="Example GmbH",
        title="Working Student AI",
        url=url,
        location="Munich, Germany",
        job_description="Full JD",
        score=4.1,
        verdict="apply",
        cv_pdf=cv_pdf,
        cover_letter_pdf=cover_letter_pdf,
        status=status,
        external_job_id=external_job_id,
    )


class TestDetectPlatform:
    def test_siemens_url(self) -> None:
        assert detect_platform("https://jobs.siemens.com/jobs/123") == Platform.SIEMENS

    def test_greenhouse_boards_url(self) -> None:
        assert (
            detect_platform("https://boards.greenhouse.io/company/jobs/123") == Platform.GREENHOUSE
        )

    def test_greenhouse_main_url(self) -> None:
        assert detect_platform("https://company.greenhouse.io/jobs/123") == Platform.GREENHOUSE

    def test_lever_url(self) -> None:
        assert detect_platform("https://jobs.lever.co/company/123") == Platform.LEVER

    def test_workday_url(self) -> None:
        assert detect_platform("https://company.myworkdayjobs.com/jobs/123") == Platform.WORKDAY

    def test_smartrecruiters_url(self) -> None:
        assert (
            detect_platform("https://careers.smartrecruiters.com/company/jobs/123")
            == Platform.SMARTRECRUITERS
        )

    def test_linkedin_jobs_url(self) -> None:
        assert (
            detect_platform("https://www.linkedin.com/jobs/view/123")
            == Platform.LINKEDIN_EASY_APPLY
        )

    def test_unknown_url(self) -> None:
        assert detect_platform("https://example.com/jobs/123") == Platform.UNKNOWN

    def test_empty_url(self) -> None:
        assert detect_platform("") == Platform.UNKNOWN

    def test_case_insensitive_host(self) -> None:
        assert detect_platform("https://JOBS.SIEMENS.COM/jobs/123") == Platform.SIEMENS


class TestAdapterRegistry:
    def test_register_and_select(self) -> None:
        registry = AdapterRegistry()
        registry.register(SiemensAdapter(SiemensAdapterConfig()))
        registry.register(GenericAdapter())

        job = _make_job(url="https://jobs.siemens.com/jobs/123")
        adapter = registry.select(job)
        assert isinstance(adapter, SiemensAdapter)

    def test_select_falls_back_to_generic(self) -> None:
        registry = AdapterRegistry()
        registry.register(SiemensAdapter(SiemensAdapterConfig()))
        registry.register(GenericAdapter())

        job = _make_job(url="https://example.com/jobs/123")
        adapter = registry.select(job)
        assert isinstance(adapter, GenericAdapter)

    def test_select_raises_when_no_adapter(self) -> None:
        registry = AdapterRegistry()
        job = _make_job(url="https://example.com/jobs/123")
        with pytest.raises(NoAdapterError):
            registry.select(job)

    def test_duplicate_platform_rejected(self) -> None:
        registry = AdapterRegistry()
        registry.register(SiemensAdapter(SiemensAdapterConfig()))
        with pytest.raises(ValueError, match="Duplicate adapter"):
            registry.register(SiemensAdapter(SiemensAdapterConfig()))

    def test_select_by_platform(self) -> None:
        registry = AdapterRegistry()
        registry.register(SiemensAdapter(SiemensAdapterConfig()))
        registry.register(GenericAdapter())

        adapter = registry.select_by_platform(Platform.SIEMENS)
        assert isinstance(adapter, SiemensAdapter)

        adapter = registry.select_by_platform(Platform.GENERIC)
        assert isinstance(adapter, GenericAdapter)

    def test_select_by_platform_raises_for_missing(self) -> None:
        registry = AdapterRegistry()
        with pytest.raises(NoAdapterError):
            registry.select_by_platform(Platform.GREENHOUSE)

    def test_adapters_list_is_a_copy(self) -> None:
        registry = AdapterRegistry()
        registry.register(GenericAdapter())
        adapters = registry.adapters
        adapters.clear()
        assert len(registry) == 1  # original is unchanged

    def test_deterministic_order(self) -> None:
        """Adapters are selected in registration order."""
        registry = AdapterRegistry()
        registry.register(SiemensAdapter(SiemensAdapterConfig()))
        registry.register(GenericAdapter())

        # Siemens is registered first, so a Siemens job gets SiemensAdapter.
        job = _make_job(url="https://jobs.siemens.com/jobs/123")
        assert isinstance(registry.select(job), SiemensAdapter)

        # An unknown job falls through to GenericAdapter.
        unknown_job = _make_job(url="https://example.com/jobs/123")
        assert isinstance(registry.select(unknown_job), GenericAdapter)


class TestSiemensAdapterCanHandle:
    def test_can_handle_siemens_url(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_job(url="https://jobs.siemens.com/jobs/123")
        assert adapter.can_handle(job)

    def test_can_handle_siemens_platform_field(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_job(
            url="https://example.com/jobs/123",
            platform=Platform.SIEMENS,
        )
        assert adapter.can_handle(job)

    def test_cannot_handle_greenhouse_url(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_job(url="https://boards.greenhouse.io/company/jobs/123")
        assert not adapter.can_handle(job)

    def test_cannot_handle_unknown_url(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_job(url="https://example.com/jobs/123")
        assert not adapter.can_handle(job)


class TestGenericAdapterCanHandle:
    def test_can_handle_anything(self) -> None:
        adapter = GenericAdapter()
        job = _make_job(url="https://example.com/jobs/123")
        assert adapter.can_handle(job)

        job2 = _make_job(url="https://jobs.siemens.com/jobs/123")
        assert adapter.can_handle(job2)
