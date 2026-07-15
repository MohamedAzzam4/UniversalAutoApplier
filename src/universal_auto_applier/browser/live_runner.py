"""Live Playwright dry-run for one queued application job.

The runner performs real browser navigation, form filling, and file uploads.
It never clicks a final submit control. Every terminal path writes evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from universal_auto_applier.browser.live_models import LiveClickRecord, LiveRunReport
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.form_engine.live_executor import (
    execute_live_form,
    execute_live_form_with_llm,
)
from universal_auto_applier.navigator.apply_path_finder import (
    analyze_page,
    choose_safe_action,
    click_action,
)

logger = logging.getLogger("universal_auto_applier.browser.live_runner")


@dataclass(frozen=True)
class LiveBrowserConfig:
    """Runtime settings for one live dry-run."""

    artifacts_root: Path
    profile_dir: Path | None = None
    headless: bool = False
    channel: str | None = None
    timeout_ms: int = 30_000
    max_steps: int = 20
    capture_trace: bool = True

    def __post_init__(self) -> None:
        if self.timeout_ms < 1_000:
            raise ValueError("timeout_ms must be at least 1000")
        if self.max_steps < 1 or self.max_steps > 100:
            raise ValueError("max_steps must be between 1 and 100")


class LiveBrowserRunner:
    """Navigate and fill one application with a real Playwright browser."""

    def __init__(self, config: LiveBrowserConfig) -> None:
        self._config = config

    def _new_artifact_dir(self, job: ApplicationJob) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = self._config.artifacts_root / f"{job.application_id[:12]}-{timestamp}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def run(
        self,
        job: ApplicationJob,
        candidate: CandidateProfile | None = None,
        qa_service: Any = None,
    ) -> LiveRunReport:
        """Launch Chromium and execute one live dry-run.

        Args:
            job: The application job to apply to.
            candidate: Optional resolved candidate profile. If None,
                resolved from job metadata.
            qa_service: Optional QuestionAnsweringService for LLM-backed
                question resolution. If None, deterministic-only behavior
                is preserved.
        """
        artifact_dir = self._new_artifact_dir(job)
        browser: Browser | None = None
        context: BrowserContext | None = None
        try:
            with sync_playwright() as playwright:
                if self._config.profile_dir is not None:
                    self._config.profile_dir.mkdir(parents=True, exist_ok=True)
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self._config.profile_dir),
                        headless=self._config.headless,
                        channel=self._config.channel,
                        accept_downloads=False,
                    )
                else:
                    browser = playwright.chromium.launch(
                        headless=self._config.headless,
                        channel=self._config.channel,
                    )
                    context = browser.new_context(accept_downloads=False)
                return self.run_in_context(
                    context,
                    job,
                    candidate=candidate,
                    artifact_dir=artifact_dir,
                    qa_service=qa_service,
                )
        except Exception as exc:
            report = LiveRunReport(
                application_id=job.application_id,
                status="failed",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                initial_url=job.url,
                final_url="",
                stopped_reason="browser_launch_failed",
                errors=[f"{type(exc).__name__}: {exc}"],
                submitted=False,
            )
            self._write_report(report, artifact_dir)
            return report
        finally:
            if context is not None:
                try:
                    context.close()
                except PlaywrightError:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except PlaywrightError:
                    pass

    def run_in_context(
        self,
        context: BrowserContext,
        job: ApplicationJob,
        *,
        candidate: CandidateProfile | None = None,
        artifact_dir: Path | None = None,
        qa_service: Any = None,
    ) -> LiveRunReport:
        """Execute in an existing context; used by fixture tests and ``run``.

        Args:
            context: The browser context to use.
            job: The application job.
            candidate: Optional resolved candidate profile.
            artifact_dir: Optional directory for evidence artifacts.
            qa_service: Optional QuestionAnsweringService for LLM-backed
                question resolution. If None, only deterministic mapping
                is used (existing behavior).
        """
        run_dir = artifact_dir or self._new_artifact_dir(job)
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved_candidate = candidate or resolve_candidate_profile(job.metadata)
        report = LiveRunReport(
            application_id=job.application_id,
            started_at=datetime.now(UTC),
            initial_url=job.url,
            submitted=False,
        )
        page: Page | None = None
        trace_started = False
        seen_actions: set[tuple[str, str, str]] = set()

        if self._config.capture_trace:
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                trace_started = True
            except PlaywrightError as exc:
                report.errors.append(f"trace_start_failed: {exc}")

        try:
            page = context.new_page()
            logger.info("[%s] navigate opening %s", job.application_id[:12], job.url)
            page.goto(job.url, wait_until="domcontentloaded", timeout=self._config.timeout_ms)
            self._wait_for_stable_page(page)

            for step_number in range(1, self._config.max_steps + 1):
                observation_shot = self._screenshot(
                    page,
                    run_dir,
                    f"step-{step_number:02d}-observe.png",
                    report,
                )
                analysis = analyze_page(page)
                logger.info(
                    "[%s] observe url=%s controls=%d files=%d form=%s blocker=%s",
                    job.application_id[:12],
                    analysis.url,
                    analysis.visible_control_count,
                    analysis.file_input_count,
                    analysis.is_application_form,
                    analysis.blocker,
                )

                if analysis.blocker:
                    report.status = "needs_user_input"
                    report.stopped_reason = analysis.blocker
                    break
                if analysis.expired:
                    report.status = "needs_user_input"
                    report.stopped_reason = "job_expired"
                    break
                if analysis.submitted:
                    report.status = "needs_user_input"
                    report.stopped_reason = "already_submitted"
                    break

                if analysis.is_application_form:
                    # Use LLM-enhanced execution when a QA service is
                    # provided; otherwise fall back to deterministic-only.
                    if qa_service is not None:
                        execution = execute_live_form_with_llm(
                            page, resolved_candidate, job, qa_service=qa_service
                        )
                    else:
                        execution = execute_live_form(page, resolved_candidate, job)
                    report.fields.extend(execution.fields)
                    report.uploads.extend(execution.uploads)
                    self._screenshot(
                        page,
                        run_dir,
                        f"step-{step_number:02d}-after-fill.png",
                        report,
                    )

                    if execution.required_unresolved > 0:
                        report.status = "needs_user_input"
                        report.stopped_reason = "required_fields_unresolved"
                        break
                    if execution.validation_errors:
                        report.status = "needs_user_input"
                        report.stopped_reason = "validation_errors"
                        report.errors.extend(execution.validation_errors)
                        break

                    post_fill = analyze_page(page)
                    if post_fill.blocker:
                        report.status = "needs_user_input"
                        report.stopped_reason = post_fill.blocker
                        break
                    if post_fill.has_dangerous_submit:
                        report.status = "review_ready"
                        report.stopped_reason = "final_submit_detected"
                        self._screenshot(
                            page,
                            run_dir,
                            "before-final-submit.png",
                            report,
                        )
                        break

                    action = choose_safe_action(
                        post_fill,
                        allow_apply=False,
                        allow_continue=True,
                    )
                    if action is None:
                        report.status = "review_ready"
                        report.stopped_reason = "form_filled_no_submit_control"
                        break
                else:
                    action = choose_safe_action(
                        analysis,
                        allow_apply=True,
                        allow_continue=True,
                    )
                    if action is None:
                        report.status = "needs_user_input"
                        report.stopped_reason = "no_safe_apply_path"
                        break

                fingerprint = (page.url, action.selector_hint, action.text)
                if fingerprint in seen_actions:
                    report.status = "needs_user_input"
                    report.stopped_reason = "navigation_loop_detected"
                    break
                seen_actions.add(fingerprint)

                from_url = page.url
                logger.info(
                    "[%s] navigate click %s text=%r selector=%s",
                    job.application_id[:12],
                    action.classification,
                    action.text,
                    action.selector_hint,
                )
                try:
                    page = click_action(
                        context,
                        page,
                        action,
                        timeout_ms=self._config.timeout_ms,
                    )
                except Exception as exc:
                    report.status = "needs_user_input"
                    report.stopped_reason = "click_failed"
                    report.errors.append(f"click_failed: {exc}")
                    break
                report.click_path.append(
                    LiveClickRecord(
                        step_number=step_number,
                        from_url=from_url,
                        to_url=page.url,
                        text=action.text or action.aria_label,
                        classification=str(action.classification),
                        selector=action.selector_hint,
                        frame_url=action.frame_url,
                        screenshot=observation_shot,
                    )
                )
            else:
                report.status = "needs_user_input"
                report.stopped_reason = "max_steps_reached"

        except PlaywrightTimeoutError as exc:
            report.status = "needs_user_input"
            report.stopped_reason = "navigation_timeout"
            report.errors.append(str(exc))
        except Exception as exc:
            report.status = "failed"
            report.stopped_reason = "browser_execution_error"
            report.errors.append(f"{type(exc).__name__}: {exc}")
            logger.exception("[%s] live browser run failed", job.application_id[:12])
        finally:
            if page is not None and not page.is_closed():
                report.final_url = page.url
                self._screenshot(page, run_dir, "final.png", report)
                self._save_dom(page, run_dir, report)
            if trace_started:
                trace_path = run_dir / "trace.zip"
                try:
                    context.tracing.stop(path=str(trace_path))
                    report.trace_path = str(trace_path.resolve())
                except PlaywrightError as exc:
                    report.errors.append(f"trace_stop_failed: {exc}")
            report.finished_at = datetime.now(UTC)
            report.submitted = False
            self._write_report(report, run_dir)

        return report

    def _wait_for_stable_page(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=min(self._config.timeout_ms, 5_000))
        except PlaywrightTimeoutError:
            pass

    def _screenshot(
        self,
        page: Page,
        run_dir: Path,
        filename: str,
        report: LiveRunReport,
    ) -> str | None:
        path = run_dir / filename
        try:
            page.screenshot(path=str(path), full_page=True, timeout=self._config.timeout_ms)
        except PlaywrightError as exc:
            report.errors.append(f"screenshot_failed:{filename}: {exc}")
            return None
        resolved = str(path.resolve())
        if resolved not in report.screenshots:
            report.screenshots.append(resolved)
        return resolved

    def _save_dom(self, page: Page, run_dir: Path, report: LiveRunReport) -> None:
        path = run_dir / "final-page.html"
        try:
            path.write_text(page.content(), encoding="utf-8")
            report.dom_snapshot_path = str(path.resolve())
        except (OSError, PlaywrightError) as exc:
            report.errors.append(f"dom_snapshot_failed: {exc}")

    def _write_report(self, report: LiveRunReport, run_dir: Path) -> None:
        path = run_dir / "report.json"
        report.report_path = str(path.resolve())
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


__all__ = ["LiveBrowserConfig", "LiveBrowserRunner"]
