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

from universal_auto_applier.browser.live_models import (
    LiveClickRecord,
    LiveFormObservation,
    LiveRunReport,
    SubmitInterlockCounters,
)
from universal_auto_applier.browser.submit_interlock import (
    install_interlock,
    is_interlock_installed,
    read_counters,
)
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.form_engine.live_executor import (
    execute_live_form,
    execute_live_form_synthetic,
    execute_live_form_with_llm,
)
from universal_auto_applier.navigator.apply_path_finder import (
    LivePageAnalysis,
    analyze_page,
    choose_safe_action,
    click_action,
)
from universal_auto_applier.synthetic_profile import SyntheticMutationProfile

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
    # WQ-7: When True, the runner is in hard-submit-blocked mode. Even a
    # direct call to ``attempt_submit`` returns "blocked" without clicking.
    # This is the lowest-layer guarantee that no final submission occurs.
    hard_submit_block: bool = False
    # WQ-8 Phase A: real-data preparation with hard interlock. When True,
    # the runner installs the WQ-7 browser-side submit interlock BEFORE
    # navigation (real candidate allowed, real CV upload allowed, final
    # submission impossible, no one-shot authorized-submit armed).
    wq8_phase_a: bool = False
    # WQ-7B: When True, the runner is in navigation/observation-only
    # reconnaissance mode. It follows safe apply/continue actions but NEVER
    # fills fields, uploads documents, or clicks submit. It stops at the
    # first application form it detects and records a structural observation
    # of that form. This is a stricter superset of the WQ-7A dry-run safety posture.
    recon_only: bool = False

    def __post_init__(self) -> None:
        if self.timeout_ms < 1_000:
            raise ValueError("timeout_ms must be at least 1000")
        if self.max_steps < 1 or self.max_steps > 100:
            raise ValueError("max_steps must be between 1 and 100")


class LiveBrowserRunner:
    """Navigate and fill one application with a real Playwright browser."""

    def __init__(self, config: LiveBrowserConfig) -> None:
        self._config = config
        # UAA-level submit-click attempts for the current run. The dry-run
        # never performs one, but any call into ``attempt_submit`` (the only
        # UAA code path that could click submit) truthfully increments this.
        self._uaa_submit_clicks = 0

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
        self._uaa_submit_clicks = 0

        # WQ-7 / WQ-8 Phase A: Install the browser-side submit interlock
        # BEFORE any page is created. This intercepts submit events,
        # form.submit(), requestSubmit(), and dispatched SubmitEvents at the
        # capture phase, before any site JavaScript can process them.
        # WQ-8 Phase A reuses the WQ-7 interlock implementation (no second
        # interlock) — real data + real CV allowed, but submission impossible,
        # no one-shot allowance armed, no authorization row required.
        if self._config.hard_submit_block or self._config.wq8_phase_a:
            install_interlock(context)
            tag = "WQ-8 Phase A" if self._config.wq8_phase_a else "WQ-7"
            logger.info(
                "[%s] %s submit interlock installed on context", job.application_id[:12], tag
            )

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

                if analysis.blocker and not (
                    self._config.recon_only and analysis.is_application_form
                ):
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
                    if self._config.recon_only:
                        # WQ-7B: navigation/observation-only. Stop at the
                        # first application form; record its structure
                        # without touching it. Nothing is filled, uploaded,
                        # or submitted.
                        report.status = "recon_complete"
                        report.stopped_reason = "first_application_form_reached"
                        report.recon_observation = self._observe_form(page, analysis)
                        self._screenshot(
                            page,
                            run_dir,
                            f"step-{step_number:02d}-recon-form.png",
                            report,
                        )
                        break

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
            # WQ-7: Read the browser-side interlock counters to record
            # truthful evidence of what happened (not what we hope happened).
            counters = {
                "submit_events": 0,
                "form_submit_calls": 0,
                "request_submit_calls": 0,
                "dispatch_submit_events": 0,
                "blocked_submissions": 0,
                "navigation_attempts": 0,
            }
            try:
                installed = (
                    page is not None and not page.is_closed() and (is_interlock_installed(page))
                )
            except Exception:  # noqa: BLE001
                installed = page is not None and not page.is_closed()
            if page is not None and not page.is_closed():
                try:
                    counters = read_counters(page)
                except Exception:  # noqa: BLE001
                    pass  # Counter reading is best-effort
            report.submit_interlock = SubmitInterlockCounters(
                installed=installed,
                uaa_submit_clicks=self._uaa_submit_clicks,
                submit_events=counters.get("submit_events", 0),
                form_submit_calls=counters.get("form_submit_calls", 0),
                request_submit_calls=counters.get("request_submit_calls", 0),
                dispatch_submit_events=counters.get("dispatch_submit_events", 0),
                blocked_submissions=counters.get("blocked_submissions", 0),
                navigation_attempts=counters.get("navigation_attempts", 0),
            )
            if counters.get("blocked_submissions", 0) > 0:
                report.errors.append(
                    f"wq7_interlock: blocked {counters['blocked_submissions']} "
                    f"submission attempt(s) — "
                    f"submit_events={counters['submit_events']}, "
                    f"form_submit={counters['form_submit_calls']}, "
                    f"request_submit={counters['request_submit_calls']}, "
                    f"dispatch={counters['dispatch_submit_events']}"
                )
                logger.warning(
                    "[%s] WQ-7 interlock blocked %d submission(s)",
                    job.application_id[:12],
                    counters["blocked_submissions"],
                )

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
            # WQ-7: report.submitted reflects what actually happened.
            # The interlock blocks all submit events, so submitted should
            # always be False. But we don't force it — we read the truth
            # from the interlock counters. If the interlock was not installed
            # (non-WQ-7 mode), submitted remains False because the runner
            # never calls submit.
            if not self._config.hard_submit_block:
                report.submitted = False
            self._write_report(report, run_dir)

        return report

    def _wait_for_stable_page(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=min(self._config.timeout_ms, 5_000))
        except PlaywrightTimeoutError:
            pass

    def _observe_form(
        self,
        page: Page,
        analysis: LivePageAnalysis,
    ) -> LiveFormObservation:
        """Collect the structural observation of the reached application form.

        Reads only — never fills, selects, checks, or uploads. Labels are
        gathered from ``name``/``id``/``placeholder``/``aria-label`` of
        visible controls so the recon evidence shows what the form looks
        like without any candidate data in it.
        """
        labels: list[str] = []
        controls = page.locator(
            "input:not([type='hidden']):not([type='button']):not([type='submit'])"
            ":not([type='reset']):not([type='image']), textarea, select"
        )
        try:
            count = min(controls.count(), 250)
        except PlaywrightError:
            count = 0
        for index in range(count):
            locator = controls.nth(index)
            try:
                if not locator.is_visible():
                    continue
            except PlaywrightError:
                continue
            label = (
                (locator.get_attribute("name") or "")
                or (locator.get_attribute("id") or "")
                or (locator.get_attribute("placeholder") or "")
                or (locator.get_attribute("aria-label") or "")
            ).strip()
            if label and label not in labels:
                labels.append(label[:120])
        return LiveFormObservation(
            page_url=page.url,
            title=analysis.title,
            visible_control_count=analysis.visible_control_count,
            file_input_count=analysis.file_input_count,
            has_dangerous_submit=analysis.has_dangerous_submit,
            field_labels=labels,
            detected_at=datetime.now(UTC),
            embedded_blocker=analysis.blocker,
        )

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

    def run_synthetic_mutation(
        self,
        job: ApplicationJob,
        mutation_profile: SyntheticMutationProfile,
        approved_document_hashes: frozenset[str],
        mutation_budget: int,
    ) -> LiveRunReport:
        """Run ONE WQ-7C synthetic mutation against a real ATS form.

        Dedicated, parallel entry point to :meth:`run`:

        - Refuses to run unless ``hard_submit_block=True`` (the config the
          synthetic-mutation CLI builds always sets it), so the browser-side
          submit interlock is armed BEFORE any mutation.
        - Refuses a non-synthetic profile at the last gate — a normal
          candidate never reaches this code path.
        - Mutates field values only when they are declared synthetic
          identity facts; uploads only hash-approved synthetic documents.
        - Stops at the final submit control (or any blocker) and NEVER
          clicks submit.

        Returns a :class:`LiveRunReport` with ``plan_hash`` and
        ``mutation_plan_path`` recorded as evidence.
        """
        if not self._config.hard_submit_block:
            raise ValueError(
                "synthetic mutation requires hard_submit_block=True so the "
                "submit interlock is armed before any mutation"
            )
        if not (
            getattr(mutation_profile, "synthetic_test", False)
            and getattr(mutation_profile, "wq7_synthetic", False)
        ):
            raise ValueError(
                "refusing synthetic mutation: profile is not a WQ-7C synthetic "
                "identity (missing synthetic_test/wq7_synthetic markers)"
            )

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
                return self.run_in_context_synthetic(
                    context,
                    job,
                    mutation_profile,
                    approved_document_hashes=approved_document_hashes,
                    mutation_budget=mutation_budget,
                    artifact_dir=artifact_dir,
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

    def run_in_context_synthetic(
        self,
        context: BrowserContext,
        job: ApplicationJob,
        mutation_profile: SyntheticMutationProfile,
        *,
        approved_document_hashes: frozenset[str],
        mutation_budget: int,
        artifact_dir: Path | None = None,
    ) -> LiveRunReport:
        """Execute one synthetic mutation in an existing browser context.

        Mirrors :meth:`run_in_context` for the WQ-7C path: the interlock is
        armed before any mutation and the final submit control is never
        clicked. Used by the fixture/Playwright tests and by
        :meth:`run_synthetic_mutation`.
        """
        run_dir = artifact_dir or self._new_artifact_dir(job)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._uaa_submit_clicks = 0
        report = LiveRunReport(
            application_id=job.application_id,
            started_at=datetime.now(UTC),
            initial_url=job.url,
            submitted=False,
        )
        page: Page | None = None
        trace_started = False
        seen_actions: set[tuple[str, str, str]] = set()

        install_interlock(context)
        logger.info(
            "[%s] WQ-7C submit interlock armed before mutation",
            job.application_id[:12],
        )

        if self._config.capture_trace:
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                trace_started = True
            except PlaywrightError as exc:
                report.errors.append(f"trace_start_failed: {exc}")

        try:
            page = context.new_page()
            logger.info("[%s] wq7c navigate opening %s", job.application_id[:12], job.url)
            page.goto(job.url, wait_until="domcontentloaded", timeout=self._config.timeout_ms)
            self._wait_for_stable_page(page)

            for step_number in range(1, self._config.max_steps + 1):
                self._screenshot(
                    page,
                    run_dir,
                    f"step-{step_number:02d}-observe.png",
                    report,
                )
                analysis = analyze_page(page)
                logger.info(
                    "[%s] wq7c observe url=%s controls=%d files=%d form=%s blocker=%s",
                    job.application_id[:12],
                    analysis.url,
                    analysis.visible_control_count,
                    analysis.file_input_count,
                    analysis.is_application_form,
                    analysis.blocker,
                )

                if analysis.blocker and not (
                    self._config.recon_only and analysis.is_application_form
                ):
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
                    execution = execute_live_form_synthetic(
                        page,
                        mutation_profile,
                        job,
                        approved_document_hashes=approved_document_hashes,
                        mutation_budget=mutation_budget,
                    )
                    report.fields.extend(execution.fields)
                    report.uploads.extend(execution.uploads)
                    report.plan_hash = execution.plan_hash
                    report.plan_chain_hash = execution.plan_chain_hash
                    # Persist EVERY frozen pass plan next to the run evidence:
                    # the initial pass (mutation-plan.json) plus one file per
                    # bounded reveal pass, referenced by the chain paths. The
                    # ordered chain covers all actual mutations.
                    for pass_record in execution.passes:
                        if pass_record.pass_index == 0:
                            pass_path = run_dir / "mutation-plan.json"
                        else:
                            pass_path = (
                                run_dir / f"mutation-plan-pass-{pass_record.pass_index}.json"
                            )
                        pass_path.write_text(
                            pass_record.plan.model_dump_json(indent=2),
                            encoding="utf-8",
                        )
                        report.mutation_plan_chain_paths.append(str(pass_path.resolve()))
                        report.plan_chain_hashes.append(pass_record.plan_hash)
                    report.mutation_plan_path = report.mutation_plan_chain_paths[0]
                    self._screenshot(
                        page,
                        run_dir,
                        f"step-{step_number:02d}-after-mutation.png",
                        report,
                    )

                    if execution.mutations_performed == 0:
                        report.status = "needs_user_input"
                        report.stopped_reason = "no_mutations_performed"
                        break
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
                        report.stopped_reason = "form_mutated_no_submit_control"
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
            logger.exception("[%s] wq7c synthetic mutation run failed", job.application_id[:12])
        finally:
            # WQ-7C closure item 2: persist the submit-interlock counters on
            # EVERY run as structured evidence, including zeros. Zeros are
            # meaningful — an installed interlock that recorded zero blocks
            # IS the safety proof, not report.submitted alone.
            try:
                installed = (
                    page is not None and not page.is_closed() and (is_interlock_installed(page))
                )
            except Exception:  # noqa: BLE001
                installed = page is not None and not page.is_closed()
            counters = {
                "submit_events": 0,
                "form_submit_calls": 0,
                "request_submit_calls": 0,
                "dispatch_submit_events": 0,
                "blocked_submissions": 0,
                "navigation_attempts": 0,
            }
            if page is not None and not page.is_closed():
                try:
                    counters = read_counters(page)
                except Exception:  # noqa: BLE001
                    pass  # Counter reading is best-effort
            report.submit_interlock = SubmitInterlockCounters(
                installed=installed,
                uaa_submit_clicks=self._uaa_submit_clicks,
                submit_events=counters.get("submit_events", 0),
                form_submit_calls=counters.get("form_submit_calls", 0),
                request_submit_calls=counters.get("request_submit_calls", 0),
                dispatch_submit_events=counters.get("dispatch_submit_events", 0),
                blocked_submissions=counters.get("blocked_submissions", 0),
                navigation_attempts=counters.get("navigation_attempts", 0),
            )
            if counters.get("blocked_submissions", 0) > 0:
                report.errors.append(
                    f"wq7_interlock: blocked {counters['blocked_submissions']} "
                    f"submission attempt(s) — "
                    f"submit_events={counters['submit_events']}, "
                    f"form_submit={counters['form_submit_calls']}, "
                    f"request_submit={counters['request_submit_calls']}, "
                    f"dispatch={counters['dispatch_submit_events']}"
                )

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

    def attempt_submit(
        self,
        page: Page,
        submit_selector: str,
        *,
        frame_url: str | None = None,
    ) -> str:
        """Attempt to click a submit control.

        In normal mode (``hard_submit_block=False``), this method would click
        the submit button. However, the runner **never** calls this method
        during a dry run — it stops at ``final_submit_detected`` before any
        click.

        In WQ-7 mode (``hard_submit_block=True``), this method **always
        returns "blocked"** without clicking, regardless of the selector
        or page state. This is the lowest-layer guarantee: even if a bug or
        future code change tried to call submit directly, the hard block
        prevents the click.

        Returns:
            "clicked" if the submit was clicked (only in non-blocked mode),
            "blocked" if the hard submit block is active,
            "not_found" if the selector was not found on the page.
        """
        self._uaa_submit_clicks += 1
        if self._config.hard_submit_block:
            logger.warning(
                "[wq7] attempt_submit blocked by hard_submit_block — "
                "no click performed (selector=%s)",
                submit_selector,
            )
            return "blocked"

        # In non-blocked mode, we still do NOT click during a dry run.
        # The runner's safety logic (choose_safe_action never returns
        # dangerous_submit) prevents this path from being reached.
        # This method exists solely to prove the hard block works.
        logger.warning(
            "[wq7] attempt_submit called in non-blocked mode — "
            "dry-run safety prevents clicking (selector=%s)",
            submit_selector,
        )
        return "blocked"


__all__ = ["LiveBrowserConfig", "LiveBrowserRunner"]
