"""Event-stream tap — incremental tool-node persistence (dispatcher-split).

Wraps the caller's ``on_event`` so tool_execution_end envelopes are
sniffed and each completed tool row is written to the DB incrementally —
without changing ``_run_loop_blocking``'s signature (test mocks wrap it
positionally and would break on a new kwarg).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import EventCallback, TurnRequest

_log = logging.getLogger(__name__)


def make_stream_tap(
    *,
    on_event: "EventCallback",
    req: "TurnRequest",
    assistant_msg_id: str,
    placeholder_inserted: bool,
    agentic_tool_names: set[str],
) -> "EventCallback":
    """Return the wrapped ``on_event`` used for the agent-loop run.

    ``agentic_tool_names`` is the (shared, mutable) set the loop runner
    fills once it resolves the tool list — @agentic_function calls are
    rendered as a runtime-block row (persisted by the wrapper in
    ``_wrap_agentic_runtime_block``), so the tap skips them here to
    avoid duplicating the call in chat.
    """
    _tool_args_by_id: dict[str, dict] = {}

    def _on_event_persist(env: dict) -> None:
        on_event(env)
        if not placeholder_inserted:
            return
        try:
            if env.get("type") != "chat_response":
                return
            payload = env.get("data") or {}
            if payload.get("type") != "stream_event":
                return
            evt = payload.get("event") or {}
            etype = evt.get("type")
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
                from openprogram.store import GraphStoreShim

                _tool_name = (meta.get("tool")
                              or evt.get("tool") or "")
                _node = Call(
                    id=f"{assistant_msg_id}_t_{tid}",
                    created_at=time.time(),
                    role=ROLE_CODE,
                    name=_tool_name,
                    input=meta.get("input") or {},
                    output=str(evt.get("result") or ""),
                    caller=assistant_msg_id,
                    metadata={
                        "tool_call_id": tid,
                        "is_error": bool(evt.get("is_error")),
                    },
                )
                GraphStoreShim(
                    _db(), req.session_id,
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

    return _on_event_persist
