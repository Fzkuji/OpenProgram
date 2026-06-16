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

import os
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
                  given, an over-cap node's full text is spilled to a sibling
                  ``large_nodes/`` dir as a plain ``.txt`` file, and the
                  truncation marker cites the exact line range + the
                  ``read()`` call to fetch the elided middle. None → generic
                  char-truncation marker.

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

    large_dir = _large_dir(history_dir)

    messages: list = []
    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None:
            continue
        ts_ms = int((node.created_at or 0) * 1000)
        _nk = f"{node.seq:04d}-{node.id}"

        if node.is_user():
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text",
                                      text=_cap_node_text(_text(node.output), large_dir=large_dir, node_key=_nk))],
                timestamp=ts_ms,
            ))

        elif node.is_llm():
            messages.append(_assistant(
                _cap_node_text(_text(node.output), large_dir=large_dir, node_key=_nk), ts_ms,
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
                ret_text = _cap_node_text(_format_result(node.output), large_dir=large_dir, node_key=_nk)
                messages.append(_assistant(ret_text, ts_ms))

    return messages


# Per-node render cap. render_range controls WHICH nodes are kept (node
# count); this controls how big any ONE node's rendered text may be. Even
# a few nodes can overflow the prompt if ONE carries a huge payload (a
# web_search dump, a giant return value). Set high (32k chars) so normal
# nodes (typically 7-32k) pass through untouched — this only fires on
# abnormally large nodes, never on routine output. Env-overridable.
_NODE_RENDER_CAP = int(os.environ.get("OPENPROGRAM_NODE_RENDER_CAP", "32000"))


_HEAD_LINES = 60   # lines kept from the top of an over-cap node
_TAIL_LINES = 40   # lines kept from the bottom


def _cap_node_text(text: str, cap: int = _NODE_RENDER_CAP,
                   large_dir: "str | None" = None,
                   node_key: "str | None" = None) -> str:
    """Truncate one over-cap node, keeping head+tail LINES, and spill the
    FULL text to a plain ``.txt`` file (one logical line per line) so the
    agent can ``read`` the elided middle by line number.

    Why a spilled .txt and not the node's history JSON: the JSON is a
    single minified line, so ``read``'s offset/limit (line-based, cat -n
    style) can't index into it. We write a real multi-line text file and
    the marker tells the agent the exact line range + the read() call to
    fetch it — standard "text + line numbers" retrieval.

    Falls back to a plain char-truncation marker when no spill dir is
    available (e.g. no session)."""
    if cap <= 0 or len(text) <= cap:
        return text

    lines = text.splitlines()
    # Only spill when we actually have a place to put it AND a key to name it.
    if large_dir and node_key and len(lines) > (_HEAD_LINES + _TAIL_LINES):
        try:
            os.makedirs(large_dir, exist_ok=True)
            path = os.path.join(large_dir, f"{node_key}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            total = len(lines)
            head = "\n".join(lines[:_HEAD_LINES])
            tail = "\n".join(lines[-_TAIL_LINES:])
            mid_start = _HEAD_LINES + 1            # 1-based first elided line
            mid_end = total - _TAIL_LINES          # 1-based last elided line
            n_mid = mid_end - mid_start + 1
            return (
                f"{head}\n\n"
                f"[... lines {mid_start}-{mid_end} ({n_mid:,} lines) of this "
                f"node elided. Full text saved to {path} ({total:,} lines). "
                f"To read the elided middle: "
                f'read("{path}", offset={mid_start}, limit={n_mid}). ...]\n\n'
                f"{tail}"
            )
        except Exception:
            pass

    # Fallback: char-level head/tail, generic pointer.
    head_c = int(cap * 0.6)
    tail_c = cap - head_c
    elided = len(text) - head_c - tail_c
    return (
        text[:head_c]
        + f"\n\n[... {elided:,} chars elided of {len(text):,} total in this "
        f"node; full artifacts are saved as files in the working directory "
        f"— read those for complete content. ...]\n\n"
        + text[-tail_c:]
    )


def _large_dir(history_dir: "str | None") -> "str | None":
    """Sibling ``large_nodes/`` dir next to the session's ``history/`` dir,
    where over-cap node text is spilled as readable .txt. None when no
    session dir is known (spill disabled → char-truncation fallback)."""
    if not history_dir:
        return None
    return os.path.join(os.path.dirname(history_dir.rstrip("/")), "large_nodes")


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
