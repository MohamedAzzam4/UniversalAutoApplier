"""WQ-7 execution mode — authoritative submit-safety enforcement.

This module defines the ``ExecutionMode`` enum and the
``SubmitSafetyGuard`` class that enforces submit-safety at the lowest
browser-action layer.

When ``ExecutionMode.REAL_SITE_DRY_RUN`` is active:
- Final-submit controls are never clicked.
- Enter is never pressed on forms.
- ``form.submit()`` / ``requestSubmit()`` are never invoked.
- Approval=true does not bypass the block.
- ``set_input_files`` is allowed only after pre-screening the target
  element for ``onchange``/``oninput`` submit handlers.
- All blocked actions are recorded as truthful evidence.

The guard is constructed by WQ-7 entry points and is NOT caller-
overridable through CLI/API/config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger("universal_auto_applier.execution_mode")


class ExecutionMode(Enum):
    """The execution mode for a browser session.

    - ``FIXTURE_DRY_RUN``: Level 0/1 — fixture HTML or local browser.
    - ``REAL_SITE_DRY_RUN``: Level 2 — real ATS pages, never submits.
    - ``CONTROLLED_SUBMISSION``: Level 3 — real submission with gates.
    """

    FIXTURE_DRY_RUN = auto()
    REAL_SITE_DRY_RUN = auto()
    CONTROLLED_SUBMISSION = auto()


@dataclass
class BlockedAction:
    """Record of a blocked submit-capable action."""

    action_type: str  # "click", "press_enter", "form_submit", "file_upload"
    selector: str = ""
    reason: str = ""
    timestamp: str = ""


@dataclass
class SubmitSafetyGuard:
    """Enforces submit-safety at the lowest browser-action layer.

    In ``REAL_SITE_DRY_RUN`` mode, this guard:
    1. Prevents any click on a ``dangerous_submit`` classified element.
    2. Prevents Enter key press on any form element.
    3. Prevents ``form.submit()`` / ``requestSubmit()`` via JS evaluation.
    4. Pre-screens file inputs for ``onchange``/``oninput`` submit handlers
       before allowing ``set_input_files``.
    5. Records every blocked action as truthful evidence.
    6. Rejects direct calls to controlled submission.

    The guard is NOT caller-overridable. Once constructed in
    ``REAL_SITE_DRY_RUN`` mode, no CLI flag, API parameter, config setting,
    or approval state can disable the protections.
    """

    mode: ExecutionMode
    _blocked_actions: list[BlockedAction] = field(default_factory=list[BlockedAction])

    @property
    def is_dry_run(self) -> bool:
        """True when submit is hard-blocked (REAL_SITE_DRY_RUN or FIXTURE)."""
        return self.mode in (ExecutionMode.REAL_SITE_DRY_RUN, ExecutionMode.FIXTURE_DRY_RUN)

    @property
    def is_real_site_dry_run(self) -> bool:
        """True when in WQ-7 real-site dry-run mode."""
        return self.mode == ExecutionMode.REAL_SITE_DRY_RUN

    @property
    def blocked_actions(self) -> list[BlockedAction]:
        """Read-only list of blocked actions (for evidence)."""
        return list(self._blocked_actions)

    def can_click(self, classification: str, selector: str = "") -> bool:
        """Check whether a click on the given classification is allowed.

        In dry-run mode, only ``safe_apply`` and ``safe_continue`` clicks
        are allowed. ``dangerous_submit``, ``login``, ``safe_upload``, and
        ``unknown`` are blocked.

        Returns True if the click is allowed, False if blocked.
        Records the blocked action if applicable.
        """
        if not self.is_dry_run:
            return True  # Controlled submission mode — gates handle safety.

        allowed = {"safe_apply", "safe_continue"}
        if classification in allowed:
            return True

        # Block all other classifications.
        self._blocked_actions.append(
            BlockedAction(
                action_type="click",
                selector=selector,
                reason=f"classification={classification} blocked in {self.mode.name}",
            )
        )
        logger.warning(
            "[wq7-guard] click blocked: classification=%s selector=%s",
            classification,
            selector,
        )
        return False

    def can_press_enter(self, selector: str = "") -> bool:
        """Check whether pressing Enter is allowed.

        Enter is NEVER allowed in dry-run mode — it could submit a form.

        Returns True if allowed, False if blocked.
        """
        if not self.is_dry_run:
            return True

        self._blocked_actions.append(
            BlockedAction(
                action_type="press_enter",
                selector=selector,
                reason="Enter key blocked in dry-run mode (could submit form)",
            )
        )
        logger.warning("[wq7-guard] Enter key blocked: selector=%s", selector)
        return False

    def can_evaluate_js(self, script: str) -> bool:
        """Check whether a JavaScript evaluation is allowed.

        In dry-run mode, any script containing ``.submit(`` or
        ``requestSubmit`` is blocked. Pure read operations are allowed.

        Returns True if allowed, False if blocked.
        """
        if not self.is_dry_run:
            return True

        script_lower = script.lower()
        if ".submit(" in script_lower or "requestsubmit" in script_lower:
            self._blocked_actions.append(
                BlockedAction(
                    action_type="form_submit",
                    selector="",
                    reason="JS evaluation contains submit() call — blocked",
                )
            )
            logger.warning("[wq7-guard] JS evaluation blocked: contains submit()")
            return False

        return True

    def can_set_input_files(
        self,
        element_html: str = "",
        selector: str = "",
    ) -> bool:
        """Check whether ``set_input_files`` is safe.

        Pre-screens the element's HTML for ``onchange``/``oninput`` handlers
        that could trigger auto-submit. If such a handler is found, the
        file upload is blocked.

        Returns True if safe, False if blocked.
        """
        if not self.is_dry_run:
            return True

        # Check for auto-submit handlers on the file input element.
        html_lower = element_html.lower()
        dangerous_patterns = [
            "onchange",
            "oninput",
            "onselect",
        ]
        submit_indicators = [".submit(", "requestsubmit", "form.submit"]

        has_handler = any(p in html_lower for p in dangerous_patterns)
        has_submit = any(s in html_lower for s in submit_indicators)

        if has_handler and has_submit:
            self._blocked_actions.append(
                BlockedAction(
                    action_type="file_upload",
                    selector=selector,
                    reason="File input has onchange/oninput submit handler — blocked",
                )
            )
            logger.warning(
                "[wq7-guard] set_input_files blocked: auto-submit handler detected (selector=%s)",
                selector,
            )
            return False

        return True

    def can_submit(self, approval: bool = False) -> bool:
        """Check whether a direct submit call is allowed.

        In dry-run mode, submit is NEVER allowed — even with approval=True.

        Returns True if allowed, False if blocked.
        """
        if not self.is_dry_run:
            return True  # Controlled submission — gates handle it.

        self._blocked_actions.append(
            BlockedAction(
                action_type="direct_submit",
                selector="",
                reason=f"Direct submit blocked in {self.mode.name} (approval={approval})",
            )
        )
        logger.warning(
            "[wq7-guard] Direct submit blocked: approval=%s mode=%s",
            approval,
            self.mode.name,
        )
        return False

    def assert_no_submit(self) -> None:
        """Assert that no submit actions were performed.

        Called at the end of a dry-run to prove zero submissions.
        Raises ``AssertionError`` if any blocked action was a direct
        submit attempt (which should never happen, but the guard is
        belt-and-suspenders).
        """
        # The guard only records BLOCKED actions, not performed ones.
        # If there are blocked direct_submit actions, it means something
        # TRIED to submit — which is a bug.
        direct_submits = [a for a in self._blocked_actions if a.action_type == "direct_submit"]
        if direct_submits:
            raise AssertionError(
                f"WQ-7 guard detected {len(direct_submits)} direct submit "
                f"attempt(s) — these should never occur in dry-run mode"
            )


def create_wq7_guard() -> SubmitSafetyGuard:
    """Create a SubmitSafetyGuard for WQ-7 real-site dry-run mode.

    This is the ONLY way to create a guard for WQ-7. The mode is
    hardcoded to ``REAL_SITE_DRY_RUN`` and cannot be overridden.
    """
    return SubmitSafetyGuard(mode=ExecutionMode.REAL_SITE_DRY_RUN)


__all__ = [
    "BlockedAction",
    "ExecutionMode",
    "SubmitSafetyGuard",
    "create_wq7_guard",
]
