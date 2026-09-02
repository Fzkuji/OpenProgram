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

**Permissions.** OpenProgram's approval gate registers a ``kind="approval"``
question on the shared QuestionRegistry and blocks. Subscribing to
``question.asked`` on the event bus catches those, forwards them as ACP
``session/request_permission``, and answers with
``resolve_question_and_broadcast``. Nothing about the gate itself changes,
so authority checks, hard constraints, permission rules and the
``allow_always`` rule-persistence path all behave exactly as in the web UI.

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
import uuid
from typing import Any

from openprogram.acp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    Connection,
    RPCError,
)
from openprogram.execution.model import ExecutionStatus

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
    "bash": "execute", "shell": "execute", "run": "execute",
    "think": "think", "todo_write": "think", "task": "think",
    "delete": "delete", "move": "move",
}

_ALLOW_ONCE = "allow_once"
_ALLOW_ALWAYS = "allow_always"
_REJECT_ONCE = "reject_once"


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

    def __init__(self, session_id: str, cwd: str) -> None:
        self.id = session_id
        self.cwd = cwd
        self.cancel_event = threading.Event()
        self.execution_id: str | None = None
        # Cancellation may arrive while durable admission is in progress,
        # before the execution identity can be published to the session.
        self.prompt_pending = False
        self.cancel_requested = False
        self.cancel_reason = ""
        # Question ids forwarded to the client and still unanswered — a
        # cancel must resolve them or the tool gate sits on its Event for
        # the full 300s timeout.
        self.open_questions: dict[str, str] = {}
        self.lock = threading.Lock()


class ACPServer:
    """The agent side of ACP. ``serve()`` runs until the client hangs up."""

    def __init__(
        self,
        reader=None,
        writer=None,
        *,
        agent_id: str = "main",
        permission_mode: str = "ask",
    ) -> None:
        self._conn = Connection(
            reader if reader is not None else sys.stdin,
            writer if writer is not None else sys.stdout,
            self._handle,
        )
        self._agent_id = agent_id
        self._permission_mode = permission_mode
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._unsubscribe = None
        self._client_caps: dict = {}

    # -- lifecycle --------------------------------------------------------

    def serve(self) -> None:
        self._subscribe_questions()
        try:
            self._conn.serve_forever()
        finally:
            if self._unsubscribe is not None:
                try:
                    self._unsubscribe()
                except Exception:
                    _log.debug("ACP question unsubscribe failed", exc_info=True)

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
        with self._lock:
            self._sessions[sid] = _Session(sid, cwd)
        return {"sessionId": sid}

    def _session_load(self, params: dict) -> dict:
        sid = params.get("sessionId")
        cwd = params.get("cwd") or os.getcwd()
        if not sid:
            raise RPCError(INVALID_PARAMS, "sessionId is required")
        from openprogram.agent.session_db import default_db

        db = default_db()
        if db.get_session(sid) is None:
            raise RPCError(INVALID_PARAMS, f"unknown session: {sid}")
        with self._lock:
            self._sessions[sid] = _Session(sid, cwd)
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
        with sess.lock:
            execution_id = sess.execution_id
            if execution_id is None:
                if not sess.prompt_pending:
                    return None
                sess.cancel_requested = True
                sess.cancel_reason = "prompt_cancel"
                sess.cancel_event.set()
                return None
        try:
            from openprogram.agent.production_driver import cancel_canonical_execution
            cancelled = asyncio.run(cancel_canonical_execution(execution_id))
            if cancelled is None:
                return None
        except Exception:
            # Notifications have no error response; leave the live prompt
            # untouched and surface infrastructure failures in the log.
            _log.warning("ACP execution cancellation failed for %s",
                         execution_id, exc_info=True)
            return None
        # The service call can race prompt teardown and a successor prompt.
        # Revalidate the identity before touching local event/question state.
        with sess.lock:
            if (
                sess.execution_id != execution_id
            ):
                return None
            sess.cancel_event.set()
            qids = [
                qid for qid, question_execution_id in sess.open_questions.items()
                if question_execution_id == execution_id
            ]
            for qid in qids:
                sess.open_questions.pop(qid, None)
        # Every permission request still in flight must be answered — the
        # spec requires the "cancelled" outcome, and the tool gate is
        # blocked on the matching question's Event.
        from openprogram.agent.questions import resolve_question_and_broadcast

        for qid in qids:
            try:
                resolve_question_and_broadcast(qid, "cancelled", None)
            except Exception:
                _log.debug("ACP question cancellation failed", exc_info=True)
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

        from openprogram.agent.authority import local_owner_authority
        from openprogram.agent.dispatcher import TurnRequest
        from openprogram.agent.production_driver import CanonicalAgentAdapter

        user_msg_id = uuid.uuid4().hex[:12]
        request = TurnRequest(
            session_id=sess.id,
            user_text=text,
            agent_id=self._agent_id,
            source="acp",
            permission_mode=self._permission_mode,
            attachments=_blocks_to_attachments(blocks) or None,
            additional_working_dirs=[sess.cwd],
            user_msg_id=user_msg_id,
            **local_owner_authority(),
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
            cancel_reason = sess.cancel_reason or "prompt_cancel"
            if not cancelled_before_activation:
                sess.prompt_pending = False
                sess.execution_id = execution_id
        if cancelled_before_activation:
            adapter.fail_admission(
                admission, reason_code=cancel_reason,
                target=ExecutionStatus.CANCELLED,
            )
            with sess.lock:
                sess.prompt_pending = False
                sess.cancel_requested = False
                sess.cancel_reason = ""
            return {"stopReason": "cancelled"}
        try:
            _active, result = asyncio.run(adapter.activate(admission))
        finally:
            with sess.lock:
                if sess.execution_id == execution_id:
                    sess.execution_id = None

        if sess.cancel_event.is_set():
            return {"stopReason": "cancelled"}
        if result.failed:
            return {"stopReason": "refusal"}
        return {"stopReason": "end_turn"}

    # -- events → session/update -----------------------------------------

    def _notify(self, session_id: str, update: dict) -> None:
        try:
            self._conn.notify("session/update",
                              {"sessionId": session_id, "update": update})
        except Exception:
            _log.debug("acp notify failed", exc_info=True)

    def _on_event(self, sess: _Session, env: dict) -> None:
        if env.get("type") != "chat_response":
            return
        data = env.get("data") or {}
        if data.get("type") != "stream_event":
            return
        ev = data.get("event") or {}
        etype = ev.get("type")

        if etype == "text":
            self._notify(sess.id, {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": ev.get("text") or ""},
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
        from openprogram.events import get_event_bus

        self._unsubscribe = get_event_bus().subscribe(
            self._on_question, types={"question.asked"})

    def _on_question(self, event) -> None:
        data = getattr(event, "payload", None) or {}
        if data.get("kind") != "approval":
            return  # runtime.ask / form questions have no ACP equivalent
        sess = self._sessions.get(data.get("session_id") or "")
        if sess is None:
            return
        qid = data.get("id")
        if not qid:
            return
        execution_id = data.get("execution_id") or ""
        if not execution_id:
            try:
                from openprogram.agent.questions import get_question_registry

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

    def _ask_permission(
        self, sess: _Session, data: dict,
        expected_execution_id: str | None = None,
    ) -> None:
        from openprogram.agent.questions import resolve_question_and_broadcast

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
            })
        except Exception:
            _log.debug("acp permission request failed", exc_info=True)
            return
        finally:
            with sess.lock:
                sess.open_questions.pop(qid, None)

        outcome = (resp or {}).get("outcome") or {}
        if outcome.get("outcome") != "selected":
            # "cancelled" — the client dropped the request; decline so the
            # gate stops waiting.
            resolve_question_and_broadcast(qid, "declined", None)
            return
        choice = outcome.get("optionId")
        if choice in (_ALLOW_ONCE, _ALLOW_ALWAYS):
            # The gate reads {"answer", "scope"}; scope="always" is what
            # writes the persistent allow rule.
            resolve_question_and_broadcast(qid, "answered", {
                "answer": "允许",
                "scope": "always" if choice == _ALLOW_ALWAYS else "once",
            })
        else:
            resolve_question_and_broadcast(qid, "declined", None)


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



def serve_stdio(*, agent_id: str = "main", permission_mode: str = "ask") -> int:
    """Run an ACP server on stdin/stdout until the editor disconnects."""
    # stdout is the protocol channel — anything else printed there corrupts
    # the stream, so logging goes to stderr.
    logging.basicConfig(stream=sys.stderr,
                        level=os.environ.get("OPENPROGRAM_ACP_LOG", "WARNING"))
    ACPServer(agent_id=agent_id, permission_mode=permission_mode).serve()
    return 0
