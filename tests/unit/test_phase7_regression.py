"""Phase 7 regression tests — prove safety is preserved.

Per ``TESTING_STRATEGY.md``, every phase must run all previous tests plus
new ones. This file is the explicit safety regression suite for Phase 7:

1. Siemens adapter safety remains unchanged (still trusted, still gated
   by review approval).
2. Generic path (now used by all 5 new ATS adapters + the improved
   Generic fallback) never submits.
3. Pipeline orchestration still gates submit behind ``is_trusted`` AND
   review approval.
4. Dashboard start endpoint cannot submit.
5. The default registry registers all adapters in deterministic order
   with the Generic fallback last.
6. The 5 new ATS adapters are untrusted and route through the generic
   path (not the trusted adapter path).

These tests are deliberately redundant with the unit tests in
``test_ats_adapters.py`` and ``test_pipeline_orchestrator.py`` — they
exist to catch future regressions where a refactor might accidentally
make an untrusted adapter trusted, or remove the orchestrator's
review gate check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.adapters.greenhouse_adapter import GreenhouseAdapter
from universal_auto_applier.adapters.lever_adapter import LeverAdapter
from universal_auto_applier.adapters.linkedin_easy_apply_adapter import (
    LinkedInEasyApplyAdapter,
)
from universal_auto_applier.adapters.registry import AdapterRegistry
from universal_auto_applier.adapters.siemens_adapter import (
    SiemensAdapter,
    SiemensAdapterConfig,
)
from universal_auto_applier.adapters.smartrecruiters_adapter import (
    SmartRecruitersAdapter,
)
from universal_auto_applier.adapters.workday_adapter import WorkdayAdapter
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import AdapterResult, ApplicationJob, CandidateProfile
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
    Phase,
    Platform,
)
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.services.pipeline_orchestrator import PipelineOrchestrator

FORMS_DIR = Path(__file__).parent.parent / "fixtures" / "forms"
PLATFORM_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"


def _read_fixture(directory: Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8")


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


def _make_job(
    tmp_path: Path,
    *,
    url: str = "https://example.com/jobs/123",
    platform: Platform = Platform.UNKNOWN,
    external_job_id: str = "job-123",
    status: ApplicationStatus = ApplicationStatus.QUEUED,
) -> ApplicationJob:
    cv = str(tmp_path / "cv.pdf")
    cover = str(tmp_path / "cover.pdf")
    Path(cv).write_bytes(b"fake")
    Path(cover).write_bytes(b"fake")
    application_id = compute_application_id(
        platform=str(platform), external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=platform,
        source="linkedin",
        company="Test Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=cv,
        cover_letter_pdf=cover,
        status=status,
        external_job_id=external_job_id,
    )


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_phase7.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
    )


# ---------------------------------------------------------------------------
# 1. Siemens adapter safety remains unchanged
# ---------------------------------------------------------------------------


class TestSiemensAdapterSafetyUnchanged:
    """The Siemens adapter is the only trusted adapter. Its safety
    semantics must not change: it is trusted, but its submit_or_pause
    is still gated by the orchestrator's review approval check."""

    def test_siemens_adapter_is_trusted(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        assert adapter.is_trusted is True

    def test_siemens_adapter_can_handle_unchanged(self, tmp_path: Path) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_job(
            tmp_path,
            url="https://jobs.siemens.com/jobs/123",
            platform=Platform.SIEMENS,
            external_job_id="siemens-1",
        )
        assert adapter.can_handle(job) is True

    def test_siemens_adapter_still_gated_by_review(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Even with dry_run=False, submit_or_pause is NOT called without
        review approval. This is the same test as Phase 8's
        ``test_submit_blocked_with_dry_run_false_no_approval``, re-run
        here to prove Phase 7 didn't break it."""
        job = _make_job(
            tmp_path,
            platform=Platform.SIEMENS,
            url="https://jobs.siemens.com/jobs/456",
            external_job_id="510486",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        registry = AdapterRegistry()
        registry.register(
            SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo, dry_run=False))
        )
        registry.register(GenericAdapter(_make_candidate()))

        orch = PipelineOrchestrator(
            settings, session_factory, registry=registry, candidate=_make_candidate()
        )

        submit_called: list[bool] = []

        def mock_submit(self, job):  # noqa: ARG001
            submit_called.append(True)
            return AdapterResult(
                status=AdapterResultStatus.SUBMITTED,
                phase=Phase.SUBMIT,
                message="submitted",
            )

        with patch.object(SiemensAdapter, "submit_or_pause", mock_submit):
            with patch.object(
                SiemensAdapter,
                "navigate_to_form",
                lambda self, job: AdapterResult(
                    status=AdapterResultStatus.DRY_RUN,
                    phase=Phase.NAVIGATE,
                    message="ok",
                ),
            ):
                with patch.object(
                    SiemensAdapter,
                    "fill",
                    lambda self, job: AdapterResult(
                        status=AdapterResultStatus.DRY_RUN, phase=Phase.FILL, message="ok"
                    ),
                ):
                    with patch.object(
                        SiemensAdapter,
                        "prepare",
                        lambda self, job: AdapterResult.success(
                            phase=Phase.PREPARE,
                            message="ok",
                            application_id=job.application_id,
                        ),
                    ):
                        orch.run(fixture_html=None, max_jobs=1)

        assert len(submit_called) == 0, "submit_or_pause was called without review approval"

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        assert updated.status == ApplicationStatus.REVIEW_READY


# ---------------------------------------------------------------------------
# 2. All 5 new ATS adapters + Generic are untrusted
# ---------------------------------------------------------------------------


class TestAllNewAdaptersAreUntrusted:
    """Every adapter introduced in Phase 7 (and the improved Generic
    fallback) must be untrusted. Only SiemensAdapter is trusted."""

    @pytest.mark.parametrize(
        "adapter_cls",
        [
            GreenhouseAdapter,
            LeverAdapter,
            WorkdayAdapter,
            SmartRecruitersAdapter,
            LinkedInEasyApplyAdapter,
            GenericAdapter,
        ],
    )
    def test_adapter_is_untrusted(self, adapter_cls: type) -> None:
        adapter = adapter_cls(_make_candidate())
        assert adapter.is_trusted is False

    def test_only_siemens_is_trusted(self) -> None:
        """Sanity check: SiemensAdapter is still trusted."""
        assert SiemensAdapter(SiemensAdapterConfig()).is_trusted is True

    def test_base_class_is_untrusted(self) -> None:
        """The ApplicationAdapter base defaults is_trusted to False."""
        assert ApplicationAdapter.is_trusted is False


# ---------------------------------------------------------------------------
# 3. Pipeline orchestrator routes untrusted adapters through the generic path
# ---------------------------------------------------------------------------


class TestPipelineRouting:
    """The pipeline orchestrator must route untrusted adapters through
    the generic path (which never submits) and trusted adapters through
    the trusted adapter path (which still requires review approval)."""

    @pytest.mark.parametrize(
        "adapter_cls,platform,url",
        [
            (
                GreenhouseAdapter,
                Platform.GREENHOUSE,
                "https://boards.greenhouse.io/example/jobs/111",
            ),
            (LeverAdapter, Platform.LEVER, "https://jobs.lever.co/techco/222"),
            (
                WorkdayAdapter,
                Platform.WORKDAY,
                "https://globalcorp.myworkdayjobs.com/jobs/333",
            ),
            (
                SmartRecruitersAdapter,
                Platform.SMARTRECRUITERS,
                "https://careers.smartrecruiters.com/innovateco/jobs/444",
            ),
            (
                LinkedInEasyApplyAdapter,
                Platform.LINKEDIN_EASY_APPLY,
                "https://www.linkedin.com/jobs/view/555",
            ),
        ],
    )
    def test_ats_adapter_routes_to_generic_path(
        self,
        adapter_cls,
        platform,
        url,
        settings,
        session_factory,
        tmp_path: Path,
    ) -> None:
        """Each ATS adapter is selected by the registry and routed to
        the generic path (not the trusted adapter path). The job ends
        in review_ready or needs_user_input, never submitted."""
        job = _make_job(
            tmp_path,
            url=url,
            platform=platform,
            external_job_id=f"route-{adapter_cls.__name__}",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        registry = AdapterRegistry()
        # Register only the adapter under test + generic fallback.
        registry.register(adapter_cls(_make_candidate()))
        registry.register(GenericAdapter(_make_candidate()))

        orch = PipelineOrchestrator(
            settings, session_factory, registry=registry, candidate=_make_candidate()
        )

        # Use the platform's apply fixture so the generic path reaches a form.
        fixture_map = {
            GreenhouseAdapter: "greenhouse_apply.html",
            LeverAdapter: "lever_apply.html",
            WorkdayAdapter: "workday_apply.html",
            SmartRecruitersAdapter: "smartrecruiters_apply.html",
            LinkedInEasyApplyAdapter: "linkedin_apply.html",
        }
        fixture_html = _read_fixture(PLATFORM_DIR, fixture_map[adapter_cls])
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        assert updated.status not in (
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.APPLIED,
        )
        assert updated.status in (
            ApplicationStatus.REVIEW_READY,
            ApplicationStatus.NEEDS_USER_INPUT,
        )


# ---------------------------------------------------------------------------
# 4. Default registry registers all adapters in deterministic order
# ---------------------------------------------------------------------------


class TestDefaultRegistryOrder:
    """The default registry must register adapters in this order:
    Siemens → Greenhouse → Lever → Workday → SmartRecruiters →
    LinkedIn Easy Apply → Generic.

    Order matters because the registry selects the first adapter whose
    can_handle returns True. GenericAdapter.can_handle always returns
    True, so it MUST be registered last.
    """

    def test_default_registry_has_seven_adapters(self, settings, session_factory) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        registry_adapters = orch._registry.adapters
        assert len(registry_adapters) == 7

    def test_default_registry_order(self, settings, session_factory) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        adapters = orch._registry.adapters
        assert isinstance(adapters[0], SiemensAdapter)
        assert isinstance(adapters[1], GreenhouseAdapter)
        assert isinstance(adapters[2], LeverAdapter)
        assert isinstance(adapters[3], WorkdayAdapter)
        assert isinstance(adapters[4], SmartRecruitersAdapter)
        assert isinstance(adapters[5], LinkedInEasyApplyAdapter)
        assert isinstance(adapters[6], GenericAdapter)

    def test_generic_is_registered_last(self, settings, session_factory) -> None:
        """GenericAdapter MUST be registered last (its can_handle always
        returns True, so any adapter registered after it would never be
        selected)."""
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        adapters = orch._registry.adapters
        assert isinstance(adapters[-1], GenericAdapter)

    def test_greenhouse_url_routes_to_greenhouse_adapter(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Sanity check: a Greenhouse URL is claimed by GreenhouseAdapter,
        not by the Generic fallback."""
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        job = _make_job(
            tmp_path,
            url="https://boards.greenhouse.io/example/jobs/123",
            platform=Platform.GREENHOUSE,
            external_job_id="route-gh",
        )
        adapter = orch._select_adapter(job)
        assert isinstance(adapter, GreenhouseAdapter)

    def test_linkedin_url_routes_to_linkedin_adapter(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        job = _make_job(
            tmp_path,
            url="https://www.linkedin.com/jobs/view/999",
            platform=Platform.LINKEDIN_EASY_APPLY,
            external_job_id="route-li",
        )
        adapter = orch._select_adapter(job)
        assert isinstance(adapter, LinkedInEasyApplyAdapter)

    def test_unknown_url_routes_to_generic(self, settings, session_factory, tmp_path: Path) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        job = _make_job(
            tmp_path,
            url="https://unknown-ats.example.com/jobs/1",
            platform=Platform.UNKNOWN,
            external_job_id="route-unknown",
        )
        adapter = orch._select_adapter(job)
        assert isinstance(adapter, GenericAdapter)


# ---------------------------------------------------------------------------
# 5. Generic path never submits (parametrized across all 5 ATS platforms)
# ---------------------------------------------------------------------------


class TestGenericPathNeverSubmits:
    """Run the pipeline with each ATS platform's apply fixture and prove
    the job never ends in SUBMITTED or APPLIED status."""

    @pytest.mark.parametrize(
        "platform,url,fixture_name",
        [
            (
                Platform.GREENHOUSE,
                "https://boards.greenhouse.io/example/jobs/101",
                "greenhouse_apply.html",
            ),
            (
                Platform.LEVER,
                "https://jobs.lever.co/techco/102",
                "lever_apply.html",
            ),
            (
                Platform.WORKDAY,
                "https://globalcorp.myworkdayjobs.com/jobs/103",
                "workday_apply.html",
            ),
            (
                Platform.SMARTRECRUITERS,
                "https://careers.smartrecruiters.com/innovateco/jobs/104",
                "smartrecruiters_apply.html",
            ),
            (
                Platform.LINKEDIN_EASY_APPLY,
                "https://www.linkedin.com/jobs/view/105",
                "linkedin_apply.html",
            ),
        ],
    )
    def test_platform_apply_never_submits(
        self,
        platform,
        url,
        fixture_name,
        settings,
        session_factory,
        tmp_path: Path,
    ) -> None:
        job = _make_job(
            tmp_path,
            url=url,
            platform=platform,
            external_job_id=f"never-submit-{platform.value}",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        fixture_html = _read_fixture(PLATFORM_DIR, fixture_name)
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        assert updated.status not in (
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.APPLIED,
        )


# ---------------------------------------------------------------------------
# 6. Dashboard start endpoint cannot submit
# ---------------------------------------------------------------------------


class TestDashboardStartSafety:
    """The POST /api/pipeline/start endpoint must never produce a
    SUBMITTED or APPLIED status, even when run with each platform's
    fixture HTML.

    These tests use the real FastAPI TestClient (which sets up
    request.app automatically)."""

    @pytest.mark.parametrize(
        "platform,url,fixture_name",
        [
            (
                Platform.GREENHOUSE,
                "https://boards.greenhouse.io/example/jobs/201",
                "greenhouse_apply.html",
            ),
            (
                Platform.LEVER,
                "https://jobs.lever.co/techco/202",
                "lever_apply.html",
            ),
            (
                Platform.WORKDAY,
                "https://globalcorp.myworkdayjobs.com/jobs/203",
                "workday_apply.html",
            ),
            (
                Platform.SMARTRECRUITERS,
                "https://careers.smartrecruiters.com/innovateco/jobs/204",
                "smartrecruiters_apply.html",
            ),
            (
                Platform.LINKEDIN_EASY_APPLY,
                "https://www.linkedin.com/jobs/view/205",
                "linkedin_apply.html",
            ),
        ],
    )
    def test_dashboard_start_does_not_submit(
        self,
        platform,
        url,
        fixture_name,
        tmp_path: Path,
    ) -> None:
        """POST /api/pipeline/start with a platform fixture must not
        result in SUBMITTED or APPLIED status."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.db import session_scope
        from universal_auto_applier.persistence.models import Base

        # Use a temp-data-dir settings instance (do not reuse the
        # session-scoped `settings` fixture because each parametrize
        # case needs its own clean DB).
        local_settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        local_settings.data_dir.mkdir(parents=True, exist_ok=True)

        app = create_app(settings=local_settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            session_factory = app.state.session_factory

            job = _make_job(
                tmp_path,
                url=url,
                platform=platform,
                external_job_id=f"dash-{platform.value}",
            )
            with session_scope(session_factory) as session:
                upsert_application_job(session, job)

            fixture_html = _read_fixture(PLATFORM_DIR, fixture_name)
            response = client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] in ("completed", "error")
            assert "No real submissions" in body["message"] or "error" in body["message"]

            with session_scope(session_factory) as session:
                updated = get_application_job(session, job.application_id)
            assert updated is not None
            assert updated.status not in (
                ApplicationStatus.SUBMITTED,
                ApplicationStatus.APPLIED,
            )


# ---------------------------------------------------------------------------
# 7. Login / captcha / review stops the pipeline for every platform
# ---------------------------------------------------------------------------


class TestLoginStopsEveryPlatform:
    """Every ATS platform's login fixture must stop the pipeline and
    create a login_required intervention."""

    @pytest.mark.parametrize(
        "platform,url,fixture_name",
        [
            (
                Platform.GREENHOUSE,
                "https://boards.greenhouse.io/example/jobs/301",
                "greenhouse_login.html",
            ),
            (
                Platform.LEVER,
                "https://jobs.lever.co/techco/302",
                "lever_login.html",
            ),
            (
                Platform.WORKDAY,
                "https://globalcorp.myworkdayjobs.com/jobs/303",
                "workday_login.html",
            ),
            (
                Platform.SMARTRECRUITERS,
                "https://careers.smartrecruiters.com/innovateco/jobs/304",
                "smartrecruiters_login.html",
            ),
            (
                Platform.LINKEDIN_EASY_APPLY,
                "https://www.linkedin.com/jobs/view/305",
                "linkedin_login.html",
            ),
        ],
    )
    def test_login_creates_intervention(
        self,
        platform,
        url,
        fixture_name,
        settings,
        session_factory,
        tmp_path: Path,
    ) -> None:
        from universal_auto_applier.interventions.store import list_pending_interventions

        job = _make_job(
            tmp_path,
            url=url,
            platform=platform,
            external_job_id=f"login-{platform.value}",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        fixture_html = _read_fixture(PLATFORM_DIR, fixture_name)
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)

        assert any(i.kind == "login_required" for i in pending), (
            f"Expected login_required intervention for {platform.value}, "
            f"got: {[i.kind for i in pending]}"
        )
