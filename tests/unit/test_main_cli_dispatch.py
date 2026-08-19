"""Hermetic regression test for the ``python -m`` CLI dispatch.

``__main__.py`` routes ``argv[0]`` to :func:`cli.run_command` for every
explicit subcommand registered in the CLI parser. A stale allowlist here
silently started the dashboard server instead of running the command (the
WQ-7C ``live-synthetic-mutation`` CLI was unreachable via
``python -m universal_auto_applier live-synthetic-mutation``).

These tests are hermetic: ``run_command`` is monkeypatched so nothing runs
a browser, imports playwright, or starts a server.
"""

from __future__ import annotations

import argparse

import pytest

import universal_auto_applier.__main__ as main_module
from universal_auto_applier import cli as cli_module
from universal_auto_applier.cli import CLI_COMMANDS, _build_parser
from universal_auto_applier.config import Settings


def _registered_commands() -> set[str]:
    """Return the set of subcommand names registered by ``_build_parser``."""
    parser = _build_parser()
    commands: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 - argparse private API for introspection
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            commands.update(action.choices.keys())
    return commands


def test_cli_commands_are_registered_commands() -> None:
    """The dispatch allowlist must list every registered subcommand exactly."""
    assert CLI_COMMANDS == frozenset(_registered_commands())


def test_queue_import_exposes_synthetic_mutation_opt_in() -> None:
    """``queue-import --synthetic-mutation`` must parse (WQ-7C opt-in stamp)."""
    parser = _build_parser()
    args = parser.parse_args(
        ["queue-import", "--path", "C:/tmp/queue.jsonl", "--synthetic-mutation"]
    )
    assert args.command == "queue-import"
    assert args.synthetic_mutation is True

    args_bare = parser.parse_args(["queue-import"])
    assert args_bare.synthetic_mutation is False


@pytest.mark.parametrize("command", sorted(CLI_COMMANDS))
def test_python_m_dispatches_every_cli_command(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main([command, ...])`` must route to ``run_command``.

    Regression: ``live-synthetic-mutation`` / ``live-submit`` /
    ``live-dry-run-platforms`` fell through to the dashboard server because
    the old allowlist only contained four commands.
    """
    seen: list[list[str]] = []

    def fake_run_command(argv: list[str], settings: object) -> int:  # noqa: ARG001
        seen.append(argv)
        return 7

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    monkeypatch.setattr(main_module, "load_settings", lambda: object())

    argv = [command, "--application-id", "prefix"]
    result = main_module.main(list(argv))

    assert result == 7
    assert seen == [argv]


def test_python_m_without_command_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """No argv (or an unknown first token) must NOT route to the CLI.

    The pre-existing behavior is to start the dashboard; we only assert the
    CLI path is not taken and the server bootstrap is reached.
    """
    entered = {"run_command": False, "server": False}

    def fake_run_command(argv: list[str], settings: object) -> int:  # noqa: ARG001
        entered["run_command"] = True
        return 0

    def fake_bootstrap(settings: object) -> None:  # noqa: ARG001
        entered["server"] = True

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    monkeypatch.setattr(
        main_module,
        "load_settings",
        lambda: Settings(),
    )
    monkeypatch.setattr(main_module, "_run_dashboard", fake_bootstrap)

    main_module.main([])

    assert entered == {"run_command": False, "server": True}
