"""
Tests for Session implementations.

These tests verify the Session interface and behavior without
actually calling external APIs. Real API tests require credentials
and are meant for integration testing.
"""

import pytest
from harness.session import (
    Session, Message,
    AnthropicSession, OpenAISession,
    ClaudeCodeSession, CodexSession,
    CLISession, OpenClawSession,
)


# --- Interface tests ---

def test_session_is_abstract():
    """Can't instantiate Session directly."""
    with pytest.raises(TypeError):
        Session()


def test_session_subclass_must_implement_send():
    """Subclass without send() raises TypeError."""
    class BadSession(Session):
        pass

    with pytest.raises(TypeError):
        BadSession()


def test_session_subclass_with_send_works():
    """Subclass with send() can be instantiated."""
    class GoodSession(Session):
        def send(self, message: Message) -> str:
            return "ok"

    s = GoodSession()
    assert s.send("hello") == "ok"
    assert s.history_length == 0  # default


# --- ClaudeCodeSession ---

def test_claude_code_session_id_auto():
    """Auto session ID is generated as UUID."""
    s = ClaudeCodeSession()
    assert len(s._session_id) == 36  # UUID format
    assert s._session_id.count("-") == 4


def test_claude_code_session_id_custom():
    """Custom session ID is used."""
    s = ClaudeCodeSession(session_id="my-session")
    assert s._session_id == "my-session"


def test_claude_code_session_id_none():
    """None session ID = stateless."""
    s = ClaudeCodeSession(session_id=None)
    assert s._session_id is None


def test_claude_code_reset():
    """Reset creates a new session ID."""
    s = ClaudeCodeSession()
    old_id = s._session_id
    s._turn_count = 5
    s.reset()
    assert s._session_id != old_id
    assert s._turn_count == 0
    assert s.history_length == 0


# --- CodexSession ---

def test_codex_session_id_auto():
    s = CodexSession()
    assert s._session_id.startswith("harness-")


def test_codex_session_reset():
    s = CodexSession()
    old_id = s._session_id
    s._turn_count = 3
    s.reset()
    assert s._session_id != old_id
    assert s._turn_count == 0


# --- CLISession ---

def test_cli_session_echo():
    """CLISession works with a simple echo command."""
    s = CLISession(command="echo hello")
    result = s.send("ignored")
    assert result == "hello"


def test_cli_session_with_stdin():
    """CLISession can pass input via stdin."""
    s = CLISession(command="cat")
    result = s.send("hello world")
    assert result == "hello world"


def test_cli_session_failure():
    """CLISession raises on non-zero exit."""
    s = CLISession(command="exit 1")
    with pytest.raises(RuntimeError, match="CLI failed"):
        s.send("test")


# --- Text extraction ---

def test_extract_text_from_string():
    assert ClaudeCodeSession._extract_text("hello") == "hello"


def test_extract_text_from_dict():
    assert ClaudeCodeSession._extract_text({"text": "hello"}) == "hello"


def test_extract_text_from_list():
    msg = [{"type": "text", "text": "hello"}, {"type": "image", "data": "..."}]
    assert ClaudeCodeSession._extract_text(msg) == "hello"


# --- History tracking ---

def test_claude_code_history_length():
    s = ClaudeCodeSession()
    assert s.history_length == 0
    s._turn_count = 5
    assert s.history_length == 5


def test_codex_history_length():
    s = CodexSession()
    assert s.history_length == 0
    s._turn_count = 3
    assert s.history_length == 3
