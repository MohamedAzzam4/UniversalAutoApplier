"""Local Playwright fixtures for real navigation, filling, and uploads."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.question_models import (
    AnswerCandidate,
    AnswerEvidence,
    ApplicationQuestion,
    QuestionCategory,
    QuestionResolution,
    QuestionRisk,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.live_executor import (
    execute_live_form,
    execute_live_form_with_llm,
)
from universal_auto_applier.submission.models import build_snapshot_from_report

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


def test_invisible_recaptcha_badge_is_not_a_captcha_blocker(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    """Greenhouse's universal off-screen reCAPTCHA badge must not block.

    Every Greenhouse job board renders a fixed ``grecaptcha-badge``
    (``size=invisible``) even though there is no challenge the applicant
    must solve. Playwright reports that iframe as visible because it has a
    non-empty bounding box, which used to misclassify every Greenhouse form
    as ``captcha_detected``. The badge must not stop navigation; only a
    real user-facing challenge widget still does.
    """
    job = _make_job(
        tmp_path,
        f"{live_fixture_server}/invisible_recaptcha_badge.html",
        "invisible-recaptcha-badge",
    )
    report = _runner(tmp_path).run_in_context(
        context,
        job,
        artifact_dir=tmp_path / "invisible-badge-artifacts",
    )

    assert report.status == "review_ready", report.model_dump_json(indent=2)
    assert report.stopped_reason == "final_submit_detected"
    assert report.click_path == []
    assert report.submitted is False
    assert report.fields, "the form must have been filled despite the badge"


def test_visible_recaptcha_widget_still_blocks_after_badge_fix(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    """A genuinely user-facing reCAPTCHA challenge still stops the run.

    The invisible-badge fix must not weaken real captcha detection. A page
    with a visible ``.g-recaptcha`` challenge is still a blocker.
    """
    job = _make_job(tmp_path, f"{live_fixture_server}/captcha.html", "visible-captcha")
    report = _runner(tmp_path).run_in_context(
        context,
        job,
        artifact_dir=tmp_path / "visible-captcha-artifacts",
    )

    assert report.status == "needs_user_input"
    assert report.stopped_reason == "captcha_detected"
    assert report.click_path == []
    assert report.submitted is False


def test_lever_cards_named_groups_are_not_a_payment_wall(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    """Lever's ``cards[<uuid>][fieldN]`` section names must not be payment.

    Lever names every application section ``cards[...]``. The payment-wall
    detector used to match ``input[name*='card' i]`` and misclassified every
    Lever form as ``payment_required``. The ``cards[`` array-group naming
    is a form-structure convention, not a payment field.
    """
    job = _make_job(
        tmp_path,
        f"{live_fixture_server}/lever_cards_groups.html",
        "lever-cards-groups",
    )
    report = _runner(tmp_path).run_in_context(
        context,
        job,
        artifact_dir=tmp_path / "lever-cards-artifacts",
    )

    assert report.status == "review_ready", report.model_dump_json(indent=2)
    assert report.stopped_reason == "final_submit_detected"
    assert report.click_path == []
    assert report.submitted is False
    assert report.fields, "the Lever-style form must have been filled"


def test_real_card_field_still_blocks_after_cards_fix(
    context: BrowserContext,
    live_fixture_server: str,
    tmp_path: Path,
) -> None:
    """A genuine payment-card field is still reported as ``payment_required``."""
    job = _make_job(tmp_path, f"{live_fixture_server}/payment_wall.html", "payment-wall")
    report = _runner(tmp_path).run_in_context(
        context,
        job,
        artifact_dir=tmp_path / "payment-wall-artifacts",
    )

    assert report.status == "needs_user_input"
    assert report.stopped_reason == "payment_required"
    assert report.click_path == []
    assert report.submitted is False


def test_cleared_required_text_fill_is_intervention_not_verified(
    page: Page,
    tmp_path: Path,
) -> None:
    page.set_content(
        """<form>
          <label for="email">Email address</label>
          <input id="email" name="email" type="email" required
                 onblur="this.value = '';">
        </form>"""
    )
    job = _make_job(tmp_path, "http://uaa.test/form", "cleared-readback")

    execution = execute_live_form(
        page,
        CandidateProfile(email="applicant@example.com"),
        job,
    )

    assert len(execution.fields) == 1
    field = execution.fields[0]
    assert field.status == "intervention_needed"
    assert field.required is True
    assert field.proposed_answer == "applicant@example.com"
    assert field.selected_value == ""
    assert field.filled_value == ""
    assert execution.required_unresolved == 1
    assert any("read-back validation failed" in error for error in execution.validation_errors)

    snapshot = build_snapshot_from_report(
        application_id=job.application_id,
        application_url=job.url,
        fields=execution.fields,
        uploads=execution.uploads,
        pending_intervention_count=execution.required_unresolved,
    )
    assert snapshot.fields[0].required is True
    assert snapshot.fields[0].status == "intervention_needed"
    assert snapshot.fields[0].filled_value == ""
    assert snapshot.unresolved_required_field_count == 1


def test_llm_cleared_required_text_fill_remains_unresolved(
    page: Page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page.set_content(
        """<form>
          <label for="motivation">Why do you want to work here?</label>
          <textarea id="motivation" name="motivation" required
                    onblur="this.value = '';">
          </textarea>
        </form>"""
    )
    job = _make_job(tmp_path, "http://uaa.test/form", "llm-cleared-readback")
    proposed_answer = AnswerCandidate(
        value="I am interested in the role.",
        confidence=0.9,
        evidence=[
            AnswerEvidence(
                source="cover_letter_markdown",
                fact="The candidate expressed interest in the role.",
            )
        ],
        source_type="llm_grounded",
        explanation="Grounded in the candidate's cover letter.",
    )
    question = ApplicationQuestion(
        question_text="Why do you want to work here?",
        field_selector="#motivation",
        field_type="textarea",
        required=True,
    )
    resolution = QuestionResolution(
        question=question,
        category=QuestionCategory.JOB_SPECIFIC_MOTIVATION,
        risk_level=QuestionRisk.MEDIUM,
        proposed_answer=proposed_answer,
    )

    from universal_auto_applier.llm import question_resolver

    monkeypatch.setattr(
        question_resolver,
        "resolve_question",
        lambda *_args, **_kwargs: resolution,
    )
    execution = execute_live_form_with_llm(
        page,
        CandidateProfile(),
        job,
        qa_service=object(),
    )

    assert len(execution.fields) == 1
    field = execution.fields[0]
    assert field.status == "intervention_needed"
    assert field.required is True
    assert field.proposed_answer == proposed_answer.value
    assert field.selected_value == ""
    assert field.filled_value == ""
    assert execution.required_unresolved == 1
    assert any("read-back validation failed" in error for error in execution.validation_errors)

    snapshot = build_snapshot_from_report(
        application_id=job.application_id,
        application_url=job.url,
        fields=execution.fields,
        uploads=execution.uploads,
        pending_intervention_count=execution.required_unresolved,
    )
    assert snapshot.fields[0].required is True
    assert snapshot.fields[0].status == "intervention_needed"
    assert snapshot.fields[0].filled_value == ""
    assert snapshot.unresolved_required_field_count == 1


def test_compatible_email_normalization_records_dom_readback(
    page: Page,
    tmp_path: Path,
) -> None:
    page.set_content(
        """<form>
          <label for="email">Email address</label>
          <input id="email" name="email" type="email" required
                 onblur="this.value = this.value.toLowerCase();">
        </form>"""
    )
    job = _make_job(tmp_path, "http://uaa.test/form", "normalized-readback")
    actual_email = "applicant@example.com"

    execution = execute_live_form(
        page,
        CandidateProfile(email=actual_email.upper()),
        job,
    )

    assert len(execution.fields) == 1
    field = execution.fields[0]
    assert field.status == "filled"
    assert field.required is True
    assert field.selected_value == actual_email
    assert field.filled_value == actual_email
    assert page.locator("#email").input_value() == actual_email
    assert execution.required_unresolved == 0

    snapshot = build_snapshot_from_report(
        application_id=job.application_id,
        application_url=job.url,
        fields=execution.fields,
        uploads=execution.uploads,
        pending_intervention_count=execution.required_unresolved,
    )
    assert snapshot.fields[0].required is True
    assert snapshot.fields[0].filled_value == actual_email
    assert snapshot.fields[0].selected_value == actual_email
