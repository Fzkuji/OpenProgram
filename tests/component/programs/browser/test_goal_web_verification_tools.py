"""Registered web inspection failures must be visible to Goal's judge."""
import asyncio
import importlib

import pytest

from openprogram.programs import agent_tools


@pytest.mark.parametrize("name", ["web_fetch", "web_search"])
def test_missing_web_input_is_a_structured_tool_error(name):
    tool = agent_tools(names=[name])[0]
    result = asyncio.run(tool.execute("goal-verification", {}, None, None))
    assert result.is_error is True
    assert result.content[0].text.startswith("Error:")


@pytest.mark.parametrize("name,module_path", [
    ("web_fetch", "openprogram.programs.tools.web.web_fetch"),
    ("web_search", "openprogram.programs.tools.web.web_search.web_search"),
])
@pytest.mark.parametrize("text,failed", [
    ("Error: backend unavailable", True),
    ("# Retrieved source\nError: quoted source text", False),
])
def test_registered_web_tool_preserves_result_error_semantics(
    monkeypatch, name, module_path, text, failed,
):
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "execute", lambda **_kwargs: text)
    tool = agent_tools(names=[name])[0]
    result = asyncio.run(tool.execute("goal-verification", {}, None, None))
    assert result.is_error is failed
    assert result.content[0].text == text
    assert module.execute() == text
