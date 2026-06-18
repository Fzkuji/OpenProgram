"""CallableModel — bridge a user-supplied ``call=fn`` into the single
provider/AgentSession path.

Background
----------
``Runtime(call=fn)`` lets a caller hand the framework their own LLM-calling
function instead of naming a provider model. Historically this took a
*separate* code path inside ``Runtime.exec`` (the "legacy call" branch:
``self._call(content)`` → ``fn`` → text, no tool loop, its own DAG write).
That second path drifted from the provider path — most visibly, the provider
path forgot to record llm DAG nodes for a while, and the two composed
context differently.

This adapter collapses the two. A ``call=fn`` runtime now sets
``api_model = CallableModel(fn)`` and flows through the SAME
``_call_via_providers`` → ``AgentSession`` → ``agent_loop`` path as a real
provider. The only difference is the *stream function*: instead of hitting a
provider over the network, :func:`make_callable_stream_fn` re-renders the
loop's messages back into the ``content`` list the user's ``fn`` expects,
calls ``fn``, and emits its reply as a single-message event stream.

What the user's ``fn`` sees
---------------------------
The same ``content`` list it always saw: ``[{"type":"text","text":...}, ...]``.
The agent loop builds a pi-ai ``Message`` list (system prompt + history +
current turn); we flatten it back to text blocks so existing callables —
which branch on ``content`` text (see ``tests/providers/test_functions.py``
``_mock_call``) — keep working unchanged.

Tool calls
----------
A callable returns plain text; it has no way to emit a ``tool_use``. So the
adapter forces a single-round, no-tool turn: it emits the reply as text and
stops. The agent loop sees no tool calls and ends. (Callable-backed runtimes
are also pinned to ``toolset="none"`` at the Runtime layer so the model is
never even offered tools it couldn't call.)
"""
from __future__ import annotations

import inspect
import time
from typing import Any, AsyncGenerator, Callable, Optional


def _messages_to_content(system_prompt: Optional[str], messages: list) -> list[dict]:
    """Flatten a pi-ai ``Message`` list back into the ``content`` block list
    a user ``call=fn`` expects.

    Mirrors the legacy text-merge: a leading system block (when present),
    then every message's text content in order. Non-text blocks (images,
    tool results) are rendered as a short text marker — a callable can't
    consume binary blocks, and dropping them silently would hide context.
    """
    blocks: list[dict] = []
    if system_prompt:
        blocks.append({"type": "text", "text": system_prompt, "role": "system"})
    for msg in messages or []:
        role = getattr(msg, "role", None) or "user"
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            blocks.append({"type": "text", "text": content, "role": role})
            continue
        for part in content or []:
            text = getattr(part, "text", None)
            if text is not None:
                blocks.append({"type": "text", "text": text, "role": role})
                continue
            # Non-text part (image / tool_use / tool_result). Render a marker
            # so the callable at least knows something was there.
            ptype = getattr(part, "type", "?")
            blocks.append({"type": "text", "text": f"[{ptype}]", "role": role})
    return blocks


def make_callable_stream_fn(
    fn: Callable[..., Any],
    *,
    response_format: Optional[dict] = None,
) -> Callable:
    """Wrap a user ``call=fn`` into a ``StreamFn`` for the agent loop.

    The returned async generator matches the ``StreamFn`` protocol
    (``fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent]``)
    used by ``agent_loop`` via ``AgentSession``. It calls the user's ``fn``
    with the flattened ``content`` list and emits the reply as one text
    message, then ``EventDone``.

    ``response_format`` (when the runtime carries one) is forwarded to ``fn``
    so callables that implement provider-native JSON mode still get it —
    preserving the behaviour the legacy path had.
    """

    async def _stream(model, context, options=None) -> AsyncGenerator[Any, None]:
        from openprogram.providers.types import (
            AssistantMessage,
            TextContent,
            EventStart,
            EventTextStart,
            EventTextEnd,
            EventDone,
        )

        system_prompt = getattr(context, "system_prompt", None)
        messages = getattr(context, "messages", None) or []
        content = _messages_to_content(system_prompt, messages)

        model_id = getattr(model, "id", None) or "callable"

        # Call the user's fn the way the legacy path did: positional content,
        # plus model / response_format kwargs. Most callables accept **kw.
        result = fn(content, model=model_id, response_format=response_format)
        if inspect.iscoroutine(result):
            result = await result
        reply = result if isinstance(result, str) else str(result)

        def _final(text: str) -> AssistantMessage:
            return AssistantMessage(
                content=[TextContent(text=text)],
                api=getattr(model, "api", "completion") or "completion",
                provider=getattr(model, "provider", "callable") or "callable",
                model=model_id,
                stop_reason="stop",
                timestamp=int(time.time() * 1000),
            )

        # Minimal valid event sequence: start → text start/end → done.
        yield EventStart(partial=_final(""))
        yield EventTextStart(content_index=0, partial=_final(""))
        yield EventTextEnd(content_index=0, content=reply, partial=_final(reply))
        yield EventDone(reason="stop", message=_final(reply))

    return _stream


def make_callable_model(fn: Callable[..., Any]):
    """Build a pi-ai ``Model`` that stands in for a ``call=fn`` runtime.

    The model itself carries no provider behaviour — it exists so the
    AgentSession path has an ``api_model`` to construct against. The actual
    call goes through the stream function (:func:`make_callable_stream_fn`),
    which the Runtime threads in.
    """
    from openprogram.providers.types import Model

    return Model(
        id="callable",
        name="callable",
        api="completion",
        provider="callable",
        base_url="",
    )
