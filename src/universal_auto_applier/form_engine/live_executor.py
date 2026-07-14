"""Execute deterministic form mappings against a rendered Playwright page.

This module fills controls and uploads documents. It has no submission API
and never clicks buttons, which keeps final-submit authority in the runner's
review gate.
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
    """Fill the current rendered form page and upload known documents."""
    targets = _extract_live_fields(page)
    target_by_token = {target.token: target for target in targets}
    summary = fill_form([target.field for target in targets], candidate, job)
    execution = LiveFormExecution()

    for result in summary.results:
        target = target_by_token[result.field_selector]
        status = result.status
        explanation = result.explanation
        if status == "filled" and result.value is not None:
            try:
                _execute_field(target, result.value)
                if target.field.type == "file":
                    # File inputs commonly trigger an asynchronous ATS upload.
                    # Give the page's change handler time to transfer and render
                    # its completion/error state before evidence is captured.
                    page.wait_for_timeout(1_000)
                execution.filled += 1
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
            )
        )

    execution.validation_errors = _validation_errors(page)
    return execution


__all__ = ["LiveFormExecution", "execute_live_form"]
