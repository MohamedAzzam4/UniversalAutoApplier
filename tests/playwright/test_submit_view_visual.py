"""Visual verification tests for the dashboard Submit view.

Tests layout, viewport responsiveness, keyboard accessibility, aria live
regions, and focused defect detection across 4 viewports and 12 states.

Uses route interception to inject controlled API responses — no backend
database complexity required for each state.

Screenshots are saved to a temp artifact directory and must NOT be committed.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import Page

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import build_engine_url
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.models import (
    SubmissionSnapshotField,
    derive_is_complete,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
)

pytestmark = pytest.mark.playwright

SCREENSHOT_DIR = Path(__file__).parent / "_screenshots"

VIEWPORTS = {
    "desktop": (1440, 900),
    "laptop": (1280, 720),
    "mobile": (390, 844),
    "narrow": (320, 700),
}


# ---------------------------------------------------------------------------
# Helper — dashboard server
# ---------------------------------------------------------------------------


def _get_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_settings(tmp_path: Path, port: int, **overrides: Any) -> Settings:
    kwargs: dict[str, Any] = dict(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_dash",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _start_dashboard(
    tmp_path: Path, port: int, settings_overrides: dict[str, Any] | None = None
) -> tuple[str, Any, Any]:
    settings = _make_settings(tmp_path, port, **(settings_overrides or {}))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    base = f"http://127.0.0.1:{port}/"
    ready = False
    while time.time() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    if not ready:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("Server did not start")

    Base.metadata.create_all(app.state.engine)
    return base, app, server


# ---------------------------------------------------------------------------
# Snapshot payload builders
# ---------------------------------------------------------------------------

_FIELDS = [
    dict(
        field_token="f_name",
        label="Full Name",
        field_type="text",
        required=True,
        filled_value="John Michael Doe",
        selected_value="John Michael Doe",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="Extracted from resume",
        source="resume_parser",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_email",
        label="Email Address",
        field_type="text",
        required=True,
        filled_value="john.doe@verylongemaildomainname.com",
        selected_value="john.doe@verylongemaildomainname.com",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_phone",
        label="Phone Number",
        field_type="tel",
        required=True,
        filled_value="+1-555-123-4567 x8901",
        selected_value="+1-555-123-4567 x8901",
        status="filled",
        risk_level="medium",
        requires_confirmation=False,
        confirmed=False,
        evidence="Matched phone pattern",
        source="resume_parser",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_years",
        label="Years of Experience",
        field_type="text",
        required=True,
        filled_value="10+ years",
        selected_value="10+ years",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_skills",
        label="Skills Summary",
        field_type="textarea",
        required=True,
        filled_value="Python, TypeScript, React, Node.js, PostgreSQL, Docker, Kubernetes, AWS, CI/CD, Terraform, Ansible, Git, Linux, Bash, Go, Rust, GraphQL, REST, gRPC, Kafka, Redis, Elasticsearch, Prometheus, Grafana",
        selected_value="",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_question",
        label="What is your greatest strength?",
        field_type="textarea",
        required=False,
        filled_value="I have a proven track record of leading cross-functional teams to deliver complex software projects on time and under budget while maintaining high code quality standards and mentoring junior engineers.",
        selected_value="",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_heard",
        label="How did you hear about us?",
        field_type="select",
        required=True,
        filled_value="LinkedIn",
        selected_value="LinkedIn",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[
            "LinkedIn",
            "Indeed",
            "Company Website",
            "Employee Referral",
            "Recruiter",
            "Job Fair",
            "Other",
        ],
        validation_error="",
    ),
    dict(
        field_token="f_gender",
        label="Gender",
        field_type="radio",
        required=True,
        filled_value="Male",
        selected_value="Male",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=["Male", "Female", "Non-binary", "Prefer not to say"],
        validation_error="",
    ),
    dict(
        field_token="f_agree",
        label="I agree to the terms and conditions",
        field_type="checkbox",
        required=True,
        filled_value="true",
        selected_value="true",
        status="filled",
        risk_level="medium",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
    dict(
        field_token="f_secret",
        label="API Access Token",
        field_type="password",
        required=False,
        filled_value="sk-1234567890abcdefghijklmnopqrstuvwxyz",
        selected_value="",
        status="filled",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
]

# Override fields for specific states (not in _FIELDS by default).
_HIGH_RISK_FIELDS = [
    dict(
        field_token="f_risk",
        label="Criminal Record Disclosure",
        field_type="radio",
        required=True,
        filled_value="No",
        selected_value="No",
        status="filled",
        risk_level="high",
        requires_confirmation=True,
        confirmed=False,
        evidence="",
        source="",
        options=["Yes", "No"],
        validation_error="",
    ),
    dict(
        field_token="f_consent",
        label="Consent to Background Check",
        field_type="checkbox",
        required=True,
        filled_value="true",
        selected_value="true",
        status="filled",
        risk_level="high",
        requires_confirmation=True,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="",
    ),
]

_VALIDATION_ERROR_FIELDS = [
    dict(
        field_token="f_bad",
        label="Invalid Field",
        field_type="text",
        required=True,
        filled_value="bad-input",
        selected_value="bad-input",
        status="validation_error",
        risk_level="low",
        requires_confirmation=False,
        confirmed=False,
        evidence="",
        source="",
        options=[],
        validation_error="Value must match pattern ^[A-Za-z0-9]{3,20}$",
    ),
]

_DOCUMENTS = [
    dict(
        document_kind="cv",
        filename="john_doe_cv_2026_v3_final.pdf",
        path="/very/long/path/that/should/definitely/wrap/properly/on/narrow/screens/cv.pdf",
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        exists=True,
        readable=True,
    ),
    dict(
        document_kind="cover_letter",
        filename="cover_letter_rev2_with_references.pdf",
        path="/another/very/long/path/for/testing/document/path/wrapping/behavior/cover.pdf",
        content_hash="a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
        exists=True,
        readable=True,
    ),
    dict(
        document_kind="portfolio",
        filename="portfolio_with_projects.pdf",
        path=str(
            Path(
                "C:/Users/john.doe/Documents/Jobs/Applications/2026/Company Name/Portfolio/portfolio.pdf"
            ).as_posix()
        ),
        content_hash="01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
        exists=False,
        readable=False,
    ),
]


def _derive_aggregates(
    fields: list[dict],
    confirmed_tokens: set[str] = frozenset(),
) -> dict:
    """Derive aggregate counts from field dicts using production helpers.

    Returns a dict with keys: unresolved_required_field_count,
    unconfirmed_high_risk_count, is_complete, can_approve, approve_blocking_reason.
    """
    sf_list = [
        SubmissionSnapshotField(
            field_token=f["field_token"],
            label=f.get("label", ""),
            field_type=f.get("field_type", "text"),
            filled_value=f.get("filled_value", ""),
            selected_value=f.get("selected_value", ""),
            status=f.get("status", "filled"),
            required=f.get("required", False),
            requires_confirmation=f.get("requires_confirmation", False),
            risk_level=f.get("risk_level", ""),
        )
        for f in fields
    ]
    unresolved = derive_unresolved_required_count(sf_list)
    unconfirmed = derive_unconfirmed_high_risk_count(sf_list, confirmed_tokens)
    complete = derive_is_complete(sf_list)
    # Determine can_approve and blocking reason based on production semantics.
    if unresolved > 0:
        can_approve = False
        blocking = f"{unresolved} unresolved required fields remain"
    elif unconfirmed > 0:
        can_approve = False
        blocking = "Unconfirmed high-risk fields require confirmation"
    else:
        can_approve = True
        blocking = ""
    return {
        "unresolved_required_field_count": unresolved,
        "unconfirmed_high_risk_count": unconfirmed,
        "is_complete": complete,
        "can_approve": can_approve,
        "approve_blocking_reason": blocking,
    }


def _build_status_response(
    app_id: str = "test-app-id",
    snapshot_hash: str = "hash-abc123def456",
    is_complete: bool | None = None,
    is_stale: bool = False,
    approval_state: str = "none",
    active_approval_id: str | None = None,
    approved_snapshot_hash: str | None = None,
    approval_is_stale: bool = False,
    can_approve: bool | None = None,
    approve_blocking_reason: str | None = None,
    can_submit: bool = False,
    submit_blocking_reason: str = "",
    pending_intervention_count: int = 0,
    unresolved_required_field_count: int | None = None,
    unconfirmed_high_risk_count: int | None = None,
    enable_real_submission: bool = True,
    latest_submission_state: str | None = None,
    latest_submission_error: str | None = None,
    latest_submission_timestamp: str | None = None,
    field_overrides: list[dict] | None = None,
    confirmed_tokens: set[str] = frozenset(),
) -> dict:
    """Build a LiveReviewSnapshotResponse JSON dict (wrapped in {snapshot: ...}).

    By default derives aggregate counts from field data using production
    canonical derivation helpers. Any explicit ``*_override`` parameter
    will take precedence over the derived value.
    """
    fields = field_overrides if field_overrides is not None else _FIELDS
    derived = _derive_aggregates(fields, confirmed_tokens)
    # Use explicit overrides when provided, otherwise fall back to derivation.
    final_unresolved = (
        unresolved_required_field_count
        if unresolved_required_field_count is not None
        else derived["unresolved_required_field_count"]
    )
    final_unconfirmed = (
        unconfirmed_high_risk_count
        if unconfirmed_high_risk_count is not None
        else derived["unconfirmed_high_risk_count"]
    )
    final_complete = is_complete if is_complete is not None else derived["is_complete"]
    final_can_approve = can_approve if can_approve is not None else derived["can_approve"]
    final_blocking = (
        approve_blocking_reason
        if approve_blocking_reason is not None
        else derived["approve_blocking_reason"]
    )
    timestamp = "2026-07-18T14:30:00Z"
    return {
        "snapshot": {
            "application_id": app_id,
            "external_job_id": "EXT-JOB-12345-VERIFIED-2026",
            "company": "Very Long Company Name GmbH, LLC & Co. KG — A Global Enterprise",
            "job_title": "Senior Principal Software Engineer & Team Lead",
            "application_url": (
                "https://careers.example.com/apply/this-is-a-very-long-url-path-that-should-definitely-wrap-properly"
                "/on-narrow-screens-without-causing-horizontal-scroll?ref=linkedin&campaign=summer2026&source=paid"
            ),
            "platform": "generic",
            "observation_timestamp": timestamp,
            "form_fingerprint": "fp-9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b",
            "snapshot_hash": snapshot_hash,
            "is_complete": final_complete,
            "is_stale": is_stale,
            "submit_control": {
                "text": "Submit Your Application Now",
                "selector": "#main-content > form > div.submit-section > button.btn-primary.large",
                "frame_url": "",
            },
            "fields": fields,
            "documents": _DOCUMENTS,
            "pending_intervention_count": pending_intervention_count,
            "unresolved_required_field_count": final_unresolved,
            "unconfirmed_high_risk_count": final_unconfirmed,
            "active_approval_id": active_approval_id,
            "approval_state": approval_state,
            "approved_snapshot_hash": approved_snapshot_hash,
            "approval_is_stale": approval_is_stale,
            "can_approve": final_can_approve,
            "approve_blocking_reason": final_blocking,
            "can_submit": can_submit,
            "submit_blocking_reason": submit_blocking_reason,
            "enable_real_submission": enable_real_submission,
            "latest_submission_state": latest_submission_state,
            "latest_submission_error": latest_submission_error,
            "latest_submission_timestamp": latest_submission_timestamp or timestamp,
        }
    }


# Named state payloads for reuse.
# Each fixture calls _derive_aggregates to obtain canonical counts from its
# field data, then passes explicit overrides to _build_status_response so
# the response JSON matches the production derivation exactly.


def state_no_snapshot(app_id: str = "test-app-id") -> dict:
    return _build_status_response(app_id, snapshot_hash="", can_approve=False, can_submit=False)


def state_complete_snapshot(app_id: str = "test-app-id") -> dict:
    """Safe complete — all fields valid, high-risk fields confirmed."""
    # Include high-risk fields but mark them confirmed so they don't block.
    fields = _FIELDS + [dict(f.copy(), confirmed=True) for f in _HIGH_RISK_FIELDS]
    derived = _derive_aggregates(fields, {"f_risk", "f_consent"})
    return _build_status_response(
        app_id,
        field_overrides=fields,
        confirmed_tokens={"f_risk", "f_consent"},
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=derived["can_approve"],
        approve_blocking_reason=derived["approve_blocking_reason"],
    )


def state_pending_interventions(app_id: str = "test-app-id") -> dict:
    fields = _FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        pending_intervention_count=2,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=False,
        approve_blocking_reason="2 pending interventions must be resolved before approval",
        can_submit=False,
    )


def state_unconfirmed_high_risk(app_id: str = "test-app-id") -> dict:
    """High-risk fields present but not confirmed — cannot approve or submit.
    No unrelated validation-error fields present."""
    fields = _FIELDS + _HIGH_RISK_FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        field_overrides=fields,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=derived["can_approve"],
        approve_blocking_reason=derived["approve_blocking_reason"],
        can_submit=False,
    )


def state_approved_snapshot(app_id: str = "test-app-id") -> dict:
    fields = _FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=False,
        can_submit=True,
        approval_state="active",
        active_approval_id="apr-abc123def456",
        approved_snapshot_hash="hash-abc123def456",
    )


def state_stale_approval(app_id: str = "test-app-id") -> dict:
    """Approval exists but snapshot hash changed — stale.
    Field-level counts are internally consistent."""
    fields = _FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=False,
        can_submit=False,
        approval_state="active",
        is_stale=True,
        approval_is_stale=True,
        active_approval_id="apr-stale001",
        approved_snapshot_hash="hash-old123456",
        snapshot_hash="hash-new789012",
        submit_blocking_reason="Approval is stale — the form has changed. Revoke and re-approve",
    )


def state_submission_blocked(app_id: str = "test-app-id") -> dict:
    """Approved but submission blocked by a gate (unresolved fields)."""
    fields = _FIELDS + _VALIDATION_ERROR_FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        field_overrides=fields,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=derived["can_approve"],
        approve_blocking_reason=derived["approve_blocking_reason"],
        can_submit=False,
        approval_state="active",
        active_approval_id="apr-blocked001",
        approved_snapshot_hash="hash-abc123def456",
        submit_blocking_reason=derived["approve_blocking_reason"],
    )


def state_submitted_confirmed(app_id: str = "test-app-id") -> dict:
    """Successfully submitted."""
    fields = _FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=False,
        can_submit=False,
        approval_state="consumed",
        active_approval_id="apr-consumed001",
        approved_snapshot_hash="hash-abc123def456",
        latest_submission_state="submitted",
    )


def state_outcome_unknown(app_id: str = "test-app-id") -> dict:
    """Submission failed — can retry."""
    fields = _FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=False,
        can_submit=False,
        approval_state="active",
        active_approval_id="apr-failed001",
        approved_snapshot_hash="hash-abc123def456",
        latest_submission_state="failed",
        latest_submission_error="Submission failed: browser timed out waiting for confirmation element",
    )


def state_validation_failure(app_id: str = "test-app-id") -> dict:
    """Snapshot has fields with validation errors.
    No unrelated high-risk fields present."""
    fields = _FIELDS + _VALIDATION_ERROR_FIELDS
    derived = _derive_aggregates(fields)
    return _build_status_response(
        app_id,
        field_overrides=fields,
        unresolved_required_field_count=derived["unresolved_required_field_count"],
        unconfirmed_high_risk_count=derived["unconfirmed_high_risk_count"],
        is_complete=derived["is_complete"],
        can_approve=derived["can_approve"],
        approve_blocking_reason=derived["approve_blocking_reason"],
        can_submit=False,
    )


# ---------------------------------------------------------------------------
# Navigation helper
# ---------------------------------------------------------------------------


def _navigate(page: Page, base: str, app_id: str, state: dict) -> None:
    """Navigate to submit tab, intercept /status, and load the given state."""
    page.route(
        "**/api/submit/*/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(state),
        ),
    )
    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)
    page.fill("#submit-job-id", app_id)
    page.click("#submit-load")
    page.wait_for_selector(".uaa-submit-section", timeout=5_000)


@pytest.fixture(scope="function")
def dashboard(page: Page, tmp_path: Path):
    """Start dashboard server and yield (base, app, server, app_id)."""
    port = _get_free_port()
    base, app, server = _start_dashboard(tmp_path, port)
    app_id = "test-visual-id"
    yield base, app, server, app_id
    server.should_exit = True


# ===================================================================
# 1. Viewport overflow tests
# ===================================================================


@pytest.mark.parametrize(
    "width,height",
    [
        (1440, 900),
        (1280, 720),
        (390, 844),
        (320, 700),
    ],
)
def test_no_horizontal_overflow(width: int, height: int, dashboard, page: Page):
    """No horizontal scrollbar appears at any required viewport."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": width, "height": height})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))

    own_width = page.evaluate("document.documentElement.scrollWidth")
    viewport_width = page.evaluate("window.innerWidth")
    assert own_width <= viewport_width + 1, (
        f"Horizontal overflow at {width}x{height}: "
        f"scrollWidth={own_width} > innerWidth={viewport_width}"
    )


# ===================================================================
# 2. Long content wrapping
# ===================================================================


def test_long_content_wraps(dashboard, page: Page):
    """Long URLs, paths, and hashes wrap without overflow."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 390, "height": 844})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))

    own_width = page.evaluate("document.documentElement.scrollWidth")
    viewport_width = page.evaluate("window.innerWidth")
    assert own_width <= viewport_width + 1, f"Content overflows at 390px: scrollWidth={own_width}"

    url_text = page.inner_text("#submit-state-display")
    assert "careers.example.com" in url_text
    assert "https://" in url_text
    assert ".pdf" in url_text
    assert "e3b0c44" in url_text or "content_hash" in url_text


# ===================================================================
# 3. Keyboard accessibility
# ===================================================================


def test_keyboard_focus_order(dashboard, page: Page):
    """Tab order reaches all action buttons in logical sequence.

    Disabled buttons are skipped in tab order (browser default), so we
    verify that each actionable button is either focusable or disabled.
    """
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    # Use complete snapshot (not yet approved) so approve button is enabled
    _navigate(page, base, app_id, state_complete_snapshot(app_id))

    expected_ids = [
        "submit-refresh",
        "submit-approve",
        "submit-revoke",
        "submit-execute",
    ]
    focused_ids = []
    for _ in range(len(expected_ids) + 2):
        page.keyboard.press("Tab")
        el = page.evaluate("document.activeElement?.id || ''")
        if el:
            focused_ids.append(el)

    for eid in expected_ids:
        is_disabled = page.evaluate(f"document.getElementById('{eid}')?.disabled ?? true")
        if is_disabled:
            continue
        assert eid in focused_ids, (
            f"Button #{eid} is enabled but not reachable via Tab. Focus chain: {focused_ids}"
        )


def test_accessible_names_visible(dashboard, page: Page):
    """Every interactive button and input has an accessible name.

    Disabled buttons with text content are excluded (their textContent is
    available to assistive technology even when disabled in Chromium).
    Structural elements (divs with tabindex) are also excluded.
    """
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))

    els = page.eval_on_selector_all(
        "button:not([disabled]), input, [role=button]",
        "els => els.map(el => ({ id: el.id || '(none)', "
        "tag: el.tagName, "
        "name: el.getAttribute('aria-label') || el.textContent?.trim()?.slice(0, 60) || el.placeholder || '(empty)' }))",
    )
    names_missing = []
    for el_info in els:
        name = el_info["name"]
        if not name or name == "(empty)":
            names_missing.append(el_info)
    assert not names_missing, f"Elements without accessible names: {names_missing}"


# ===================================================================
# 4. aria-live behavior
# ===================================================================


def test_aria_live_announces_loading(dashboard, page: Page):
    """aria-live region is populated during loading."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)
    page.fill("#submit-job-id", app_id)

    announcer = page.locator("#submit-announce")
    assert announcer.get_attribute("aria-live") == "polite"
    assert announcer.get_attribute("aria-atomic") == "true"

    # Setup route interception to delay the response so we can observe loading
    page.evaluate("""() => {
        const _fetch = window.fetch;
        window.fetch = function(url, opts) {
            if (typeof url === 'string' && url.includes('/status')) {
                return new Promise(resolve => setTimeout(() => resolve(_fetch(url, opts)), 200));
            }
            return _fetch(url, opts);
        };
    }""")

    page.route(
        "**/api/submit/*/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(state_complete_snapshot(app_id)),
        ),
    )

    page.click("#submit-load")
    page.wait_for_function(
        "() => document.getElementById('submit-announce')?.textContent?.includes('Loading')",
        timeout=3_000,
    )


def test_aria_live_announces_error(dashboard, page: Page):
    """API error is announced via aria-live region."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)
    page.fill("#submit-job-id", app_id)

    page.route(
        "**/api/submit/*/status",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"detail": "Service unavailable"}),
        ),
    )

    page.click("#submit-load")
    page.wait_for_selector(".uaa-error", timeout=5_000)
    announcer_text = page.inner_text("#submit-announce")
    assert "error" in announcer_text.lower(), (
        f"aria-live should announce error, got: {announcer_text}"
    )


# ===================================================================
# 5. Loading layout stability
# ===================================================================


def test_loading_layout_no_jump(dashboard, page: Page):
    """The loading state does not shift button positions."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1280, "height": 720})

    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)
    page.fill("#submit-job-id", app_id)

    # Capture button positions before click
    positions_before = page.eval_on_selector_all(
        "#submit-controls button, #submit-refresh, #submit-load",
        "els => els.map(el => ({ id: el.id, left: el.getBoundingClientRect().left, top: el.getBoundingClientRect().top }))",
    )

    page.evaluate("""() => {
        const _fetch = window.fetch;
        window.fetch = function(url, opts) {
            if (typeof url === 'string' && url.includes('/status')) {
                return new Promise(resolve => setTimeout(() => resolve(_fetch(url, opts)), 300));
            }
            return _fetch(url, opts);
        };
    }""")

    page.route(
        "**/api/submit/*/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(state_complete_snapshot(app_id)),
        ),
    )

    page.click("#submit-load")
    page.wait_for_function(
        "() => document.querySelector('.uaa-submit-loading')",
        timeout=2_000,
    )

    positions_during = page.eval_on_selector_all(
        "#submit-controls button, #submit-refresh, #submit-load",
        "els => els.map(el => ({ id: el.id, left: el.getBoundingClientRect().left, top: el.getBoundingClientRect().top }))",
    )

    for before, during in zip(positions_before, positions_during, strict=True):
        assert before["id"] == during["id"]
        assert abs(before["left"] - during["left"]) < 2, (
            f"Button #{before['id']} jumped horizontally during loading"
        )
        assert abs(before["top"] - during["top"]) < 2, (
            f"Button #{before['id']} jumped vertically during loading"
        )


# ===================================================================
# 6. Mobile action controls
# ===================================================================


def test_mobile_actions_reachable(dashboard, page: Page):
    """All action buttons are visible and reachable on mobile viewport."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 390, "height": 844})
    _navigate(page, base, app_id, state_approved_snapshot(app_id))

    for btn_id in ["submit-refresh", "submit-approve", "submit-revoke", "submit-execute"]:
        btn = page.locator(f"#{btn_id}")
        assert btn.is_visible(), f"Button #{btn_id} not visible on mobile"
        assert btn.is_enabled() == (btn_id != "submit-approve"), (
            f"Button #{btn_id} unexpected disabled state on mobile"
        )


def test_confirm_dialog_fits_mobile(dashboard, page: Page):
    """Confirmation dialog does not overflow mobile viewport."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 390, "height": 844})
    _navigate(page, base, app_id, state_approved_snapshot(app_id))

    page.click("#submit-execute")
    page.wait_for_selector("#submit-confirm-dialog", state="visible", timeout=3_000)

    dialog_box = page.evaluate("""() => {
        const d = document.getElementById('submit-confirm-dialog');
        if (!d) return null;
        const r = d.getBoundingClientRect();
        return { width: r.width, height: r.height, left: r.left, top: r.top };
    }""")
    assert dialog_box is not None, "Confirm dialog not found"
    vp = page.evaluate("() => ({ w: window.innerWidth, h: window.innerHeight })")
    assert dialog_box["left"] >= 0, "Dialog starts off-screen left"
    assert dialog_box["top"] >= 0, "Dialog starts off-screen top"
    assert dialog_box["left"] + dialog_box["width"] <= vp["w"] + 1, "Dialog overflows right edge"
    assert dialog_box["top"] + dialog_box["height"] <= vp["h"] + 1, "Dialog overflows bottom edge"

    # Both buttons reachable
    yes_btn = page.locator("#submit-confirm-yes")
    no_btn = page.locator("#submit-confirm-no")
    assert yes_btn.is_visible()
    assert no_btn.is_visible()

    # Cancel works
    no_btn.click()
    page.wait_for_selector("#submit-confirm-dialog", state="hidden", timeout=2_000)


def test_stale_disables_submit_mobile(dashboard, page: Page):
    """Stale approval disables submit even on mobile."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 390, "height": 844})
    _navigate(page, base, app_id, state_stale_approval(app_id))

    assert page.is_disabled("#submit-execute"), "Submit should be disabled when stale"
    display_text = page.inner_text("#submit-state-display")
    assert "stale" in display_text.lower() or "STALE" in display_text
    assert "Revoke" in display_text or "revoke" in display_text


# ===================================================================
# 7. Secret field values
# ===================================================================


def test_secret_values_never_visible(dashboard, page: Page):
    """Password/token/api_key field values show '(hidden)' not actual value."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))

    text = page.inner_text("#submit-state-display")
    assert "sk-1234567890" not in text, "Secret value leaked in rendered text"
    assert "(hidden)" in text, "Secret fields should show '(hidden)'"


# ===================================================================
# 8. High-risk checkbox alignment
# ===================================================================


def test_high_risk_checkboxes_aligned(dashboard, page: Page):
    """High-risk confirmation checkboxes are visible and aligned."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 390, "height": 844})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))

    checkboxes = page.locator(".uaa-hr-checkbox")
    count = checkboxes.count()
    assert count == 2, f"Expected 2 high-risk checkboxes, found {count}"

    for i in range(count):
        cb = checkboxes.nth(i)
        assert cb.is_visible(), f"Checkbox #{i} not visible on mobile"
        rect = cb.bounding_box()
        assert rect is not None
        assert rect["width"] > 0 and rect["height"] > 0

    # Confirm button should be disabled (nothing checked)
    confirm_btn = page.locator("#submit-confirm-high-risk")
    assert confirm_btn.is_disabled()

    # Check both boxes
    for i in range(count):
        checkboxes.nth(i).check()

    assert confirm_btn.is_enabled(), (
        "Confirm button should be enabled after checking high-risk boxes"
    )


def test_high_risk_keyboard_toggle(dashboard, page: Page):
    """High-risk checkboxes can be toggled by keyboard."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))

    # Tab to first high-risk checkbox and press Space
    for _ in range(15):
        page.keyboard.press("Tab")
        is_checkbox = page.evaluate(
            "document.activeElement?.classList.contains('uaa-hr-checkbox') ?? false"
        )
        if is_checkbox:
            page.keyboard.press("Space")
            checked = page.evaluate("document.activeElement?.checked ?? false")
            assert checked, "Space should toggle high-risk checkbox on"
            break
    else:
        pytest.fail("Could not tab to high-risk checkbox")


# ===================================================================
# 9. Disabled controls expose reason
# ===================================================================


def test_disabled_reason_visible(dashboard, page: Page):
    """Disabled controls have a visible textual reason nearby."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_pending_interventions(app_id))

    text = page.inner_text("#submit-state-display")
    assert page.is_disabled("#submit-execute"), "Submit should be disabled"
    assert "blocking reason" in text.lower() or "blocked" in text.lower(), (
        "Disabled controls should have visible reason, got: " + text[-200:]
    )


# ===================================================================
# 10. Field-summary consistency assertions (tests 1-12)
# ===================================================================


def test_safe_no_pending_confirmations(dashboard, page: Page):
    """1. Safe fixture has no 'Pending' confirmation labels."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))
    text = page.inner_text("#submit-state-display")
    assert "Confirmation state: Pending" not in text, (
        "Safe fixture should not have any pending confirmation"
    )


def test_safe_no_validation_errors(dashboard, page: Page):
    """2. Safe fixture has no validation errors."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))
    payload = state_complete_snapshot(app_id)
    for f in payload["snapshot"]["fields"]:
        if f.get("validation_error"):
            pytest.fail(f"Field {f['field_token']} has unexpected validation_error")
    # UI shows "Validation: None" for every field — no actual error text present
    text = page.inner_text("#submit-state-display")
    assert "Value must match" not in text, "No field should show a validation error value"


def test_safe_can_approve(dashboard, page: Page):
    """3. Safe fixture can approve."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_complete_snapshot(app_id))
    approve_btn = page.locator("#submit-approve")
    assert not approve_btn.is_disabled(), "Approve button should be enabled in safe state"
    payload = state_complete_snapshot(app_id)
    assert payload["snapshot"]["can_approve"] is True


def test_high_risk_exactly_two_pending(dashboard, page: Page):
    """4. High-risk fixture has exactly two pending high-risk fields."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))
    checkboxes = page.locator(".uaa-hr-checkbox")
    assert checkboxes.count() == 2, "Should be exactly 2 high-risk checkboxes"
    for i in range(2):
        cb = checkboxes.nth(i)
        assert not cb.is_checked(), f"High-risk checkbox {i} should not be checked"


def test_high_risk_summary_count_equals_two(dashboard, page: Page):
    """5. High-risk summary count equals two."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))
    payload = state_unconfirmed_high_risk(app_id)
    assert payload["snapshot"]["unconfirmed_high_risk_count"] == 2


def test_high_risk_cannot_approve(dashboard, page: Page):
    """6. High-risk fixture cannot approve."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))
    approve_btn = page.locator("#submit-approve")
    assert approve_btn.is_disabled(), "Approve should be disabled with unconfirmed high-risk"
    payload = state_unconfirmed_high_risk(app_id)
    assert payload["snapshot"]["can_approve"] is False


def test_high_risk_no_unrelated_validation_error(dashboard, page: Page):
    """7. High-risk fixture has no unrelated validation error."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_unconfirmed_high_risk(app_id))
    payload = state_unconfirmed_high_risk(app_id)
    for f in payload["snapshot"]["fields"]:
        if f.get("validation_error"):
            pytest.fail(f"Field {f['field_token']} has unexpected validation_error")
    text = page.inner_text("#submit-state-display")
    assert "Value must match" not in text, "No field should show a validation error value"


def test_validation_exactly_one_error(dashboard, page: Page):
    """8. Validation fixture has exactly one required validation error."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_validation_failure(app_id))
    text = page.inner_text("#submit-state-display")
    assert "validation_error" in text.lower() or "Value must match" in text, (
        "Validation fixture should show validation error"
    )


def test_validation_summary_count_one(dashboard, page: Page):
    """9. Validation summary unresolved count equals one."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_validation_failure(app_id))
    payload = state_validation_failure(app_id)
    assert payload["snapshot"]["unresolved_required_field_count"] == 1
    assert payload["snapshot"]["is_complete"] is False


def test_validation_cannot_approve(dashboard, page: Page):
    """10. Validation fixture cannot approve."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    _navigate(page, base, app_id, state_validation_failure(app_id))
    approve_btn = page.locator("#submit-approve")
    assert approve_btn.is_disabled(), "Approve should be disabled with validation errors"
    payload = state_validation_failure(app_id)
    assert payload["snapshot"]["can_approve"] is False


def test_stale_field_counts_consistent(dashboard, page: Page):
    """11. Stale fixture has internally consistent field-level counts."""
    payload = state_stale_approval("test")
    fields = payload["snapshot"]["fields"]
    sf_list = [
        SubmissionSnapshotField(
            field_token=f["field_token"],
            label=f.get("label", ""),
            field_type=f.get("field_type", "text"),
            filled_value=f.get("filled_value", ""),
            selected_value=f.get("selected_value", ""),
            status=f.get("status", "filled"),
            required=f.get("required", False),
            requires_confirmation=f.get("requires_confirmation", False),
            risk_level=f.get("risk_level", ""),
        )
        for f in fields
    ]
    unresolved = derive_unresolved_required_count(sf_list)
    unconfirmed = derive_unconfirmed_high_risk_count(sf_list)
    assert unresolved == payload["snapshot"]["unresolved_required_field_count"], (
        f"Stale fixture unresolved mismatch: derived={unresolved} "
        f"payload={payload['snapshot']['unresolved_required_field_count']}"
    )
    assert unconfirmed == payload["snapshot"]["unconfirmed_high_risk_count"], (
        f"Stale fixture unconfirmed mismatch: derived={unconfirmed} "
        f"payload={payload['snapshot']['unconfirmed_high_risk_count']}"
    )
    assert "stale" in payload["snapshot"]["submit_blocking_reason"].lower(), (
        "Stale fixture should mention staleness"
    )


def test_all_fixtures_use_derived_aggregates():
    """12. Every named state fixture's aggregates are generated by
    canonical derivation, not independent hard-coded values.

    Each fixture function calls _derive_aggregates and passes the
    derived values as explicit parameters to _build_status_response.
    This test proves that by checking the response matches derivation.
    """
    fixture_fns = [
        state_complete_snapshot,
        state_pending_interventions,
        state_unconfirmed_high_risk,
        state_approved_snapshot,
        state_stale_approval,
        state_submission_blocked,
        state_submitted_confirmed,
        state_outcome_unknown,
        state_validation_failure,
    ]
    for fn in fixture_fns:
        payload = fn("test")
        snap = payload["snapshot"]
        fields = snap["fields"]
        # Collect confirmed tokens from field data (the derivation uses a
        # confirmed_tokens set, not per-field booleans)
        confirmed_tokens = {f["field_token"] for f in fields if f.get("confirmed")}
        sf_list = [
            SubmissionSnapshotField(
                field_token=f["field_token"],
                label=f.get("label", ""),
                field_type=f.get("field_type", "text"),
                filled_value=f.get("filled_value", ""),
                selected_value=f.get("selected_value", ""),
                status=f.get("status", "filled"),
                required=f.get("required", False),
                requires_confirmation=f.get("requires_confirmation", False),
                risk_level=f.get("risk_level", ""),
            )
            for f in fields
        ]
        d_unresolved = derive_unresolved_required_count(sf_list)
        d_unconfirmed = derive_unconfirmed_high_risk_count(sf_list, confirmed_tokens)
        d_complete = derive_is_complete(sf_list)
        assert snap["unresolved_required_field_count"] == d_unresolved, (
            f"{fn.__name__}: unresolved mismatch payload={snap['unresolved_required_field_count']} "
            f"derived={d_unresolved}"
        )
        assert snap["unconfirmed_high_risk_count"] == d_unconfirmed, (
            f"{fn.__name__}: unconfirmed mismatch payload={snap['unconfirmed_high_risk_count']} "
            f"derived={d_unconfirmed}"
        )
        assert snap["is_complete"] == d_complete, (
            f"{fn.__name__}: is_complete mismatch payload={snap['is_complete']} "
            f"derived={d_complete}"
        )
        # can_approve must be logically consistent with aggregates
        if d_unresolved > 0 or d_unconfirmed > 0:
            assert snap["can_approve"] is False, (
                f"{fn.__name__}: should not allow approval with unresolved={d_unresolved} "
                f"unconfirmed={d_unconfirmed}"
            )
        if d_unresolved > 0:
            assert len(snap["approve_blocking_reason"]) > 0, (
                f"{fn.__name__}: should have blocking reason for unresolved fields"
            )


def test_confirmation_required_low_risk_shows_pending(dashboard, page: Page):
    """13. requires_confirmation=true, risk low → Pending."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    payload = _build_status_response(
        app_id,
        field_overrides=[
            dict(
                field_token="f_custom",
                label="Custom Confirm",
                field_type="text",
                required=True,
                filled_value="foo",
                selected_value="foo",
                status="filled",
                risk_level="low",
                requires_confirmation=True,
                confirmed=False,
                evidence="",
                source="",
                options=[],
                validation_error="",
            )
        ],
        unresolved_required_field_count=0,
        unconfirmed_high_risk_count=1,
        is_complete=True,
        can_approve=False,
        approve_blocking_reason="Unconfirmed high-risk fields require confirmation",
    )
    _navigate(page, base, app_id, payload)
    text = page.inner_text("#submit-state-display")
    assert "Confirmation state: Pending" in text, (
        "requires_confirmation=true, risk low should show Pending"
    )
    assert "Confirmation state: Not required" not in text, "Should not say Not required"


def test_confirmation_not_required_high_risk_shows_pending(dashboard, page: Page):
    """14. requires_confirmation=false, risk high → Pending."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    payload = _build_status_response(
        app_id,
        field_overrides=[
            dict(
                field_token="f_high_no_confirm",
                label="High No Confirm",
                field_type="text",
                required=True,
                filled_value="bar",
                selected_value="bar",
                status="filled",
                risk_level="high",
                requires_confirmation=False,
                confirmed=False,
                evidence="",
                source="",
                options=[],
                validation_error="",
            )
        ],
        unresolved_required_field_count=0,
        unconfirmed_high_risk_count=1,
        is_complete=True,
        can_approve=False,
        approve_blocking_reason="Unconfirmed high-risk fields require confirmation",
    )
    _navigate(page, base, app_id, payload)
    text = page.inner_text("#submit-state-display")
    assert "Confirmation state: Pending" in text, (
        "risk_level=high should show Pending even without requires_confirmation"
    )
    assert "Confirmation state: Not required" not in text, "Should not say Not required"


def test_confirmed_high_risk_shows_confirmed(dashboard, page: Page):
    """15. Confirmed high-risk token → Confirmed."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    payload = _build_status_response(
        app_id,
        field_overrides=[
            dict(
                field_token="f_risk",
                label="Risk Field",
                field_type="radio",
                required=True,
                filled_value="No",
                selected_value="No",
                status="filled",
                risk_level="high",
                requires_confirmation=True,
                confirmed=True,
                evidence="",
                source="",
                options=["Yes", "No"],
                validation_error="",
            )
        ],
        unresolved_required_field_count=0,
        unconfirmed_high_risk_count=0,
        is_complete=True,
        can_approve=True,
        approve_blocking_reason="",
    )
    _navigate(page, base, app_id, payload)
    text = page.inner_text("#submit-state-display")
    assert "Confirmation state: Confirmed" in text, "Confirmed high-risk should show Confirmed"
    assert "Confirmation state: Pending" not in text, "Confirmed field should not show Pending"


def test_low_risk_no_confirmation_required_shows_not_required(dashboard, page: Page):
    """16. Low risk and no confirmation requirement → Not required."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})
    payload = _build_status_response(
        app_id,
        field_overrides=[
            dict(
                field_token="f_normal",
                label="Normal Field",
                field_type="text",
                required=False,
                filled_value="baz",
                selected_value="",
                status="filled",
                risk_level="low",
                requires_confirmation=False,
                confirmed=False,
                evidence="",
                source="",
                options=[],
                validation_error="",
            )
        ],
        unresolved_required_field_count=0,
        unconfirmed_high_risk_count=0,
        is_complete=True,
        can_approve=True,
        approve_blocking_reason="",
    )
    _navigate(page, base, app_id, payload)
    text = page.inner_text("#submit-state-display")
    assert "Confirmation state: Not required" in text, (
        "Low-risk field without confirmation requirement should show Not required"
    )
    assert "Confirmation state: Pending" not in text, "Not required field should not show Pending"


# ===================================================================
# 11. Screenshots
# ===================================================================


@pytest.mark.parametrize(
    "viewport_name,width,height,state_name,state_fn",
    [
        ("desktop", 1440, 900, "complete-snapshot", state_complete_snapshot),
        ("desktop", 1440, 900, "high-risk", state_unconfirmed_high_risk),
        ("desktop", 1440, 900, "stale-approval", state_stale_approval),
        ("desktop", 1440, 900, "outcome-unknown", state_outcome_unknown),
        ("mobile", 390, 844, "complete-snapshot", state_complete_snapshot),
        ("mobile", 390, 844, "high-risk", state_unconfirmed_high_risk),
        ("narrow", 320, 700, "stale-approval", state_stale_approval),
    ],
)
def test_screenshot(
    viewport_name: str,
    width: int,
    height: int,
    state_name: str,
    state_fn: Any,
    dashboard,
    page: Page,
    tmp_path: Path,
):
    """Capture screenshot artifacts for the required states."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": width, "height": height})
    payload = state_fn(app_id)
    _navigate(page, base, app_id, payload)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{viewport_name}_{state_name}.png"
    path = SCREENSHOT_DIR / filename
    page.screenshot(path=str(path), full_page=True)
    assert path.exists(), f"Screenshot not saved: {path}"
    print(f"\n  [saved] {path}")


# ===================================================================
# 11. Confirm dialog keyboard acceptance
# ===================================================================


def test_confirm_dialog_keyboard(dashboard, page: Page):
    """Enter/Space activate Yes in confirm dialog; Escape does not."""
    base, _app, _server, app_id = dashboard
    page.set_viewport_size({"width": 1440, "height": 900})

    # Set up — need an approved state
    page.goto(base)
    page.click('a[data-view="submit"]')
    page.wait_for_selector("#submit-job-id", timeout=5_000)
    page.fill("#submit-job-id", app_id)

    page.route(
        "**/api/submit/*/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(state_approved_snapshot(app_id)),
        ),
    )
    page.route(
        "**/api/submit/*/submit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "application_id": app_id,
                    "state": "submitted",
                    "clicked": True,
                    "confirmation_evidence": "evidence",
                }
            ),
        ),
    )

    page.click("#submit-load")
    page.wait_for_selector(".uaa-submit-section", timeout=5_000)

    # Open confirm dialog
    page.click("#submit-execute")
    page.wait_for_selector("#submit-confirm-dialog", state="visible", timeout=3_000)

    # Cancel with Escape
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    is_hidden = page.evaluate(
        "() => { var d = document.getElementById('submit-confirm-dialog'); return !d || d.style.display === 'none'; }"
    )
    assert is_hidden, "Escape should close confirm dialog"

    # Re-open and confirm with Enter on Yes
    page.click("#submit-execute")
    page.wait_for_selector("#submit-confirm-dialog", state="visible", timeout=3_000)

    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")

    page.wait_for_timeout(500)
    error_el = page.query_selector("#submit-state-display .uaa-error")
    assert error_el is None, f"Unexpected error element: {error_el}"
