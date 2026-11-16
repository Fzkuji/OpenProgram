"""ACP agent-side server: protocol methods mapped onto the turn dispatcher.

Method coverage (protocol version 1):

  client → agent   initialize, session/new, session/load, session/prompt,
                   session/cancel
  agent → client   session/update, session/request_permission

Three translations do the real work.

**Prompt in.** ACP content blocks become one text message. A ``resource``
block is the editor's selection or open file shipped inline; it is fenced
under its URI so the model sees the path and the excerpt as context rather
than as the user's words. ``resource_link`` becomes a bare path mention.

**Events out.** ``process_user_turn`` emits ``chat_response`` envelopes;
``_on_event`` turns each into a ``session/update`` notification. Text →
``agent_message_chunk``, thinking → ``agent_thought_chunk``, tool start →
``tool_call`` (in_progress), tool end → ``tool_call_update``
(completed/failed).

**Working directory.** The session's first ``session/prompt`` creates the
conversation bound to the client's ``cwd`` as its project, the way
``openprogram --print`` binds the directory it runs in
(``working_directory_project``), so tools start there. Binding registers the
folder as a project (shown again if it was hidden) and writes its identity
marker, ``<cwd>/.openprogram/project.json``; a thread opened and never
prompted leaves nothing behind. The home folder needs no binding; a folder
that cannot be opened as a project runs unbound, with a warning on stderr.

**Permissions.** OpenProgram's approval gate opens a durable
``kind="approval"`` wait and suspends the turn's execution. The wait's
``question.asked`` frame reaches the prompt's event sink (or, for a turn
resumed after an earlier answer, the event bus); either is forwarded as ACP
``session/request_permission`` and answered with the canonical execution wait
command, which resumes the execution. ``session/prompt`` stays open until
that execution ends, so the editor sees the whole turn, resumed rounds
included. A client that answers the request with an error, or not within ten
minutes (sooner when the wait expires sooner), declines it, and the turn goes on without the operation.
Questions other than approvals have no ACP form and are declined at once.
The prompt also reads the execution's open waits while it is suspended, so
a wait whose frame never reached it is handled the same way. A wait for
system access cannot be granted from an editor: the turn is cancelled and
the prompt answers with an error naming the access it needs. A turn that
stops where nothing here can move it on (paused by someone else, needing
reconciliation, or suspended with no open wait) ends the prompt with an
error rather than keeping it open.
Nothing about the gate itself changes, so authority checks, hard
constraints, permission rules and the ``allow_always`` rule-persistence path
all behave exactly as in the web UI.

**Ends.** A turn that fails (a provider error, say) answers
``session/prompt`` with a JSON-RPC error carrying the reason, not a stop
reason, whichever round failed: a resumed round's error is read from the
attempt that ran it. A cancelled turn ends with ``cancelled``.

An ACP connection is a local editor driven by the person at the keyboard,
so turns carry ``local_owner_authority()`` — owner tier, interactive. That
is what makes ``approval.request`` available at all; a non-interactive
authority would auto-deny every gated tool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from typing import Any

from openprogram.interfaces.acp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    Connection,
    RPCError,
)
from openprogram.runtime.execution.control import ObservedCancelSubmission, submit_observed_cancel

_log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1

# ACP ToolKind values, keyed by how OpenProgram names its tools. Clients use
# the kind only to pick an icon, so an unknown tool falling back to "other"
# costs nothing.
_TOOL_KINDS: dict[str, str] = {
    "read": "read", "read_file": "read", "lsp_definition": "read",
    "lsp_diagnostics": "read", "lsp_references": "read",
    "write": "edit", "write_file": "edit", "edit": "edit",
    "edit_file": "edit", "multi_edit": "edit", "apply_patch": "edit",
    "grep": "search", "glob": "search", "search": "search",
    "web_search": "fetch", "web_fetch": "fetch", "fetch": "fetch",
    "bash": "execute", "write_stdin": "execute", "shell": "execute", "run": "execute",
    "think": "think", "todo_write": "think", "task": "think",
    "task_start": "think", "task_step": "think", "task_list": "think",
    "delete": "delete", "move": "move",
}

_ALLOW_ONCE = "allow_once"
_ALLOW_ALWAYS = "allow_always"
_REJECT_ONCE = "reject_once"

# How often a prompt re-reads its execution while a resumed turn runs.
_FOLLOW_POLL_S = 0.1
# How long a cancelled prompt waits for its execution to stop.
_CANCEL_GRACE_S = 30.0
# The permission request's limit when the wait carries no expiry.
_PERMISSION_TIMEOUT_S = 600.0
# How long a suspended execution may sit with no open wait (an answer being
# applied takes far less) before the prompt reports it stuck.
_STALL_S = 10.0


def _tool_kind(name: str) -> str:
    return _TOOL_KINDS.get((name or "").lower(), "other")


def _blocks_to_text(blocks: list) -> str:
    """Flatten ACP prompt content blocks into the turn's user text.

    Editor context (``resource`` = selection / open file shipped inline)
    is fenced under its URI so the model can tell quoted context from what
    the user typed.
    """
    parts: list[str] = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            parts.append(str(b.get("text") or ""))
        elif btype == "resource":
            res = b.get("resource") or {}
            uri = str(res.get("uri") or "")
            text = res.get("text")
            if text is None:  # blob: binary, not usable as prompt text
                parts.append(f"[attached resource: {uri}]")
                continue
            path = uri[7:] if uri.startswith("file://") else uri
            parts.append(f"Context from {path}:\n```\n{text}\n```")
        elif btype == "resource_link":
            uri = str(b.get("uri") or "")
            path = uri[7:] if uri.startswith("file://") else uri
            parts.append(f"@{path}")
        elif btype == "image":
            parts.append("[image attached]")
    return "\n\n".join(p for p in parts if p.strip())


def _blocks_to_attachments(blocks: list) -> list[dict]:
    """Image blocks → dispatcher attachments (base64 + media type)."""
    out: list[dict] = []
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "image" and b.get("data"):
            out.append({"type": "image", "data": b["data"],
                        "media_type": b.get("mimeType") or "image/png"})
    return out


class _Session:
    """Per-session ACP state: where it runs and what's cancellable."""

    def __init__(self, session_id: str, cwd: str, *, created: bool = False) -> None:
        self.id = session_id
        self.cwd = cwd
        # The conversation exists (a loaded one, or after the first prompt).
        self.created = created
        # The conversation's project is ``cwd``: tools already start there.
        self.bound = False
        # Why the prompt ends with an error although its turn was cancelled
        # (a wait the editor cannot answer).
        self.stop_error: str | None = None
        self.cancel_event = threading.Event()
        self.execution_id: str | None = None
        self.execution_status_version: int | None = None
        # Cancellation may arrive while durable admission is in progress,
        # before the execution identity can be published to the session.
        self.prompt_pending = False
        self.cancel_requested = False
        self.cancel_reason = ""
        self.cancel_command_id = ""
        self.cancel_in_flight = False
        # Question ids forwarded to the client and still unanswered — a
        # cancel must resolve them or the tool gate sits on its Event for
        # the full 300s timeout.
        self.open_questions: dict[str, str] = {}
        # A wait's frame can come by the prompt's sink and by the event bus.
        self.seen_questions: set[str] = set()
        self.lock = threading.Lock()


class ACPServer:
    """The agent side of ACP. ``serve()`` runs until the client hangs up."""

    def __init__(
        self,
        reader=None,
        writer=None,
        *,
        agent_id: str = "main",
        permission_mode: str | None = None,
    ) -> None:
        self._conn = Connection(
            reader if reader is not None else sys.stdin,
            writer if writer is not None else sys.stdout,
            self._handle,
        )
        self._agent_id = agent_id
        from openprogram.runtime.permissions.modes import resolve_mode

        self._permission_mode = resolve_mode(permission_mode)
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._unsubscribe: list = []
        self._client_caps: dict = {}

    # -- lifecycle --------------------------------------------------------

    def serve(self) -> None:
        self._subscribe_questions()
        try:
            self._conn.serve_forever()
        finally:
            for unsubscribe in self._unsubscribe:
                try:
                    unsubscribe()
                except Exception:
                    _log.debug("ACP event unsubscribe failed", exc_info=True)

    # -- method dispatch --------------------------------------------------

    def _handle(self, method: str, params: dict) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method == "session/new":
            return self._session_new(params)
        if method == "session/load":
            return self._session_load(params)
        if method == "session/prompt":
            return self._session_prompt(params)
        if method == "session/cancel":
            return self._session_cancel(params)
        raise RPCError(METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize(self, params: dict) -> dict:
        self._client_caps = params.get("clientCapabilities") or {}
        # The spec says answer with the client's version when we support it,
        # otherwise our own latest — the client then decides to disconnect.
        requested = params.get("protocolVersion")
        version = (PROTOCOL_VERSION
                   if not isinstance(requested, int)
                   else min(requested, PROTOCOL_VERSION))
        return {
            "protocolVersion": version,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "image": True,
                    "audio": False,
                    "embeddedContext": True,
                },
                "mcpCapabilities": {"http": False, "sse": False},
            },
            "authMethods": [],
        }

    def _session_new(self, params: dict) -> dict:
        cwd = params.get("cwd") or os.getcwd()
        if not os.path.isabs(cwd):
            raise RPCError(INVALID_PARAMS, "cwd must be an absolute path")
        sid = "acp_" + uuid.uuid4().hex[:10]
        # The conversation, and the project binding that writes a marker
        # into ``cwd``, wait for the first prompt.
        with self._lock:
            self._sessions[sid] = _Session(sid, cwd)
        return {"sessionId": sid}

    def _create_conversation(self, sid: str, cwd: str) -> bool:
        """Create the conversation bound to ``cwd``; True when it is bound.

        The binding is the one ``openprogram --print`` makes for the
        directory it runs in, so the session's tools start in ``cwd``.
        """
        from openprogram.interfaces.cli.print_mode.print_turn import working_directory_project
        from openprogram.runtime.context.session_db import default_db

        project_id = working_directory_project(cwd)
        default_db().create_session(sid, self._agent_id, title="", source="acp",
                                    project_id=project_id)
        return project_id is not None

    def _session_load(self, params: dict) -> dict:
        sid = params.get("sessionId")
        cwd = params.get("cwd") or os.getcwd()
        if not sid:
            raise RPCError(INVALID_PARAMS, "sessionId is required")
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
        if db.get_session(sid) is None:
            raise RPCError(INVALID_PARAMS, f"unknown session: {sid}")
        with self._lock:
            self._sessions[sid] = _Session(sid, cwd, created=True)
        # A loaded conversation keeps the project it was created in.
        # The spec has the agent replay the conversation as session/update
        # notifications before answering, so the editor can render history.
        for msg in db.get_branch(sid):
            role = msg.get("role")
            content = msg.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            self._notify(sid, {
                "sessionUpdate": ("user_message_chunk" if role == "user"
                                  else "agent_message_chunk"),
                "content": {"type": "text", "text": content},
            })
        return {}

    def _session_cancel(self, params: dict) -> None:
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is None:
            return None
        return self._cancel(sess, "prompt_cancel")

    def _cancel(self, sess: _Session, reason: str) -> None:
        with sess.lock:
            execution_id = sess.execution_id
            if execution_id is None:
                if not sess.prompt_pending:
                    return None
                sess.cancel_requested = True
                if not sess.cancel_reason:
                    sess.cancel_reason = reason
                if not sess.cancel_command_id:
                    sess.cancel_command_id = f"acp-cancel:{uuid.uuid4().hex}"
                return None
            if sess.cancel_in_flight:
                return None
            sess.cancel_requested = True
            sess.cancel_reason = sess.cancel_reason or reason
            if not sess.cancel_command_id:
                sess.cancel_command_id = f"acp-cancel:{uuid.uuid4().hex}"
            expected_version = sess.execution_status_version
            command_id = sess.cancel_command_id
            reason_code = sess.cancel_reason
            sess.cancel_in_flight = True
        try:
            if expected_version is None:
                return None
            from openprogram.runtime.permissions.authority import local_owner_authority
            from openprogram.runtime.execution import default_control_service

            submitted = asyncio.run(submit_observed_cancel(
                default_control_service(),
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                actor=local_owner_authority(),
                reason_code=reason_code,
            ))
        except Exception:
            # Notifications have no error response; leave the live prompt
            # untouched and surface infrastructure failures in the log.
            _log.warning("ACP execution cancellation failed for %s",
                         execution_id, exc_info=True)
            return None
        finally:
            with sess.lock:
                sess.cancel_in_flight = False
        if not isinstance(submitted, ObservedCancelSubmission) or not submitted.accepted:
            return None
        # The service call can race prompt teardown and a successor prompt.
        # Revalidate the identity before touching local event/question state.
        with sess.lock:
            if (
                sess.execution_id != execution_id
                or sess.cancel_command_id != command_id
            ):
                return None
            sess.cancel_event.set()
            qids = [
                qid for qid, question_execution_id in sess.open_questions.items()
                if question_execution_id == execution_id
            ]
            for qid in qids:
                sess.open_questions.pop(qid, None)
        # request_cancel already closes every wait for this exact execution
        # in the same durable transaction.
        return None

    # -- the turn ---------------------------------------------------------

    def _session_prompt(self, params: dict) -> dict:
        sid = params.get("sessionId")
        sess = self._sessions.get(sid or "")
        if sess is None:
            raise RPCError(INVALID_PARAMS, f"unknown session: {sid}")
        blocks = params.get("prompt") or []
        text = _blocks_to_text(blocks)
        if not text.strip():
            raise RPCError(INVALID_PARAMS, "prompt has no text content")

        from openprogram.runtime.permissions.authority import local_owner_authority
        from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
        from openprogram.runtime.conversation.turn_admission.turn_input import build_turn_request

        if not sess.created:
            try:
                sess.bound = self._create_conversation(sess.id, sess.cwd)
            except Exception as exc:
                raise RPCError(INTERNAL_ERROR,
                               f"the session could not be set up in {sess.cwd}: {exc}") from exc
            sess.created = True

        user_msg_id = uuid.uuid4().hex[:12]
        # A bound session already works in ``cwd``; otherwise (the home
        # folder, or a folder that could not be bound) it stays writable.
        extra_dirs = [] if sess.bound else [sess.cwd]
        request = build_turn_request(
            sess.id, text, agent_id=self._agent_id, source="acp", msg_id=user_msg_id,
            authority=local_owner_authority(),
            permission={"permission_mode": self._permission_mode},
            attachments=_blocks_to_attachments(blocks) or None,
            additional_working_dirs=extra_dirs,
        )
        adapter = CanonicalAgentAdapter(
            event_sink=lambda env: self._on_event(sess, env),
        )
        with sess.lock:
            if sess.execution_id is not None or sess.prompt_pending:
                raise RPCError(INTERNAL_ERROR, "a prompt turn is already active")
            sess.prompt_pending = True
            sess.cancel_requested = False
            sess.cancel_reason = ""
            sess.cancel_command_id = ""
            sess.stop_error = None
            sess.cancel_event.clear()
        try:
            admission = adapter.admit(
                request,
                trusted_actor=local_owner_authority(),
                user_message_id=user_msg_id,
                config_snapshot_ref=f"acp:{sess.id}",
            )
        except Exception as exc:
            with sess.lock:
                sess.prompt_pending = False
            raise RPCError(INTERNAL_ERROR, f"prompt admission failed: {exc}") from exc
        execution_id = admission.execution_id
        with sess.lock:
            cancelled_before_activation = sess.cancel_requested
            sess.prompt_pending = False
            sess.execution_id = execution_id
            sess.execution_status_version = admission.status_version
        if cancelled_before_activation:
            self._session_cancel({"sessionId": sess.id})
            if sess.cancel_event.is_set():
                with sess.lock:
                    if sess.execution_id == execution_id:
                        sess.execution_id = None
                        sess.execution_status_version = None
                return {"stopReason": "cancelled"}
        try:
            activated = asyncio.run(adapter.activate(admission))
            result = activated[1] if activated else None
            # An approval suspends the execution and ends this attempt; the
            # answer resumes it in a new one. The prompt lasts as long as
            # the execution does.
            final, stuck = self._follow(sess, adapter.store, execution_id)
        finally:
            with sess.lock:
                if sess.execution_id == execution_id:
                    sess.execution_id = None
                    sess.execution_status_version = None

        if sess.stop_error:
            raise RPCError(INTERNAL_ERROR, sess.stop_error,
                           {"reason": "system_access_required"})
        if sess.cancel_event.is_set():
            return {"stopReason": "cancelled"}
        if stuck:
            raise RPCError(INTERNAL_ERROR, stuck, {
                "reason": getattr(final, "reason_code", None) or "execution_stalled",
                "status": getattr(getattr(final, "status", None), "value", None),
            })
        return _stop(result, final, _last_failure(adapter.store, execution_id))

    def _follow(self, sess: _Session, store, execution_id: str):
        """Wait for the execution to end.

        Returns its last record and, when the prompt must end while the
        execution has not, why. While the execution is suspended its open
        waits are read from the store and handled like their frames, so a
        wait whose frame never arrived cannot hold the prompt open. A
        cancelled prompt waits at most :data:`_CANCEL_GRACE_S`.
        """
        from openprogram.runtime.execution.model import (
            TERMINAL_EXECUTION_STATUSES,
            ExecutionStatus,
        )
        from openprogram.runtime.execution.waits import DurableWaitStore, question_frame_data

        waits = DurableWaitStore(store)
        stop_by = None
        idle_since: tuple[int, float] | None = None
        while True:
            record = store.get_execution(execution_id)
            if record is None or record.status in TERMINAL_EXECUTION_STATUSES:
                return record, None
            with sess.lock:
                if sess.execution_id == execution_id and not sess.cancel_in_flight:
                    sess.execution_status_version = record.status_version
            if sess.cancel_event.is_set() or sess.stop_error:
                stop_by = stop_by or time.monotonic() + _CANCEL_GRACE_S
                if time.monotonic() >= stop_by:
                    return record, None
            elif record.status is ExecutionStatus.RECONCILIATION_REQUIRED:
                return record, ("the turn stopped and needs reconciliation "
                                f"({record.reason_code or 'unknown'}); open it in OpenProgram")
            elif record.status is ExecutionStatus.PAUSED:
                if record.reason_code not in ("wait_open", "system_access_required"):
                    return record, (f"the turn was paused ({record.reason_code or 'no reason'}); "
                                    "continue it in OpenProgram")
                open_waits = waits.list_open(execution_id=execution_id)
                for wait in open_waits:
                    self._forward_question(sess, {
                        **question_frame_data(wait, session_id=sess.id,
                                              expected_version=record.status_version),
                        "required_capabilities": list(
                            wait.request.get("required_capabilities") or []),
                    })
                if open_waits:
                    idle_since = None
                else:
                    # Between an answer and the resumed attempt the
                    # execution is briefly suspended with no open wait.
                    if idle_since is None or idle_since[0] != record.status_version:
                        idle_since = (record.status_version, time.monotonic())
                    elif time.monotonic() - idle_since[1] >= _STALL_S:
                        return record, ("the turn is suspended with nothing left to answer "
                                        "and did not resume; open it in OpenProgram")
            else:
                idle_since = None
            time.sleep(_FOLLOW_POLL_S)

    # -- events → session/update -----------------------------------------

    def _notify(self, session_id: str, update: dict) -> None:
        try:
            self._conn.notify("session/update",
                              {"sessionId": session_id, "update": update})
        except Exception:
            _log.debug("acp notify failed", exc_info=True)

    def _on_event(self, sess: _Session, env: dict) -> None:
        if env.get("type") in ("question.asked", "system_access.waiting"):
            self._forward_question(sess, env.get("data") or {})
            return
        if env.get("type") != "chat_response":
            return
        data = env.get("data") or {}
        if data.get("type") != "stream_event":
            return
        ev = data.get("event") or {}
        etype = ev.get("type")

        if etype == "text":
            from openprogram.state.reply_text import stream_text
            self._notify(sess.id, {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": stream_text(ev)},
            })
        elif etype == "thinking":
            self._notify(sess.id, {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": ev.get("text") or ""},
            })
        elif etype == "tool_use":
            tool = ev.get("tool") or "?"
            raw = ev.get("input")
            try:
                raw_input = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                raw_input = {"raw": raw}
            self._notify(sess.id, {
                "sessionUpdate": "tool_call",
                "toolCallId": ev.get("tool_call_id") or uuid.uuid4().hex[:12],
                "title": tool,
                "kind": _tool_kind(tool),
                "status": "in_progress",
                "rawInput": raw_input,
                "locations": _locations(raw_input),
            })
        elif etype == "tool_result":
            text = ev.get("result") or ""
            self._notify(sess.id, {
                "sessionUpdate": "tool_call_update",
                "toolCallId": ev.get("tool_call_id") or "",
                "status": "failed" if ev.get("is_error") else "completed",
                "content": [{"type": "content",
                             "content": {"type": "text", "text": str(text)}}],
            })

    # -- approvals → session/request_permission ---------------------------

    def _subscribe_questions(self) -> None:
        from openprogram.runtime.events import get_event_bus
        from openprogram.runtime.events.bus import WS_FRAME_EVENT

        bus = get_event_bus()
        self._unsubscribe = [
            bus.subscribe(self._on_question, types={"question.asked"}),
            # A turn resumed after an answer streams through the shared
            # event stream, not the sink of the prompt that started it.
            bus.subscribe(self._on_frame, types={WS_FRAME_EVENT}),
        ]

    def _on_frame(self, event) -> None:
        frame = (getattr(event, "payload", None) or {}).get("frame")
        if not isinstance(frame, dict):
            return
        data = frame.get("data")
        if not isinstance(data, dict):
            return
        sess = self._sessions.get(data.get("session_id") or "")
        if sess is None:
            return
        if frame.get("type") == "chat_response":
            # Only while a prompt is open: the editor has no thread to show
            # a turn it did not start.
            with sess.lock:
                active = sess.execution_id is not None
            if not active:
                return
        self._on_event(sess, frame)

    def _on_question(self, event) -> None:
        data = getattr(event, "payload", None) or {}
        sess = self._sessions.get(data.get("session_id") or "")
        if sess is None:
            return
        self._forward_question(sess, data)

    def _forward_question(self, sess: _Session, data: dict) -> None:
        qid = data.get("id")
        if not qid:
            return
        with sess.lock:
            if qid in sess.seen_questions:
                return
            sess.seen_questions.add(qid)
        if data.get("kind") == "system_access":
            # Only the user, in the system's settings, can grant it, and only
            # a worker resumes the turn after; an editor has no way to.
            threading.Thread(target=self._refuse_system_access, args=(sess, data),
                             daemon=True).start()
            return
        if data.get("kind") != "approval":
            # runtime.ask / form questions have no ACP form. Declining lets
            # the tool report that no one answered instead of the turn
            # waiting on a question nobody is shown.
            if data.get("execution_id"):
                threading.Thread(
                    target=self._resolve_wait,
                    args=(data, data["execution_id"], "execution.wait.decline", None),
                    daemon=True,
                ).start()
            return
        execution_id = data.get("execution_id") or ""
        if not execution_id:
            try:
                from openprogram.runtime.agent.questions import get_question_registry

                pending = next(
                    (
                        q for q in get_question_registry().list_pending(sess.id)
                        if q.id == qid
                    ),
                    None,
                )
                execution_id = getattr(pending, "execution_id", "") or ""
            except Exception:
                execution_id = ""
        with sess.lock:
            # Events from older question producers may omit execution_id. Keep
            # those ownerless instead of assigning the current foreground
            # turn, which could make cancellation resolve a sibling question.
            if (
                execution_id
                and execution_id == sess.execution_id
                and sess.cancel_event.is_set()
            ):
                return
            sess.open_questions[qid] = execution_id
        threading.Thread(
            target=self._ask_permission, args=(sess, data, execution_id),
            daemon=True,
        ).start()

    def _refuse_system_access(self, sess: _Session, data: dict) -> None:
        execution_id = data.get("execution_id") or ""
        with sess.lock:
            if not execution_id or execution_id != sess.execution_id:
                return
            needed = data.get("required_capabilities") or []
            sess.stop_error = (
                "the turn needs system access an editor cannot grant"
                + (f" ({', '.join(map(str, needed))})" if needed else "")
                + "; grant it in OpenProgram and send the prompt again"
            )
        self._cancel(sess, "system_access_unavailable")

    def _ask_permission(
        self, sess: _Session, data: dict,
        expected_execution_id: str | None = None,
    ) -> None:
        qid = data.get("id")
        if not qid:
            return
        if expected_execution_id is None:
            expected_execution_id = data.get("execution_id") or ""
        with sess.lock:
            if sess.open_questions.get(qid) != expected_execution_id:
                return
            if (
                expected_execution_id
                and expected_execution_id == sess.execution_id
                and sess.cancel_event.is_set()
            ):
                return
        tool = data.get("tool") or "?"
        expires_at = data.get("expires_at")
        timeout = (max(1.0, float(expires_at) - time.time())
                   if isinstance(expires_at, (int, float)) and expires_at > 0
                   else _PERMISSION_TIMEOUT_S)
        resp = None
        try:
            resp = self._conn.request("session/request_permission", {
                "sessionId": sess.id,
                "toolCall": {
                    "toolCallId": qid,
                    "title": tool,
                    "kind": _tool_kind(tool),
                    "status": "pending",
                    "rawInput": data.get("args") or {},
                },
                "options": [
                    {"optionId": _ALLOW_ONCE, "name": "Allow",
                     "kind": "allow_once"},
                    {"optionId": _ALLOW_ALWAYS, "name": "Always allow",
                     "kind": "allow_always"},
                    {"optionId": _REJECT_ONCE, "name": "Reject",
                     "kind": "reject_once"},
                ],
            }, timeout=timeout)
        except Exception:
            # The client does not take permission requests, answered with
            # an error, did not answer in time or hung up: the operation is
            # declined, so the turn goes on without it instead of waiting.
            _log.warning("ACP permission request for %s not answered; declining",
                         tool, exc_info=True)
        finally:
            with sess.lock:
                sess.open_questions.pop(qid, None)

        if not expected_execution_id:
            return
        outcome = (resp or {}).get("outcome") or {}
        choice = outcome.get("optionId")
        action = (
            "execution.wait.answer"
            if outcome.get("outcome") == "selected"
            and choice in (_ALLOW_ONCE, _ALLOW_ALWAYS)
            else "execution.wait.decline"
        )
        value = (
            {"answer": "允许", "scope": "always" if choice == _ALLOW_ALWAYS else "once"}
            if action == "execution.wait.answer" else None
        )
        self._resolve_wait(data, expected_execution_id, action, value)

    def _resolve_wait(self, data: dict, execution_id: str, action: str, value) -> None:
        """Answer or decline one durable wait through the control service."""
        try:
            from openprogram.runtime.permissions.authority import local_owner_authority
            from openprogram.runtime.execution import (
                default_control_service, default_store, submit_wait_command,
            )
            execution = default_store().get_execution(execution_id)
            if execution is None:
                return
            asyncio.run(submit_wait_command(
                default_control_service(), action=action,
                command_id=f"acp-wait:{uuid.uuid4().hex}",
                execution_id=execution_id,
                expected_version=execution.status_version,
                actor=local_owner_authority(), wait_id=data.get("id"),
                generation=int(data.get("wait_generation") or 0), value=value,
            ))
        except Exception:
            # A wait already settled (cancelled, expired) refuses the command.
            _log.debug("ACP question resolution failed", exc_info=True)


def _last_failure(store, execution_id: str) -> str | None:
    """The failure the execution's last attempt recorded, if any.

    A turn resumed after a permission answer fails in a later attempt than
    the one the prompt started; its error lives only on that attempt.
    """
    try:
        from openprogram.runtime.execution.attempts import AttemptStore

        attempts = AttemptStore(store).list_for_execution(execution_id)
    except Exception:
        _log.debug("attempts of %s not read", execution_id, exc_info=True)
        return None
    for attempt in sorted(attempts, key=lambda a: a.generation, reverse=True):
        if attempt.failure_text:
            return attempt.failure_text
    return None


def _stop(result, final, last_failure: str | None = None) -> dict:
    """The ``session/prompt`` answer for a turn that ended uncancelled.

    A failed turn is a JSON-RPC error, so the client shows why it failed
    instead of an ordinary stop. ``result`` is the first attempt's; when it
    did not fail, the error is the one ``last_failure`` read from the
    attempt that did.
    """
    from openprogram.runtime.execution.model import ExecutionStatus

    status = getattr(final, "status", None)
    if status in (ExecutionStatus.CANCELLED, ExecutionStatus.INTERRUPTED):
        return {"stopReason": "cancelled"}
    failed_result = result is not None and getattr(result, "failed", False)
    if failed_result or status is ExecutionStatus.FAILED:
        reason = getattr(final, "reason_code", None)
        message = ((getattr(result, "error", None) if failed_result else None)
                   or last_failure
                   or (f"the turn failed ({reason})" if reason else "the turn failed"))
        data = {"reason": (getattr(result, "error_reason", None) if failed_result else None)
                or reason}
        if failed_result and getattr(result, "error_retryable", None) is not None:
            data["retryable"] = bool(result.error_retryable)
        raise RPCError(INTERNAL_ERROR, str(message), data)
    if result is None and final is None:
        raise RPCError(INTERNAL_ERROR, "the turn ended without a result")
    return {"stopReason": "end_turn"}


def _locations(raw_input) -> list[dict]:
    """Best-effort file locations so editors can follow along."""
    if not isinstance(raw_input, dict):
        return []
    for key in ("path", "file_path", "file", "filename"):
        val = raw_input.get(key)
        if isinstance(val, str) and val:
            loc: dict[str, Any] = {"path": os.path.abspath(val)}
            line = raw_input.get("line")
            if isinstance(line, int):
                loc["line"] = line
            return [loc]
    return []



def serve_stdio(*, agent_id: str = "main", permission_mode: str | None = None) -> int:
    """Run an ACP server on stdin/stdout until the editor disconnects."""
    # stdout is the protocol channel — anything else printed there corrupts
    # the stream, so logging goes to stderr. The CLI has already attached the
    # process handler at WARNING; this moves the level for the ACP run
    # (OPENPROGRAM_LOG_LEVEL still wins).
    from openprogram.host.process_logging import configure_process_logging

    configure_process_logging(os.environ.get("OPENPROGRAM_ACP_LOG", "WARNING"))
    ACPServer(agent_id=agent_id, permission_mode=permission_mode).serve()
    return 0
