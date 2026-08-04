"""TUI slash dispatch goes through the unified commands registry.

Covers: builtin registration (existence + aliases + help text from the
registry), local-action routing with raw args, prompt-layer expansion
into a chat turn, and unknown-command handling. No real TUI needed —
``_handle_slash`` takes a Rich console we can record.
"""
from __future__ import annotations

import pytest
from rich.console import Console

from openprogram._cli_chat import handlers
from openprogram.commands import registry as _reg
from openprogram.commands.dispatch import invoke
from openprogram.commands.frontmatter import ParsedCommand


class _FakeAgent:
    id = "agent-x"


@pytest.fixture()
def clean_registry(monkeypatch):
    """Empty registry isolated from the developer's real
    ~/.openprogram/commands and project commands dirs."""
    monkeypatch.setattr(_reg, "load_user", lambda: [])
    monkeypatch.setattr(_reg, "load_project", lambda cwd=None: [])
    import openprogram.commands._plugin_adapter as _pa
    import openprogram.commands._skill_adapter as _sa
    monkeypatch.setattr(_pa, "sync_into_registry", lambda: None)
    monkeypatch.setattr(_sa, "sync_into_registry", lambda: None)
    _reg.clear_all()
    # Prime the (empty) file-backed layers now — otherwise the first
    # resolve() inside a test triggers the lazy reload and wipes any
    # register_external() the test did beforehand.
    _reg.reload()
    yield
    _reg.clear_all()


def _console() -> Console:
    return Console(record=True, width=200, force_terminal=False)


def test_builtins_registered_with_aliases(clean_registry):
    handlers.register_repl_builtins()
    spec = _reg.resolve("model")
    assert spec is not None and spec.source == "builtin"
    # Aliases resolve to the canonical command.
    assert _reg.resolve("q").name == "quit"
    assert _reg.resolve("fns").name == "functions"
    assert _reg.resolve("?").name == "help"
    # The old hardcoded table is gone.
    assert not hasattr(handlers, "SLASH_HELP")


def test_invoke_local_carries_marker_and_raw_args(clean_registry):
    handlers.register_repl_builtins()
    res = invoke("/model glm-4.7", session_id="s1")
    assert res.ok and res.kind == "local"
    assert res.local_handler == "model"
    assert res.raw_args == "glm-4.7"


def test_local_action_dispatch(clean_registry):
    console = _console()
    out = handlers._handle_slash("/session", console, None,
                                 agent=_FakeAgent(), session_id="sess-42")
    assert out is False
    text = console.export_text()
    assert "sess-42" in text and "agent-x" in text


def test_quit_returns_exit(clean_registry):
    console = _console()
    assert handlers._handle_slash("/quit", console, None) is True
    # Alias goes through the same registry path.
    assert handlers._handle_slash("/q", _console(), None) is True


def test_prompt_command_expands_into_turn(clean_registry, monkeypatch):
    handlers.register_repl_builtins()
    _reg.register_external(
        "myreview", source="user", source_label="(user)",
        raw=ParsedCommand(name="myreview",
                          description="review helper",
                          body="Review this: $ARGUMENTS"),
    )
    sent = {}

    def _fake_turn(agent, session_id, message, *, console=None):
        sent.update(agent=agent.id, session_id=session_id, message=message)
        return "ok"

    import openprogram._cli_chat.turn as _turn
    monkeypatch.setattr(_turn, "_run_turn_with_history", _fake_turn)

    console = _console()
    out = handlers._handle_slash("/myreview src/foo.py", console, None,
                                 agent=_FakeAgent(), session_id="s9")
    assert out is False
    assert sent["message"] == "Review this: src/foo.py"
    assert sent["session_id"] == "s9"


def test_prompt_command_without_session_is_refused(clean_registry):
    _reg.register_external(
        "myreview", source="user", source_label="(user)",
        raw=ParsedCommand(name="myreview", body="x"),
    )
    console = _console()
    out = handlers._handle_slash("/myreview", console, None,
                                 agent=None, session_id="")
    assert out is False
    assert "No active session" in console.export_text()


def test_unknown_command_message(clean_registry):
    console = _console()
    out = handlers._handle_slash("/definitely-not-a-command", console, None)
    assert out is False
    assert "Unknown command" in console.export_text()


def test_help_lists_every_source(clean_registry):
    handlers.register_repl_builtins()
    _reg.register_external(
        "myreview", source="user", source_label="(user)",
        raw=ParsedCommand(name="myreview",
                          description="review helper", body="x"),
    )
    console = _console()
    out = handlers._handle_slash("/help", console, None)
    assert out is False
    text = console.export_text()
    assert "/model" in text
    assert "/myreview" in text and "(user)" in text


def test_bare_slash_shows_help(clean_registry):
    console = _console()
    assert handlers._handle_slash("/", console, None) is False
    assert "/model" in console.export_text()


def test_uppercase_verb_still_resolves(clean_registry):
    console = _console()
    assert handlers._handle_slash("/QUIT", console, None) is True
