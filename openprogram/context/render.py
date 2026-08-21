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

from typing import Any

from openprogram.context.nodes import Call, Graph


def _aged_code_ids(graph: Graph, read_ids: list[str],
                   manifest: "dict | None" = None) -> "tuple[set[str], int]":
    """``(ids to render as aged stubs, the boundary used)``.

    The last ``TAIL_TURNS`` llm nodes keep full fidelity; code nodes
    before that window collapse to a one-line stub (protected tools like
    todo_list are never aged). This is a pre-pass over read_ids — the
    renderer stays a strict translation, aging policy lives here.

    The boundary comes from :mod:`openprogram.context.aging`, which
    ratchets it per turn so two renders inside one turn agree. When a
    ``manifest`` is supplied its recorded ``aged_before_seq`` wins over
    live policy — that's what makes a historical render replayable byte
    for byte after the policy constants change.
    """
    try:
        from openprogram.context.tool_aging import policy
        from openprogram.context.aging import aged_before_seq, manifest_boundary
    except Exception:
        return set(), -1
    # Read the flag at call time (not import time) so tests and ablation
    # runs can flip it via monkeypatch without reimporting the module.
    if not policy.AGING_ENABLED:
        return set(), -1
    nodes = [graph.nodes.get(nid) for nid in read_ids]
    nodes = [n for n in nodes if n is not None]

    boundary = manifest_boundary(manifest)
    if boundary is None:
        boundary = aged_before_seq(nodes)
    if boundary < 0:
        return set(), boundary  # whole conversation fits in the tail window

    aged: set[str] = set()
    for n in nodes:
        if not n.is_code():
            continue
        if n.seq >= boundary:
            continue
        if (n.name or "") in policy.PRUNE_PROTECTED_TOOLS:
            continue
        aged.add(n.id)
    return aged, boundary


def render_dag_messages(graph: Graph, read_ids: list[str],
                        history_dir: "str | None" = None,
                        manifest: "dict | None" = None) -> list:
    """Translate ``read_ids`` into a pi-ai message list.

    Pure read path: it never writes. An over-cap node was already spilled
    to ``large_nodes/`` when it was recorded (see
    :func:`openprogram.context.spill.spill_if_large`) and carries the
    path in ``metadata.spilled``; the renderer only cites it.

    Args:
        graph:    the DAG to look up nodes in.
        read_ids: node ids to include, in chronological order (as
                  produced by :func:`render_context`).
        history_dir: unused for spilling now; retained because callers
                  pass it and it still identifies the session on disk.
        manifest: a previously recorded ``render_manifest``. When given,
                  its ``aged_before_seq`` overrides live aging policy so
                  the historical render is reproduced exactly.

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

    aged_ids, _boundary = _aged_code_ids(graph, read_ids, manifest)
    _spilled = [
        (graph.nodes[nid].metadata or {})["spilled"].get("path", "")
        for nid in read_ids
        if nid in graph.nodes
        and isinstance((graph.nodes[nid].metadata or {}).get("spilled"), dict)
    ]
    if manifest is None:
        # Record what THIS render did, so the llm node about to be closed
        # can stamp it and a later replay can reproduce these bytes.
        from openprogram.context.aging import build_manifest, publish_manifest
        publish_manifest(build_manifest(_boundary, _spilled))

    def _result_text_for(node: Call) -> str:
        """Rendered text of a code node's result, aged to a stub when the
        node is outside the tail window (saves tokens on long histories)."""
        if node.id in aged_ids:
            from openprogram.context.tool_aging.summarize import summarize_tool_call
            return summarize_tool_call(
                node.name or "", node.input,
                node.output, bool((node.metadata or {}).get("is_error")),
            )
        return _elide(_format_result(node.output), node)

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

        if node.is_user():
            last_assistant = None
            from openprogram.agent.authority import render_model_input_from
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text",
                                      text=render_model_input_from(
                                          node.metadata or {},
                                          _elide(_text(node.output), node),
                                      ))],
                timestamp=ts_ms,
            ))

        elif node.is_llm():
            am = _assistant(
                _elide(_text(node.output), node), ts_ms,
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
            ended = _ended_task_note(md)
            if ended:
                call_text = f"{ended}\n{call_text}"
            messages.append(UserMessage(
                role="user",
                content=[TextContent(type="text", text=call_text)],
                timestamp=ts_ms,
            ))
            if node.output is not None:
                messages.append(_assistant(_result_text_for(node), ts_ms))
            elif ended:
                messages.append(_assistant(ended, ts_ms))

    return messages


def _elide(text: str, node: Call) -> str:
    """Shrink one over-cap node's text for the prompt. Read-only.

    The spill file was written when the node was recorded; here we only
    read ``metadata.spilled`` to cite it. A node with no spill entry is
    under the cap and passes through untouched.
    """
    from openprogram.context.spill import elide_spilled
    return elide_spilled(text, (node.metadata or {}).get("spilled"))


def _text(value: Any) -> str:
    """Make sure whatever we put into TextContent is a ``str``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _ended_task_note(md: dict) -> str:
    """Label a failed/cancelled program Call so the model does not resume it."""
    status = (md.get("status") or "").strip()
    if status == "error":
        return (
            "[This program task ended in error. Do not continue or retry it "
            "unless the user explicitly asks.]"
        )
    if status == "cancelled":
        return (
            "[This program task was cancelled. Do not continue or retry it "
            "unless the user explicitly asks.]"
        )
    return ""


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
