"""Canvas public content semantics with actual sandbox-owned files."""

import asyncio
from pathlib import Path

import pytest

from openprogram.programs._host.runtime import current_tool_call_id, get
from openprogram.programs.tools.interaction import canvas


@pytest.fixture
def owned_canvas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "canvas.md"


async def _call(path: Path, action: str, block_id: str, **arguments):
    tool = get("canvas")
    assert tool is not None
    result = await tool.execute(
        f"canvas-{action}",
        {"action": action, "block_id": block_id, "path": str(path), **arguments},
        asyncio.Event(),
        None,
    )
    assert current_tool_call_id() is None
    return "\n".join(block.text for block in result.content)


def test_registered_set_empty_clears_only_named_block(owned_canvas):
    async def run():
        await _call(owned_canvas, "set", "first", content="old")
        await _call(owned_canvas, "set", "neighbor", content="keep")
        result = await _call(owned_canvas, "set", "first", content="")
        assert "Set block" in result
        assert await _call(owned_canvas, "get", "first") == ""
        assert await _call(owned_canvas, "get", "neighbor") == "keep"
        assert "`first` (0 chars)" in await _call(owned_canvas, "list", "first")

    asyncio.run(run())


@pytest.mark.parametrize("alias", ["body", "text"])
def test_bare_empty_content_keeps_precedence_over_alias(owned_canvas, alias):
    canvas.execute(action="set", block_id="first", content="old", path=str(owned_canvas))
    result = canvas.execute(
        action="set", block_id="first", content="", path=str(owned_canvas), **{alias: "must not win"}
    )
    assert isinstance(result, str) and "Set block" in result
    assert canvas.execute(action="get", block_id="first", path=str(owned_canvas)) == ""


@pytest.mark.parametrize("alias", ["body", "text"])
def test_bare_empty_alias_and_append_remain_valid(owned_canvas, alias):
    result = canvas.execute(op="set", id="first", file=str(owned_canvas), **{alias: ""})
    assert "Set block" in result
    assert canvas.execute(action="get", block_id="first", path=str(owned_canvas)) == ""
    canvas.execute(action="set", block_id="first", content="keep", path=str(owned_canvas))
    result = canvas.execute(action="append", block_id="first", content="", path=str(owned_canvas))
    assert "Appended" in result
    assert canvas.execute(action="get", block_id="first", path=str(owned_canvas)) == "keep"


@pytest.mark.parametrize("arguments", [{}, {"content": None}])
@pytest.mark.parametrize("action", ["set", "append"])
def test_registered_missing_content_does_not_modify(owned_canvas, arguments, action):
    async def run():
        await _call(owned_canvas, "set", "first", content="keep")
        before = owned_canvas.read_bytes()
        result = await _call(owned_canvas, action, "first", **arguments)
        assert "`content` is required" in result
        assert owned_canvas.read_bytes() == before
        assert await _call(owned_canvas, "get", "first") == "keep"

    asyncio.run(run())


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"action": "set", "block_id": 'bad"id', "content": "x"}, "invalid block id"),
        ({"action": "get", "block_id": "missing"}, "not found"),
        ({"action": "delete", "block_id": "missing"}, "not found"),
        ({"action": "set", "block_id": "block"}, "`content` is required"),
        ({"action": "append", "block_id": "block"}, "`content` is required"),
        ({"action": "get"}, "`block_id` is required"),
    ],
)
def test_registered_canvas_explicit_failure(owned_canvas, arguments, message):
    async def run():
        tool = get("canvas")
        result = await tool.execute(
            "canvas-explicit-failure", {"path": str(owned_canvas), **arguments}, asyncio.Event(), None
        )
        assert message in result.content[0].text
        assert result.is_error is True
        assert current_tool_call_id() is None
        assert not owned_canvas.exists()
        bare = canvas.execute(path=str(owned_canvas), **arguments)
        assert isinstance(bare, str) and bare == result.content[0].text

    asyncio.run(run())


def test_registered_canvas_error_prefix_body_is_success(owned_canvas):
    async def run():
        body = "Error: ordinary report content\nline two"
        await _call(owned_canvas, "set", "block", content=body)
        tool = get("canvas")
        result = await tool.execute(
            "canvas-valid-prefix", {"action": "get", "block_id": "block", "path": str(owned_canvas)},
            asyncio.Event(), None,
        )
        assert result.content[0].text == body and result.is_error is False
        assert canvas.execute(action="get", block_id="block", path=str(owned_canvas)) == body

    asyncio.run(run())
