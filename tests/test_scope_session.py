"""
Tests for Scope + Session integration.
"""

import pytest
from harness.scope import Scope
from harness.session import Session, Message


# --- Mock Sessions ---

class MockAPISession(Session):
    """Simulates an API Session (no memory)."""
    def __init__(self):
        self._history = []

    def send(self, message: Message) -> str:
        self._history.append({"role": "user", "content": message})
        reply = '{"status": "ok"}'
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def apply_scope(self, scope, context):
        import json
        if scope.peer and scope.peer != "none" and "_prior_results" in context:
            summary = json.dumps(context["_prior_results"])
            self._history.append({"role": "user", "content": f"[Prior results] {summary}"})
            self._history.append({"role": "assistant", "content": "Understood."})

    def post_execution(self, scope):
        if scope.needs_compact and len(self._history) >= 2:
            self._history[-2:] = [
                {"role": "user", "content": "[Compacted]"},
                {"role": "assistant", "content": "Noted."},
            ]


class MockCLISession(Session):
    """Simulates a CLI Session (has memory)."""
    def __init__(self):
        self._session_id = "test-session"
        self._forked = False

    def send(self, message: Message) -> str:
        return '{"status": "ok"}'

    @property
    def has_memory(self) -> bool:
        return True

    def apply_scope(self, scope, context):
        pass  # CLI has own memory

    def post_execution(self, scope):
        if scope.needs_compact:
            self._session_id = "new-session"
            self._forked = True


# --- Scope tests ---

def test_scope_none_fields():
    s = Scope()
    assert s.depth is None
    assert s.detail is None
    assert s.peer is None
    assert s.compact is None
    assert not s.needs_call_stack
    assert not s.needs_peers
    assert not s.shares_session
    assert not s.needs_compact


def test_scope_partial_fields():
    s = Scope(peer="io", compact=True)
    assert s.depth is None
    assert s.peer == "io"
    assert s.compact is True
    assert s.needs_peers
    assert s.needs_compact


def test_scope_presets():
    assert Scope.isolated().peer == "none"
    assert Scope.chained().peer == "io"
    assert Scope.aware().depth == 1
    assert Scope.full().shares_session


def test_scope_validation():
    with pytest.raises(ValueError):
        Scope(detail="invalid")
    with pytest.raises(ValueError):
        Scope(peer="invalid")


# --- Session + Scope tests ---

def test_api_session_injects_context():
    session = MockAPISession()
    scope = Scope(peer="io")
    context = {"_prior_results": [{"function": "observe", "output": {"found": True}}]}
    session.apply_scope(scope, context)
    assert len(session._history) == 2
    assert "Prior results" in session._history[0]["content"]


def test_api_session_no_inject_when_peer_none():
    session = MockAPISession()
    scope = Scope(peer="none")
    context = {"_prior_results": [{"function": "observe"}]}
    session.apply_scope(scope, context)
    assert len(session._history) == 0


def test_api_session_compacts():
    session = MockAPISession()
    session.send("hello")
    scope = Scope(compact=True)
    session.post_execution(scope)
    assert "[Compacted]" in session._history[-2]["content"]


def test_cli_session_ignores_injection():
    session = MockCLISession()
    scope = Scope(depth=2, peer="io")
    context = {"_prior_results": [{"function": "observe"}]}
    session.apply_scope(scope, context)
    assert session.has_memory


def test_cli_session_forks_on_compact():
    session = MockCLISession()
    old_id = session._session_id
    scope = Scope(compact=True)
    session.post_execution(scope)
    assert session._forked
    assert session._session_id != old_id


def test_scope_str_partial():
    s = Scope(peer="io", compact=True)
    result = str(s)
    assert "peer=io" in result
    assert "compact=True" in result
