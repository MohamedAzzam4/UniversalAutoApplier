"""Local Playwright fixtures for real navigation, filling, and uploads."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, ApplicationJobDocuments
from universal_auto_applier.core.statuses import ApplicationStatus, Platform

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture(scope="module")
def live_fixture_server() -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_job(tmp_path: Path, url: str, external_id: str) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_md = tmp_path / f"{external_id}-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python automation, FastAPI, and data analysis", encoding="utf-8")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
        source="fixture",
        company="Fixture Company",
        title="Working Student",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id=external_id,
        documents=ApplicationJobDocuments(cv_md=str(cv_md)),
        metadata={
            "candidate_profile": {
                "first_name": "Mohamed",
                "last_name": "Azzam",
                "full_name": "Mohamed Azzam",
                "email": "mohamed@example.com",
                "phone": "+49 1234567",
                "requires_sponsorship": False,
                "salutation": "Mr.",
            },
            "question_answers": {"Do you have experience with Python?": "Yes"},
        },
    )


def _runner(tmp_path: Path) -> LiveBrowserRunner:
    return LiveBrowserRunner(
        LiveBrowserConfig(
            artifacts_root=tmp_path / "live-runs",
            headless=True,
            timeout_ms=10_000,
            max_steps=10,
        )
    )


def _assert_review_ready_without_submit(report, context: BrowserContext) -> None:
    assert report.status == "review_ready", report.model_dump_json(indent=2)
    assert report.stopped_reason == "final_submit_detected"
    assert report.submitted is False
    assert report.report_path is not None and Path(report.report_path).exists()
    assert report.trace_path is not None and Path(report.trace_path).exists()
    final_page = context.pages[-1]
    assert final_page.locator("body").get_attribute("data-submitted") == "false"


def test_direct_ats_form_fills_and_uploads(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    job = _make_job(tmp_path, f"{live_fixture_server}/direct_application.html", "direct")
    report = _runner(tmp_path).run_in_context(
        context, job, artifact_dir=tmp_path / "direct-artifacts"
    )

    _assert_review_ready_without_submit(report, context)
    page = context.pages[-1]
    assert page.locator("#first_name").input_value() == "Mohamed"
    assert page.locator("#last_name").input_value() == "Azzam"
    assert page.locator("#email").input_value() == "mohamed@example.com"
    assert page.locator("#salutation").input_value() == "mr"
    assert page.locator("input[name='sponsorship'][value='No']").is_checked()
    assert all(record.label != "Search options" for record in report.fields)
    assert {upload.document_kind for upload in report.uploads} == {"cv", "cover_letter"}
    assert all(upload.status == "uploaded" for upload in report.uploads)


def test_linkedin_outbound_apply_reaches_company_form(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    job = _make_job(tmp_path, f"{live_fixture_server}/linkedin_job.html", "linkedin")
    report = _runner(tmp_path).run_in_context(
        context, job, artifact_dir=tmp_path / "linkedin-artifacts"
    )

    _assert_review_ready_without_submit(report, context)
    assert [record.text for record in report.click_path] == [
        "Apply on company website",
        "Online bewerben",
        "Continue",
    ]
    assert report.final_url.endswith("/application_step_2.html")
    assert len(report.uploads) == 2


def test_softgarden_online_bewerben_reaches_form(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    job = _make_job(tmp_path, f"{live_fixture_server}/softgarden_job.html", "softgarden")
    report = _runner(tmp_path).run_in_context(
        context, job, artifact_dir=tmp_path / "softgarden-artifacts"
    )

    _assert_review_ready_without_submit(report, context)
    assert [record.text for record in report.click_path] == ["Online bewerben", "Continue"]


def test_multistep_form_fills_each_page(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    job = _make_job(tmp_path, f"{live_fixture_server}/application_step_1.html", "multistep")
    report = _runner(tmp_path).run_in_context(
        context, job, artifact_dir=tmp_path / "multistep-artifacts"
    )

    _assert_review_ready_without_submit(report, context)
    assert [record.text for record in report.click_path] == ["Continue"]
    labels = {record.label for record in report.fields if record.status == "filled"}
    assert {"First name", "Email address", "Phone number", "Upload CV"} <= labels


@pytest.mark.parametrize(
    ("fixture_name", "expected_reason"),
    [("captcha.html", "captcha_detected"), ("login.html", "login_required")],
)
def test_blocker_pages_stop_without_clicking(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
    fixture_name: str,
    expected_reason: str,
) -> None:
    job = _make_job(tmp_path, f"{live_fixture_server}/{fixture_name}", fixture_name)
    report = _runner(tmp_path).run_in_context(
        context,
        job,
        artifact_dir=tmp_path / f"{fixture_name}-artifacts",
    )

    assert report.status == "needs_user_input"
    assert report.stopped_reason == expected_reason
    assert report.click_path == []
    assert report.submitted is False
