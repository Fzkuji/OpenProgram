"""What an application's backend asks the host for, answered in the worker.

An operation runs in a process of its own (``runner``). What only the
worker can do for it (speech to text with the engine of Settings → Voice,
a conversation with the owner's default Agent) the runner asks for with a
``host`` event on its stdout; the application service calls :func:`handle`
here and writes the answer back on the runner's stdin. Each method needs a
capability the manifest declares:

``speech``
    ``speech.transcribe`` (one recording in the application's files:
    timed segments, its length, whether it is silent),
    ``speech.diarize`` (speaker turns of one track given as its chunks)
    and ``speech.readiness`` (whether a speech engine and ffmpeg are set up).
``conversation``
    ``conversation.open`` (a new conversation with the default Agent,
    titled by the application; the application may show it later) and
    ``conversation.ask`` (one owner turn without tools in it, answered
    when the turn ends).

Paths are relative to the instance's files (``storage.files``); a path
outside them is refused.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from . import catalog, state

#: The longest a ``conversation.ask`` turn may take before the backend hears it failed.
ASK_TIMEOUT_SECONDS = 30 * 60


class HostCallError(RuntimeError):
    """A host call that cannot be answered; the message goes to the backend as is."""


def _file(instance: dict, relative: Any) -> Path:
    if not isinstance(relative, str):
        raise HostCallError("expected a path inside the application's files")
    path = catalog.contained(state.files_root(instance["id"]), relative)
    if not path.is_file():
        raise HostCallError(f"no such file: {relative}")
    return path


def _transcribe(instance: dict, params: dict) -> dict:
    from openprogram.runtime import speech_link
    language = str(params.get("language") or "")
    try:
        return speech_link.transcribe_file(str(_file(instance, params.get("path"))), language=language)
    except speech_link.TranscriptionError as exc:
        raise HostCallError(str(exc)) from exc


def _diarize(instance: dict, params: dict) -> dict:
    from openprogram.runtime import speech_link
    chunks = params.get("chunks")
    if not isinstance(chunks, list):
        raise HostCallError("chunks must be a list of {t_ms, path}")
    files = [(int(chunk.get("t_ms") or 0), str(_file(instance, chunk.get("path")))) for chunk in chunks]
    try:
        turns = speech_link.diarize(files)
    except speech_link.DiarizationUnavailable as exc:
        return {"available": False, "reason": exc.reason, "message": str(exc), "turns": []}
    except speech_link.TranscriptionError as exc:
        return {"available": False, "reason": "not_installed", "message": str(exc), "turns": []}
    return {"available": True, "reason": "", "message": "",
            "turns": [{"start_ms": int(a), "end_ms": int(b), "speaker": int(c)} for a, b, c in turns]}


def _readiness(instance: dict, params: dict) -> dict:
    from openprogram.runtime import speech_link
    return speech_link.readiness()


def _open_conversation(instance: dict, params: dict) -> dict:
    from openprogram.runtime.agent_registry.manager import get_default
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.session_settings.session_model import ensure_session_chat_model

    title = " ".join(str(params.get("title") or "").split())[:160] or catalog.get(instance["app_id"])["title"]
    spec = get_default()
    agent_id = spec.id if spec is not None else "main"
    session_id = f"application_{uuid.uuid4().hex[:16]}"
    default_db().create_session(session_id, agent_id, title=title, source="web")
    try:
        ensure_session_chat_model(session_id, agent_id)
    except Exception:  # noqa: BLE001 — the turn pins the chat model again at dispatch
        pass
    state.remember_conversation(instance["id"], session_id)
    return {"session_id": session_id}


def _ask(instance: dict, params: dict) -> dict:
    from openprogram.runtime.agent_registry.manager import get_default
    from openprogram.runtime.conversation.turn_admission.owner_turn import start_owner_turn

    session_id = str(params.get("session_id") or "")
    if not state.started_conversation(instance["id"], session_id):
        raise HostCallError("the application did not open this conversation")
    text = str(params.get("text") or "")
    if not text.strip():
        raise HostCallError("a message is required")
    spec = get_default()
    agent_id = spec.id if spec is not None else "main"
    finished = threading.Event()
    answer: dict = {}

    def done(result: Any) -> None:
        answer["text"] = str(getattr(result, "final_text", "") or "") if result is not None else ""
        finished.set()

    # No tools: what the application sends is data it collected, never instructions.
    start_owner_turn(session_id, agent_id, text, tools=[], name=f"application-{instance['app_id']}",
                     reason_code="application_turn_failed", on_done=done)
    if not finished.wait(ASK_TIMEOUT_SECONDS):
        return {"answer": "", "error": "the turn did not end in time"}
    reply = answer.get("text", "")
    return {"answer": reply, "error": "" if reply.strip() else "the turn did not answer"}


#: method → (capability, handler)
METHODS = {
    "speech.transcribe": ("speech", _transcribe),
    "speech.diarize": ("speech", _diarize),
    "speech.readiness": ("speech", _readiness),
    "conversation.open": ("conversation", _open_conversation),
    "conversation.ask": ("conversation", _ask),
}


def answer_with(method: str, handler) -> None:
    """Answer ``method`` with ``handler(instance, params)`` in this process.

    A host that runs one application on its own (``interfaces/standalone``)
    keeps its conversations itself rather than in the App's sessions; the
    capability each method needs stays as declared in :data:`METHODS`.
    """
    capability, _ = METHODS[method]
    METHODS[method] = (capability, handler)


def handle(instance: dict, definition: dict, method: Any, params: Any) -> Any:
    """Answer one host call of an operation of ``instance``; raises :class:`HostCallError`."""
    found = METHODS.get(method) if isinstance(method, str) else None
    if found is None:
        raise HostCallError(f"unknown host call: {method}")
    capability, handler = found
    if capability not in definition.get("capabilities", ()):
        raise HostCallError(f"application requires {capability}")
    return handler(instance, params if isinstance(params, dict) else {})


__all__ = ["ASK_TIMEOUT_SECONDS", "HostCallError", "METHODS", "answer_with", "handle"]
