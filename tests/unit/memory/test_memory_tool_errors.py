"""Registered memory tools report refusals and failures as tool errors."""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    import openprogram.state.paths as paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    return tmp_path / "state"


def _tool(name):
    import openprogram.programs.tools.knowledge.memory  # noqa: F401  registers
    from openprogram.programs import get_agent_tool

    tool = get_agent_tool(name)
    assert tool is not None, name
    return tool


def _run(tool, args):
    return asyncio.run(tool.execute("call-1", args, None, None))


def test_failed_memory_call_is_an_error_result(state_dir):
    from openprogram.programs.tools.knowledge.memory.memory import memory_get

    result = _run(_tool("memory_get"), {"path": "missing.md"})

    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    # Python callers keep the JSON string contract.
    assert json.loads(memory_get(path="missing.md"))["ok"] is False


def test_denied_memory_space_is_an_error_result(state_dir, monkeypatch):
    from openprogram.state.memory import policy

    def deny(*, write, space):
        raise policy.MemoryPolicyError("Memory space is not readable")

    monkeypatch.setattr(policy, "current", lambda: _Policy())
    monkeypatch.setattr(policy, "check", deny)

    result = _run(_tool("memory_search"), {"query": "plans"})

    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["error"]["code"] == "MEMORY_ACCESS_DENIED"


def test_successful_memory_call_is_not_an_error(state_dir):
    result = _run(_tool("memory_get"), {})

    assert result.is_error is False
    assert "Memory is empty" in result.content[0].text


class _Policy:
    write_space = "self"
    read_spaces = ("self",)
