"""Unit tests for :mod:`universal_auto_applier.config`.

Pure-logic tests for the settings loader. No network, no browser.
"""

from __future__ import annotations

import pytest

from universal_auto_applier.config import Settings, load_settings


def test_load_settings_defaults_when_env_empty() -> None:
    settings = load_settings(env={})
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.submit_mode == "review"
    assert settings.browser_headless is False
    assert settings.jobhunter_queue is None
    assert settings.siemens_repo is None


def test_load_settings_reads_all_variables() -> None:
    env = {
        "UAA_HOST": "127.0.0.1",
        "UAA_PORT": "9001",
        "UAA_DATA_DIR": "/tmp/uaa_test_data",
        "UAA_JOBHUNTER_QUEUE": "/tmp/queue.jsonl",
        "UAA_SIEMENS_REPO": "/tmp/siemens",
        "UAA_BROWSER_HEADLESS": "true",
        "UAA_SUBMIT_MODE": "dry_run",
    }
    settings = load_settings(env=env)
    assert settings.host == "127.0.0.1"
    assert settings.port == 9001
    assert str(settings.data_dir) == "/tmp/uaa_test_data"
    assert settings.jobhunter_queue is not None
    assert str(settings.jobhunter_queue) == "/tmp/queue.jsonl"
    assert settings.siemens_repo is not None
    assert str(settings.siemens_repo) == "/tmp/siemens"
    assert settings.browser_headless is True
    assert settings.submit_mode == "dry_run"


def test_load_settings_rejects_wildcard_bind() -> None:
    with pytest.raises(ValueError, match="bind publicly"):
        load_settings(env={"UAA_HOST": "0.0.0.0"})


def test_load_settings_rejects_ipv6_wildcard_bind() -> None:
    with pytest.raises(ValueError, match="bind publicly"):
        load_settings(env={"UAA_HOST": "::"})


def test_load_settings_treats_empty_strings_as_unset() -> None:
    env = {
        "UAA_HOST": "",
        "UAA_PORT": "",
        "UAA_DATA_DIR": "",
        "UAA_JOBHUNTER_QUEUE": "",
        "UAA_SIEMENS_REPO": "",
        "UAA_SUBMIT_MODE": "",
    }
    settings = load_settings(env=env)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.submit_mode == "review"
    assert settings.jobhunter_queue is None
    assert settings.siemens_repo is None


def test_settings_is_frozen() -> None:
    settings = Settings()
    with pytest.raises(Exception, match="frozen"):
        settings.host = "0.0.0.0"  # type: ignore[misc]


def test_load_settings_parses_boolean_variants() -> None:
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        settings = load_settings(env={"UAA_BROWSER_HEADLESS": truthy})
        assert settings.browser_headless is True, truthy
    for falsy in ("0", "false", "no", "off", ""):
        settings = load_settings(env={"UAA_BROWSER_HEADLESS": falsy})
        assert settings.browser_headless is False, falsy


def test_load_settings_rejects_invalid_port() -> None:
    with pytest.raises(ValueError):
        load_settings(env={"UAA_PORT": "0"})
    with pytest.raises(ValueError):
        load_settings(env={"UAA_PORT": "70000"})


def test_load_settings_rejects_invalid_submit_mode() -> None:
    with pytest.raises(ValueError):
        load_settings(env={"UAA_SUBMIT_MODE": "auto_submit_please"})
