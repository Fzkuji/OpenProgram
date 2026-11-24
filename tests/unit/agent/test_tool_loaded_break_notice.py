"""A search that loaded a tool announces the cache break its load makes,
whatever note the runtime put after its result (``streaming._note_tool_loaded``)."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.models.providers.types import TextContent, ToolResultMessage
from openprogram.runtime.agent.loop import streaming
from openprogram.runtime.agent.loop.message_context import RuntimeNote


def _search_result():
    definition = {"name": "image_analyze", "description": "d", "parameters": {"type": "object"}}
    return ToolResultMessage(tool_call_id="c1", tool_name="tool_search",
                             content=[TextContent(text="Loaded image_analyze")],
                             details={"loads": [{"name": "image_analyze", "definition": definition}]},
                             timestamp=0)


def test_the_load_is_announced_past_the_runtime_note(monkeypatch):
    announced = []
    monkeypatch.setattr("openprogram.models.usage.prefix_fingerprint.expect_break",
                        lambda session_id, cause, **_kw: announced.append((session_id, cause)))
    config = SimpleNamespace(session_id="s1", model=SimpleNamespace(provider="xai-subscription",
                                                                    api="openai-responses", id="grok-4.7"))
    note = RuntimeNote(content=[TextContent(text="[Runtime permission setting] bypass")], timestamp=0)
    streaming._note_tool_loaded(config, [_search_result(), note])
    assert announced == [("s1", "tool_loaded")]
