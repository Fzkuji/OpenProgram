"""A tool result is sent in its turn the way history renders it later.

History stores a result as one text and renders it as one text block. A
result made of several text blocks (a guide put first, the AGENTS.md a file
tool reached put after) must reach the provider in that form in its own turn
too, or Anthropic and chat completions send different bytes from the next
turn on and the provider cache breaks there.
"""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.providers.adapters.anthropic.anthropic import _convert_tool_result_block
import openprogram.models.providers.adapters.openai_completions.openai_completions as completions
from openprogram.models.providers.types import (
    AgentToolResult,
    ImageContent,
    TextContent,
    ToolResultMessage,
)
from openprogram.runtime.agent.loop.tool_dispatch import _as_stored
from openprogram.runtime.conversation.event_parsing import stored_tool_result

CALL = SimpleNamespace(id="c1", name="read")


def _message(content) -> ToolResultMessage:
    return ToolResultMessage(tool_call_id="c1", tool_name="read", content=content, timestamp=0)


def _completions(message: ToolResultMessage):
    context = SimpleNamespace(messages=[message], system_prompt=None, tools=None)
    model = SimpleNamespace(input=["text"], compat=None, reasoning=False, id="x",
                            provider="openai", api="openai-completions")
    return completions._build_messages(context, model)


def test_a_result_of_several_text_blocks_is_sent_as_history_renders_it():
    result = AgentToolResult(content=[
        TextContent(text="line\n" * 2000), TextContent(text=""),
        TextContent(text="\n\n# AGENTS.md\nrules"),
    ])
    in_turn = _message(_as_stored(CALL, result).content)
    # What render.py ``_tool_result`` sends on every later turn.
    later = _message([TextContent(type="text", text=stored_tool_result(result))])

    assert _convert_tool_result_block(in_turn) == _convert_tool_result_block(later)
    assert _completions(in_turn) == _completions(later)


def test_images_keep_their_place_and_a_single_block_is_left_alone():
    image = ImageContent(data="aGk=", mime_type="image/png")
    result = AgentToolResult(content=[image, TextContent(text="a"), TextContent(text="b")])
    assert [c.type for c in _as_stored(CALL, result).content] == ["image", "text"]
    assert _as_stored(CALL, result).content[1].text == "a\nb"

    single = AgentToolResult(content=[TextContent(text="only")])
    assert _as_stored(CALL, single) is single


def test_a_tool_search_result_is_bounded_and_keeps_its_load_record(monkeypatch, tmp_path):
    import openprogram.programs._host.runtime as host_runtime
    from openprogram.state.store.session.spill import result_cap

    saved = {}

    def _persist(call_id, whole):
        saved[call_id] = whole
        path = tmp_path / f"{call_id}.txt"
        path.write_text(whole)
        return str(path)

    monkeypatch.setattr(host_runtime, "_persist_full_result", _persist)
    loads = {"loads": [{"name": "t", "definition": {"name": "t", "description": "", "parameters": {}}}]}
    whole = "definition line\n" * (result_cap() // 2)
    result = AgentToolResult(content=[TextContent(text=whole)], details=loads)

    stored = _as_stored(SimpleNamespace(id="s1", name="tool_search"), result)

    from openprogram.state.store.session.weight import weight
    assert weight(stored.content[0].text) <= result_cap() < weight(whole)
    assert saved == {"s1": whole}
    assert stored.details["loads"] == loads["loads"]
    # Where the whole text is, and the head the live card shows.
    assert stored.details["output"]["full_path"] == str(tmp_path / "s1.txt")
    assert stored.details["output"]["total_chars"] == len(whole)
    assert stored.details["preview"] == whole[:4_000]


def test_a_result_with_no_output_says_so():
    """An empty text is rejected by some providers and read as a call that
    never returned by others, so it says it finished with nothing."""
    empty = AgentToolResult(content=[TextContent(text="")])
    assert _as_stored(CALL, empty).content[0].text == "(read completed with no output)"
    failed = AgentToolResult(content=[], is_error=True)
    assert _as_stored(CALL, failed).content[0].text == "(read failed with no output)"


def test_media_alone_is_sent_without_an_empty_text_block():
    image = ImageContent(data="aGk=", mime_type="image/png")
    stored = _as_stored(CALL, AgentToolResult(content=[TextContent(text=""), image]))
    assert [c.type for c in stored.content] == ["image"]


def test_a_tool_declared_cap_bounds_below_the_models(monkeypatch, tmp_path):
    import openprogram.programs._host.runtime as host_runtime
    from openprogram.programs._host.execution import with_cap
    from openprogram.state.store.session.weight import weight

    monkeypatch.setattr(host_runtime, "_persist_full_result",
                        lambda call_id, whole: str(tmp_path / "w.txt"))
    result = with_cap(AgentToolResult(content=[TextContent(text="z\n" * 2_000)]), 500)
    stored = _as_stored(CALL, result)
    assert weight(stored.content[0].text) <= 500
