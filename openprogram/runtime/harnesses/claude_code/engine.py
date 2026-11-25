"""One conversation turn run by the person's Claude Code.

A conversation's branch keeps one ``claude -p`` in stream-JSON mode,
started in the session's folder on the branch's Claude Code session
(``--resume``; a new branch of a session forks it with ``--fork-session``;
a conversation with none gets a new id with ``--session-id``), and each
turn is a user message sent into it. Model, effort and permission mode
change in the running process (``set_model``, ``apply_flag_settings``,
``set_permission_mode``); another session, folder or agent starts another
process. What Claude Code says becomes the chat's frames and cards:
thinking and text as they stream, tool calls and their results, context
use. Permission prompts reach the person as live waits; messages sent
while a turn runs go in as its next input; Stop is an ``interrupt``.
This follows T3 Code and Paseo, which drive the same program through the
Agent SDK.

The session is Claude Code's own, saved in ``~/.claude/projects`` like
every other: ``claude --resume`` and the Claude app show these turns too.
The uuids of the assistant records it wrote here are kept as
``own_turns``, so the app sync does not write them twice.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Optional

from openprogram.runtime.harnesses import handoff, split_ref
from openprogram.runtime.harnesses.claude_code import client
from openprogram.runtime.harnesses.claude_code.client import ClaudeError, ClaudeProcess

_log = logging.getLogger(__name__)

PROVIDER = "claude-code-harness"
HARNESS_ID = "claude-code"
_OWN_TURNS_KEPT = 1000
#: How often the uuids a turn wrote are saved while it runs.
_OWN_SAVE_SECONDS = 1.0
#: How long after a ``result`` a turn waits for the turn a message it sent mid-turn started.
_STEER_FOLLOW_SECONDS = 120.0
#: How long after a ``result`` a turn that took steering watches for such a turn to begin, when
#: Claude Code began a message after the steering (its request may have gone out before it).
_STEER_QUIET_SECONDS = 3.0
#: How long a Stop waits for the turn to end before the process is stopped (T3 Code).
CANCEL_GRACE_SECONDS = 10.0
#: OpenProgram's permission modes as Claude Code's own.
PERMISSION_MODES = {"ask": "default", "plan": "plan", "acceptEdits": "acceptEdits", "auto": "auto",
                    "bypass": "bypassPermissions"}
_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]
_MODEL_DEFAULTS = {"", "default", "auto"}
#: The context window of each model Claude Code last reported.
_windows: dict[str, int] = {}


def claude_effort(effort: Optional[str], supported: Optional[list[str]] = None) -> Optional[str]:
    """Claude Code's effort nearest OpenProgram's ``effort``; None keeps Claude Code's own."""
    value = str(effort or "").strip().lower()
    if not value or value in ("auto", "default"):
        return None
    value = {"off": "low", "none": "low", "minimal": "low"}.get(value, value)
    levels = [level for level in (supported or _EFFORT_ORDER) if level in _EFFORT_ORDER]
    if value in levels:
        return value
    if value not in _EFFORT_ORDER or not levels:
        return None
    rank = _EFFORT_ORDER.index(value)
    return min(levels, key=lambda level: (abs(_EFFORT_ORDER.index(level) - rank), -_EFFORT_ORDER.index(level)))


def _error_reason(message: str):
    from openprogram.models.providers.utils.failures.errors import ErrorReason
    text = message.lower()
    if "rate limit" in text or "usage limit" in text or "credit balance" in text or "429" in text:
        return ErrorReason.RATE_LIMIT
    if "log in" in text or "login" in text or "authenticat" in text or "401" in text:
        return ErrorReason.AUTHENTICATION
    if "prompt is too long" in text or "context" in text and "window" in text:
        return ErrorReason.CONTEXT_LENGTH
    if "overloaded" in text or "529" in text or "500" in text:
        return ErrorReason.PROVIDER_INTERNAL
    return ErrorReason.UNKNOWN


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict) and block.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else str(content)


class ClaudeCodeEngine:
    """The engine registered for ``claude-code-harness``."""

    provider = PROVIDER
    label = "Claude Code"

    def run_turn(self, *, req, history, on_event, cancel_event, assistant_msg_id,
                 ordered_blocks_out, execution_context) -> tuple[str, dict, list[dict]]:
        return ClaudeTurn(
            req=req, history=history or [], on_event=on_event, cancel_event=cancel_event,
            assistant_msg_id=assistant_msg_id or f"{req.user_msg_id}_reply",
            ordered_blocks_out=ordered_blocks_out, execution_context=execution_context,
        ).run()


class ClaudeTurn:
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
        self.process: Optional[ClaudeProcess] = None
        self.session_id: Optional[str] = None
        self.cwd: Optional[str] = None
        self.own: list[str] = []
        self.last_message: Optional[str] = None
        self.streamed: set[str] = set()
        self.current_message: Optional[str] = None
        self.open_cards: dict[str, dict] = {}
        self.records: dict = {}
        self.reused = False
        self.live: Optional[client.Live] = None
        self.resumed_seen: Optional[str] = None
        self.marked: Optional[tuple[str, str]] = None
        self.last_steer_at = 0.0  # when a message was last sent mid-turn
        self.last_start_at = 0.0  # when Claude Code last began a message
        self.own_saved = 0
        self.own_saved_at = 0.0

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
            if (message_id != self.last_message and last["text"] and not last["text"][-1].isspace()
                    and not text[0].isspace()):
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
        if not call_id or call_id in self.open_cards:
            return
        shown = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False, default=str)
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

    def _context(self, usage: dict, model: Optional[str]) -> None:
        used = sum(int(usage.get(key) or 0) for key in
                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if not used:
            return
        self.usage["context_tokens"] = used
        self.usage["agent_iteration_count"] += 1
        window = _windows.get(model or "") or _windows.get(self.model_id or "") or 0
        frame = {"type": "context_usage", "session_id": self.req.session_id, "msg_id": self.req.user_msg_id,
                 "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
                 "model": f"{PROVIDER}/{self.model_id}" if self.model_id else PROVIDER, "context_tokens": used}
        if window:
            frame["context_window"] = window
        self.on_event({"type": "chat_response", "data": frame})

    # -- setup ------------------------------------------------------------

    def _workdir(self) -> str:
        import os
        try:
            from openprogram.runtime.conversation.session_settings.workdir import runtime_location_for
            workdir = runtime_location_for(self.req.session_id, branch_id=getattr(self.req, "branch_id", None)).get("workdir")
            if workdir:
                return str(workdir)
        except Exception:  # noqa: BLE001
            _log.debug("workdir not resolved for %s", self.req.session_id, exc_info=True)
        return os.path.expanduser("~")

    def _plan(self) -> tuple[tuple, list[str], Optional[str]]:
        """The session the branch runs in: the process identity, the arguments that open it,
        and the handoff for its next message."""
        import os
        from openprogram.runtime.context.session_db import default_db

        db = default_db()
        key = handoff.branch_key(self.req)
        records = handoff.read_records(db, self.req.session_id, PROVIDER)
        plan = handoff.plan_thread(records, key, self.history)
        self.records = records
        cwd = self._workdir()
        if plan.action in ("resume", "fork") and plan.thread_id:
            record = next((r for r in records.values()
                           if isinstance(r, dict) and r.get("thread_id") == plan.thread_id), {})
            imported = (db.get_session(self.req.session_id) or {}).get("imported_from") or {}
            # A session resumes only from the folder it was made in.
            folder = record.get("cwd") or (imported.get("cwd") if isinstance(imported, dict) else None)
            if folder and os.path.isdir(str(folder)):
                cwd = str(folder)
        self.cwd = cwd
        note = handoff.handoff_text(plan.missed)
        agent = self._agent()
        # Claude Code's own effort comes back only in a new process: no request unsets one.
        own_effort = self._wanted()[1] is None
        if plan.action == "resume" and plan.thread_id:
            self.session_id = plan.thread_id
            # What the session it goes on in has seen, for this branch's first record of it.
            self.resumed_seen = record.get("seen")
            return (plan.thread_id, cwd, agent, own_effort), ["--resume", plan.thread_id], note
        if plan.action == "fork" and plan.thread_id:
            # Its own id comes in the session's ``init`` message.
            return (f"fork:{plan.thread_id}:{uuid.uuid4()}", cwd, agent, own_effort), \
                ["--resume", plan.thread_id, "--fork-session"], note
        self.session_id = str(uuid.uuid4())
        return (self.session_id, cwd, agent, own_effort), ["--session-id", self.session_id], note

    def _agent(self) -> Optional[str]:
        from openprogram.runtime.harnesses import branch_setting
        agent = str(branch_setting(self.req, "harness_agent") or "")
        return agent.removeprefix("agent:") if agent.startswith("agent:") else None

    def _wanted(self) -> tuple[Optional[str], Optional[str], str]:
        """The branch's model, effort and permission mode in Claude Code's terms."""
        model = self.model_id if self.model_id and self.model_id.lower() not in _MODEL_DEFAULTS else None
        effort = claude_effort(getattr(self.req, "thinking_effort", None), self._listed_efforts())
        mode = PERMISSION_MODES.get(str(getattr(self.req, "permission_mode", None) or "bypass"), "bypassPermissions")
        return model, effort, mode

    def _launch(self, binary: str, session_args: list[str], agent: Optional[str]) -> list[str]:
        model, effort, mode = self._wanted()
        # Full access can be chosen later in the running process (Paseo).
        argv = [*client.stream_args(binary), *session_args, "--allow-dangerously-skip-permissions",
                "--permission-mode", mode]
        if model:
            argv += ["--model", model]
        if effort:
            argv += ["--effort", effort]
        if agent:
            argv += ["--agent", agent]
        return argv

    def _configure(self, live: client.Live) -> None:
        """Bring a running process to the branch's model, effort and permission mode."""
        model, effort, mode = self._wanted()
        if model != live.model:
            live.process.control({"subtype": "set_model", "model": model or "default"})
            live.model = model
            live.effort = None  # a model change can reset the effort: set it again
        if effort != live.effort and effort:
            live.process.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": effort}})
            live.effort = effort
        if mode != live.mode:
            live.process.control({"subtype": "set_permission_mode", "mode": mode})
            live.mode = mode

    def _listed_efforts(self) -> Optional[list[str]]:
        try:
            from openprogram.runtime.harnesses.models import harness_model
            listed = harness_model(PROVIDER, self.model_id or "default")
        except Exception:  # noqa: BLE001
            listed = None
        return list(listed.get("efforts") or []) if listed else None

    def _message_text(self, note: Optional[str]) -> str:
        text = self.req.user_text or ""
        try:
            from openprogram.runtime.permissions.authority import render_model_input_from
            text = render_model_input_from(self.req, text)
        except Exception:  # noqa: BLE001
            pass
        return f"{note}\n\n{text}" if note else text

    def _opening_message(self, note: Optional[str]) -> dict:
        """The person's message, with a uuid Claude Code keeps for its record: the app sync
        knows the turn as this engine's even when a Stop comes before any answer."""
        message_uuid = str(uuid.uuid4())
        if message_uuid not in self.own:
            self.own.append(message_uuid)
        return self._user_message(self._message_text(note), uuid=message_uuid)

    def _user_message(self, text: str, **extra: Any) -> dict:
        return {"type": "user", "session_id": self.session_id or "", "parent_tool_use_id": None,
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}, **extra}

    # -- the turn ---------------------------------------------------------

    def _open(self, binary: str) -> tuple[client.Live, Optional[str]]:
        """The conversation's running Claude Code, started or reused, and the handoff."""
        identity, session_args, note = self._plan()
        key = f"{self.req.session_id}:{handoff.branch_key(self.req)}"
        live = client.pool().get(key, identity)
        if live is not None and _synced_since(self.records, self.session_id, live.loaded_at):
            # Turns were added outside since this process read the session (T3 Code).
            client.pool().release(key, live, broken=True)
            live = None
        if live is not None:
            try:
                live.process.drop_pending()  # a turn reads only what comes after its message
                self._configure(live)
                self.reused = True
                return live, note
            except ClaudeError:
                # It stopped or cannot be changed: this turn starts a new one.
                client.pool().release(key, live, broken=True)
        self.reused = False
        process = ClaudeProcess(self._launch(binary, session_args, identity[2]), cwd=self.cwd)
        try:
            process.start()
        except OSError as exc:
            raise ClaudeError(f"Claude Code did not start: {exc}", exited=True) from exc
        live = client.Live(process, identity)
        live.model, live.effort, live.mode = self._wanted()
        client.pool().put(key, live)
        return live, note

    def run(self) -> tuple[str, dict, list[dict]]:
        from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError

        binary = client.binary_path()
        if binary is None:
            raise LLMError(message="Claude Code is not installed on this computer.",
                           reason=ErrorReason.INVALID_REQUEST, provider=PROVIDER, model=self.model_id)
        key = f"{self.req.session_id}:{handoff.branch_key(self.req)}"
        live: Optional[client.Live] = None
        finished = broken = False
        try:
            live, note = self._open(binary)
            self.live = live
            self.process = live.process
            self._mark()
            try:
                live.process.send(self._opening_message(note))
            except ClaudeError:
                if not self.reused:
                    raise
                # The kept process ended between turns: the turn goes to a new one.
                client.pool().release(key, live, broken=True)
                live = None
                live, note = self._open(binary)
                self.live = live
                self.process = live.process
                live.process.send(self._opening_message(note))
            result = self._drain()
            if result is None:  # stopped
                finished = True
            elif result.get("is_error") and not self.cancel_event.is_set():
                message = str(result.get("result") or "").strip() or "; ".join(
                    str(e) for e in result.get("errors") or []) or str(result.get("subtype") or "error")
                raise LLMError(message=f"Claude Code: {message}", reason=_error_reason(message),
                               provider=PROVIDER, model=self.model_id)
            else:
                finished = True
            if self.session_id and live.identity[0] != self.session_id:
                live.identity = (self.session_id, *live.identity[1:])  # a fork now holds its own session
        except ClaudeError as exc:
            broken = True
            if self.cancel_event.is_set():
                finished = True
            else:
                tail = self.process.stderr_tail().splitlines()[-1:] if self.process else []
                message = str(exc) + (f": {tail[0]}" if exc.exited and tail else "")
                raise LLMError(message=message, reason=_error_reason(message) if tail else ErrorReason.PROVIDER_INTERNAL,
                               provider=PROVIDER, model=self.model_id) from exc
        finally:
            for call_id in list(self.open_cards):
                self._end_card(call_id, "Stopped", True, "cancelled")
            if live is not None:
                # A process that took steering may still hold a turn of it: no later turn reads that.
                client.pool().release(key, live, broken=broken or bool(self.last_steer_at) or not live.process.alive)
            if finished:
                self._remember_seen()
            elif self.own:
                self._save_own()  # what it wrote is the session's, though the turn failed
            if self.marked:
                handoff.leave_running(self.marked)
        from openprogram.state.reply_text import reply_text
        return reply_text(self.blocks).strip(), self.usage, self.tool_calls

    def _mark(self) -> None:
        """The app sync waits while this turn writes into the session (a fork's id comes with ``init``)."""
        if self.marked is None and self.session_id:
            self.marked = (PROVIDER, self.session_id)
            handoff.enter_running(self.marked)

    def _leave_plan(self) -> None:
        """An approved plan ends Plan here as it does in Claude Code, so the menu says what runs."""
        try:
            mode = _restore_after_plan(self.req)
        except Exception:  # noqa: BLE001 - the next turn's mode is applied as chosen
            _log.warning("Plan not ended for %s", self.req.session_id, exc_info=True)
            return
        if not mode:
            return
        claude_mode = PERMISSION_MODES.get(mode, "default")
        try:
            assert self.process is not None
            # Not awaited: its answer arrives on the reader thread while this one drains.
            self.process.control_async({"subtype": "set_permission_mode", "mode": claude_mode})
        except ClaudeError:
            return
        if self.live is not None:
            self.live.mode = claude_mode

    def _remember_seen(self) -> None:
        self._write_record(seen=True)

    def _save_own(self) -> None:
        """Keep the uuids written so far, so the app sync never copies them back."""
        self.own_saved = len(self.own)
        self.own_saved_at = time.monotonic()
        self._write_record(seen=False)

    def _write_record(self, *, seen: bool) -> None:
        if not self.session_id:
            return
        try:
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            key = handoff.branch_key(self.req)
            record = handoff.read_records(db, self.req.session_id, PROVIDER).get(key) or {}
            own = list(record.get("own_turns") or []) if record.get("thread_id") == self.session_id else []
            own = (own + [u for u in self.own if u not in own])[-_OWN_TURNS_KEPT:]
            update: dict = {"thread_id": self.session_id, "cwd": self.cwd, "own_turns": own}
            if seen:
                update.update(seen=self.assistant_msg_id, at=time.time())
            elif record.get("thread_id") != self.session_id:
                # A session resumed here has seen what its record said; a new one nothing yet.
                update.update(seen=self.resumed_seen)
            handoff.write_record(db, self.req.session_id, PROVIDER, key, update)
        except Exception:  # noqa: BLE001
            _log.warning("Claude Code session record not kept for %s", self.req.session_id, exc_info=True)

    def _drain(self) -> Optional[dict]:
        """Handle Claude Code's messages until its ``result``; None when stopped."""
        assert self.process is not None
        stop_seen: Optional[float] = None
        steered = time.monotonic()
        ended: Optional[dict] = None  # a result, while a message sent mid-turn waits for its own turn
        ended_at = 0.0
        wait = 0.0
        while True:
            if ended is not None and (stop_seen is not None or time.monotonic() - ended_at >= wait):
                return ended
            if stop_seen is None and time.monotonic() - steered >= 0.5:
                steered = time.monotonic()
                self._steer()
            if self.cancel_event.is_set():
                if stop_seen is None:
                    stop_seen = time.monotonic()
                    try:
                        self.process.control_async({"subtype": "interrupt"})
                    except ClaudeError:
                        pass
                elif time.monotonic() - stop_seen > CANCEL_GRACE_SECONDS:
                    raise ClaudeError("Claude Code did not stop", exited=True)
            try:
                message = self.process.messages.get(timeout=0.1)
            except queue.Empty:
                continue
            kind = message.get("type")
            if kind == "openprogram/exited":
                if stop_seen is not None or ended is not None:
                    return None if ended is None else ended
                raise ClaudeError("Claude Code stopped unexpectedly", exited=True)
            if kind == "result":
                for model, info in (message.get("modelUsage") or {}).items():
                    if isinstance(info, dict) and info.get("contextWindow"):
                        _windows[model] = int(info["contextWindow"])
                if message.get("session_id"):
                    self.session_id = str(message["session_id"])
                if stop_seen is not None:
                    return None if message.get("is_error") else message
                if self.last_steer_at:
                    # A message sent mid-turn may be answered as a turn of its own (T3 Code):
                    # this turn goes on to that one's result. Surely so when it came after
                    # Claude Code last began a message; maybe when its request was already out.
                    pending = self.last_steer_at > self.last_start_at
                    wait = _STEER_FOLLOW_SECONDS if pending else _STEER_QUIET_SECONDS
                    ended, ended_at = message, time.monotonic()
                    continue
                return message
            if ended is not None and not message.get("parent_tool_use_id") and (
                    kind in ("assistant", "stream_event") or kind == "system" and message.get("subtype") == "init"):
                ended = None  # the steered turn began
            if kind == "control_request":
                self._answer(message)
            elif kind == "system" and message.get("subtype") == "init" and message.get("session_id"):
                self.session_id = str(message["session_id"])
                self._mark()
            elif message.get("parent_tool_use_id"):
                continue  # a subagent's own steps: its outcome comes back as the tool's result
            elif kind == "stream_event":
                self._stream(message.get("event") or {})
            elif kind == "assistant":
                self._assistant(message)
            elif kind == "user":
                self._tool_results(message)

    def _steer(self) -> None:
        """Messages the person sends while the turn runs go in as its next input (T3 Code, Paseo)."""
        hook = self.execution_context.get("safe_point_hook")
        waiting = self.execution_context.get("steer_inputs")
        if hook is None or not hasattr(hook, "take_open_steers") or not isinstance(waiting, list):
            return
        try:
            hook.take_open_steers()
        except Exception:  # noqa: BLE001 - steering waits for the next poll
            return
        if not waiting:
            return
        import asyncio
        from types import SimpleNamespace
        from openprogram.runtime.context import Context
        from openprogram.runtime.conversation.turn_runtime.loop_runner import _steering_source

        take, delivered = _steering_source(
            self.req, model=SimpleNamespace(input=["text"]), db=Context.turn_store(self.req.session_id),
            assistant_msg_id=self.assistant_msg_id, execution_context=self.execution_context,
        )
        for message in asyncio.run(take()):
            frame = delivered.pop(id(message), None)
            if frame is None:
                continue
            self.blocks.append({"type": "steering", "message_id": frame["msg_id"],
                                "text": frame["content"], "timestamp": frame["timestamp"]})
            self.last_message = None
            self.on_event({"type": "chat_response", "data": frame})
            assert self.process is not None
            steer_uuid = str(uuid.uuid4())
            self.own.append(steer_uuid)
            self.process.send(self._user_message(str(frame["content"]), priority="next", uuid=steer_uuid))
            self.last_steer_at = time.monotonic()

    def _stream(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self.last_start_at = time.monotonic()
            self.current_message = str((event.get("message") or {}).get("id") or "") or None
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                text, block = str(delta.get("text") or ""), "text"
            elif delta.get("type") == "thinking_delta":
                text, block = str(delta.get("thinking") or ""), "thinking"
            else:
                return
            if self.current_message:
                self.streamed.add(self.current_message)
            self._add_text(block, text, self.current_message)

    def _assistant(self, message: dict) -> None:
        if message.get("uuid"):
            self.own.append(str(message["uuid"]))
            if time.monotonic() - self.own_saved_at >= _OWN_SAVE_SECONDS:
                self._save_own()
        body = message.get("message") or {}
        message_id = str(body.get("id") or "") or None
        streamed = message_id in self.streamed
        for block in body.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                self._start_card(str(block.get("id") or ""), str(block.get("name") or "tool"), block.get("input"))
            elif not streamed and kind == "text":
                self._add_text("text", str(block.get("text") or ""), message_id)
            elif not streamed and kind == "thinking":
                self._add_text("thinking", str(block.get("thinking") or ""), message_id)
        usage = body.get("usage")
        if isinstance(usage, dict):
            self._context(usage, body.get("model"))

    def _tool_results(self, message: dict) -> None:
        content = (message.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                is_error = bool(block.get("is_error"))
                self._end_card(str(block.get("tool_use_id") or ""), _result_text(block.get("content")), is_error,
                               "error" if is_error else "completed")

    def _answer(self, message: dict) -> None:
        assert self.process is not None
        request = message.get("request") or {}
        request_id = str(message.get("request_id") or "")
        if request.get("subtype") != "can_use_tool":
            self.process.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": request_id,
                "error": f"OpenProgram does not handle {request.get('subtype')}"}})
            return
        tool = str(request.get("tool_name") or "tool")
        arguments = request.get("input") if isinstance(request.get("input"), dict) else {}
        deny = {"behavior": "deny", "message": "The person declined this."}
        from openprogram.runtime.permissions.approvals.live import ask_live_approval, live_owner_from
        owner = live_owner_from(self.execution_context, self.req.session_id, HARNESS_ID)
        if owner is None:
            self.process.respond(request_id, deny)
            return
        suggestions = request.get("permission_suggestions") or []
        decision = ask_live_approval(
            owner, tool=tool, arguments=arguments, prompt=f"允许执行 {tool}？",
            detail=str(request.get("decision_reason") or ""),
            allowed_scopes=("once", "session") if suggestions else ("once",),
            risk_level="high" if tool == "Bash" else "medium",
            publish=self.on_event, cancel_event=self.cancel_event,
        )
        if decision.allowed:
            answer: dict = {"behavior": "allow", "updatedInput": arguments}
            if decision.scope in ("session", "always") and suggestions:
                answer["updatedPermissions"] = suggestions
            self.process.respond(request_id, answer)
            if tool == "ExitPlanMode":
                self._leave_plan()
        else:
            self.process.respond(request_id, {**deny, "interrupt": decision.outcome != "deny"})


def _restore_after_plan(req: Any) -> Optional[str]:
    """The branch's permission back to what it was before Plan, as OpenProgram's own
    plan mode does when its plan is approved (``plan_mode/exit.py``); the mode now set."""
    from openprogram.runtime.events import emit_ws_frame
    from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
    from openprogram.runtime.permissions.state import permission_state, update_permission
    from openprogram.state.authority import normalize_authority

    branch_id = getattr(req, "branch_id", None)
    state = permission_state(req.session_id, branch_id=branch_id)
    if state["mode"] != "plan":
        return None
    updated = update_permission(req.session_id, state.get("before_plan") or DEFAULT_PERMISSION, state["version"],
                                normalize_authority(req), branch_id=branch_id)
    # Every open window showing that branch follows, as when the owner clicks it.
    from openprogram.runtime.context.session_db import default_db
    active = (default_db().get_session(req.session_id) or {}).get("active_branch_id")
    emit_ws_frame({"type": "permission_changed", "data": {
        "session_id": req.session_id, "action": "set_permission", "mode": updated["mode"],
        "version": updated["version"], "branch_id": branch_id or active, "active_branch_id": active}})
    return str(updated["mode"])


def _synced_since(records: dict, session_id: Optional[str], since: float) -> bool:
    """The app sync moved ``session_id`` on after ``since``."""
    return bool(session_id) and any(
        isinstance(r, dict) and r.get("thread_id") == session_id and float(r.get("synced_at") or 0) > since
        for r in (records or {}).values())
