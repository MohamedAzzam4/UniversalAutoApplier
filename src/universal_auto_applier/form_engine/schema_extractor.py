"""Form schema extractor — extract form fields from HTML.

Per ``ROADMAP.md`` WP 4.1, extracts fields with:
- selector
- label
- type
- required
- options
- current value
- nearby text
- confidence

Supported controls: text, email, phone, textarea, select, radio, checkbox,
file upload, date, number.

Labels can be found from ``label for``, ``aria-label``, placeholder,
name/id, and nearby text.

The extractor works on raw HTML strings for fixture-based tests. In
production, a Playwright-based extractor would be used.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any

from universal_auto_applier.core.models import FieldOption, FormField

logger = logging.getLogger("universal_auto_applier.form_engine.schema_extractor")

# Confidence values.
_CONFIDENCE_HIGH = 0.95
_CONFIDENCE_MEDIUM = 0.75
_CONFIDENCE_LOW = 0.40


class _FormExtractor(HTMLParser):
    """HTML parser that extracts form fields."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: list[FormField] = []
        self._labels: dict[str, str] = {}  # for="id" -> label text
        self._label_for: str | None = None
        self._label_text: list[str] = []
        self._field_counter: int = 0
        self._select_name: str | None = None
        self._select_selector: str | None = None
        self._select_options: list[FieldOption] = []
        self._select_id: str | None = None
        self._radio_groups: dict[str, list[FieldOption]] = {}
        self._checkbox_groups: dict[str, list[FieldOption]] = {}
        self._all_text: list[str] = []
        self._element_stack: list[dict[str, Any]] = []
        self._in_label: bool = False
        self._in_option: bool = False
        self._option_value: str = ""
        self._option_text: list[str] = []
        self._in_select: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: (v or "") for k, v in attrs}

        if tag == "label":
            self._in_label = True
            self._label_for = attrs_dict.get("for", "")
            self._label_text = []

        if tag == "select":
            self._in_select = True
            self._select_name = attrs_dict.get("name", "")
            self._select_id = attrs_dict.get("id", "")
            self._select_options = []
            self._field_counter += 1
            if self._select_id:
                self._select_selector = f"#{self._select_id}"
            elif self._select_name:
                self._select_selector = f"select[name='{self._select_name}']"
            else:
                self._select_selector = f"select:nth-of-type({self._field_counter})"

        if tag == "option" and self._in_select:
            self._in_option = True
            self._option_value = attrs_dict.get("value", "")
            self._option_text = []
            # Check if selected
            if "selected" in attrs_dict:
                pass  # Will mark after collecting text

        if tag in ("input", "textarea", "select"):
            self._handle_field(tag, attrs_dict)

        self._element_stack.append({"tag": tag, "attrs": attrs_dict, "text": ""})

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._in_label:
            self._in_label = False
            label_text = " ".join(self._label_text).strip()
            if self._label_for:
                self._labels[self._label_for] = label_text

        if tag == "option" and self._in_option:
            self._in_option = False
            option_label = " ".join(self._option_text).strip()
            self._select_options.append(
                FieldOption(
                    value=self._option_value,
                    label=option_label or self._option_value,
                )
            )

        if tag == "select" and self._in_select:
            self._in_select = False
            # The select field was already added in _handle_field.
            # Now update its options.
            for field in self.fields:
                if field.selector == self._select_selector:
                    field.options = list(self._select_options)
                    break

        if self._element_stack:
            element = self._element_stack.pop()
            text = element.get("text", "").strip()
            if text:
                self._all_text.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_label:
            self._label_text.append(data)
        if self._in_option:
            self._option_text.append(data)
        if self._element_stack:
            self._element_stack[-1]["text"] += data

    def _handle_field(self, tag: str, attrs: dict[str, str]) -> None:
        """Handle an input, textarea, or select field."""
        if tag == "select":
            # Select is handled via _select_* state, field added here.
            required = "required" in attrs
            label = self._find_label(attrs)
            self.fields.append(
                FormField(
                    selector=self._select_selector or "",
                    name=self._select_name or "",
                    label=label,
                    type="select",
                    required=required,
                    confidence=_CONFIDENCE_HIGH,
                )
            )
            return

        input_type = attrs.get("type", "text").lower()

        # Skip submit/button/reset/image inputs — they are clickables, not form fields.
        if input_type in ("submit", "button", "reset", "image"):
            return

        # Skip hidden inputs.
        if input_type == "hidden":
            return

        # Map HTML input types to FormField types.
        field_type = _map_input_type(input_type, tag)

        # Skip password fields — they are blocked for safety.
        if input_type == "password":
            field_type = "unknown"  # Will be blocked by fill engine

        name = attrs.get("name", "")
        element_id = attrs.get("id", "")

        # Build selector.
        self._field_counter += 1
        if element_id:
            selector = f"#{element_id}"
        elif name:
            selector = f"{tag}[name='{name}']"
        else:
            selector = f"{tag}:nth-of-type({self._field_counter})"

        # Find label.
        label = self._find_label(attrs)

        # Find nearby text.
        nearby_text = label or attrs.get("placeholder", "") or attrs.get("aria-label", "")

        required = "required" in attrs
        current_value = attrs.get("value", "")

        # For radio and checkbox, collect into groups.
        if field_type == "radio":
            if name not in self._radio_groups:
                self._radio_groups[name] = []
            self._radio_groups[name].append(
                FieldOption(value=current_value, label=label or nearby_text)
            )
            # Don't add individual radio buttons as separate fields; the group
            # will be added as one field at finalize time.
            return

        if field_type == "checkbox":
            if name not in self._checkbox_groups:
                self._checkbox_groups[name] = []
            self._checkbox_groups[name].append(
                FieldOption(value=current_value, label=label or nearby_text)
            )
            return

        confidence = _determine_confidence(label, nearby_text, name, element_id)

        self.fields.append(
            FormField(
                selector=selector,
                name=name,
                label=label,
                type=field_type,
                required=required,
                current_value=current_value,
                nearby_text=nearby_text,
                confidence=confidence,
            )
        )

    def _find_label(self, attrs: dict[str, str]) -> str:
        """Find the label for a field.

        Priority: label[for=id] > aria-label > placeholder > name/id.
        """
        element_id = attrs.get("id", "")
        if element_id and element_id in self._labels:
            return self._labels[element_id]

        aria_label = attrs.get("aria-label", "")
        if aria_label:
            return aria_label

        placeholder = attrs.get("placeholder", "")
        if placeholder:
            return placeholder

        name = attrs.get("name", "")
        if name:
            return name.replace("_", " ").replace("-", " ").title()

        return ""

    def finalize(self) -> None:
        """Add radio and checkbox group fields after parsing is complete."""
        for name, options in self._radio_groups.items():
            self.fields.append(
                FormField(
                    selector=f"input[name='{name}']",
                    name=name,
                    label=options[0].label if options else name,
                    type="radio",
                    required=False,  # Will be determined by context
                    options=list(options),
                    confidence=_CONFIDENCE_MEDIUM,
                )
            )

        for name, options in self._checkbox_groups.items():
            self.fields.append(
                FormField(
                    selector=f"input[name='{name}']",
                    name=name,
                    label=options[0].label if options else name,
                    type="checkbox",
                    required=False,
                    options=list(options),
                    confidence=_CONFIDENCE_MEDIUM,
                )
            )


def _map_input_type(html_type: str, tag: str) -> str:
    """Map an HTML input type to a FormField type."""
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"

    type_map = {
        "text": "text",
        "email": "email",
        "tel": "phone",
        "phone": "phone",
        "url": "text",
        "password": "unknown",  # Blocked for safety
        "number": "number",
        "date": "date",
        "datetime-local": "date",
        "time": "date",
        "file": "file",
        "radio": "radio",
        "checkbox": "checkbox",
        "search": "text",
    }
    return type_map.get(html_type, "unknown")


def _determine_confidence(label: str, nearby_text: str, name: str, element_id: str) -> float:
    """Determine extraction confidence based on label availability."""
    if label:
        return _CONFIDENCE_HIGH
    if nearby_text:
        return _CONFIDENCE_MEDIUM
    if name or element_id:
        return _CONFIDENCE_LOW
    return 0.0


def extract_form_fields(html: str) -> list[FormField]:
    """Extract form fields from raw HTML.

    Args:
        html: The raw HTML string.

    Returns:
        A list of :class:`FormField` objects.
    """
    parser = _FormExtractor()
    parser.feed(html)
    parser.finalize()
    return parser.fields


__all__ = ["extract_form_fields"]
