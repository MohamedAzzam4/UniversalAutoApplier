"""Unit tests for WQ-3 queue-import configuration.

Covers ``UAA_QUEUE_PATH`` (primary) / ``UAA_JOBHUNTER_QUEUE`` (legacy
fallback) resolution and the opt-in ``UAA_IMPORT_QUEUE_ON_STARTUP`` flag.
Pure-logic tests; no network, no browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.config import Settings, load_settings


class TestQueuePathResolution:
    def test_defaults_to_none(self) -> None:
        settings = load_settings(env={})
        assert settings.queue_path is None
        assert settings.jobhunter_queue is None

    def test_primary_env_var_read(self) -> None:
        settings = load_settings(env={"UAA_QUEUE_PATH": "/tmp/queue.jsonl"})
        assert settings.queue_path == Path("/tmp/queue.jsonl")
        # The legacy alias resolves to the same value.
        assert settings.jobhunter_queue == settings.queue_path

    def test_legacy_env_var_fallback(self) -> None:
        settings = load_settings(env={"UAA_JOBHUNTER_QUEUE": "/tmp/legacy.jsonl"})
        assert settings.queue_path == Path("/tmp/legacy.jsonl")

    def test_primary_wins_over_legacy(self) -> None:
        settings = load_settings(
            env={
                "UAA_QUEUE_PATH": "/tmp/primary.jsonl",
                "UAA_JOBHUNTER_QUEUE": "/tmp/legacy.jsonl",
            }
        )
        assert settings.queue_path == Path("/tmp/primary.jsonl")

    def test_empty_strings_treated_as_unset(self) -> None:
        settings = load_settings(env={"UAA_QUEUE_PATH": "", "UAA_JOBHUNTER_QUEUE": ""})
        assert settings.queue_path is None

    def test_settings_constructor_accepts_queue_path(self) -> None:
        settings = Settings(queue_path=Path("/tmp/q.jsonl"))
        assert settings.queue_path == Path("/tmp/q.jsonl")
        assert settings.jobhunter_queue == Path("/tmp/q.jsonl")


class TestImportQueueOnStartup:
    def test_default_is_false(self) -> None:
        settings = load_settings(env={})
        assert settings.import_queue_on_startup is False

    def test_parses_truthy_variants(self) -> None:
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            settings = load_settings(env={"UAA_IMPORT_QUEUE_ON_STARTUP": truthy})
            assert settings.import_queue_on_startup is True, truthy

    def test_parses_falsy_variants(self) -> None:
        for falsy in ("0", "false", "no", "off", ""):
            settings = load_settings(env={"UAA_IMPORT_QUEUE_ON_STARTUP": falsy})
            assert settings.import_queue_on_startup is False, falsy

    def test_rejects_invalid_boolean(self) -> None:
        with pytest.raises(ValueError):
            load_settings(env={"UAA_IMPORT_QUEUE_ON_STARTUP": "maybe"})
