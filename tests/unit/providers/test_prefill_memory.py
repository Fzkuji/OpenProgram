"""A model that refuses a prefill gets a user turn after the assistant's.

The refusal is remembered for that model on that provider and API; the
refused request is sent again with "Continue." last, and later ones go so
from the start. Other models, and conversations ending with a tool call,
are left alone.
"""
from __future__ import annotations

import asyncio

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventError,
    EventStart,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)
from openprogram.models.providers.utils import prefill, request_repair

REFUSAL = ("This model does not support assistant message prefill. "
           "The conversation must end with a user message.")


def _model(model_id="claude-x") -> Model:
    return Model(id=model_id, name=model_id, api="anthropic-messages", provider="gateway",
                 base_url="https://x.invalid")


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), api="anthropic-messages", provider="gateway",
                            model="claude-x", timestamp=1)


def _context(last) -> Context:
    return Context(messages=[UserMessage(content="hi", timestamp=1), last])


class Provider:
    def __init__(self):
        self.sent: list[Context] = []

    def __call__(self, _model, context, _options):
        self.sent.append(context)

        async def events():
            yield EventStart(partial=_assistant())
            if isinstance(context.messages[-1], AssistantMessage):
                error = _assistant()
                error.stop_reason, error.error_message = "error", REFUSAL
                yield EventError(reason="error", error=error)
            else:
                yield EventDone(reason="stop", message=_assistant(TextContent(text="ok")))

        return events()


def _run(provider, model, context):
    async def main():
        ready = request_repair.prepared(model, context)
        return [event async for event in
                request_repair.repairing(provider(model, ready, None), provider, model, ready, None)]

    return asyncio.run(main())


def test_a_refused_prefill_goes_again_with_a_user_turn_and_is_remembered():
    provider = Provider()
    events = _run(provider, _model(), _context(_assistant(TextContent(text="Sure, "))))
    assert [event.type for event in events] == ["start", "done"]
    assert len(provider.sent) == 2
    assert provider.sent[1].messages[-1].content == prefill.CONTINUE_TEXT
    assert prefill.known_to_refuse(_model())

    later = Provider()
    _run(later, _model(), _context(_assistant(TextContent(text="Sure, "))))
    assert len(later.sent) == 1 and isinstance(later.sent[0].messages[-1], UserMessage)


def test_the_memory_is_per_model():
    _run(Provider(), _model(), _context(_assistant(TextContent(text="Sure, "))))
    assert not prefill.known_to_refuse(_model("claude-y"))
    other = _context(_assistant(TextContent(text="Sure, ")))
    assert request_repair.prepared(_model("claude-y"), other) is other


def test_a_conversation_ending_with_a_call_is_not_a_prefill():
    called = _context(_assistant(ToolCall(id="c1", name="read", arguments={})))
    assert not prefill.ends_with_assistant(called)
