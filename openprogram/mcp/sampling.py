"""Sampling — host serves an LLM endpoint to MCP servers.

MCP's "sampling" capability lets a server ask the host to run an LLM
call on its behalf (``sampling/createMessage``). The server provides
messages + a system prompt; the host picks a model, runs inference,
and returns the assistant message. Useful when an MCP server wants to
do semantic processing but doesn't want to bring its own LLM key.

Spec mapping into OpenProgram:

  * Model selection: we use the host's default agent's configured
    model. Servers can pass ``modelPreferences`` (hints / costPriority
    / speedPriority); for v1 we ignore them — picking models per-call
    is a deeper integration than this layer is worth.
  * Multi-turn messages: the spec allows a full conversation array.
    We feed it to :class:`Runtime` as a single content list of
    text blocks tagged with role labels in front (``[user]:`` /
    ``[assistant]:``), since Runtime.exec is a one-shot call rather
    than a chat-completion loop.
  * Images / audio / tool content: only text is forwarded; other
    content types are skipped with a placeholder note. Most sampling
    requests are text-only anyway, and adding image+audio is a
    separate piece of work involving Context image refs.
  * No default model configured → ``ErrorData`` so the server gets a
    clean "host doesn't support sampling" signal (matches the SDK's
    default behaviour, just routed through our codepath).
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Optional


def _read_default_model() -> Optional[tuple[str, str]]:
    """Return ``(provider, model_id)`` from the user's default agent."""
    from openprogram.memory.llm_bridge import _read_default_model as _r
    return _r()


def _extract_text(content: Any) -> str:
    """Flatten an MCP SamplingMessage.content into a plain-text string.

    SamplingMessage.content is a union of TextContent / ImageContent /
    AudioContent / ToolUseContent / ToolResultContent — or a list of
    those. Non-text blocks are replaced with a marker so the LLM at
    least knows something was elided.
    """
    if content is None:
        return ""
    blocks = content if isinstance(content, list) else [content]
    parts: list[str] = []
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "text":
            parts.append(getattr(block, "text", "") or "")
        elif kind == "image":
            parts.append("[image omitted — sampling text-only for now]")
        elif kind == "audio":
            parts.append("[audio omitted — sampling text-only for now]")
        else:
            # Tool use / tool result — these surface in
            # context-included sampling. Render their textual repr so
            # the LLM at least has a hint.
            text = (getattr(block, "text", None)
                    or repr(block.model_dump() if hasattr(block, "model_dump") else block))
            parts.append(text)
    return "\n".join(p for p in parts if p)


def _flatten_messages(messages: list[Any]) -> str:
    """Render a multi-turn message list as a single annotated text
    block. Last user message is the "current" question; prior turns
    become labelled history above it.

    Returns text suitable for Runtime.exec via ``[{"type":"text",
    "text": ...}]``.
    """
    if not messages:
        return ""
    if len(messages) == 1:
        return _extract_text(messages[0].content)
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "role", "user")
        body = _extract_text(m.content)
        lines.append(f"[{role}]\n{body}")
    return "\n\n".join(lines)


async def sampling_callback(context, params) -> Any:  # noqa: ANN001
    """``sampling_callback`` for ``mcp.ClientSession``.

    Returns either :class:`CreateMessageResult` on success or
    :class:`ErrorData` so the server gets a clean JSON-RPC error
    instead of a transport-level exception when something goes wrong
    on our end (no model configured, runtime fails, etc.).
    """
    from mcp import types as _mcp_types

    pair = _read_default_model()
    if pair is None:
        return _mcp_types.ErrorData(
            code=_mcp_types.INVALID_REQUEST,
            message=("Sampling not configured: no default agent / model "
                     "set on this OpenProgram host."),
        )
    provider, model_id = pair
    model_uri = f"{provider}:{model_id}"

    try:
        text = _flatten_messages(list(params.messages))
        if not text.strip():
            return _mcp_types.ErrorData(
                code=_mcp_types.INVALID_PARAMS,
                message="Empty messages list",
            )

        from openprogram.agentic_programming.runtime import Runtime
        rt = Runtime(model=model_uri)
        if params.systemPrompt:
            rt.system = params.systemPrompt

        # Runtime.exec is sync; punt to a thread so the asyncio loop
        # serving the MCP session stays responsive.
        def _do_call() -> str:
            return str(rt.exec(
                content=[{"type": "text", "text": text}],
                max_iterations=1,
            ))

        try:
            reply = await asyncio.to_thread(_do_call)
        finally:
            try:
                rt.close()
            except Exception:  # noqa: BLE001
                pass

        return _mcp_types.CreateMessageResult(
            role="assistant",
            content=_mcp_types.TextContent(type="text", text=reply),
            model=model_uri,
            stopReason="endTurn",
        )
    except Exception as e:  # noqa: BLE001
        print(f"[mcp][sampling] inference failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return _mcp_types.ErrorData(
            code=_mcp_types.INTERNAL_ERROR,
            message=f"Sampling inference failed: {type(e).__name__}",
        )
