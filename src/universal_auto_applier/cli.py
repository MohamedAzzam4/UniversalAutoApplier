"""Explicit local CLI commands beyond the dashboard server."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from universal_auto_applier.browser.live_models import LiveRunReport
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import list_application_jobs
from universal_auto_applier.persistence.migrations import apply_migrations

# Single source of truth for every explicit CLI subcommand. ``__main__.py``
# routes its argv[0] here so ``python -m universal_auto_applier <command>``
# dispatches to :func:`run_command` instead of starting the dashboard server.
# Keep this in sync with the subparsers registered in ``_build_parser``.
CLI_COMMANDS: frozenset[str] = frozenset(
    {
        "list-jobs",
        "queue-import",
        "browser-session",
        "live-dry-run",
        "live-submit",
        "live-dry-run-platforms",
        "live-synthetic-mutation",
        "wq8-review-packet",
        "wq8-authorize",
        "wq8-status",
        "supervisor-run",
        "supervisor-status",
        "supervisor-handoffs",
        "supervisor-tickets",
        "supervisor-review-ready",
        "supervisor-application",
        "supervisor-interventions",
        "supervisor-candidate-facts",
        "supervisor-review-packet",
        "supervisor-repair-context",
        "supervisor-resolve-intervention",
        "supervisor-retry",
    }
)

_ATTACHED_PREPARATION_UNAVAILABLE = (
    "attached browser preparation is temporarily unavailable because safety requires a fresh "
    "UAA-owned browser context with no existing pages. Use the standard UAA-launched browser; "
    "verified attached-session resume is planned for V2-04."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m universal_auto_applier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-jobs", help="List imported jobs and application IDs.")

    queue_import = subparsers.add_parser(
        "queue-import",
        help=(
            "Import the configured JobHunter application queue "
            "(UAA_QUEUE_PATH) through the named import service. "
            "Does not apply or submit anything."
        ),
    )
    queue_import.add_argument(
        "--path",
        type=Path,
        help="Optional operator-supplied absolute queue path overriding the configured one.",
    )
    queue_import.add_argument(
        "--synthetic-mutation",
        action="store_true",
        help=(
            "WQ-7C opt-in: stamp synthetic_test/wq7_synthetic markers on every "
            "row whose candidate snapshot already IS the WQ-7C synthetic identity "
            "(Test Candidate / test.candidate@example.com). Any other row is refused."
        ),
    )

    session = subparsers.add_parser(
        "browser-session",
        help="Open UAA's persistent browser profile for manual login/setup.",
    )
    session.add_argument("--url", default="https://www.linkedin.com/login")
    session.add_argument("--profile-dir", type=Path)
    session.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    session.add_argument(
        "--attachable",
        action="store_true",
        help="Keep browser alive for attachable handoff (CDP loopback, writes browser-session.json).",
    )
    session.add_argument(
        "--attachable-port",
        type=int,
        help="Fixed local port for CDP endpoint (default: auto-allocate free port on 127.0.0.1).",
    )
    session.add_argument(
        "--session-file",
        type=Path,
        help="Path for attachable session metadata JSON (default: <data_dir>/browser-session.json).",
    )

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
    live.add_argument(
        "--browser-session-file",
        type=Path,
        help="Attach to a running attachable browser-session via its metadata JSON (CDP loopback).",
    )
    live.add_argument(
        "--cdp-endpoint",
        help="Direct CDP endpoint http://127.0.0.1:port for attach (loopback only).",
    )
    display = live.add_mutually_exclusive_group()
    display.add_argument("--headless", action="store_true", default=None)
    display.add_argument("--headed", action="store_false", dest="headless")
    live.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    live.add_argument("--timeout-ms", type=int)
    live.add_argument("--max-steps", type=int)
    live.add_argument(
        "--wq8-phase-a",
        action="store_true",
        help="WQ-8 Phase A: real-data preparation with submit interlock (real candidate + real CV allowed, final submission impossible, no one-shot armed).",
    )

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

    # WQ-7: Per-platform real ATS dry-run
    platforms = subparsers.add_parser(
        "live-dry-run-platforms",
        help=(
            "Run live dry-runs across configured ATS platforms (Greenhouse, "
            "Lever, Workday, SmartRecruiters, iCIMS). Requires "
            "UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=true and per-platform URL "
            "env vars. Never submits."
        ),
    )
    platforms.add_argument("--artifacts-dir", type=Path)
    platforms.add_argument("--profile-dir", type=Path)
    platforms.add_argument(
        "--ephemeral-profile",
        action="store_true",
        help="Do not reuse saved browser cookies/login state.",
    )
    platforms.add_argument(
        "--recon-only",
        action="store_true",
        help=(
            "WQ-7B navigation/observation-only: never fill, never upload, "
            "stop at the first application form."
        ),
    )
    display_p = platforms.add_mutually_exclusive_group()
    display_p.add_argument("--headless", action="store_true", default=None)
    display_p.add_argument("--headed", action="store_false", dest="headless")
    platforms.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    platforms.add_argument("--timeout-ms", type=int)
    platforms.add_argument("--max-steps", type=int)

    synthetic = subparsers.add_parser(
        "live-synthetic-mutation",
        help=(
            "WQ-7C: fill ONE real ATS form with the dedicated synthetic "
            "mutation identity and approved synthetic documents, stopping "
            "before final submit. Requires UAA_LIVE_SYNTHETIC_MUTATION=true "
            "and a synthetic candidate snapshot on the job. Never submits, "
            "never uses a real candidate."
        ),
    )
    synthetic.add_argument(
        "--application-id",
        required=True,
        help="Full application ID or an unambiguous prefix shown by list-jobs.",
    )
    synthetic.add_argument(
        "--start-url",
        help="Diagnostic URL override for this run only.",
    )
    synthetic.add_argument("--artifacts-dir", type=Path)
    synthetic.add_argument("--profile-dir", type=Path)
    synthetic.add_argument(
        "--ephemeral-profile",
        action="store_true",
        help="Do not reuse saved browser cookies/login state.",
    )
    display_s = synthetic.add_mutually_exclusive_group()
    display_s.add_argument("--headless", action="store_true", default=None)
    display_s.add_argument("--headed", action="store_false", dest="headless")
    synthetic.add_argument("--channel", help="Playwright browser channel, e.g. chrome or msedge.")
    synthetic.add_argument("--timeout-ms", type=int)
    synthetic.add_argument("--max-steps", type=int)
    synthetic.add_argument(
        "--max-mutations",
        type=int,
        default=None,
        help="Optional per-run mutation budget (default: Settings value).",
    )

    # WQ-8: review-packet (Phase A freeze), authorize (Phase B), status.
    rewpkt = subparsers.add_parser(
        "wq8-review-packet",
        help=(
            "Phase A: recompute the frozen review_plan_hash for one application "
            "and print the owner review packet (sanitized — no filled values, "
            "no PII). Reads the persisted live review snapshot. Never submits."
        ),
    )
    rewpkt.add_argument("--application-id", required=True)
    rewpkt.add_argument(
        "--job-url",
        help=(
            "Diagnostic URL override used ONLY for the packet (does not modify "
            "the stored job). Useful when the stored URL differs from the ATS."
        ),
    )

    au = subparsers.add_parser(
        "wq8-authorize",
        help=(
            "Phase B (owner-only): create the single-use real-submission "
            "authorization. Requires UAA_ENABLE_REAL_SUBMISSION=true, the "
            "exact frozen review_plan_hash from the review packet, a "
            "review_ready job, and --confirm. Refuses if the plan hashes "
            "differ, if any bound identity/URL/document/hash changed, or if a "
            "real submission already exists."
        ),
    )
    au.add_argument("--application-id", required=True)
    au.add_argument("--review-plan-hash", required=True)
    au.add_argument("--expires-in-hours", type=float, default=24.0)
    au.add_argument(
        "--confirm",
        action="store_true",
        required=True,
        help="Deliberate owner confirmation to authorize one real submission.",
    )
    au.add_argument(
        "--job-url",
        help="Diagnostic URL override (recomputed against this URL).",
    )

    st = subparsers.add_parser(
        "wq8-status",
        help="Read-only diagnostic: show WQ-8 authorization state for one application.",
    )
    st.add_argument("--application-id", required=True)

    # Supervisor V0 — agent-assisted operator mode (review-only)
    sup_run = subparsers.add_parser(
        "supervisor-run",
        help=(
            "Run the agent-assisted supervisor (V0, review-only, concurrency=1). "
            "Imports the JobHunter queue and drives review-only preparation through safe "
            "typed tools. Never submits. Use --queue <path> to import before the run. "
            "Use --review-only (default true) to keep the run review-only. "
            "Optional --owner-policy <path> loads reusable owner policies (JSON)."
        ),
    )
    sup_run.add_argument("--queue", type=Path, help="Optional queue file to import before the run.")
    sup_run.add_argument(
        "--review-only",
        action="store_true",
        default=True,
        help="Keep the run review-only (default true; V0 never submits).",
    )
    sup_run.add_argument(
        "--no-review-only",
        dest="review_only",
        action="store_false",
        help="Disable review-only (V0 will refuse — run remains review-only).",
    )
    sup_run.add_argument("--owner-policy", type=Path, help="Optional owner policy JSON file.")
    sup_run.add_argument(
        "--application-id",
        dest="application_ids",
        action="append",
        help="Limit the run to one or more application IDs (repeatable).",
    )
    sup_run.add_argument(
        "--planner",
        choices=["deterministic", "model"],
        default="deterministic",
        help="Supervisor planner mode (default: deterministic).",
    )

    sup_status = subparsers.add_parser(
        "supervisor-status",
        help="Show supervisor run status and per-application states.",
    )
    sup_status.add_argument("--run-id", help="Specific run ID to inspect (default: latest).")

    sup_handoffs = subparsers.add_parser(
        "supervisor-handoffs",
        help="List human handoffs created by the supervisor.",
    )
    sup_handoffs.add_argument("--run-id", help="Filter by run ID.")
    sup_handoffs.add_argument("--status", default=None, help="Filter by status (open, resolved).")

    sup_tickets = subparsers.add_parser(
        "supervisor-tickets",
        help="List repair tickets created by the supervisor.",
    )
    sup_tickets.add_argument("--run-id", help="Filter by run ID.")
    sup_tickets.add_argument("--status", default=None, help="Filter by status (open, resolved).")

    sup_review = subparsers.add_parser(
        "supervisor-review-ready",
        help="List review-ready applications from the latest or a specific supervisor run.",
    )
    sup_review.add_argument("--run-id", help="Specific run ID (default: latest).")

    # --- Additional supervisor commands for external agent interface ---
    sup_app = subparsers.add_parser(
        "supervisor-application",
        help="Get detailed status for a single application (JSON output).",
    )
    sup_app.add_argument("--application-id", required=True, help="Application ID.")
    sup_app.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_int = subparsers.add_parser(
        "supervisor-interventions",
        help="Get pending interventions for an application (JSON output).",
    )
    sup_int.add_argument("--application-id", required=True, help="Application ID.")
    sup_int.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_facts = subparsers.add_parser(
        "supervisor-candidate-facts",
        help="Get candidate fact keys for an application (JSON output, keys only — no values).",
    )
    sup_facts.add_argument("--application-id", required=True, help="Application ID.")
    sup_facts.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_review_pkt = subparsers.add_parser(
        "supervisor-review-packet",
        help="Get the canonical review packet for an application (JSON output).",
    )
    sup_review_pkt.add_argument("--application-id", required=True, help="Application ID.")
    sup_review_pkt.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_repair = subparsers.add_parser(
        "supervisor-repair-context",
        help="Get repair context for an application (JSON output — combines status, interventions, snapshot).",
    )
    sup_repair.add_argument("--application-id", required=True, help="Application ID.")
    sup_repair.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_resolve = subparsers.add_parser(
        "supervisor-resolve-intervention",
        help="Resolve an intervention through the official resolve service (JSON output).",
    )
    sup_resolve.add_argument("--intervention-id", required=True, help="Intervention ID.")
    sup_resolve.add_argument(
        "--resolution",
        required=True,
        choices=["resolved", "dismissed", "escalated"],
        help="Resolution status.",
    )
    sup_resolve.add_argument("--answer", help="Answer text for FIELD_ANSWER interventions.")
    sup_resolve.add_argument(
        "--file-bundle", action="append", help="File bundle as 'kind:path' pairs (repeatable)."
    )
    sup_resolve.add_argument(
        "--save-to-memory", action="store_true", help="Save answer to reusable memory."
    )
    sup_resolve.add_argument("--json", action="store_true", help="Output as JSON.")

    sup_retry = subparsers.add_parser(
        "supervisor-retry",
        help="Retry the review-only preparation for an application (JSON output).",
    )
    sup_retry.add_argument("--application-id", required=True, help="Application ID.")
    sup_retry.add_argument("--json", action="store_true", help="Output as JSON.")

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


def _resolve_cdp_endpoint(args: argparse.Namespace, settings: Settings) -> str | None:
    """Resolve CDP endpoint from --browser-session-file or --cdp-endpoint."""
    import json

    endpoint = getattr(args, "cdp_endpoint", None)
    session_file = getattr(args, "browser_session_file", None)
    if session_file is not None:
        session_file = Path(session_file)
        if not session_file.exists():
            print(f"error: browser-session-file not found: {session_file}", file=sys.stderr)
            return None
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"error: invalid browser-session-file JSON: {exc}", file=sys.stderr)
            return None
        # Validate loopback only.
        host = str(data.get("host", ""))
        port = data.get("port")
        cdp = str(data.get("cdp_endpoint", ""))
        if cdp:
            endpoint = cdp
        elif host and port:
            endpoint = f"http://{host}:{port}"
        else:
            print("error: browser-session-file missing host/port/cdp_endpoint", file=sys.stderr)
            return None
    if endpoint is None:
        return None
    # Enforce loopback only.
    from urllib.parse import urlsplit as _urlsplit

    try:
        parts = _urlsplit(str(endpoint))
    except ValueError:
        print(f"error: invalid cdp endpoint: {endpoint}", file=sys.stderr)
        return None
    if parts.hostname not in ("127.0.0.1", "localhost"):
        print(
            f"error: cdp endpoint must be loopback 127.0.0.1, got {parts.hostname!r}",
            file=sys.stderr,
        )
        return None
    return str(endpoint)


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

    # Attachable session takes precedence over profile launch.
    cdp_endpoint = _resolve_cdp_endpoint(args, settings)
    if cdp_endpoint is not None and getattr(args, "ephemeral_profile", False):
        print(
            "error: --ephemeral-profile cannot be used with --browser-session-file/--cdp-endpoint",
            file=sys.stderr,
        )
        return 2
    if cdp_endpoint is not None:
        print(
            f"error: {_ATTACHED_PREPARATION_UNAVAILABLE}",
            file=sys.stderr,
        )
        return 2

    headless = settings.browser_headless if args.headless is None else bool(args.headless)
    profile_dir: Path | None
    if cdp_endpoint is not None:
        # Attached mode reuses host browser; profile_dir is not launched anew.
        profile_dir = None
        headless = False
    elif args.ephemeral_profile:
        profile_dir = None
    else:
        profile_dir = (
            args.profile_dir
            or settings.browser_profile_dir
            or settings.data_dir / "browser-profile"
        )
    wq8_phase_a = bool(getattr(args, "wq8_phase_a", False) or settings.wq8_phase_a)
    if wq8_phase_a:
        from universal_auto_applier.synthetic_profile import is_synthetic_metadata

        if is_synthetic_metadata(job.metadata):
            print(
                "error: WQ-8 Phase A refuses synthetic candidate snapshot — "
                "real WQ-8 jobs must use a real candidate profile (is_synthetic_metadata true). "
                "Refusing.",
                file=sys.stderr,
            )
            return 2
    config = LiveBrowserConfig(
        artifacts_root=args.artifacts_dir or settings.data_dir / "live-runs",
        profile_dir=profile_dir,
        headless=headless,
        channel=args.channel or settings.browser_channel,
        timeout_ms=args.timeout_ms or settings.browser_timeout_ms,
        max_steps=args.max_steps or settings.browser_max_steps,
        hard_submit_block=True,
        wq8_phase_a=wq8_phase_a,
        cookie_consent_policy=settings.cookie_consent_policy,  # type: ignore[arg-type]
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

    # Attached execution via CDP.
    if cdp_endpoint is not None:
        from playwright.sync_api import sync_playwright

        print(f"Attaching to live browser session at {cdp_endpoint} (loopback)")
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_endpoint)
                try:
                    contexts = browser.contexts
                    if not contexts:
                        print(
                            "error: no browser contexts found on attached browser", file=sys.stderr
                        )
                        return 2
                    context = contexts[0]
                    # Use existing page if present, else new.
                    page = context.pages[0] if context.pages else context.new_page()
                    # Session validity check: must be DATEV Workday and not still on Konto erstellen if we expect authenticated.
                    try:
                        url_lower = (page.url or "").lower()
                        if "datev.wd3.myworkdayjobs.com" not in url_lower:
                            print(
                                f"warning: attached page host is not DATEV Workday: {page.url!r}",
                                file=sys.stderr,
                            )
                    except Exception:
                        pass
                    # Run in existing context without closing host.
                    runner = LiveBrowserRunner(config)
                    # For attached, we run in_context directly so host stays alive.
                    # We reuse the existing page's URL as start point if job.url differs from current authenticated page.
                    # If attached page is already on an authenticated step (My Information), use it.
                    report = runner.run_in_context(
                        context,
                        job,
                        candidate=candidate,
                        artifact_dir=None,
                        qa_service=qa_service,
                    )
                finally:
                    # Attached: disconnect but DO NOT close host browser/context.
                    try:
                        browser.close()
                    except Exception:
                        pass
                # Owns_browser=False: host remains alive.
                print("Attached execution detached; host browser remains running.")
        except Exception as exc:
            print(
                f"error: failed to attach to browser session: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
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
    print("request_interlock_scope: LiveBrowserRunner.run")
    print(f"request_interlock_installed: {report.request_interlock_installed}")
    print(f"request_interlock_coverage: {report.request_interlock_coverage}")
    print(f"request_interlock_limits: {'; '.join(report.request_interlock_limitations)}")
    print(f"blocked_http_requests: {report.blocked_http_request_count}")
    print(f"request_interlock_failure: {report.request_interlock_failure or 'none'}")
    print(f"request_outcome_unknown: {report.request_outcome_unknown}")
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
                if field.category or field.risk_level or field.evidence_summary or field.label:
                    llm_metadata = {
                        "available_options": field_options,
                        "evidence_summary": field.evidence_summary or "",
                        "category": field.category or "",
                        "risk_level": field.risk_level or "",
                        "requires_confirmation": field.requires_confirmation,
                        "unresolved_reason": field.explanation or "",
                        "field_token": field.field_token or "",
                        "answer_source": field.source or "",
                        "field_label": field.label,
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
    if bool(getattr(args, "attachable", False)):
        print(f"error: {_ATTACHED_PREPARATION_UNAVAILABLE}", file=sys.stderr)
        return 2

    import json
    import os
    import socket
    import time
    from datetime import datetime

    profile_dir = (
        args.profile_dir or settings.browser_profile_dir or settings.data_dir / "browser-profile"
    )
    profile_dir.mkdir(parents=True, exist_ok=True)

    attachable = bool(getattr(args, "attachable", False))
    if not attachable:
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
                    str(args.url),
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )
                try:
                    input("Press Enter after the browser session is ready... ")
                except EOFError:
                    print("No interactive terminal input was available; closing the browser.")
            finally:
                context.close()
        print("Browser session saved.")
        return 0

    # Attachable host mode: keep browser alive, expose loopback CDP, write metadata.
    session_file = getattr(args, "session_file", None) or (
        settings.data_dir / "browser-session.json"
    )
    session_file = Path(session_file)
    # Allocate loopback port.
    requested_port = getattr(args, "attachable_port", None)
    if requested_port is not None:
        port = int(requested_port)
        if port < 1 or port > 65535:
            print(f"error: --attachable-port must be 1-65535, got {port}", file=sys.stderr)
            return 2
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    # Validate loopback only.
    host = "127.0.0.1"
    print(f"Opening attachable UAA browser profile: {profile_dir.resolve()}")
    print(f"CDP loopback endpoint will be http://{host}:{port}")
    print(f"Session metadata will be written to {session_file.resolve()}")
    print("Human must authenticate directly in the Workday webpage (no password via terminal).")
    import subprocess

    # Launch Chromium directly to avoid Playwright pipe/port conflict.
    # launch_persistent_context always adds --remote-debugging-pipe which
    # collides with --remote-debugging-port. Using subprocess with
    # --user-data-dir + --remote-debugging-port is the supported attachable pattern.
    with sync_playwright() as playwright:
        chrome_path = playwright.chromium.executable_path
        # Build args for subprocess launch.
        chrome_args = [
            chrome_path,
            f"--user-data-dir={profile_dir.resolve()}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-features=Translate",
            str(args.url),
        ]
        if args.channel:
            # Channel is ignored for subprocess launch; log it.
            print(
                f"Note: --channel {args.channel} ignored for attachable host (uses bundled Chromium)."
            )
        proc = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait for CDP endpoint to become reachable.
        import httpx

        cdp_ready = False
        for _ in range(30):
            time.sleep(0.5)
            try:
                with httpx.Client(timeout=2) as client:
                    r = client.get(f"http://{host}:{port}/json/version", timeout=2)
                    if r.status_code == 200:
                        cdp_ready = True
                        break
            except Exception:
                pass
            if proc.poll() is not None:
                print(f"error: Chromium host exited early with code {proc.poll()}", file=sys.stderr)
                return 2
        if not cdp_ready:
            print("error: CDP endpoint did not become ready", file=sys.stderr)
            try:
                proc.terminate()
            except Exception:
                pass
            return 2
        # Connect Playwright to the running host for initial navigation (optional).
        # The host browser already navigated via chrome_args URL, but we also ensure page.
        browser = None
        context = None
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://{host}:{port}")
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                pages = context.pages
                page = pages[0] if pages else context.new_page()
                # Ensure requested URL is loaded if host didn't navigate.
                try:
                    if str(args.url) not in (page.url or ""):
                        page.goto(
                            str(args.url),
                            wait_until="domcontentloaded",
                            timeout=settings.browser_timeout_ms,
                        )
                except Exception:
                    pass
            else:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.goto(
                    str(args.url),
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )
            # Write ready metadata — no credentials/cookies/tokens.
            metadata = {
                "version": 1,
                "status": "ready",
                "host": host,
                "port": port,
                "cdp_endpoint": f"http://{host}:{port}",
                "profile_dir": str(profile_dir.resolve()),
                "pid": os.getpid(),
                "created_at": datetime.now(UTC).isoformat(),
                "url": str(args.url),
            }
            session_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = session_file.with_suffix(session_file.suffix + ".tmp")
            tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            tmp.replace(session_file)
            print(f"Attachable browser ready. Metadata: {session_file.resolve()}")
            print(
                "Complete login in the headed browser. The browser will remain open for attached execution."
            )
            print("Press Enter to keep browser alive for attach (or Ctrl+C to close when done).")
            try:
                input("Press Enter after login is complete (browser will stay open)... ")
            except EOFError:
                print("No interactive input; browser will remain open until process exit.")
                # Keep alive until Ctrl+C in non-interactive case — block.
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nClosing attachable browser...")
            # After first Enter, keep browser alive until second signal.
            print("Browser remains open for attached UAA execution.")
            print(
                f"Run attached: python -m universal_auto_applier live-dry-run --application-id <id> --browser-session-file {session_file.resolve()}"
            )
            print("Press Ctrl+C to close the host browser when the pilot is complete.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nClosing attachable browser host...")
        finally:
            # Cleanup metadata on host close.
            try:
                if session_file.exists():
                    session_file.unlink()
            except OSError:
                pass
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    print("Attachable browser host closed. Metadata removed.")
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
    if result.ats_reference_id:
        print(f"  ATS reference: {result.ats_reference_id}")
    if result.error_message:
        print(f"  Error: {result.error_message}")
    if result.post_submit_url:
        print(f"  Post-submit URL: {result.post_submit_url}")
    if result.pre_submit_screenshot:
        print(f"  Pre-submit screenshot: {result.pre_submit_screenshot}")
    if result.post_submit_screenshot:
        print(f"  Post-submit screenshot: {result.post_submit_screenshot}")

    # The job status transition was applied by the execution service when it
    # persisted the result (record_result -> apply_result_status_transition).
    # No manual status update here: the persisted result row is the source
    # of truth, and a manual SUBMITTED write could overwrite an APPLIED
    # status established from a structured ATS reference.
    if result.state.value == "submitted_confirmed":
        return 0
    if result.state.value in ("outcome_unknown", "already_submitted"):
        return 2
    return 3


def _queue_import(settings: Settings, args: argparse.Namespace) -> int:
    """Import the configured queue through the named import service (WQ-3).

    Prints counts and structured row errors. This command never launches a
    browser and never starts the pipeline. With ``--synthetic-mutation``,
    rows whose candidate snapshot matches the WQ-7C synthetic identity are
    stamped with the synthetic markers (identity-guarded per row).
    """
    from universal_auto_applier.services.queue_import_service import (
        QueueImportConcurrentError,
        QueueImportConfigurationError,
        QueueImportService,
    )

    engine, session_factory = _open_store(settings)
    try:
        service = QueueImportService(settings, session_factory)
        try:
            summary = service.run(
                path=args.path,
                trigger="cli",
                synthetic_mutation=getattr(args, "synthetic_mutation", False),
            )
        except QueueImportConfigurationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except QueueImportConcurrentError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    finally:
        engine.dispose()

    print(f"state: {summary.state}")
    print(f"run_id: {summary.run_id}")
    print(f"source: {summary.source_path}")
    print(f"fingerprint: {summary.source_fingerprint}")
    print(
        f"total: {summary.total_lines}  imported: {summary.imported}  "
        f"skipped: {summary.skipped}  errors: {summary.error_count}"
    )
    if summary.failure_reason:
        print(f"reason: {summary.failure_reason}")
    for row_error in summary.row_errors:
        print(f"row {row_error.get('line_number')}: {row_error.get('error')}", file=sys.stderr)

    if summary.state == "failed":
        return 2
    return 0


def _live_dry_run_platforms(settings: Settings, args: argparse.Namespace) -> int:
    """Run live dry-runs across configured ATS platforms (WQ-7)."""
    from universal_auto_applier.services.live_dry_run_platforms import (
        run_platform_dry_runs,
    )

    if not settings.enable_live_platform_dry_run:
        print(
            "Live platform dry-run is not enabled.\n"
            "Set UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=true to opt in.\n"
            "Also set UAA_LIVE_GREENHOUSE_URL, UAA_LIVE_LEVER_URL, "
            "UAA_LIVE_WORKDAY_URL, UAA_LIVE_SMARTRECRUITERS_URL, "
            "UAA_LIVE_ICIMS_URL to specify real ATS URLs."
        )
        return 2

    # Resolve profile dir
    profile_dir = None
    if getattr(args, "ephemeral_profile", False):
        profile_dir = None
    elif getattr(args, "profile_dir", None):
        profile_dir = args.profile_dir
    else:
        profile_dir = settings.browser_profile_dir

    recon_only = getattr(args, "recon_only", False)
    if not recon_only:
        recon_only = settings.live_recon_only

    headless = args.headless if args.headless is not None else settings.browser_headless

    try:
        summary = run_platform_dry_runs(
            settings,
            artifacts_dir=getattr(args, "artifacts_dir", None),
            headless=headless,
            profile_dir=profile_dir,
            max_steps=getattr(args, "max_steps", None),
            timeout_ms=getattr(args, "timeout_ms", None),
            recon_only=recon_only,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        return 2

    print("\n=== WQ-7 Platform Dry-Run Summary ===")
    print(f"  Total platforms: {summary.total_platforms}")
    print(f"  Total run: {summary.total_run}")
    print(f"  Total skipped: {summary.total_skipped}")
    print(f"  Review ready: {summary.total_review_ready}")
    print(f"  Needs user input: {summary.total_needs_user_input}")
    print(f"  Failed: {summary.total_failed}")
    print(f"  Recon complete: {summary.total_recon_complete}")
    print(f"  Submitted: {summary.total_submitted} (must be 0)")
    print()
    for r in summary.results:
        status = "SKIPPED" if r.skipped else r.status
        reason = r.skip_reason if r.skipped else r.stopped_reason
        submitted = r.submitted
        recon_label = ""
        if r.report and r.report.recon_observation:
            obs = r.report.recon_observation
            recon_label = f" controls={obs.visible_control_count} files={obs.file_input_count}"
        print(
            f"  {r.platform:20s} status={status:20s} reason={reason}"
            f" submitted={submitted}{recon_label}"
        )
        if r.report and r.report.errors:
            for err in r.report.errors:
                print(f"    error: {err}")

    # Exit code: 0 if at least one review_ready or recon_complete and zero submissions
    if summary.total_submitted > 0:
        return 1  # Should never happen
    if summary.total_review_ready > 0 or summary.total_recon_complete > 0:
        return 0
    if summary.total_needs_user_input > 0:
        return 3
    return 1


def _live_synthetic_mutation(settings: Settings, args: argparse.Namespace) -> int:
    """Run ONE WQ-7C controlled synthetic mutation (never submits)."""
    if not settings.live_synthetic_mutation:
        print(
            "error: WQ-7C synthetic mutation is disabled. Set "
            "UAA_LIVE_SYNTHETIC_MUTATION=true (it is OFF by default) and "
            "ensure UAA_ENABLE_REAL_SUBMISSION is not set.",
            file=sys.stderr,
        )
        return 2

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

    # WQ-7C safety gate: the job's candidate snapshot must be synthetic.
    from universal_auto_applier.synthetic_profile import is_synthetic_metadata

    if not is_synthetic_metadata(job.metadata):
        print(
            "error: this job does not carry a synthetic candidate snapshot "
            "(UAA_LIVE_SYNTHETIC_MUTATION only runs against synthetic "
            "identities). Refusing.",
            file=sys.stderr,
        )
        return 2

    headless = settings.browser_headless if args.headless is None else bool(args.headless)
    # Synthetic mutation always uses an ephemeral browser profile: it never
    # reuses saved cookies or login state.

    from universal_auto_applier.synthetic_profile import (
        SyntheticMutationProfile,
        approved_document_hashes,
        create_synthetic_mutation_documents,
    )

    profile = SyntheticMutationProfile()
    docs_dir = settings.data_dir / "synthetic-docs"
    cv, cover = create_synthetic_mutation_documents(docs_dir)
    approved = approved_document_hashes(cv, cover)

    config = LiveBrowserConfig(
        artifacts_root=args.artifacts_dir or settings.data_dir / "live-runs",
        profile_dir=None,
        headless=headless,
        channel=args.channel or settings.browser_channel,
        timeout_ms=args.timeout_ms or settings.browser_timeout_ms,
        max_steps=args.max_steps or settings.browser_max_steps,
        hard_submit_block=True,  # interlock armed before any mutation
        cookie_consent_policy=settings.cookie_consent_policy,  # type: ignore[arg-type]
    )
    budget = args.max_mutations or settings.synthetic_mutation_max_mutations
    budget = max(1, min(budget, settings.synthetic_mutation_max_mutations))

    # Point the job's document fields at the approved synthetic files so the
    # deterministic file mapper proposes ONLY approved synthetic documents.
    job = job.model_copy(update={"cv_pdf": str(cv), "cover_letter_pdf": str(cover)})

    report = LiveBrowserRunner(config).run_synthetic_mutation(
        job,
        profile,
        approved_document_hashes=approved,
        mutation_budget=budget,
    )

    print(f"status: {report.status}")
    print(f"stopped_reason: {report.stopped_reason}")
    print(f"final_url: {report.final_url}")
    print(f"clicks: {len(report.click_path)}")
    print(f"fields: {len(report.fields)}")
    print(f"uploads: {len(report.uploads)}")
    print("request_interlock_scope: LiveBrowserRunner.run_synthetic_mutation")
    print(f"request_interlock_installed: {report.request_interlock_installed}")
    print(f"request_interlock_coverage: {report.request_interlock_coverage}")
    print(f"request_interlock_limits: {'; '.join(report.request_interlock_limitations)}")
    print(f"blocked_http_requests: {report.blocked_http_request_count}")
    print(f"request_interlock_failure: {report.request_interlock_failure or 'none'}")
    print(f"request_outcome_unknown: {report.request_outcome_unknown}")
    print(f"plan_hash: {report.plan_hash}")
    print(f"plan_path: {report.mutation_plan_path}")
    print(f"plan_chain_hashes: {len(report.plan_chain_hashes)}")
    print(f"plan_chain_hash: {report.plan_chain_hash}")
    print(f"plan_chain_paths: {len(report.mutation_plan_chain_paths)}")
    counters = report.submit_interlock
    if counters is not None:
        print(f"interlock: installed={counters.installed} blocked={counters.blocked_submissions}")
    print(f"submitted: {report.submitted}")
    print(f"report: {report.report_path}")
    if report.errors:
        for err in report.errors:
            print(f"  error: {err}")
    if report.submitted:
        return 1
    if report.status == "review_ready":
        return 0
    if report.status == "needs_user_input":
        return 3
    return 2


def _load_review_snapshot(
    session: Any,
    application_id: str,
) -> tuple[Any, Any | None]:
    """Return (job, snapshot) for a review packet.

    Reads the latest persisted approval's snapshot for the application.
    ``create_approval`` keeps a single active approval; we read it via
    ``get_active_approval``.
    """
    from universal_auto_applier.submission.models import SubmissionSnapshot
    from universal_auto_applier.submission.store import get_active_approval

    job = list_application_jobs(session)
    job = next((j for j in job if j.application_id == application_id), None)
    if job is None:
        return None, None
    approval = get_active_approval(session, application_id)
    if approval is None or not approval.snapshot_json:
        return job, None
    return job, SubmissionSnapshot.model_validate(approval.snapshot_json)


def _wq8_review_packet(settings: Settings, args: argparse.Namespace) -> int:
    """Phase A: freeze the review plan and print the owner review packet.

    Sanitized output only — never prints filled values, real emails,
    phones, addresses, or document paths.
    """
    from universal_auto_applier.core.statuses import ApplicationStatus
    from universal_auto_applier.submission.authorization import (
        build_review_plan,
        compute_review_plan_hash,
    )

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            job, snapshot = _load_review_snapshot(session, str(args.application_id))
            if job is None:
                print(f"ERROR: no application matches {args.application_id!r}")
                return 2
            application_id = job.application_id
            if snapshot is None:
                print(
                    f"ERROR: {application_id[:12]} has no review snapshot. "
                    "Run the live review observation first (dashboard "
                    "observe endpoint)."
                )
                return 2
            # WQ-8 ATS URL separation: the canonical review plan must bind
            # the actual ATS application FORM URL from the persisted
            # snapshot, NOT job.url (which may be a detail page). For WQ-8
            # a populated snapshot MUST have a non-empty application_url;
            # a missing URL is a fail-closed error — we do NOT silently
            # fall back to job.url (detail page).
            if not snapshot.application_url or not snapshot.application_url.strip():
                print(
                    "ERROR: persisted snapshot has no application_url "
                    "(the WQ-8 review packet requires a real ATS form URL; "
                    "re-observe the live form before freezing)."
                )
                return 2
            canonical_form_url = snapshot.application_url
            # If --job-url is supplied and differs from snapshot.application_url,
            # fail closed — the owner must not override the canonical frozen
            # target via CLI.
            if args.job_url and args.job_url != canonical_form_url:
                print(
                    f"ERROR: --job-url {args.job_url!r} does not match the persisted "
                    f"snapshot application_url {canonical_form_url!r}. The canonical "
                    "review plan binds the actual ATS form URL; a CLI override cannot "
                    "change the frozen target."
                )
                return 2
            plan = build_review_plan(
                application_id=application_id,
                company=job.company,
                job_title=job.title,
                application_url=canonical_form_url,
                fields=snapshot.fields,
                documents=snapshot.documents,
                submit_control_text=(
                    snapshot.submit_control.text if snapshot.submit_control else ""
                ),
                submit_control_selector=(
                    snapshot.submit_control.selector if snapshot.submit_control else ""
                ),
                submit_control_frame_url=(
                    snapshot.submit_control.frame_url if snapshot.submit_control else ""
                ),
                pending_intervention_count=snapshot.pending_intervention_count,
            )
            frozen_hash = compute_review_plan_hash(plan)

            print("WQ-8 review packet (Phase A freeze; sanitized)")
            print(f"application_id:      {application_id}")
            print(f"status:              {str(job.status)}")
            if job.status != ApplicationStatus.REVIEW_READY:
                print(f"NOTICE: status is not review_ready (it is {job.status})")
            print(f"company:             {job.company}")
            print(f"job_title:           {job.title}")
            print(f"job_url (source):    {job.url}")
            print(f"application_url:     {canonical_form_url}")
            print(f"snapshot_hash:       {snapshot.snapshot_hash}")
            print(f"review_plan_hash:    {frozen_hash}")
            print(f"pending_interventions: {snapshot.pending_intervention_count}")
            high_risk = [f for f in snapshot.fields if f.risk_level == "high"]
            unconfirmed = [f for f in snapshot.fields if f.requires_confirmation]
            print(
                f"fields:              {len(snapshot.fields)} (high-risk {len(high_risk)}, "
                f"requires-confirmation {len(unconfirmed)})"
            )
            print(f"documents:           {len(snapshot.documents)}")
            print(
                "document hashes: "
                + ", ".join(
                    f"{d.document_kind}={d.content_hash[:12]}"
                    for d in sorted(snapshot.documents, key=lambda d: d.document_kind)
                )
            )
            if snapshot.submit_control:
                print(
                    f"submit_control:      {snapshot.submit_control.text!r} "
                    f"({snapshot.submit_control.classification})"
                )
            print(
                "\nTo authorize the single real submission, run (see docs/evidence/wq-8/DESIGN.md):"
            )
            print(
                f"  python -m universal_auto_applier wq8-authorize "
                f"--application-id {application_id[:12]} "
                f"--review-plan-hash {frozen_hash} --confirm"
            )
            return 0
    finally:
        engine.dispose()


def _wq8_authorize(settings: Settings, args: argparse.Namespace) -> int:
    """Phase B (owner-only): create the single-use authorization."""
    from universal_auto_applier.core.statuses import ApplicationStatus
    from universal_auto_applier.submission.authorization import (
        build_review_plan,
        compute_review_plan_hash,
    )

    if not settings.enable_real_submission:
        print(
            "ERROR: UAA_ENABLE_REAL_SUBMISSION is not true. Real submission is disabled by default."
        )
        return 2
    if not args.confirm:
        print("ERROR: --confirm is required to authorize a real submission.")
        return 2

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            from universal_auto_applier.submission.authorization_store import (
                create_authorization,
                has_converted_submission,
            )

            job, snapshot = _load_review_snapshot(session, str(args.application_id))
            if job is None:
                print(f"ERROR: no application matches {args.application_id!r}")
                return 2
            application_id = job.application_id
            if snapshot is None:
                print(
                    f"ERROR: {application_id[:12]} has no review snapshot. "
                    "Run the live review observation first."
                )
                return 2
            if job.status != ApplicationStatus.REVIEW_READY:
                print(
                    f"ERROR: job status is {job.status}, not review_ready. "
                    "Only a review_ready job may be authorized."
                )
                return 2
            if has_converted_submission(session):
                print(
                    "ERROR: a real submission already exists somewhere. "
                    "The absolute one-submission limit is consumed."
                )
                return 2

            # WQ-8 ATS URL separation: the authorization must bind the actual
            # ATS application FORM URL from the persisted snapshot, NOT
            # job.url (which may be a detail page). For WQ-8 a populated
            # snapshot MUST have a non-empty application_url; missing is
            # fail-closed — no fallback to job.url (detail page).
            if not snapshot.application_url or not snapshot.application_url.strip():
                print(
                    "ERROR: persisted snapshot has no application_url "
                    "(the WQ-8 authorization requires a real ATS form URL; "
                    "re-observe the live form before authorizing)."
                )
                return 2
            canonical_form_url = snapshot.application_url
            # If --job-url is supplied and differs from snapshot.application_url,
            # fail closed.
            if args.job_url and args.job_url != canonical_form_url:
                print(
                    f"ERROR: --job-url {args.job_url!r} does not match the persisted "
                    f"snapshot application_url {canonical_form_url!r}. The authorization "
                    "must bind the actual ATS form URL; a CLI override cannot change it."
                )
                return 2
            plan = build_review_plan(
                application_id=application_id,
                company=job.company,
                job_title=job.title,
                application_url=canonical_form_url,
                fields=snapshot.fields,
                documents=snapshot.documents,
                submit_control_text=(
                    snapshot.submit_control.text if snapshot.submit_control else ""
                ),
                submit_control_selector=(
                    snapshot.submit_control.selector if snapshot.submit_control else ""
                ),
                submit_control_frame_url=(
                    snapshot.submit_control.frame_url if snapshot.submit_control else ""
                ),
                pending_intervention_count=snapshot.pending_intervention_count,
            )
            current_hash = compute_review_plan_hash(plan)
            if current_hash != args.review_plan_hash:
                print(
                    f"ERROR: review_plan_hash mismatch. Current {current_hash} != "
                    f"authorized {args.review_plan_hash}. The plan changed since "
                    "the review packet; return to Phase A and re-freeze."
                )
                return 2

            doc_hashes = sorted(d.content_hash for d in snapshot.documents if d.content_hash)
            from datetime import UTC, datetime, timedelta

            expires_at = datetime.now(UTC) + timedelta(hours=float(args.expires_in_hours))
            try:
                auth = create_authorization(
                    session,
                    application_id=application_id,
                    application_url=canonical_form_url,
                    job_company=job.company,
                    job_title=job.title,
                    review_plan_hash=current_hash,
                    document_hashes=doc_hashes,
                    expires_at=expires_at,
                )
            except ValueError as exc:
                print(f"ERROR: {exc}")
                return 2

        print("WQ-8 authorization created (single-use, absolute limit=1)")
        print(f"authorization_id:    {auth.authorization_id}")
        print(f"application_id:      {application_id}")
        print(f"review_plan_hash:    {current_hash}")
        print(f"expires_at:          {auth.expires_at.isoformat()}")
        print(
            "Then run the UNCHANGED controlled submit (see "
            "docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md):"
        )
        from universal_auto_applier.submission.store import get_active_approval

        with session_scope(session_factory) as session:
            approval = get_active_approval(session, application_id)
        if approval is not None:
            print(
                f"  python -m universal_auto_applier live-submit "
                f"--application-id {application_id[:12]} "
                f"--approval-id {approval.approval_id} --confirm"
            )
        return 0
    finally:
        engine.dispose()


def _wq8_status(settings: Settings, args: argparse.Namespace) -> int:
    """Read-only diagnostic: show WQ-8 authorization state."""
    from universal_auto_applier.submission.authorization_store import (
        authorization_to_model,
    )

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import (
                SubmissionAuthorizationRow,
            )

            job, _snapshot = _load_review_snapshot(session, str(args.application_id))
            if job is None:
                print(f"ERROR: no application matches {args.application_id!r}")
                return 2
            application_id = job.application_id
            stmt = (
                select(SubmissionAuthorizationRow)
                .where(SubmissionAuthorizationRow.application_id == application_id)
                .order_by(SubmissionAuthorizationRow.created_at.desc())
            )
            rows = session.execute(stmt).scalars().all()

        if not rows:
            print(f"WQ-8 authorization state for {application_id}: NONE")
            print("No authorization exists. Real submission is FORBIDDEN.")
            return 0
        for row in rows:
            model = authorization_to_model(row)
            state = (
                "ACTIVE"
                if model.is_active
                else (
                    "CONSUMED"
                    if model.consumed_at
                    else ("REVOKED" if model.revoked_at else "EXPIRED")
                )
            )
            print(f"authorization_id:  {row.authorization_id}")
            print(f"  state:           {state}")
            print(f"  review_plan_hash: {row.review_plan_hash}")
            print(f"  created_at:      {row.created_at.isoformat()}")
            if row.consumed_at:
                print(f"  consumed_at:     {row.consumed_at.isoformat()}")
            if row.revoked_at:
                print(f"  revoked_at:      {row.revoked_at.isoformat()}")
            print(f"  expires_at:      {row.expires_at.isoformat()}")
        return 0
    finally:
        engine.dispose()


def _supervisor_run(settings: Settings, args: argparse.Namespace) -> int:
    """Run the agent-assisted supervisor (V0, review-only, concurrency=1)."""
    import os as _os
    from pathlib import Path as _Path

    from universal_auto_applier.supervisor import PolicyEngine, SupervisorService, SupervisorTools
    from universal_auto_applier.supervisor.models import SupervisorLimits
    from universal_auto_applier.supervisor.planner import (
        DeterministicPlanner,
        OpenAICompatiblePlanner,
    )
    from universal_auto_applier.supervisor.policy import load_owner_policies

    if not getattr(args, "review_only", True):
        print(
            "error: V0 supervisor is always review-only — --no-review-only is refused.",
            file=sys.stderr,
        )
        return 2

    queue_path: str | None = str(args.queue) if getattr(args, "queue", None) else None
    if queue_path and not _Path(queue_path).exists():
        print(f"error: queue file not found: {queue_path}", file=sys.stderr)
        return 2

    owner_policy_path = getattr(args, "owner_policy", None)
    owner_policies = None
    if owner_policy_path:
        try:
            owner_policies = load_owner_policies(_Path(owner_policy_path))
        except Exception as exc:  # noqa: BLE001
            print(f"error: failed to load owner policy file: {exc}", file=sys.stderr)
            return 2

    application_ids: list[str] | None = getattr(args, "application_ids", None)

    engine, session_factory = _open_store(settings)
    try:
        policy_engine = PolicyEngine(owner_policies=owner_policies)
        # Choose planner: deterministic by default. Model-backed requires explicit opt-in.
        # - No flag, no env vars        → DeterministicPlanner (default)
        #   --planner deterministic     → DeterministicPlanner (explicit)
        #   --planner model             → OpenAICompatiblePlanner (explicit opt-in)
        # All UAA_SUPERVISOR_MODEL_* env vars populated without --planner model
        #   → MUST STILL BE DeterministicPlanner
        _model_base = _os.environ.get("UAA_SUPERVISOR_MODEL_BASE_URL", "").strip()
        _model_name = _os.environ.get("UAA_SUPERVISOR_MODEL_NAME", "").strip()
        _model_key = _os.environ.get("UAA_SUPERVISOR_MODEL_API_KEY", "").strip()
        _planner_arg = getattr(args, "planner", "deterministic")
        if _planner_arg == "model" and _model_base and _model_name and _model_key:
            planner: DeterministicPlanner | OpenAICompatiblePlanner = OpenAICompatiblePlanner(
                policy_engine
            )
            planner_type = f"model-backed ({_model_name} via {_model_base.split('/')[2] if '/' in _model_base else _model_base})"
        else:
            planner = DeterministicPlanner(policy_engine)
            planner_type = "deterministic"
        print(f"supervisor planner: {planner_type}")
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        service = SupervisorService(
            tools=tools,
            policy_engine=policy_engine,
            planner=planner,
            session_factory=session_factory,
            limits=SupervisorLimits(),
        )
        summary = service.run(queue_path=queue_path, application_ids=application_ids)
        import json as _json

        print(_json.dumps(summary.model_dump(), indent=2))
        print(f"\nsupervisor run {summary.run_id} completed")
        print(f"  imported: {summary.imported}  skipped_siemens: {summary.skipped_siemens}")
        print(
            f"  review_ready: {len(summary.review_ready)}  needs_human: {len(summary.needs_human)}"
        )
        print(f"  repair_needed: {len(summary.repair_needed)}  failed: {len(summary.failed)}")
        return 0
    finally:
        engine.dispose()


def _supervisor_status(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.store import (
        get_supervisor_run,
        list_supervisor_application_states,
        list_supervisor_events,
        list_supervisor_runs,
    )

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            run_id = getattr(args, "run_id", None)
            if run_id:
                row = get_supervisor_run(session, run_id)
                if row is None:
                    print(f"error: no supervisor run {run_id!r}", file=sys.stderr)
                    return 2
                runs = [row]
            else:
                runs = list_supervisor_runs(session, limit=5)
                if not runs:
                    print("No supervisor runs found.")
                    return 0
                if len(runs) > 1:
                    print("Latest supervisor runs (use --run-id to inspect one):")
                    for r in runs:
                        print(
                            f"  {r.run_id}  {r.status}  {r.started_at.isoformat()}  queue={r.queue_path!r}"
                        )
                    print()
                runs = [runs[0]]
            run = runs[0]
            print(f"run_id: {run.run_id}")
            print(f"status: {run.status}")
            print(f"queue_path: {run.queue_path}")
            print(f"review_only: {run.review_only}")
            print(f"started_at: {run.started_at.isoformat()}")
            if run.finished_at:
                print(f"finished_at: {run.finished_at.isoformat()}")
            if run.error_message:
                print(f"error: {run.error_message}")
            print(f"summary: {_json.dumps(run.summary_json, indent=2)}")
            states = list_supervisor_application_states(session, run_id=run.run_id)
            if states:
                print(f"\napplication states ({len(states)}):")
                for s in states:
                    print(
                        f"  {s.application_id[:12]}  {s.state:22}  reason={s.reason_code}  retry={s.retry_count}"
                    )
            events = list_supervisor_events(session, run_id=run.run_id, limit=50)
            if events:
                print(f"\nrecent events ({len(events)}):")
                for e in events[-10:]:
                    print(
                        f"  {e.created_at.isoformat()}  {e.application_id[:12]}  {e.action}->{e.resulting_state}  {e.reason_code}"
                    )
        return 0
    finally:
        engine.dispose()


def _supervisor_handoffs(settings: Settings, args: argparse.Namespace) -> int:
    from universal_auto_applier.supervisor.store import list_human_handoffs

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            rows = list_human_handoffs(
                session, run_id=getattr(args, "run_id", None), status=getattr(args, "status", None)
            )
            if not rows:
                print("No human handoffs found.")
                return 0
            for r in rows:
                print(
                    f"handoff_id: {r.handoff_id}  app={r.application_id[:12]}  reason={r.reason_code}  status={r.status}"
                )
                print(f"  company: {r.company}  role: {r.role}")
                print(f"  question: {r.question}")
                print(f"  action: {r.action_required}")
        return 0
    finally:
        engine.dispose()


def _supervisor_tickets(settings: Settings, args: argparse.Namespace) -> int:
    from universal_auto_applier.supervisor.store import list_repair_tickets

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            rows = list_repair_tickets(
                session, run_id=getattr(args, "run_id", None), status=getattr(args, "status", None)
            )
            if not rows:
                print("No repair tickets found.")
                return 0
            for r in rows:
                print(
                    f"ticket_id: {r.ticket_id}  app={r.application_id[:12]}  reason={r.reason_code}  status={r.status}"
                )
                print(f"  field: {r.field_label!r}  type={r.field_type}  ats={r.ats_family}")
                print(f"  failure: {r.actual_failure}")
        return 0
    finally:
        engine.dispose()


def _supervisor_review_ready(settings: Settings, args: argparse.Namespace) -> int:
    from universal_auto_applier.supervisor.store import (
        get_supervisor_run,
        list_supervisor_application_states,
        list_supervisor_runs,
    )

    engine, session_factory = _open_store(settings)
    try:
        with session_scope(session_factory) as session:
            run_id = getattr(args, "run_id", None)
            if run_id:
                run = get_supervisor_run(session, run_id)
                if run is None:
                    print(f"error: no supervisor run {run_id!r}", file=sys.stderr)
                    return 2
            else:
                runs = list_supervisor_runs(session, limit=1)
                if not runs:
                    print("No supervisor runs found.")
                    return 0
                run = runs[0]
            states = list_supervisor_application_states(session, run_id=run.run_id)
            ready = [s for s in states if s.state == "review_ready"]
            if not ready:
                print(f"No review-ready applications for run {run.run_id}.")
                return 0
            print(f"Review-ready applications for run {run.run_id} ({len(ready)}):")
            for s in ready:
                print(f"  {s.application_id}  reason={s.reason_code}")
        return 0
    finally:
        engine.dispose()


def _sanitize_failure_text(text: str, limit: int = 300) -> str:
    """Redact paths/secrets from failure text for agent-facing output."""
    import os as _os

    cleaned = str(text or "").replace(_os.path.expanduser("~"), "~")
    for token in ("sk-or-", "AIza", "xoxb-", "ghp_"):
        idx = cleaned.find(token)
        while idx != -1:
            end = idx
            while end < len(cleaned) and cleaned[end] not in (" ", '"', "'", "\n"):
                end += 1
            cleaned = cleaned[:idx] + token + "***" + cleaned[end:]
            idx = cleaned.find(token)
    return cleaned[:limit]


def _failure_evidence(
    session: Session, job: ApplicationJob, status: dict[str, Any]
) -> dict[str, Any]:
    """Build the sanitized failure-evidence block for one application.

    Sources: latest supervisor event + latest application state row for
    the app (both sanitized). Exposes no PII, cookies, tokens, HTML, or
    credentials — only reason codes, stage classification, host, and a
    redacted one-line error summary.
    """
    from urllib.parse import urlsplit

    from universal_auto_applier.supervisor.store import (
        list_supervisor_application_states,
        list_supervisor_events,
    )

    app_id = job.application_id
    try:
        target_host = (urlsplit(job.url).hostname or "").lower()
    except ValueError:
        target_host = ""

    events = list_supervisor_events(session, application_id=app_id, limit=200)
    last_event = events[-1] if events else None
    states = [s for s in list_supervisor_application_states(session) if s.application_id == app_id]
    last_state = states[-1] if states else None

    reason_code = None
    if last_event is not None and last_event.reason_code:
        reason_code = last_event.reason_code
    elif last_state is not None and last_state.reason_code:
        reason_code = last_state.reason_code

    snapshot_present = bool(status.get("snapshot_present"))
    pending = int(status.get("pending_intervention_count") or 0)
    unresolved = status.get("unresolved_required_field_count")
    failed = bool(reason_code) and (last_state is None or last_state.state != "review_ready")

    if reason_code == "application_expired":
        failure_stage: str | None = "target-preflight"
        target_status: str | None = "expired"
    elif snapshot_present or pending > 0:
        failure_stage = "preparation"
        target_status = "live"
    elif failed:
        failure_stage = "navigation"
        target_status = "unknown"
    else:
        failure_stage = None
        target_status = "live" if snapshot_present else "unknown"

    tool_result = ""
    if last_event is not None and last_event.tool_result:
        tool_result = _sanitize_failure_text(last_event.tool_result)

    return {
        "reason_code": reason_code,
        "failure_stage": failure_stage,
        "target_status": target_status,
        "ats": str(job.platform),
        "target_host": target_host,
        "error_category": reason_code if failed else None,
        "sanitized_error_summary": tool_result,
        "interaction_ready": bool(snapshot_present and (unresolved or 0) == 0 and pending == 0),
    }


def _supervisor_application(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        status = tools.get_application_status(args.application_id)
        with session_scope(session_factory) as session:
            job = tools.get_job(args.application_id)
            status["failure_evidence"] = (
                _failure_evidence(session, job, status) if job is not None else None
            )
        if getattr(args, "json", False):
            print(_json.dumps(status, indent=2))
        else:
            for k, v in status.items():
                print(f"{k}: {v}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_interventions(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        interventions = tools.get_interventions(args.application_id)
        if getattr(args, "json", False):
            print(_json.dumps([vars(i) for i in interventions], indent=2))
        else:
            for i in interventions:
                print(f"intervention_id: {i.intervention_id}")
                print(f"  kind: {i.kind}")
                print(f"  question: {i.question}")
                print(f"  field_label: {i.field_label}")
                print(f"  field_type: {i.field_type}")
                print(f"  options: {i.options}")
                print(f"  reason: {i.reason}")
                print(f"  required: {i.required}")
                print(f"  suggested_answer: {i.suggested_answer}")
                print(f"  confidence: {i.confidence}")
                print(f"  risk_level: {i.risk_level}")
                print(f"  category: {i.category}")
                print()
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_candidate_facts(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        job = tools.get_job(args.application_id)
        if job is None:
            print(f"error: application not found: {args.application_id}", file=sys.stderr)
            return 2
        keys = tools.candidate_fact_keys(job)
        result = {"application_id": args.application_id, "candidate_fact_keys": keys}
        if getattr(args, "json", False):
            print(_json.dumps(result, indent=2))
        else:
            for k in keys:
                print(k)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_review_packet(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        packet = tools.get_review_packet(args.application_id)
        if packet is None:
            print(f"error: no review packet available for {args.application_id}", file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            print(_json.dumps(packet, indent=2))
        else:
            for k, v in packet.items():
                print(f"{k}: {v}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_repair_context(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        status = tools.get_application_status(args.application_id)
        interventions = tools.get_interventions(args.application_id)
        snapshot = tools.load_review_snapshot(args.application_id)
        with session_scope(session_factory) as session:
            job = tools.get_job(args.application_id)
            evidence = _failure_evidence(session, job, status) if job is not None else None
        result = {
            "application_id": args.application_id,
            "status": status,
            "interventions": [vars(i) for i in interventions],
            "snapshot": snapshot.model_dump() if snapshot is not None else None,
            "failure_evidence": evidence,
        }
        if getattr(args, "json", False):
            print(_json.dumps(result, indent=2))
        else:
            print(_json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_resolve_intervention(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        file_bundle: list[dict[str, str]] | None = None
        if getattr(args, "file_bundle", None):
            file_bundle = []
            for fb in args.file_bundle:
                if ":" in fb:
                    kind, path = fb.split(":", 1)
                    file_bundle.append({"document_kind": kind, "content_hash": path})
                else:
                    file_bundle.append({"document_kind": "unknown", "content_hash": fb})
        result = tools.resolve_intervention(
            intervention_id=args.intervention_id,
            resolution=args.resolution,
            answer=args.answer,
            file_bundle=file_bundle,
            save_to_memory=getattr(args, "save_to_memory", False),
        )
        if getattr(args, "json", False):
            print(_json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def _supervisor_retry(settings: Settings, args: argparse.Namespace) -> int:
    import json as _json

    from universal_auto_applier.supervisor.tools import SupervisorTools

    engine, session_factory = _open_store(settings)
    try:
        tools = SupervisorTools(settings=settings, session_factory=session_factory)
        outcome = tools.retry_application(args.application_id)
        result = {
            "application_id": outcome.application_id,
            "blocked": outcome.blocked,
            "error": outcome.error,
            "snapshot": outcome.snapshot.model_dump() if outcome.snapshot is not None else None,
        }
        if getattr(args, "json", False):
            print(_json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


def run_command(argv: list[str], settings: Settings) -> int:
    """Run a non-server CLI command and return its process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "list-jobs":
        return _list_jobs(settings)
    if args.command == "queue-import":
        return _queue_import(settings, args)
    if args.command == "browser-session":
        return _browser_session(settings, args)
    if args.command == "live-dry-run":
        return _live_dry_run(settings, args)
    if args.command == "live-submit":
        return _live_submit(settings, args)
    if args.command == "live-dry-run-platforms":
        return _live_dry_run_platforms(settings, args)
    if args.command == "live-synthetic-mutation":
        return _live_synthetic_mutation(settings, args)
    if args.command == "wq8-review-packet":
        return _wq8_review_packet(settings, args)
    if args.command == "wq8-authorize":
        return _wq8_authorize(settings, args)
    if args.command == "wq8-status":
        return _wq8_status(settings, args)
    if args.command == "supervisor-run":
        return _supervisor_run(settings, args)
    if args.command == "supervisor-status":
        return _supervisor_status(settings, args)
    if args.command == "supervisor-handoffs":
        return _supervisor_handoffs(settings, args)
    if args.command == "supervisor-tickets":
        return _supervisor_tickets(settings, args)
    if args.command == "supervisor-review-ready":
        return _supervisor_review_ready(settings, args)
    if args.command == "supervisor-application":
        return _supervisor_application(settings, args)
    if args.command == "supervisor-interventions":
        return _supervisor_interventions(settings, args)
    if args.command == "supervisor-candidate-facts":
        return _supervisor_candidate_facts(settings, args)
    if args.command == "supervisor-review-packet":
        return _supervisor_review_packet(settings, args)
    if args.command == "supervisor-repair-context":
        return _supervisor_repair_context(settings, args)
    if args.command == "supervisor-resolve-intervention":
        return _supervisor_resolve_intervention(settings, args)
    if args.command == "supervisor-retry":
        return _supervisor_retry(settings, args)
    parser.error(f"unknown command: {args.command}")
    return 2


__all__ = ["run_command"]
