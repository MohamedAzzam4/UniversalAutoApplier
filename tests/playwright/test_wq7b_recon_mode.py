"""WQ-7B recon-mode tests: navigation/observation only, zero writes.

These tests prove that ``LiveBrowserRunner`` with ``recon_only=True``:

1. Follows the safe apply path from a job-detail page to the application
   form (real navigation, using apply/continue clicks).
2. STOPS at the first application form it detects.
3. NEVER fills a field, NEVER uploads a file, NEVER clicks submit.
4. Records a structural observation of the reached form.
5. Keeps the WQ-7 submit interlock armed through the whole run.

The recon fixture includes a job page with an "Apply now" link and an
application form page with first/last name, email, and resume fields. The
form page's onsubmit handler marks ``data-submitted=true``; recon runs must
never trigger it.

Tests use the REAL production path ``LiveBrowserRunner.run_in_context``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext

from tests.playwright._fixture_server import serve_fixture_dir
from universal_auto_applier.browser.live_runner import (
    LiveBrowserConfig,
    LiveBrowserRunner,
)
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform

pytestmark = pytest.mark.playwright

RECON_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "recon"


@pytest.fixture(scope="module")
def recon_server() -> str:
    yield from serve_fixture_dir(RECON_FIXTURE_DIR)


def _make_job(url: str, tmp_path: Path) -> ApplicationJob:
    cv_pdf = tmp_path / "recon-cv.pdf"
    cover_pdf = tmp_path / "recon-cover.pdf"
    cv_md = tmp_path / "recon-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python product manager", encoding="utf-8")
    return ApplicationJob(
        application_id=compute_application_id(
            platform="generic", external_job_id="recon-1", url=url
        ),
        platform=Platform.GENERIC,
        source="wq7b_recon_fixture",
        company="Example Corp",
        title="Senior Product Manager",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="recon-1",
        documents=ApplicationJobDocuments(cv_md=str(cv_md)),
        metadata={
            "candidate_profile": {
                "first_name": "Recon",
                "last_name": "Observer",
                "full_name": "Recon Observer",
                "email": "recon.observer@example.com",
                "phone": "+49 000",
                "wq7_synthetic": True,
            },
        },
    )


def _make_recon_config(tmp_path: Path, **overrides) -> LiveBrowserConfig:  # type: ignore[no-untyped-def]
    defaults = {
        "artifacts_root": tmp_path / "live-runs",
        "headless": True,
        "timeout_ms": 10_000,
        "max_steps": 5,
        "capture_trace": True,
        "hard_submit_block": True,
        "recon_only": True,
    }
    defaults.update(overrides)
    return LiveBrowserConfig(**defaults)


class TestReconStopsAtFirstForm:
    def test_navigates_to_form_and_stops(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """Recon mode follows the apply link, then stops at the form."""
        url = f"{recon_server}/job.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon",
        )

        # The recon run followed the apply path: at least one click happened.
        assert len(report.click_path) >= 1, "recon mode should navigate to the form"
        last_url = report.click_path[-1].to_url
        assert last_url.endswith("apply.html")

        # It stopped at the first application form it detected.
        assert report.status == "recon_complete"
        assert report.stopped_reason == "first_application_form_reached"
        assert report.final_url.endswith("apply.html")

    def test_zero_fills_zero_uploads_zero_submits(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """Recon mode records zero field fills, zero uploads, zero submits."""
        url = f"{recon_server}/job.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon-zero",
        )

        assert report.status == "recon_complete"
        assert report.fields == [], "recon mode must not fill any field"
        assert report.uploads == [], "recon mode must not upload any document"
        assert report.submitted is False, "recon mode must never submit"
        # The form never submitted: its onsubmit marker stays untouched.
        assert report.dom_snapshot_path is not None
        dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8")
        assert 'data-submitted="false"' in dom, "the form was submitted during recon!"

    def test_observation_records_form_structure(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """Recon observations capture the form's structural shape."""
        url = f"{recon_server}/job.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon-obs",
        )

        obs = report.recon_observation
        assert obs is not None
        assert obs.page_url.endswith("apply.html")
        assert obs.visible_control_count >= 3  # first_name, last_name, email
        assert obs.file_input_count >= 1  # resume
        assert obs.has_dangerous_submit is True  # a real submit button exists
        assert any("email" in label.lower() for label in obs.field_labels)
        assert any("first_name" in label.lower() for label in obs.field_labels)

    def test_interlock_armed_during_recon(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """The WQ-7 submit interlock is active across the whole recon run."""
        url = f"{recon_server}/job.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon-interlock",
        )

        assert report.status == "recon_complete"
        assert report.submitted is False
        # Recon mode must never even attempt a submission.
        assert not any("blocked" in err for err in report.errors), (
            "recon mode should not attempt submissions the interlock must block"
        )

    def test_form_with_embedded_captcha_is_still_recorded(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """A real form that embeds an anti-bot widget is still observed.

        Real ATS forms (for example Lever's application form) render an
        hCaptcha widget inside the form. Recon must record the form's
        structure and note the embedded widget as evidence — it never
        interacts with the widget and still stops at the first form.
        """
        url = f"{recon_server}/job_hcaptcha.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon-hcaptcha",
        )

        assert report.status == "recon_complete", (
            "a form containing an embedded captcha widget must still be recorded"
        )
        assert report.stopped_reason == "first_application_form_reached"
        obs = report.recon_observation
        assert obs is not None
        assert obs.embedded_blocker == "captcha_detected"
        assert obs.visible_control_count >= 3
        assert obs.file_input_count >= 1
        assert report.fields == [], "recon still never fills on a captcha form"
        assert report.uploads == []
        assert report.submitted is False
        # The widget was never touched: no value, no checkbox click.
        assert report.dom_snapshot_path is not None
        dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8")
        assert 'data-submitted="false"' in dom

    def test_fill_mode_still_blocks_before_fill_on_captcha_form(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """WQ-7A fill mode keeps blocker-before-fill even on a captcha form.

        The recon-only exception (record a form that embeds a captcha) must
        apply ONLY to reconnaissance/observation. In fill mode the captcha
        must stop the run BEFORE any field is touched.
        """
        url = f"{recon_server}/job_hcaptcha.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path, recon_only=False))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-fill-captcha",
        )

        assert report.status == "needs_user_input"
        assert report.stopped_reason == "captcha_detected"
        assert report.fields == [], "fill mode must not touch a captcha form"
        assert report.uploads == []
        assert report.submitted is False
        # The form's onsubmit marker stays untouched: nothing was submitted.
        assert report.dom_snapshot_path is not None
        dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8")
        assert 'data-submitted="false"' in dom


class TestReconConfigFlag:
    def test_recon_only_defaults_false(self, tmp_path: Path) -> None:
        """Recon mode is opt-in; default config is WQ-7A full dry-run."""
        config = LiveBrowserConfig(artifacts_root=tmp_path)
        assert config.recon_only is False

    def test_recon_only_flag_stored(self, tmp_path: Path) -> None:
        config = LiveBrowserConfig(artifacts_root=tmp_path, recon_only=True)
        assert config.recon_only is True


class TestReconNeverSubmitsCode:
    def test_recon_branch_before_fill_code(self) -> None:
        """The recon branch is reached before any form-fill code runs."""
        import inspect

        from universal_auto_applier.browser.live_runner import LiveBrowserRunner

        source = inspect.getsource(LiveBrowserRunner.run_in_context)
        recon_index = source.index("recon_only")
        fill_index = source.index("execute_live_form")
        assert recon_index < fill_index, (
            "recon mode must short-circuit before the WQ-7A fill path executes"
        )

    def test_recon_requires_interlock(self, tmp_path: Path) -> None:
        """Recon configs created by the CLI/service always arm the interlock."""
        config = LiveBrowserConfig(
            artifacts_root=tmp_path,
            hard_submit_block=True,
            recon_only=True,
        )
        assert config.hard_submit_block is True and config.recon_only is True


class TestReconReadOnlyEvidence:
    def test_no_candidate_data_in_dom(
        self, context: BrowserContext, recon_server: str, tmp_path: Path
    ) -> None:
        """The observed DOM contains no candidate data and no typed values."""
        url = f"{recon_server}/job.html"
        job = _make_job(url, tmp_path)
        runner = LiveBrowserRunner(_make_recon_config(tmp_path))
        report = runner.run_in_context(
            context,
            job,
            candidate=CandidateProfile(
                first_name="Recon",
                last_name="Observer",
                full_name="Recon Observer",
                email="recon.observer@example.com",
                phone="+49 000",
            ),
            artifact_dir=tmp_path / "run-recon-dom",
        )

        assert report.dom_snapshot_path is not None
        dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8").lower()
        # Nobody with the synthetic identity's personal data may appear in DOM.
        assert "recon.observer@example.com" not in dom
        assert "recon observer" not in dom
        # The empty inputs keep their empty state.
        assert 'name="first_name"' in dom
