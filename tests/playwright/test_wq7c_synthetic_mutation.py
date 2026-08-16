"""WQ-7C synthetic mutation tests: fill a live form, never submit.

These tests prove that the opt-in synthetic mutation path (the SAME
production code used by the ``live-synthetic-mutation`` CLI):

1. Fills Greenhouse/Lever-style fixture forms with the dedicated WQ-7C
   synthetic identity and approved synthetic documents only.
2. NEVER enters a value the synthetic identity does not declare.
3. NEVER uploads a document whose SHA-256 is not in the approved set.
4. NEVER clicks the final submit control, never triggers form.submit(),
   and keeps the browser-side submit interlock armed the whole run.
5. Records a frozen, hash-verifiable mutation plan BEFORE any mutation.

Tests use the REAL production path ``LiveBrowserRunner.run_in_context_
synthetic`` against local HTTP-served fixtures (no live ATS sites).
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
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.synthetic_profile import (
    SyntheticMutationProfile,
    approved_document_hashes,
    create_synthetic_mutation_documents,
)

pytestmark = pytest.mark.playwright

PLATFORM_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"


@pytest.fixture(scope="module")
def platform_dir(tmp_path_factory) -> Path:
    """Build a temp fixture dir served by the shared fixture server.

    The fixture server's readiness probe GETs ``/conditional_reveal.html``,
    so that marker file (plus the two apply forms we actually exercise)
    must live in the served directory.
    """
    target = tmp_path_factory.mktemp("wq7c-platforms")
    (target / "conditional_reveal.html").write_text(
        "<html><body>ready</body></html>", encoding="utf-8"
    )
    for name in ("greenhouse_apply.html", "lever_apply.html"):
        (target / name).write_bytes((PLATFORM_FIXTURE_DIR / name).read_bytes())
    return target


@pytest.fixture(scope="module")
def platform_server(platform_dir: Path) -> str:
    yield from serve_fixture_dir(platform_dir)


def _make_job(url: str, tmp_path: Path) -> ApplicationJob:
    cv, cover = create_synthetic_mutation_documents(tmp_path / "docs")
    return ApplicationJob(
        application_id=compute_application_id(
            platform="generic", external_job_id="wq7c-fixture", url=url
        ),
        platform=Platform.GENERIC,
        source="wq7c_fixture",
        company="Example Corp",
        title="Software Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="wq7c-fixture",
        metadata={
            "candidate_profile": {
                "first_name": "Test",
                "last_name": "Candidate",
                "full_name": "Test Candidate",
                "email": "test.candidate@example.com",
                "phone": "+1 555 0199",
                "synthetic_test": True,
                "wq7_synthetic": True,
            },
        },
    )


def _approved(tmp_path: Path) -> frozenset[str]:
    cv, cover = create_synthetic_mutation_documents(tmp_path / "docs")
    return approved_document_hashes(cv, cover)


def _config(tmp_path: Path) -> LiveBrowserConfig:
    return LiveBrowserConfig(
        artifacts_root=tmp_path / "live-runs",
        headless=True,
        timeout_ms=10_000,
        max_steps=5,
        capture_trace=True,
        hard_submit_block=True,  # interlock armed before any mutation
    )


PROFILE = SyntheticMutationProfile()


class TestGreenhouseSyntheticMutation:
    def test_fills_greenhouse_like_form_and_stops_before_submit(
        self, context: BrowserContext, platform_server: str, tmp_path: Path
    ) -> None:
        job = _make_job(f"{platform_server}/greenhouse_apply.html", tmp_path)
        runner = LiveBrowserRunner(_config(tmp_path))
        report = runner.run_in_context_synthetic(
            context,
            job,
            PROFILE,
            approved_document_hashes=_approved(tmp_path),
            mutation_budget=20,
            artifact_dir=tmp_path / "run-greenhouse",
        )

        assert report.status == "review_ready", (
            f"got status={report.status} reason={report.stopped_reason} errors={report.errors}"
        )
        assert report.stopped_reason == "final_submit_detected"
        assert report.submitted is False, "synthetic mutation must never submit"

        # Declared identity facts were entered.
        assert len(report.fields) >= 4
        filled = {f.label for f in report.fields if f.status == "filled"}
        assert {"First name", "Last name", "Email address", "Phone number"} <= filled

        # Approved synthetic documents were uploaded (resume + cover letter).
        assert len(report.uploads) >= 2
        assert all(u.status == "uploaded" for u in report.uploads)
        for upload in report.uploads:
            # Every uploaded file's SHA-256 MUST be in the approved set.
            from universal_auto_applier.synthetic_profile import sha256_file

            assert sha256_file(Path(upload.path)) in _approved(tmp_path), (
                f"unapproved doc uploaded: {upload.path}"
            )

    def test_plan_is_frozen_and_hashed_before_mutation(
        self, context: BrowserContext, platform_server: str, tmp_path: Path
    ) -> None:
        job = _make_job(f"{platform_server}/greenhouse_apply.html", tmp_path)
        runner = LiveBrowserRunner(_config(tmp_path))
        report = runner.run_in_context_synthetic(
            context,
            job,
            PROFILE,
            approved_document_hashes=_approved(tmp_path),
            mutation_budget=20,
            artifact_dir=tmp_path / "run-greenhouse-plan",
        )

        assert report.plan_hash, "plan_hash must be recorded"
        assert len(report.plan_hash) == 64
        assert report.mutation_plan_path is not None
        plan_json = Path(report.mutation_plan_path)
        assert plan_json.exists()

        # The recorded hash must re-verify against the persisted plan.
        import hashlib
        import json

        data = json.loads(plan_json.read_text(encoding="utf-8"))
        data.pop("generated_at", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert report.plan_hash == hashlib.sha256(canonical).hexdigest()

        # Every mutated entry carries a declared-synthetic source.
        for entry in data["entries"]:
            if entry["decision"] == "mutate":
                assert entry["value_source"] in {"candidate_profile", "document_path"}

    def test_interlock_armed_and_zero_submit_attempts(
        self, context: BrowserContext, platform_server: str, tmp_path: Path
    ) -> None:
        job = _make_job(f"{platform_server}/greenhouse_apply.html", tmp_path)
        runner = LiveBrowserRunner(_config(tmp_path))
        report = runner.run_in_context_synthetic(
            context,
            job,
            PROFILE,
            approved_document_hashes=_approved(tmp_path),
            mutation_budget=20,
            artifact_dir=tmp_path / "run-greenhouse-interlock",
        )

        assert report.submitted is False
        # Nothing attempted a submission, so the interlock never had to
        # block anything (and never will, because the runner never clicks).
        assert not any("wq7_interlock" in err for err in report.errors), (
            "runner must not attempt submissions during synthetic mutation"
        )
        assert not any("blocked" in err for err in report.errors)

    def test_no_real_candidate_data_in_dom(
        self, context: BrowserContext, platform_server: str, tmp_path: Path
    ) -> None:
        job = _make_job(f"{platform_server}/greenhouse_apply.html", tmp_path)
        runner = LiveBrowserRunner(_config(tmp_path))
        report = runner.run_in_context_synthetic(
            context,
            job,
            PROFILE,
            approved_document_hashes=_approved(tmp_path),
            mutation_budget=20,
            artifact_dir=tmp_path / "run-greenhouse-dom",
        )

        assert report.submitted is False
        assert report.dom_snapshot_path is not None

        # page.content() does not serialize input[name=value] property
        # updates, so verify the synthetic identity via the observed
        # fields AND verify no real candidate data appears in the DOM.
        filled_values = [
            f.filled_value for f in report.fields if f.status == "filled" and f.filled_value
        ]
        synthetic_visible = {
            "test.candidate@example.com",
            "+1 555 0199",
        }
        assert synthetic_visible <= set(filled_values), (
            f"declared synthetic identity not fully entered: {filled_values}"
        )

        dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8").lower()
        assert "mohammed.abd.elrhman" not in dom, "a real candidate email leaked!"


class TestLeverSyntheticMutation:
    def test_fills_lever_like_form_and_stops_before_submit(
        self, context: BrowserContext, platform_server: str, tmp_path: Path
    ) -> None:
        job = _make_job(f"{platform_server}/lever_apply.html", tmp_path)
        runner = LiveBrowserRunner(_config(tmp_path))
        report = runner.run_in_context_synthetic(
            context,
            job,
            PROFILE,
            approved_document_hashes=_approved(tmp_path),
            mutation_budget=20,
            artifact_dir=tmp_path / "run-lever",
        )

        assert report.status == "review_ready", (
            f"got status={report.status} reason={report.stopped_reason} errors={report.errors}"
        )
        assert report.stopped_reason == "final_submit_detected"
        assert report.submitted is False

        filled = {f.label for f in report.fields if f.status == "filled"}
        assert {"Full name", "Email", "Phone"} <= filled
        assert len(report.uploads) >= 2
        assert all(u.status == "uploaded" for u in report.uploads)


class TestSyntheticMutationGuard:
    def test_refuses_non_synthetic_profile_at_runner(self, tmp_path: Path) -> None:
        from universal_auto_applier.synthetic_profile import SyntheticProfile

        runner = LiveBrowserRunner(_config(tmp_path))
        job = _make_job("https://example.test/apply", tmp_path)
        plain = SyntheticProfile()  # wq7_synthetic marker, but not synthetic_test
        # SyntheticProfile lacks synthetic_test -> guard must refuse.
        with pytest.raises(ValueError, match="not a WQ-7C synthetic identity"):
            runner.run_synthetic_mutation(job, plain, frozenset(), 5)  # type: ignore[arg-type]

    def test_refuses_when_interlock_not_armed(self, tmp_path: Path) -> None:
        runner = LiveBrowserRunner(
            LiveBrowserConfig(
                artifacts_root=tmp_path / "live-runs",
                hard_submit_block=False,
            )
        )
        job = _make_job("https://example.test/apply", tmp_path)
        with pytest.raises(ValueError, match="hard_submit_block"):
            runner.run_synthetic_mutation(job, PROFILE, frozenset(), 5)
