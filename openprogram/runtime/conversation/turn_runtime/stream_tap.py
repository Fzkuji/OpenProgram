"""Event-stream tap — incremental tool-node persistence (dispatcher-split).

Wraps the caller's ``on_event`` so tool_execution_end envelopes are
sniffed and each completed tool row is written to the DB incrementally —
without changing ``_run_loop_blocking``'s signature (test mocks wrap it
positionally and would break on a new kwarg).
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Callable
from dataclasses import dataclass

from openprogram.runtime.conversation.event_parsing import STORED_DETAILS_KEY, STORED_MEDIA_KEY, STORED_RESULT_KEY
from openprogram.state.reply_text import opening_separator, reply_text
from openprogram.state import task_blocks
from openprogram.state.attachments import markers_as_names

if TYPE_CHECKING:
    from openprogram.runtime.conversation.turn_runtime.types import EventCallback
    from openprogram.runtime.agent.turn_request import TurnRequest

_log = logging.getLogger(__name__)


def utf16_length(text: str) -> int:
    """Length in UTF-16 units, the unit a browser's string offsets use."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def block_item_id(blocks: list[dict], index: int) -> str:
    """The stable id of a text or thinking block.

    ``<type>:<n>`` counts runs of that type; a later block in the same run
    is ``<type>:<n>.<k>``, so a block the tap merged keeps its id when the
    final write segments it.
    """
    kind = blocks[index].get("type")
    run = -1
    position = 0
    for i in range(index + 1):
        if blocks[i].get("type") != kind:
            continue
        if i == 0 or blocks[i - 1].get("type") != kind:
            run += 1
            position = 0
        else:
            position += 1
    return f"{kind}:{run}.{position}" if position else f"{kind}:{run}"


def naming_reply_row(on_event: "EventCallback", session_id: str, user_msg_id: str, reply_row_id: str) -> "EventCallback":
    """Stamp the reply's row id on every frame of this turn that lacks one.

    A client looks up the reply a result, error or retry frame answers by
    this ``row_id``; it never builds the id from ``msg_id`` itself.
    """
    def emit(env: dict) -> None:
        data = env.get("data") if env.get("type") == "chat_response" else None
        if (isinstance(data, dict) and "row_id" not in data and data.get("msg_id") == user_msg_id
                and data.get("session_id") in (None, session_id) and data.get("type") != "user_message"):
            env = {**env, "data": {**data, "row_id": reply_row_id}}
        on_event(env)
    return emit


@dataclass
class StreamTap:
    callback: Callable[[dict], None]
    flush: Callable[[], None]
    blocks: list[dict]
    # What the reply belonged to before its first task call
    # (``state.task_blocks``): ``{"task", "step"}`` or None.
    initial: dict | None = None

    def __call__(self, env: dict) -> None:
        self.callback(env)


def final_blocks(tap: StreamTap, ordered: list[dict], cancel_event) -> list[dict]:
    """The blocks a reply's terminal write keeps.

    The loop's ordered blocks win: on a Stop they hold every response that
    ended, signed as it was, and the cut one's text marked ``partial``
    (``loop_runner._keep_stopped_response``). The tap's trace stands in
    when they hold nothing. Either way each block is stamped with the task
    and step it belongs to, from the task calls the tap saw
    (``state.task_blocks``).
    """
    chosen = ordered or tap.blocks
    task_blocks.stamp(chosen, task_blocks.effects_of(tap.blocks), tap.initial)
    return chosen


def _starting_owner(req: "TurnRequest") -> dict | None:
    """The task a reply continues from its first block: the open task of
    the Goal this turn works for (``todo.shared.turn_owner``)."""
    if getattr(req, "goal_verification", None):
        return None
    try:
        from openprogram.programs.tools.planning.goal import chat as goal_chat
        from openprogram.programs.tools.planning.todo import shared as tasks
        goal = goal_chat.current_identity() or getattr(req, "goal_context", None)
        return tasks.turn_owner(req.session_id, tasks.scope_of(req), goal, getattr(req, "branch_id", None))
    except Exception:
        _log.debug("task of the turn not read for session %s", req.session_id, exc_info=True)
        return None


def make_stream_tap(
    *,
    on_event: "EventCallback",
    req: "TurnRequest",
    assistant_msg_id: str,
    placeholder_inserted: bool,
    agentic_tool_names: set[str],
) -> StreamTap:
    """Return the wrapped ``on_event`` used for the agent-loop run.

    ``agentic_tool_names`` is the (shared, mutable) set the loop runner
    fills once it resolves the tool list — Agent method calls are
    rendered as a runtime-block row (persisted by the wrapper in
    ``_wrap_agentic_runtime_block``), so the tap skips them here to
    avoid duplicating the call in chat.

    Every ``stream_event`` frame of this reply gets a ``seq`` one higher
    than the last, and the persisted row records the ``stream_seq`` its
    content already reflects. A client that loads the row mid-turn applies
    only frames with a higher ``seq``, so no delta is shown twice or lost.
    """
    _tool_args_by_id: dict[str, dict] = {}
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store import SessionNodeWriter
    store = default_db()
    writer = SessionNodeWriter(store, req.session_id)
    # A resumed execution reuses its placeholder and its earlier display trace.
    node = next((n for n in store.get_nodes(req.session_id) if n.id == assistant_msg_id), None)
    existing = (node.metadata or {}) if node else {}
    extra = existing.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    blocks: list[dict] = [dict(b) for b in extra.get("blocks", [])]
    # A resumed execution keeps what its reply started with; a new one asks.
    if "task_initial" not in extra and not blocks:
        started_with = _starting_owner(req)
        if started_with:
            extra["task_initial"] = started_with
    initial: dict | None = task_blocks.clean_effect(extra.get("task_initial"))
    if initial is not None and not initial.get("task"):
        initial = None
    # What the next block belongs to: ``{"task", "step"}`` or empty.
    owner: dict = task_blocks.owner_after(blocks, initial)
    # The task whose segment for this reply starts with its first real call.
    segment_due: list = [initial["task"]] if initial else []
    for block in blocks:
        if block.get("type") == "tool" and block.get("tool_call_id"):
            _tool_args_by_id[block["tool_call_id"]] = block
    # A resumed execution continues its reply's numbering.
    stream_seq = int(extra.get("stream_seq") or 0) if isinstance(extra, dict) else 0
    dirty = False

    from openprogram.state.memory.policy import stamp as _memory_stamp

    def _tail() -> tuple[int, int]:
        """Where the blocks end now: their count and the open text block's length."""
        last = blocks[-1] if blocks else None
        open_text = last is not None and last.get("type") in ("text", "thinking")
        return len(blocks), len(last["text"]) if open_text else 0

    # Where the provider response now streaming began. A response that is
    # sent again (a ``retry`` frame with ``restart``) is cut back to here.
    response_start = _tail()

    # What the log already holds of this reply, so each later write records
    # only what was added (``session_log.apply_stream_changes``): the first
    # write is the whole trace, then the open block's new text, or the
    # blocks from the first one that changed. Rewriting the whole trace on
    # every delta made a long reply's log grow with the square of its size.
    persisted: dict = {"written": False, "count": 0, "tail": None, "output": "", "changed_from": None,
                       "split": None}

    def _changed(index: int) -> None:
        """Block ``index`` changed other than by text added to the open block."""
        current = persisted["changed_from"]
        persisted["changed_from"] = index if current is None else min(current, index)

    def _remember(output: str) -> None:
        last = blocks[-1] if blocks else None
        persisted.update(
            written=True, count=len(blocks), output=output, changed_from=None,
            tail=len(last["text"]) if last is not None and last.get("type") in ("text", "thinking") else None,
        )

    def _stream_change(output: str) -> dict:
        count, previous = len(blocks), persisted["count"]
        start = min(count, previous, *(() if persisted["changed_from"] is None else (persisted["changed_from"],)))
        change: dict = {"stream_seq": stream_seq}
        tail = previous - 1
        if persisted["split"] == tail and count > previous and start > tail >= 0:
            # The block that just closed reached the log as pieces; write it
            # whole once, so a search of the log finds its text on one line
            # even when the reply never gets its closing whole write.
            start = tail
        if persisted["split"] is not None and start <= persisted["split"]:
            persisted["split"] = None
        if start > tail >= 0 and persisted["tail"] is not None:
            text = blocks[tail].get("text") or ""
            if len(text) > persisted["tail"]:
                change["text"] = [tail, text[persisted["tail"]:]]
                persisted["split"] = tail
        if start < max(count, previous):
            change["from"] = start
            change["blocks"] = blocks[start:]
        if output.startswith(persisted["output"]):
            if len(output) > len(persisted["output"]):
                change["output_tail"] = output[len(persisted["output"]):]
        else:
            change["output"] = output
        return change

    def _drop_restarted_response() -> bool:
        count, length = response_start
        changed = len(blocks) > count
        del blocks[count:]
        if count and blocks[count - 1].get("type") in ("text", "thinking"):
            text = blocks[count - 1]["text"]
            if len(text) > length:
                blocks[count - 1]["text"] = text[:length]
                changed = True
        if changed:
            _changed(max(count - 1, 0))
        owner.clear()
        owner.update(task_blocks.owner_after(blocks, initial))
        return changed

    def _start_segment(tool: str | None) -> None:
        """The first real call of a reply that continues a task begins the
        reply's segment of it."""
        if not segment_due or tool in task_blocks.TASK_TOOLS or owner.get("task") != segment_due[0]:
            return
        task_id = segment_due.pop()
        try:
            from openprogram.programs.tools.planning.todo import shared as tasks
            tasks.open_turn_segment(req.session_id, assistant_msg_id, task_id, scope=tasks.scope_of(req, assistant_msg_id),
                                    branch_id=getattr(req, "branch_id", None))
        except Exception:
            _log.debug("task segment not started for session %s", req.session_id, exc_info=True)

    def _apply_effect(index: int, effect) -> None:
        """A task call returned: it and every block after it change owner."""
        effect = task_blocks.clean_effect(effect)
        if effect is None:
            return
        blocks[index]["task_effect"] = effect
        first = task_blocks.stamp(blocks, task_blocks.effects_of(blocks), initial)
        if first is not None:
            _changed(first)
        owner.clear()
        owner.update(task_blocks.owner_after(blocks, initial))
        if segment_due and effect.get("task") == segment_due[0]:
            # The call wrote the segment itself.
            segment_due.clear()

    def flush(*, quiet: bool = False) -> None:
        nonlocal dirty
        if not dirty or not placeholder_inserted:
            return
        try:
            # A marker the model wrote is not a file the turn attached
            # (those are added at the end, send_file.with_attachments);
            # a reply cut off here (a worker restart) keeps this text.
            output = markers_as_names(reply_text(blocks))
            fields = {
                "output": output,
                "metadata": {**_memory_stamp(req), "extra": json.dumps({
                    **extra,
                    "blocks": blocks, "stream_seq": stream_seq,
                }, default=str)},
            }
            if persisted["written"]:
                store.update_stream_progress(req.session_id, assistant_msg_id, change=_stream_change(output),
                                             quiet=quiet, **fields)
            elif quiet:
                store.update_stream_progress(req.session_id, assistant_msg_id, **fields)
            else:
                writer.update(assistant_msg_id, **fields)
            _remember(output)
            dirty = False
        except Exception:
            _log.warning("failed to persist chat progress for session %s", req.session_id, exc_info=True)

    def _owns(payload: dict) -> bool:
        # Runtime children share this callback but own different rows.
        if payload.get("session_id") not in (None, req.session_id):
            return False
        return payload.get("msg_id") in (
            None, req.user_msg_id, assistant_msg_id.removesuffix("_reply"),
            assistant_msg_id,
        )

    def _on_event_persist(env: dict) -> None:
        nonlocal dirty, stream_seq
        payload = env.get("data") if env.get("type") == "chat_response" else None
        if (isinstance(payload, dict) and payload.get("type") == "stream_event"
                and _owns(payload)):
            stream_seq += 1
            payload["seq"] = stream_seq
        # A frame sent before a read is reflected in that read, so the row
        # is written first. A failed write still emits; the offset shows it.
        _persist(env)
        if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
            # The whole result is for the tool node; clients get the preview.
            payload["event"].pop(STORED_RESULT_KEY, None)
            payload["event"].pop(STORED_DETAILS_KEY, None)
            payload["event"].pop(STORED_MEDIA_KEY, None)
        on_event(env)

    def _persist(env: dict) -> None:
        nonlocal dirty, response_start
        if not placeholder_inserted:
            return
        try:
            if env.get("type") != "chat_response":
                return
            payload = env.get("data") or {}
            if (payload.get("type") == "user_message" and payload.get("steering")
                    and payload.get("assistant_msg_id") == assistant_msg_id
                    and payload.get("session_id") == req.session_id):
                if not any(b.get("type") == "steering" and b.get("message_id") == payload.get("msg_id") for b in blocks):
                    blocks.append({"type":"steering", "message_id":payload["msg_id"],
                                   "text":payload.get("content", ""), "timestamp":payload.get("timestamp")})
                    response_start = _tail()
                    dirty = True
                    flush()
                return
            if payload.get("type") != "stream_event" or not _owns(payload):
                return
            evt = payload.get("event") or {}
            etype = evt.get("type")
            if etype == "retry":
                if evt.get("restart"):
                    # The request goes out again: the failed response's text
                    # and reasoning leave the row before the new ones arrive.
                    if _drop_restarted_response():
                        dirty = True
                        flush()
                elif evt.get("reason") not in ("transport", "network_wait"):
                    # A continuation: the response ended, the next one adds to it.
                    response_start = _tail()
                return
            if etype in {"thinking", "text", "tool_use", "tool_result"}:
                if etype in {"thinking", "text"}:
                    delta = evt.get("text") or ""
                    if blocks and blocks[-1].get("type") == etype:
                        offset = utf16_length(blocks[-1]["text"])
                    else:
                        offset = 0
                        blocks.append({"type": etype, "text": "", **owner})
                    if etype == "text" and delta and not blocks[-1]["text"]:
                        # The first text of a passage after a tool call,
                        # reasoning or a steering message: a client that
                        # builds the reply's flat text from deltas puts
                        # this before it (``reply_text.stream_text``).
                        separator = opening_separator(blocks, len(blocks) - 1, delta)
                        if separator:
                            evt["separator"] = separator
                    blocks[-1]["text"] += delta
                    payload.update(
                        row_id=assistant_msg_id,
                        item_id=block_item_id(blocks, len(blocks) - 1),
                        item_index=len(blocks) - 1,
                        offset=offset,
                        **{key: blocks[-1][key] for key in ("task", "step") if blocks[-1].get(key)},
                    )
                elif etype == "tool_use":
                    tid = evt.get("tool_call_id")
                    if not tid or not any(b.get("type") == "tool" and b.get("tool_call_id") == tid for b in blocks):
                        card = {
                            "type": "tool", "tool": evt.get("tool"),
                            "tool_call_id": tid,
                            "input": evt.get("input"), "result": None,
                            "is_error": False, **owner,
                        }
                        if evt.get("provider_executed"):
                            card["provider_executed"] = True
                        blocks.append(card)
                        if evt.get("task_effect") is not None:
                            # Its ``task_progress`` moved the reply before it ran.
                            _apply_effect(len(blocks) - 1, evt["task_effect"])
                        _start_segment(evt.get("tool"))
                else:
                    for index in range(len(blocks) - 1, -1, -1):
                        block = blocks[index]
                        if (block.get("type") == "tool"
                                and block.get("tool_call_id") == evt.get("tool_call_id")):
                            _changed(index)
                            block.update(result=evt.get("result"),
                                         is_error=bool(evt.get("is_error")),
                                         outcome=evt.get("outcome"))
                            if evt.get("full_output"):
                                block["full_output"] = evt["full_output"]
                            if evt.get("provider_executed"):
                                # The query of a provider search is often
                                # known only when it ends.
                                block["provider_executed"] = True
                                if evt.get("input"):
                                    block["input"] = evt.get("input")
                            stored_details = evt.get(STORED_DETAILS_KEY)
                            if isinstance(stored_details, dict) and "task" in stored_details:
                                _apply_effect(index, stored_details["task"])
                            break
                    else:
                        if evt.get("provider_executed"):
                            blocks.append({
                                "type": "tool", "tool": evt.get("tool"),
                                "tool_call_id": evt.get("tool_call_id"),
                                "provider_executed": True,
                                "input": evt.get("input"),
                                "result": evt.get("result"),
                                "is_error": bool(evt.get("is_error")),
                                "outcome": evt.get("outcome"), **owner,
                            })
                if etype in {"tool_use", "tool_result"} and not evt.get("provider_executed"):
                    # Local tools run after a response ended; the next one starts here.
                    response_start = _tail()
                dirty = True
                flush(quiet=etype in {"thinking", "text"})
            if etype == "text_end" and evt.get("citations"):
                for index in range(len(blocks) - 1, -1, -1):
                    if blocks[index].get("type") == "text":
                        blocks[index]["citations"] = list(evt["citations"])
                        _changed(index)
                        payload.update(row_id=assistant_msg_id, item_id=block_item_id(blocks, index))
                        dirty = True
                        flush()
                        break
                return
            if evt.get("provider_executed"):
                # The provider ran this search; the reply's blocks carry it
                # and no local tool node (a ToolCall on replay) is written.
                return
            if etype == "tool_use":
                tid = evt.get("tool_call_id")
                if tid:
                    _tool_args_by_id[tid] = {
                        "tool": evt.get("tool"),
                        "input": evt.get("input"),
                    }
            elif etype == "tool_result":
                tid = evt.get("tool_call_id")
                if not tid:
                    return
                meta = _tool_args_by_id.get(tid, {})
                # Agent method tool calls are rendered as a
                # runtime-block row (persisted by the wrapper in
                # _wrap_agentic_runtime_block) — don't ALSO persist
                # them as collapsed role=tool entries, that would
                # duplicate the call in chat.
                _tname = meta.get("tool") or evt.get("tool") or ""
                if _tname in agentic_tool_names:
                    return
                from openprogram.runtime.context.session_db import (
                    default_db as _db,
                )
                from openprogram.state.store.session.nodes import Call, ROLE_CODE
                from openprogram.state.store import SessionNodeWriter

                _tool_name = (meta.get("tool")
                              or evt.get("tool") or "")
                _node_id = f"{assistant_msg_id}_t_{tid}"
                _store = _db()
                if _store.message_exists(req.session_id, _node_id):
                    return
                _node = Call(
                    id=_node_id,
                    created_at=time.time(),
                    role=ROLE_CODE,
                    name=_tool_name,
                    input=meta.get("input") or {},
                    # What the model read, so every later request sends
                    # the same bytes; the frame's ``result`` is a preview.
                    output=str(evt.get(STORED_RESULT_KEY, evt.get("result")) or ""),
                    caller=assistant_msg_id,
                    metadata={
                        **_memory_stamp(req),
                        "tool_call_id": tid,
                        "is_error": bool(evt.get("is_error")),
                        "outcome": evt.get("outcome"),
                        **({"details": evt[STORED_DETAILS_KEY]}
                           if evt.get(STORED_DETAILS_KEY) else {}),
                        **({"media": evt[STORED_MEDIA_KEY]}
                           if evt.get(STORED_MEDIA_KEY) else {}),
                    },
                )
                SessionNodeWriter(
                    _store, req.session_id,
                ).append(_node)
        except Exception:
            # Event-tap boundary: this runs inside the provider's stream
            # callback, so raising here would abort a turn that is
            # otherwise fine. A dropped tool node costs history fidelity,
            # which is worth a log line.
            _log.warning(
                "failed to persist tool node for session %s",
                req.session_id, exc_info=True,
            )

    return StreamTap(_on_event_persist, flush, blocks, initial)
