"""A reply that ends inside a block ends with that block as content.

An adapter fills a block as a plain dict and types it when the block ends
(the Codex adapter does). A reply it aborts mid-block, on the caller's stop,
carries the dict in its final message. Read through the one place every
provider's stream passes (``stream_simple_with_provider``), the final message
holds text and thinking as content blocks, so recording it does not warn.
"""
from __future__ import annotations

import asyncio
import warnings

from openprogram.models.providers.streaming.stream import stream_simple_with_provider
from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    UserMessage,
)
from openprogram.models.providers.utils.event_stream import AssistantMessageEventStream


def _model() -> Model:
    return Model(id="blocks-model", name="Blocks model", api="blocks-test-api",
                 provider="blocks-provider", base_url="http://blocks.invalid")


class _AbortsMidBlock:
    """Streams part of a thinking block and a text block, then ends aborted."""

    requires_credentials = False

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    def stream_simple(self, model, context, options=None):
        stream = AssistantMessageEventStream()
        output = AssistantMessage(content=[], api=model.api, provider=model.provider,
                                  model=model.id, timestamp=0)

        async def run() -> None:
            stream.push({"type": "start", "partial": output})
            output.content.append({"type": "thinking", "thinking": "count", "index": 0})
            output.content.append({"type": "text", "text": "1\n2\n", "index": 1})
            output.content.append({"type": "toolCall", "id": "call_1", "name": "look",
                                   "arguments": {}, "partial_json": '{"q": ', "index": 2})
            stream.push({"type": "text_delta", "content_index": 1, "delta": "2\n", "partial": output})
            for block in output.content:
                block.pop("index", None)
            output.stop_reason = "aborted"
            stream.push({"type": "error", "reason": "aborted", "error": output})
            stream.end(output)

        stream.attach_producer(asyncio.ensure_future(run()))
        return stream


def test_an_aborted_final_message_holds_its_text_and_thinking_as_content_blocks():
    async def read():
        events = []
        context = Context(system_prompt="", messages=[UserMessage(role="user", content="hi", timestamp=0)])
        async for event in stream_simple_with_provider(
            _AbortsMidBlock(), _model(), context, SimpleStreamOptions(),
        ):
            events.append(event)
        return events

    final = asyncio.run(read())[-1].error

    assert final.stop_reason == "aborted"
    thinking, text, call = final.content
    assert isinstance(thinking, ThinkingContent) and thinking.thinking == "count"
    assert isinstance(text, TextContent) and text.text == "1\n2\n"
    # An unfinished call was never a call to dispatch: it stays as the adapter left it.
    assert isinstance(call, dict) and call["name"] == "look"

    final.content.pop()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dumped = final.model_dump(mode="json")
    assert [str(item.message) for item in caught] == []
    assert [block["type"] for block in dumped["content"]] == ["thinking", "text"]
