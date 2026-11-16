"""OpenAI-compatible endpoints: an OpenProgram Agent as a model for other tools.

``GET /v1/models`` lists the Agents; ``POST /v1/chat/completions`` runs one
Agent turn and answers in OpenAI's Chat Completions shape, whole or as
server-sent ``chat.completion.chunk`` events ending in ``data: [DONE]``.
Both sit on the Web server's port behind its owner authentication: the
caller sends the server's Web token as ``Authorization: Bearer``, exactly
as any other API client of the server does, and a refusal there comes back
in OpenAI's error shape (``auth_error_body``).

The request's ``model`` names the Agent. Each request starts a conversation
of that Agent, as Hermes Agent's and OpenClaw's endpoints do: the last user
message is the turn's input and the messages before it (instructions, earlier
user and assistant turns) are given to the turn as the caller's context
(:func:`turn_text`). A request that names an earlier conversation in the
``conversation_id`` extension field adds a turn to it instead, and only its
last user message is sent; every answer names its conversation in the same
field and in the ``X-OpenProgram-Conversation-Id`` header.

The turn is the conversation's normal turn (``owner_turn.start_owner_turn``):
it takes the conversation's run slot, runs the Agent's own model, tools and
permission mode, is stored in the conversation and shows in the App while it
runs. No one is behind the request to answer an approval or a question, so it
is admitted non-interactive, as ``openprogram --print`` is: an operation that
would ask is refused at once. A streamed request whose client goes away
cancels its turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

_log = logging.getLogger(__name__)

#: The ``source`` of a turn this endpoint starts.
SOURCE = "api"

#: The extension field (request and answer) and header naming the conversation.
CONVERSATION_FIELD = "conversation_id"
CONVERSATION_HEADER = "X-OpenProgram-Conversation-Id"

_CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
_ROLE_LABELS = {"system": "Instructions", "developer": "Instructions",
                "user": "User", "assistant": "Assistant", "tool": "Tool result"}


class OpenAIError(Exception):
    """A request answered with OpenAI's error body."""

    def __init__(self, status: int, message: str, *, type_: str = "invalid_request_error",
                 code: str | None = None, param: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = type_
        self.code = code
        self.param = param

    def body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type,
                          "param": self.param, "code": self.code}}

    def response(self, conversation_id: str | None = None) -> JSONResponse:
        headers = {CONVERSATION_HEADER: conversation_id} if conversation_id else None
        return JSONResponse(self.body(), status_code=self.status, headers=headers)


_AUTH_ERRORS = {
    "authentication_required": OpenAIError(
        401, "Missing or invalid API key. Send the OpenProgram Web token as "
             "'Authorization: Bearer <token>'.",
        type_="authentication_error", code="invalid_api_key"),
    "request_origin_rejected": OpenAIError(
        403, "The request's Host or Origin is not one this OpenProgram server accepts.",
        type_="permission_error", code="origin_rejected"),
}


def auth_error_body(error: str) -> bytes:
    """The body of an owner-authentication refusal of a ``/v1`` request."""
    refusal = _AUTH_ERRORS.get(error) or OpenAIError(
        400, error.replace("_", " "), code=error)
    return json.dumps(refusal.body(), separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def content_text(content: Any, param: str) -> str:
    """The text of one message's ``content``: a string or a list of text parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise OpenAIError(400, "content must be a string or a list of content parts",
                          param=param)
    parts: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict) or part.get("type") not in ("text", "input_text"):
            kind = part.get("type") if isinstance(part, dict) else type(part).__name__
            raise OpenAIError(400, f"content part type {kind!r} is not supported; "
                                   "send text parts only", param=f"{param}[{index}]")
        text = part.get("text")
        if not isinstance(text, str):
            raise OpenAIError(400, "a text part needs a string 'text'",
                              param=f"{param}[{index}].text")
        parts.append(text)
    return "\n".join(parts)


def turn_text(messages: Any, *, continuing: bool) -> str:
    """The turn's input for ``messages``.

    The last message must be the user's; it is the turn's message. A new
    conversation also gets every earlier message, labelled by role, ahead of
    it: the calling tool's instructions and the turns it already had. A
    continued conversation has its own history, so only the last message is
    sent.
    """
    if not isinstance(messages, list) or not messages:
        raise OpenAIError(400, "messages must be a non-empty list", param="messages")
    rendered: list[tuple[str, str]] = []
    for index, message in enumerate(messages):
        param = f"messages[{index}]"
        if not isinstance(message, dict):
            raise OpenAIError(400, "each message must be an object", param=param)
        role = message.get("role")
        if role not in _ROLES:
            raise OpenAIError(400, f"unknown message role {role!r}", param=f"{param}.role")
        rendered.append((role, content_text(message.get("content"), f"{param}.content")))
    role, last = rendered[-1]
    if role != "user":
        raise OpenAIError(400, "the last message must be the user's",
                          param=f"messages[{len(messages) - 1}].role")
    if not last.strip():
        raise OpenAIError(400, "the last user message has no text",
                          param=f"messages[{len(messages) - 1}].content")
    earlier = [(r, t) for r, t in rendered[:-1] if t.strip()]
    if continuing or not earlier:
        return last
    instructions = [t.strip() for r, t in earlier if r in ("system", "developer")]
    history = [f"{_ROLE_LABELS[r]}: {t.strip()}" for r, t in earlier
               if r not in ("system", "developer")]
    blocks: list[str] = []
    if instructions:
        blocks.append("Instructions from the application sending this message:\n"
                      + "\n\n".join(instructions))
    if history:
        blocks.append("The conversation so far in that application:\n"
                      + "\n\n".join(history))
    blocks.append("Current message:\n" + last)
    return "\n\n".join(blocks)


def _agent_ids() -> list[str]:
    from openprogram.runtime.agent_registry import manager

    default = manager.get_default()
    ids = manager.list_ids()
    if default is not None and default.id in ids:
        ids = [default.id, *(i for i in ids if i != default.id)]
    return ids


def resolve_target(model: Any, conversation_id: Any) -> tuple[str, str | None]:
    """The Agent and, when the request continues one, the conversation."""
    if model is not None and not isinstance(model, str):
        raise OpenAIError(400, "model must be a string", param="model")
    model = (model or "").strip()
    if conversation_id is not None:
        if not isinstance(conversation_id, str) or not _CONVERSATION_ID.fullmatch(conversation_id):
            raise OpenAIError(400, "conversation_id is not a conversation id",
                              param=CONVERSATION_FIELD)
        from openprogram.runtime.context.session_db import default_db

        row = default_db().get_session(conversation_id)
        if not isinstance(row, dict):
            raise OpenAIError(404, f"no conversation {conversation_id!r}",
                              param=CONVERSATION_FIELD, code="conversation_not_found")
        agent_id = str(row.get("agent_id") or "")
        if model and model != agent_id:
            raise OpenAIError(400, f"conversation {conversation_id!r} belongs to the "
                                   f"Agent {agent_id!r}, not {model!r}", param="model")
        return agent_id, conversation_id
    if not model:
        raise OpenAIError(400, "model is required: name an Agent from GET /v1/models",
                          param="model")
    if model not in _agent_ids():
        raise OpenAIError(404, f"the model {model!r} does not exist; the models are "
                               "this server's Agents (GET /v1/models)",
                          param="model", code="model_not_found")
    return model, None


def _new_conversation(agent_id: str) -> str:
    from openprogram.runtime.context.session_db import default_db

    db = default_db()
    for _ in range(8):
        candidate = f"api_{uuid.uuid4().hex[:16]}"
        if db.get_session(candidate) is None:
            db.create_session(candidate, agent_id, title="", source=SOURCE)
            return candidate
    raise OpenAIError(500, "no conversation could be created", type_="api_error")


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------

def _text_delta(frame: Any, session_id: str, own_ids: frozenset) -> str | None:
    """The reply text a turn frame adds (with the blank line that opens a
    passage after a tool call), if it is one of this turn's own.

    A frame of another message in the same conversation (a sub-agent branch
    the turn started, say) is not this reply, as for a channel's bound reply
    (``bound_reply.BoundReply._observe``).
    """
    if not isinstance(frame, dict) or frame.get("type") != "chat_response":
        return None
    data = frame.get("data") or {}
    if (data.get("type") != "stream_event"
            or data.get("session_id") not in (None, session_id)
            or data.get("msg_id") not in own_ids):
        return None
    event = data.get("event") or {}
    if event.get("type") != "text":
        return None
    from openprogram.state.reply_text import stream_text
    return stream_text(event) or None


class TurnRun:
    """One turn started for a request, read back on the request's event loop."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        # The turn's user message id; its reply is ``<id>_reply``.
        self.msg_id = uuid.uuid4().hex
        self.own_ids = frozenset({None, self.msg_id, f"{self.msg_id}_reply"})
        self.execution_id: str | None = None
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.done = False
        # The request has its answer or its client went: nothing reads on.
        self.left = False

    def _put(self, item: tuple[str, Any]) -> None:
        if self.left:
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, item)
        except RuntimeError:
            # The request's event loop is gone: so is its reader.
            self.left = True

    def on_event(self, frame: dict) -> None:
        text = _text_delta(frame, self.session_id, self.own_ids)
        if text:
            self._put(("text", text))

    def on_done(self, result: Any) -> None:
        self._put(("done", result))

    async def events(self) -> AsyncIterator[tuple[str, Any]]:
        while not self.done:
            kind, value = await self.queue.get()
            if kind == "done":
                self.done = True
            yield kind, value

    def leave(self) -> None:
        """The request is over; a turn still running lost its client and stops."""
        self.left = True
        if self.done or not self.execution_id:
            return
        execution_id = self.execution_id

        def stop() -> None:
            from openprogram.runtime.execution.run_control import cancel_execution_for_reason

            try:
                cancel_execution_for_reason(execution_id, "cancel.api_client_gone")
            except Exception:  # noqa: BLE001 - already ended
                _log.debug("API turn %s not cancelled", execution_id, exc_info=True)

        self.loop.run_in_executor(None, stop)


def _start(run: TurnRun, agent_id: str, text: str, authority: dict[str, Any]) -> str:
    from openprogram.runtime.conversation.turn_admission.owner_turn import start_owner_turn
    from openprogram.runtime.conversation.run_control.run_slot import RunSlotBusy
    from openprogram.state.store.session.files.session_lock import SessionLockTimeout

    try:
        return start_owner_turn(
            run.session_id, agent_id, text, name="openai-compatible-turn",
            reason_code="api_turn_activation_failed", on_done=run.on_done, msg_id=run.msg_id,
            source=SOURCE, authority=authority, unattended=True, on_event=run.on_event,
        )
    except RunSlotBusy as exc:
        raise OpenAIError(409, f"conversation {run.session_id!r} is already running a turn",
                          param=CONVERSATION_FIELD, code="conversation_busy") from exc
    except SessionLockTimeout as exc:
        # Nothing was admitted: the conversation was held (a long load or a
        # move), not running. The same request can be sent again.
        raise OpenAIError(503, f"conversation {run.session_id!r} is locked; try again",
                          type_="api_error", param=CONVERSATION_FIELD,
                          code="conversation_locked") from exc


def result_error(result: Any) -> OpenAIError | None:
    """The error a finished turn answers with, or ``None`` when it replied."""
    if result is None:
        return OpenAIError(500, "the turn ended without a result",
                           type_="api_error", code="turn_failed")
    if getattr(result, "_execution_safe_point_handoff", False):
        return OpenAIError(409, "the turn stopped to wait for an answer; continue it in OpenProgram",
                           type_="api_error", code="turn_waiting")
    if not getattr(result, "failed", False):
        return None
    message = str(result.error or result.error_reason or "the turn failed")
    reason = getattr(result, "error_reason", None)
    if reason == "rate_limit":
        return OpenAIError(429, message, type_="rate_limit_error", code="rate_limit_exceeded")
    if reason == "context":
        return OpenAIError(400, message, code="context_length_exceeded")
    return OpenAIError(502, message, type_="api_error", code=reason or "turn_failed")


def usage(result: Any) -> dict[str, int]:
    raw = getattr(result, "usage", None) or {}

    def tokens(*names: str) -> int:
        for name in names:
            value = raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)
            if isinstance(value, (int, float)) and value:
                return int(value)
        return 0

    prompt = (tokens("input_tokens", "input", "prompt_tokens")
              + tokens("cache_read_tokens", "cache_read")
              + tokens("cache_write_tokens", "cache_write"))
    completion = tokens("output_tokens", "output", "completion_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


def _sse(payload: Any) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


async def _completion(run: TurnRun, model: str, completion_id: str, created: int) -> JSONResponse:
    result = None
    async for kind, value in run.events():
        if kind == "done":
            result = value
    error = result_error(result)
    if error is not None:
        return error.response(run.session_id)
    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.final_text or ""},
            "finish_reason": "stop",
        }],
        "usage": usage(result),
        CONVERSATION_FIELD: run.session_id,
    }, headers={CONVERSATION_HEADER: run.session_id})


async def _chunks(run: TurnRun, model: str, completion_id: str, created: int,
                  include_usage: bool) -> AsyncIterator[bytes]:
    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": model, CONVERSATION_FIELD: run.session_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    sent = ""
    try:
        yield _sse(chunk({"role": "assistant", "content": ""}))
        result = None
        async for kind, value in run.events():
            if kind == "text":
                sent += value
                yield _sse(chunk({"content": value}))
            else:
                result = value
        error = result_error(result)
        if error is not None:
            yield _sse(error.body())
        else:
            final = result.final_text or ""
            # Text the turn adds to its reply after streaming (attachments).
            if final.startswith(sent) and len(final) > len(sent):
                yield _sse(chunk({"content": final[len(sent):]}))
            yield _sse(chunk({}, "stop"))
            if include_usage:
                yield _sse({"id": completion_id, "object": "chat.completion.chunk",
                            "created": created, "model": model, "choices": [],
                            "usage": usage(result), CONVERSATION_FIELD: run.session_id})
        yield _sse("[DONE]")
    finally:
        run.leave()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(app) -> None:
    @app.get("/v1/models")
    async def list_models():
        from openprogram.runtime.agent_registry import manager

        def read() -> list[dict[str, Any]]:
            rows = []
            for agent_id in _agent_ids():
                try:
                    spec = manager.get(agent_id)
                except Exception:  # noqa: BLE001 - one unreadable Agent is left out
                    _log.warning("Agent %s not listed", agent_id, exc_info=True)
                    continue
                if spec is None:
                    continue
                rows.append({"id": spec.id, "object": "model",
                             "created": int(spec.created_at or 0), "owned_by": "openprogram",
                             "name": spec.name or spec.id})
            return rows

        try:
            rows = await asyncio.to_thread(read)
        except Exception:  # noqa: BLE001 - the registry unreadable: OpenAI's shape still
            _log.exception("Agents not listed for /v1/models")
            return OpenAIError(500, "the Agents could not be listed", type_="api_error",
                               code="internal_error").response()
        return JSONResponse({"object": "list", "data": rows})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        from openprogram.state.store.session.files.session_lock import SessionLockTimeout

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any unreadable body
            return OpenAIError(400, "the request body is not JSON").response()
        if not isinstance(body, dict):
            return OpenAIError(400, "the request body must be a JSON object").response()
        conversation_id = body.get(CONVERSATION_FIELD)
        try:
            n = body.get("n", 1)
            if n not in (None, 1):
                raise OpenAIError(400, "only n=1 is supported", param="n")
            stream = body.get("stream") is True
            options = body.get("stream_options")
            include_usage = isinstance(options, dict) and options.get("include_usage") is True
            # A named conversation is read, and the turn admitted, under its
            # lock: in its lane, so requests queued on one held conversation
            # never take the threads the others need.
            from openprogram.state.store.session.conversation_work import run_for_conversation

            agent_id, conversation_id = await run_for_conversation(
                conversation_id if isinstance(conversation_id, str) else None,
                resolve_target, body.get("model"), conversation_id)
            text = turn_text(body.get("messages"), continuing=conversation_id is not None)
            from openprogram.runtime.conversation.turn_admission.turn_input import input_too_large

            too_large = input_too_large(text)
            if too_large:
                raise OpenAIError(400, too_large, param="messages", code="input_too_large")
            session_id = conversation_id or await asyncio.to_thread(_new_conversation, agent_id)
            authority = dict((request.scope.get("state") or {}).get("authority") or {})
            if not authority:
                from openprogram.runtime.permissions.authority import local_owner_authority

                authority = local_owner_authority()
            authority["interaction"] = "non-interactive"
            run = TurnRun(session_id)
            run.execution_id = await run_for_conversation(
                session_id, _start, run, agent_id, text, authority)
        except OpenAIError as error:
            return error.response(conversation_id if isinstance(conversation_id, str) else None)
        except SessionLockTimeout:
            # Reading the conversation waited out its lock: nothing started.
            return OpenAIError(503, f"conversation {conversation_id!r} is locked; try again",
                               type_="api_error", param=CONVERSATION_FIELD,
                               code="conversation_locked").response(
                conversation_id if isinstance(conversation_id, str) else None)
        except Exception:  # noqa: BLE001 - any other failure still answers in OpenAI's shape
            _log.exception("OpenAI-compatible request failed before its turn started")
            return OpenAIError(500, "the request could not be started", type_="api_error",
                               code="internal_error").response(
                conversation_id if isinstance(conversation_id, str) else None)
        completion_id = f"chatcmpl-{run.execution_id or uuid.uuid4().hex}"
        created = int(time.time())
        if not stream:
            try:
                return await _completion(run, agent_id, completion_id, created)
            finally:
                run.leave()
        return StreamingResponse(
            _chunks(run, agent_id, completion_id, created, include_usage),
            media_type="text/event-stream",
            headers={CONVERSATION_HEADER: session_id, "Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"},
        )

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   include_in_schema=False)
    async def unknown_endpoint(rest: str, request: Request):
        # Registered after the two routes above, so it answers only the rest of
        # /v1 (``/v1/responses``, ``/v1/embeddings``, a wrong method), in
        # OpenAI's shape rather than as the App's page or FastAPI's 404/405.
        return OpenAIError(404, f"{request.method} /v1/{rest} is not served; this server "
                                "offers GET /v1/models and POST /v1/chat/completions",
                           code="unknown_url").response()


__all__ = [
    "CONVERSATION_FIELD", "CONVERSATION_HEADER", "OpenAIError", "SOURCE", "auth_error_body",
    "content_text", "register", "resolve_target", "result_error", "turn_text", "usage",
]
