"""A request refused for its size is sent again with its oldest images left out.

The cap the refusal teaches is kept for the provider, later requests are
fitted to it before they go, and the latest user message's images and all
text stay.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventError,
    EventStart,
    ImageContent,
    Model,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from openprogram.models.providers.utils import body_cap, request_repair

IMAGE = "A" * 100_000


@pytest.fixture(autouse=True)
def _fresh_caps():
    body_cap.forget()
    yield
    body_cap.forget()


def _model() -> Model:
    return Model(id="m", name="m", api="completion", provider="fake", base_url="https://x.invalid")


def _context(images: int) -> Context:
    messages = [
        ToolResultMessage(tool_call_id=f"c{i}", tool_name="screenshot", timestamp=1,
                          content=[TextContent(text=f"shot {i}"), ImageContent(data=IMAGE, mime_type="image/png")])
        for i in range(images)
    ]
    messages.append(UserMessage(content=[TextContent(text="what now?"),
                                         ImageContent(data=IMAGE, mime_type="image/png")], timestamp=1))
    return Context(system_prompt="sys", messages=messages)


def _reply(text="ok", *, error: str | None = None) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)] if not error else [], api="completion",
                            provider="fake", model="m", stop_reason="error" if error else "stop",
                            error_message=error, timestamp=1)


class Provider:
    """Refuses any request over ``cap`` bytes with ``refusal``."""

    def __init__(self, cap: int, refusal: str) -> None:
        self.cap, self.refusal = cap, refusal
        self.sent: list[Context] = []

    def __call__(self, _model, context, _options):
        self.sent.append(context)

        async def events():
            yield EventStart(partial=_reply(""))
            if body_cap.request_bytes(context) > self.cap:
                yield EventError(reason="error", error=_reply(error=self.refusal))
            else:
                yield EventDone(reason="stop", message=_reply())

        return events()


def _run(provider, context):
    async def main():
        context_fitted = request_repair.prepared(_model(), context)
        events = request_repair.repairing(
            provider(_model(), context_fitted, None), provider, _model(), context_fitted, None)
        return [event async for event in events]

    return asyncio.run(main())


def _images(context: Context) -> int:
    return sum(isinstance(block, ImageContent) for message in context.messages
               if not isinstance(message.content, str) for block in message.content)


def test_a_size_refusal_is_sent_again_with_the_oldest_images_left_out():
    provider = Provider(cap=450_000, refusal="Error code: 413 - request_too_large")
    events = _run(provider, _context(images=8))

    assert [event.type for event in events] == ["start", "done"]
    first, second = provider.sent[0], provider.sent[-1]
    assert _images(first) == 9 and len(provider.sent) <= 1 + request_repair.MAX_RESENDS
    assert body_cap.request_bytes(second) <= 450_000
    left = second.messages
    assert left[0].content[1].text == body_cap.IMAGE_LEFT_OUT
    assert left[0].content[0].text == "shot 0"  # text is never touched
    assert isinstance(left[-1].content[1], ImageContent)  # the latest user message keeps its image


def test_the_stated_cap_is_learned_and_later_requests_go_fitted():
    refusal = "Request body is 1.0 MB, over the 300 KB limit for one request"
    provider = Provider(cap=300 * 1024, refusal=refusal)
    _run(provider, _context(images=8))
    assert body_cap.known_cap(_model()) == 300 * 1024

    later = Provider(cap=300 * 1024, refusal=refusal)
    _run(later, _context(images=8))
    assert len(later.sent) == 1
    assert body_cap.request_bytes(later.sent[0]) <= body_cap.room(300 * 1024)


def test_when_no_earlier_image_is_left_to_go_the_refusal_goes_on():
    provider = Provider(cap=50_000, refusal="413 Payload Too Large")
    events = _run(provider, _context(images=1))
    assert events[-1].type == "error"


@pytest.mark.parametrize("text, refused", [
    ("Error code: 413 - {'type': 'request_too_large'}", True),
    ("HTTP 413: Payload Too Large", True),
    ("request body exceeds the limit", True),
    ("prompt is too long: 250000 tokens > 200000 maximum", False),
    ("Error code: 429 - rate limit", False),
])
def test_what_counts_as_a_size_refusal(text, refused):
    assert body_cap.refused_for_size(_reply(error=text)) is refused


def test_stated_caps_ignore_the_body_size_itself():
    text = "Request body is 40.1 MB, over the 32 MB limit for one request (33554432 bytes)"
    assert body_cap.stated_cap(text, sent=42_000_000) == 32 * 1024 * 1024
