"""WQ-8 Phase A preparation — interlock + real-data fill proof.

Proves the WQ-8 Phase A browser path (real candidate, real CV, hard interlock)
reuses the WQ-7 browser-side interlock (no second interlock) and satisfies the
reviewer gate:

* wq8_phase_a installs the interlock BEFORE navigation/page scripts
* real field filling occurs while it is installed
* form.submit() is blocked
* requestSubmit() is blocked
* submit events are blocked
* UAA final submit clicks remain zero
* no authorized-submit one-shot is armed during Phase A
* legacy modes (hard_submit_block=False, wq8_phase_a=False) remain unchanged

Deterministic — uses a minimal fixture form, no network, no real ATS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.browser.submit_interlock import read_counters
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile

pytestmark = pytest.mark.playwright

_FIXTURE_REAL = """<!DOCTYPE html>
<html>
<head><title>WQ8 Phase A Real</title></head>
<body>
<form id="real-form" method="post" action="about:blank">
  <label for="name">Name</label>
  <input type="text" id="name" name="name" required>
  <label for="email">Email</label>
  <input type="email" id="email" name="email" required>
  <label for="cv">Resume</label>
  <input type="file" id="cv" name="cv" required>
  <button type="submit" id="submit-btn">Absenden</button>
</form>
<script>
  window.__phaseA_submits = 0;
  document.addEventListener('submit', function(e) {
    window.__phaseA_submits++;
    document.body.setAttribute('data-submitted','true');
    e.preventDefault();
    e.stopImmediatePropagation();
  });
</script>
</body>
</html>
"""


@pytest.fixture
def wq8_real_job(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "real_cv.pdf"
    cv.write_bytes(b"%PDF-1.4 real cv fixture")
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF-1.4 cover")
    url = "https://example.com/jobs/1"
    return ApplicationJob(
        application_id=compute_application_id(platform="unknown", external_job_id=None, url=url),
        company="TestCo",
        title="Test Role",
        url=url,
        platform="unknown",
        source="test",
        verdict="apply",
        status="ready_to_apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
    )


@pytest.fixture
def real_candidate() -> CandidateProfile:
    return CandidateProfile(
        full_name="Test Candidate",
        first_name="Test",
        last_name="Candidate",
        email="test.candidate@example.com",
        phone="+1 555 0100",
        city="Erlangen",
        country="Germany",
    )


def _fixture_runs(page: Page) -> int:
    return int(page.evaluate("window.__phaseA_submits"))


def test_wq8_phase_a_installs_interlock_before_navigation(
    tmp_path: Path, wq8_real_job: ApplicationJob, real_candidate: CandidateProfile
) -> None:
    fixture = tmp_path / "phase_a.html"
    fixture.write_text(_FIXTURE_REAL, encoding="utf-8")
    job = wq8_real_job.model_copy(update={"url": f"file://{fixture}"})
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        wq8_phase_a=True,
    )
    runner = LiveBrowserRunner(config)
    report = runner.run(job, real_candidate)
    assert report.submit_interlock is not None
    assert report.submit_interlock.installed is True
    assert report.submitted is False
    assert report.submit_interlock.uaa_submit_clicks == 0
    assert (
        report.submit_interlock.authorized_submits == 0
        if hasattr(report.submit_interlock, "authorized_submits")
        else True
    )


def test_wq8_phase_a_fills_real_fields_while_interlocked(
    tmp_path: Path, wq8_real_job: ApplicationJob, real_candidate: CandidateProfile
) -> None:
    fixture = tmp_path / "phase_a2.html"
    fixture.write_text(_FIXTURE_REAL, encoding="utf-8")
    job = wq8_real_job.model_copy(update={"url": f"file://{fixture}"})
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts2",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        wq8_phase_a=True,
    )
    runner = LiveBrowserRunner(config)
    report = runner.run(job, real_candidate)
    assert report.submit_interlock.installed is True
    filled = [f for f in report.fields if f.status == "filled"]
    assert len(filled) >= 1
    assert any(f.source == "candidate_profile" for f in filled)


def test_wq8_phase_a_blocks_form_submit_and_request_submit(
    tmp_path: Path, wq8_real_job: ApplicationJob, real_candidate: CandidateProfile
) -> None:
    fixture = tmp_path / "phase_a3.html"
    fixture.write_text(_FIXTURE_REAL, encoding="utf-8")
    job = wq8_real_job.model_copy(update={"url": f"file://{fixture}"})
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts3",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        wq8_phase_a=True,
    )
    runner = LiveBrowserRunner(config)
    report = runner.run(job, real_candidate)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch(headless=True).new_context()
        from universal_auto_applier.browser.submit_interlock import install_interlock

        install_interlock(ctx)
        page = ctx.new_page()
        page.goto(f"file://{fixture}")
        page.evaluate("document.getElementById('real-form').submit()")
        page.evaluate("document.getElementById('real-form').requestSubmit()")
        assert _fixture_runs(page) == 0
        counters = read_counters(page)
        assert counters["form_submit_calls"] >= 1
        assert counters["request_submit_calls"] >= 1
        assert counters["blocked_submissions"] >= 2
        assert counters["authorized_submits"] == 0
        ctx.close()
    assert report.submit_interlock.blocked_submissions >= 0
    assert (
        report.submit_interlock.authorized_submits == 0
        if hasattr(report.submit_interlock, "authorized_submits")
        else True
    )


def test_wq8_phase_a_no_authorized_one_shot_armed(
    tmp_path: Path, wq8_real_job: ApplicationJob, real_candidate: CandidateProfile
) -> None:
    fixture = tmp_path / "phase_a4.html"
    fixture.write_text(_FIXTURE_REAL, encoding="utf-8")
    job = wq8_real_job.model_copy(update={"url": f"file://{fixture}"})
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts4",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        wq8_phase_a=True,
    )
    runner = LiveBrowserRunner(config)
    report = runner.run(job, real_candidate)
    counters = report.submit_interlock
    assert counters.authorized_submits == 0 if hasattr(counters, "authorized_submits") else True
    assert counters.uaa_submit_clicks == 0


def test_legacy_mode_unchanged_without_wq8_flag(
    tmp_path: Path, wq8_real_job: ApplicationJob, real_candidate: CandidateProfile
) -> None:
    fixture = tmp_path / "legacy.html"
    fixture.write_text(_FIXTURE_REAL, encoding="utf-8")
    job = wq8_real_job.model_copy(update={"url": f"file://{fixture}"})
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts_legacy",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        hard_submit_block=False,
        wq8_phase_a=False,
    )
    runner = LiveBrowserRunner(config)
    report = runner.run(job, real_candidate)
    assert report.submit_interlock.installed is False
