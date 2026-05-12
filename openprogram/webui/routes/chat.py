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

        return JSONResponse(content={"session_id": session_id, "msg_id": msg_id})

    @app.post("/api/chat/branch")
    async def post_chat_branch(body: dict = None):
        """Fork a conversation at a specific message into a new conv."""
        from openprogram.webui import server as _s
        from openprogram.agentic_programming.context import Context
        if body is None:
            return JSONResponse(content={"error": "no body"}, status_code=400)
        session_id = body.get("session_id")
        pivot_id = body.get("msg_id")
        if not session_id or not pivot_id:
            return JSONResponse(
                content={"error": "session_id and msg_id required"}, status_code=400,
            )

        with _s._sessions_lock:
            src = _s._sessions.get(session_id)
            if src is None:
                return JSONResponse(content={"error": "unknown conv"}, status_code=404)
            msgs = src["messages"]
            pivot_idx = next(
                (i for i, m in enumerate(msgs) if m.get("id") == pivot_id), -1,
            )
            if pivot_idx < 0:
                return JSONResponse(content={"error": "unknown msg"}, status_code=404)

            new_id = str(uuid.uuid4())[:8]
            new_title = f"{src.get('title', 'branch')} (branch)"
            _s._sessions[new_id] = {
                "id": new_id,
                "title": new_title,
                "root_context": Context(
                    name="chat_session", status="idle", start_time=time.time(),
                ),
                "runtime": None,
                "provider_name": src.get("provider_name"),
                "messages": _copy.deepcopy(msgs[: pivot_idx + 1]),
                "function_trees": [],
                "created_at": time.time(),
                "branched_from": session_id,
                "branched_at_msg": pivot_id,
            }

        _s._save_session(new_id)
        return JSONResponse(content={
            "session_id": new_id,
            "title": new_title,
            "branched_from": session_id,
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

        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "run"),
            kwargs={"func_name": function_name, "kwargs": kwargs},
            daemon=True,
        ).start()

        return JSONResponse(content={"session_id": session_id, "msg_id": msg_id})
