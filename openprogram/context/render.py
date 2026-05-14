"""DAG → provider messages rendering.

Given a Graph and a list of node ids (typically the output of
:func:`compute_reads`), turn them into a sequence of pi-ai ``Message``
objects the way providers expect.

This is the bridge that lets ``runtime.exec`` build its LLM prompt
straight from the DAG, replacing the legacy tree-Context
``render_messages`` path.

Mapping rules
-------------

    Call(role=user)     →  UserMessage(content)
    Call(role=llm)      →  AssistantMessage(content)
    Call(role=code)     →  pair: UserMessage(call signature) +
                                  AssistantMessage(result)
                          unless metadata.expose == "hidden"
                          (those should already be excluded upstream
                          by compute_reads / dispatcher, but the
                          renderer also defends against them.)

Visibility / hiding semantics live in :func:`compute_reads` — the
renderer is a strict translation pass on whatever ids it gets.
"""

from __future__ import annotations

from typing import Any

from openprogram.context.nodes import Call, Graph


def render_dag_messages(graph: Graph, read_ids: list[str]) -> list:
    """Translate ``read_ids`` into a pi-ai message list.

    Args:
        graph:    the DAG to look up nodes in.
        read_ids: node ids to include, in chronological order (as
                  produced by :func:`compute_reads`).

    Returns:
        list of provider ``Message`` objects (``UserMessage`` /
        ``AssistantMessage``). Unknown ids and ``expose="hidden"``
        code Calls are silently skipped.
    """
    # Local import: providers.types pulls a non-trivial dependency
    # chain (pydantic etc.); keep nodes.py free of it.
    from openprogram.providers.types import (
        UserMessage,
        AssistantMessage,
        TextContent,
    )

    def _assistant(text: str, ts: int, model: str = "") -> AssistantMessage:
        """Build an AssistantMessage with sensible defaults for the
        non-content fields (the renderer doesn't know real api /
        provider / usage — these are reconstructions of history)."""
        return AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text=text)],
            api="messages",        # neutral default; consumers ignore for history reconstruction
            provider="anthropic",  # neutral default
            model=model or "history",
            timestamp=ts,
        )

    messages: list = []
    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None:
            continue
        ts_ms = int((node.created_at or 0) * 1000)

        if node.is_user():
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text",
                                      text=_text(node.output))],
                timestamp=ts_ms,
            ))

        elif node.is_llm():
            messages.append(_assistant(
                _text(node.output), ts_ms, model=node.name or "",
            ))

        elif node.is_code():
            expose = (node.metadata or {}).get("expose") or "io"
            if expose == "hidden":
                continue
            # Render as a user→assistant pair: the call signature
            # asks the assistant to "respond", and the return value
            # is that response. Matches the tree-Context
            # render_messages convention so legacy provider prompts
            # see the same shape.
            call_text = _format_call_signature(node)
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text", text=call_text)],
                timestamp=ts_ms,
            ))
            if node.output is not None:
                ret_text = _format_result(node.output)
                messages.append(_assistant(ret_text, ts_ms))

    return messages


def _text(value: Any) -> str:
    """Make sure whatever we put into TextContent is a ``str``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _format_call_signature(node: Call) -> str:
    """Turn a code Call into a human-readable "function(args)" string."""
    name = node.name or "<unnamed>"
    args = node.input
    if isinstance(args, dict):
        try:
            import json as _json
            args_str = _json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args_str = repr(args)
    elif args is None:
        args_str = ""
    else:
        args_str = repr(args)
    return f"{name}({args_str})"


def _format_result(value: Any) -> str:
    """Stringify a code Call's return value for the assistant turn."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value.keys()) == {"error"}:
        return f"[error] {value['error']}"
    try:
        import json as _json
        return _json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


__all__ = ["render_dag_messages"]
