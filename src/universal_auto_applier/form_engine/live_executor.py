"""Execute deterministic form mappings against a rendered Playwright page.

This module fills controls and uploads documents. It has no submission API
and never clicks buttons, which keeps final-submit authority in the runner's
review gate.

The LLM question resolver (:mod:`universal_auto_applier.llm.question_resolver`)
is integrated via :func:`execute_live_form_with_llm`, which extends
:func:`execute_live_form` with grounded LLM answers for questions that
deterministic mapping cannot resolve. The LLM path never invents personal
facts and never clicks final submit.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Locator, Page

from universal_auto_applier.browser.live_models import LiveFieldRecord, LiveUploadRecord
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldOption,
    FormField,
)
from universal_auto_applier.form_engine.fill_engine import fill_form

logger = logging.getLogger("universal_auto_applier.form_engine.live_executor")

_CONTROL_SELECTOR = (
    "input:not([type='hidden']):not([type='button']):not([type='submit'])"
    ":not([type='reset']):not([type='image']), textarea, select"
)
_FIELD_METADATA_JS = r"""
(el) => {
  const text = (node) => (node && (node.innerText || node.textContent) || '').trim();
  const id = el.id || '';
  const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
  const wrapper = el.closest('label');
  const fieldset = el.closest('fieldset');
  const legend = fieldset ? fieldset.querySelector('legend') : null;
  const container = el.closest(
    '[role="group"], .form-group, .field-border, .field, .question, .control'
  );
  const label = text(explicit) || text(wrapper) || el.getAttribute('aria-label') ||
    el.getAttribute('placeholder') || el.getAttribute('name') || id;
  const nearby = text(legend) || text(container) || label;
  return {
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    id,
    name: el.getAttribute('name') || '',
    label,
    nearby: nearby.slice(0, 1000),
    required: el.required || el.getAttribute('aria-required') === 'true' ||
      /\*/.test(label) || /\*/.test(nearby),
    value: el.value || '',
    placeholder: el.getAttribute('placeholder') || ''
  };
}
"""


@dataclass
class _LiveFieldTarget:
    token: str
    selector_hint: str
    frame_url: str
    field: FormField
    locator: Locator


@dataclass
class LiveFormExecution:
    """Structured result of one rendered form page fill."""

    fields: list[LiveFieldRecord] = field(default_factory=list[LiveFieldRecord])
    uploads: list[LiveUploadRecord] = field(default_factory=list[LiveUploadRecord])
    validation_errors: list[str] = field(default_factory=list[str])
    required_unresolved: int = 0
    filled: int = 0


def _metadata(locator: Locator) -> dict[str, Any]:
    raw = locator.evaluate(_FIELD_METADATA_JS)
    if not isinstance(raw, dict):
        return {}
    return cast(dict[str, Any], raw)


def _field_type(metadata: dict[str, Any]) -> str:
    tag = str(metadata.get("tag", "")).lower()
    input_type = str(metadata.get("type") or "text").lower()
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    return {
        "text": "text",
        "email": "email",
        "tel": "phone",
        "url": "text",
        "number": "number",
        "date": "date",
        "datetime-local": "date",
        "file": "file",
        "radio": "radio",
        "checkbox": "checkbox",
        "password": "unknown",
    }.get(input_type, "unknown")


def _selector_hint(metadata: dict[str, Any], tag: str, index: int) -> str:
    element_id = str(metadata.get("id", ""))
    if element_id:
        return f"{tag}[id={element_id!r}]"
    name = str(metadata.get("name", ""))
    if name:
        return f"{tag}[name={name!r}]"
    return f"{tag}[{index}]"


def _field_options(locator: Locator, field_type: str) -> list[FieldOption]:
    if field_type == "select":
        options: list[FieldOption] = []
        option_locators = locator.locator("option")
        for index in range(option_locators.count()):
            option = option_locators.nth(index)
            options.append(
                FieldOption(
                    value=option.get_attribute("value") or option.inner_text(),
                    label=option.inner_text().strip(),
                )
            )
        return options
    if field_type == "checkbox":
        meta = _metadata(locator)
        return [
            FieldOption(
                value=str(meta.get("value", "on")),
                label=str(meta.get("label", "")),
            )
        ]
    return []


def _extract_live_fields(page: Page) -> list[_LiveFieldTarget]:
    targets: list[_LiveFieldTarget] = []
    processed_radio_groups: set[tuple[int, str]] = set()

    for frame_index, frame in enumerate(page.frames):
        controls = frame.locator(_CONTROL_SELECTOR)
        try:
            control_count = min(controls.count(), 250)
        except Exception:
            continue

        for control_index in range(control_count):
            locator = controls.nth(control_index)
            try:
                meta = _metadata(locator)
            except Exception:
                continue
            css_classes = locator.get_attribute("class") or ""
            if "chosen-search-input" in css_classes:
                # Chosen.js mirrors a hidden native <select> with an internal
                # search input. The native select is the real field; treating
                # this helper as a second required field creates duplicates.
                continue
            field_type = _field_type(meta)
            is_file = field_type == "file"
            try:
                if not is_file and not locator.is_visible():
                    element_id = str(meta.get("id", ""))
                    chosen_visible = (
                        bool(element_id)
                        and frame.locator(f"[id={json.dumps(element_id + '_chosen')}]").is_visible()
                    )
                    if field_type != "select" or not chosen_visible:
                        continue
                if not locator.is_enabled():
                    continue
            except Exception:
                continue

            name = str(meta.get("name", ""))
            token = f"live-field-{frame_index}-{control_index}"
            target_locator = locator
            options: list[FieldOption] = _field_options(locator, field_type)

            if field_type == "radio" and name:
                group_key = (frame_index, name)
                if group_key in processed_radio_groups:
                    continue
                processed_radio_groups.add(group_key)
                group_selector = f"input[type='radio'][name={json.dumps(name)}]"
                target_locator = frame.locator(group_selector)
                options = []
                required = False
                nearby = ""
                for option_index in range(target_locator.count()):
                    option_locator = target_locator.nth(option_index)
                    option_meta = _metadata(option_locator)
                    options.append(
                        FieldOption(
                            value=str(option_meta.get("value", "")),
                            label=str(option_meta.get("label", "")),
                        )
                    )
                    required = required or bool(option_meta.get("required", False))
                    nearby = nearby or str(option_meta.get("nearby", ""))
                meta["required"] = required
                meta["nearby"] = nearby

            tag = str(meta.get("tag", "input"))
            field_model = FormField(
                selector=token,
                name=name,
                label=str(meta.get("label", "")),
                type=field_type,
                required=bool(meta.get("required", False)),
                options=options,
                current_value=str(meta.get("value", "")),
                nearby_text=str(meta.get("nearby", "")),
                confidence=0.95 if meta.get("label") else 0.6,
            )
            targets.append(
                _LiveFieldTarget(
                    token=token,
                    selector_hint=_selector_hint(meta, tag, control_index),
                    frame_url=frame.url,
                    field=field_model,
                    locator=target_locator,
                )
            )
    return targets


def _normalize_option(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    aliases = {
        "true": "yes",
        "1": "yes",
        "ja": "yes",
        "false": "no",
        "0": "no",
        "nein": "no",
    }
    return aliases.get(normalized, normalized)


def _select_option(locator: Locator, value: str) -> None:
    desired = _normalize_option(value)
    options = locator.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        option_value = option.get_attribute("value") or ""
        option_label = option.inner_text().strip()
        if desired in {_normalize_option(option_value), _normalize_option(option_label)}:
            locator.select_option(value=option_value, force=True)
            return
    raise ValueError(f"no select option matches {value!r}")


def _choose_radio(locator: Locator, value: str) -> None:
    desired = _normalize_option(value)
    for index in range(locator.count()):
        option = locator.nth(index)
        meta = _metadata(option)
        candidates = {
            _normalize_option(str(meta.get("value", ""))),
            _normalize_option(str(meta.get("label", ""))),
        }
        if desired in candidates:
            option.check()
            return
    raise ValueError(f"no radio option matches {value!r}")


def _set_checkbox(locator: Locator, value: str) -> None:
    desired = _normalize_option(value)
    if desired == "yes":
        locator.check()
        return
    if desired == "no":
        locator.uncheck()
        return
    raise ValueError(f"checkbox answer must be yes/no, got {value!r}")


def _execute_field(target: _LiveFieldTarget, value: str) -> None:
    field_type = target.field.type
    if field_type in {"text", "email", "phone", "textarea", "date", "number"}:
        target.locator.fill(value)
    elif field_type == "select":
        _select_option(target.locator, value)
    elif field_type == "radio":
        _choose_radio(target.locator, value)
    elif field_type == "checkbox":
        _set_checkbox(target.locator, value)
    elif field_type == "file":
        target.locator.set_input_files(value)
    else:
        raise ValueError(f"unsupported live field type: {field_type}")


def _document_kind(target: _LiveFieldTarget) -> str:
    descriptor = f"{target.field.label} {target.field.nearby_text} {target.field.name}".lower()
    if "cover" in descriptor or "anschreiben" in descriptor:
        return "cover_letter"
    if "resume" in descriptor or "cv" in descriptor or "lebenslauf" in descriptor:
        return "cv"
    return "unknown"


def _validation_errors(page: Page) -> list[str]:
    errors: list[str] = []
    for frame in page.frames:
        locators = frame.locator(
            "form [role='alert'], [aria-invalid='true'], .field-error, .error-message"
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
                message = locator.inner_text().strip() or locator.get_attribute("aria-label") or ""
            except Exception:
                continue
            if message and message not in errors:
                errors.append(message[:500])
    return errors


def execute_live_form(
    page: Page,
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> LiveFormExecution:
    """Fill the current rendered form page and upload known documents.

    After filling radio/select/checkbox fields that may trigger conditional
    field revelation (via JavaScript change handlers), the executor re-observes
    the page to detect newly visible fields. This handles conditional questions
    that appear only after a parent answer is selected.
    """
    targets = _extract_live_fields(page)
    target_by_token = {target.token: target for target in targets}
    summary = fill_form([target.field for target in targets], candidate, job)
    execution = LiveFormExecution()
    filled_tokens: set[str] = set()

    for result in summary.results:
        target = target_by_token[result.field_selector]
        status = result.status
        explanation = result.explanation
        if status == "filled" and result.value is not None:
            try:
                _execute_field(target, result.value)
                if target.field.type == "file":
                    page.wait_for_timeout(1_000)
                elif target.field.type in ("radio", "select", "checkbox"):
                    # Radio/select/checkbox changes may trigger JavaScript
                    # that reveals conditional fields. Wait briefly for the
                    # DOM to update.
                    page.wait_for_timeout(500)
                execution.filled += 1
                filled_tokens.add(target.token)
            except Exception as exc:
                status = "failed"
                explanation = f"Playwright fill failed: {exc}"
                logger.warning(
                    "[%s] fill failed selector=%s: %s",
                    job.application_id[:12],
                    target.selector_hint,
                    exc,
                )

            if target.field.type == "file":
                path = Path(result.value)
                upload_status = "uploaded" if status == "filled" else "failed"
                execution.uploads.append(
                    LiveUploadRecord(
                        page_url=page.url,
                        selector=target.selector_hint,
                        document_kind=cast(Any, _document_kind(target)),
                        path=str(path),
                        status=cast(Any, upload_status),
                        message=explanation,
                    )
                )

        if target.field.required and status in {"blocked", "intervention_needed", "failed"}:
            execution.required_unresolved += 1
        execution.fields.append(
            LiveFieldRecord(
                page_url=page.url,
                selector=target.selector_hint,
                label=target.field.label,
                field_type=target.field.type,
                status=cast(Any, status),
                source=result.source,
                explanation=explanation,
                field_token=target.token,
            )
        )

    # Re-observe the page after filling to detect newly revealed
    # conditional fields (e.g., a text input that appears only after
    # selecting "Yes" on a radio question).
    #
    # Bounded: this is a SINGLE re-observation pass, not a loop. It
    # processes only fields that were NOT in the initial extraction.
    # It does not recursively re-observe after filling revealed fields.
    # This prevents infinite loops and avoids re-filling unchanged fields.
    _MAX_REOBSERVE_PASSES = 1
    for _pass in range(_MAX_REOBSERVE_PASSES):
        if not filled_tokens:
            break
        new_targets = _extract_live_fields(page)
        existing_tokens = {f.field_token for f in execution.fields if f.field_token}
        revealed_targets = [
            t
            for t in new_targets
            if t.token not in existing_tokens and t.token not in filled_tokens
        ]
        if not revealed_targets:
            break
        # Process the newly revealed fields with the fill engine.
        revealed_fields = [t.field for t in revealed_targets]
        revealed_summary = fill_form(revealed_fields, candidate, job)
        revealed_by_token = {t.token: t for t in revealed_targets}

        for result in revealed_summary.results:
            target = revealed_by_token.get(result.field_selector)
            if target is None:
                continue
            status = result.status
            explanation = result.explanation
            if status == "filled" and result.value is not None:
                try:
                    _execute_field(target, result.value)
                    execution.filled += 1
                except Exception as exc:
                    status = "failed"
                    explanation = f"Playwright fill failed: {exc}"

            if target.field.required and status in {"blocked", "intervention_needed", "failed"}:
                execution.required_unresolved += 1
            execution.fields.append(
                LiveFieldRecord(
                    page_url=page.url,
                    selector=target.selector_hint,
                    label=target.field.label,
                    field_type=target.field.type,
                    status=cast(Any, status),
                    source=result.source,
                    explanation=explanation,
                    field_token=target.token,
                )
            )
        # Only newly filled tokens from this pass could trigger another
        # reveal, but we stop here (bounded to 1 pass).

    execution.validation_errors = _validation_errors(page)
    return execution


def execute_live_form_with_llm(
    page: Page,
    candidate: CandidateProfile,
    job: ApplicationJob,
    qa_service: Any = None,
    answer_memory_facts: list[Any] | None = None,
) -> LiveFormExecution:
    """Fill the current rendered form page with deterministic + LLM answers.

    This extends :func:`execute_live_form` with grounded LLM question
    resolution. For each field that deterministic mapping cannot resolve,
    the LLM resolver (:mod:`universal_auto_applier.llm.question_resolver`)
    is invoked. If the LLM proposes an answer that passes validation, it
    is filled. Otherwise, the field is recorded as requiring an
    intervention.

    Safety:
    - Deterministic mapping is tried first (never bypassed).
    - HIGH-risk categories (salary, legal, demographic, consent) are
      never auto-filled by the LLM; they always create interventions.
    - The LLM may only use candidate evidence; it must not invent facts.
    - Final submission is never triggered.
    - If the LLM service is not configured, unresolved fields become
      interventions (the pipeline does not crash).

    Stable field identity:
    - Each field has a ``field_token`` (e.g. ``live-field-0-3``) that is
      assigned during DOM extraction and propagated through
      deterministic execution → unresolved result → LLM resolution →
      Playwright fill. This ensures two similar fields cannot receive
      each other's answers.

    Args:
        page: The Playwright page with a rendered form.
        candidate: The resolved candidate profile.
        job: The application job.
        qa_service: Optional :class:`QuestionAnsweringService`. If None,
            a default is created from environment config.
        answer_memory_facts: Optional reusable approved answers.

    Returns:
        A :class:`LiveFormExecution` with all field outcomes.
    """
    # First, run the deterministic fill (existing behavior).
    execution = execute_live_form(page, candidate, job)

    # If there are no unresolved required fields, we're done.
    if execution.required_unresolved == 0:
        return execution

    # Re-extract live fields to get the locators (same extraction as
    # execute_live_form, so tokens match).
    targets = _extract_live_fields(page)
    target_by_token: dict[str, _LiveFieldTarget] = {t.token: t for t in targets}

    # Build a set of field tokens that need LLM resolution.
    # Uses the stable field_token propagated from execute_live_form.
    unresolved_tokens: set[str] = set()
    for record in execution.fields:
        if record.status in ("blocked", "intervention_needed", "failed"):
            if record.field_token:
                unresolved_tokens.add(record.field_token)

    if not unresolved_tokens:
        return execution

    # Import here to avoid circular imports at module load time.
    from universal_auto_applier.llm.qa_service import create_qa_service
    from universal_auto_applier.llm.question_resolver import resolve_question

    service = qa_service or create_qa_service()

    # Process each unresolved field using stable token matching.
    for token in unresolved_tokens:
        target = target_by_token.get(token)
        if target is None:
            # Token not found in re-extracted targets (page may have
            # changed). Leave as intervention_needed.
            continue

        # Resolve the question.
        resolution = resolve_question(
            target.field,
            candidate,
            job,
            qa_service=service,
            answer_memory_facts=answer_memory_facts,
        )

        # Find the existing field record by stable token and update it.
        for i, record in enumerate(execution.fields):
            if record.field_token != token:
                continue

            if resolution.can_auto_fill and resolution.proposed_answer is not None:
                # Try to fill the field with the LLM answer.
                try:
                    _execute_field(target, resolution.proposed_answer.normalized_value)
                    execution.fields[i] = LiveFieldRecord(
                        page_url=record.page_url,
                        selector=record.selector,
                        label=record.label,
                        field_type=record.field_type,
                        status="filled",
                        source="llm_grounded",
                        explanation=resolution.proposed_answer.explanation,
                        field_token=token,
                        proposed_answer=resolution.proposed_answer.value,
                        confidence=resolution.proposed_answer.confidence,
                        evidence_summary="; ".join(
                            e.fact for e in resolution.proposed_answer.evidence
                        ),
                        category=str(resolution.category),
                        risk_level=str(resolution.risk_level),
                        requires_confirmation=False,
                    )
                    execution.filled += 1
                    if target.field.required:
                        execution.required_unresolved = max(0, execution.required_unresolved - 1)
                except Exception as exc:
                    execution.fields[i] = LiveFieldRecord(
                        page_url=record.page_url,
                        selector=record.selector,
                        label=record.label,
                        field_type=record.field_type,
                        status="failed",
                        source="llm_grounded",
                        explanation=f"LLM answer fill failed: {exc}",
                        field_token=token,
                        proposed_answer=resolution.proposed_answer.value,
                        confidence=resolution.proposed_answer.confidence,
                        category=str(resolution.category),
                        risk_level=str(resolution.risk_level),
                        requires_confirmation=True,
                    )
                    logger.warning(
                        "[%s] LLM fill failed for %s: %s",
                        job.application_id[:12],
                        record.label,
                        exc,
                    )
            else:
                # LLM could not resolve — keep as intervention_needed with
                # LLM metadata for the dashboard.
                reason = resolution.refusal or resolution.unresolved_reason or "unresolved"
                proposed = resolution.proposed_answer
                execution.fields[i] = LiveFieldRecord(
                    page_url=record.page_url,
                    selector=record.selector,
                    label=record.label,
                    field_type=record.field_type,
                    status="intervention_needed",
                    source="llm_grounded" if proposed else None,
                    explanation=f"LLM unresolved: {reason}",
                    field_token=token,
                    proposed_answer=proposed.value if proposed else None,
                    confidence=proposed.confidence if proposed else None,
                    evidence_summary=(
                        "; ".join(e.fact for e in proposed.evidence) if proposed else ""
                    ),
                    category=str(resolution.category),
                    risk_level=str(resolution.risk_level),
                    requires_confirmation=True,
                )
            break

    # Re-check validation errors after LLM fills.
    execution.validation_errors = _validation_errors(page)
    return execution


__all__ = ["LiveFormExecution", "execute_live_form", "execute_live_form_with_llm"]
