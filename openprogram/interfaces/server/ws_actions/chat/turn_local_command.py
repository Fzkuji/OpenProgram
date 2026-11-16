"""The ``chat`` action's local builtin command phase.

Local builtin commands execute backend-side. Status/clear return a
local reply and stop. A command may instead return one ``invoke``
descriptor: dispatch that registered Agent method through the
same forced-call boundary as Programs. /goal uses this path, so the
command and form execute the same Goal Workflow rather than separate
chat-turn and function loops.
"""
from __future__ import annotations

import asyncio
import json

from .turn_request import _ChatRequest
from .turn_request import _send_chat_error
from .turn_request import _client_request_id
from .turn_request import _with_client_request_id

# ``_validate_local_surface`` result when the Page context was rejected.
_STALE = object()


def _resolve_local_command(text: str, session_id: str):
    # One resolver for every surface (``--print`` runs the same commands).
    from openprogram.runtime.conversation.turn_admission import local_commands
    return local_commands.resolve(text, session_id)


async def _validate_local_surface(ws, surface_ref: dict | None, session_id: str):
    """The Page context a local command may run against.

    ``None`` when no surface was sent, the validated binding otherwise, or
    ``_STALE`` after sending the error frame for a stale or foreign one.
    """
    _validated_surface = None
    if surface_ref is not None:
        _surface_window = surface_ref.get("window_id")
        _surface_tab = surface_ref.get("tab_id")
        from openprogram.interfaces.server.ws_actions import webtab
        _surface_version_valid = (
            type(surface_ref.get("version")) is int
            and surface_ref.get("version") == 1
        )
        _surface_tab_valid = (
            _surface_tab is None
            or (
                isinstance(_surface_tab, str)
                and bool(_surface_tab)
            )
        )
        _surface_window_owned = (
            isinstance(_surface_window, str)
            and bool(_surface_window)
            and any(
                owner is ws and window_id == _surface_window
                for owner, window_id, _revision
                in webtab.registered_desktop_windows()
            )
        )
        if not (
            _surface_version_valid
            and _surface_tab_valid
            and _surface_window_owned
        ):
            await _send_chat_error(
                ws, session_id, "page_context_stale",
                ("The submitted Page context is stale or "
                 "belongs to another desktop window."),
            )
            return _STALE
        _validated_surface = {
            "version": 1,
            "window_id": _surface_window,
        }
        if _surface_tab:
            _validated_surface["tab_id"] = _surface_tab
    return _validated_surface


async def _send_local_error(ws, session_id: str, code: str, content: str) -> None:
    await _send_chat_error(ws, session_id, code, content)


async def _run_local_invocation(ws, _invoke: dict, session_id: str,
                                _validated_surface) -> None:
    """Dispatch the Agent method a local command returned, then ack it."""
    _name = str(_invoke.get("name") or "")
    _kwargs = _invoke.get("kwargs")
    if not _name or not isinstance(_kwargs, dict):
        await _send_local_error(
            ws, session_id, "invalid_local_invocation",
            "Local command returned an invalid invocation.",
        )
        return
    from openprogram.interfaces.server.routes.chat import (
        run_agent_method_call,
    )
    _run_options = {}
    if _validated_surface:
        _run_options["origin_window_id"] = _validated_surface[
            "window_id"
        ]
        _run_options["surface_ref"] = _validated_surface
    _request_id = _client_request_id.get()
    if _request_id:
        _run_options["client_request_id"] = _request_id
    # The run slot and the call's records take the conversation's lock:
    # off the event loop, so a held conversation delays this only.
    _run = await asyncio.to_thread(
        run_agent_method_call, _name, _kwargs, session_id, **_run_options,
    )
    if "error" in _run:
        await _send_local_error(
            ws, session_id, _run.get("code") or "function_call_failed",
            str(_run.get("error") or "Function call failed."),
        )
        return
    if not _run.get("execution_id"):
        await _send_local_error(
            ws, session_id, "execution_admission_failed",
            "Function execution was not admitted.",
        )
        return
    from .turn import _join_and_read
    _first_read, _end_parking = await _join_and_read(ws, _run.get("session_id", session_id))
    try:
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": _with_client_request_id({
                **_first_read,
                "session_id": _run.get("session_id", session_id),
                "msg_id": _run.get("msg_id", ""),
                "execution_id": _run["execution_id"],
                "function_run": True,
            }),
        }))
    finally:
        _end_parking()


async def _run_local_command(ws, req: _ChatRequest) -> bool:
    """Run a local builtin command, if the text is one.

    Returns ``False`` when the command finished the turn; ``True`` to go on
    with a model turn (``req.text`` may now be the command's ``send_text``).
    """
    text = req.text
    session_id = req.session_id
    if not text.startswith("/"):
        return True
    _local_res = _resolve_local_command(text, session_id)
    if _local_res is None:
        return True
    _validated_surface = await _validate_local_surface(
        ws, req.surface_ref, session_id,
    )
    if _validated_surface is _STALE:
        return False
    from openprogram.runtime.conversation.turn_admission import local_commands
    # The command runs on the branch it was sent on.
    _outcome = await asyncio.to_thread(local_commands.run, _local_res, session_id,
                                       branch_id=req.side_branch_id)
    _out = {"text": _outcome.text, "invoke": _outcome.invoke, "send_text": _outcome.send_text}
    _reply_text = str(_out.get("text") or "")
    if _outcome.starts_goal:
        # The message that sets a Goal is labelled Goal in the transcript;
        # it says what a reply line would, so none is sent.
        req.starts_goal = True
        _reply_text = ""
    if _reply_text:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": _with_client_request_id({"type": "local_command",
                                             "session_id": session_id,
                                             "command": _local_res.command_name,
                                             "content": _reply_text}),
        }))
    _invoke = _out.get("invoke")
    if isinstance(_invoke, dict):
        await _run_local_invocation(ws, _invoke, session_id, _validated_surface)
        return False
    _send_text = _out.get("send_text")
    if not _send_text:
        return False
    req.text = str(_send_text)
    return True
