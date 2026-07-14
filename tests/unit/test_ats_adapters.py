"""Unit tests for the Phase 7 ATS platform adapters.

Covers all six adapters (Greenhouse, Lever, Workday, SmartRecruiters,
LinkedIn Easy Apply, and the improved Generic fallback) plus the shared
``_UntrustedATSAdapter`` base.

Per ``TESTING_STRATEGY.md`` Phase 7 requirements, each adapter must have:
- ``can_handle`` positive URL test
- ``can_handle`` negative URL test
- ``can_handle`` with platform field set
- ``prepare`` behavior (blocked if no cv_pdf, success otherwise)
- ``navigate_to_form`` behavior (form, login, captcha, review, planning mode)
- ``fill`` behavior (form, planning mode, no form fields)
- ``submit_or_pause`` ALWAYS returns ``review_ready`` and never submits
- Login/captcha/password/review stop behavior creates intervention
- Missing selector / layout change fails safely with structured result

All tests use local fixture HTML only. No live browser, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.adapters.greenhouse_adapter import GreenhouseAdapter
from universal_auto_applier.adapters.lever_adapter import LeverAdapter
from universal_auto_applier.adapters.linkedin_easy_apply_adapter import (
    LinkedInEasyApplyAdapter,
)
from universal_auto_applier.adapters.smartrecruiters_adapter import (
    SmartRecruitersAdapter,
)
from universal_auto_applier.adapters.workday_adapter import WorkdayAdapter
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
    Phase,
    Platform,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"
FORMS_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_platform_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _read_forms_fixture(name: str) -> str:
    return (FORMS_DIR / name).read_text(encoding="utf-8")


def _make_job(
    *,
    url: str = "https://example.com/jobs/123",
    platform: Platform = Platform.UNKNOWN,
    external_job_id: str = "job-123",
    status: ApplicationStatus = ApplicationStatus.QUEUED,
    cv_pdf: str | None = None,
    cover_letter_pdf: str | None = None,
    tmp_path: Path | None = None,
) -> ApplicationJob:
    """Build a valid ApplicationJob with a deterministic application_id.

    If ``cv_pdf`` is None and ``tmp_path`` is provided, fake PDF files
    are written to tmp_path so the job validates as ``ready_to_apply``
    or ``queued``. If ``cv_pdf`` is None and ``tmp_path`` is None, the
    job is built without documents (callers must use a non-ready status).
    """
    cv = cv_pdf
    cover = cover_letter_pdf
    if cv is None and tmp_path is not None:
        cv = str(tmp_path / "cv.pdf")
        cover = cover or str(tmp_path / "cover.pdf")
        Path(cv).write_bytes(b"fake")
        Path(cover).write_bytes(b"fake")
    application_id = compute_application_id(
        platform=str(platform), external_job_id=external_job_id, url=url
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
        cv_pdf=cv,
        cover_letter_pdf=cover,
        status=status,
        external_job_id=external_job_id,
    )


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        full_name="John Doe",
        email="john@example.com",
        phone="+49 123 456789",
        linkedin_url="https://linkedin.com/in/johndoe",
        city="Munich",
        country="Germany",
        requires_sponsorship=False,
    )


# ---------------------------------------------------------------------------
# Parametrized fixtures: every adapter is tested through the same matrix.
# ---------------------------------------------------------------------------

# Each tuple is (adapter_class, platform, positive_url, negative_url).
ADAPTER_MATRIX = [
    (
        GreenhouseAdapter,
        Platform.GREENHOUSE,
        "https://boards.greenhouse.io/example/jobs/123",
        "https://example.com/jobs/123",
    ),
    (
        LeverAdapter,
        Platform.LEVER,
        "https://jobs.lever.co/techco/123",
        "https://example.com/jobs/123",
    ),
    (
        WorkdayAdapter,
        Platform.WORKDAY,
        "https://globalcorp.myworkdayjobs.com/jobs/123",
        "https://example.com/jobs/123",
    ),
    (
        SmartRecruitersAdapter,
        Platform.SMARTRECRUITERS,
        "https://careers.smartrecruiters.com/innovateco/jobs/123",
        "https://example.com/jobs/123",
    ),
    (
        LinkedInEasyApplyAdapter,
        Platform.LINKEDIN_EASY_APPLY,
        "https://www.linkedin.com/jobs/view/123",
        "https://example.com/jobs/123",
    ),
]


# Map adapter class -> (job_fixture, apply_fixture, login_fixture,
#                       review_fixture, changed_layout_fixture)
PLATFORM_FIXTURES: dict[type, dict[str, str]] = {
    GreenhouseAdapter: {
        "job": "greenhouse_job.html",
        "apply": "greenhouse_apply.html",
        "login": "greenhouse_login.html",
        "review": "greenhouse_review.html",
        "changed": "greenhouse_changed_layout.html",
    },
    LeverAdapter: {
        "job": "lever_job.html",
        "apply": "lever_apply.html",
        "login": "lever_login.html",
        "review": "lever_review.html",
        "changed": "lever_changed_layout.html",
    },
    WorkdayAdapter: {
        "job": "workday_job.html",
        "apply": "workday_apply.html",
        "login": "workday_login.html",
        "review": "workday_review.html",
        "changed": "workday_changed_layout.html",
    },
    SmartRecruitersAdapter: {
        "job": "smartrecruiters_job.html",
        "apply": "smartrecruiters_apply.html",
        "login": "smartrecruiters_login.html",
        "review": "smartrecruiters_review.html",
        "changed": "smartrecruiters_changed_layout.html",
    },
    LinkedInEasyApplyAdapter: {
        "job": "linkedin_job.html",
        "apply": "linkedin_apply.html",
        "login": "linkedin_login.html",
        "review": "linkedin_review.html",
        "changed": "linkedin_changed_layout.html",
    },
}


# ---------------------------------------------------------------------------
# Shared safety tests — parametrized over every adapter.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterCanHandle:
    def test_can_handle_positive_url(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-pos",
            tmp_path=tmp_path,
        )
        assert adapter.can_handle(job) is True

    def test_can_handle_negative_url(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=negative_url,
            platform=Platform.UNKNOWN,
            external_job_id=f"{adapter_cls.__name__}-neg",
            tmp_path=tmp_path,
        )
        assert adapter.can_handle(job) is False

    def test_can_handle_platform_field(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        # Even with a non-matching URL, the platform field routes to the adapter.
        job = _make_job(
            url=negative_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-field",
            tmp_path=tmp_path,
        )
        assert adapter.can_handle(job) is True


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterIsTrusted:
    def test_adapter_is_untrusted(self, adapter_cls, platform, positive_url, negative_url) -> None:
        """All five new ATS adapters must be untrusted."""
        adapter = adapter_cls(_make_candidate())
        assert adapter.is_trusted is False

    def test_base_class_default_is_untrusted(
        self, adapter_cls, platform, positive_url, negative_url
    ) -> None:
        """The base ApplicationAdapter defaults is_trusted to False."""
        from universal_auto_applier.adapters.base import ApplicationAdapter

        # We cannot instantiate ABC directly, but the class attribute is False.
        assert ApplicationAdapter.is_trusted is False


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterPrepare:
    def test_prepare_success_with_cv(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-prep-ok",
            tmp_path=tmp_path,
        )
        result = adapter.prepare(job)
        assert result.status == AdapterResultStatus.SUCCESS
        assert result.phase == Phase.PREPARE
        assert result.platform == platform
        assert result.application_id == job.application_id
        assert result.next_action == "navigate_to_form"
        # is_trusted must be recorded in metadata for callers.
        assert result.metadata.get("is_trusted") is False

    def test_prepare_blocked_without_cv(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        # Build a job with no documents and a status that does not require them.
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-prep-no-cv",
            status=ApplicationStatus.EVALUATED,
            cv_pdf=None,
            cover_letter_pdf=None,
        )
        result = adapter.prepare(job)
        assert result.status == AdapterResultStatus.BLOCKED
        assert result.phase == Phase.PREPARE
        assert "missing_cv_pdf" in result.errors


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterNavigate:
    def test_navigate_planning_mode_without_fixture(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Without fixture HTML, navigate runs in planning mode (dry_run)."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-nav-plan",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        result = adapter.navigate_to_form(job, fixture_html=None)
        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.phase == Phase.NAVIGATE
        assert result.metadata.get("planning_mode") is True

    def test_navigate_reaches_form(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Navigate with an apply-form fixture reaches a form."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-nav-form",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        apply_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["apply"])
        result = adapter.navigate_to_form(job, fixture_html=apply_html)
        # The apply fixture is already a form, so navigate returns success.
        assert result.status == AdapterResultStatus.SUCCESS
        assert result.phase == Phase.NAVIGATE
        assert result.next_action == "fill"
        assert result.metadata.get("page_state") == "form"

    def test_navigate_stops_on_login(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Navigate with a login fixture stops with needs_user_input."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-nav-login",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        login_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["login"])
        result = adapter.navigate_to_form(job, fixture_html=login_html)
        assert result.status == AdapterResultStatus.NEEDS_USER_INPUT
        assert result.phase == Phase.NAVIGATE
        assert "login" in result.metadata.get("page_state", "")
        assert "login_required" in result.metadata.get("intervention_kind", "")

    def test_navigate_stops_on_review(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Navigate with a review fixture stops with needs_user_input."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-nav-review",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        review_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["review"])
        result = adapter.navigate_to_form(job, fixture_html=review_html)
        assert result.status == AdapterResultStatus.NEEDS_USER_INPUT
        assert result.phase == Phase.NAVIGATE
        assert "review" in result.metadata.get("page_state", "")
        assert "review_before_submit" in result.metadata.get("intervention_kind", "")


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterFill:
    def test_fill_planning_mode_without_fixture(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Without fixture HTML, fill runs in planning mode (dry_run)."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-fill-plan",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        result = adapter.fill(job, fixture_html=None)
        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.phase == Phase.FILL
        assert result.metadata.get("planning_mode") is True

    def test_fill_processes_apply_form(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Fill with an apply-form fixture extracts and fills fields."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-fill-apply",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        apply_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["apply"])
        result = adapter.fill(job, fixture_html=apply_html)
        # Fill should succeed or need user input (for screening questions).
        assert result.status in (
            AdapterResultStatus.SUCCESS,
            AdapterResultStatus.NEEDS_USER_INPUT,
        )
        assert result.phase == Phase.FILL
        # Metadata should contain a fill summary.
        summary = result.metadata.get("fill_summary", {})
        assert "total_fields" in summary
        assert summary["total_fields"] > 0
        # The adapter should not have submitted.
        assert result.metadata.get("submitted") is not True

    def test_fill_fails_safely_on_no_fields(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Fill with a no-form-fields fixture fails safely."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-fill-empty",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        # Use the changed-layout fixture, which has no form fields.
        changed_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["changed"])
        result = adapter.fill(job, fixture_html=changed_html)
        assert result.status == AdapterResultStatus.FAILED
        assert result.phase == Phase.FILL
        assert "no_form_fields" in result.errors
        assert result.metadata.get("failed_safely") is True
        assert result.metadata.get("reason") == "no_form_fields"


@pytest.mark.parametrize(
    "adapter_cls,platform,positive_url,negative_url",
    ADAPTER_MATRIX,
    ids=[a[0].__name__ for a in ADAPTER_MATRIX],
)
class TestAdapterSubmitSafety:
    """The most important safety tests: adapters never submit."""

    def test_submit_or_pause_always_returns_review_ready(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-submit-1",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        result = adapter.submit_or_pause(job, approved=False)
        assert result.status == AdapterResultStatus.REVIEW_READY
        assert result.phase == Phase.SUBMIT
        assert result.metadata.get("submitted") is False
        assert result.metadata.get("review_before_submit") is True
        assert result.metadata.get("is_trusted") is False

    def test_submit_or_pause_refuses_even_when_approved(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """Even if approved=True is passed, untrusted adapters refuse to submit."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-submit-2",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        result = adapter.submit_or_pause(job, approved=True)
        assert result.status == AdapterResultStatus.REVIEW_READY
        assert result.metadata.get("submitted") is False

    def test_submit_or_pause_never_returns_submitted(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """The 'submitted' status must NEVER appear from an untrusted adapter."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-submit-3",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        for approved in (False, True):
            result = adapter.submit_or_pause(job, approved=approved)
            assert result.status != AdapterResultStatus.SUBMITTED
            assert result.status == AdapterResultStatus.REVIEW_READY


# ---------------------------------------------------------------------------
# Generic fallback adapter tests (separately, because can_handle is always True)
# ---------------------------------------------------------------------------


class TestGenericAdapterCanHandle:
    def test_can_handle_any_url(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(
            url="https://anything.example.com/jobs/123",
            external_job_id="generic-1",
            tmp_path=tmp_path,
        )
        assert adapter.can_handle(job) is True

    def test_can_handle_known_platform_urls_too(self, tmp_path: Path) -> None:
        """Generic adapter can_handle returns True even for known platforms.

        This is by design: GenericAdapter is the fallback. The registry
        registers it LAST, so known-platform URLs are claimed by their
        specific adapter before the generic adapter sees them.
        """
        adapter = GenericAdapter(_make_candidate())
        for url in (
            "https://boards.greenhouse.io/example/jobs/123",
            "https://jobs.lever.co/techco/123",
            "https://globalcorp.myworkdayjobs.com/jobs/123",
            "https://careers.smartrecruiters.com/innovateco/jobs/123",
            "https://www.linkedin.com/jobs/view/123",
            "https://jobs.siemens.com/jobs/123",
        ):
            job = _make_job(
                url=url,
                external_job_id=f"generic-{url.split('/')[2]}",
                tmp_path=tmp_path,
            )
            assert adapter.can_handle(job) is True, f"failed for {url}"

    def test_is_trusted_is_false(self) -> None:
        assert GenericAdapter(_make_candidate()).is_trusted is False


class TestGenericAdapterPrepare:
    def test_prepare_success(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-prep-1", tmp_path=tmp_path)
        result = adapter.prepare(job)
        assert result.status == AdapterResultStatus.SUCCESS
        assert result.metadata.get("is_trusted") is False

    def test_prepare_blocked_without_cv(self) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(
            external_job_id="generic-prep-2",
            status=ApplicationStatus.EVALUATED,
            cv_pdf=None,
            cover_letter_pdf=None,
        )
        result = adapter.prepare(job)
        assert result.status == AdapterResultStatus.BLOCKED
        assert "missing_cv_pdf" in result.errors


class TestGenericAdapterNavigate:
    def test_navigate_planning_mode(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-nav-1", tmp_path=tmp_path)
        adapter.prepare(job)
        result = adapter.navigate_to_form(job, fixture_html=None)
        assert result.status == AdapterResultStatus.DRY_RUN

    def test_navigate_reaches_form(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-nav-2", tmp_path=tmp_path)
        adapter.prepare(job)
        # Use the simple_application form fixture from the forms/ dir.
        html = _read_forms_fixture("simple_application.html")
        result = adapter.navigate_to_form(job, fixture_html=html)
        assert result.status == AdapterResultStatus.SUCCESS
        assert result.metadata.get("page_state") == "form"

    def test_navigate_stops_on_login(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-nav-3", tmp_path=tmp_path)
        adapter.prepare(job)
        html = _read_forms_fixture("login_page.html")
        result = adapter.navigate_to_form(job, fixture_html=html)
        assert result.status == AdapterResultStatus.NEEDS_USER_INPUT
        assert "login" in result.metadata.get("intervention_kind", "")

    def test_navigate_stops_on_captcha(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-nav-4", tmp_path=tmp_path)
        adapter.prepare(job)
        html = _read_forms_fixture("captcha_page.html")
        result = adapter.navigate_to_form(job, fixture_html=html)
        assert result.status == AdapterResultStatus.NEEDS_USER_INPUT
        assert "captcha" in result.metadata.get("intervention_kind", "")

    def test_navigate_stops_on_review(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-nav-5", tmp_path=tmp_path)
        adapter.prepare(job)
        html = _read_forms_fixture("review_submit.html")
        result = adapter.navigate_to_form(job, fixture_html=html)
        assert result.status == AdapterResultStatus.NEEDS_USER_INPUT
        assert "review" in result.metadata.get("intervention_kind", "")


class TestGenericAdapterFill:
    def test_fill_planning_mode(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-fill-1", tmp_path=tmp_path)
        adapter.prepare(job)
        result = adapter.fill(job, fixture_html=None)
        assert result.status == AdapterResultStatus.DRY_RUN

    def test_fill_processes_simple_form(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-fill-2", tmp_path=tmp_path)
        adapter.prepare(job)
        html = _read_forms_fixture("simple_application.html")
        result = adapter.fill(job, fixture_html=html)
        assert result.status in (
            AdapterResultStatus.SUCCESS,
            AdapterResultStatus.NEEDS_USER_INPUT,
        )
        summary = result.metadata.get("fill_summary", {})
        assert summary.get("total_fields", 0) > 0

    def test_fill_fails_safely_on_empty_page(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-fill-3", tmp_path=tmp_path)
        adapter.prepare(job)
        # Empty HTML has no form fields.
        result = adapter.fill(job, fixture_html="<html><body></body></html>")
        assert result.status == AdapterResultStatus.FAILED
        assert "no_form_fields" in result.errors


class TestGenericAdapterSubmitSafety:
    def test_submit_returns_review_ready(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-submit-1", tmp_path=tmp_path)
        adapter.prepare(job)
        result = adapter.submit_or_pause(job, approved=False)
        assert result.status == AdapterResultStatus.REVIEW_READY
        assert result.metadata.get("submitted") is False

    def test_submit_refuses_when_approved(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-submit-2", tmp_path=tmp_path)
        adapter.prepare(job)
        result = adapter.submit_or_pause(job, approved=True)
        assert result.status == AdapterResultStatus.REVIEW_READY
        assert result.metadata.get("submitted") is False

    def test_submit_never_returns_submitted(self, tmp_path: Path) -> None:
        adapter = GenericAdapter(_make_candidate())
        job = _make_job(external_job_id="generic-submit-3", tmp_path=tmp_path)
        adapter.prepare(job)
        for approved in (False, True):
            result = adapter.submit_or_pause(job, approved=approved)
            assert result.status != AdapterResultStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Failure-behavior tests — prove layout changes / errors fail safely.
# ---------------------------------------------------------------------------


class TestAdapterFailureBehavior:
    """Adapters fail safely on changed layouts, parser errors, etc."""

    @pytest.mark.parametrize(
        "adapter_cls,platform,positive_url,negative_url",
        ADAPTER_MATRIX,
        ids=[a[0].__name__ for a in ADAPTER_MATRIX],
    )
    def test_fill_on_changed_layout_fails_safely(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """The changed-layout fixture has no form fields; fill must fail safely."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-fail-1",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        changed_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["changed"])
        result = adapter.fill(job, fixture_html=changed_html)
        assert result.status == AdapterResultStatus.FAILED
        assert result.metadata.get("failed_safely") is True
        assert result.metadata.get("reason") == "no_form_fields"

    @pytest.mark.parametrize(
        "adapter_cls,platform,positive_url,negative_url",
        ADAPTER_MATRIX,
        ids=[a[0].__name__ for a in ADAPTER_MATRIX],
    )
    def test_navigate_on_changed_layout_does_not_submit(
        self, adapter_cls, platform, positive_url, negative_url, tmp_path: Path
    ) -> None:
        """The changed-layout fixture has no apply button; navigate must
        either return needs_user_input (stopped) or success (reached form)
        but NEVER submit."""
        adapter = adapter_cls(_make_candidate())
        job = _make_job(
            url=positive_url,
            platform=platform,
            external_job_id=f"{adapter_cls.__name__}-fail-2",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        changed_html = _read_platform_fixture(PLATFORM_FIXTURES[adapter_cls]["changed"])
        result = adapter.navigate_to_form(job, fixture_html=changed_html)
        # The changed layout has no apply button and no form, so navigate
        # will stop with needs_user_input (no_safe_action or unknown page).
        assert result.status != AdapterResultStatus.SUBMITTED
        assert result.status in (
            AdapterResultStatus.NEEDS_USER_INPUT,
            AdapterResultStatus.SUCCESS,
            AdapterResultStatus.DRY_RUN,
        )

    def test_observe_error_caught(self, tmp_path: Path) -> None:
        """If observe_html raises, the adapter fails safely."""
        from unittest.mock import patch

        import universal_auto_applier.adapters._ats_base as ats_base

        adapter = GreenhouseAdapter(_make_candidate())
        job = _make_job(
            url="https://boards.greenhouse.io/example/jobs/123",
            platform=Platform.GREENHOUSE,
            external_job_id="gh-observe-err",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        # Patch observe_html on the _ats_base module, which imports it by name.
        # Patching on page_observer alone would not affect the bound reference
        # already imported into _ats_base.
        with patch.object(
            ats_base,
            "observe_html",
            side_effect=RuntimeError("simulated parser crash"),
        ):
            result = adapter.navigate_to_form(job, fixture_html="<html></html>")
        assert result.status == AdapterResultStatus.FAILED
        assert result.metadata.get("failed_safely") is True
        assert result.metadata.get("reason") == "observe_error"

    def test_extract_form_fields_error_caught(self, tmp_path: Path) -> None:
        """If extract_form_fields raises, fill fails safely."""
        from unittest.mock import patch

        import universal_auto_applier.adapters._ats_base as ats_base

        adapter = GreenhouseAdapter(_make_candidate())
        job = _make_job(
            url="https://boards.greenhouse.io/example/jobs/123",
            platform=Platform.GREENHOUSE,
            external_job_id="gh-extract-err",
            tmp_path=tmp_path,
        )
        adapter.prepare(job)
        with patch.object(
            ats_base,
            "extract_form_fields",
            side_effect=RuntimeError("simulated extract crash"),
        ):
            result = adapter.fill(job, fixture_html="<html><body><form></form></body></html>")
        assert result.status == AdapterResultStatus.FAILED
        assert result.metadata.get("failed_safely") is True
        assert result.metadata.get("reason") == "extract_error"


# ---------------------------------------------------------------------------
# Base-class behavior tests
# ---------------------------------------------------------------------------


class TestUntrustedATSAdapterBase:
    def test_is_trusted_is_false_on_base(self) -> None:
        # The base class is private but its is_trusted attribute must be False.
        assert _UntrustedATSAdapter.is_trusted is False

    def test_state_resets_on_prepare(self, tmp_path: Path) -> None:
        adapter = GreenhouseAdapter(_make_candidate())
        job1 = _make_job(
            url="https://boards.greenhouse.io/example/jobs/111",
            platform=Platform.GREENHOUSE,
            external_job_id="gh-state-1",
            tmp_path=tmp_path,
        )
        adapter.prepare(job1)
        adapter.state.notes.append("dirty-state")
        assert adapter.state.application_id == job1.application_id

        job2 = _make_job(
            url="https://boards.greenhouse.io/example/jobs/222",
            platform=Platform.GREENHOUSE,
            external_job_id="gh-state-2",
            tmp_path=tmp_path,
        )
        adapter.prepare(job2)
        # State was reset: dirty-state note is gone, application_id is job2's.
        assert "dirty-state" not in adapter.state.notes
        assert adapter.state.application_id == job2.application_id
