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

A finished turn renders the way its own requests sent it, so the
provider's cached prefix still matches when the next turn starts:

* a user message carries the blocks its turn put in front of it (the
  recalled memory, the run note), stored on its node;
* a reply goes back one provider response at a time, as the turn sent it:
  that response's reasoning, text and calls in one assistant message,
  then the results of those calls. Reasoning goes back, to the model that
  wrote it only, as the provider's own signed item (a Responses reasoning
  item with its encrypted content); text goes under the message id the
  provider gave it. The reply's
  ordered blocks mark where each response began (``round_start``); a reply recorded without the marks
  sends its calls one per message, each with its result, and its text last;
* the reply is the model's own (its provider, api and model), so the
  provider keeps the ids it issued.
"""

from __future__ import annotations

from typing import Any

from openprogram.runtime.context.session_dag.nodes import summary_covers_ids
from openprogram.state.store.session.nodes import Call, Graph


def latest_media_id(graph: Graph, read_ids: list[str]) -> "str | None":
    """The last user message in ``read_ids`` that carries media: the only
    one whose media is attached; earlier ones name their files."""
    return next((nid for nid in reversed(list(read_ids))
                 if (n := graph.nodes.get(nid)) is not None and n.is_user()
                 and (n.metadata or {}).get("media")), None)


_WHOLE_READ = object()


def render_dag_messages(graph: Graph, read_ids: list[str],
                        session_directory: "str | None" = None,
                        reasoning_for: Any = None,
                        audience: str = "own_prompt",
                        latest_media: Any = _WHOLE_READ) -> list:
    """Translate ``read_ids`` into a pi-ai message list.

    Pure read path: it never writes. An over-cap node was already spilled
    to ``large_nodes/`` when it was recorded (see
    :func:`openprogram.state.store.session.files.spill.spill_if_large`) and carries the
    path in ``metadata.spilled``; the renderer only cites it.

    Args:
        graph:    the DAG to look up nodes in.
        read_ids: node ids to include, in chronological order (as
                  produced by :func:`render_context`).
        session_directory: the session's directory on disk, when the
                  caller knows it: kept media are read from its ``media/``
                  (``store.session.media``), else from the path each
                  record names. Spilling does not read it.
        reasoning_for: the model the messages are sent to. A reply this
                  model wrote goes back with its reasoning; any other
                  reply, and every reply when this is None (a summary
                  source, a token estimate), goes back without it.
                  Another model cannot use a reasoning item and would
                  read its summary as text the assistant said.
        audience: who the request is built for (``context.prompt_assembly.turn_context``):
                  a conversation turn's own request shows the section
                  notes stored beside earlier messages; any call with a
                  system prompt of its own (the default) does not.

    Returns:
        list of provider ``Message`` objects (``UserMessage`` /
        ``AssistantMessage``). Unknown ids and ``expose="hidden"``
        code Calls are silently skipped.
    """
    # Local import: providers.types pulls a non-trivial dependency
    # chain (pydantic etc.); keep nodes.py free of it.
    from openprogram.models.providers.types import (
        UserMessage,
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
    )

    reasoning_identity = (
        getattr(reasoning_for, "api", None),
        getattr(reasoning_for, "provider", None),
        getattr(reasoning_for, "id", None),
    )

    def _assistant(text: str, ts: int, model: str = "",
                   identity: "tuple[str, str, str] | None" = None) -> AssistantMessage:
        """An AssistantMessage of history. ``identity`` is the reply's own
        ``(api, provider, model)`` when it is known; otherwise neutral
        defaults, which every provider treats as another model's turn."""
        api, provider, model_id = identity or ("messages", "anthropic", model or "history")
        return AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text=text)] if text is not None else [],
            api=api,
            provider=provider,
            model=model_id,
            timestamp=ts,
        )

    def _result_text_for(node: Call) -> str:
        """Rendered text of a code node's result: the text the turn sent.
        Old results are never shortened here; only compaction replaces
        them (``docs/reference/design/context/overview.html``)."""
        return _elide(_format_result(node.output), node)

    def _tool_call(node: Call) -> ToolCall:
        return ToolCall(id=(node.metadata or {})["tool_call_id"], name=node.name or "",
                        arguments=_call_arguments(node.input))

    def _tool_result(node: Call, ts: int) -> ToolResultMessage:
        # The node keeps the result's durable details (its load record,
        # ``shared.tool_loads``) beside its text; the message carries them
        # back, so a request built from history matches the one built in
        # the turn that ran the tool.
        result_text = _result_text_for(node) if node.output is not None else ""
        media = (node.metadata or {}).get("media")
        # A result of media alone had no text block in its turn.
        text_blocks = [] if (media and not result_text) else [TextContent(type="text", text=result_text)]
        from openprogram.runtime.context.session_dag.media_content import with_media
        return ToolResultMessage(
            tool_call_id=(node.metadata or {})["tool_call_id"],
            tool_name=node.name or "",
            content=with_media(text_blocks, media, session_directory),
            details=(node.metadata or {}).get("details"),
            is_error=bool((node.metadata or {}).get("is_error")),
            timestamp=ts,
        )

    # The calls each reply made, by id, for sending them back inside the
    # response that made them; a node sent that way is not sent again.
    calls_by_reply = _model_calls_by_reply(graph, read_ids)
    sent_in_round: set[str] = set()

    messages: list = []
    # The reply being rendered: its text and any imported calls wait here
    # until its own tool calls (the code nodes it called) are out, because
    # in the turn the text came after them. ``reply_identity`` is that
    # reply's (api, provider, model), which its tool calls share.
    last_assistant: "AssistantMessage | None" = None
    reply_identity: "tuple[str, str, str] | None" = None

    def _flush_reply() -> None:
        nonlocal last_assistant, reply_identity
        if last_assistant is not None and any(
            getattr(block, "type", None) != "text" or getattr(block, "text", "")
            for block in last_assistant.content
        ):
            messages.append(last_assistant)
        last_assistant = None
        reply_identity = None

    # Only the latest message with media attaches it; earlier ones name
    # their files (``media.as_references``). A caller rendering one part of
    # a chain names the chain's latest (``latest_media_id``).
    if latest_media is _WHOLE_READ:
        latest_media = latest_media_id(graph, read_ids)

    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None:
            continue
        ts_ms = int((node.created_at or 0) * 1000)

        if node.is_user():
            # ROOT is structural, not a user turn. Historical empty rows must
            # not become empty user messages (or authority-only messages).
            # A message of pictures alone is a turn too.
            user_media = (node.metadata or {}).get("media")
            if node.id == "ROOT" or (not _text(node.output).strip() and not user_media):
                continue
            _flush_reply()
            from openprogram.state.authority import render_model_input_from
            # ``model_input`` is what the model read for this turn when it
            # differs from the visible message (a ``/skill`` expansion).
            model_input = (node.metadata or {}).get("model_input")
            raw_text = _text(model_input or node.output)
            text = render_model_input_from(node.metadata or {}, _elide(raw_text, node))
            text = with_turn_block(text, _stored_turn_block(node, audience))
            from openprogram.runtime.context.session_dag.media_content import as_references, with_media
            # A turn with no text sent its pictures alone, with no text block.
            text_blocks = ([] if user_media and not raw_text.strip()
                           else [TextContent(type="text", text=text)])
            content = (with_media(text_blocks, user_media, session_directory)
                       if node.id == latest_media
                       else as_references(text_blocks, user_media, session_directory))
            if not content:
                continue
            messages.append(UserMessage(
                role="user",
                # The images the person sent go back where the turn put them.
                content=content,
                timestamp=ts_ms,
            ))

        elif node.is_llm() and (node.metadata or {}).get("function") == "attach":
            # A sub-agent's report, stored on the caller's line. It is not
            # the caller's own reply, so it reaches the model as input that
            # names its sender and where its full run can be read.
            report = _attach_report(node)
            if report:
                _flush_reply()
                messages.append(UserMessage(
                    role="user",
                    content=[TextContent(type="text", text=report)],
                    timestamp=ts_ms,
                ))

        elif node.is_llm() and summary_covers_ids(node) is not None:
            # A compaction summary stands where the turns it covers began.
            # One written as user input (``render_role``) reads as context
            # given to the model, with its prefix; an older one stays the
            # assistant message it was. Either carries the tools its turns
            # loaded, so the request still loads them (``shared.tool_loads``),
            # and the working state the compaction restored after its text.
            _flush_reply()
            from openprogram.runtime.context.compaction.carried_tools import carried_tools
            carried = carried_tools(graph, node) or None
            text = _elide(_text(node.output), node)
            restored = (node.metadata or {}).get("restored_state")
            if isinstance(restored, str) and restored.strip():
                text = f"{text}\n\n{restored}"
            if (node.metadata or {}).get("render_role") == "user":
                messages.append(UserMessage(
                    role="user", content=[TextContent(type="text", text=text)],
                    carried_tools=carried, timestamp=ts_ms))
            else:
                last_assistant = _assistant(text, ts_ms, model=node.name or "",
                                            identity=_reply_identity(node))
                last_assistant.carried_tools = carried

        elif node.is_llm():
            _flush_reply()
            request_content = (node.metadata or {}).get("request_content")
            if request_content is not None:
                messages.append(UserMessage.model_validate(request_content))
            reply_identity = _reply_identity(node)
            own_reasoning = reply_identity is not None and reply_identity == reasoning_identity
            rounds = _reply_rounds(node)
            if rounds is not None:
                calls = calls_by_reply.get(node.id, {})
                for blocks in rounds:
                    response = _assistant(None, ts_ms, identity=reply_identity)
                    called: list[Call] = []
                    for block in blocks:
                        if block["type"] == "text":
                            response.content.append(TextContent(
                                type="text", text=block["text"], text_signature=block.get("signature")))
                            continue
                        if block["type"] == "thinking":
                            if not own_reasoning:
                                continue
                            response.content.append(ThinkingContent(
                                thinking=block.get("text") or "", thinking_signature=block["signature"],
                                redacted=True if block.get("redacted") else None))
                            continue
                        code = calls.get(str(block.get("tool_call_id") or ""))
                        if code is None or code.id in sent_in_round:
                            # No result to pair it with (an Agent method
                            # call renders as its own row): leave it out.
                            continue
                        response.content.append(_tool_call(code))
                        called.append(code)
                        sent_in_round.add(code.id)
                    if any(not isinstance(item, ThinkingContent) for item in response.content):
                        # Reasoning alone answers nothing; its response
                        # left nothing else to send.
                        messages.append(response)
                    messages.extend(_tool_result(code, int((code.created_at or 0) * 1000))
                                    for code in called)
                continue
            last_assistant = _assistant(
                _elide(_text(node.output), node), ts_ms,
                model=node.name or "", identity=reply_identity,
            )

        elif node.is_code():
            if node.id in sent_in_round:
                continue
            md = node.metadata or {}
            expose = md.get("expose") or "io"
            if expose == "hidden":
                continue
            if md.get("provider_executed"):
                # A search the provider ran (serverToolUse). The reply's own
                # block carries it; a local ToolCall here would be orphaned.
                continue
            tool_call_id = md.get("tool_call_id")
            imported = md.get("imported_from")
            if imported:
                # A call made by another agent before the conversation was
                # imported (store/session/external_import). Its tool does
                # not exist here, so a structured ToolCall would invite the
                # model to call it again; replay it as part of the reply.
                if last_assistant is None:
                    last_assistant = _assistant("", ts_ms)
                agent = imported.get("agent") if isinstance(imported, dict) else ""
                text = f"[{agent} tool call] {_format_call_signature(node)}"
                if node.output is not None:
                    text += f"\n[result]\n{_result_text_for(node)}"
                last_assistant.content.append(TextContent(type="text", text=text))
                continue
            if tool_call_id:
                # A model call whose reply has no response marks: a real
                # ToolCall in an assistant message of its own + a
                # ToolResultMessage. Providers reject an orphaned
                # tool_use/tool_result, so the ToolCall always has an
                # assistant message; the reply's text follows its calls
                # (``_flush_reply``).
                call_message = _assistant(None, ts_ms, identity=reply_identity)
                call_message.content.append(_tool_call(node))
                messages.append(call_message)
                messages.append(_tool_result(node, ts_ms))
                continue
            # Direct Agent method (no tool_call_id): render as a
            # user→assistant text pair (the legacy convention).
            _flush_reply()
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

    _flush_reply()
    return messages


def turn_block(memory_prefetch: Any, run_note: Any, current_date: Any = None,
               memory_map: Any = None) -> str:
    """The block a turn puts in front of its live user message: the date it
    was sent on, its notes (a rewind, the sections that changed:
    ``context.prompt_assembly.turn_context.notes_text``), the memory it recalled, then its
    run note. Later renders of that message join its ``turn_context``
    entries in the same order (``turn_context.block``), so the bytes match."""
    parts = (current_date, memory_map, memory_prefetch, run_note)
    return "\n\n".join(part for part in parts if isinstance(part, str) and part)


def with_turn_block(text: str, block: str) -> str:
    """``text`` with ``block`` in front, exactly as the turn's requests sent it."""
    return f"{block.rstrip()}\n\n{text}" if block else text


def _stored_turn_block(node: Call, audience: str = "own_prompt") -> str:
    """The block a past turn sent before its user message, from its node's
    ``turn_context``, as a request for ``audience`` shows it."""
    from openprogram.runtime.context.prompt_assembly.turn_context import block

    return block(node.metadata or {}, audience)


def _reply_identity(node: Call) -> "tuple[str, str, str] | None":
    """``(api, provider, model)`` of the model that wrote this reply.

    The reply node records its provider and model; the api is the one that
    model is served through. Unknown (an old node, a model no longer
    listed) means the neutral history identity.
    """
    md = node.metadata or {}
    provider = md.get("provider")
    model_id = md.get("model") or node.name
    if not isinstance(provider, str) or not provider or not isinstance(model_id, str) or not model_id:
        return None
    try:
        from openprogram.models.providers.catalog.models import get_model

        model = get_model(provider, model_id)
    except Exception:
        return None
    api = getattr(model, "api", None)
    if not isinstance(api, str) or not api:
        return None
    return api, provider, model_id


def _model_calls_by_reply(graph: Graph, read_ids: list[str]) -> "dict[str, dict[str, Call]]":
    """``{reply id: {tool call id: code node}}`` for the calls a model made
    and this render sends as ToolCalls (not hidden, not run by the provider,
    not imported from another agent)."""
    calls: dict[str, dict[str, Call]] = {}
    for nid in read_ids:
        node = graph.nodes.get(nid)
        if node is None or not node.is_code() or not node.caller:
            continue
        md = node.metadata or {}
        if (not md.get("tool_call_id") or (md.get("expose") or "io") == "hidden"
                or md.get("provider_executed") or md.get("imported_from")):
            continue
        calls.setdefault(node.caller, {}).setdefault(str(md["tool_call_id"]), node)
    return calls


def _reply_rounds(node: Call) -> "list[list[dict]] | None":
    """A reply's signed reasoning, text and call blocks, split into the
    provider responses that made them, or None when the reply does not record where each began.

    The turn marks the first block of each response ``round_start``
    (``turn_runtime.loop_runner``). A reply recorded before the marks, one
    whose first block is unmarked, or one whose text was spilled (the text
    to send is then the elided output) is rendered from its output instead.
    """
    md = node.metadata or {}
    if md.get("spilled"):
        return None
    extra = md.get("extra")
    if isinstance(extra, str):
        try:
            import json as _json

            extra = _json.loads(extra)
        except (TypeError, ValueError):
            return None
    blocks = extra.get("blocks") if isinstance(extra, dict) else None
    if not isinstance(blocks, list) or not blocks:
        return None
    if not isinstance(blocks[0], dict) or not blocks[0].get("round_start"):
        return None
    rounds: list[list[dict]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("round_start"):
            rounds.append([])
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str) and block["text"]:
            rounds[-1].append(block)
        elif kind == "thinking" and isinstance(block.get("signature"), str) and block["signature"]:
            # A signed block: a Responses reasoning item, an Anthropic
            # thinking block, a signed Gemini thought part. An unsigned one
            # stays out; a Gemini thought part without a signature was sent
            # inside its turn, so the next turn's request differs there.
            rounds[-1].append(block)
        elif kind == "tool" and not block.get("provider_executed"):
            rounds[-1].append(block)
    return rounds


def _call_arguments(value: Any) -> dict:
    """A recorded call's arguments as the dict the model sent.

    A tool node stores them as the JSON text the turn sent; parsing it gives
    back the same keys in the same order, so they serialize to the same bytes.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            import json as _json

            parsed = _json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_SETTLED = ("completed", "errored", "failed", "cancelled", "canceled")


def _attach_report(node: Call) -> str:
    """The text a sub-agent's attach node shows the caller, or "" for none.

    A synchronous spawn already returned the reply as the agent tool's
    result, which carries the branch address, so its node adds nothing.

    A card written while its helper runs (``result_in="notice"``, set with
    its ``job_id``) renders only what it was written with: its status, reply
    and usage change as the helper works, and the caller's prompt only grows
    by appending, so they stay on the node for the interface and the result
    reaches the caller as the notice turn (``jobs/notices.py``). A card
    without the mark (written after its helper ended, or before the notice
    carried results) carries the reply, so older conversations keep theirs.
    """
    md = node.metadata or {}
    attach = md.get("attach") if isinstance(md.get("attach"), dict) else {}
    if attach.get("delivery") == "tool_result":
        return ""
    label = str(attach.get("label") or "").strip()
    task = str(attach.get("prompt") or "").strip()
    job_id = str(attach.get("job_id") or "").strip()
    if job_id and attach.get("result_in") == "notice":
        lines = [f'[Sub-agent "{label}" started]' if label else "[A sub-agent started]"]
        if task:
            lines.append(f"Task: {task[:500]}")
        lines.append("Its result comes back to you as a notice when it ends. Its steps: "
                     f'read_conversation(session_id="{job_id}")')
        return "\n".join(lines)
    lines = [f'[Report from sub-agent "{label}"]' if label else "[Report from a sub-agent]"]
    if task:
        lines.append(f"Task: {task[:500]}")
    status = str(attach.get("status") or "").strip()
    content = _elide(_text(node.output), node).strip()
    if status in _SETTLED or content:
        lines.append(f"Final reply ({status}):" if status else "Final reply:")
        lines.append(content or "(no output)")
    else:
        lines.append("Still running; its report arrives when it finishes.")
    session_id = str(attach.get("session_id") or "").strip()
    head_id = str(attach.get("head_id") or "").strip()
    if session_id and head_id:
        lines.append("Full transcript, if you need its steps: "
                     f'read_conversation(session_id="{session_id}", head_id="{head_id}")')
    return "\n".join(lines)


def _elide(text: str, node: Call) -> str:
    """Shrink one over-cap node's text for the prompt. Read-only.

    The spill file was written when the node was recorded; here we only
    read ``metadata.spilled`` to cite it, at the cap the stamp recorded.
    A tool result the model called for was bounded when it was made, and a
    message a person or a model wrote is never cut (``spill.never_cut``):
    both go as the turn sent them. A node recorded with a stamp (before
    those rules) is cut as it was then.
    """
    from openprogram.state.store.session.files.spill import bounded_when_made, elide_spilled, never_cut
    spilled = (node.metadata or {}).get("spilled")
    if spilled is None and (bounded_when_made(node) or never_cut(node)):
        return text
    return elide_spilled(text, spilled)


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
