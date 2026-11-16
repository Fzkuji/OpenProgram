"""A reply's reasoning and message ids replay in later turns as they were sent.

On a Responses backend with ``store=false`` (Codex) a request carries the
turn's reasoning items whole (``encrypted_content``) and each assistant
message under the id the backend gave it. The next turn's first request
reuses the cached prefix only if it sends those items again, byte for byte:
stored without them, the history breaks the cache right after the first
user message of the turn that reasoned.
"""
from __future__ import annotations

import json

from openprogram.models.providers.types import Context, TextContent, ThinkingContent, ToolCall
from openprogram.runtime.agent import turn_request as agent_turn_request  # noqa: F401
from tests.support.codex_turns import (  # noqa: F401  (fixtures)
    _model_and_profile,
    _reply,
    _scripted_stream,
    _turn,
    _wire,
    lookup_tool,
    tmp_db,
)


def _reasoning(n: int, summary: str = "") -> ThinkingContent:
    """A Codex reasoning block as the shared Responses processor stores it."""
    item = {"id": f"rs_{n}", "type": "reasoning",
            "summary": [{"type": "summary_text", "text": summary}] if summary else [],
            "content": None, "encrypted_content": f"gAAAA-encrypted-{n}", "status": None}
    return ThinkingContent(thinking=summary, thinking_signature=json.dumps(item))


def _first_turn_replies():
    return [
        _reply(_reasoning(1),
               ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}), stop="toolUse"),
        _reply(_reasoning(2, "Now b."), TextContent(text="Checking b.", text_signature="msg_srv_2"),
               ToolCall(id="call_2|fc_2", name="lookup", arguments={"key": "b"}), stop="toolUse"),
        _reply(_reasoning(3), TextContent(text="a and b are 42", text_signature="msg_srv_3")),
    ]


def _extends(last: list[str], first_next: list[str]) -> None:
    diverged = next((index for index, (a, b) in enumerate(zip(last, first_next)) if a != b), None)
    assert diverged is None and len(first_next) > len(last), (
        f"the previous turn re-rendered differently from item {diverged}: "
        f"{last[diverged] if diverged is not None else ''}")


def test_reasoning_and_message_ids_replay_in_the_next_turns(tmp_db, lookup_tool, monkeypatch):
    requests: list[Context] = []
    stream = _scripted_stream(requests, [
        *_first_turn_replies(),
        _reply(_reasoning(4), TextContent(text="done", text_signature="msg_srv_4")),
        _reply(TextContent(text="ok", text_signature="msg_srv_5")),
    ])
    _turn("look up a, then b", stream, monkeypatch)
    _turn("thanks", stream, monkeypatch)
    _turn("bye", stream, monkeypatch)
    wire = [_wire(context) for context in requests]
    assert len(wire) == 5

    # Inside the turn every request carries the reasoning items on.
    assert sum('"encrypted_content": "gAAAA-encrypted-1"' in item for item in wire[2]) == 1
    assert any('"id": "msg_srv_2"' in item for item in wire[2])
    # The next turns start with the previous turn's last request, whole.
    _extends(wire[2], wire[3])
    _extends(wire[3], wire[4])
    # The final answer goes back with its own reasoning and id.
    assert any('"encrypted_content": "gAAAA-encrypted-3"' in item for item in wire[3])
    assert any('"id": "msg_srv_3"' in item for item in wire[3])


def test_a_retried_turn_starts_with_the_turn_before_it(tmp_db, lookup_tool, monkeypatch):
    """A retry forks off the previous reply and renders it from the store."""
    requests: list[Context] = []
    stream = _scripted_stream(requests, [
        *_first_turn_replies(),
        _reply(TextContent(text="ok", text_signature="msg_srv_4")),
        _reply(TextContent(text="ok again", text_signature="msg_srv_5")),
    ])
    first = _turn("look up a, then b", stream, monkeypatch)
    _turn("thanks", stream, monkeypatch)
    _turn("thanks", stream, monkeypatch, branch_from=first.assistant_msg_id)
    wire = [_wire(context) for context in requests]
    _extends(wire[2], wire[4])
    assert wire[4] == wire[3]


def test_a_turn_resumed_from_its_journal_keeps_its_reasoning(tmp_db, lookup_tool, monkeypatch):
    """The cards a journal folds back carry the reasoning items and message
    ids too, so a resumed turn renders in the next turn as it was sent."""
    from openprogram.runtime.execution.agent_journal import DECISION, TOOL_RESULT, JournalState, apply_journal_record

    replies = _first_turn_replies()
    journal = JournalState(execution_id="run-1")
    for n, reply in enumerate(replies, 1):
        apply_journal_record(journal, {"type": DECISION, "data": {
            "position": 2 * n - 1, "action_id": f"a{n}", "message": reply.model_dump(mode="json"), "turn": {}}})
        for block in reply.content:
            if isinstance(block, ToolCall):
                apply_journal_record(journal, {"type": TOOL_RESULT, "data": {
                    "position": 2 * n, "tool_call_id": block.id, "is_error": False,
                    "result": {"content": [{"type": "text",
                                            "text": f"value of {block.arguments['key']} is 42"}]}}})
    display = journal.display_blocks()
    assert [bool(block.get("round_start")) for block in display] == [True, False, True, False, False, True, False]

    requests: list[Context] = []
    stream = _scripted_stream(requests, [*replies, _reply(TextContent(text="ok"))])
    first = _turn("look up a and b", stream, monkeypatch)
    node = tmp_db.get_messages_by_id("c1", [first.assistant_msg_id])[0]
    extra = json.loads(node["extra"]) if isinstance(node.get("extra"), str) else dict(node.get("extra") or {})
    extra["blocks"] = display
    tmp_db.merge_node_metadata("c1", first.assistant_msg_id, {"extra": json.dumps(extra)})
    _turn("thanks", stream, monkeypatch)
    wire = [_wire(context) for context in requests]
    _extends(wire[2], wire[3])


def test_reasoning_stays_out_of_the_transcript(tmp_db, lookup_tool, monkeypatch):
    """The stored reasoning item is for the model; the transcript shows only
    reasoning that has a summary, and never the encrypted payload."""
    from openprogram.interfaces.server.transcript.blocks import project_blocks

    stream = _scripted_stream([], _first_turn_replies())
    first = _turn("look up a, then b", stream, monkeypatch)
    node = tmp_db.get_messages_by_id("c1", [first.assistant_msg_id])[0]
    extra = json.loads(node["extra"]) if isinstance(node.get("extra"), str) else dict(node.get("extra") or {})
    blocks, _tools, thinking = project_blocks(first.assistant_msg_id, extra["blocks"])
    assert thinking == "Now b."
    assert "encrypted" not in json.dumps(blocks)


def test_a_redacted_block_replays_redacted(tmp_db, lookup_tool, monkeypatch):
    """An Anthropic redacted_thinking block carries its payload as the
    signature; replayed as plain thinking it would be sent as the wrong
    block kind."""
    requests: list[Context] = []
    stream = _scripted_stream(requests, [
        _reply(ThinkingContent(thinking="", thinking_signature="opaque-redacted-data", redacted=True),
               ToolCall(id="call_1|fc_1", name="lookup", arguments={"key": "a"}), stop="toolUse"),
        _reply(TextContent(text="42")),
        _reply(TextContent(text="ok")),
    ])
    _turn("look up a", stream, monkeypatch)
    _turn("thanks", stream, monkeypatch)
    sent = [block for message in requests[1].messages if message.role == "assistant"
            for block in message.content if isinstance(block, ThinkingContent)]
    replayed = [block for message in requests[2].messages if message.role == "assistant"
                for block in message.content if isinstance(block, ThinkingContent)]
    assert sent == replayed == [ThinkingContent(thinking="", thinking_signature="opaque-redacted-data",
                                                redacted=True)]


def test_another_model_reads_no_earlier_reasoning(tmp_db, lookup_tool, monkeypatch):
    """Reasoning goes back only to the model that wrote it. Another model
    would read a foreign summary as text the assistant said
    (transform_messages turns foreign thinking into text), so after a
    switch earlier turns send no reasoning at all, as they did before
    reasoning was stored."""
    from openprogram.models.providers.shared.transform_messages import transform_messages
    from openprogram.models.providers.types import Model
    from openprogram.runtime.conversation import turn_runtime as D

    requests: list[Context] = []
    stream = _scripted_stream(requests, [
        *_first_turn_replies(), _reply(TextContent(text="ok")), _reply(TextContent(text="ok"))])
    _turn("look up a, then b", stream, monkeypatch)
    for other in (
        Model(id="claude-x", name="claude-x", api="anthropic-messages", provider="anthropic",
              base_url="https://api.anthropic.com"),
        Model(id="cache-test-2", name="cache-test-2", api="openai-codex", provider="openai-codex",
              base_url="https://chatgpt.com/backend-api"),
    ):
        monkeypatch.setattr(D, "_resolve_model", lambda profile, override=None, other=other: other)
        _turn("thanks", stream, monkeypatch)
        sent = transform_messages(requests[-1].messages, other)
        said = [block.text for message in sent if message.role == "assistant"
                for block in message.content if isinstance(block, TextContent)]
        assert said[:2] == ["Checking b.", "a and b are 42"] and "Now b." not in said, other.id
        assert not any(isinstance(block, ThinkingContent) for message in sent
                       if message.role == "assistant" for block in message.content), other.id
