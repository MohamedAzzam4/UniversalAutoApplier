"""Explicit local CLI commands beyond the dashboard server."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from universal_auto_applier.browser.live_models import LiveRunReport
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import list_application_jobs
from universal_auto_applier.persistence.migrations import apply_migrations


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m universal_auto_applier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-jobs", help="List imported jobs and application IDs.")

    session = subparsers.add_parser(
        "browser-session",
        help="Open UAA's persistent browser profile for manual login/setup.",
    )
    session.add_argument("--url", default="https://www.linkedin.com/login")
    session.add_argument("--profile-dir", type=Path)
    session.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")

    live = subparsers.add_parser(
        "live-dry-run",
        help="Open and fill one real application, stopping before final submit.",
    )
    live.add_argument(
        "--application-id",
        required=True,
        help="Full application ID or an unambiguous prefix shown by list-jobs.",
    )
    live.add_argument(
        "--start-url",
        help=(
            "Diagnostic URL override for this run only (for example a known direct ATS URL). "
            "The stored job is not modified."
        ),
    )
    live.add_argument("--artifacts-dir", type=Path)
    live.add_argument("--profile-dir", type=Path)
    live.add_argument(
        "--ephemeral-profile",
        action="store_true",
        help="Do not reuse saved browser cookies/login state.",
    )
    display = live.add_mutually_exclusive_group()
    display.add_argument("--headless", action="store_true", default=None)
    display.add_argument("--headed", action="store_false", dest="headless")
    live.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    live.add_argument("--timeout-ms", type=int)
    live.add_argument("--max-steps", type=int)

    submit = subparsers.add_parser(
        "live-submit",
        help=(
            "Execute the controlled final submission for an approved application. "
            "Requires UAA_ENABLE_REAL_SUBMISSION=true and an active approval."
        ),
    )
    submit.add_argument(
        "--application-id",
        required=True,
        help="Full application ID or an unambiguous prefix shown by list-jobs.",
    )
    submit.add_argument(
        "--approval-id",
        required=True,
        help="The approval ID returned by the approve-snapshot API/CLI.",
    )
    submit.add_argument(
        "--confirm",
        action="store_true",
        required=True,
        help="Deliberate confirmation that you want to click Submit.",
    )
    submit.add_argument("--profile-dir", type=Path)
    submit.add_argument(
        "--ephemeral-profile",
        action="store_true",
        help="Do not reuse saved browser cookies/login state.",
    )
    display = submit.add_mutually_exclusive_group()
    display.add_argument("--headless", action="store_true", default=None)
    display.add_argument("--headed", action="store_false", dest="headless")
    submit.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    submit.add_argument("--timeout-ms", type=int)
    submit.add_argument("--artifacts-dir", type=Path)
    return parser


def _open_store(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    apply_migrations(database_url)
    engine = make_engine(database_url)
    return engine, make_session_factory(engine)


def _list_jobs(settings: Settings) -> int:
    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            jobs = list_application_jobs(session)
        if not jobs:
            print("No imported jobs found.")
            return 0
        for job in jobs:
            print(f"{job.application_id[:12]}  {str(job.status):18}  {job.company} | {job.title}")
        return 0
    finally:
        engine.dispose()


def _find_job(settings: Settings, application_id: str):
    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            jobs = list_application_jobs(session)
        matches = [job for job in jobs if job.application_id.startswith(application_id)]
        if not matches:
            raise ValueError(f"no application matches ID prefix {application_id!r}")
        if len(matches) > 1:
            raise ValueError(
                f"application ID prefix {application_id!r} is ambiguous ({len(matches)} matches)"
            )
        return matches[0]
    finally:
        engine.dispose()


def _live_dry_run(settings: Settings, args: argparse.Namespace) -> int:
    try:
        job = _find_job(settings, str(args.application_id))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.start_url:
        parts = urlsplit(str(args.start_url))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            print("error: --start-url must be an HTTP(S) URL", file=sys.stderr)
            return 2
        job = job.model_copy(update={"url": str(args.start_url)})

    headless = settings.browser_headless if args.headless is None else bool(args.headless)
    profile_dir: Path | None
    if args.ephemeral_profile:
        profile_dir = None
    else:
        profile_dir = (
            args.profile_dir
            or settings.browser_profile_dir
            or settings.data_dir / "browser-profile"
        )
    config = LiveBrowserConfig(
        artifacts_root=args.artifacts_dir or settings.data_dir / "live-runs",
        profile_dir=profile_dir,
        headless=headless,
        channel=args.channel or settings.browser_channel,
        timeout_ms=args.timeout_ms or settings.browser_timeout_ms,
        max_steps=args.max_steps or settings.browser_max_steps,
    )
    candidate = resolve_candidate_profile(job.metadata, settings.candidate_profile)

    # Create the LLM QA service. When configuration is valid (API key +
    # model), the run uses LLM-assisted question resolution. When
    # configuration is absent, deterministic-only mode continues safely.
    from universal_auto_applier.llm.qa_service import create_qa_service

    qa_service = create_qa_service()
    if qa_service.is_configured:
        print("llm_mode: llm_assisted")
    else:
        print("llm_mode: deterministic_only")
        qa_service = None  # Don't pass an unconfigured service.

    report = LiveBrowserRunner(config).run(job, candidate, qa_service=qa_service)

    # Persist interventions for unresolved/confirmation-required fields.
    _persist_interventions(settings, job.application_id, report)

    print(f"status: {report.status}")
    print(f"stopped_reason: {report.stopped_reason}")
    print(f"final_url: {report.final_url}")
    print(f"clicks: {len(report.click_path)}")
    print(f"fields: {len(report.fields)}")
    print(f"uploads: {len(report.uploads)}")
    print(f"submitted: {report.submitted}")
    print(f"report: {report.report_path}")
    if report.status == "review_ready":
        return 0
    if report.status == "needs_user_input":
        return 3
    return 2


def _persist_interventions(settings: Settings, application_id: str, report: LiveRunReport) -> None:
    """Persist interventions for fields that need user input.

    For every final-terminal LiveFieldRecord with status=intervention_needed
    or requires_confirmation=True, create a persisted intervention using
    the existing intervention store. Uses the deterministic intervention
    ID (derived from application_id + kind + field_selector + question) to
    prevent duplicates on reprocessing.

    Stale-pending supersession: for every final-terminal record whose
    status is ``filled`` (the field was successfully answered), any
    existing PENDING intervention for the same (application_id, kind,
    field_selector, question) is resolved as ``RESOLVED``. This prevents
    a stale pending intervention from lingering after the field was filled
    — which was the real-ATS defect: a field first seen as
    ``intervention_needed`` and later filled by the LLM left a stale
    pending intervention because the token shifted between observations.

    The report's fields are assumed to already be consolidated by
    :func:`universal_auto_applier.form_engine.live_executor.consolidate_fields`
    (one terminal record per logical field). This function does NOT
    re-consolidate; it trusts the report's terminal state.

    Legacy-token limitation (honest reporting): existing pending
    interventions created with the OLD positional token format
    (``live-field-0-N``) CANNOT be auto-matched to the NEW stable token
    format (``lf-...``). The deterministic intervention ID is derived
    from ``field_selector``, and since the selector format changed
    completely, the IDs do not match. Such legacy pending interventions
    are NOT automatically resolved by this function. They require
    local-data cleanup: the user must manually resolve them via the
    dashboard UI or run a one-time cleanup script. This is a known
    migration cost of switching to stable field identity and is not
    silently papered over.
    """
    from universal_auto_applier.core.statuses import (
        InterventionKind,
        InterventionStatus,
    )
    from universal_auto_applier.interventions.store import (
        create_intervention,
        find_pending_intervention_for_field,
        resolve_intervention,
    )

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            for field in report.fields:
                field_selector = field.field_token or field.selector
                question = field.label or field.field_token or "Unknown question"

                if field.status == "filled":
                    # The field was successfully filled. If a previous run
                    # left a PENDING intervention for this same field
                    # (same application_id + kind + field_selector +
                    # question), resolve it now — the intervention is no
                    # longer needed. This is idempotent: if no pending
                    # intervention exists, nothing happens.
                    if not field_selector:
                        continue
                    stale = find_pending_intervention_for_field(
                        session,
                        application_id=application_id,
                        kind=InterventionKind.FIELD_ANSWER,
                        field_selector=field_selector,
                        question=question,
                    )
                    if stale is not None:
                        resolve_intervention(
                            session,
                            stale.intervention_id,
                            resolution=InterventionStatus.RESOLVED,
                            answer=field.filled_value or None,
                        )
                    continue

                if field.status != "intervention_needed" and not field.requires_confirmation:
                    continue

                # Pass the field's available options through to BOTH the
                # intervention's `options` column and the LLM metadata's
                # `available_options` field. Previously this was hardcoded
                # to [], which lost the option list on persisted
                # interventions (real-ATS defect: the user saw an
                # intervention with no choices to pick from).
                field_options = list(field.options)

                llm_metadata: dict[str, Any] | None = None
                if field.category or field.risk_level or field.evidence_summary:
                    llm_metadata = {
                        "available_options": field_options,
                        "evidence_summary": field.evidence_summary or "",
                        "category": field.category or "",
                        "risk_level": field.risk_level or "",
                        "requires_confirmation": field.requires_confirmation,
                        "unresolved_reason": field.explanation or "",
                        "field_token": field.field_token or "",
                        "answer_source": field.source or "",
                    }
                create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question=question,
                    options=field_options,
                    suggested_answer=field.proposed_answer,
                    confidence=field.confidence,
                    field_selector=field_selector,
                    page_url=field.page_url,
                    llm_metadata=llm_metadata,
                )
    finally:
        engine.dispose()


def _browser_session(settings: Settings, args: argparse.Namespace) -> int:
    profile_dir = (
        args.profile_dir or settings.browser_profile_dir or settings.data_dir / "browser-profile"
    )
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"Opening UAA browser profile: {profile_dir.resolve()}")
    print("Complete login/setup in the browser, then return here and press Enter.")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            channel=args.channel or settings.browser_channel,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                str(args.url), wait_until="domcontentloaded", timeout=settings.browser_timeout_ms
            )
            try:
                input("Press Enter after the browser session is ready... ")
            except EOFError:
                print("No interactive terminal input was available; closing the browser.")
        finally:
            context.close()
    print("Browser session saved.")
    return 0


"""Execute the controlled final submission for an approved application.

This is the ONLY CLI command that can click the final submit control.
It delegates to :class:`SubmissionExecutionService` which guarantees
same-page execution (observation, gate checks, claim, click, and
result detection all happen on one live ``Page``).

Call path:
CLI ``live-submit`` → ``SubmissionExecutionService.execute_controlled_submission``
→ ``coordinator.execute_submission_from_page`` (same Page)

Requires:
- ``UAA_ENABLE_REAL_SUBMISSION=true``
- An active approval (``--approval-id``) for this application
- ``--confirm`` flag (deliberate confirmation)
"""


def _live_submit(settings: Settings, args: argparse.Namespace) -> int:
    from universal_auto_applier.submission.execution_service import (
        PlaywrightContextFactory,
        SubmissionExecutionService,
    )

    if not settings.enable_real_submission:
        print(
            "ERROR: UAA_ENABLE_REAL_SUBMISSION is not true. "
            "Controlled final submission is disabled by default."
        )
        return 2

    if not args.confirm:
        print("ERROR: --confirm is required to submit. This is a deliberate safety gate.")
        return 2

    try:
        job = _find_job(settings, str(args.application_id))
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    application_id = job.application_id

    # Build the browser context factory.
    profile_dir = args.profile_dir or settings.browser_profile_dir
    if args.ephemeral_profile:
        profile_dir = None
    headless = args.headless if args.headless is not None else settings.browser_headless

    context_factory = PlaywrightContextFactory(
        settings=settings,
        profile_dir=profile_dir,
        headless=headless,
        channel=args.channel or settings.browser_channel,
    )

    artifact_dir = args.artifacts_dir or (
        settings.data_dir / "live-runs" / f"{application_id[:12]}-submit"
    )

    engine, session_factory = _open_store(settings)
    try:
        service = SubmissionExecutionService(settings, session_factory, context_factory)
        result = service.execute_controlled_submission(
            application_id=application_id,
            approval_id=args.approval_id,
            artifact_dir=artifact_dir,
        )
    finally:
        engine.dispose()

    # Report the result.
    print(f"\nSubmission result: {result.state}")
    print(f"  Clicked: {result.clicked}")
    if result.confirmation_evidence:
        print(f"  Evidence: {result.confirmation_evidence}")
    if result.error_message:
        print(f"  Error: {result.error_message}")
    if result.post_submit_url:
        print(f"  Post-submit URL: {result.post_submit_url}")
    if result.pre_submit_screenshot:
        print(f"  Pre-submit screenshot: {result.pre_submit_screenshot}")
    if result.post_submit_screenshot:
        print(f"  Post-submit screenshot: {result.post_submit_screenshot}")

    # Update application status on confirmed success.
    if result.state.value == "submitted_confirmed":
        engine2, session_factory2 = _open_store(settings)
        try:
            from universal_auto_applier.core.statuses import ApplicationStatus
            from universal_auto_applier.persistence.job_repository import (
                update_application_status,
            )

            with session_scope(session_factory2) as session:
                update_application_status(session, application_id, ApplicationStatus.SUBMITTED)
        finally:
            engine2.dispose()
        return 0
    if result.state.value in ("outcome_unknown", "already_submitted"):
        return 2
    return 3


def run_command(argv: list[str], settings: Settings) -> int:
    """Run a non-server CLI command and return its process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "list-jobs":
        return _list_jobs(settings)
    if args.command == "browser-session":
        return _browser_session(settings, args)
    if args.command == "live-dry-run":
        return _live_dry_run(settings, args)
    if args.command == "live-submit":
        return _live_submit(settings, args)
    parser.error(f"unknown command: {args.command}")
    return 2


__all__ = ["run_command"]
