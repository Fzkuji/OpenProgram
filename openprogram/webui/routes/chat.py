"""REST chat entry points (parallel to the WS chat action).

Two handlers:
  POST /api/chat/branch — fork a conv at a specific message
  POST /api/function/{name} — directly run an @agentic_function via the
      forced-tool-call dispatch path (same code path as an LLM-issued
      tool call; see dispatcher.dispatch_forced_tool_call).

Sending a chat message goes through the WS ``chat`` action
(ws_actions/chat.py) — that path owns the two-stage session naming via
finalize_turn → _maybe_auto_title.
"""
from __future__ import annotations

import threading
import time
import uuid
import os

from fastapi.responses import JSONResponse


_FUNCTION_BODY_CONTROL_KEYS = {
    "kwargs",
    "session_id",
    "_session_id",
    "work_dir",
    "_workdir",
    "workdir",
}


def register(app):
    @app.post("/api/chat/branch")
    async def post_chat_branch(body: dict = None):
        """Fork a conversation at a specific message — in place.

        New model: fork = move HEAD to the pivot in the same session.
        The next user turn from there writes a sibling, forking the
        DAG naturally. No new session, no history copy. Same backend
        op as ``/api/chat/checkout`` (the sibling navigator); kept as a
        separate endpoint for back-compat with older clients.
        """
        from openprogram.webui import server as _s
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        session_id = body.get("session_id")
        pivot_id = body.get("msg_id")
        if not session_id or not pivot_id:
            return JSONResponse(
                content={"error": "session_id and msg_id required"}, status_code=400,
            )

        from openprogram.agent.session_db import default_db
        db = default_db()
        if not db.message_exists(session_id, pivot_id):
            return JSONResponse(content={"error": "unknown msg"}, status_code=404)
        db.set_head(session_id, pivot_id)
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            if conv is not None:
                conv["head_id"] = pivot_id
                try:
                    conv["messages"] = db.get_branch(session_id) or []
                except Exception:
                    pass
        _s._invalidate_messages(session_id)
        _s._save_session(session_id)
        return JSONResponse(content={
            "session_id": session_id,
            "head_id": pivot_id,
        })

    @app.post("/api/function/{name}")
    async def post_function(name: str, body: dict = None):
        """Directly invoke an @agentic_function through the forced
        tool-call dispatch path. Replaces the former ``/api/run`` —
        all @agentic_function runs (UI-triggered or LLM-issued) now
        share ``dispatcher._wrap_agentic_runtime_block``.

        Body:
          ``session_id`` (optional) — target conversation; created if absent.
          ``kwargs`` (dict)         — function arguments.
          ``work_dir`` (str, optional) — workdir bound to the call.
            Falls back to the session's last workdir for this function,
            then the repo root.
        """
        body = body or {}
        session_id = body.get("session_id") or body.get("_session_id")
        if isinstance(body.get("kwargs"), dict):
            kwargs = dict(body.get("kwargs") or {})
        else:
            # Compatibility for older callers that posted function
            # params at the top level instead of under ``kwargs``.
            kwargs = {
                k: v
                for k, v in body.items()
                if k not in _FUNCTION_BODY_CONTROL_KEYS
            }
        work_dir = (
            body.get("work_dir")
            or body.get("_workdir")
            or body.get("workdir")
        )
        result = run_agentic_function_call(name, kwargs, session_id, work_dir)
        if "error" in result:
            return JSONResponse(status_code=result.pop("status_code", 400),
                                content=result)
        return JSONResponse(content=result)


def run_agentic_function_call(
    name: str,
    kwargs: dict,
    session_id: str | None = None,
    work_dir: str | None = None,
    anchor_msg_id: str | None = None,
) -> dict:
    """Dispatch an @agentic_function via the forced tool-call path and
    return ``{"session_id", "msg_id"}`` (or ``{"error", "status_code",
    ...}`` on a validation failure).

    Shared by ``POST /api/function/{name}`` (fn-form / welcome button)
    and the WS ``retry_function`` action (the Retry button) so both go
    through one code path — a top-level code node appended to the session
    DAG, dispatched exactly like an LLM-issued tool call.

    ``anchor_msg_id`` controls where the run lands on the conversation
    chain, so function calls become first-class members of the same chain
    chat turns use:

    * ``None`` (default — a NEW run from fn-form / welcome) → passed as an
      EMPTY caller, which makes the @agentic_function decorator stamp the
      run's ``metadata.predecessor`` with the session's CURRENT HEAD (see
      ``function.py`` — the "top-level manual call" branch). The run
      chains SEQUENTIALLY off the previous turn's terminal node, exactly
      like a new chat turn: distinct predecessor → its own 1/1 card, no
      false siblings. An empty session (no head) → a root-level run.
    * explicit id (the Retry button passes the ORIGINAL call's
      predecessor) → becomes the re-run's caller so it lands as a SIBLING
      of that call (same fork model as chat-message retry): both runs
      share the original's predecessor, so the version switcher counts
      2/2 and only the active head renders in the transcript.

    The forced path advances HEAD to the new node, so the newest run
    becomes the active branch and only it renders in the transcript.
    """
    from openprogram.webui import server as _s

    # Synchronously validate the tool exists AND is @agentic_function
    # BEFORE we create a session / write a user msg / spawn the
    # subprocess. Without this gate, picking a non-agentic function
    # in fn-form would land in dispatch_forced_tool_call's raise
    # path inside a daemon thread; the HTTP response had already
    # returned 200 with a session_id + msg_id, so the frontend
    # showed a phantom "[function call] foo()" user row that never
    # produced output. Reject early so the caller sees the reason
    # in the response body.
    try:
        from openprogram.functions import agent_tools as _agent_tools
        _tools = _agent_tools(names=[name]) or []
    except Exception as e:  # noqa: BLE001
        return {"error": f"failed to resolve tool {name!r}: {type(e).__name__}: {e}",
                "status_code": 500}
    _tool = next((t for t in _tools if t.name == name), None)
    if _tool is None:
        return {"error": f"tool not found: {name!r}", "status_code": 404}
    if not getattr(_tool, "_is_agentic", False):
        return {
            "error": (
                f"tool {name!r} is not an @agentic_function — "
                "only agentic tools can be invoked via fn-form. "
                "Use the chat interface or LLM tool-call path "
                "for ordinary tools."
            ),
            "tool": name,
            "is_agentic": False,
            "status_code": 400,
        }

    # No enabled model → refuse. An agentic function still needs a
    # model to dispatch its agent loop against; with everything
    # disabled the run would fall back to a pinned / auto-detected
    # default the user explicitly turned off. Reject so the UI can
    # prompt for a model instead of silently executing (the exact
    # surprise of "I disabled everything yet gui_agent still ran").
    if not _s._runtime_management._enabled_model_keys():
        return {
            "error": (
                "No model enabled. Enable a model in "
                "Settings → Providers before running a function."
            ),
            "code": "no_model",
            "status_code": 409,
        }

    kwargs = dict(kwargs or {})
    conv = _s._get_or_create_session(session_id)
    session_id = conv["id"]
    # Reject a second run while one is already in flight in this session.
    # The chat/retry path already guards via _is_run_active; the fn-form /
    # Retry entry point did not, so two concurrent runs could advance HEAD
    # at the same time and interleave the conversation chain (corrupt
    # siblings, a dropped running_task entry from the setdefault below).
    # Same 409 the chat retry path returns.
    if _s._is_run_active(session_id):
        return {
            "error": (
                "a run is currently active in this session — wait for it "
                "to finish or stop it first"
            ),
            "code": "run_active",
            "status_code": 409,
        }
    # A NEW run (anchor left unset) passes an EMPTY caller so the
    # @agentic_function decorator stamps its metadata.predecessor with the
    # session's current head (function.py's top-level-call branch) — the
    # run chains off the previous turn's terminal node like a new chat
    # turn. An explicit anchor (the Retry button) is honoured verbatim as
    # the run's caller so it forks as a sibling of the original.
    if anchor_msg_id is None:
        anchor_msg_id = ""
    if not work_dir or not str(work_dir).strip():
        work_dir = (conv.get("last_workdirs") or {}).get(name)
    if not work_dir or not str(work_dir).strip():
        work_dir = os.path.abspath(
            os.path.join(os.path.dirname(_s.__file__), "..", "..")
        )
    work_dir = os.path.abspath(os.path.expanduser(str(work_dir)))
    try:
        conv.setdefault("last_workdirs", {})[name] = work_dir
    except Exception:
        pass
    # msg_id is only a WS-routing handle for the response stream;
    # it is never written to the DAG. The code node written by the
    # @agentic_function is the canonical record: a NEW run (empty anchor)
    # gets metadata.predecessor = the session head (or ROOT for an empty
    # session); a Retry (explicit pred:<id> anchor) forks off that id.
    msg_id = uuid.uuid4().hex[:8]

    # Ensure the session ROOT node exists so a run that anchors at ROOT
    # (empty session, or a legacy retry) resolves to a real node. No
    # anchor row is written for the run itself.
    try:
        from openprogram.agent.session_db import default_db
        from openprogram.context.nodes import Call as _C, ROLE_USER as _RU
        from openprogram.store import GraphStoreShim as _GS
        _db = default_db()
        if not _db.message_exists(session_id, "ROOT"):
            _GS(_db, session_id).append(_C(
                id="ROOT", role=_RU, output="",
                metadata={"display": "root"},
            ))
    except Exception:
        pass

    from openprogram.agent.session_db import default_db as _rc_db2
    agent_id = (_rc_db2().get_session(session_id) or {}).get("agent_id") or _s._default_agent_id()

    # Stage-1 title (immediate placeholder): the call signature, so
    # the sidebar row shows instantly and the session survives a
    # refresh — without an anchor user row there is no preview, and
    # build_sessions_list drops title-less + preview-less rows.
    #
    # Per docs/design/runtime/session/, fn-form takes the SAME
    # two-stage naming as a normal chat: this signature is the
    # stage-1 truncation, and stage-2 is the background LLM rename
    # (below, after the call produces a result). No lock flag is set
    # here, so stage-2 is free to rename it — fn-form is not pinned.
    _fn_title = ""
    try:
        _arg_bits = ", ".join(
            f"{k}={v!r}" if not isinstance(v, str) or len(v) <= 40
            else f"{k}={v[:37]!r}…"
            for k, v in (kwargs or {}).items()
        )
        _fn_title = f"{name}({_arg_bits})"[:80]
        _existing = _rc_db2().get_session(session_id) or {}
        _meta_fields = {"title": _fn_title, "agent_id": agent_id}
        # Stamp created_at on first use so the row sorts to the top of
        # the sidebar (build_sessions_list orders by created_at desc).
        if not _existing.get("created_at"):
            _meta_fields["created_at"] = time.time()
        _rc_db2().update_session(session_id, **_meta_fields)
    except Exception:
        pass

    def _run():
        from openprogram.agent.dispatcher import dispatch_forced_tool_call
        try:
            try:
                out = dispatch_forced_tool_call(
                    session_id=session_id,
                    anchor_msg_id=anchor_msg_id,
                    tool_name=name,
                    tool_input=kwargs,
                    work_dir=work_dir,
                    agent_id=agent_id,
                    source="fn-form",
                    on_event=lambda env: _s._broadcast_envelope(env)
                        if hasattr(_s, "_broadcast_envelope")
                        else _s._broadcast(__import__("json").dumps(env, default=str)),
                )
            except Exception as e:  # noqa: BLE001
                _s._broadcast_chat_response(session_id, msg_id, {
                    "type": "error",
                    "content": f"function call failed: {type(e).__name__}: {e}",
                    "function": name,
                    "display": "runtime",
                })
                return
            if (out or {}).get("ok") and _fn_title:
                # Stage-2 of the doc's two-stage naming: the function has
                # produced a result, so let the LLM rename the session
                # over the call + output (race-guarded; never locks).
                from openprogram.agent.dispatcher.titles import (
                    fn_form_llm_title,
                )
                fn_form_llm_title(_rc_db2(), session_id, _fn_title)
        finally:
            # The function run is over (success / error / exception) —
            # clear the running task so the sidebar's flowing animation
            # stops and the composer's stop button reverts to send.
            # Without this the session shows "running" forever (the
            # chat path clears via _execute finalize; the fn-form path
            # never did — only set it on start). Mirrors
            # _execute/chat.py:277-278.
            try:
                with _s._running_tasks_lock:
                    _s._running_tasks.pop(session_id, None)
                _s._emit_running_task_event(session_id)
            except Exception:
                pass

    # Mark the session running BEFORE starting the thread so its
    # sidebar row shows the flowing animation (convRunningFlow)
    # immediately — same signal the chat path emits at chat_ack. Must
    # precede Thread.start(): a very fast function could otherwise hit
    # its finalize pop before this setdefault runs, re-pinning the
    # session as "running" forever. The thread's finally pops it +
    # emits running_task_clear when the run ends.
    try:
        with _s._running_tasks_lock:
            _s._running_tasks.setdefault(session_id, {
                "msg_id": msg_id, "func_name": name,
                "started_at": time.time(), "last_event_at": time.time(),
                "display_params": "", "loaded_func_ref": None,
                "stream_events": [],
            })
        _s._emit_running_task_event(session_id)
    except Exception:
        pass

    threading.Thread(target=_run, daemon=True).start()

    # The fn-form path creates the session row directly (no WS
    # action ran), so the sidebar — which only fetches the list on
    # mount/manual refresh — never learns about the new session.
    # Broadcast the current list once so every connected client's
    # sidebar shows the new conversation immediately.
    try:
        from openprogram.webui.ws_actions.session import broadcast_sessions_list
        broadcast_sessions_list()
    except Exception:
        pass

    return {"session_id": session_id, "msg_id": msg_id}
