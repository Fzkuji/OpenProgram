"""One conversation turn run by Codex through ``codex app-server``.

Codex makes the model calls and runs the commands, edits, searches and MCP
calls in its own sandbox; this module streams what it does as the chat's
frames and cards, routes its approval requests to the person as live waits,
forwards Stop as ``turn/interrupt`` and steering as ``turn/steer``. The
thread is a real Codex session (``codex resume <id>`` continues it), kept
per branch; turns it did not run reach it as a handoff transcript.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from types import SimpleNamespace
from typing import Any, Optional

from openprogram.runtime.harnesses import handoff, split_ref
from openprogram.runtime.harnesses.codex import items as codex_items
from openprogram.runtime.harnesses.codex.client import AppServer, AppServerError, pool

_log = logging.getLogger(__name__)

PROVIDER = "codex-harness"
#: A model id that runs OpenProgram's own model through the local gateway.
GATEWAY_PREFIX = "openprogram:"
#: How long a Stop waits for Codex to end the turn before its process is stopped.
INTERRUPT_GRACE_SECONDS = 5.0
#: How often a running turn looks for steering.
STEER_POLL_SECONDS = 0.5
#: The context window assumed until Codex reports its own.
DEFAULT_CONTEXT_WINDOW = 272_000

#: OpenProgram's permission modes in Codex's own terms (never emulated):
#: (approvalPolicy, sandbox mode, sandbox policy type, approvals reviewer).
PERMISSION_MODES: dict[str, tuple[str, str, str, Optional[str]]] = {
    "ask": ("untrusted", "read-only", "readOnly", None),
    "plan": ("untrusted", "read-only", "readOnly", None),
    "acceptEdits": ("on-request", "workspace-write", "workspaceWrite", None),
    "auto": ("on-request", "workspace-write", "workspaceWrite", "auto_review"),
    "bypass": ("never", "danger-full-access", "dangerFullAccess", None),
}

#: OpenProgram's effort names Codex does not use, as the nearest Codex level.
#: OpenProgram's "off" is Codex's "none" (the nearest level a model lists, when it has no "none").
_EFFORT_ALIASES = {"off": "none", "auto": None, "default": None}

_MODEL_DEFAULTS = {"", "default", "auto"}


def permission_settings(mode: Optional[str]) -> dict:
    approval, sandbox, policy, reviewer = PERMISSION_MODES.get(str(mode or "bypass"), PERMISSION_MODES["bypass"])
    return {"approvalPolicy": approval, "sandbox": sandbox, "sandboxPolicy": {"type": policy},
            "approvalsReviewer": reviewer or "user"}


def codex_effort(effort: Optional[str], supported: Optional[list[str]] = None) -> Optional[str]:
    """The Codex reasoning effort for OpenProgram's ``effort``; None keeps the model's default."""
    if not effort:
        return None
    value = str(effort).strip()
    value = _EFFORT_ALIASES.get(value, value)
    if value is None:
        return None
    if not supported:
        return value
    if value in supported:
        return value
    order = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
    if value in order:
        rank = order.index(value)
        known = [level for level in supported if level in order]
        if known:
            return min(known, key=lambda level: (abs(order.index(level) - rank), -order.index(level)))
    return None


def _usage_limit_message(info: Any, message: str) -> str:
    if info == "usageLimitExceeded" or (isinstance(info, dict) and "usageLimitExceeded" in info):
        return ("Codex has reached its usage limit for your ChatGPT plan. "
                "It resets later; you can pick another model meanwhile.")
    return message


def _raise_turn_error(error: Optional[dict], model: Optional[str]) -> None:
    from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError

    error = error or {}
    info = error.get("codexErrorInfo")
    message = _usage_limit_message(info, str(error.get("message") or "Codex could not finish this turn."))
    kind = info if isinstance(info, str) else next(iter(info), "") if isinstance(info, dict) else ""
    reason = {
        "usageLimitExceeded": ErrorReason.RATE_LIMIT, "rateLimitExceeded": ErrorReason.RATE_LIMIT,
        "unauthorized": ErrorReason.AUTHENTICATION, "contextWindowExceeded": ErrorReason.CONTEXT_LENGTH,
        "serverOverloaded": ErrorReason.PROVIDER_INTERNAL, "internalServerError": ErrorReason.PROVIDER_INTERNAL,
        "badRequest": ErrorReason.INVALID_REQUEST, "cyberPolicy": ErrorReason.CONTENT_POLICY,
        "httpConnectionFailed": ErrorReason.TRANSPORT, "responseStreamDisconnected": ErrorReason.TRANSPORT,
        "responseStreamConnectionFailed": ErrorReason.TRANSPORT,
    }.get(kind, ErrorReason.UNKNOWN)
    raise LLMError(message=message, reason=reason, provider=PROVIDER, model=model,
                   failure={"codex_error": kind} if kind else None)


class CodexEngine:
    """The engine registered for ``codex-harness``."""

    provider = PROVIDER
    label = "Codex"

    def run_turn(self, *, req, history, on_event, cancel_event, assistant_msg_id,
                 ordered_blocks_out, execution_context) -> tuple[str, dict, list[dict]]:
        return CodexTurn(
            req=req, history=history or [], on_event=on_event, cancel_event=cancel_event,
            assistant_msg_id=assistant_msg_id or f"{req.user_msg_id}_reply",
            ordered_blocks_out=ordered_blocks_out, execution_context=execution_context,
        ).run()


class CodexTurn:
    """The state of one turn: the frames sent, the blocks kept, the usage counted."""

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
        self.thread_id: Optional[str] = None
        self.turn_id: Optional[str] = None
        self.items: dict[str, dict] = {}
        self.open_cards: dict[str, dict] = {}
        self.streamed: set[str] = set()
        self.text_item: Optional[str] = None
        self.plan_updates = 0
        self.steer_images: dict[str, list[str]] = {}
        self.followups: list[list[dict]] = []
        self.last_error: Optional[dict] = None
        self.context_window = DEFAULT_CONTEXT_WINDOW
        self.server: Optional[AppServer] = None
        self.pool_key = f"{req.session_id}:{handoff.branch_key(req)}"

    # -- frames and blocks ------------------------------------------------

    def _emit(self, event: dict) -> None:
        self.on_event({"type": "chat_response", "data": {
            "type": "stream_event", "session_id": self.req.session_id,
            "msg_id": self.req.user_msg_id, "event": event,
        }})

    def _round_start(self) -> bool:
        """A block opens a model response when it is the turn's first or follows a call."""
        last = self.blocks[-1] if self.blocks else None
        return last is None or last.get("type") == "tool"

    def _add_text(self, kind: str, delta: str) -> None:
        if not delta:
            return
        last = self.blocks[-1] if self.blocks else None
        if last is not None and last.get("type") == kind:
            last["text"] += delta
        else:
            block = {"type": kind, "text": delta}
            if self._round_start():
                block["round_start"] = True
            self.blocks.append(block)
        self._emit({"type": kind, "text": delta})

    def _text_delta(self, item_id: str, delta: str) -> None:
        if self.text_item != item_id:
            last = self.blocks[-1] if self.blocks else None
            # Two messages in a row are two passages.
            if (self.text_item is not None and last is not None and last.get("type") == "text"
                    and last["text"] and not last["text"][-1].isspace() and delta and not delta[0].isspace()):
                delta = "\n\n" + delta
            self.text_item = item_id
        self.streamed.add(item_id)
        self._add_text("text", delta)

    def _start_card(self, item_id: str, card: codex_items.ItemCard) -> None:
        if item_id in self.open_cards:
            return
        block = {"type": "tool", "tool": card.tool, "tool_call_id": item_id, "input": card.input}
        if card.provider_executed:
            block["provider_executed"] = True
        if self._round_start():
            block["round_start"] = True
        self.blocks.append(block)
        self.open_cards[item_id] = block
        self.text_item = None
        if not card.provider_executed:
            self._emit({"type": "tool_use", "tool": card.tool, "input": card.input, "tool_call_id": item_id})

    def _end_card(self, item_id: str, result: str, is_error: bool, outcome: str,
                  card: Optional[codex_items.ItemCard] = None) -> None:
        block = self.open_cards.pop(item_id, None)
        if block is None:
            if card is None:
                return
            self._start_card(item_id, card)
            block = self.open_cards.pop(item_id)
        from openprogram.runtime.conversation.event_parsing import shorten
        shown = shorten(result)
        event = {"type": "tool_result", "tool": block["tool"], "result": shown, "is_error": is_error,
                 "outcome": outcome, "tool_call_id": item_id}
        if block.get("provider_executed"):
            if card is not None:
                block["input"] = card.input
            block.update(result=shown, is_error=is_error, outcome=outcome)
            event.update(provider_executed=True, input=block["input"])
        else:
            if shown != result:
                from openprogram.runtime.conversation.event_parsing import STORED_RESULT_KEY
                event[STORED_RESULT_KEY] = result
            self.tool_calls.append({
                "id": item_id, "tool_call_id": item_id, "tool": block["tool"], "input": block["input"],
                "result": shown, "is_error": is_error, "outcome": outcome,
            })
        self._emit(event)

    def _plan(self, plan: list, explanation: Optional[str]) -> None:
        self.plan_updates += 1
        call_id = f"plan-{self.turn_id or 'turn'}-{self.plan_updates}"
        card = codex_items.ItemCard("todo_write", codex_items.plan_input(plan, explanation))
        self._start_card(call_id, card)
        self._end_card(call_id, codex_items.plan_text(plan), False, "completed")

    def _usage(self, token_usage: dict) -> None:
        last = token_usage.get("last") or {}
        window = token_usage.get("modelContextWindow")
        if isinstance(window, int) and window > 0:
            self.context_window = window
        prompt = int(last.get("inputTokens") or 0)
        cached = int(last.get("cachedInputTokens") or 0)
        written = int(last.get("cacheWriteInputTokens") or 0)
        output = int(last.get("outputTokens") or 0)
        if not (prompt or output):
            return
        self.usage["input_tokens"] += max(0, prompt - cached - written)
        self.usage["cache_read_tokens"] += cached
        self.usage["cache_write_tokens"] += written
        self.usage["output_tokens"] += output
        self.usage["provider_request_count"] += 1
        self.usage["agent_iteration_count"] += 1
        self.usage["context_tokens"] = prompt
        self.on_event({"type": "chat_response", "data": {
            "type": "context_usage", "session_id": self.req.session_id, "msg_id": self.req.user_msg_id,
            "usage": {"input_tokens": max(0, prompt - cached - written), "output_tokens": output,
                      "cache_read_tokens": cached, "cache_write_tokens": written},
            "model": f"{PROVIDER}/{self.model_id}" if self.model_id else PROVIDER,
            "context_window": self.context_window,
        }})

    # -- setup ------------------------------------------------------------

    def _cwd(self) -> str:
        import os
        try:
            from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
            workdir = runtime_location_for(self.req.session_id, branch_id=getattr(self.req, "branch_id", None)).get("workdir")
            if workdir:
                return str(workdir)
        except Exception:  # noqa: BLE001 - the server's folder is never a conversation's
            _log.debug("workdir not resolved for %s", self.req.session_id, exc_info=True)
        return os.path.expanduser("~")

    def _launch(self) -> tuple[list[str], dict[str, str], Optional[str]]:
        """Extra ``app-server`` arguments, environment, and the model id Codex is sent."""
        model = self.model_id
        if not model or model.strip().lower() in _MODEL_DEFAULTS:
            return [], {}, None
        if model.startswith(GATEWAY_PREFIX):
            reference = model[len(GATEWAY_PREFIX):]
            try:
                from openprogram.models.gateway.launch import codex_launch
            except ImportError as exc:
                raise AppServerError(
                    "Running OpenProgram's models in Codex needs the local model gateway, "
                    "which this version does not have yet.") from exc
            args, env = codex_launch(reference)
            return list(args), dict(env), reference
        return [], {}, model

    def _listed_model(self) -> Optional[dict]:
        try:
            from openprogram.runtime.harnesses.models import harness_model
            return harness_model(PROVIDER, self.model_id)
        except Exception:  # noqa: BLE001 - an unread listing only loses the effort check
            return None

    def _supported_efforts(self) -> Optional[list[str]]:
        model = self._listed_model()
        return list(model.get("efforts") or []) if model else None

    def _agent(self) -> tuple[list[str], dict]:
        """The branch's Codex agent choice: launch arguments (a profile) and
        thread/turn parameters (a personality, for a model that takes one)."""
        from openprogram.runtime.harnesses import branch_setting
        choice = branch_setting(self.req, "harness_agent")
        if not isinstance(choice, str) or ":" not in choice:
            return [], {}
        kind, _, value = choice.partition(":")
        if kind == "profile" and value:
            return ["-c", f"profile={json.dumps(value)}"], {}
        if kind == "personality" and value:
            listed = self._listed_model()
            if listed is not None and listed.get("personality"):
                return [], {"personality": value}
        return [], {}

    def _thread(self, server: AppServer, fresh: bool, model: Optional[str], cwd: str,
                settings: dict) -> Optional[str]:
        """Open the branch's thread; returns the handoff for its first input, if any."""
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
        key = handoff.branch_key(self.req)
        records = handoff.read_records(db, self.req.session_id, PROVIDER)
        plan = handoff.plan_thread(records, key, self.history)
        common = {"cwd": cwd, "approvalPolicy": settings["approvalPolicy"], "sandbox": settings["sandbox"],
                  "approvalsReviewer": settings["approvalsReviewer"], **settings.get("extra", {})}
        if model:
            common["model"] = model
        loaded: set = server.__dict__.setdefault("loaded_threads", set())
        note: Optional[str] = None
        thread_id: Optional[str] = None
        if plan.action == "resume":
            thread_id = plan.thread_id
            if fresh or thread_id not in loaded:
                try:
                    server.request("thread/resume", {"threadId": thread_id, "excludeTurns": True, **common})
                except AppServerError as exc:
                    if exc.exited:
                        raise
                    _log.info("Codex thread %s not resumed (%s); starting another", thread_id, exc)
                    thread_id, plan = None, handoff.ThreadPlan("start", missed=list(self.history))
            note = handoff.handoff_text(plan.missed) if thread_id else None
        elif plan.action == "fork":
            try:
                answer = server.request("thread/fork", {"threadId": plan.thread_id, **common})
                thread_id = ((answer or {}).get("thread") or {}).get("id")
            except AppServerError as exc:
                if exc.exited:
                    raise
                plan = handoff.ThreadPlan("start", missed=list(self.history))
        if thread_id is None:
            instructions = handoff.handoff_text(plan.missed)
            answer = server.request("thread/start", {
                **common, **({"developerInstructions": instructions} if instructions else {}),
            })
            thread_id = ((answer or {}).get("thread") or {}).get("id")
            if not thread_id:
                raise AppServerError("Codex did not start a thread")
        loaded.add(thread_id)
        self.thread_id = thread_id
        handoff.write_record(db, self.req.session_id, PROVIDER, key, {"thread_id": thread_id})
        return note

    def _input(self, note: Optional[str]) -> list[dict]:
        text = self.req.user_text or ""
        try:
            from openprogram.runtime.permissions.authority import render_model_input_from
            text = render_model_input_from(self.req, text)
        except Exception:  # noqa: BLE001 - the person's own text still goes
            pass
        entries: list[dict] = []
        if note:
            entries.append({"type": "text", "text": note})
        entries.append({"type": "text", "text": text})
        for attachment in self.req.attachments or []:
            if isinstance(attachment, dict) and attachment.get("type") == "image" and attachment.get("path"):
                entries.append({"type": "localImage", "path": str(attachment["path"])})
        return entries

    # -- the turn ---------------------------------------------------------

    def run(self) -> tuple[str, dict, list[dict]]:
        extra_args, env, model = self._launch()
        agent_args, agent_params = self._agent()
        server, fresh = pool().acquire(self.pool_key, extra_args=[*extra_args, *agent_args], env=env)
        self.server = server
        unlisten = server.listen(self._listen)
        crashed = False
        finished = False
        try:
            cwd = self._cwd()
            settings = permission_settings(getattr(self.req, "permission_mode", None))
            note = self._thread(server, fresh, model, cwd, {**settings, "extra": agent_params})
            effort = codex_effort(getattr(self.req, "thinking_effort", None), self._supported_efforts())
            inputs = self._input(note)
            while True:
                params = {"threadId": self.thread_id, "input": inputs, "cwd": cwd,
                          "approvalPolicy": settings["approvalPolicy"],
                          "approvalsReviewer": settings["approvalsReviewer"],
                          "sandboxPolicy": settings["sandboxPolicy"], **agent_params}
                if model:
                    params["model"] = model
                if effort:
                    params["effort"] = effort
                self.turn_id = None
                if self.cancel_event.is_set():
                    break
                answer = server.request("turn/start", params)
                self.turn_id = self.turn_id or ((answer or {}).get("turn") or {}).get("id")
                completed = self._drain()
                if completed is None:  # stopped
                    break
                status = completed.get("status")
                if status == "failed":
                    _raise_turn_error(completed.get("error") or self.last_error, self.model_id)
                if status == "interrupted" or self.cancel_event.is_set() or not self.followups:
                    break
                inputs = [entry for followup in self.followups for entry in followup]
                self.followups.clear()
            finished = True
        except AppServerError as exc:
            crashed = exc.exited or not server.alive
            if self.cancel_event.is_set() and crashed:
                finished = True  # stopped by ending the process; the reply so far is kept
            else:
                from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError
                tail = server.stderr_tail().splitlines()[-1:] if crashed else []
                message = str(exc) if not crashed else "Codex stopped unexpectedly" + (f": {tail[0]}" if tail else ".")
                raise LLMError(message=message, reason=ErrorReason.PROVIDER_INTERNAL if crashed else ErrorReason.UNKNOWN,
                               provider=PROVIDER, model=self.model_id) from exc
        finally:
            unlisten()
            self._close_open_cards()
            if crashed or not server.alive:
                pool().discard(self.pool_key, server)
            else:
                pool().release(self.pool_key, server)
            # A failed turn leaves no reply the thread saw; the next one tells it.
            if finished:
                self._remember_seen()
        from openprogram.state.reply_text import reply_text
        return reply_text(self.blocks).strip(), self.usage, self.tool_calls

    def _remember_seen(self) -> None:
        if not self.thread_id:
            return
        try:
            from openprogram.runtime.context.session_db import default_db
            handoff.write_record(default_db(), self.req.session_id, PROVIDER, handoff.branch_key(self.req),
                                 {"thread_id": self.thread_id, "seen": self.assistant_msg_id,
                                  **({"last_turn_id": self.turn_id} if self.turn_id else {})})
        except Exception:  # noqa: BLE001 - the next turn hands off what it cannot tell
            _log.warning("Codex thread record not kept for %s", self.req.session_id, exc_info=True)

    def _close_open_cards(self) -> None:
        for item_id in list(self.open_cards):
            self._end_card(item_id, "Stopped", True, "cancelled")

    def _listen(self, message: dict) -> bool:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        thread = params.get("threadId")
        if thread and self.thread_id and thread != self.thread_id:
            return False
        method = message.get("method")
        if "id" in message and method not in _ANSWERED_REQUESTS:
            return False  # the client's default answer
        self.messages.put(message)
        return True

    def _drain(self) -> Optional[dict]:
        """Handle Codex's messages until the turn completes; None when it was stopped."""
        stop_seen: Optional[float] = None
        interrupted = False
        next_steer = time.monotonic()
        while True:
            if self.cancel_event.is_set():
                stop_seen = stop_seen or time.monotonic()
                if not interrupted and self.turn_id:
                    # Sent once the turn has an id, however early the Stop came.
                    interrupted = True
                    try:
                        assert self.server is not None
                        self.server.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id},
                                            timeout=INTERRUPT_GRACE_SECONDS)
                    except AppServerError:
                        _log.debug("Codex interrupt not answered", exc_info=True)
                if time.monotonic() - stop_seen > INTERRUPT_GRACE_SECONDS:
                    # Codex did not end the turn: stop its process and what it runs.
                    assert self.server is not None
                    self.server.kill()
                    return None
            elif self.turn_id and time.monotonic() >= next_steer:
                next_steer = time.monotonic() + STEER_POLL_SECONDS
                self._steer()
            try:
                message = self.messages.get(timeout=0.1)
            except queue.Empty:
                continue
            completed = self._handle(message)
            if completed is not None:
                return None if (self.cancel_event.is_set() and completed.get("status") != "failed") else completed

    def _handle(self, message: dict) -> Optional[dict]:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "openprogram/exited":
            raise AppServerError("Codex stopped", exited=True)
        if "id" in message:
            self._answer(message)
            return None
        if method == "turn/started":
            self.turn_id = self.turn_id or (params.get("turn") or {}).get("id")
        elif method == "item/agentMessage/delta":
            self._text_delta(str(params.get("itemId") or ""), str(params.get("delta") or ""))
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            item_id = str(params.get("itemId") or "")
            kind = "summary" if method.endswith("summaryTextDelta") else "raw"
            first = self.items.setdefault(item_id, {}).setdefault("reasoning", kind)
            if first == kind:
                self.streamed.add(item_id)
                self.text_item = None
                self._add_text("thinking", str(params.get("delta") or ""))
        elif method == "item/reasoning/summaryPartAdded":
            item_id = str(params.get("itemId") or "")
            if item_id in self.streamed:
                self._add_text("thinking", "\n\n")
        elif method == "item/started":
            self._item_started(params.get("item") or {})
        elif method == "item/completed":
            self._item_completed(params.get("item") or {})
        elif method == "turn/plan/updated":
            self._plan(list(params.get("plan") or []), params.get("explanation"))
        elif method == "thread/tokenUsage/updated":
            self._usage(params.get("tokenUsage") or {})
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            if self.turn_id and turn.get("id") and turn["id"] != self.turn_id:
                return None
            return turn
        elif method == "error":
            if not params.get("willRetry"):
                self.last_error = params.get("error") or {}
        return None

    def _item_started(self, item: dict) -> None:
        item_id = str(item.get("id") or "")
        if not item_id:
            return
        self.items.setdefault(item_id, {})["item"] = item
        card = codex_items.card_for(item)
        if card is not None:
            self._start_card(item_id, card)

    def _item_completed(self, item: dict) -> None:
        item_id = str(item.get("id") or "")
        kind = item.get("type")
        if kind == "agentMessage":
            if item_id not in self.streamed:
                self._text_delta(item_id, str(item.get("text") or ""))
            return
        if kind == "reasoning":
            if item_id not in self.streamed:
                self.text_item = None
                self._add_text("thinking", codex_items.reasoning_text(item))
            return
        card = codex_items.card_for(item)
        if card is None:
            return
        result, is_error, outcome = codex_items.result_of(item)
        self._end_card(item_id, result, is_error, outcome, card)

    # -- Codex's requests -------------------------------------------------

    def _answer(self, message: dict) -> None:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        assert self.server is not None
        from openprogram.runtime.permissions.approvals.live import ask_live_approval, live_owner_from

        owner = live_owner_from(self.execution_context, self.req.session_id, "codex")
        if owner is None:
            self.server.respond(message["id"], {"decision": "decline"})
            return
        item_id = str(params.get("itemId") or "")
        started = (self.items.get(item_id) or {}).get("item") or {}
        reason = str(params.get("reason") or "")
        if method == "item/commandExecution/requestApproval":
            command = codex_items.shown_command(params.get("command") or started.get("command"))
            tool, arguments = "bash", {"command": command,
                                       **({"workdir": params["cwd"]} if params.get("cwd") else {})}
            detail = reason
        else:
            changes = started.get("changes") or []
            arguments = {"input": codex_items.patch_envelope(changes)}
            paths = [str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")]
            if paths:
                arguments["path"] = paths[0]
            tool = "apply_patch"
            detail = "\n".join(part for part in (reason, f"Write access to {params['grantRoot']}"
                                                  if params.get("grantRoot") else "") if part)
        decision = ask_live_approval(
            owner, tool=tool, arguments=arguments, prompt=f"允许执行 {tool}？", detail=detail,
            allowed_scopes=("once", "session"), risk_level="high" if tool == "bash" else "medium",
            publish=self.on_event, cancel_event=self.cancel_event,
        )
        if decision.allowed:
            answer = "acceptForSession" if decision.scope in ("session", "always") else "accept"
        elif decision.outcome == "deny":
            answer = "decline"
        else:
            answer = "cancel"
        self.server.respond(message["id"], {"decision": answer})

    # -- steering ---------------------------------------------------------

    def _steer(self) -> None:
        hook = self.execution_context.get("safe_point_hook")
        queue_ = self.execution_context.get("steer_inputs")
        if hook is None or not hasattr(hook, "take_open_steers") or not isinstance(queue_, list):
            return
        try:
            hook.take_open_steers()
        except Exception:  # noqa: BLE001 - steering waits for the next poll
            _log.debug("steering not read", exc_info=True)
            return
        if not queue_:
            return
        for entry in queue_:
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if isinstance(payload, dict) and isinstance(entry.get("command_id"), str):
                self.steer_images[entry["command_id"]] = [
                    str(image.get("path")) for image in payload.get("images") or []
                    if isinstance(image, dict) and image.get("path")
                ]
        from openprogram.runtime.context import Context
        from openprogram.runtime.conversation.turn_runtime.loop_runner import _steering_source

        take, delivered = _steering_source(
            self.req, model=SimpleNamespace(input=["text", "image"]),
            db=Context.turn_store(self.req.session_id), assistant_msg_id=self.assistant_msg_id,
            execution_context=self.execution_context,
        )
        messages = asyncio.run(take())
        for message in messages:
            frame = delivered.pop(id(message), None)
            if frame is None:
                continue
            self.blocks.append({"type": "steering", "message_id": frame["msg_id"],
                                "text": frame["content"], "timestamp": frame["timestamp"]})
            self.text_item = None
            self.on_event({"type": "chat_response", "data": frame})
            entries = [{"type": "text", "text": frame["content"]}]
            entries += [{"type": "localImage", "path": path}
                        for path in self.steer_images.pop(str(frame.get("command_id")), [])]
            try:
                assert self.server is not None
                self.server.request("turn/steer", {"threadId": self.thread_id,
                                                   "expectedTurnId": self.turn_id, "input": entries})
            except AppServerError as exc:
                if exc.exited:
                    raise
                # The turn ended meanwhile: the steer starts the next one.
                self.followups.append(entries)


#: Codex's requests this engine answers itself; the rest get the client's default answer.
_ANSWERED_REQUESTS = frozenset({"item/commandExecution/requestApproval", "item/fileChange/requestApproval"})
