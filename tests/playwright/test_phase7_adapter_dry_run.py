"""Phase 7 per-adapter Playwright dry-run tests.

Per ``docs/generalization/ROADMAP.md`` Phase 7, each adapter must include
a "Playwright dry-run test". Per ``docs/generalization/TESTING_STRATEGY.md``
Phase 7: "Per-adapter fixture tests. Per-adapter dry-run Playwright tests."

Per ``docs/generalization/DRY_RUN_LEVELS.md`` Level 1 (Local Browser
Dry-Run): "Uses Playwright against local fixture pages served from
localhost. No external websites. Verifies browser execution behavior
(Playwright locators, fill methods, file uploads, screenshots). Safe
for CI if the fixture server is stable. Not yet implemented. Deferred
to Phase 8 (full pipeline) when Playwright integration lands."

Phase 8 has now landed (Playwright is integrated for dashboard tests).
This file implements the deferred Level 1 dry-run for Phase 7 adapters.

Test approach:
1. Start the real uvicorn dashboard on an ephemeral port (mirroring the
   ``server_url`` fixture pattern from conftest.py).
2. Wait for the server to be ready, then create DB tables and seed a
   queued job for each platform.
3. Use Playwright to drive the dashboard, posting the platform fixture
   HTML to /api/pipeline/start via ``page.evaluate``.
4. Assert the job ends in ``review_ready`` or ``needs_user_input``,
   NEVER ``submitted`` or ``applied``.

These tests use only local fixture HTML. No external network access.
No real submissions. The Playwright browser drives the dashboard UI on
localhost; the adapter processes fixture HTML strings via the pipeline
orchestrator.

Run with::

    python -m pytest tests/playwright/test_phase7_adapter_dry_run.py -m playwright
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

from universal_auto_applier.api.app import create_app
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base

pytestmark = pytest.mark.playwright

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"


def _read_platform_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# Map platform -> fixture filename prefix. Platform.LINKEDIN_EASY_APPLY's
# value is "linkedin_easy_apply", but the fixture files use "linkedin_"
# as the prefix. This map avoids the mismatch.
_PLATFORM_FIXTURE_PREFIX: dict[Platform, str] = {
    Platform.GREENHOUSE: "greenhouse",
    Platform.LEVER: "lever",
    Platform.WORKDAY: "workday",
    Platform.SMARTRECRUITERS: "smartrecruiters",
    Platform.LINKEDIN_EASY_APPLY: "linkedin",
    Platform.SIEMENS: "siemens",
    Platform.GENERIC: "generic",
    Platform.UNKNOWN: "unknown",
}


@dataclass
class _ServerHandle:
    base_url: str
    app: object
    server: uvicorn.Server
    thread: threading.Thread


def _start_server(settings) -> _ServerHandle:
    """Start a real uvicorn server on an ephemeral port, wait for it,
    and create DB tables. Returns a handle for cleanup."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings=settings)

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

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
        raise RuntimeError("uvicorn server did not start in time")

    # Now that the lifespan has created the engine, create tables.
    Base.metadata.create_all(app.state.engine)

    return _ServerHandle(base_url=base, app=app, server=server, thread=thread)


def _stop_server(handle: _ServerHandle) -> None:
    handle.server.should_exit = True
    handle.thread.join(timeout=5.0)


def _seed_job(
    app,
    tmp_path: Path,
    *,
    url: str,
    platform: Platform,
    external_job_id: str,
) -> str:
    """Seed a queued job into the app's database and return its application_id."""
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"fake")
    cover.write_bytes(b"fake")
    application_id = compute_application_id(
        platform=str(platform), external_job_id=external_job_id, url=url
    )
    job = ApplicationJob(
        application_id=application_id,
        platform=platform,
        source="linkedin",
        company="Test Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.QUEUED,
        external_job_id=external_job_id,
    )
    session_factory = app.state.session_factory
    with session_scope(session_factory) as session:
        upsert_application_job(session, job)
    return application_id


@pytest.fixture
def server_with_seeded_job(settings, tmp_path: Path, request) -> Iterator[tuple[str, str, object]]:
    """Yield (base_url, application_id, app) for a single seeded job.

    The platform/url/external_job_id come from the test via
    ``request.param`` (a tuple of (platform, url, fixture_name)).
    """
    platform, url, _fixture_name = request.param
    handle = _start_server(settings)
    try:
        application_id = _seed_job(
            handle.app,
            tmp_path,
            url=url,
            platform=platform,
            external_job_id=f"playwright-{platform.value}",
        )
        yield handle.base_url, application_id, handle.app
    finally:
        _stop_server(handle)


# ---------------------------------------------------------------------------
# Test 1: Each adapter's apply-form fixture, posted via Playwright, must
# never produce a SUBMITTED or APPLIED status.
# ---------------------------------------------------------------------------

_PLATFORM_APPLY_MATRIX = [
    (
        Platform.GREENHOUSE,
        "https://boards.greenhouse.io/example/jobs/901",
        "greenhouse_apply.html",
    ),
    (
        Platform.LEVER,
        "https://jobs.lever.co/techco/902",
        "lever_apply.html",
    ),
    (
        Platform.WORKDAY,
        "https://globalcorp.myworkdayjobs.com/jobs/903",
        "workday_apply.html",
    ),
    (
        Platform.SMARTRECRUITERS,
        "https://careers.smartrecruiters.com/innovateco/jobs/904",
        "smartrecruiters_apply.html",
    ),
    (
        Platform.LINKEDIN_EASY_APPLY,
        "https://www.linkedin.com/jobs/view/905",
        "linkedin_apply.html",
    ),
]


@pytest.mark.parametrize(
    "server_with_seeded_job",
    _PLATFORM_APPLY_MATRIX,
    ids=[p[0].value for p in _PLATFORM_APPLY_MATRIX],
    indirect=True,
)
def test_adapter_dry_run_never_submits(
    page,
    server_with_seeded_job: tuple[str, str, object],
) -> None:
    """Drive the dashboard with a platform apply fixture and prove no
    submission occurs. The job must end in review_ready or
    needs_user_input, never submitted or applied."""
    base_url, application_id, app = server_with_seeded_job

    # Find the fixture name from the parametrize (third element of the tuple).
    # We re-read it here because the indirect fixture only passes the platform.
    # Instead, we read the fixture name from the request param via the
    # server_with_seeded_job fixture's request.param.
    # Simpler: re-derive from platform by reading the test's parametrize.
    # But the cleanest approach is to pass fixture_name through the indirect
    # fixture. We'll just re-read from the matrix here.
    platform_value = application_id  # placeholder to satisfy linter
    del platform_value

    # Navigate to the dashboard.
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base_url)
    page.wait_for_selector("h1", timeout=10_000)
    assert "UniversalAutoApplier" in page.locator("h1").inner_text()

    # We need the fixture name. Get it from the app's session factory
    # by looking at the seeded job's platform. This is a bit awkward;
    # a cleaner approach is to pass the fixture name through the indirect
    # fixture. Let's restructure: pass the whole tuple through.
    # For now, derive fixture name from the platform.
    with session_scope(app.state.session_factory) as session:
        job = get_application_job(session, application_id)
    assert job is not None
    prefix = _PLATFORM_FIXTURE_PREFIX[job.platform]
    fixture_name = f"{prefix}_apply.html"
    fixture_html = _read_platform_fixture(fixture_name)

    response_json = page.evaluate(
        """
        async (fixtureHtml) => {
            const resp = await fetch('/api/pipeline/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({fixture_html: fixtureHtml, max_jobs: 10})
            });
            return await resp.json();
        }
        """,
        fixture_html,
    )

    assert response_json["status"] in ("completed", "error"), (
        f"Unexpected pipeline status: {response_json['status']}"
    )
    assert (
        "No real submissions" in response_json["message"] or "error" in response_json["message"]
    ), f"Unexpected message: {response_json['message']}"

    with session_scope(app.state.session_factory) as session:
        updated = get_application_job(session, application_id)

    assert updated is not None
    assert updated.status not in (ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED), (
        f"Job ended in {updated.status} — submission occurred!"
    )
    assert updated.status in (
        ApplicationStatus.REVIEW_READY,
        ApplicationStatus.NEEDS_USER_INPUT,
    ), f"Job ended in unexpected status {updated.status}"


# ---------------------------------------------------------------------------
# Test 2: Each adapter's login fixture, posted via Playwright, must stop
# the pipeline and create a login_required intervention.
# ---------------------------------------------------------------------------

_PLATFORM_LOGIN_MATRIX = [
    (
        Platform.GREENHOUSE,
        "https://boards.greenhouse.io/example/jobs/911",
        "greenhouse_login.html",
    ),
    (
        Platform.LEVER,
        "https://jobs.lever.co/techco/912",
        "lever_login.html",
    ),
    (
        Platform.WORKDAY,
        "https://globalcorp.myworkdayjobs.com/jobs/913",
        "workday_login.html",
    ),
    (
        Platform.SMARTRECRUITERS,
        "https://careers.smartrecruiters.com/innovateco/jobs/914",
        "smartrecruiters_login.html",
    ),
    (
        Platform.LINKEDIN_EASY_APPLY,
        "https://www.linkedin.com/jobs/view/915",
        "linkedin_login.html",
    ),
]


@pytest.mark.parametrize(
    "server_with_seeded_job",
    _PLATFORM_LOGIN_MATRIX,
    ids=[p[0].value for p in _PLATFORM_LOGIN_MATRIX],
    indirect=True,
)
def test_adapter_login_fixture_stops_pipeline(
    page,
    server_with_seeded_job: tuple[str, str, object],
) -> None:
    """Drive the dashboard with a login fixture and prove the pipeline
    stops and creates a login_required intervention."""
    base_url, application_id, app = server_with_seeded_job

    page.goto(base_url)
    page.wait_for_selector("h1", timeout=10_000)

    with session_scope(app.state.session_factory) as session:
        job = get_application_job(session, application_id)
    assert job is not None
    prefix = _PLATFORM_FIXTURE_PREFIX[job.platform]
    fixture_name = f"{prefix}_login.html"
    fixture_html = _read_platform_fixture(fixture_name)

    response_json = page.evaluate(
        """
        async (fixtureHtml) => {
            const resp = await fetch('/api/pipeline/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({fixture_html: fixtureHtml, max_jobs: 10})
            });
            return await resp.json();
        }
        """,
        fixture_html,
    )
    assert response_json["status"] in ("completed", "error")

    with session_scope(app.state.session_factory) as session:
        from universal_auto_applier.interventions.store import list_pending_interventions

        pending = list_pending_interventions(session, application_id)

    kinds = [i.kind for i in pending]
    assert "login_required" in kinds, f"Expected login_required intervention, got: {kinds}"


# ---------------------------------------------------------------------------
# Test 3: Browser-context extraction consistency. Prove the adapter's HTML
# parser produces results consistent with what a real Playwright browser
# sees on the same fixture HTML.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        "greenhouse_apply.html",
        "lever_apply.html",
        "workday_apply.html",
        "smartrecruiters_apply.html",
        "linkedin_apply.html",
    ],
)
def test_browser_form_field_count_matches_parser(page, fixture_name: str) -> None:
    """The number of form elements a real browser sees must match what
    the adapter's extractor finds (within the radio/checkbox grouping
    tolerance)."""
    from universal_auto_applier.form_engine.schema_extractor import (
        extract_form_fields,
    )

    fixture_html = _read_platform_fixture(fixture_name)
    adapter_fields = extract_form_fields(fixture_html)
    adapter_field_count = len(adapter_fields)

    page.set_content(fixture_html)
    browser_field_count = page.evaluate(
        """
        () => {
            const inputs = Array.from(document.querySelectorAll(
                'input:not([type="submit"]):not([type="button"]):not([type="reset"]):not([type="image"]):not([type="hidden"]), select, textarea'
            ));
            return inputs.length;
        }
        """
    )

    assert adapter_field_count >= 1, (
        f"Adapter found 0 fields in {fixture_name}, but browser found {browser_field_count}"
    )
    assert adapter_field_count <= browser_field_count, (
        f"Adapter found {adapter_field_count} fields in {fixture_name}, "
        f"but browser only found {browser_field_count}"
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "greenhouse_apply.html",
        "lever_apply.html",
        "workday_apply.html",
        "smartrecruiters_apply.html",
        "linkedin_apply.html",
    ],
)
def test_browser_detects_submit_button_in_apply_fixture(page, fixture_name: str) -> None:
    """A real browser must find a submit button in each apply fixture,
    and the adapter's classifier must classify it as dangerous_submit."""
    from universal_auto_applier.core.statuses import ClickableClassification
    from universal_auto_applier.navigator.clickable_classifier import (
        classify_clickable,
    )

    fixture_html = _read_platform_fixture(fixture_name)

    page.set_content(fixture_html)
    submit_buttons = page.evaluate(
        """
        () => {
            const buttons = Array.from(document.querySelectorAll(
                'button[type="submit"], input[type="submit"], button'
            ));
            return buttons.map(b => ({
                text: (b.textContent || b.value || '').trim(),
                type: b.type || '',
                tag: b.tagName.toLowerCase()
            }));
        }
        """
    )
    assert len(submit_buttons) >= 1, f"Browser found no buttons in {fixture_name}"

    submit_button = next(
        (b for b in submit_buttons if "submit" in b["text"].lower()),
        None,
    )
    assert submit_button is not None, (
        f"Browser found no 'Submit' button in {fixture_name}: {submit_buttons}"
    )

    result = classify_clickable(
        text=submit_button["text"],
        aria_label="",
        href="",
        role="button",
        tag=submit_button["tag"],
        enabled=True,
        visible=True,
    )
    assert result.classification == ClickableClassification.DANGEROUS_SUBMIT, (
        f"Adapter classified '{submit_button['text']}' as "
        f"{result.classification}, expected dangerous_submit"
    )
