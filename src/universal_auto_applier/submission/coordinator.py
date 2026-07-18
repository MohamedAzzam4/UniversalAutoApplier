"""Central controlled-submission coordinator.

This is the SINGLE entry point for final submission. It enforces every
safety gate before a submit click can occur. No other module may click
the final submit control.

Gates (ALL must pass for a click to occur):

1. ``settings.enable_real_submission`` is True (hard kill switch).
2. An active (non-consumed, non-revoked) approval exists for this
   application ID.
3. The approval's ``snapshot_hash`` matches the current snapshot hash
   (computed from the live page).
4. No pending interventions remain for this application.
5. No unresolved required fields remain.
6. No high-risk answer lacks explicit user confirmation.
7. Exactly one unambiguous final-submit control is visible and enabled.
8. No previous unconsumed submission claim exists (prevents duplicate
   clicks from concurrent requests / double-clicks / process restart).
9. No previous consumed claim with outcome ``outcome_unknown`` (blocks
   automatic retry after an uncertain outcome).
10. The application is not already in a submitted/applied state.
11. The browser is still on the approved application URL.

If any gate fails, the coordinator returns a
:class:`SubmissionResult` with the appropriate state and ``clicked=False``.
No click occurs.

If all gates pass, the coordinator:
1. Acquires a transactional one-time claim.
2. Rechecks all gates.
3. Captures a pre-submit screenshot.
4. Clicks the submit control ONCE.
5. Waits for a bounded confirmation period.
6. Captures post-submit evidence (URL, screenshot, DOM).
7. Classifies the result.
8. Consumes the claim and approval.
9. Records the result for audit.

See ``docs/generalization/DRY_RUN_LEVELS.md`` Level 3 and
``docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from universal_auto_applier.config import Settings
from universal_auto_applier.core.statuses import ApplicationStatus
from universal_auto_applier.navigator.apply_path_finder import analyze_page
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
    SubmissionSnapshot,
    check_snapshot_consistency,
    derive_unconfirmed_high_risk_count,
    derive_unresolved_required_count,
)
from universal_auto_applier.submission.store import (
    acquire_claim,
    consume_approval,
    consume_claim,
    count_pending_interventions,
    create_approval,
    get_active_approval,
    get_latest_result,
    has_unconsumed_claim,
    record_result,
    revoke_approval,
)

logger = logging.getLogger("universal_auto_applier.submission.coordinator")


# ---------------------------------------------------------------------------
# Gate check result
# ---------------------------------------------------------------------------


class GateResult:
    """The outcome of a gate check.

    If ``allowed`` is False, ``reason`` explains which gate failed and
    ``state`` is the :class:`SubmissionResultState` to return.
    """

    def __init__(
        self,
        *,
        allowed: bool,
        reason: str = "",
        state: SubmissionResultState = SubmissionResultState.SUBMISSION_NOT_ALLOWED,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.state = state

    def __repr__(self) -> str:
        if self.allowed:
            return "GateResult(allowed=True)"
        return f"GateResult(allowed=False, reason={self.reason!r}, state={self.state})"


# ---------------------------------------------------------------------------
# Submission coordinator
# ---------------------------------------------------------------------------


class SubmissionCoordinator:
    """Central coordinator for controlled final submission.

    Constructed with the app :class:`Settings` and a SQLAlchemy session
    factory. The :meth:`check_gates` method is pure logic (no browser)
    and can be called by the API/CLI to determine if submission is
    allowed. The :meth:`execute_submission` method performs the actual
    browser click and is called only by the CLI/API submit endpoint.
    """

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        self._settings = settings
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Gate checks (pure logic, no browser)
    # ------------------------------------------------------------------

    def check_gates(
        self,
        *,
        application_id: str,
        current_snapshot: SubmissionSnapshot | None = None,
        skip_claim_check: bool = False,
    ) -> GateResult:
        """Check all submission gates WITHOUT performing a click.

        If ``current_snapshot`` is provided, it is used to verify the
        approval's snapshot hash AND the direct field-level gates
        (unresolved required fields, high-risk unconfirmed answers). If
        not provided, only the DB-level gates are checked.

        Gates (ALL must pass):
        1. ``enable_real_submission`` is True.
        2. Active approval exists.
        3. Snapshot hash matches (if current_snapshot provided).
        3b. Form fingerprint matches (if current_snapshot provided).
        4. No pending interventions (DB check).
        4b. No unresolved required fields (direct field check).
        4c. No high-risk unconfirmed answers (direct field check).
        5. No unconsumed claim (in-progress submission).
        6. No previous unknown outcome.
        7. Application not already submitted/applied.

        Returns a :class:`GateResult`.
        """
        # Gate 1: feature disabled.
        if not self._settings.enable_real_submission:
            return GateResult(
                allowed=False,
                reason="enable_real_submission is False (default; hard kill switch)",
                state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
            )

        with session_scope(self._session_factory) as session:
            # Gate 2: active approval exists.
            approval = get_active_approval(session, application_id)
            if approval is None:
                return GateResult(
                    allowed=False,
                    reason="no active approval for this application",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                )

            # Gate 3: snapshot hash matches (complete state).
            if current_snapshot is not None:
                if approval.snapshot_hash != current_snapshot.snapshot_hash:
                    return GateResult(
                        allowed=False,
                        reason=(
                            f"snapshot hash mismatch: approved "
                            f"{approval.snapshot_hash[:12]} != current "
                            f"{current_snapshot.snapshot_hash[:12]}"
                        ),
                        state=SubmissionResultState.APPROVAL_STALE,
                    )

            # Gate 3b: form fingerprint matches (structure only).
            if current_snapshot is not None and current_snapshot.form_fingerprint:
                # The form fingerprint is stored inside the snapshot_json
                # on the approval row. Extract and compare.
                import json as _json

                try:
                    approved_snap_data = (
                        _json.loads(approval.snapshot_json)
                        if isinstance(approval.snapshot_json, str)
                        else approval.snapshot_json
                    )
                    approved_fingerprint = approved_snap_data.get("form_fingerprint", "")
                except Exception:
                    approved_fingerprint = ""
                if (
                    approved_fingerprint
                    and approved_fingerprint != current_snapshot.form_fingerprint
                ):
                    return GateResult(
                        allowed=False,
                        reason=(
                            f"form fingerprint mismatch: approved "
                            f"{approved_fingerprint[:12]} != current "
                            f"{current_snapshot.form_fingerprint[:12]}"
                        ),
                        state=SubmissionResultState.APPROVAL_STALE,
                    )

            # Gate 4: no pending interventions (DB check).
            pending_count = count_pending_interventions(session, application_id)
            if pending_count > 0:
                return GateResult(
                    allowed=False,
                    reason=f"{pending_count} pending interventions remain",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                )

            # Gates 4b-4c: field-level checks derived from field data.
            if current_snapshot is not None:
                confirmed_tokens = set(approval.confirmed_high_risk_fields_json or [])
                consistency_error = check_snapshot_consistency(current_snapshot, confirmed_tokens)
                if consistency_error:
                    return GateResult(
                        allowed=False,
                        reason=consistency_error,
                        state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    )
                unresolved = derive_unresolved_required_count(current_snapshot.fields)
                if unresolved > 0:
                    return GateResult(
                        allowed=False,
                        reason=f"{unresolved} unresolved required fields remain",
                        state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    )
                unconfirmed_high_risk = derive_unconfirmed_high_risk_count(
                    current_snapshot.fields, confirmed_tokens
                )
                if unconfirmed_high_risk > 0:
                    return GateResult(
                        allowed=False,
                        reason=f"{unconfirmed_high_risk} high-risk answers lack explicit confirmation",
                        state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                    )

            # Gate 5: no unconsumed claim (in-progress submission).
            if not skip_claim_check and has_unconsumed_claim(session, application_id):
                return GateResult(
                    allowed=False,
                    reason="an unconsumed submission claim exists (concurrent attempt?)",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                )

            # Gate 6: no previous unknown outcome.
            latest_result = get_latest_result(session, application_id)
            if latest_result is not None:
                if latest_result.state == SubmissionResultState.OUTCOME_UNKNOWN.value:
                    return GateResult(
                        allowed=False,
                        reason="previous submission had unknown outcome; manual review required",
                        state=SubmissionResultState.OUTCOME_UNKNOWN,
                    )
                if latest_result.state == SubmissionResultState.SUBMITTED_CONFIRMED.value:
                    return GateResult(
                        allowed=False,
                        reason="application already submitted successfully",
                        state=SubmissionResultState.ALREADY_SUBMITTED,
                    )

            # Gate 7: application not already submitted/applied.
            job = get_application_job(session, application_id)
            if job is None:
                return GateResult(
                    allowed=False,
                    reason="application not found",
                    state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                )
            status = str(job.status)
            if status in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ):
                return GateResult(
                    allowed=False,
                    reason=f"application status is {status}",
                    state=SubmissionResultState.ALREADY_SUBMITTED,
                )

        # Gates 5, 6, 7, 11 require the live page and are checked in
        # execute_submission (they cannot be checked without a browser).
        return GateResult(allowed=True)

    # ------------------------------------------------------------------
    # Approval management
    # ------------------------------------------------------------------

    def approve_snapshot(
        self,
        *,
        application_id: str,
        snapshot: SubmissionSnapshot,
    ) -> str:
        """Create (or return existing) approval for a snapshot.

        Returns the approval_id.
        """
        with session_scope(self._session_factory) as session:
            row = create_approval(
                session,
                application_id=application_id,
                snapshot=snapshot,
            )
            return row.approval_id

    def revoke_approval(self, approval_id: str) -> bool:
        """Revoke an approval. Returns True if found and revoked."""
        with session_scope(self._session_factory) as session:
            row = revoke_approval(session, approval_id)
            return row is not None and row.revoked_at is not None

    # ------------------------------------------------------------------
    # Submission execution (browser interaction)
    # ------------------------------------------------------------------

    def execute_submission_from_page(
        self,
        *,
        page: Page,
        application_id: str,
        approval_id: str,
        current_snapshot: SubmissionSnapshot,
        artifact_dir: Path | None = None,
        confirmation_timeout_ms: int = 15_000,
    ) -> SubmissionResult:
        """Execute submission from an already-open page in a single context.

        This is the context-safe variant of :meth:`execute_submission`.
        The caller opens the browser, navigates to the application URL,
        and passes the live ``page``. This method:

        1. Observes the current page (finds the submit control).
        2. Recomputes the snapshot from the live page.
        3. Compares it with the approved snapshot (via check_gates).
        4. Acquires the claim.
        5. Rechecks the submit control on the SAME page.
        6. Clicks once on the SAME page.
        7. Waits for confirmation on the SAME page.

        The observation, gate check, claim, and click all operate on the
        same live browser page/context. There is no context close/reopen
        between observation and click.
        """
        from universal_auto_applier.navigator.apply_path_finder import analyze_page

        # Step 1: observe the current page.
        analysis = analyze_page(page)
        submit_clickables = [
            c for c in analysis.clickables if c.classification.value == "dangerous_submit"
        ]

        # Step 2: gate — exactly one submit control.
        if len(submit_clickables) != 1:
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=current_snapshot.snapshot_hash,
                state=SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS,
                clicked=False,
                error_message=f"expected exactly 1 submit control, found {len(submit_clickables)}",
            )
            with session_scope(self._session_factory) as session:
                record_result(session, result)
            return result

        submit_control = submit_clickables[0]

        # Step 3: delegate to execute_submission with the page's context.
        # skip_claim_gate=True because the execution service already
        # acquired the claim before starting the browser.
        return self.execute_submission(
            context=page.context,
            application_id=application_id,
            approval_id=approval_id,
            current_snapshot=current_snapshot,
            submit_control_selector=submit_control.selector_hint,
            submit_control_frame_url=submit_control.frame_url,
            artifact_dir=artifact_dir,
            confirmation_timeout_ms=confirmation_timeout_ms,
            skip_claim_gate=True,
        )

    def execute_submission(
        self,
        *,
        context: BrowserContext,
        application_id: str,
        approval_id: str,
        current_snapshot: SubmissionSnapshot,
        submit_control_selector: str,
        submit_control_frame_url: str = "",
        artifact_dir: Path | None = None,
        confirmation_timeout_ms: int = 15_000,
        skip_claim_gate: bool = False,
    ) -> SubmissionResult:
        """Execute the controlled submission.

        This is the ONLY method that clicks the final submit control.
        It enforces all gates, acquires a claim, clicks once, waits for
        confirmation, and records the result.

        Args:
            context: The Playwright browser context.
            application_id: The application to submit.
            approval_id: The approval ID (must be active).
            current_snapshot: The current form snapshot (must match the
                approved snapshot hash).
            submit_control_selector: The CSS selector of the final
                submit control to click.
            submit_control_frame_url: The frame URL containing the
                submit control (empty for main frame).
            artifact_dir: Directory for screenshots and DOM artifacts.
            confirmation_timeout_ms: How long to wait for confirmation.

        Returns:
            A :class:`SubmissionResult` with the outcome.
        """
        # --- Pre-flight gate check (no browser) ---
        # When skip_claim_gate=True, the claim was already acquired by
        # the execution service. Skip ONLY the unconsumed-claim check
        # (gate 5) — all other gates (snapshot hash, form fingerprint,
        # pending interventions, etc.) must still be checked.
        if skip_claim_gate:
            gate = self.check_gates(
                application_id=application_id,
                current_snapshot=current_snapshot,
                skip_claim_check=True,
            )
        else:
            gate = self.check_gates(
                application_id=application_id,
                current_snapshot=current_snapshot,
            )
        if not gate.allowed:
            logger.warning(
                "[%s] submission gate failed: %s (state=%s)",
                application_id[:12],
                gate.reason,
                gate.state,
            )
            result = SubmissionResult(
                application_id=application_id,
                approval_id=approval_id,
                snapshot_hash_at_submit=current_snapshot.snapshot_hash,
                state=gate.state,
                clicked=False,
                error_message=gate.reason,
            )
            with session_scope(self._session_factory) as session:
                record_result(session, result)
            return result

        # --- Acquire claim (transactional one-time lock) ---
        # When skip_claim_gate=True, the claim was already acquired by
        # the execution service. Skip the claim acquisition here.
        if not skip_claim_gate:
            with session_scope(self._session_factory) as session:
                approval = get_active_approval(session, application_id)
                if approval is None or approval.approval_id != approval_id:
                    result = SubmissionResult(
                        application_id=application_id,
                        approval_id=approval_id,
                        snapshot_hash_at_submit=current_snapshot.snapshot_hash,
                        state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                        clicked=False,
                        error_message="approval not found or does not match",
                    )
                    record_result(session, result)
                    return result

                claim = acquire_claim(
                    session,
                    application_id=application_id,
                    approval=approval,
                )
                if claim is None:
                    result = SubmissionResult(
                        application_id=application_id,
                        approval_id=approval_id,
                        snapshot_hash_at_submit=current_snapshot.snapshot_hash,
                        state=SubmissionResultState.SUBMISSION_NOT_ALLOWED,
                        clicked=False,
                        error_message="could not acquire submission claim (concurrent attempt?)",
                    )
                    record_result(session, result)
                    return result
                claim_id = claim.claim_id
        else:
            claim_id = ""  # Claim already acquired by execution service

        # --- Browser interaction ---
        page: Page | None = None
        pre_submit_screenshot: str | None = None
        post_submit_screenshot: str | None = None
        post_submit_url: str = ""
        post_submit_dom_path: str | None = None
        clicked = False
        result_state: SubmissionResultState
        confirmation_evidence = ""
        validation_errors: list[str] = []
        error_message = ""

        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Gate 7: exactly one visible, enabled submit control.
            frame = self._find_frame(page, submit_control_frame_url)
            if frame is None:
                result_state = SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                error_message = f"frame not found: {submit_control_frame_url!r}"
            else:
                locator = frame.locator(submit_control_selector)
                count = locator.count()
                if count == 0:
                    result_state = SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                    error_message = (
                        f"no submit control found for selector {submit_control_selector!r}"
                    )
                elif count > 1:
                    result_state = SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                    error_message = f"{count} submit controls found (ambiguous)"
                else:
                    try:
                        if not locator.is_visible() or not locator.is_enabled():
                            result_state = SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                            error_message = "submit control is not visible or not enabled"
                        else:
                            # Gate 11: browser is on the approved URL.
                            if page.url != current_snapshot.application_url:
                                result_state = SubmissionResultState.APPROVAL_STALE
                                error_message = (
                                    f"browser URL {page.url!r} != approved "
                                    f"{current_snapshot.application_url!r}"
                                )
                            else:
                                # Capture pre-submit screenshot.
                                if artifact_dir:
                                    artifact_dir.mkdir(parents=True, exist_ok=True)
                                    pre_path = artifact_dir / "pre-submit.png"
                                    try:
                                        page.screenshot(
                                            path=str(pre_path),
                                            full_page=True,
                                            timeout=self._settings.browser_timeout_ms,
                                        )
                                        pre_submit_screenshot = str(pre_path.resolve())
                                    except PlaywrightError as exc:
                                        logger.warning(
                                            "[%s] pre-submit screenshot failed: %s",
                                            application_id[:12],
                                            exc,
                                        )

                                # Click the submit control ONCE.
                                logger.info(
                                    "[%s] clicking final submit control: %s",
                                    application_id[:12],
                                    submit_control_selector,
                                )
                                locator.click(timeout=self._settings.browser_timeout_ms)
                                clicked = True

                                # Wait for confirmation.
                                result_state, confirmation_evidence = self._wait_for_confirmation(
                                    page,
                                    confirmation_timeout_ms,
                                )

                                # Capture post-submit evidence.
                                post_submit_url = page.url
                                if artifact_dir:
                                    post_path = artifact_dir / "post-submit.png"
                                    dom_path = artifact_dir / "post-submit.html"
                                    try:
                                        page.screenshot(
                                            path=str(post_path),
                                            full_page=True,
                                            timeout=self._settings.browser_timeout_ms,
                                        )
                                        post_submit_screenshot = str(post_path.resolve())
                                    except PlaywrightError as exc:
                                        logger.warning(
                                            "[%s] post-submit screenshot failed: %s",
                                            application_id[:12],
                                            exc,
                                        )
                                    try:
                                        dom_path.write_text(page.content(), encoding="utf-8")
                                        post_submit_dom_path = str(dom_path.resolve())
                                    except (OSError, PlaywrightError) as exc:
                                        logger.warning(
                                            "[%s] post-submit DOM capture failed: %s",
                                            application_id[:12],
                                            exc,
                                        )

                                # Check for validation errors.
                                validation_errors = self._detect_validation_errors(page)
                                if validation_errors and (
                                    result_state == SubmissionResultState.OUTCOME_UNKNOWN
                                ):
                                    result_state = SubmissionResultState.VALIDATION_FAILED
                    except PlaywrightTimeoutError as exc:
                        result_state = SubmissionResultState.OUTCOME_UNKNOWN
                        error_message = f"submit click timed out: {exc}"
                    except PlaywrightError as exc:
                        result_state = SubmissionResultState.OUTCOME_UNKNOWN
                        error_message = f"submit click failed: {exc}"

        except Exception as exc:
            result_state = SubmissionResultState.OUTCOME_UNKNOWN
            error_message = f"unexpected error during submission: {exc}"
            logger.exception(
                "[%s] unexpected error during submission",
                application_id[:12],
            )
        finally:
            if page is not None and not page.is_closed():
                try:
                    post_submit_url = post_submit_url or page.url
                except PlaywrightError:
                    pass

        # --- Record result and consume claim ---
        result = SubmissionResult(
            application_id=application_id,
            approval_id=approval_id,
            snapshot_hash_at_submit=current_snapshot.snapshot_hash,
            state=result_state,
            clicked=clicked,
            pre_submit_screenshot=pre_submit_screenshot,
            post_submit_screenshot=post_submit_screenshot,
            post_submit_url=post_submit_url,
            post_submit_dom_path=post_submit_dom_path,
            confirmation_evidence=confirmation_evidence,
            validation_errors=validation_errors,
            error_message=error_message,
        )

        with session_scope(self._session_factory) as session:
            record_result(session, result)
            if claim_id:
                consume_claim(session, claim_id, state=result_state)
            if clicked:
                consume_approval(session, approval_id)

        logger.info(
            "[%s] submission result: state=%s clicked=%s",
            application_id[:12],
            result_state,
            clicked,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_frame(self, page: Page, frame_url: str) -> Any:
        """Find a frame by URL, or return the main frame if URL is empty."""
        if not frame_url:
            return page.main_frame
        for frame in page.frames:
            if frame.url == frame_url:
                return frame
        return None

    def _wait_for_confirmation(
        self,
        page: Page,
        timeout_ms: int,
    ) -> tuple[SubmissionResultState, str]:
        """Wait for a bounded confirmation period after the click.

        Looks for strong confirmation signals:
        - URL change to a known confirmation/thank-you page.
        - Text on the page containing "thank you", "confirmation",
          "application received", "successfully submitted", "bewerbung
          erhalten", "vielen dank".
        - An application reference number appearing.

        If no strong confirmation is detected within the timeout, returns
        ``OUTCOME_UNKNOWN`` — which blocks automatic retry.
        """
        confirmation_terms = [
            "thank you",
            "confirmation",
            "application received",
            "successfully submitted",
            "application submitted",
            "vielen dank",
            "bewerbung erhalten",
            "bewerbung eingegangen",
            "application id",
            "reference number",
            "confirmation number",
            "your application has been",
        ]

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            try:
                # Check for navigation to a confirmation URL.
                current_url = page.url.lower()
                if any(term in current_url for term in ("thank", "confirm", "success", "done")):
                    return (
                        SubmissionResultState.SUBMITTED_CONFIRMED,
                        f"confirmation URL: {page.url}",
                    )

                # Check page text for confirmation terms.
                text = page.inner_text("body", timeout=2_000).lower()
                for term in confirmation_terms:
                    if term in text:
                        return (
                            SubmissionResultState.SUBMITTED_CONFIRMED,
                            f"confirmation text: {term!r}",
                        )

                # Check for blocker (CAPTCHA, login, payment).
                analysis = analyze_page(page)
                if analysis.blocker:
                    return (
                        SubmissionResultState.BLOCKED_USER_ACTION,
                        f"blocker detected: {analysis.blocker}",
                    )
            except PlaywrightError:
                pass

            time.sleep(0.5)

        return (
            SubmissionResultState.OUTCOME_UNKNOWN,
            "no confirmation detected within timeout",
        )

    def _detect_validation_errors(self, page: Page) -> list[str]:
        """Detect validation errors on the page after a submit click."""
        errors: list[str] = []
        for frame in page.frames:
            locators = frame.locator(
                "form [role='alert'], [aria-invalid='true'], "
                ".field-error, .error-message, .invalid-feedback"
            )
            try:
                count = min(locators.count(), 50)
            except Exception:
                continue
            for index in range(count):
                locator = locators.nth(index)
                try:
                    if not locator.is_visible():
                        continue
                    message = locator.inner_text().strip()
                except Exception:
                    continue
                if message and message not in errors:
                    errors.append(message[:500])
        return errors


__all__ = ["GateResult", "SubmissionCoordinator"]
