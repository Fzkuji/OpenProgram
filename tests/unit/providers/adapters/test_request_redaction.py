from __future__ import annotations

import asyncio

from openprogram.models.providers.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    EventToolCallEnd,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from openprogram.models.providers.utils.event_stream import AssistantMessageEventStream
from openprogram.models.providers.utils.redaction import Vault, redact_context, redacting_stream_fn

KEY = "sk-ant-api03-" + "a" * 40
TOKEN = "ghp_" + "b" * 36


def _message(content) -> AssistantMessage:
    return AssistantMessage(
        content=content, api="anthropic-messages", provider="test", model="m",
        usage=Usage(), timestamp=1,
    )


def test_secrets_and_emails_become_numbered_placeholders_and_come_back() -> None:
    vault = Vault()
    text = f"key {KEY}, token {TOKEN}, password=hunter2hunter2, mail me@example.com, again {KEY}"
    redacted = vault.redact(text)

    assert KEY not in redacted and TOKEN not in redacted
    assert "hunter2hunter2" not in redacted and "me@example.com" not in redacted
    assert redacted == (
        "key [[SECRET_1]], token [[SECRET_2]], password=[[SECRET_3]], "
        "mail [[EMAIL_4]], again [[SECRET_1]]"
    )
    assert vault.restore(redacted) == text


def test_the_same_history_redacts_to_the_same_request() -> None:
    context = Context(system_prompt="be brief", messages=[
        UserMessage(content=f"use {KEY}", timestamp=1),
        ToolResultMessage(tool_call_id="c1", tool_name="read",
                          content=[TextContent(text=f"TOKEN={TOKEN}")], timestamp=2),
    ])
    first = redact_context(context, Vault())
    second = redact_context(context, Vault())
    assert first.model_dump() == second.model_dump()
    assert KEY not in str(first.model_dump()) and TOKEN not in str(first.model_dump())
    # The caller's history is never rewritten.
    assert context.messages[0].content == f"use {KEY}"


def test_binary_data_and_signatures_are_left_alone() -> None:
    image = ImageContent(data="eyJhbGciOi.eyJzdWIiOiIx.c2lnbmF0dXJl", mime_type="image/png")
    call = ToolCall(id="me@example.com", name="send", arguments={"to": "me@example.com"},
                    thought_signature="me@example.com")
    context = Context(messages=[
        UserMessage(content=[image], timestamp=1),
        _message([call]),
    ])
    redacted = redact_context(context, Vault())
    assert redacted.messages[0].content[0].data == image.data
    sent = redacted.messages[1].content[0]
    assert sent.id == "me@example.com" and sent.thought_signature == "me@example.com"
    assert sent.arguments == {"to": "[[EMAIL_1]]"}


def test_nothing_to_redact_leaves_the_stream_untouched() -> None:
    sentinel = object()
    fn = redacting_stream_fn(lambda model, context, options: sentinel)
    context = Context(messages=[UserMessage(content="hello", timestamp=1)])
    assert fn(object(), context, None) is sentinel


def test_the_provider_sees_placeholders_and_the_reply_gets_real_values() -> None:
    async def run() -> None:
        sent: list[Context] = []

        def provider(model, context, options):
            sent.append(context)
            stream = AssistantMessageEventStream()

            async def produce() -> None:
                message = _message([])
                stream.push(EventStart(partial=message))
                stream.push(EventTextStart(content_index=0, partial=message))
                text = ""
                # The placeholder arrives split over three deltas.
                for piece in ("Using [", "[SECRET_", "1]] now"):
                    text += piece
                    message = _message([TextContent(text=text)])
                    stream.push(EventTextDelta(content_index=0, delta=piece, partial=message))
                stream.push(EventTextEnd(content_index=0, content=text, partial=message))
                call = ToolCall(id="c1", name="bash", arguments={"command": "curl -H 'x: [[SECRET_1]]'"})
                message = _message([TextContent(text=text), call])
                stream.push(EventToolCallEnd(content_index=1, tool_call=call, partial=message))
                stream.push(EventDone(reason="toolUse", message=message))

            asyncio.create_task(produce())
            return stream

        fn = redacting_stream_fn(provider)
        context = Context(messages=[UserMessage(content=f"call it with {KEY}", timestamp=1)])
        events = [event async for event in fn(object(), context, None)]

        assert KEY not in str(sent[0].model_dump())
        deltas = "".join(e.delta for e in events if e.type == "text_delta")
        assert deltas == f"Using {KEY} now"
        assert all("[[" not in str(e.partial.model_dump()) for e in events if hasattr(e, "partial"))
        done = events[-1]
        assert done.message.content[0].text == f"Using {KEY} now"
        assert done.message.content[1].arguments == {"command": f"curl -H 'x: {KEY}'"}
        tool_end = next(e for e in events if e.type == "toolcall_end")
        assert tool_end.tool_call.arguments == {"command": f"curl -H 'x: {KEY}'"}

    asyncio.run(run())


def test_a_bracket_at_the_end_of_the_reply_is_not_lost() -> None:
    async def run() -> None:
        def provider(model, context, options):
            stream = AssistantMessageEventStream()

            async def produce() -> None:
                message = _message([TextContent(text="see [")])
                stream.push(EventStart(partial=message))
                stream.push(EventTextStart(content_index=0, partial=message))
                stream.push(EventTextDelta(content_index=0, delta="see [", partial=message))
                stream.push(EventTextEnd(content_index=0, content="see [", partial=message))
                stream.push(EventDone(reason="stop", message=message))

            asyncio.create_task(produce())
            return stream

        fn = redacting_stream_fn(provider)
        context = Context(messages=[UserMessage(content=f"{KEY}", timestamp=1)])
        events = [event async for event in fn(object(), context, None)]
        assert "".join(e.delta for e in events if e.type == "text_delta") == "see ["

    asyncio.run(run())


def test_a_stop_reaches_the_provider_request_under_the_redaction_wrapper() -> None:
    from openprogram.models.providers.utils.event_stream import close_stream

    async def run() -> None:
        ended: list[bool] = []

        def provider(model, context, options):
            stream = AssistantMessageEventStream()

            async def produce() -> None:
                stream.push(EventStart(partial=_message([])))
                try:
                    await asyncio.Event().wait()
                finally:
                    ended.append(True)

            stream.attach_producer(asyncio.ensure_future(produce()))
            return stream

        fn = redacting_stream_fn(provider)
        context = Context(messages=[UserMessage(content=f"call it with {KEY}", timestamp=1)])
        stream = fn(object(), context, None)
        assert (await stream.__anext__()).type == "start"

        await close_stream(stream)  # what a stopped turn does with its response stream

        assert ended == [True]

    asyncio.run(run())


def test_a_restarted_reply_does_not_inherit_a_half_placeholder() -> None:
    from openprogram.models.providers.types import EventRetry

    async def run() -> None:
        def provider(model, context, options):
            stream = AssistantMessageEventStream()

            async def produce() -> None:
                stream.push(EventStart(partial=_message([])))
                # The first attempt stops inside a placeholder...
                stream.push(EventTextDelta(content_index=0, delta="Using [[SEC",
                                           partial=_message([TextContent(text="Using [[SEC")])))
                stream.push(EventRetry(attempt=2, reason="transport", restart=True))
                # ...and the request is sent again from its start.
                message = _message([TextContent(text="Fresh")])
                stream.push(EventTextDelta(content_index=0, delta="Fresh", partial=message))
                stream.push(EventDone(reason="stop", message=message))

            asyncio.create_task(produce())
            return stream

        fn = redacting_stream_fn(provider)
        context = Context(messages=[UserMessage(content=f"call it with {KEY}", timestamp=1)])
        events = [event async for event in fn(object(), context, None)]
        deltas = [e.delta for e in events if e.type == "text_delta"]
        assert deltas == ["Using ", "Fresh"]

    asyncio.run(run())
