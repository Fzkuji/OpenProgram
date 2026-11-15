"""A real chat server in a temporary home that records one browser's view of it.

The chat action, agent loop, stream tap, job runner, execution store and session
store are the production ones. Only the model and its plain tools are scripted.
Every frame in a recording left through the server's own ``_broadcast``, event
bus or ``send_text``, so no id in it was put there by a test.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest

from openprogram.interfaces.server import ws_delivery
from openprogram.interfaces.server.transcript import publisher, turn_index

SESSION_ID = "transcript-recording"
_API_NAME = "transcript-recording-api"
_PROVIDER_NAME = "transcript-recording-provider"
_MODEL_NAME = "transcript-recording-model"
_WAIT_SECONDS = 15.0


class RecordingSocket:
    """One browser socket; the reader of its frames is whoever holds ``sink``."""

    def __init__(self, sink: Callable[[dict], None], *, live: Callable[[str], None] | None = None) -> None:
        from openprogram.runtime.permissions.authority import owner_authority

        self.sink = sink
        self.live = live
        self.scope = {"state": {"authority": owner_authority("owner/install/0123456789abcdef")}}
        # What the web client negotiates with ``history_version: 3``.
        self._history_protocol = 3

    async def send_text(self, value: str) -> None:
        self.sink(json.loads(value))

    def enqueue_text_threadsafe(self, value: str) -> bool:
        """What the publisher sends: this browser's numbered updates."""
        if self.live is not None:
            self.live(value)
        return True


class ScriptedModel:
    """Serves one scripted response per model call."""

    requires_credentials = False

    def __init__(self) -> None:
        self.responses: list[tuple] = []
        # Scripted replies for a call whose latest message names the key; a
        # background sub-agent's call races the parent's next one.
        self.by_prompt: dict[str, tuple] = {}
        self.calls = 0

    def add(self, *steps: Any) -> None:
        self.responses.append(steps)

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        from tests.component.providers.scripted_provider import ScriptedProvider

        scripted = ScriptedProvider()
        routed = next((key for key in self.by_prompt if key in repr(context.messages[-1])), None)
        if routed is not None:
            scripted.add_response(*self.by_prompt.pop(routed))
        else:
            index = self.calls
            self.calls += 1
            if index >= len(self.responses):
                raise AssertionError(f"unexpected model call {index}")
            scripted.add_response(*self.responses[index])
        async for event in scripted.stream_simple(model, context, options):
            yield event


class ScriptedTools:
    """Tools the scripted model calls; a held tool waits until released."""

    def __init__(self) -> None:
        self.started: defaultdict[str, threading.Event] = defaultdict(threading.Event)
        self.release: defaultdict[str, threading.Event] = defaultdict(threading.Event)
        self.held: set[str] = set()
        self.during: dict[str, Callable[[], None]] = {}
        self.extra: list[Any] = []

    def tool(self, name: str):
        from openprogram.models.providers.types import AgentTool, AgentToolResult, TextContent

        async def execute(_call_id, _args, _cancel_event, _on_update):
            self.started[name].set()
            if name in self.during:
                await asyncio.to_thread(self.during[name])
            # A held tool does not watch the stop signal: a stop cuts it, so
            # its call ends as stopped. Returning on the signal instead raced
            # the cut, and a loaded machine recorded a finished call.
            while name in self.held and not self.release[name].is_set():
                await asyncio.sleep(0.005)
            return AgentToolResult(content=[TextContent(text=f"{name}:ok")])

        made = AgentTool(name=name, label=name, description=name,
                         parameters={"type": "object", "properties": {}}, execute=execute)
        made._runtime_implementation = {
            "module": __name__, "qualname": f"ScriptedTools.tool:{name}",
            "code_sha256": hashlib.sha256(b"transcript-recording").hexdigest(),
        }
        made._requires_approval = False
        return made

    def all(self) -> list[Any]:
        return [self.tool("lookup"), self.tool("build"), *self.extra]


def wait_until(predicate, *, timeout: float = _WAIT_SECONDS, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(0.005)
    raise AssertionError(f"{what} did not happen within {timeout}s")


class LiveChat:
    """Drives one conversation through the public actions and writes its timeline.

    A timeline is the order one browser sees: ``compose`` (the local pending
    row), ``frame`` (a pushed frame), ``read`` (the reply to a transcript read),
    ``page`` (a history page the client asked for), ``disconnect``, ``reopen``,
    ``checkpoint`` (compare live against a cold read here).
    """

    def __init__(self, *, sessions, store, control, server, model, tools, effects) -> None:
        self.sessions = sessions
        self.store = store
        self.control = control
        self.server = server
        self.model = model
        self.tools = tools
        self.effects = effects
        self.session_id = SESSION_ID
        self.timeline: list[dict] = []
        self.execution_id: str | None = None
        self._lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._connected = True
        self.socket = RecordingSocket(self._frame)
        # The browser's one socket, seen from the reads and the publisher: its
        # row frames wait for the read in flight, as QueuedWebSocket parks them.
        self.parking = ws_delivery.ReadParking(self._parked)
        self._read_socket = RecordingSocket(lambda _frame: None, live=self._live_frame)
        self._end_parking = lambda: None
        self.tree_updates = 0
        # Tools whose call frame the server has sent, socket up or down.
        self.calls_sent: set[str] = set()
        self._peer_socket = RecordingSocket(lambda _frame: None)

    # Delivery

    def _parked(self, value: str) -> None:
        # QueuedWebSocket releases parked frames through _enqueue, which
        # applies the protocol filter again after the read has ended.
        if not ws_delivery.frame_is_for_older_clients(self._read_socket, value):
            self._frame(json.loads(value))

    def _live_frame(self, value: str) -> None:
        # How many of these a run sends depends on tick timing, and they carry no row.
        if '"branches_changed"' in value[:40]:
            return
        if not self.parking.offer(value):
            self._parked(value)

    def _frame(self, frame: dict) -> None:
        with self._lock:
            if not self._connected:
                return
            self.timeline.append({"frame": frame})

    def broadcast(self, value: str, *, exclude: Any = None) -> None:
        """The server's fan-out, for the one socket a recording follows."""
        if '"tool_use"' in value:
            data = json.loads(value).get("data") or {}
            event = data.get("event") if isinstance(data, dict) else None
            if data.get("type") == "stream_event" and isinstance(event, dict) and event.get("type") == "tool_use":
                self.calls_sent.add(str(event.get("tool")))
        if exclude is self.socket:
            return
        if '"tree_update"' in value:
            self.tree_updates += 1
        if self.parking.offer(value) or ws_delivery.frame_is_for_older_clients(self._read_socket, value):
            return
        self._frame(json.loads(value))

    @staticmethod
    def _in_helper_thread(call: Callable[[], Any]) -> Any:
        """Run ``call`` off the emitting thread, which may be inside an event loop."""
        box: list[Any] = []
        worker = threading.Thread(target=lambda: box.append(call()), daemon=True)
        worker.start()
        worker.join(_WAIT_SECONDS)
        if not box:
            raise AssertionError("a transcript read did not finish")
        return box[0]

    # Conversation

    def send(self, text: str, *, peer: bool = False) -> None:
        """A send from this browser, or from another tab when ``peer`` is set."""
        from openprogram.interfaces.server.ws_actions.chat import handle_chat

        request_id = f"request-{len(self.timeline)}"
        if peer:
            peer_frames: list[dict] = []
            self._peer_socket.sink = peer_frames.append
            asyncio.run(handle_chat(self._peer_socket, {
                "text": text, "session_id": self.session_id, "permission_mode": "bypass",
                "client_request_id": request_id,
            }))
            ack = next(f["data"] for f in peer_frames if f.get("type") == "chat_ack")
        else:
            with self._lock:
                self.timeline.append({"compose": {"text": text, "client_request_id": request_id}})
            asyncio.run(handle_chat(self.socket, {
                "text": text, "session_id": self.session_id, "permission_mode": "bypass",
                "client_request_id": request_id,
            }))
            with self._lock:
                ack = next(s["frame"]["data"] for s in reversed(self.timeline)
                           if s.get("frame", {}).get("type") == "chat_ack")
        self.execution_id = ack.get("execution_id")

    def wait_tool(self, name: str) -> None:
        """Wait until the tool runs and its call is in the stored reply.

        The loop starts a tool while the stream tap may still be writing
        the call into the reply; the tap sends the call's frame only after
        that write, so a read after the frame shows the call."""
        wait_until(self.tools.started[name].is_set, what=f"tool {name} starting")
        wait_until(lambda: name in self.calls_sent, what=f"the {name} call reaching the stored reply")

    def release_tool(self, name: str) -> None:
        self.tools.release[name].set()

    def wait_finished(self) -> None:
        wait_until(self._settled, what="the run settling")
        with self.server._running_tasks_lock:
            running = self.server._running_tasks.get(self.session_id)
            thread = running.get("thread") if isinstance(running, dict) else None
        if thread is not None:
            thread.join(_WAIT_SECONDS)
        self.effects.effects.drain()

    def wait_frame(self, frame_type: str) -> None:
        """Wait for a frame that arrives on the event bus's own thread."""
        wait_until(lambda: any(s.get("frame", {}).get("type") == frame_type for s in self.timeline),
                   what=f"a {frame_type} frame")

    def wait_job_finished(self) -> None:
        """Wait for a background job's terminal status and the reload it sends."""
        from openprogram.runtime.jobs import get_runner

        finished = wait_until(lambda: next((s["frame"]["data"] for s in list(self.timeline)
                                            if s.get("frame", {}).get("type") == "job_status"
                                            and s["frame"]["data"].get("status") in {"completed", "failed", "errored"}),
                                           None), what="the job finishing")
        # The status goes out before the runner settles the job's card and
        # queues its notice; the runner's own wait covers both.
        get_runner().await_job(finished["job_id"], timeout=_WAIT_SECONDS)
        self.wait_published()
        self.effects.effects.drain()

    def wait_published(self) -> None:
        """Wait until the publisher has sent everything written so far.

        A write notices the publisher before it returns, and ``idle`` counts
        a notice from then until its update is sent, so no settling time is
        needed after the writes this thread waited for."""
        current = publisher.current()
        wait_until(lambda: current is None or current.idle, what="the publisher going idle")

    def _settled(self) -> bool:
        from openprogram.runtime.execution.model import ExecutionStatus

        terminal = {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
        return (self.execution_id is None
                or self.store.get_execution(self.execution_id).status in terminal
                ) and not self.server._is_run_active(self.session_id)

    def cancel(self, command_id: str = "stop-command") -> None:
        self._command("execution.cancel", command_id, {})

    def steer(self, message: str, command_id: str = "steer-command") -> None:
        self._command("execution.steer", command_id, {"message": message})

    def _command(self, action: str, command_id: str, payload: dict) -> None:
        from openprogram.interfaces.server.ws_actions import runtime

        execution = self.store.get_execution(self.execution_id)
        asyncio.run(runtime.ACTIONS[action](self.socket, {
            "type": "execution.command", "action": action, "command_id": command_id,
            "execution_id": execution.execution_id, "expected_version": execution.status_version,
            "payload": payload,
        }))

    def filler(self, count: int, *, prefix: str = "filler") -> list[str]:
        """Later plain user rows, each its own turn, that move a page edge."""
        head = self.sessions.get_session(self.session_id)["head_id"]
        added = []
        for index in range(count):
            row_id = f"{prefix}-{index}"
            self.sessions.append_message(self.session_id, {
                "id": row_id, "role": "user", "content": f"{prefix} {index}",
                "timestamp": time.time(), "predecessor": head, "source": "web",
            })
            head = row_id
            added.append(row_id)
        self.sessions.set_head(self.session_id, head)
        with self.server._sessions_lock:
            self.server._sessions.pop(self.session_id, None)
        return added

    def reply_id(self, nth: int = 0) -> str:
        """The nth assistant reply on the current branch."""
        rows = [m for m in self.sessions.get_messages(self.session_id) if m.get("role") == "assistant"]
        return rows[nth]["id"]

    # Reads

    def read(self, **request: Any) -> list[dict]:
        """A real ``load_session`` on the browser's history state; its reply frames."""
        from openprogram.interfaces.server.ws_actions.session import handle_load_session

        # A socket's commands run one at a time; a new snapshot closes the last.
        with self._read_lock:
            reply: list[dict] = []
            self._read_socket.sink = reply.append
            self._end_parking()
            self._end_parking = self.parking.begin(self.session_id)
            asyncio.run(handle_load_session(self._read_socket, {"session_id": self.session_id, **request}))
            return reply

    def _reply_delivered(self) -> None:
        """Frames parked during the read follow its reply into the timeline."""
        self._end_parking()

    def client_read(self, name: str = "read") -> None:
        """The read the client does on open or reconnect, delivered here."""
        reply = self.read()
        with self._lock:
            self.timeline.append({"read": {"name": name, "frames": reply}})
        self._reply_delivered()

    def page(self, direction: str, *, around: str | None = None, name: str = "page") -> dict:
        """The next history page; ``direction`` is ``older``, ``newer`` or ``around``."""
        window = self._current_window()
        field = {"older": {"history_before": window.get("before")},
                 "newer": {"history_after": window.get("after")},
                 "around": {"history_around": around}}[direction]
        reply = self.read(**field)
        page = next(f["data"] for f in reply if f["type"] == "session_history_page")
        with self._lock:
            self.timeline.append({"page": {"name": name, "direction": direction, "around": around, "frames": reply}})
        self._reply_delivered()
        return page["window"]

    def _current_window(self) -> dict:
        with self._lock:
            for step in reversed(self.timeline):
                frames = (step.get("page") or step.get("read") or {}).get("frames") or []
                for frame in frames:
                    if frame["type"] in {"session_loaded", "session_history_page"}:
                        return frame["data"]["window"]
        raise AssertionError("no history page has been read yet")

    def reopen(self) -> None:
        """This browser comes back later with nothing held and reads the chat."""
        with self._lock:
            self.timeline.append({"reopen": True})
        self.client_read("open")

    def disconnect(self) -> None:
        """The socket drops; frames sent while it is down never arrive."""
        with self._lock:
            self.timeline.append({"disconnect": True})
            self._connected = False

    def reconnect(self) -> None:
        with self._lock:
            self._connected = True
        self.client_read("reconnect")

    def checkpoint(self, name: str, *, partial: bool = False) -> None:
        """Compare what the client holds here with a whole cold read of the store."""
        self.wait_published()
        whole = self.whole_read()
        with self._lock:
            self.timeline.append({"checkpoint": {"name": name, "partial": partial, "cold": whole}})

    def _cold(self, read: Callable[[Any], list[dict]]) -> list[dict]:
        """Run ``read`` against a store that never saw this run."""
        from openprogram.runtime.context import session_db
        from openprogram.state.store.session import session_store as session_store_module
        from openprogram.state.store.session.session_store import SessionStore

        def run() -> list[dict]:
            fresh = SessionStore(self.sessions.root_path)
            # A cold read must not disturb the view the browser holds.
            views = dict(turn_index._views)
            restore = (session_db.default_db, session_store_module.shared._default_store)
            with self.server._sessions_lock:
                held = self.server._sessions.pop(self.session_id, None)
            session_db.default_db = lambda: fresh
            session_store_module.shared._default_store = fresh
            try:
                return read(fresh)
            finally:
                session_db.default_db, session_store_module.shared._default_store = restore
                turn_index._views.clear()
                turn_index._views.update(views)
                # A cold store's repairs read as another process's writes here. The
                # browser's numbered view is unchanged by them; keep its numbering.
                state = self.sessions.transcript_state(self.session_id)
                held_view = views.get(self.session_id)
                if held_view is not None and state is not None and held_view.fingerprint:
                    held_view.fingerprint = (state[0], *held_view.fingerprint[1:])
                with self.server._sessions_lock:
                    if held is not None:
                        self.server._sessions[self.session_id] = held

        return self._in_helper_thread(run)

    def whole_read(self) -> list[dict]:
        """The unpaged transcript, read from a store that never saw this run."""
        from openprogram.interfaces.server.transcript import reader
        from openprogram.interfaces.server.transcript.turn_index import get_view
        from openprogram.interfaces.server.ws_actions.session import handle_load_session

        def read(fresh) -> list[dict]:
            frames: list[dict] = []
            cold_socket = RecordingSocket(frames.append)
            asyncio.run(handle_load_session(cold_socket, {"session_id": self.session_id}))
            publisher.release(cold_socket)
            loaded = [f for f in frames if f["type"] == "session_loaded"]
            turns, window = reader.whole_read(fresh, get_view(fresh, self.session_id))
            loaded[0]["data"].update(json.loads(json.dumps({"turns": turns, "window": window}, default=str)))
            return loaded

        return self._cold(read)

    def recording(self, name: str, summary: str) -> dict:
        self.effects.effects.drain()
        cold = self.whole_read()
        return {"name": name, "summary": summary, "session_id": self.session_id,
                "timeline": self.timeline, "cold": cold}


@contextmanager
def live_chat(root: Path) -> Iterator[LiveChat]:
    """Yield a :class:`LiveChat` whose every store lives under ``root``."""
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import chat as chat_actions
    from openprogram.interfaces.server.ws_actions import session as session_actions
    from openprogram.models.providers.catalog import api_registry
    from openprogram.models.providers.streaming.budget import BudgetedRequest
    from openprogram.models.providers.types import Model
    from openprogram.operations.worker import lock as worker_lock_module
    from openprogram.runtime.agent import questions
    from openprogram.runtime.context import session_db
    from openprogram.runtime.conversation import session_config
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.events import WS_FRAME_EVENT, get_event_bus
    from openprogram.runtime.execution import AttemptStore, ExecutionStore, RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.event_effects import start_event_effects, stop_event_effects
    from openprogram.state.store.session import session_log
    from openprogram.state.store.session import session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    # Everything the chat resolves through the state directory (the event
    # log, session logs, project lookups) stays under ``root`` too, however
    # this runs: a copy of a transcript test run without tests/conftest.py
    # still has the person's real HOME, and once wrote its turns into a real
    # ``transcript-recording`` conversation in ~/.openprogram.
    lock_patch = pytest.MonkeyPatch()
    lock_patch.setenv("HOME", str(root))
    lock_patch.setenv("USERPROFILE", str(root))
    lock_patch.delenv("OPENPROGRAM_PROFILE", raising=False)
    # This process is the worker for this home, as a running server is; the
    # sync agent tool probes the lock and must find it held.
    lock_patch.setattr(worker_lock_module, "lock_path", lambda: root / "worker.lock")
    worker_lock = worker_lock_module.WorkerLock()
    if not worker_lock.try_acquire():
        lock_patch.undo()
        raise AssertionError("this home's worker lock is held")
    store = ExecutionStore(root / "execution.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    sessions = SessionStore(root / "sessions")
    sessions.create_session(SESSION_ID, "main")
    model = ScriptedModel()
    tools = ScriptedTools()
    effects = start_event_effects(store)
    publisher.start(lambda ws, payload: ws.enqueue_text_threadsafe(payload))
    chat = LiveChat(sessions=sessions, store=store, control=control, server=server,
                    model=model, tools=tools, effects=effects)
    registry = questions.QuestionRegistry()
    previous_provider = api_registry.get_api_provider(_API_NAME)
    api_registry.register_api_provider(_API_NAME, model)
    resolved = Model(id=_MODEL_NAME, name=_MODEL_NAME, api=_API_NAME, provider=_PROVIDER_NAME,
                     base_url="http://transcript-recording.invalid")

    patch = pytest.MonkeyPatch()
    # A sub-agent's job budget meters real providers; the scripted model has no bill.
    patch.setattr(BudgetedRequest, "begin", classmethod(lambda *_a, **_k: None))
    patch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    patch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    patch.setattr(session_db, "default_db", lambda: sessions)
    patch.setattr(session_store_module.shared, "_default_store", sessions)
    # Earlier fixtures may materialize the public package's lazy default_store
    # export while its source module is patched. Undo restores that old alias,
    # not __getattr__, so pin BOTH exports to the real singleton resolver here.
    # Keep the resolver dynamic: _cold temporarily replaces the shared pointer.
    import openprogram.state.store as public_store
    patch.setattr(session_store_module, "default_store", session_store_module.shared.default_store)
    patch.setattr(public_store, "default_store", session_store_module.shared.default_store)
    patch.setattr(server, "_sessions", {})
    patch.setattr(server, "_broadcast", chat.broadcast)
    patch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    patch.setattr(server, "refresh_context_stats", lambda *_a, **_k: None)
    patch.setattr(chat_actions.turn, "_db_agent_id", lambda _sid: "main")
    patch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    patch.setattr(dispatcher, "_load_agent_profile",
                  lambda _id: {"id": "main", "model": f"{_PROVIDER_NAME}/{_MODEL_NAME}"})
    patch.setattr(dispatcher, "_resolve_model", lambda _profile, _override: resolved)
    patch.setattr(loop_runner, "_resolve_tools", lambda *_a, **_k: tools.all())
    patch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *_a: tool)
    patch.setattr(questions, "get_question_registry", lambda: registry)
    patch.setattr(session_config, "save_session_run_config", lambda *_a, **kwargs: SimpleNamespace(
        tools_override=None, web_search=False, thinking_effort="medium",
        permission_mode=kwargs.get("permission_mode"), permission_rules=None,
        sandbox_enabled=None, additional_working_dirs=[],
    ))
    patch.setattr(session_config, "permission_from_config",
                  lambda config, default=None: config.permission_mode or default or "bypass")
    patch.setattr(session_config, "project_defaults", lambda _sid: {"permission_mode": "bypass"})

    async def activate(attempt, activation):
        driver = AgentProductionDriver(store, control_service=control, question_registry=registry)
        return await driver.activate(attempt, activation)

    control.activator = activate

    def forward(event) -> None:
        frame = event.payload.get("frame")
        if frame is not None:
            chat.broadcast(json.dumps(frame, default=str))

    unsubscribe = get_event_bus().subscribe(forward, types={WS_FRAME_EVENT})
    try:
        yield chat
    finally:
        for gate in tools.release.values():
            gate.set()
        try:
            wait_until(lambda: not server._is_run_active(SESSION_ID), timeout=_WAIT_SECONDS,
                       what="the run slot clearing at teardown")
        finally:
            # A stuck run still fails its test; it must not leak into the next.
            unsubscribe()
            publisher.stop()
            turn_index.forget_views()
            stop_event_effects(store)
            patch.undo()
            worker_lock.release()
            lock_patch.undo()
            session_log.claim_main_writer()
            if previous_provider is None:
                api_registry._registry.pop(_API_NAME, None)
                api_registry._original_registry.pop(_API_NAME, None)
            else:
                api_registry.register_api_provider(_API_NAME, previous_provider)
