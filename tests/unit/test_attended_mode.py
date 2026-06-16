"""Attended / unattended mode gates the user-question tool.

Unattended (the default for background/CLI runs) must withhold
``ask_user_question`` no matter which toolset a function requested, so the
agent can't block on a prompt nobody is there to answer.
"""
from __future__ import annotations

import pytest

from openprogram.agent import attended
from openprogram.functions import agent_tools


@pytest.fixture(autouse=True)
def _restore_mode():
    before = attended.is_attended()
    yield
    attended.set_attended(before)


def test_default_is_unattended():
    # A fresh process defaults to unattended — a bare run has no watcher.
    # (Re-assert the module default explicitly.)
    attended.set_attended(False)
    assert attended.is_attended() is False
    assert "ask_user_question" in attended.denied_ask_tools()


def test_attended_allows_ask_tool():
    attended.set_attended(True)
    assert attended.denied_ask_tools() == []


def _names(toolset, deny):
    return [getattr(t, "name", t) for t in (agent_tools(toolset=toolset, deny=deny) or [])]


def test_unattended_strips_ask_from_full_toolset():
    attended.set_attended(False)
    deny = (attended.denied_ask_tools() or None)
    names = _names("full", deny)
    assert "ask_user_question" not in names
    # other tools survive
    assert "bash" in names


def test_attended_keeps_ask_in_full_toolset():
    attended.set_attended(True)
    deny = (attended.denied_ask_tools() or None)
    names = _names("full", deny)
    assert "ask_user_question" in names


def test_per_session_isolation():
    # One session attended must not flip another (single worker, many sessions).
    attended.set_attended(False)               # default unattended
    attended.set_attended(True, "sessA")
    assert attended.is_attended("sessA") is True
    assert attended.is_attended("sessB") is False  # falls back to default
    assert attended.denied_ask_tools("sessA") == []
    assert "ask_user_question" in attended.denied_ask_tools("sessB")
    attended.clear_session("sessA")
    assert attended.is_attended("sessA") is False  # back to default
