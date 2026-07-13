"""Safe exploration loop — navigate to a form without clicking submit.

Per ``ROADMAP.md`` WP 3.3, the SafeExplorer implements the loop:

    observe -> classify -> choose safe action -> click -> observe again

Stop conditions:
- form is visible
- login is required and no credentials are configured
- captcha is detected
- final submit is detected
- page is unknown
- max step count is reached

Safety rules:
- Unknown pages do not cause random clicks.
- Final submit is never clicked in generic dry-run.
- Every step is logged with URL, action, and screenshot path.

The SafeExplorer is designed to work with any callable that produces a
:class:`PageObservation` and any callable that clicks a selector. This
separation lets us test the exploration logic with fixture HTML and mock
clicks, without launching a browser.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from universal_auto_applier.core.models import Clickable, PageObservation
from universal_auto_applier.core.statuses import (
    ClickableClassification,
    PageState,
)

logger = logging.getLogger("universal_auto_applier.navigator.safe_explorer")

# Maximum number of exploration steps before giving up.
DEFAULT_MAX_STEPS = 10

# Safe classifications that the explorer is allowed to click.
_SAFE_CLASSIFICATIONS: frozenset[ClickableClassification] = frozenset(
    {
        ClickableClassification.SAFE_APPLY,
        ClickableClassification.SAFE_CONTINUE,
        ClickableClassification.SAFE_UPLOAD,
    }
)


class ObserverFn(Protocol):
    """Protocol for the observation callback."""

    def __call__(self) -> PageObservation: ...


class ClickFn(Protocol):
    """Protocol for the click callback."""

    def __call__(self, selector: str) -> bool: ...


@dataclass
class ExplorationStep:
    """One step in the exploration loop."""

    step_number: int
    url: str
    page_state: PageState
    action: str  # "click:safe_apply", "stop:form_visible", etc.
    selector: str | None = None
    screenshot: str | None = None


@dataclass
class ExplorationResult:
    """The outcome of a safe exploration run."""

    steps: list[ExplorationStep] = field(default_factory=list[ExplorationStep])
    final_state: PageState = PageState.UNKNOWN
    stopped_reason: str = ""
    final_observation: PageObservation | None = None

    @property
    def reached_form(self) -> bool:
        """True if the explorer reached a form page."""
        return self.final_state == PageState.FORM

    @property
    def step_count(self) -> int:
        return len(self.steps)


def _choose_action(observation: PageObservation) -> Clickable | None:
    """Choose the next safe clickable to click, or None if no safe action.

    Selection priority:
    1. safe_apply (highest priority — starts the application)
    2. safe_continue (advances to the next step)
    3. safe_upload (uploads a document)

    Returns None if no safe clickable is found.
    """
    safe_clickables = [
        c for c in observation.clickables if c.classification in _SAFE_CLASSIFICATIONS
    ]
    if not safe_clickables:
        return None

    # Sort by priority: safe_apply > safe_continue > safe_upload.
    priority = {
        ClickableClassification.SAFE_APPLY: 0,
        ClickableClassification.SAFE_CONTINUE: 1,
        ClickableClassification.SAFE_UPLOAD: 2,
    }
    safe_clickables.sort(key=lambda c: priority.get(c.classification, 99))
    return safe_clickables[0]


def safe_explore(
    observe: ObserverFn,
    click: ClickFn,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ExplorationResult:
    """Run the safe exploration loop.

    Args:
        observe: A callable that returns the current :class:`PageObservation`.
        click: A callable that takes a selector string, clicks the element,
            and returns True if the click succeeded.
        max_steps: Maximum number of exploration steps before stopping.

    Returns:
        An :class:`ExplorationResult` with all steps and the final state.

    Safety:
    - Never clicks ``dangerous_submit``.
    - Never clicks ``unknown`` elements.
    - Stops on captcha, login, form visible, submit detected, unknown page,
      or max steps.
    """
    result = ExplorationResult()
    observation: PageObservation | None = None

    for step_num in range(1, max_steps + 1):
        observation = observe()

        # Check stop conditions.
        state = observation.page_state

        if state == PageState.FORM:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:form_visible",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "form_visible"
            result.final_observation = observation
            logger.info("[step %d] stop: form visible", step_num)
            return result

        if state == PageState.CAPTCHA:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:captcha",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "captcha_detected"
            result.final_observation = observation
            logger.warning("[step %d] stop: captcha detected", step_num)
            return result

        if state == PageState.LOGIN:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:login_required",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "login_required"
            result.final_observation = observation
            logger.warning("[step %d] stop: login required", step_num)
            return result

        if state == PageState.REVIEW:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:review_page",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "review_page"
            result.final_observation = observation
            logger.info("[step %d] stop: review page", step_num)
            return result

        if state == PageState.SUBMITTED:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:already_submitted",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "already_submitted"
            result.final_observation = observation
            logger.info("[step %d] stop: already submitted", step_num)
            return result

        if state == PageState.ERROR:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:error",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "error_page"
            result.final_observation = observation
            logger.error("[step %d] stop: error page", step_num)
            return result

        if state == PageState.EXPIRED:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:expired",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "expired"
            result.final_observation = observation
            logger.warning("[step %d] stop: job expired", step_num)
            return result

        # Check for dangerous_submit clickables — stop if found.
        has_dangerous_submit = any(
            c.classification == ClickableClassification.DANGEROUS_SUBMIT
            for c in observation.clickables
        )
        if has_dangerous_submit and state != PageState.APPLY_PAGE:
            # A submit button on a non-apply page is suspicious. Stop.
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:submit_detected",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "submit_detected"
            result.final_observation = observation
            logger.info("[step %d] stop: submit button detected", step_num)
            return result

        # Choose a safe action.
        chosen = _choose_action(observation)
        if chosen is None:
            # No safe clickable found. If the page is unknown, stop.
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:no_safe_action",
                    screenshot=observation.screenshot,
                )
            )
            result.final_state = state
            result.stopped_reason = "no_safe_action"
            result.final_observation = observation
            logger.info("[step %d] stop: no safe clickable found", step_num)
            return result

        # Click the chosen element.
        action = f"click:{chosen.classification}"
        result.steps.append(
            ExplorationStep(
                step_number=step_num,
                url=observation.url,
                page_state=state,
                action=action,
                selector=chosen.selector,
                screenshot=observation.screenshot,
            )
        )
        logger.info(
            "[step %d] click: %s (selector=%s, text=%r)",
            step_num,
            chosen.classification,
            chosen.selector,
            chosen.text,
        )

        # Perform the click.
        success = click(chosen.selector)
        if not success:
            result.steps.append(
                ExplorationStep(
                    step_number=step_num,
                    url=observation.url,
                    page_state=state,
                    action="stop:click_failed",
                    selector=chosen.selector,
                )
            )
            result.final_state = state
            result.stopped_reason = "click_failed"
            result.final_observation = observation
            logger.warning("[step %d] click failed: %s", step_num, chosen.selector)
            return result

    # Max steps reached.
    final_obs = (
        observation
        if observation is not None
        else PageObservation(url="", page_state=PageState.UNKNOWN)
    )
    result.final_state = final_obs.page_state
    result.stopped_reason = "max_steps_reached"
    result.final_observation = final_obs
    result.steps.append(
        ExplorationStep(
            step_number=max_steps,
            url=final_obs.url,
            page_state=result.final_state,
            action="stop:max_steps",
        )
    )
    logger.warning("stop: max steps (%d) reached", max_steps)
    return result


__all__ = [
    "ExplorationStep",
    "ExplorationResult",
    "safe_explore",
    "DEFAULT_MAX_STEPS",
]
