"""Conversations of a standalone application (capability ``conversation``).

In the App a backend's ``conversation.open`` creates a chat with the
default Agent and ``conversation.ask`` runs one turn of it without tools.
A standalone application has no chats: it keeps each conversation as a
file in its data folder and answers with the configured model, still
without tools, with the earlier messages as context. Its window shows a
conversation (``conversations.show``) and takes follow-up questions
through :func:`ask` as well.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from openprogram.host._platform.filesystem import replace_file
from openprogram.programs._host.ui import state
from openprogram.programs._host.ui.host_calls import HostCallError

_lock = threading.Lock()
#: ``reply(prompt) -> text``; the model by default, replaced in tests.
Reply = Callable[[str], str]

#: The earlier messages a follow-up carries, newest last.
HISTORY_CHARACTERS = 200_000


def _folder() -> Path:
    from openprogram.state.paths import get_state_dir
    folder = get_state_dir() / "standalone" / "conversations"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _path(session_id: str) -> Path:
    if not session_id.startswith("application_") or not session_id[12:].isalnum():
        raise HostCallError("unknown conversation")
    return _folder() / f"{session_id}.json"


def read(session_id: str) -> dict:
    try:
        return json.loads(_path(session_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HostCallError("unknown conversation") from None


def _write(conversation: dict) -> None:
    path = _path(conversation["id"])
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    temp.write_text(json.dumps(conversation, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_file(temp, path)


def open_conversation(instance: dict, params: dict) -> dict:
    title = " ".join(str(params.get("title") or "").split())[:160] or "Conversation"
    session_id = f"application_{uuid.uuid4().hex[:16]}"
    with _lock:
        _write({"id": session_id, "title": title, "created_at": time.time(), "messages": []})
    state.remember_conversation(instance["id"], session_id)
    return {"session_id": session_id}


def model_reply(prompt: str) -> str:
    """One answer of the configured model, without tools."""
    from openprogram.runtime.agent.runtime.factory import create_runtime
    runtime = create_runtime()
    try:
        return str(runtime.exec(content=[{"type": "text", "text": prompt}]) or "")
    finally:
        close = getattr(runtime, "close", None)
        if close:
            close()


def _prompt(messages: list[dict], text: str) -> str:
    if not messages:
        return text
    earlier = "\n\n".join(f"[{m['role']}]\n{m['text']}" for m in messages)[-HISTORY_CHARACTERS:]
    return (f"<earlier-messages>\n{earlier}\n</earlier-messages>\n\n"
            f"Answer this next message of the same conversation:\n\n{text}")


def ask(instance: dict, params: dict, reply: Reply | None = None) -> dict:
    session_id = str(params.get("session_id") or "")
    if not state.started_conversation(instance["id"], session_id):
        raise HostCallError("the application did not open this conversation")
    text = str(params.get("text") or "")
    if not text.strip():
        raise HostCallError("a message is required")
    with _lock:
        conversation = read(session_id)
        earlier = list(conversation["messages"])
        conversation["messages"].append({"role": "user", "text": text, "at": time.time()})
        conversation["replying"] = True
        _write(conversation)
    _changed(session_id)
    try:
        answer, error = (reply or model_reply)(_prompt(earlier, text)), ""
    except Exception as exc:  # noqa: BLE001 — the backend hears why, as from the App
        answer, error = "", str(exc) or type(exc).__name__
    if not answer.strip() and not error:
        error = "the model did not answer"
    with _lock:
        conversation = read(session_id)
        if answer.strip():
            conversation["messages"].append({"role": "assistant", "text": answer, "at": time.time()})
        conversation["replying"] = False
        conversation["error"] = error
        _write(conversation)
    _changed(session_id)
    return {"answer": answer if answer.strip() else "", "error": error}


def _changed(session_id: str) -> None:
    from openprogram.host._platform.changes import signal
    signal("standalone-conversation", session_id)


def install() -> None:
    """Answer the ``conversation`` host calls here instead of in the App's sessions."""
    from openprogram.host._platform.changes import publish
    from openprogram.programs._host.ui import host_calls
    publish("standalone-conversation")
    host_calls.answer_with("conversation.open", open_conversation)
    host_calls.answer_with("conversation.ask", lambda instance, params: ask(instance, params))


__all__ = ["HISTORY_CHARACTERS", "ask", "install", "model_reply", "open_conversation", "read"]


def describe(session_id: str) -> dict[str, Any]:
    conversation = read(session_id)
    return {key: conversation.get(key) for key in ("id", "title", "messages", "replying", "error")}
