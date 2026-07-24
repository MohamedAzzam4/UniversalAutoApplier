"""Playwright regression tests for stable field identity.

These tests prove the stable-token properties against rendered fixture
pages, using the real LiveBrowserRunner with deterministic-only mapping
(no LLM). They cover the scenarios required by the stable-field-identity
workpackage:

1. DOM inserts a new field before an existing field between observations;
   existing token remains unchanged.
2. Conditional field reveal changes extraction order; existing tokens
   remain unchanged.
3. Same selector is first unresolved and then filled; final report has
   one filled record (via consolidation, tested at the unit level and
   exercised here through the real executor).
4. Persistence creates zero interventions for a resolved field.
5. Legitimately unresolved fields still create exactly one intervention
   each.
6. Two similar radio groups remain distinct.
7. One radio group has a single stable group token.
8. iframe fields do not collide with main-page fields.
9. submitted=false throughout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import BrowserContext

from tests.playwright._fixture_server import serve_fixture_dir
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.live_executor import _extract_live_fields

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


@pytest.fixture(scope="module")
def fixture_server() -> str:
    yield from serve_fixture_dir(FIXTURE_DIR)


def _make_job(
    tmp_path: Path,
    url: str,
    external_id: str,
    metadata: dict[str, Any] | None = None,
) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_md = tmp_path / f"{external_id}-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python automation, FastAPI, Docker, Kubernetes", encoding="utf-8")
    base_meta: dict[str, Any] = {
        "candidate_profile": {
            "first_name": "Mohamed",
            "last_name": "Azzam",
            "full_name": "Mohamed Azzam",
            "email": "mohamed@example.com",
            "phone": "+49 1234567",
            "requires_sponsorship": False,
        },
    }
    if metadata:
        base_meta.update(metadata)
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
        metadata=base_meta,
    )


def _make_config(tmp_path: Path) -> LiveBrowserConfig:
    return LiveBrowserConfig(
        artifacts_root=tmp_path / "live-runs",
        profile_dir=None,
        headless=True,
        channel=None,
        timeout_ms=15_000,
        max_steps=5,
        capture_trace=False,
    )


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="Mohamed",
        last_name="Azzam",
        full_name="Mohamed Azzam",
        email="mohamed@example.com",
        phone="+49 1234567",
        requires_sponsorship=False,
    )


# ---------------------------------------------------------------------------
# 1. DOM insert stability: token unchanged when a field is inserted above
# ---------------------------------------------------------------------------


class TestDomInsertStability:
    def test_field_token_unchanged_after_dom_insert(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Extract fields, then insert a new field above #phone, then
        extract again. The #phone field's token must NOT change."""
        url = f"{fixture_server}/dynamic_dom_insert.html"
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")

            # First extraction.
            targets_before = _extract_live_fields(page)
            phone_before = next((t for t in targets_before if t.field.name == "phone"), None)
            assert phone_before is not None, "phone field not found in first extraction"
            token_before = phone_before.token

            # Insert a new field above #phone via the fixture's helper.
            page.evaluate("window.__insertFieldAbovePhone()")

            # Second extraction.
            targets_after = _extract_live_fields(page)
            phone_after = next((t for t in targets_after if t.field.name == "phone"), None)
            assert phone_after is not None, "phone field not found in second extraction"
            token_after = phone_after.token

            # The token must be unchanged despite the DOM insert.
            assert token_before == token_after, (
                f"Phone token changed after DOM insert: {token_before!r} -> {token_after!r}"
            )

            # The new middle_name field should have its own distinct token.
            middle = next((t for t in targets_after if t.field.name == "middle_name"), None)
            assert middle is not None, "middle_name field not found after insert"
            assert middle.token != token_after, (
                "middle_name token must be distinct from phone token"
            )
        finally:
            page.close()


# ---------------------------------------------------------------------------
# 2. Conditional reveal: tokens unchanged when extraction order changes
# ---------------------------------------------------------------------------


class TestConditionalRevealStability:
    def test_radio_token_unchanged_after_conditional_reveal(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Extract fields (radio visible, conditional hidden). Select
        'Yes' on the radio to reveal the conditional field. Extract
        again. The radio group's token must NOT change, even though the
        extraction order changed (the conditional field is now between
        the radio and the submit button)."""
        url = f"{fixture_server}/conditional_reveal.html"
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")

            # First extraction: radio visible, conditional hidden.
            targets_before = _extract_live_fields(page)
            radio_before = next((t for t in targets_before if t.field.type == "radio"), None)
            assert radio_before is not None, "radio group not found before reveal"
            token_before = radio_before.token

            # The conditional field should NOT be present yet.
            docker_years_before = next(
                (t for t in targets_before if t.field.name == "docker_years"), None
            )
            assert docker_years_before is None, (
                "Conditional field should be hidden (not extracted) before reveal"
            )

            # Select 'Yes' on the radio to reveal the conditional field.
            page.check('input[type="radio"][name="docker_exp"][value="Yes"]')
            page.wait_for_timeout(500)

            # Second extraction: conditional field is now visible.
            targets_after = _extract_live_fields(page)
            radio_after = next((t for t in targets_after if t.field.type == "radio"), None)
            assert radio_after is not None, "radio group not found after reveal"
            token_after = radio_after.token

            # The radio group's token must be unchanged.
            assert token_before == token_after, (
                f"Radio token changed after conditional reveal: {token_before!r} -> {token_after!r}"
            )

            # The conditional field should now be present.
            docker_years_after = next(
                (t for t in targets_after if t.field.name == "docker_years"), None
            )
            assert docker_years_after is not None, (
                "Conditional field should be visible (extracted) after reveal"
            )
        finally:
            page.close()


# ---------------------------------------------------------------------------
# 3. Same selector first unresolved then filled: one filled record
# ---------------------------------------------------------------------------


class TestConsolidationFilledSupersedesIntervention:
    def test_same_field_unresolved_then_filled_one_record(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Run the real executor on the conditional fixture with an LLM
        mock that answers 'Yes' for the Docker radio. The Docker radio is
        filled deterministically (CV mentions Docker). The conditional
        number field is revealed but not fillable. The final report must
        have ONE record per logical field — no duplicate for the Docker
        radio."""

        url = f"{fixture_server}/conditional_reveal.html"
        job = _make_job(tmp_path, url, "consolidation-1")
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-consolidation",
        )

        # The report must have at most one record per logical field
        # (consolidated by stable token).
        tokens = [f.field_token for f in report.fields if f.field_token]
        assert len(tokens) == len(set(tokens)), f"Duplicate field tokens in report: {tokens}"

        # The Docker radio should appear exactly once.
        docker_radios = [
            f for f in report.fields if f.field_type == "radio" and "docker" in f.label.lower()
        ]
        assert len(docker_radios) == 1, (
            f"Expected exactly 1 Docker radio record, got {len(docker_radios)}"
        )

        # submitted must be False.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 4 & 5. Persistence: zero interventions for filled, one for unresolved
# ---------------------------------------------------------------------------


class TestPersistenceWithStableTokens:
    def test_zero_interventions_for_filled_one_for_unresolved(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Run the executor on the conditional fixture. The Docker radio
        is filled (deterministic). The conditional number field is
        unresolved (intervention_needed). After persistence:
        - zero interventions for the Docker radio (it was filled);
        - exactly one intervention for the conditional number field."""
        from universal_auto_applier.cli import _persist_interventions
        from universal_auto_applier.config import Settings
        from universal_auto_applier.interventions.store import list_pending_interventions
        from universal_auto_applier.persistence.db import (
            build_engine_url,
            make_engine,
            make_session_factory,
            session_scope,
        )
        from universal_auto_applier.persistence.job_repository import upsert_application_job
        from universal_auto_applier.persistence.migrations import apply_migrations

        url = f"{fixture_server}/conditional_reveal.html"
        job = _make_job(tmp_path, url, "persist-1")
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-persist",
        )

        # Set up a temp data dir and persist interventions.
        settings = Settings(
            host="127.0.0.1",
            port=8010,
            data_dir=tmp_path / "uaa_persist",
            browser_headless=True,
            submit_mode="review",
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        _persist_interventions(settings, job.application_id, report)

        # Query persisted interventions.
        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            interventions = list_pending_interventions(session, job.application_id)
        engine2.dispose()

        # No intervention for the Docker radio (it was filled).
        docker_ivs = [
            iv
            for iv in interventions
            if "docker" in iv.question.lower() and "years" not in iv.question.lower()
        ]
        assert len(docker_ivs) == 0, (
            f"Expected 0 interventions for filled Docker radio, got {len(docker_ivs)}: "
            f"{[iv.question for iv in docker_ivs]}"
        )

        # The conditional number field should have exactly one intervention.
        years_ivs = [
            iv
            for iv in interventions
            if "years" in iv.question.lower() or "docker" in iv.question.lower()
        ]
        # Filter to just the "years of Docker experience" question.
        years_ivs = [iv for iv in interventions if "years" in iv.question.lower()]
        assert len(years_ivs) == 1, (
            f"Expected exactly 1 intervention for the unresolved number field, "
            f"got {len(years_ivs)}: {[(iv.question, iv.field_selector) for iv in interventions]}"
        )

        # The intervention's field_selector must be a stable lf- token.
        assert years_ivs[0].field_selector.startswith("lf-"), (
            f"Intervention field_selector must be a stable lf- token, "
            f"got {years_ivs[0].field_selector!r}"
        )

        assert report.submitted is False


# ---------------------------------------------------------------------------
# 6 & 7. Two radio groups distinct + one token per group
# ---------------------------------------------------------------------------


class TestTwoRadioGroups:
    def test_two_radio_groups_distinct_tokens(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Two radio groups (Kubernetes + Docker) must have DISTINCT
        stable tokens, and each group has exactly ONE record."""
        url = f"{fixture_server}/two_radio_groups.html"
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            targets = _extract_live_fields(page)

            radios = [t for t in targets if t.field.type == "radio"]
            assert len(radios) == 2, f"Expected 2 radio groups, got {len(radios)}"

            k8s = next((t for t in radios if "kubernetes" in t.field.label.lower()), None)
            docker = next((t for t in radios if "docker" in t.field.label.lower()), None)
            assert k8s is not None, "Kubernetes radio group not found"
            assert docker is not None, "Docker radio group not found"

            # Distinct tokens.
            assert k8s.token != docker.token, (
                f"Two radio groups must have distinct tokens: "
                f"k8s={k8s.token!r}, docker={docker.token!r}"
            )

            # Both start with lf-.
            assert k8s.token.startswith("lf-")
            assert docker.token.startswith("lf-")
        finally:
            page.close()

    def test_one_radio_group_has_single_token(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """A single radio group produces exactly ONE field target (not
        one per option). The token is the group token, not an option
        token."""
        url = f"{fixture_server}/radio_fieldset.html"
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            targets = _extract_live_fields(page)

            radios = [t for t in targets if t.field.type == "radio"]
            assert len(radios) == 1, f"Expected exactly 1 radio group target, got {len(radios)}"

            radio = radios[0]
            assert radio.token.startswith("lf-")
            # The label is the question, not an option label.
            assert radio.field.label == "Do you have experience with Kubernetes?"
            assert radio.field.label not in ("Yes", "No")
        finally:
            page.close()


# ---------------------------------------------------------------------------
# 8. iframe fields do not collide with main-page fields
# ---------------------------------------------------------------------------


class TestIframeFieldCollision:
    def test_iframe_and_main_page_fields_with_same_id_are_distinct(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """The main page and the iframe both have a field with
        id='first_name' and name='first_name'. They must have DIFFERENT
        stable tokens because they live in different frames."""
        url = f"{fixture_server}/iframe_outer.html"
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            # Wait for the iframe to load.
            page.wait_for_timeout(1_000)
            targets = _extract_live_fields(page)

            first_name_targets = [t for t in targets if t.field.name == "first_name"]
            # There should be at least 2 (one from main, one from iframe).
            assert len(first_name_targets) >= 2, (
                f"Expected at least 2 first_name fields (main + iframe), "
                f"got {len(first_name_targets)}"
            )

            # Their tokens must all be distinct.
            tokens = [t.token for t in first_name_targets]
            assert len(tokens) == len(set(tokens)), (
                f"first_name fields must have distinct tokens: {tokens}"
            )

            # All start with lf-.
            for tok in tokens:
                assert tok.startswith("lf-"), f"Token must start with lf-: {tok!r}"
        finally:
            page.close()


# ---------------------------------------------------------------------------
# 9. submitted=false throughout (covered by all tests above)
# ---------------------------------------------------------------------------


class TestSubmittedFalse:
    def test_conditional_fixture_submitted_false(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """The runner never clicks the final Submit button."""
        url = f"{fixture_server}/conditional_reveal.html"
        job = _make_job(tmp_path, url, "submitted-1")
        config = _make_config(tmp_path)
        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-submitted",
        )
        assert report.submitted is False
