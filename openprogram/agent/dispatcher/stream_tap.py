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

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import EventCallback, TurnRequest

_log = logging.getLogger(__name__)


@dataclass
class StreamTap:
    callback: Callable[[dict], None]
    flush: Callable[[], None]
    blocks: list[dict]

    def __call__(self, env: dict) -> None:
        self.callback(env)


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
    fills once it resolves the tool list — @agentic_function calls are
    rendered as a runtime-block row (persisted by the wrapper in
    ``_wrap_agentic_runtime_block``), so the tap skips them here to
    avoid duplicating the call in chat.
    """
    _tool_args_by_id: dict[str, dict] = {}
    from openprogram.agent.session_db import default_db
    from openprogram.store import SessionNodeWriter
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
    blocks: list[dict] = [dict(b) for b in extra.get("blocks", [])] if isinstance(extra, dict) else []
    for block in blocks:
        if block.get("type") == "tool" and block.get("tool_call_id"):
            _tool_args_by_id[block["tool_call_id"]] = block
    dirty = False

    def flush() -> None:
        nonlocal dirty
        if not dirty or not placeholder_inserted:
            return
        try:
            writer.update(
                assistant_msg_id,
                output="".join(b["text"] for b in blocks if b["type"] == "text"),
                metadata={"extra": json.dumps({**(extra if isinstance(extra, dict) else {}), "blocks": blocks}, default=str)},
            )
            dirty = False
        except Exception:
            _log.warning("failed to persist chat progress for session %s", req.session_id, exc_info=True)

    def _on_event_persist(env: dict) -> None:
        nonlocal dirty
        on_event(env)
        if not placeholder_inserted:
            return
        try:
            if env.get("type") != "chat_response":
                return
            payload = env.get("data") or {}
            if payload.get("type") != "stream_event":
                return
            # Runtime children share this callback but own different rows.
            if payload.get("session_id") not in (None, req.session_id):
                return
            if payload.get("msg_id") not in (
                None, req.user_msg_id, assistant_msg_id.removesuffix("_reply"),
                assistant_msg_id,
            ):
                return
            evt = payload.get("event") or {}
            etype = evt.get("type")
            if etype in {"thinking", "text", "tool_use", "tool_result"}:
                if etype in {"thinking", "text"}:
                    delta = evt.get("text") or ""
                    if blocks and blocks[-1].get("type") == etype:
                        blocks[-1]["text"] += delta
                    else:
                        blocks.append({"type": etype, "text": delta})
                elif etype == "tool_use":
                    tid = evt.get("tool_call_id")
                    if not tid or not any(b.get("type") == "tool" and b.get("tool_call_id") == tid for b in blocks):
                        blocks.append({
                            "type": "tool", "tool": evt.get("tool"),
                            "tool_call_id": tid,
                            "input": evt.get("input"), "result": None,
                            "is_error": False,
                        })
                else:
                    for block in reversed(blocks):
                        if (block.get("type") == "tool"
                                and block.get("tool_call_id") == evt.get("tool_call_id")):
                            block.update(result=evt.get("result"),
                                         is_error=bool(evt.get("is_error")),
                                         outcome=evt.get("outcome"))
                            break
                dirty = True
                flush()
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
                # @agentic_function tool calls are rendered as a
                # runtime-block row (persisted by the wrapper in
                # _wrap_agentic_runtime_block) — don't ALSO persist
                # them as collapsed role=tool entries, that would
                # duplicate the call in chat.
                _tname = meta.get("tool") or evt.get("tool") or ""
                if _tname in agentic_tool_names:
                    return
                from openprogram.agent.session_db import (
                    default_db as _db,
                )
                from openprogram.context.nodes import Call, ROLE_CODE
                from openprogram.store import SessionNodeWriter

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
                    output=str(evt.get("result") or ""),
                    caller=assistant_msg_id,
                    metadata={
                        "tool_call_id": tid,
                        "is_error": bool(evt.get("is_error")),
                        "outcome": evt.get("outcome"),
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

    return StreamTap(_on_event_persist, flush, blocks)
