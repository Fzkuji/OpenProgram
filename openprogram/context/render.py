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


def render_dag_messages(graph: Graph, read_ids: list[str],
                        history_dir: "str | None" = None) -> list:
    """Translate ``read_ids`` into a pi-ai message list.

    Args:
        graph:    the DAG to look up nodes in.
        read_ids: node ids to include, in chronological order (as
                  produced by :func:`compute_reads`).
        history_dir: absolute path to the session's ``history/`` dir. When
                  given, an over-cap node's truncation marker cites the
                  exact node file (``<seq>-<role>-<id>.json``) so the agent
                  can ``read`` the full content. None → generic marker.

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
        _nf = _node_file(history_dir, node)

        if node.is_user():
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text",
                                      text=_cap_node_text(_text(node.output), node_file=_nf))],
                timestamp=ts_ms,
            ))

        elif node.is_llm():
            messages.append(_assistant(
                _cap_node_text(_text(node.output), node_file=_nf), ts_ms,
                model=node.name or "",
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
            # The function's docstring (stored on the node at entry)
            # travels into the rendered context so the model sees what
            # the function does, not just its name(args).
            doc = (node.metadata or {}).get("doc")
            if doc:
                call_text = f"{doc}\n\n{call_text}"
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text", text=call_text)],
                timestamp=ts_ms,
            ))
            if node.output is not None:
                ret_text = _cap_node_text(_format_result(node.output), node_file=_nf)
                messages.append(_assistant(ret_text, ts_ms))

    return messages


import os

# Per-node render cap. render_range controls WHICH nodes are kept (node
# count); this controls how big any ONE node's rendered text may be. Even
# a few nodes can overflow the prompt if ONE carries a huge payload (a
# web_search dump, a giant return value). Set high (32k chars) so normal
# nodes (typically 7-32k) pass through untouched — this only fires on
# abnormally large nodes, never on routine output. Env-overridable.
_NODE_RENDER_CAP = int(os.environ.get("OPENPROGRAM_NODE_RENDER_CAP", "32000"))


def _cap_node_text(text: str, cap: int = _NODE_RENDER_CAP,
                   node_file: "str | None" = None) -> str:
    """Truncate one node's rendered text to ``cap`` chars, keeping head+tail
    with an elision marker. Only fires on abnormally large nodes.

    The full node content always lives on disk in the session history file.
    When ``node_file`` is known, the marker cites the exact path so the
    agent can ``read`` it back — never a dead pointer."""
    if cap <= 0 or len(text) <= cap:
        return text
    head = int(cap * 0.6)
    tail = cap - head
    elided = len(text) - head - tail
    if node_file:
        where = (f"the full content of this node is at {node_file} — use the "
                 f"`read` tool to fetch it")
    else:
        where = ("the full artifacts this step produced are saved as files "
                 "in the working directory — read those for complete content")
    return (
        text[:head]
        + f"\n\n[... {elided:,} chars elided of {len(text):,} total in this "
        f"node. This is only the truncated in-context view; {where}. ...]\n\n"
        + text[-tail:]
    )


def _node_file(history_dir: "str | None", node) -> "str | None":
    """Absolute path to a node's history JSON, matching git_session's
    naming (``<seq:04d>-<role_letter>-<id>.json``). None when unknown."""
    if not history_dir:
        return None
    role_letter = (getattr(node, "role", None) or "x")[0]
    return f"{history_dir}/{node.seq:04d}-{role_letter}-{node.id}.json"


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
