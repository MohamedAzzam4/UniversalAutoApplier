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

    For every LiveFieldRecord with status=intervention_needed or
    requires_confirmation=True, create a persisted intervention using
    the existing intervention store. Uses the deterministic intervention
    ID to prevent duplicates on reprocessing.
    """
    from universal_auto_applier.core.statuses import InterventionKind
    from universal_auto_applier.interventions.store import create_intervention

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            for field in report.fields:
                if field.status != "intervention_needed" and not field.requires_confirmation:
                    continue
                llm_metadata: dict[str, Any] | None = None
                if field.category or field.risk_level or field.evidence_summary:
                    llm_metadata = {
                        "available_options": [],
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
                    question=field.label or field.field_token or "Unknown question",
                    options=[],
                    suggested_answer=field.proposed_answer,
                    confidence=field.confidence,
                    field_selector=field.field_token or field.selector,
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
    parser.error(f"unknown command: {args.command}")
    return 2


__all__ = ["run_command"]
