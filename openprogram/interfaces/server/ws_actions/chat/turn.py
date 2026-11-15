"""The ``chat`` action: admission, reservation and turn execution.

:func:`handle_chat` runs the turn as a sequence of phases. Parsing lives in
``turn_request``, the ``/skill`` expansion in ``turn_skill``, local builtin
commands in ``turn_local_command`` and the running-task hand-off and run
thread in ``turn_launch``; this module keeps the session, reservation,
run-config and admission phases.
"""
from __future__ import annotations

import logging
import json
import time
import uuid

from openprogram.state.store.session.conversation_work import run_for_conversation

from .session_agent import _db_agent_id
from .context_usage import _record_context_usage
from .attachments import _persist_attachments_async
from .attachments import _attachments_for_dispatch
from .message_input import _title_from_text
from .message_input import _pasted_text_elements
from .turn_request import _ChatRequest
from .turn_request import _send_chat_error
from .turn_request import _client_request_id
from .turn_request import _with_client_request_id
from .turn_request import client_request_id_of
from .turn_request import _parse_chat_command
from .turn_request import _prepare_agent_and_environment
from .turn_skill import _expand_skill_command
from .turn_skill import _is_skill_command
from .turn_skill import _skill_model_text
from .turn_local_command import _run_local_command
from .turn_launch import _AdmittedTurn
from .turn_launch import _publish_running_task
from .turn_launch import _start_run_thread

_log = logging.getLogger(__name__)


async def handle_chat(ws, cmd: dict):
    token = _client_request_id.set(client_request_id_of(cmd))
    try:
        await _handle_chat(ws, cmd)
    finally:
        _client_request_id.reset(token)


async def _handle_chat(ws, cmd: dict):
    from openprogram.interfaces.server import server as _s
    req = await _parse_chat_command(ws, cmd)
    if req is None:
        return
    from openprogram.interfaces.server.branch_settings import check_branch
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    try:
        # Reads the conversation's branch record under its lock: off the loop.
        await run_for_conversation(req.session_id, check_branch, req.session_id, cmd)
    except BranchConfigConflict:
        await _send_chat_error(ws, req.session_id, "branch_config_conflict",
                               "The selected branch changed. Reload it before sending.")
        return
    if not await _prepare_agent_and_environment(ws, req):
        return
    # Session open, run config, admission and the user-message commit read
    # and write the session repo and the execution store. They run off the
    # event loop so one send never stalls every other socket.
    conv = await run_for_conversation(req.session_id, _open_session, _s, req)
    if not await _resolve_side_branch(ws, req):
        return
    if not _is_skill_command(req.text) and not await _run_local_command(ws, req):
        return
    msg_id = await _reserve_run(ws, _s, req)
    if msg_id is None:
        return
    if not await _admit_skill_command(ws, _s, req, msg_id):
        return
    try:
        run_cfg, effective_permission = await run_for_conversation(req.session_id, 
            _save_run_config, _s, req, conv, msg_id,
        )
    except BranchConfigConflict:
        await _send_chat_error(ws, req.session_id, "branch_config_conflict",
                               "The selected branch changed. Reload it before sending.")
        return
    await _persist_turn_attachments(_s, req, msg_id)
    from openprogram.runtime.conversation.production_driver import AgentDriverError
    try:
        turn, user_msg = await run_for_conversation(req.session_id, 
            _admit_and_commit, _s, ws, req, conv, msg_id, run_cfg, effective_permission,
        )
    except AgentDriverError as refused:
        # Admission refused the input (too large, a bad image): the turn
        # ends with that reason, not a bare ``handler_error``. Nothing was
        # written and the reservation is already released (``_admit_turn``).
        await _send_chat_error(ws, req.session_id, refused.code, str(refused),
                               kind=refused.code)
        return
    _broadcast_user_message(_s, ws, turn, user_msg)
    await _send_chat_ack(ws, req, turn, effective_permission)
    # Its run-state broadcast reads the conversation's records: off the loop.
    await run_for_conversation(req.session_id, _publish_running_task, _s, turn)
    # A start that fails releases the slot, whose clear reads the selected
    # branch under the conversation's lock: in its lane too.
    await run_for_conversation(req.session_id, _start_run_thread, _s, turn)


def _admit_and_commit(_s, ws, req: _ChatRequest, conv: dict, msg_id: str,
                      run_cfg, effective_permission):
    """Admit the turn and commit its user message; ``(turn, user_msg)``."""
    _backfill_title(req)
    parsed, local_authority, surface_context, release_surface = (
        _capture_turn_inputs(_s, ws, req, msg_id)
    )
    user_msg = _build_user_msg(req, msg_id, parsed, local_authority)
    turn = _admit_turn(
        _s, req, conv, msg_id, run_cfg, effective_permission,
        parsed, local_authority, surface_context, release_surface,
    )
    _commit_user_message(_s, req, turn, user_msg)
    return turn, user_msg


def _broadcast_user_message(_s, ws, turn: _AdmittedTurn, user_msg: dict) -> None:
    """Show the committed message in every other tab on this session.

    The sender already holds the bubble and learns its id from
    ``chat_ack``, so it is left out.
    """
    try:
        _s._broadcast(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "user_message",
                "session_id": turn.session_id,
                "msg_id": turn.msg_id,
                "content": user_msg.get("content") or "",
                "source": user_msg.get("source"),
                "timestamp": user_msg.get("timestamp"),
                "predecessor": user_msg.get("predecessor"),
                **({"branch_id": turn.branch_id} if turn.branch_id else {}),
            },
        }, default=str), exclude=ws)
    except Exception:
        # Peers catch up on their next load; the turn itself is committed.
        _log.debug(
            "user_message not broadcast for session %s", turn.session_id, exc_info=True)


def _open_session(_s, req: _ChatRequest) -> dict:
    from openprogram.interfaces.server.branch_settings import prepare_initial_guard, materialized_guard
    if req.session_id is None:
        req.session_id = "local_" + uuid.uuid4().hex[:10]
    req.initial_branch_id = prepare_initial_guard(req.session_id, req.cmd)
    cmd = req.cmd
    new_channel = (cmd.get("channel") or "").strip().lower() or None
    new_account_id = (cmd.get("account_id") or "").strip() or None
    new_peer = (cmd.get("peer") or "").strip() or None
    conv = _s._get_or_create_session(
        req.session_id,
        agent_id=req.agent_id,
        channel=new_channel,
        account_id=new_account_id,
        peer=new_peer,
    )
    session_id = req.session_id = conv["id"]

    # Project binding MUST happen before the first DB write below (the
    # title backfill / run-config / _append_msg all update_session with
    # create_if_missing=True, which would materialise the session repo
    # at the home root). create_session is the only path that can place
    # the repo inside the project (<project>/.openprogram/sessions/<id>/),
    # so when the composer sent the picker's project_id with the first
    # message, create the session with it right here. A draft whose
    # settings were already stored (a model pick) has a repo but is not
    # created yet, so the check is ``is_created``. Existing sessions
    # are untouched — mid-chat project switches go through
    # set_session_project and never move the repo.
    project_id = req.project_id = (cmd.get("project_id") or "").strip() or None
    if project_id:
        try:
            from openprogram.runtime.context.session_db import default_db as _proj_db
            _pdb = _proj_db()
            if not _pdb.is_created(session_id):
                _pdb.create_session(
                    session_id,
                    req.agent_id or _s._default_agent_id(),
                    project_id=project_id,
                )
        except Exception:
            # The turn proceeds; the first save creates the row without the
            # project.
            _log.warning(
                "project session row not created for %s", session_id, exc_info=True)
    req.cmd = materialized_guard(session_id, req.cmd, req.initial_branch_id)
    return conv


#: Commands that act on the conversation as a whole, not on one branch:
#: they run only on the selected branch. ``/goal`` counts only when it
#: starts or clears a Goal; reading or steering the existing one is fine.
CONVERSATION_WIDE_COMMANDS = frozenset({"clear", "goal", "rename"})
_GOAL_ON_EXISTING = frozenset({"", "status", "pause", "resume", "verify", "budget", "edit"})


def conversation_wide_command(text: str) -> bool:
    head, _, rest = (text or "").strip().partition(" ")
    name = head[1:].lower() if head.startswith("/") else ""
    if name not in CONVERSATION_WIDE_COMMANDS:
        return False
    if name == "goal":
        verb = rest.strip().split(" ", 1)[0].lower() if rest.strip() else ""
        return verb not in _GOAL_ON_EXISTING
    return True


async def _resolve_side_branch(ws, req: _ChatRequest) -> bool:
    """Find the unselected branch a message names; ``False`` refuses it.

    A message to a recorded branch that is not selected runs on that branch
    beside the selected one, commands, skills and Agent calls included: a
    branch's todo board and Goal are its own, so nothing it does overwrites
    another branch's. Only a command that acts on the whole conversation
    (``CONVERSATION_WIDE_COMMANDS``) stays with the selected branch.
    """
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    from openprogram.interfaces.server.server_runtime.session_lifecycle import side_branch_target
    try:
        req.side_branch_id = await run_for_conversation(req.session_id, side_branch_target, req.session_id, req.cmd)
    except BranchConfigConflict:
        await _send_chat_error(ws, req.session_id, "branch_gone",
                               "The branch no longer exists; reload the conversation.")
        return False
    if req.side_branch_id is None:
        return True
    if req.text.startswith("/") and conversation_wide_command(req.text):
        await _send_chat_error(
            ws, req.session_id, "side_branch_refused",
            "This command acts on the whole conversation; select this branch to use it.")
        return False
    return True


async def _reserve_run(ws, _s, req: _ChatRequest) -> str | None:
    """Reserve the session's run slot; the turn's message id, or ``None``."""
    # Run-active guard — the last unguarded HEAD-moving entry point
    # (fn-form dispatch, retry/edit, checkout, merge, rewind all check
    # _is_run_active already). Without it, two clients racing on one
    # session dispatch two turns that _append_msg + advance the same
    # HEAD concurrently and interleave the conversation chain. The
    # frontend routes to either its queue or the explicit ``steer`` action
    # when it knows a run is active; this covers the race window it cannot
    # see. A concurrent ordinary ``chat`` remains a queue fallback, never an
    # implicit steer, so the user's selected running-message mode stays exact.
    session_id = req.session_id
    msg_id = uuid.uuid4().hex
    # The slot keeps the send's id: a repeat of a send that holds it is
    # that send, answered ``request_admitted``, never queued as a new one.
    request_id = client_request_id_of(req.cmd)
    key = {"key": f"send:{request_id}"} if request_id else {}
    from openprogram.state.store.session.session_lock import SessionLockTimeout
    if req.side_branch_id:
        # A branch that is not selected has its own slot: one turn on it at
        # a time, whatever the selected branch is doing.
        try:
            reserved = await run_for_conversation(session_id, lambda: _s._try_reserve_branch_run(
                session_id, req.side_branch_id, msg_id, **key))
        except SessionLockTimeout as exc:
            await _send_locked_refusal(ws, _s, req, exc, branch_id=req.side_branch_id)
            return None
        if not reserved:
            if await _send_repeat_refusal(ws, _s, session_id, key):
                return None
            await _send_chat_error(
                ws, session_id, "run_active", _s.RUN_ACTIVE_ERROR,
                retry_query=req.text, display="chat", branch_id=req.side_branch_id,
            )
            return None
        return msg_id
    from openprogram.programs.tools.planning.goal.chat import note_user_waiting
    try:
        reserved = await run_for_conversation(session_id, lambda: _s._try_reserve_run(session_id, msg_id, **key))
    except SessionLockTimeout as exc:
        await _send_locked_refusal(ws, _s, req, exc)
        return None
    if not reserved:
        if await _send_repeat_refusal(ws, _s, session_id, key):
            return None
        # The client re-queues this message; an automatic Goal turn yields to it.
        note_user_waiting(session_id, True)
        await _send_chat_error(
            ws, session_id, "run_active", _s.RUN_ACTIVE_ERROR,
            retry_query=req.text, display="chat",
        )
        return None
    return msg_id


async def _send_locked_refusal(ws, _s, req: _ChatRequest, exc, **extra) -> None:
    """The conversation's lock was not had in time: nothing runs that the
    message could queue behind, so it is not answered ``run_active``. The
    client keeps the text to send again (``retry_query``)."""
    from openprogram.interfaces.server.server_runtime.run_slots import locked_refusal
    refusal = locked_refusal(exc.waited, exc.holder)
    _log.warning("send to %s not started: %s", req.session_id, exc)
    await _send_chat_error(
        ws, req.session_id, refusal["code"], refusal["error"],
        retry_query=req.text, display="chat", retryable=True, **extra,
    )


async def _send_repeat_refusal(ws, _s, session_id: str, key: dict) -> bool:
    """Answer a send whose own id holds the slot; ``False`` when it does not."""
    refusal = await run_for_conversation(session_id, 
        _s.repeated_request_refusal, session_id, key.get("key"),
    ) if key else None
    if refusal is None:
        return False
    await _send_chat_error(
        ws, session_id, "request_admitted", refusal["error"], display="chat",
        **{name: refusal[name] for name in ("execution_id", "branch_id", "op_id") if name in refusal},
    )
    return True


async def _admit_skill_command(ws, _s, req: _ChatRequest, msg_id: str) -> bool:
    """Expand a ``/skill`` message inside the turn's reservation.

    An unknown, ambiguous or blocked skill ends the turn with a local
    reply; it never becomes a model turn about the failure.
    """
    try:
        refusal = await run_for_conversation(req.session_id, _expand_skill_command, req)
    except BaseException:
        await run_for_conversation(req.session_id, _s._release_run_reservation, req.session_id, msg_id)
        raise
    if refusal is None:
        return True
    await run_for_conversation(req.session_id, _s._release_run_reservation, req.session_id, msg_id)
    code, content = refusal
    await ws.send_text(json.dumps({
        "type": "chat_response",
        "data": _with_client_request_id({
            "type": "local_command",
            "session_id": req.session_id,
            "command": "skill",
            "code": code,
            "error": True,
            "content": content,
        }),
    }))
    return False


def _save_run_config(_s, req: _ChatRequest, conv: dict, msg_id: str):
    """Persist the session and its run settings; ``(run_cfg, permission)``."""
    session_id = req.session_id
    try:
        from openprogram.runtime.conversation.session_config import (
            permission_from_config,
            project_defaults,
            save_session_run_config,
        )
        # This is an accepted message, not an unsent composer draft. Persist
        # its session before saving settings so a first-turn Bypass selection
        # (and other run settings) survives refresh without a project binding.
        from openprogram.runtime.context.session_db import default_db
        session_db = default_db()
        from openprogram.interfaces.server.branch_settings import (
            check_branch, branch_guard, prepare_initial_guard, materialized_guard,
        )
        if req.initial_branch_id is None:
            req.initial_branch_id = prepare_initial_guard(session_id, req.cmd)
        req.cmd = materialized_guard(session_id, req.cmd, req.initial_branch_id)
        check_branch(session_id, req.cmd)
        if not session_db.is_created(session_id):
            session_db.create_session(session_id, req.agent_id or _s._default_agent_id(),
                                      project_id=req.project_id)
            _bind_draft_channel(_s, req)
            req.cmd = materialized_guard(session_id, req.cmd, req.initial_branch_id)
        from openprogram.host.environments import assign_new_session_environment
        conv["environment_id"] = assign_new_session_environment(
            session_db, session_id, req.requested_environment)
        if req.agent_invocation is not None:
            from ..agent_invocation import persist_agent_invocation
            persist_agent_invocation(session_id, req.agent_invocation, conv,
                                     branch_id=req.side_branch_id)
        # The composer's values are stored on the branch the message goes to.
        # The Agent is not: it changes only through the Agent switch. A
        # /skill's tool whitelist is not either: it is this turn's alone.
        # A branch whose permission is versioned keeps it; the composer's
        # legacy mode would only be masked by it.
        stored = session_db.get_session(session_id) or {}
        if req.side_branch_id:
            from openprogram.state.store.session.branch_config import config_for_branch
            stored = config_for_branch(stored, req.side_branch_id)
        run_cfg = save_session_run_config(
            session_id,
            # web_search stored as INTENT (not expanded into a list) so
            # the session always follows the live tool set.
            web_search=req.web_search_flag,
            thinking_effort=req.thinking_effort,
            permission_mode=None if stored.get("permission_state") else req.permission_mode,
            service_tier=req.service_tier,
            # 草稿会话（尚无 session_id）在首条消息落地额外工作目录的唯一通道
            # （additional-working-directories.md §3.3）。None = 不动既有配置。
            additional_working_dirs=req.cmd.get("additional_working_dirs"),
            sandbox_enabled=req.sandbox_flag,
            **({"branch_id": req.side_branch_id} if req.side_branch_id else branch_guard(req.cmd)),
        )
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise
    effective_permission = permission_from_config(
        run_cfg, default=project_defaults(session_id).get("permission_mode"))
    if req.side_branch_id:
        # The live mirror shows the selected branch; this turn's branch is another.
        if req.tools_flag is not None:
            run_cfg.tools_override = list(req.tools_flag)
        return run_cfg, effective_permission
    # The mirror holds the branch's stored tools: ``_save_session`` writes it
    # back to the branch after the turn, so a /skill whitelist placed there
    # would narrow every later message.
    conv["tools_override"] = run_cfg.tools_override
    if req.tools_flag is not None:
        # The /skill whitelist narrows this turn's request only.
        run_cfg.tools_override = list(req.tools_flag)
    conv["web_search"] = run_cfg.web_search
    conv["thinking_effort"] = run_cfg.thinking_effort
    conv["permission_mode"] = run_cfg.permission_mode
    conv["sandbox_enabled"] = (
        run_cfg.sandbox_enabled if run_cfg.sandbox_enabled is not None
        else req.sandbox_flag
    )
    return run_cfg, effective_permission


def _bind_draft_channel(_s, req: _ChatRequest) -> None:
    """A draft's channel pick arrives with its first message; bind the
    conversation the moment its row exists, the same as picking it on a
    saved chat."""
    channel = (req.cmd.get("channel") or "").strip()
    if not channel:
        return
    from .conversation_channel import bind_conversation_channel
    try:
        result = bind_conversation_channel(
            _s, req.session_id, channel,
            req.cmd.get("account_id"), req.cmd.get("peer"),
        )
    except Exception:
        # The turn runs; the chat stays unbound and the chip shows it.
        _log.warning("draft channel not bound for %s", req.session_id, exc_info=True)
        return
    if not result.ok:
        _log.warning("draft channel not bound for %s: %s",
                     req.session_id, result.error)


async def _persist_turn_attachments(_s, req: _ChatRequest, msg_id: str) -> None:
    # Persist EVERY attachment to the session workdir so the agent's file
    # tools can read them and the chat can render them back, and embed the
    # saved ABSOLUTE PATH into the message text (every file is referenced
    # by path, never inlined). Images additionally continue to the
    # dispatcher as ImageContent blocks; documents are NOT passed as
    # content blocks (providers have no document-block support here).
    attachments = req.attachments
    if attachments:
        try:
            # Attachment decoding, hashing, and filesystem access are all
            # synchronous.  Keep the operation one sequential unit so the
            # durable admission/ACK order is unchanged, but do not block the
            # WebSocket event loop while a browser upload is being copied.
            req.text = await _persist_attachments_async(
                req.session_id, attachments, req.text,
            )
        except BaseException:
            await run_for_conversation(
                req.session_id, _s._release_run_reservation, req.session_id, msg_id)
            raise
        attachments = _attachments_for_dispatch(attachments)
    req.user_attachments = attachments
    if req.recording_images:
        attachments = (attachments or []) + req.recording_images
    req.attachments = attachments


def _backfill_title(req: _ChatRequest) -> None:
    # Stage 1 (immediate, zero-latency sidebar placeholder): truncate the
    # user's first line into a title the instant the message is sent, so the
    # sidebar never shows an empty row while stage 2 (the background LLM
    # title in finalize→_maybe_auto_title) is still running. We mark
    # ``_auto_titled`` — the SAME flag _maybe_auto_title uses — so its own
    # stage-1 backfill is a no-op and its race guard (which compares the
    # live title against the truncation it expects) keeps the LLM title.
    # We do NOT set _user_titled: this is an automatic title, not a manual
    # rename, so the LLM stage and turn-1/6/16/40 re-titling stay live.
    try:
        from openprogram.runtime.context.session_db import default_db as _chat_ddb
        _chat_sess = _chat_ddb().get_session(req.session_id) or {}
    except Exception:
        _chat_sess = {}
    _chat_extra = _chat_sess.get("extra_meta") or {}
    if not _chat_extra.get("_auto_titled") and not _chat_extra.get("_user_titled"):
        _truncated = _title_from_text(req.text)
        try:
            _chat_ddb().update_session(req.session_id, title=_truncated,
                                       _auto_titled=True)
        except Exception:
            # Auto-title is cosmetic; the background rename retries it.
            _log.debug(
                "auto title not written for session %s", req.session_id, exc_info=True)


def _capture_turn_inputs(_s, ws, req: _ChatRequest, msg_id: str):
    """Parse the text, take the local authority and bind the surface.

    Returns ``(parsed, local_authority, surface_context, release_bindings)``.
    """
    session_id = req.session_id
    try:
        parsed = _s._parse_chat_input(req.text)
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise

    try:
        from openprogram.runtime.permissions.authority import local_owner_authority
        _local_authority = local_owner_authority()
        # A client with no one behind it (``openprogram --print`` in a
        # pipeline) says so, and the turn runs as one nobody answers: an
        # operation that would ask is refused at once and the question tool
        # is not offered, instead of the turn waiting on an approval card.
        # It can only narrow the owner's authority, never widen it.
        if req.cmd.get("interaction") == "non-interactive":
            _local_authority = {**_local_authority, "interaction": "non-interactive"}
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise

    # The renderer's surface reference is only a hint. Resolve it against
    # this authenticated websocket and capture a server-owned binding before
    # durable admission; client-supplied capability fields are never stored.
    from openprogram.runtime.conversation.surface_context import (
        capture as _capture_surface,
        release_bindings as _release_surface_bindings,
    )
    try:
        surface_context = _capture_surface(req.surface_ref, ws, session_id=session_id)
    except BaseException:
        _s._release_run_reservation(session_id, msg_id)
        raise
    return parsed, _local_authority, surface_context, _release_surface_bindings


def _build_user_msg(req: _ChatRequest, msg_id: str, parsed: dict,
                    _local_authority: dict) -> dict:
    text = req.text
    user_msg = {
        "role": "user",
        "id": msg_id,
        "content": text,
        "timestamp": time.time(),
        "source": "web",
        "origin": "person",
        **_local_authority,
    }
    if getattr(req, "starts_goal", False):
        # The chat labels this message Goal; its content stays the objective.
        user_msg["starts_goal"] = True
    request_id = client_request_id_of(req.cmd)
    if request_id is not None:
        user_msg["client_request_id"] = request_id
    model_text = _skill_model_text(req)
    if model_text is not None:
        # Later turns read the skill body from here; the transcript shows
        # what the user typed.
        user_msg["model_input"] = model_text
    if parsed["action"] == "spawn":
        # SYNC path only: tag the /task user msg so the DAG layout
        # treats it as a branch fork (main trunk stops here; the
        # spawned turn + sub-agent reply live on a new lane). Same
        # idea as git: /task probe → `git checkout -b probe`.
        # ASYNC path: don't tag — the spawned turn lives on its own
        # session (or independent branch), not as a fork of THIS
        # message. Marking it function="agent" made the user msg
        # surface in the Branches panel as a stray named branch
        # (with the raw command as its label) because lane.py
        # treated it as a fork tip with no follow-up.
        if parsed.get("wait", True):
            user_msg["function"] = "agent"
    extra_meta: dict = {}
    user_attachments = req.user_attachments
    if user_attachments:
        extra_meta["attachments"] = [
            {"type": a.get("type"), "media_type": a.get("media_type"),
             "size_b64": len(a.get("data") or "")}
            for a in user_attachments
        ]
    if req.recording_meta:
        extra_meta["recording"] = req.recording_meta
    text_elements = _pasted_text_elements(req.cmd.get("text_elements"), len(text))
    if text_elements:
        extra_meta["text_elements"] = text_elements
    if extra_meta:
        user_msg["extra"] = json.dumps(extra_meta, default=str)
    if req.attachments:
        # The images the turn sends after the text, kept so later requests
        # send them again (``store.session.media``).
        try:
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store.session.media import records_for_attachments
            media = records_for_attachments(
                default_db()._session_dir(req.session_id), req.attachments, 1)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - the turn goes on without the copy
            _log.warning("user message media not kept for session %s", req.session_id, exc_info=True)
            media = []
        if media:
            user_msg["media"] = media
    return user_msg


def _agent_event_sink(_s, session_id: str, msg_id: str, conv: dict,
                      side_branch_id: str | None = None, branch_id: str | None = None):
    """Broadcast a turn's frames, each naming the branch the turn runs on, so
    a client draws them only while that branch is selected. Context usage
    goes on the conversation's mirror only while the turn's branch is the
    selected one; the person may switch away while it runs."""
    from openprogram.interfaces.server.server_runtime.run_slots import SELECTED
    branch_id = side_branch_id or branch_id

    def _on_agent_event(env: dict) -> None:
        data = env.get("data") or {}
        if data.get("type") == "result" and data.get("msg_id") == msg_id:
            # A turn no one watches hands its requests' wire facts back.
            from openprogram.models.providers.recording import wire_facts as _wire
            facts = _wire.take(msg_id)
            if facts is not None:
                data = {**data, "wire_facts": facts}
                env = {**env, "data": data}
        if branch_id and isinstance(env.get("data"), dict):
            env = {**env, "data": {**data, "branch_id": branch_id}}
        selected = not side_branch_id and _s._slot_location(session_id, msg_id) in (SELECTED, None)
        if not selected:
            # A turn on another branch: its context usage is that branch's,
            # not the selected one's.
            if data.get("type") == "context_usage":
                return
            if hasattr(_s, "_broadcast_envelope"):
                _s._broadcast_envelope(env)
            else:
                _s._broadcast(json.dumps(env, default=str))
            return
        if data.get("type") == "context_usage":
            try:
                _record_context_usage(
                    session_id, msg_id, conv, data.get("usage") or {},
                    model=data.get("model"),
                    context_window=data.get("context_window"),
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "failed to record in-flight context stats for %s", session_id,
                )
            return
        if hasattr(_s, "_broadcast_envelope"):
            _s._broadcast_envelope(env)
        else:
            _s._broadcast(json.dumps(env, default=str))
    return _on_agent_event


def _turn_request_fields(req: _ChatRequest, parsed: dict) -> dict:
    """What a chat command adds to the turn request beside what every
    surface's request carries (``turn_input.build_turn_request``)."""
    response_format = req.response_format
    _response_format_payload = (
        response_format.model_dump(mode="json")
        if hasattr(response_format, "model_dump") else response_format
    )
    from openprogram.runtime.conversation.local_commands import slash_fields
    return {
        # A typed /task or /merge: its own field, so `interaction` keeps the
        # authority's value (``--print`` builds the same fields).
        **slash_fields(parsed),
        "service_tier": req.service_tier,
        "response_format": _response_format_payload,
        "attachments": req.attachments,
        "user_already_persisted": True,
        **_side_branch_fields(req),
        **_one_turn_fields(req),
    }


def _surface_fields(session_id: str, req: _ChatRequest, parsed: dict,
                    admitted: str | None) -> dict:
    """The fields a chat command passes to ``build_turn_request``: the Agent
    binding the conversation runs (an Agent trial's profile and saved model),
    then the command's own fields, which win, so a model chosen for this
    turn only (``turn_model``) replaces the trial's saved one instead of
    colliding with it."""
    from ..agent_invocation import request_agent_invocation
    return {
        **request_agent_invocation(session_id, req.agent_invocation),
        **_turn_request_fields(req, parsed),
        **({"branch_id": admitted} if admitted and not req.side_branch_id else {}),
    }


def _one_turn_fields(req: _ChatRequest) -> dict:
    """The model, effort and permission mode a frame sets for this turn only
    (``turn_*``); as request fields they win over the branch's saved ones.
    A model the provider lists but no one enabled is usable for the turn,
    in this process only (``enabled_models.use_model_in_process``)."""
    fields: dict = {}
    if req.turn_model:
        from openprogram.models.providers.catalog.enabled_models import use_model_in_process
        provider, _, model_id = req.turn_model.partition("/")
        use_model_in_process(provider, model_id)
        fields["model_override"] = req.turn_model
    if req.turn_effort:
        fields["thinking_effort"] = req.turn_effort
    if req.turn_permission_mode:
        fields["permission_mode"] = req.turn_permission_mode
    if getattr(req, "turn_no_web", None):
        from openprogram.runtime.agent.provider_search import NO_WEB_OVERRIDE
        fields["tools_override"] = dict(NO_WEB_OVERRIDE)
    return fields


def _side_branch_fields(req: _ChatRequest) -> dict:
    """A turn on an unselected branch: it continues that branch's tip and
    moves only that branch (``advance_head=False``, ``side_branch``)."""
    if not req.side_branch_id:
        return {}
    from openprogram.runtime.context.session_db import default_db
    row = default_db().get_session(req.session_id) or {}
    ref = (row.get("branch_refs") or {}).get(req.side_branch_id) or {}
    return {
        "branch_id": req.side_branch_id,
        "branch_from": ref.get("head_id"),
        "advance_head": False,
        "side_branch": True,
    }


def _admit_turn(_s, req: _ChatRequest, conv: dict, msg_id: str, run_cfg,
                effective_permission, parsed: dict, _local_authority: dict,
                surface_context, _release_surface_bindings) -> _AdmittedTurn:
    # Admission is the sole source of execution identity. The message id is
    # retained only as transport/DAG provenance and never becomes ownership.
    session_id = req.session_id
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.conversation.turn_input import build_turn_request
    from openprogram.runtime.permissions.rule import load_merged_rules
    # The branch the turn was admitted on: its slot names it. The turn
    # writes there whatever the person selects while it runs.
    admitted = req.side_branch_id or _s._slot_branch(session_id, msg_id)
    _adapter = CanonicalAgentAdapter(
        event_sink=_agent_event_sink(_s, session_id, msg_id, conv, req.side_branch_id, admitted),
    )
    try:
        _request = build_turn_request(
            session_id,
            _skill_model_text(req) or parsed.get("raw") or req.text,
            # A message goes to the selected branch: its Agent, mode and rules.
            agent_id=_db_agent_id(session_id),
            source="web",
            msg_id=msg_id,
            authority=_local_authority,
            permission={"permission_mode": effective_permission,
                        "permission_rules": load_merged_rules(session_id)},
            run_config=("thinking_effort", "tools_override", "additional_working_dirs"),
            run_cfg=run_cfg,
            # A chat command no client answers (``--print`` through the
            # Worker) is told so, the same way the in-process run is.
            unattended=_local_authority.get("interaction") == "non-interactive",
            surface_context=surface_context,
            **_surface_fields(session_id, req, parsed, admitted),
        )
        _admission = _adapter.admit(
            _request,
            trusted_actor=_local_authority,
            user_message_id=msg_id,
            # The dispatcher writes this deterministic placeholder before
            # provider/tool callbacks.  Persist the same anchor at admission
            # so a resumed Agent checkpoint can fence it against immutable
            # execution input and finalize it exactly once.
            assistant_message_id=f"{msg_id}_reply",
            config_snapshot_ref=f"session:{session_id}",
            # A stop can name this send before the ack names its execution.
            client_request_id=client_request_id_of(req.cmd),
        )
    except BaseException:
        _release_surface_bindings(surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise
    if _request.run_note:
        # ``--print`` on the Worker: note what its requests send, for the
        # result frame (``wire_facts``).
        from openprogram.models.providers.recording import wire_facts as _wire
        _wire.watch(_admission.execution_id, msg_id)
    return _AdmittedTurn(
        session_id=session_id,
        msg_id=msg_id,
        conv=conv,
        adapter=_adapter,
        admission=_admission,
        surface_context=surface_context,
        release_surface_bindings=_release_surface_bindings,
        response_format=req.response_format,
        execution_id=_admission.execution_id,
        side_branch_id=req.side_branch_id,
        branch_from=_request.branch_from if req.side_branch_id else None,
        branch_id=admitted,
    )


def _commit_user_message(_s, req: _ChatRequest, turn: _AdmittedTurn,
                         user_msg: dict) -> None:
    session_id, msg_id = turn.session_id, turn.msg_id
    # User/DAG content is committed only after the bounded canonical payload
    # has been admitted. If admission rejects an oversized or malformed
    # request, no user node is left behind without an execution record.
    try:
        if turn.side_branch_id:
            _append_side_branch_message(turn, user_msg, turn.side_branch_id)
        elif _left_since_admission(turn):
            # The person switched away after the send was admitted: the
            # message goes on the branch it was sent to, not the one now shown.
            _append_side_branch_message(turn, user_msg, turn.branch_id)
        else:
            _s._append_msg(turn.conv, user_msg, strict=True)
    except BaseException:
        turn.adapter.fail_admission(turn.admission, reason_code="user_persist_failed")
        turn.release_surface_bindings(turn.surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise

    # chat.before_send on the bus — plugin subscribers observe the
    # message about to enter the runtime. emit_safe swallows failures
    # so a bad subscriber can't poison the chat path.
    try:
        from openprogram.runtime.events import emit_safe
        emit_safe("chat.before_send", "user", {
            "session_id": session_id,
            "msg_id": msg_id,
            "text": req.text,
            "agent_id": _db_agent_id(session_id),
            "attachments": bool(req.attachments),
        }, {"session": session_id})
    except BaseException:
        turn.adapter.fail_admission(turn.admission, reason_code="chat_event_failed")
        turn.release_surface_bindings(turn.surface_context)
        _s._release_run_reservation(session_id, msg_id)
        raise


def _left_since_admission(turn: _AdmittedTurn) -> bool:
    """Whether the turn's branch is no longer selected; sets the tip it
    continues (``turn.branch_from``) when so."""
    if not turn.branch_id:
        return False
    from openprogram.runtime.context.session_db import default_db
    row = default_db().get_session(turn.session_id) or {}
    if row.get("active_branch_id") in (None, turn.branch_id):
        return False
    ref = (row.get("branch_refs") or {}).get(turn.branch_id)
    if not isinstance(ref, dict) or not ref.get("head_id"):
        return False
    turn.branch_from = ref["head_id"]
    return True


def _append_side_branch_message(turn: _AdmittedTurn, user_msg: dict, branch_id: str) -> None:
    """Write a message under a branch's tip and move only that branch.

    The head, the selected branch and the live mirror stay as they are. The
    branch record follows the message (``point_branch``), so the reply,
    written beside the head, carries it on. A branch the person selected
    meanwhile takes the head with its record.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call, ROLE_USER
    db = default_db()
    tip = turn.branch_from
    metadata = {k: v for k, v in user_msg.items()
                if k not in {"id", "role", "content", "timestamp", "predecessor"} and v is not None}
    SessionNodeWriter(db, turn.session_id, advance_head=False).append(Call(
        id=turn.msg_id, role=ROLE_USER, output=user_msg.get("content") or "",
        caller="ROOT", predecessor=tip or "ROOT", metadata=metadata,
        created_at=user_msg.get("timestamp") or time.time(),
    ))
    if not db.point_branch(turn.session_id, branch_id,
                           expected_head=tip, head=turn.msg_id):
        raise RuntimeError("the branch moved before its message was written")
    user_msg["predecessor"] = tip
    user_msg["branch_id"] = branch_id


async def _send_chat_ack(ws, req: _ChatRequest, turn: _AdmittedTurn,
                         effective_permission) -> None:
    # Echo the STORED text back. The composer only knows the path-less
    # mention it wrote (the encoded absolute path is appended above, after the
    # bytes hit disk), so an optimistic bubble built from the client's
    # own draft has no path to open. Handing it the final text is what
    # makes an attachment clickable in the turn you sent it, instead of
    # only after a reload.
    from openprogram.interfaces.server.handshake import server_identity

    first_read, end_parking = await _join_and_read(ws, turn.session_id)
    try:
        await ws.send_text(json.dumps({
            "type": "chat_ack",
            "data": _with_client_request_id({
                **first_read,
                "session_id": turn.session_id,
                "msg_id": turn.msg_id,
                "reply_id": f"{turn.msg_id}_reply",
                "text": req.text,
                # The message that set a Goal: the sender's bubble takes the
                # Goal label from here, before the transcript row arrives.
                **({"starts_goal": True} if req.starts_goal else {}),
                "execution_id": turn.execution_id,
                "status_version": turn.admission.status_version,
                # The mode the turn runs with: a turn-only override
                # (``turn_permission_mode``) wins over the branch's own.
                "permission_mode": req.turn_permission_mode or effective_permission,
                "environment_id": turn.conv.get("environment_id"),
                **({"branch_id": turn.side_branch_id} if turn.side_branch_id else {}),
                # Who took the turn, for a client that did not read it on
                # connect (``handshake.server_identity``).
                "server": server_identity(),
            }),
        }))
    except Exception:
        # The turn is already persisted. Losing its originating socket must
        # not leave that user message without a corresponding execution.
        _log.debug(
            "chat_ack not delivered for session %s", turn.session_id, exc_info=True)
    finally:
        end_parking()


async def _join_and_read(ws, session_id: str):
    """A send from a socket holding no view of the chat joins it, and the ack
    carries the first read: ``(fields for the ack, end of parking)``."""
    from openprogram.interfaces.server.transcript import publisher, reader, turn_index
    from openprogram.interfaces.server.ws_actions.session.load import _park_read
    from openprogram.runtime.context.session_db import default_db

    held = getattr(ws, "_transcript_views", None) or ()
    if getattr(ws, "_history_protocol", 0) != 3 or session_id in held:
        return {}, lambda: None
    end_parking = _park_read(ws, session_id)
    publisher.hold(ws, session_id)
    try:
        db = default_db()
        head = await run_for_conversation(session_id, lambda: (db.get_session(session_id) or {}).get("head_id"))
        view = await run_for_conversation(session_id, turn_index.get_view, db, session_id, head)
        publisher.read_view(session_id, view)
        turns, window = await run_for_conversation(session_id, reader.first_read, db, view)
    except Exception:
        _log.warning("first read for session %s failed", session_id, exc_info=True)
        return {}, end_parking
    return {"first_read": {"turns": turns, "window": window}}, end_parking
