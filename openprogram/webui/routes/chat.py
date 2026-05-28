"""REST chat entry points (parallel to the WS chat action).

Three handlers:
  POST /api/chat — send a chat message
  POST /api/chat/branch — fork a conv at a specific message
  POST /api/function/{name} — directly run an @agentic_function via the
      forced-tool-call dispatch path (same code path as an LLM-issued
      tool call; see dispatcher.dispatch_forced_tool_call).
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


def _kwargs_repr(kwargs: dict) -> str:
    parts = []
    for k, v in (kwargs or {}).items():
        if k in ("runtime", "callback"):
            continue
        try:
            r = repr(v)
        except Exception:
            r = "<unrepr>"
        if len(r) > 60:
            r = r[:57] + "..."
        parts.append(f"{k}={r}")
    return ", ".join(parts)


def register(app):
    @app.post("/api/chat")
    async def post_chat(body: dict = None):
        from openprogram.webui import server as _s
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        text = body.get("text", "").strip()
        session_id = body.get("session_id")
        if not text:
            return JSONResponse(content={"error": "empty message"}, status_code=400)

        conv = _s._get_or_create_session(session_id)
        session_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        if not conv["messages"]:
            conv["title"] = text[:50]

        parsed = _s._parse_chat_input(text)
        user_msg = {
            "role": "user",
            "id": msg_id,
            "content": text,
            "timestamp": time.time(),
        }
        _s._append_msg(conv, user_msg)

        if parsed["action"] == "query":
            threading.Thread(
                target=_s._execute_in_context,
                args=(session_id, msg_id, "query"),
                kwargs={"query": parsed["raw"]},
                daemon=True,
            ).start()
        elif parsed["action"] == "spawn":
            threading.Thread(
                target=_s._execute_in_context,
                args=(session_id, msg_id, "spawn"),
                kwargs={"kwargs": {
                    "prompt": parsed.get("prompt") or "",
                    "label": parsed.get("label") or "",
                    "context": parsed.get("context") or "inherit",
                    "wait": parsed.get("wait", True),
                }},
                daemon=True,
            ).start()
        elif parsed["action"] == "merge":
            threading.Thread(
                target=_s._execute_in_context,
                args=(session_id, msg_id, "merge"),
                kwargs={"kwargs": {
                    "sub_sessions": parsed.get("sub_sessions") or [],
                    "message": parsed.get("message") or "",
                }},
                daemon=True,
            ).start()

        return JSONResponse(content={"session_id": session_id, "msg_id": msg_id})

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
            return JSONResponse(
                status_code=500,
                content={"error": f"failed to resolve tool {name!r}: {type(e).__name__}: {e}"},
            )
        _tool = next((t for t in _tools if t.name == name), None)
        if _tool is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"tool not found: {name!r}"},
            )
        if not getattr(_tool, "_is_agentic", False):
            return JSONResponse(
                status_code=400,
                content={
                    "error": (
                        f"tool {name!r} is not an @agentic_function — "
                        "only agentic tools can be invoked via fn-form. "
                        "Use the chat interface or LLM tool-call path "
                        "for ordinary tools."
                    ),
                    "tool": name,
                    "is_agentic": False,
                },
            )

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

        conv = _s._get_or_create_session(session_id)
        session_id = conv["id"]
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
        msg_id = uuid.uuid4().hex[:8]

        # Persist the user-side command marker that anchors the
        # runtime-block row. Same shape the chat path uses for /run.
        _s._append_msg(conv, {
            "role": "user",
            "id": msg_id,
            "content": f"[function call] {name}({_kwargs_repr(kwargs)})",
            "display": "runtime",
            "source": "fn-form",
            "timestamp": time.time(),
        })
        try:
            _s._save_session(session_id)
        except Exception:
            pass

        agent_id = conv.get("agent_id") or _s._default_agent_id()

        def _run():
            from openprogram.agent.dispatcher import dispatch_forced_tool_call
            try:
                dispatch_forced_tool_call(
                    session_id=session_id,
                    anchor_msg_id=msg_id,
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

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse(content={"session_id": session_id, "msg_id": msg_id})
