"""The ``chat`` action's first phases: parse the command into a turn request.

``_ChatRequest`` carries what the client asked for between the phases of
:func:`~.turn.handle_chat`; later phases update ``text``, the tool flags and
the attachments in place as the message is expanded and persisted.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from .message_input import _with_recording


# The ``client_request_id`` of the ``chat`` command being handled. Every
# frame that answers the send before its ``chat_ack`` (and the ack itself)
# echoes it, so a client rejects only the pending send it belongs to.
_client_request_id: ContextVar[str | None] = ContextVar(
    "chat_client_request_id", default=None,
)


def client_request_id_of(cmd: dict) -> str | None:
    value = cmd.get("client_request_id")
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _with_client_request_id(data: dict) -> dict:
    request_id = _client_request_id.get()
    if request_id is not None:
        data["client_request_id"] = request_id
    return data


def _chat_error_data(session_id: str | None, code: str, content: str, *,
                     retry_query: str | None = None, **extra: Any) -> dict:
    """The ``chat_response`` error payload that ends a chat turn early.

    The client resolves its pending user bubble only from a frame that
    names the session and carries a message id, so every turn-ending
    error has both. The id is fresh: no reply row stands behind it.
    """
    data = {
        "type": "error",
        "session_id": session_id,
        "msg_id": uuid.uuid4().hex,
        "code": code,
        "content": content,
        "timestamp": time.time(),
        **extra,
    }
    if retry_query is not None:
        data["retry_query"] = retry_query
    return _with_client_request_id(data)


async def _send_chat_error(ws, session_id: str | None, code: str, content: str, *,
                           retry_query: str | None = None, **extra: Any) -> None:
    await ws.send_text(json.dumps({
        "type": "chat_response",
        "data": _chat_error_data(
            session_id, code, content, retry_query=retry_query, **extra,
        ),
    }, default=str))


@dataclass
class _ChatRequest:
    cmd: dict
    text: str
    session_id: str | None
    agent_id: str | None
    thinking_effort: str | None
    tools_flag: list | None
    web_search_flag: bool | None
    permission_mode: str | None
    sandbox_flag: Any
    surface_ref: dict | None
    service_tier: str | None
    response_format: Any
    attachments: list | None
    recording_meta: Any = None
    recording_images: list = field(default_factory=list)
    agent_invocation: dict | None = None
    requested_environment: str | None = None
    project_id: str | None = None
    # Server-only guard for a draft that materializes during first-send admission.
    initial_branch_id: str | None = None
    # The attachments the user sent, before recording frames are appended.
    user_attachments: list | None = None
    # A ``/skill`` message's SKILL.md activation block, read by the model
    # only; the typed message stays the visible one.
    skill_name: str | None = None
    skill_activation: str | None = None
    # A recorded branch that is not the selected one, named by the message's
    # ``branch_id``: the turn runs there, beside the selected branch.
    side_branch_id: str | None = None
    # This turn's own model, effort and permission mode (``turn_*`` keys,
    # sent by ``openprogram --print``): never saved on the branch.
    turn_model: str | None = None
    turn_effort: str | None = None
    turn_permission_mode: str | None = None
    # ``openprogram --print --no-web``: no web tool and no provider search
    # this turn.
    turn_no_web: bool | None = None
    # The message sets a Goal (``/goal <objective>``): written on its user
    # message as ``starts_goal``.
    starts_goal: bool = False


@dataclass(frozen=True)
class TurnOverride:
    """A setting a ``chat`` frame may override for its turn.

    A frame that leaves the key out, or sends ``None``, changes nothing: the
    turn runs with the conversation's own value (the CLI and the TUI send
    only what the person chose; the Web composer sends every setting).
    ``read`` turns a sent value into the request's value, ``None`` again
    meaning unchanged. ``sticky`` settings are saved on the branch the
    message goes to and apply to its later turns too; the others apply to
    this turn only.
    """

    attribute: str
    key: str
    read: Callable[[Any], Any]
    sticky: bool


def _permission_mode(value: Any) -> Any:
    from openprogram.runtime.permissions.modes import normalize_permission

    if isinstance(value, str) and value.strip().lower() in ("", "inherit"):
        return None
    # An illegal value is treated like no value: the turn inherits.
    return normalize_permission(value)


def _turn_model(value: Any) -> Any:
    """``provider/id``, else ``None`` (an illegal value changes nothing)."""
    if not isinstance(value, str):
        return None
    provider, sep, model_id = value.strip().partition("/")
    return f"{provider.strip()}/{model_id.strip()}" if sep and provider.strip() and model_id.strip() else None


#: Every per-turn override a ``chat`` frame carries, in one place.
TURN_OVERRIDES: tuple[TurnOverride, ...] = (
    TurnOverride("thinking_effort", "thinking_effort", lambda value: value or None, True),
    TurnOverride("web_search_flag", "web_search", bool, True),
    TurnOverride("permission_mode", "permission_mode", _permission_mode, True),
    TurnOverride("sandbox_flag", "sandbox_enabled", lambda value: value, True),
    # Speed / priority tier from the composer's Fast control ("priority" =
    # Fast, "default" = standard).
    TurnOverride("service_tier", "service_tier", lambda value: value or None, True),
    # The desktop surface the message was sent from.
    TurnOverride("surface_ref", "surface",
                 lambda value: value if isinstance(value, dict) else None, False),
    # A one-shot run's own choices (``openprogram --print --model / --effort
    # / --permission-mode``): this turn only, whatever the branch has saved.
    TurnOverride("turn_model", "turn_model", _turn_model, False),
    TurnOverride("turn_effort", "turn_effort", lambda value: str(value) if value else None, False),
    TurnOverride("turn_permission_mode", "turn_permission_mode", _permission_mode, False),
    TurnOverride("turn_no_web", "turn_no_web", lambda value: True if value is True else None, False),
)


def read_turn_overrides(cmd: dict) -> dict[str, Any]:
    """The request values of :data:`TURN_OVERRIDES` a frame carries, ``None``
    for each it leaves out."""
    return {
        override.attribute: (None if cmd.get(override.key) is None
                             else override.read(cmd[override.key]))
        for override in TURN_OVERRIDES
    }


async def _parse_chat_command(ws, cmd: dict) -> _ChatRequest | None:
    """Read the composer's fields. ``None`` means the turn stops here."""
    text = cmd.get("text", "").strip()
    session_id = cmd.get("session_id")
    from openprogram.runtime.conversation.turn_input import INPUT_TOO_LARGE, input_too_large
    too_large = input_too_large(text)
    if too_large:
        # Refused before anything is written; the composer keeps the text.
        await _send_chat_error(ws, session_id, INPUT_TOO_LARGE, too_large)
        return None
    agent_id = cmd.get("agent_id") or None
    # The Agent's configuration decides the tool set; a turn only narrows
    # it to a /skill's allowed-tools (later) or overlays web search.
    tools_flag = None
    overrides = read_turn_overrides(cmd)
    response_format = None
    if cmd.get("response_format") is not None:
        from openprogram.models.providers.structured_output import (
            StructuredOutputError,
            normalize_response_format,
        )
        try:
            response_format = normalize_response_format(cmd["response_format"])
        except StructuredOutputError as exc:
            await _send_chat_error(
                ws, session_id, exc.code, "Structured output request is invalid",
                retry_query=text, issues=exc.issues,
            )
            return None
    raw_attachments = cmd.get("attachments") or None
    attachments = None
    if raw_attachments is not None and (not isinstance(raw_attachments, list) or len(raw_attachments) > 20):
        raise ValueError("Attachments must be a list of at most 20 files")
    if isinstance(raw_attachments, list) and raw_attachments:
        if any(not isinstance(a, dict) or not isinstance(a.get("data"), str)
               for a in raw_attachments):
            raise ValueError("Invalid attachment payload")
        attachments = raw_attachments
    recording_meta = None
    recording_images: list = []
    if cmd.get("recording") is not None:
        text, recording_meta, recording_images = await asyncio.to_thread(
            _with_recording, text, cmd.get("recording"))
    if not text and not attachments:
        return None
    if not text and attachments:
        text = "(see attachment)"
    return _ChatRequest(
        cmd=cmd,
        text=text,
        session_id=session_id,
        agent_id=agent_id,
        tools_flag=tools_flag,
        **overrides,
        response_format=response_format,
        attachments=attachments,
        recording_meta=recording_meta,
        recording_images=recording_images,
    )


async def _prepare_agent_and_environment(ws, req: _ChatRequest) -> bool:
    """Validate the Agent invocation and the environment pick.

    Returns ``False`` after sending the error frame that stops the turn.
    """
    cmd = req.cmd
    from ..agent_invocation import AgentInvocationError, prepare_agent_invocation
    try:
        # It reads the conversation's row under its lock: off the loop.
        req.agent_invocation = await asyncio.to_thread(prepare_agent_invocation, cmd)
    except AgentInvocationError as exc:
        await _send_chat_error(
            ws, req.session_id, exc.code, str(exc), retry_query=req.text,
        )
        return False
    # Environment pick for a brand-new conversation. Validated before any
    # state changes; an existing conversation ignores it (environments are
    # chosen once, at creation).
    requested_environment = (cmd.get("environment_id") or "").strip() or None
    req.requested_environment = requested_environment
    if requested_environment is not None:
        from openprogram.host.environments import get_environment
        if get_environment(requested_environment) is None:
            await _send_chat_error(
                ws, req.session_id, "unknown_environment",
                (f"Environment {requested_environment!r} no longer exists. "
                 "Pick another one in the composer."),
                retry_query=req.text,
            )
            return False
    if req.agent_invocation is not None:
        # A new Agent conversation starts from its configuration. Unrelated
        # composer defaults must not replace the Agent's effort or tools.
        req.thinking_effort = (
            req.agent_invocation["profile_snapshot"].get("thinking_effort") or None
        )
        req.tools_flag = None
        req.web_search_flag = None
    return True
