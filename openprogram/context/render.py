"""DAG → provider messages rendering.

Given a Graph and a list of node ids (typically the output of
:func:`render_context`), turn them into a sequence of pi-ai ``Message``
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
                          by render_context / dispatcher, but the
                          renderer also defends against them.)

Visibility / hiding semantics live in :func:`render_context` — the
renderer is a strict translation pass on whatever ids it gets.
"""

from __future__ import annotations

import os
from typing import Any

from openprogram.context.nodes import Call, Graph


def _aged_code_ids(graph: Graph, read_ids: list[str]) -> set[str]:
    """Which code nodes in ``read_ids`` should render as an aged stub.

    Mirrors tool_aging's policy on the DAG: the last ``TAIL_TURNS`` llm
    nodes keep full fidelity; code nodes that occur before that tail
    window collapse to a one-line stub to save tokens (protected tools
    like todo_read are never aged). This is a pre-pass over read_ids —
    the renderer stays a strict translation; aging policy lives here, not
    baked into the per-node emit. Keyed only on data DAG nodes already
    carry (role/seq/name), so no storage change is needed.
    """
    try:
        from openprogram.context.tool_aging.policy import (
            TAIL_TURNS, PRUNE_PROTECTED_TOOLS,
        )
    except Exception:
        return set()
    nodes = [graph.nodes.get(nid) for nid in read_ids]
    nodes = [n for n in nodes if n is not None]
    llm_seqs = sorted(n.seq for n in nodes if n.is_llm())
    if len(llm_seqs) <= TAIL_TURNS:
        return set()  # whole conversation fits in the tail window
    # Boundary: code nodes with seq < the TAIL_TURNS-th-from-last llm seq
    # are "old" and get aged.
    tail_cutoff_seq = llm_seqs[-TAIL_TURNS]
    aged: set[str] = set()
    for n in nodes:
        if not n.is_code():
            continue
        if n.seq >= tail_cutoff_seq:
            continue
        if (n.name or "") in PRUNE_PROTECTED_TOOLS:
            continue
        aged.add(n.id)
    return aged


def render_dag_messages(graph: Graph, read_ids: list[str],
                        history_dir: "str | None" = None) -> list:
    """Translate ``read_ids`` into a pi-ai message list.

    Args:
        graph:    the DAG to look up nodes in.
        read_ids: node ids to include, in chronological order (as
                  produced by :func:`render_context`).
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
        ToolCall,
        ToolResultMessage,
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

    aged_ids = _aged_code_ids(graph, read_ids)

    def _result_text_for(node: Call) -> str:
        """Rendered text of a code node's result, aged to a stub when the
        node is outside the tail window (saves tokens on long histories)."""
        if node.id in aged_ids:
            from openprogram.context.tool_aging.summarize import summarize_tool_call
            return summarize_tool_call(
                node.name or "", node.input,
                node.output, bool((node.metadata or {}).get("is_error")),
            )
        return _cap_node_text(_format_result(node.output),
                              large_dir=large_dir, node_key=f"{node.seq:04d}-{node.id}")

    messages: list = []
    # The most recent AssistantMessage emitted — a model-tool_use code
    # node appends its ToolCall here (the call must live INSIDE the
    # assistant turn that emitted it, then a ToolResultMessage follows).
    last_assistant: "AssistantMessage | None" = None
    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None:
            continue
        ts_ms = int((node.created_at or 0) * 1000)
        _nk = f"{node.seq:04d}-{node.id}"

        if node.is_user():
            last_assistant = None
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text",
                                      text=_cap_node_text(_text(node.output), large_dir=large_dir, node_key=_nk))],
                timestamp=ts_ms,
            ))

        elif node.is_llm():
            am = _assistant(
                _cap_node_text(_text(node.output), large_dir=large_dir, node_key=_nk), ts_ms,
                model=node.name or "",
            )
            last_assistant = am
            messages.append(am)

        elif node.is_code():
            md = node.metadata or {}
            expose = md.get("expose") or "io"
            if expose == "hidden":
                continue
            tool_call_id = md.get("tool_call_id")
            if tool_call_id:
                # Model-emitted tool_use: round-trip as a real
                # ToolCall (inside the owning assistant turn) + a
                # ToolResultMessage. Providers reject an orphaned
                # tool_use/tool_result, so the ToolCall must attach to
                # an AssistantMessage. If none precedes (e.g. reads
                # started mid-turn), synthesize an empty one.
                if last_assistant is None:
                    last_assistant = _assistant("", ts_ms)
                    messages.append(last_assistant)
                last_assistant.content.append(ToolCall(
                    id=tool_call_id,
                    name=node.name or "",
                    arguments=node.input if isinstance(node.input, dict) else {},
                ))
                result_text = _result_text_for(node) if node.output is not None else ""
                messages.append(ToolResultMessage(
                    tool_call_id=tool_call_id,
                    tool_name=node.name or "",
                    content=[TextContent(type="text", text=result_text)],
                    timestamp=ts_ms,
                ))
                continue
            # Direct @agentic_function (no tool_call_id): render as a
            # user→assistant text pair (the legacy convention).
            last_assistant = None
            call_text = _format_call_signature(node)
            doc = md.get("doc")
            if doc:
                call_text = f"{doc}\n\n{call_text}"
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text", text=call_text)],
                timestamp=ts_ms,
            ))
            if node.output is not None:
                messages.append(_assistant(_result_text_for(node), ts_ms))

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
