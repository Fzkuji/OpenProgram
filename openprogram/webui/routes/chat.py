"""REST chat entry points (parallel to the WS chat action).

Three handlers:
  POST /api/chat — send a chat message
  POST /api/chat/branch — fork a conv at a specific message
  POST /api/run/{function_name} — run a function directly
"""
from __future__ import annotations

import copy as _copy
import threading
import time
import uuid

from fastapi.responses import JSONResponse


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
        if parsed["action"] == "run":
            user_msg["display"] = "runtime"
        _s._append_msg(conv, user_msg)

        if parsed["action"] == "run":
            threading.Thread(
                target=_s._execute_in_context,
                args=(session_id, msg_id, "run"),
                kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"]},
                daemon=True,
            ).start()
        elif parsed["action"] == "query":
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

    @app.post("/api/run/{function_name}")
    async def run_function(function_name: str, body: dict = None):
        """Directly run a specific function. `work_dir` is required."""
        from openprogram.webui import server as _s
        kwargs = body or {}
        session_id = kwargs.pop("_session_id", None)
        work_dir = kwargs.pop("work_dir", None)
        if not work_dir or not str(work_dir).strip():
            return JSONResponse(
                content={"error": "work_dir is required"},
                status_code=400,
            )
        kwargs["_work_dir"] = work_dir
        conv = _s._get_or_create_session(session_id)
        session_id = conv["id"]
        msg_id = str(uuid.uuid4())[:8]

        # Anchor the run into the conversation branch with a command
        # message — same as the WS `chat` / REST `/api/chat` run paths.
        # Without this the run's assistant reply (parent_id=msg_id) and
        # the @agentic_function DAG nodes (called_by=msg_id) point at a
        # message that never existed, so the run lands orphaned in a
        # detached view instead of connected to the conversation.
        cmd_parts = ["run", function_name]
        for k, v in kwargs.items():
            if k in ("_work_dir", "runtime", "callback"):
                continue
            cmd_parts.append(f"{k}={v!r}" if " " in str(v) else f"{k}={v}")
        _s._append_msg(conv, {
            "role": "user",
            "id": msg_id,
            "content": " ".join(cmd_parts),
            "display": "runtime",
            "timestamp": time.time(),
            "source": "web",
        })

        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "run"),
            kwargs={"func_name": function_name, "kwargs": kwargs},
            daemon=True,
        ).start()

        return JSONResponse(content={"session_id": session_id, "msg_id": msg_id})
