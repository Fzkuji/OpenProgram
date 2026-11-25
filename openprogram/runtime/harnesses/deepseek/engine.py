"""One conversation turn run by DeepSeek Harness over ACP.

DeepSeek Harness makes the model calls and runs its own tools; this module
sends the person's message as ``session/prompt`` and turns the session's
updates into the chat's frames and cards: committed thoughts and replies
(``agent_thought_chunk``, ``agent_message_chunk``), tool calls and their
results (``tool_call``, ``tool_call_update``) and context use
(``usage_update``). Its permission requests reach the person as live
waits, unless the branch runs with full access. Stop is ``session/cancel``.

The session is a real DeepSeek Harness session saved with the app's own,
kept per branch in the conversation's ``harness_threads`` like a Codex
thread: a conversation synced from the app continues its session, and
turns the session did not run reach it as a handoff transcript (ACP has
no fork, so a new branch starts a session with one). The ids of the
messages it wrote here are kept as ``own_turns`` so the app sync does
not write them twice.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Optional

from openprogram.runtime.harnesses import handoff, split_ref
from openprogram.runtime.harnesses.codex.client import AppServer, AppServerError
from openprogram.runtime.harnesses.deepseek import client

_log = logging.getLogger(__name__)

PROVIDER = "deepseek-harness"
HARNESS_ID = "deepseek-harness"
_OWN_TURNS_KEPT = 500
#: How long a Stop waits for the turn to end before the process is stopped.
CANCEL_GRACE_SECONDS = 8.0
#: A turn's longest wait for its prompt to settle.
PROMPT_TIMEOUT_SECONDS = 6 * 60 * 60
_EFFORT_ORDER = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]


def dsh_effort(effort: Optional[str], supported: Optional[list[str]]) -> Optional[str]:
    """DeepSeek Harness's reasoning effort nearest OpenProgram's ``effort``; None keeps the session's."""
    value = str(effort or "").strip().lower()
    if not value or value in ("auto", "default"):
        return None
    value = {"none": "off"}.get(value, value)
    if not supported:
        return value
    if value in supported:
        return value
    if value not in _EFFORT_ORDER:
        return None
    rank = _EFFORT_ORDER.index(value)
    known = [level for level in supported if level in _EFFORT_ORDER]
    if not known:
        return None
    return min(known, key=lambda level: (abs(_EFFORT_ORDER.index(level) - rank), -_EFFORT_ORDER.index(level)))


def _error_reason(message: str):
    from openprogram.models.providers.utils.failures.errors import ErrorReason
    text = message.lower()
    if "balance" in text or "quota" in text or "rate limit" in text or "429" in text:
        return ErrorReason.RATE_LIMIT
    if "unauthorized" in text or "api key" in text or "401" in text:
        return ErrorReason.AUTHENTICATION
    if "context" in text and ("length" in text or "window" in text):
        return ErrorReason.CONTEXT_LENGTH
    return ErrorReason.UNKNOWN


def _content_text(content: Any) -> str:
    """The text of ACP content: a block, a list of blocks, or tool-call content entries."""
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        if content.get("type") == "content":
            return _content_text(content.get("content"))
        return ""
    if isinstance(content, list):
        return "\n".join(part for part in (_content_text(c) for c in content) if part)
    return ""


class DeepSeekHarnessEngine:
    """The engine registered for ``deepseek-harness``."""

    provider = PROVIDER
    label = "DeepSeek Harness"

    def run_turn(self, *, req, history, on_event, cancel_event, assistant_msg_id,
                 ordered_blocks_out, execution_context) -> tuple[str, dict, list[dict]]:
        return DshTurn(
            req=req, history=history or [], on_event=on_event, cancel_event=cancel_event,
            assistant_msg_id=assistant_msg_id or f"{req.user_msg_id}_reply",
            ordered_blocks_out=ordered_blocks_out, execution_context=execution_context,
        ).run()


class DshTurn:
    def __init__(self, *, req, history, on_event, cancel_event, assistant_msg_id,
                 ordered_blocks_out, execution_context) -> None:
        self.req = req
        self.history = history
        self.on_event = on_event
        self.cancel_event = cancel_event or threading.Event()
        self.assistant_msg_id = assistant_msg_id
        self.blocks: list[dict] = ordered_blocks_out if ordered_blocks_out is not None else []
        self.execution_context = execution_context or {}
        self.tool_calls: list[dict] = []
        self.usage: dict = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "provider_request_count": 0, "agent_iteration_count": 0, "context_tokens": 0,
        }
        _, self.model_id = split_ref(getattr(req, "model_override", None))
        self.messages: "queue.Queue[dict]" = queue.Queue()
        self.session_id: Optional[str] = None
        self.cwd: Optional[str] = None
        self.message_ids: list[str] = []
        self.last_message: Optional[str] = None
        self.open_cards: dict[str, dict] = {}
        self.server: Optional[AppServer] = None
        self.pool_key = f"{req.session_id}:{handoff.branch_key(req)}"
        self.prompt_sent = threading.Event()
        self.settled = False

    # -- frames and blocks ------------------------------------------------

    def _emit(self, event: dict) -> None:
        self.on_event({"type": "chat_response", "data": {
            "type": "stream_event", "session_id": self.req.session_id,
            "msg_id": self.req.user_msg_id, "event": event,
        }})

    def _add_text(self, kind: str, text: str, message_id: Optional[str]) -> None:
        if not text:
            return
        last = self.blocks[-1] if self.blocks else None
        if last is not None and last.get("type") == kind:
            if message_id != self.last_message and last["text"] and not last["text"][-1].isspace():
                text = "\n\n" + text  # another message: another passage
            last["text"] += text
        else:
            block = {"type": kind, "text": text}
            if last is None or last.get("type") == "tool":
                block["round_start"] = True
            self.blocks.append(block)
        self.last_message = message_id
        self._emit({"type": kind, "text": text})

    def _start_card(self, call_id: str, tool: str, arguments: Any) -> None:
        if call_id in self.open_cards:
            return
        if not isinstance(arguments, dict):
            arguments = {"input": arguments}
        # A card's input is its arguments as JSON, as OpenProgram's own tools' are.
        shown = json.dumps(arguments, ensure_ascii=False, default=str)
        block = {"type": "tool", "tool": tool, "tool_call_id": call_id, "input": shown}
        last = self.blocks[-1] if self.blocks else None
        if last is None or last.get("type") == "tool":
            block["round_start"] = True
        self.blocks.append(block)
        self.open_cards[call_id] = block
        self.last_message = None
        self._emit({"type": "tool_use", "tool": tool, "input": shown, "tool_call_id": call_id})

    def _end_card(self, call_id: str, result: str, is_error: bool, outcome: str) -> None:
        block = self.open_cards.pop(call_id, None)
        if block is None:
            return
        from openprogram.runtime.conversation.event_parsing import STORED_RESULT_KEY, shorten
        shown = shorten(result)
        event = {"type": "tool_result", "tool": block["tool"], "result": shown, "is_error": is_error,
                 "outcome": outcome, "tool_call_id": call_id}
        if shown != result:
            event[STORED_RESULT_KEY] = result
        self.tool_calls.append({"id": call_id, "tool_call_id": call_id, "tool": block["tool"],
                                "input": block["input"], "result": shown, "is_error": is_error, "outcome": outcome})
        self._emit(event)

    def _context(self, used: int, size: int) -> None:
        self.usage["context_tokens"] = used
        self.usage["agent_iteration_count"] += 1
        self.on_event({"type": "chat_response", "data": {
            "type": "context_usage", "session_id": self.req.session_id, "msg_id": self.req.user_msg_id,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
            "model": f"{PROVIDER}/{self.model_id}" if self.model_id else PROVIDER,
            "context_window": size, "context_tokens": used,
        }})

    # -- setup ------------------------------------------------------------

    def _workdir(self) -> str:
        import os
        try:
            from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
            workdir = runtime_location_for(self.req.session_id, branch_id=getattr(self.req, "branch_id", None)).get("workdir")
            if workdir:
                return str(workdir)
        except Exception:  # noqa: BLE001 - the server's folder is never a conversation's
            _log.debug("workdir not resolved for %s", self.req.session_id, exc_info=True)
        return os.path.expanduser("~")

    def _session(self, server: AppServer, fresh: bool) -> tuple[Optional[str], list]:
        """Open the branch's session; returns the handoff for its prompt and its config options."""
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
        key = handoff.branch_key(self.req)
        records = handoff.read_records(db, self.req.session_id, PROVIDER)
        plan = handoff.plan_thread(records, key, self.history, getattr(self.req, "user_text", None))
        loaded: set = server.__dict__.setdefault("loaded_sessions", set())
        loaded_at: dict = server.__dict__.setdefault("loaded_at", {})
        options: list = server.__dict__.setdefault("options", {}).get(plan.thread_id or "", [])
        note: Optional[str] = None
        session_id: Optional[str] = None
        cwd = self._workdir()
        if plan.action == "resume":
            session_id = plan.thread_id
            record = next((r for r in records.values() if isinstance(r, dict) and r.get("thread_id") == session_id), {})
            # A session resumes only in the folder it was made in.
            imported = (db.get_session(self.req.session_id) or {}).get("imported_from") or {}
            cwd = str(record.get("cwd") or (imported.get("cwd") if isinstance(imported, dict) else None) or cwd)
            if session_id in loaded and not fresh and _synced_since(records, session_id, loaded_at.get(session_id, 0)):
                # The app added turns since this process read the session.
                try:
                    server.request("session/close", {"sessionId": session_id})
                    loaded.discard(session_id)
                except AppServerError as exc:
                    if exc.exited:
                        raise
                    # Still open here: the turn goes on in it rather than in a new session.
                    _log.info("DeepSeek Harness session %s not reloaded (%s)", session_id, exc)
            if fresh or session_id not in loaded:
                try:
                    answer = server.request("session/resume", {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
                                            timeout=120)
                    options = (answer or {}).get("configOptions") or []
                except AppServerError as exc:
                    if exc.exited:
                        raise
                    _log.info("DeepSeek Harness session %s not resumed (%s); starting another", session_id, exc)
                    session_id, plan = None, handoff.ThreadPlan("start", missed=list(self.history))
                    cwd = self._workdir()
            note = handoff.handoff_text(plan.missed) if session_id else None
        created = session_id is None
        if session_id is None:
            missed = plan.missed if plan.action != "fork" else list(self.history)
            answer = server.request("session/new", {"cwd": cwd, "mcpServers": []}, timeout=120)
            session_id = (answer or {}).get("sessionId")
            if not session_id:
                raise AppServerError("DeepSeek Harness did not start a session")
            options = (answer or {}).get("configOptions") or []
            note = handoff.handoff_text(missed)
        if session_id not in loaded:
            loaded_at[session_id] = time.time()
        loaded.add(session_id)
        server.__dict__["options"][session_id] = options
        self.session_id, self.cwd = session_id, cwd
        record = {"thread_id": session_id, "cwd": cwd}
        if (records.get(key) or {}).get("thread_id") != session_id:
            # This branch's first record of the session: a resumed one holds this branch's
            # history up to the handoff it has yet to read, a new one nothing yet.
            record.update(seen=None if created else handoff.resumed_position(self.history, plan),
                          own_turns=[], shared=False)
        handoff.write_record(db, self.req.session_id, PROVIDER, key, record)
        if created:
            # A new session's current model is the harness's default; a resumed one's is its own.
            _remember_models(options)
        return note, options

    def _configure(self, server: AppServer, options: list) -> None:
        """Pick the branch's model and effort for the session, where they differ."""
        def current(options: list) -> dict:
            return {o.get("id"): o.get("currentValue") for o in options if isinstance(o, dict)}

        def setting(config_id: str, value: str) -> list:
            answer = server.request("session/set_config_option",
                                    {"sessionId": self.session_id, "configId": config_id, "value": value})
            return (answer or {}).get("configOptions") or options

        model = self.model_id
        if model and model.lower() not in ("default", "auto"):
            now = current(options).get("model")
            if not now or client.model_id(str(now)) != model:
                options = setting("model", client.option_value(model))
        # The model's own effort levels, read after it is chosen.
        _, efforts, _ = client.read_options(options)
        # A model with no effort levels takes none; Auto asks for the model's own again,
        # since an effort set earlier stays with the session.
        effort = (dsh_effort(getattr(self.req, "thinking_effort", None), efforts)
                  or self._default_effort()) if efforts else None
        if effort and current(options).get("reasoning_effort") != effort:
            options = setting("reasoning_effort", effort)
        server.__dict__.setdefault("options", {})[self.session_id] = options

    def _default_effort(self) -> Optional[str]:
        try:
            from openprogram.runtime.harnesses.models import harness_model
            return (harness_model(PROVIDER, self.model_id or "default") or {}).get("default_effort")
        except Exception:  # noqa: BLE001 - the session keeps its effort
            return None

    def _prompt_text(self, note: Optional[str]) -> str:
        text = self.req.user_text or ""
        try:
            from openprogram.runtime.permissions.authority import render_model_input_from
            text = render_model_input_from(self.req, text)
        except Exception:  # noqa: BLE001 - the person's own text still goes
            pass
        return f"{note}\n\n{text}" if note else text

    def _prompt(self, server: AppServer, note: Optional[str]) -> list[dict]:
        """The prompt's blocks: the text, and the attached images where DeepSeek Harness reads them."""
        images = [a for a in self.req.attachments or [] if isinstance(a, dict) and a.get("type") == "image"]
        blocks: list[dict] = []
        left = 0
        for image in images:
            data = image.get("data")
            if not data and image.get("path"):
                try:
                    import base64
                    from pathlib import Path
                    data = base64.b64encode(Path(str(image["path"])).read_bytes()).decode("ascii")
                except OSError:
                    data = None
            if getattr(server, "takes_images", False) and isinstance(data, str) and data:
                blocks.append({"type": "image", "data": data,
                               "mimeType": str(image.get("media_type") or "image/png").partition(";")[0]})
            else:
                left += 1
        text = self._prompt_text(note)
        if left:
            # Never dropped unsaid: the agent can tell the person it did not see them.
            text += (f"\n\n[{left} attached image{'s' if left > 1 else ''} could not be passed on "
                     "to DeepSeek Harness.]")
        return [{"type": "text", "text": text}, *blocks]

    # -- the turn ---------------------------------------------------------

    def run(self) -> tuple[str, dict, list[dict]]:
        server, fresh = client.pool().acquire(self.pool_key, extra_args=[], env={})
        self.server = server
        unlisten = server.listen(self._listen)
        crashed = finished = False
        marked: Optional[tuple[str, str]] = None
        try:
            note, options = self._session(server, fresh)
            marked = (PROVIDER, str(self.session_id))
            handoff.enter_running(marked)
            if self.cancel_event.is_set():
                return "", self.usage, self.tool_calls  # stopped before it was sent
            self._configure(server, options)
            if self.cancel_event.is_set():
                return "", self.usage, self.tool_calls
            outcome: dict = {}

            def prompt() -> None:
                try:
                    self.prompt_sent.set()
                    outcome["answer"] = server.request("session/prompt", {
                        "sessionId": self.session_id,
                        "prompt": self._prompt(server, note),
                    }, timeout=PROMPT_TIMEOUT_SECONDS)
                except AppServerError as exc:
                    outcome["error"] = exc
                finally:
                    self.messages.put({"method": "openprogram/settled"})

            threading.Thread(target=prompt, name="dsh-prompt", daemon=True).start()
            self._drain()
            error = outcome.get("error")
            if isinstance(error, AppServerError):
                if error.exited:
                    raise error
                if not self.cancel_event.is_set():
                    from openprogram.models.providers.utils.failures.errors import LLMError
                    message = str(error).removeprefix("Internal error: ").removeprefix("turn failed: ")
                    raise LLMError(message=f"DeepSeek Harness: {message}", reason=_error_reason(message),
                                   provider=PROVIDER, model=self.model_id)
            finished = True
        except AppServerError as exc:
            crashed = exc.exited or not server.alive or self.prompt_sent.is_set() and not self.settled
            if self.cancel_event.is_set() and crashed:
                finished = True
            else:
                from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
                tail = server.stderr_tail().splitlines()[-1:] if crashed else []
                message = str(exc) if not crashed else "DeepSeek Harness stopped unexpectedly" + (f": {tail[0]}" if tail else ".")
                raise LLMError(message=message, reason=ErrorReason.PROVIDER_INTERNAL if crashed else ErrorReason.UNKNOWN,
                               provider=PROVIDER, model=self.model_id) from exc
        except BaseException:
            # Anything else leaves a prompt running with nobody reading it: stop it and the process.
            if self.prompt_sent.is_set() and not self.settled:
                crashed = True
                try:
                    server.notify("session/cancel", {"sessionId": self.session_id})
                except AppServerError:
                    pass
            raise
        finally:
            unlisten()
            for call_id in list(self.open_cards):
                self._end_card(call_id, "Stopped", True, "cancelled")
            if crashed or not server.alive:
                client.pool().discard(self.pool_key, server)
            else:
                client.pool().release(self.pool_key, server)
            if finished:
                self._remember_seen()
            elif self.message_ids:
                # What it wrote is the session's, though the turn failed: the session holds the
                # person's message, so the next turn hands over only what comes after it.
                self._remember_seen(seen=False, holds=getattr(self.req, "user_msg_id", None))
            if marked:
                handoff.leave_running(marked)
        from openprogram.state.reply_text import reply_text
        return reply_text(self.blocks).strip(), self.usage, self.tool_calls

    def _remember_seen(self, *, seen: bool = True, holds: Optional[str] = None) -> None:
        if not self.session_id:
            return
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            key = handoff.branch_key(self.req)
            own = list((handoff.read_records(db, self.req.session_id, PROVIDER).get(key) or {}).get("own_turns") or [])
            own = (own + [m for m in self.message_ids if m not in own])[-_OWN_TURNS_KEPT:]
            update: dict = {"thread_id": self.session_id, "cwd": self.cwd, "own_turns": own}
            if seen:
                update.update(seen=self.assistant_msg_id, at=time.time())
            elif holds:
                update.update(handoff.held_record(self.history, holds, getattr(self.req, "user_text", None)))
            handoff.write_record(db, self.req.session_id, PROVIDER, key, update)
        except Exception:  # noqa: BLE001 - the next turn hands off what it cannot tell
            _log.warning("DeepSeek Harness session record not kept for %s", self.req.session_id, exc_info=True)

    def _listen(self, message: dict) -> bool:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("sessionId") and self.session_id and params["sessionId"] != self.session_id:
            return False
        if message.get("method") not in ("session/update", "session/request_permission", "openprogram/exited"):
            return False
        if message.get("method") == "session/update" and not self.prompt_sent.is_set():
            return True  # a late update of an earlier turn, not this one's
        self.messages.put(message)
        return True

    def _drain(self) -> None:
        stop_seen: Optional[float] = None
        while True:
            if self.cancel_event.is_set() and self.prompt_sent.is_set():
                if stop_seen is None:
                    stop_seen = time.monotonic()
                    try:
                        assert self.server is not None
                        self.server.notify("session/cancel", {"sessionId": self.session_id})
                    except AppServerError:
                        _log.debug("DeepSeek Harness cancel not sent", exc_info=True)
                elif time.monotonic() - stop_seen > CANCEL_GRACE_SECONDS:
                    assert self.server is not None
                    self.server.kill()
                    return
            try:
                message = self.messages.get(timeout=0.1)
            except queue.Empty:
                continue
            method = message.get("method")
            if method == "openprogram/settled":
                self.settled = True
                return
            if method == "openprogram/exited":
                raise AppServerError("DeepSeek Harness stopped", exited=True)
            if method == "session/request_permission":
                self._answer(message)
            else:
                self._update((message.get("params") or {}).get("update") or {})

    def _update(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        message_id = update.get("messageId")
        # Every message it writes, a tool call's too, so the app sync knows the turn as this one's.
        if message_id and str(message_id) not in self.message_ids:
            self.message_ids.append(str(message_id))
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = _content_text(update.get("content"))
            self._add_text("text" if kind == "agent_message_chunk" else "thinking", text, message_id)
        elif kind == "tool_call":
            self._start_card(str(update.get("toolCallId") or ""), str(update.get("title") or "tool"),
                             update.get("rawInput") if update.get("rawInput") is not None else {})
        elif kind == "tool_call_update":
            status = update.get("status")
            if status in ("completed", "failed"):
                result = _content_text(update.get("content"))
                if not result and update.get("rawOutput") is not None:
                    result = json.dumps(update["rawOutput"], ensure_ascii=False, default=str)
                self._end_card(str(update.get("toolCallId") or ""), result, status == "failed",
                               "error" if status == "failed" else "completed")
        elif kind == "usage_update":
            used, size = update.get("used"), update.get("size")
            if isinstance(used, int) and isinstance(size, int) and size > 0:
                self._context(used, size)

    def _answer(self, message: dict) -> None:
        assert self.server is not None
        params = message.get("params") or {}
        call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        card = self.open_cards.get(str(call.get("toolCallId") or "")) or {}
        allow = {"outcome": {"outcome": "selected", "optionId": _option(params, "allow_once", "allow-once")}}
        reject = {"outcome": {"outcome": "selected", "optionId": _option(params, "reject_once", "reject-once")}}
        if str(getattr(self.req, "permission_mode", None) or "bypass") == "bypass":
            self.server.respond(message["id"], allow)
            return
        from openprogram.runtime.permissions.approvals.live import ask_live_approval, live_owner_from
        owner = live_owner_from(self.execution_context, self.req.session_id, HARNESS_ID)
        if owner is None:
            self.server.respond(message["id"], reject)
            return
        # The request names its tool call; the card may not exist yet.
        tool = str(call.get("title") or card.get("tool") or "tool")
        arguments = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else _loads(card.get("input"))
        decision = ask_live_approval(
            owner, tool=tool, arguments=arguments, prompt=f"允许执行 {tool}？", detail="",
            allowed_scopes=("once",), risk_level="high" if call.get("kind") == "execute" else "medium",
            publish=self.on_event, cancel_event=self.cancel_event,
            ended=lambda server=self.server: server is None or not server.alive,
        )
        if decision.allowed:
            self.server.respond(message["id"], allow)
        elif decision.outcome == "deny":
            self.server.respond(message["id"], reject)
        else:
            self.server.respond(message["id"], {"outcome": {"outcome": "cancelled"}})


def _option(params: dict, kind: str, fallback: str) -> str:
    """The id of the option of ``kind`` the request offers (ACP ``options``), else the usual one."""
    for option in params.get("options") or []:
        if isinstance(option, dict) and option.get("kind") == kind and option.get("optionId"):
            return str(option["optionId"])
    return fallback


def _loads(text: Any) -> dict:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {"input": text}
    return value if isinstance(value, dict) else {"input": value}


def _synced_since(records: dict, session_id: str, since: float) -> bool:
    """The app sync moved ``session_id`` on after ``since``."""
    return any(isinstance(r, dict) and r.get("thread_id") == session_id
               and float(r.get("synced_at") or 0) > since for r in (records or {}).values())


def _remember_models(options: list) -> None:
    """Keep the models a live session offers as the harness's listing."""
    try:
        from openprogram.runtime.harnesses.models import remember_options
        remember_options(HARNESS_ID, options)
    except Exception:  # noqa: BLE001 - the listing is read again later
        _log.debug("DeepSeek Harness models not kept", exc_info=True)
