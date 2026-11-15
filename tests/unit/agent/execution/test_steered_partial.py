"""A steer that cuts a response off mid tool call keeps no call without a result.

Mid-stream, providers hold content blocks as plain dicts. A steered reply once
kept such a dict call, the next request turned it into a real function call,
and OpenAI rejected the turn with "No tool output found for function call".
"""
from openprogram.models.providers.shared.openai_responses import convert_responses_messages
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.runtime.agent.loop.streaming import _steered_partial

MODEL = Model(id="gpt-test", name="test", api="openai-responses", provider="openai", base_url="http://test")


def _partial(*blocks):
    message = AssistantMessage(content=[], api=MODEL.api, provider=MODEL.provider, model=MODEL.id, timestamp=1)
    message.content.extend(blocks)
    return message


def test_a_dict_call_still_streaming_is_dropped_and_the_text_kept():
    partial = _partial(
        {"type": "text", "text": "checking the folder", "index": 0},
        {"type": "toolCall", "id": "call_A|fc_1", "name": "bash", "arguments": {}, "partial_json": '{"comm', "index": 1},
    )
    kept = _steered_partial(partial)
    assert [type(block) for block in kept.content] == [TextContent]
    assert kept.content[0].text == "checking the folder"
    assert len(partial.content) == 2, "the live partial is left alone"


def test_a_finished_call_is_dropped_too():
    partial = _partial(TextContent(text="done"), ToolCall(id="call_B|fc_2", name="bash", arguments={}))
    assert [block.type for block in _steered_partial(partial).content] == ["text"]


def test_the_request_after_the_steer_has_no_call_without_a_result():
    partial = _partial(
        {"type": "text", "text": "hi", "index": 0},
        {"type": "toolCall", "id": "call_A|fc_1", "name": "bash", "arguments": {}, "partial_json": "", "index": 1},
    )
    steered = _steered_partial(partial)
    steered.stop_reason = "aborted"
    context = Context(system_prompt="", tools=[], messages=[
        UserMessage(content="tidy the VPS notes", timestamp=1),
        steered,
        UserMessage(content="the DMIT server was released", timestamp=2),
    ])
    items = convert_responses_messages(MODEL, context)
    assert not [item for item in items if item.get("type") == "function_call"]
