"""One ``--print`` turn through the running worker.

The worker owns everything a turn may need that this short-lived process
does not have: the desktop App's built-in browser, the channel bridges, the
permission store, the memory writer and the sessions the Web UI shows. A
one-shot turn that ran here, in its own runtime, could not reach any of
them; the agent in a resumed Web session saw no browser at all.

So a one-shot turn is sent to the worker over its WebSocket, with the same
``chat`` command the Web UI sends, and this process only relays frames: it
collects the reply (or the structured output), answers the agent's
follow-up questions from the terminal, and stops the execution on Ctrl-C
or SIGTERM. The turn lands in the session store the way a Web turn does,
so the Web UI shows it live and on reload.

A stop is the Web UI's stop: the same ``execution.command`` cancel, which
names the execution whatever version it is at, and the relay waits, for a
bounded time, until
the worker says the execution has ended. The command then says whether
the turn stopped or may still be running.

The turn outlives the socket. A worker restart (a deploy, an update)
closes every connection while the execution checkpoints and resumes on
the worker that comes back; once the ack has named the execution, the
relay reconnects, reads where the execution stands and follows the same
session and message id to the end, reading the reply from the session
when the turn finished while no socket was open.

:func:`resolve_worker` says whether a worker is there to take the turn;
without one the caller runs the turn in-process as before.
"""
from __future__ import annotations

import contextlib

import dataclasses
import json
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

_log = logging.getLogger(__name__)

# Set to ``1`` to run a ``--print`` turn in this process even while a
# worker is up: for a turn that must not touch the worker's sessions, or
# to reproduce the fallback path.
IN_PROCESS_ENV = "OPENPROGRAM_PRINT_IN_PROCESS"

_CONNECT_TIMEOUT_S = 10.0
# After the worker closed the socket mid-turn (a restart), how long the relay
# keeps looking for a worker to follow the turn on, and how often it looks.
_REATTACH_WINDOW_S = 180.0
_REATTACH_INTERVAL_S = 2.0
# Reattached and hearing nothing, how often the execution's state is read
# again, so a turn that ended without a frame this socket saw still ends
# the relay.
_SNAPSHOT_INTERVAL_S = 15.0
# The longest one read of the socket waits. On Windows a blocking wait wakes
# for no signal (Ctrl-Break never, Ctrl-C not inside a lock wait); the
# handler runs only once the wait returns, so a quiet socket read with no
# limit would hold a stop until the next frame.
_SIGNAL_POLL_S = 0.5


class WorkerTurnError(RuntimeError):
    """The worker ended the turn with an error frame.

    ``code`` is the frame's error code when it carried one (an admission
    refusal, a structured-output failure); ``issues`` are the validation
    issues of a structured-output failure. ``reason``, ``retryable`` and
    ``kind`` are the runtime's classification of a failed turn as the
    error frame carries it (``ErrorReason`` value, whether retrying may
    help, the stable exception category), ``None`` where it carried none.
    """

    def __init__(self, message: str, *, code: str | None = None,
                 issues: list | None = None, reason: str | None = None,
                 retryable: bool | None = None, kind: str | None = None):
        super().__init__(message)
        self.code = code
        self.issues = list(issues or [])
        self.reason = reason
        self.retryable = retryable
        self.kind = kind


class WorkerTurnCancelled(RuntimeError):
    """The execution was cancelled before it produced a reply."""


class WorkerTurnStopped(KeyboardInterrupt):
    """Ctrl-C or SIGTERM stopped the relay; says what became of the turn.

    ``confirmed`` is true once the worker reported the execution ended
    (``status``: ``cancelled``, or how it ended when it finished before the
    cancel took). Otherwise ``detail`` says why the stop is not confirmed:
    the worker refused it, did not end the turn within the wait, or the
    connection closed; the turn may then still be running.
    """

    def __init__(self, confirmed: bool, status: str = "", detail: str = ""):
        super().__init__(detail or status or "stopped")
        self.confirmed = confirmed
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class WorkerTurn:
    """What one worker-run turn came back with."""

    reply: str
    structured_output: Any = None
    session_id: str = ""
    msg_id: str = ""
    assistant_msg_id: str = ""
    # Tool calls the reply's blocks show; ``None`` when the reply came
    # without its blocks (read back from the session after a restart).
    tool_calls: int | None = None
    # A Worker restart ended the turn and the reply is the one of the turn
    # that continued it (:meth:`_Relay._follow_continuation`).
    restarted: bool = False
    # Who ran the turn, as the Worker said (``handshake.server_identity``):
    # its version (``None`` when it did not say) and its capabilities.
    worker_version: str | None = None
    worker_capabilities: frozenset = frozenset()
    # What the turn's requests sent, as the Worker saw them (``wire_facts``
    # in the result frame); ``None`` from a Worker that sends none.
    wire_facts: list | None = None
    # A slash command the Worker answered itself (no model turn): its name.
    local_command: str | None = None


def resolve_worker():
    """The verified endpoint of the running worker, or ``None``.

    ``None`` means the turn runs in-process: no worker is up for this
    profile, its state files do not verify, or the environment asks for
    the in-process path.
    """
    if os.environ.get(IN_PROCESS_ENV, "").strip() == "1":
        return None
    from openprogram.host.security.backend_endpoint import (
        OwnerAuthError,
        resolve_backend_endpoint,
    )
    try:
        return resolve_backend_endpoint()
    except (OwnerAuthError, OSError, ValueError) as exc:
        _log.debug("no worker for the one-shot turn: %s", exc)
        return None


def _response_format_payload(response_format) -> Any:
    if response_format is None:
        return None
    if hasattr(response_format, "model_dump"):
        return response_format.model_dump(mode="json")
    if dataclasses.is_dataclass(response_format):
        return dataclasses.asdict(response_format)
    return response_format


def _terminal_ask(question: str, function: str) -> str | None:
    """Ask the agent's follow-up question on the terminal.

    Without a terminal to ask on (a pipeline, a cron job) the question
    goes unanswered: the turn continues as if the user had not replied,
    which is what the Web UI does when the page is gone.
    """
    if not sys.stdin.isatty():
        return None
    label = f" ({function})" if function else ""
    sys.stderr.write(f"\n? {question}{label}\n> ")
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except (OSError, ValueError):
        return None
    if not line:
        return None
    return line.rstrip("\n")


_LIVE_STATUSES = frozenset({"queued", "running", "pausing", "paused", "cancelling"})
_STOPPING_STATUSES = frozenset({"cancelling", "cancelled"})

# After Ctrl-C or SIGTERM the relay waits as the in-process run does: the
# grace periods, the poll and the stale-version retries are
# :mod:`openprogram.interfaces.cli.print_mode.print_stop`'s.


def _open(endpoint):
    """Connect to the worker; a handshake failure of any kind is a
    :class:`WorkerTurnError`."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect as _connect

    try:
        return _connect(
            endpoint.websocket_url,
            additional_headers={
                "Authorization": endpoint.authorization_header,
                "Origin": endpoint.origin,
            },
            open_timeout=_CONNECT_TIMEOUT_S,
            max_size=None,
        )
    except (OSError, ConnectionClosed, TimeoutError) as exc:
        raise WorkerTurnError(f"cannot connect to the worker: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - handshake failures of every kind
        raise WorkerTurnError(f"cannot connect to the worker: {exc}") from exc


def _reply_text(content: Any) -> str:
    """The text of a stored assistant message, whichever shape it was saved in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    return "" if content is None else str(content)


def _tool_blocks(blocks: Any) -> int | None:
    """How many tool calls a result frame's blocks show."""
    if not isinstance(blocks, list):
        return None
    return sum(1 for block in blocks
               if isinstance(block, dict) and block.get("type") == "tool")


class _Relay:
    """The state of one turn's relay across the sockets it travels on.

    The first socket carries the ``chat`` command and its ack, which names
    the execution. Should the worker close that socket (a restart), the
    execution outlives it: the relay opens a socket to the worker that
    comes back, reads the execution's state and goes on following the same
    session and message id, so the reply still reaches the terminal.
    """

    def __init__(self, session_id: str, request_id: str, on_text, ask, on_stop=None,
                 on_execution=None, on_server=None, on_ack=None, on_notice=None):
        self.session_id = session_id
        self.request_id = request_id
        self.on_text = on_text
        self.ask = ask
        # Called once a stop begins; returns the event a further Ctrl-C or
        # SIGTERM sets (``print_turn.hold_signals``).
        self.on_stop = on_stop
        # Called with the execution id once the ack names it.
        self.on_execution = on_execution
        # Called with the ack's data (the turn's permission mode, its
        # message ids), before ``on_execution``.
        self.on_ack = on_ack
        # Called with ``{to, from, cause}`` when another of the provider's
        # credentials took over (a ``retry`` frame with reason ``credential``).
        self.on_notice = on_notice
        self.execution_id: str | None = None
        self.status_version = None
        # The execution's status as the worker last broadcast it.
        self.status: str | None = None
        self.msg_id: str | None = None
        # Set once the relay follows the turn on a socket other than the one
        # that sent it: frames may have passed while no socket was open, so
        # the execution's state is read on connect and again whenever the
        # socket stays quiet.
        self.reattached = False
        self.awaiting_reply = False
        # Following the turn a Worker restart continued this one as.
        self.restarted = False
        # Waiting for a restart intent to settle: when to give up.
        self._continuation_deadline: float | None = None
        # What the Worker announced on connect (``worker_capabilities``)
        # and in the ack (``server.capabilities``).
        self.capabilities: set[str] = set()
        # Who the Worker is (``handshake.server_identity``), once it said.
        self.server: dict | None = None
        # Called with that identity each time the Worker gives it.
        self.on_server = on_server

    # -- helpers -----------------------------------------------------------

    def _take_ack(self, data: dict) -> None:
        """Keep what the ack of this turn's ``chat`` names."""
        self.execution_id = data.get("execution_id")
        self.status_version = data.get("status_version")
        self.msg_id = data.get("msg_id")
        self._note_server(data.get("server"))
        if self.on_ack is not None:
            self.on_ack(data)
        if self.execution_id and self.on_execution is not None:
            self.on_execution(str(self.execution_id))

    def _note_capabilities(self, data: dict, server: Any = None) -> None:
        """Keep what the Worker this socket reaches announced it does: a new
        socket may reach another Worker, so the set is replaced."""
        self.capabilities = {str(name) for name, on in data.items() if on is True}
        self._note_server(server)

    def _note_server(self, server: Any) -> None:
        """Keep who the Worker says it is and add the capabilities it names."""
        if not isinstance(server, dict):
            return
        self.server = server
        names = server.get("capabilities")
        if isinstance(names, list):
            self.capabilities |= {name for name in names if isinstance(name, str)}
        if self.on_server is not None:
            self.on_server(server)

    @property
    def worker_version(self) -> str | None:
        version = (self.server or {}).get("version")
        return version if isinstance(version, str) else None

    def _answers_this_send(self, data: dict) -> bool:
        echoed = data.get("client_request_id")
        if echoed is not None:
            return echoed == self.request_id
        return data.get("session_id") == self.session_id

    def _other_turn(self, data: dict) -> bool:
        """A frame of another execution in this session.

        A sub-agent the turn spawns runs as a branch of the same session
        and streams, ends and fails under its own message id; only the
        frames carrying the acked message id (or none, before the ack)
        belong to this turn.
        """
        theirs = data.get("msg_id")
        return bool(self.msg_id) and bool(theirs) and theirs != self.msg_id

    def _observe(self, execution: Any) -> None:
        """Keep the status and version of this turn's execution current."""
        if not isinstance(execution, dict) or not self.execution_id:
            return
        if execution.get("execution_id") not in (None, self.execution_id):
            return
        version = execution.get("status_version")
        if type(version) is int and (type(self.status_version) is not int
                                     or version >= self.status_version):
            self.status_version = version
            if execution.get("status"):
                self.status = str(execution["status"])

    def stop(self, ws, reconnect: Callable[[], Any] | None = None) -> WorkerTurnStopped:
        """Cancel the execution on the worker and wait until it has ended.

        The cancel is the Web UI's ``execution.command``. It names the
        execution; the version is only the one this relay last saw, since
        the worker takes a stop at the execution's current version. An
        older worker (one that does not announce ``cancel_ignores_version``)
        refuses a stale one: that cancel is sent again, same command id, with
        the version the refusal names. Before the ack names the execution,
        a Worker that announces ``cancel_by_request`` is sent the stop naming
        this send (session and ``client_request_id``) at once, and the send's
        admission ends as stopped; an older one is sent the stop once the
        ack names the execution. A refusal of the turn ends the stop at once,
        since nothing runs. Should the socket close (a worker restart), or
        be closed already (``ws`` is ``None``), the stop finds the worker
        again through ``reconnect``, reads the execution's state there and
        sends the cancel unless the execution is already stopping. The wait
        ends when the worker reports the execution ended, or when the shared
        stop clock runs out (:class:`print_stop.StopClock`: the grace period,
        shortened from the cancel once a further signal came). Returns what
        became of the turn.
        """
        from websockets.exceptions import ConnectionClosed

        from openprogram.interfaces.cli.print_mode import print_stop

        noted = self.on_stop() if self.on_stop is not None else None
        clock = print_stop.StopClock(noted)
        noted = clock.noted
        command_id = f"cli-print-{uuid.uuid4().hex}"
        # Whether the cancel is still to go out on the socket now open: not
        # yet sent, or sent on a socket that closed before the worker said
        # the execution is stopping.
        to_send = True
        stale_left = print_stop.STALE_RETRIES
        opened: list = []
        remaining = clock.remaining

        def send_cancel() -> None:
            nonlocal to_send
            ws.send(json.dumps({
                "type": "execution.command",
                "action": "execution.cancel",
                "command_id": command_id,
                "execution_id": self.execution_id,
                # A stop names its execution; the version is only what this
                # relay last saw (none yet for a function run's ack).
                "expected_version": self.status_version if type(self.status_version) is int else 0,
                "payload": {},
            }))
            to_send = False
            clock.sent()

        def ended() -> WorkerTurnStopped | None:
            if self.status in _LIVE_STATUSES or not self.status:
                return None
            return WorkerTurnStopped(True, self.status)

        def send_by_request() -> None:
            nonlocal to_send
            ws.send(json.dumps({
                "type": "execution.command",
                "action": "execution.cancel",
                "command_id": command_id,
                "session_id": self.session_id,
                "client_request_id": self.request_id,
                "payload": {},
            }))
            to_send = False
            clock.sent()

        def send_if_due() -> None:
            # Once the execution is named and not stopping already (the
            # worker kept an earlier cancel, or another client's); before
            # that, by this send's id where the Worker takes one.
            if not to_send:
                return
            if not self.execution_id:
                if "cancel_by_request" in self.capabilities:
                    send_by_request()
                return
            if self.status not in _STOPPING_STATUSES and ended() is None:
                send_cancel()

        def refused_by_request() -> None:
            # The stop naming this send was refused: it goes out again,
            # naming the execution, once the ack names it.
            nonlocal to_send
            to_send = True

        def reach() -> bool:
            # A socket to the worker, on which the execution's state is asked.
            nonlocal ws
            while remaining() > 0:
                try:
                    endpoint = (reconnect or resolve_worker)()
                except Exception:  # noqa: BLE001 - the worker's state files mid-write
                    endpoint = None
                if endpoint is not None:
                    try:
                        candidate = _open(endpoint)
                    except WorkerTurnError:
                        candidate = None
                    if candidate is not None:
                        opened.append(candidate)
                        try:
                            self.query_state(candidate)
                        except ConnectionClosed:
                            continue
                        ws = candidate
                        self.reattached = True
                        return True
                time.sleep(max(0.0, min(_REATTACH_INTERVAL_S, remaining())))
            return False

        def unconfirmed() -> WorkerTurnStopped:
            waited = clock.waited()
            if not self.execution_id:
                detail = "the worker had not taken the turn yet"
            elif ws is None:
                detail = "no worker was reachable to stop the turn"
            elif clock.sent_at is None:
                detail = "the worker did not say where the turn stands"
            else:
                detail = (f"the worker had not ended the turn {waited:.0f} s "
                          "after the stop")
            return WorkerTurnStopped(False, self.status or "", detail)

        try:
            while True:
                try:
                    if ws is None:
                        if not reach():
                            return unconfirmed()
                    elif to_send:
                        send_if_due()
                    done = self._stop_wait(ws, command_id, send_if_due, refused_by_request,
                                           ended, remaining, noted)
                except ConnectionClosed as exc:
                    if not self.execution_id:
                        return WorkerTurnStopped(
                            False, self.status or "",
                            f"the connection to the worker closed ({exc})")
                    # A restart: the execution goes on, or resumes, on the
                    # worker that comes back, so the cancel goes there.
                    ws = None
                    if self.status not in _STOPPING_STATUSES:
                        to_send = True
                    continue
                if isinstance(done, WorkerTurnStopped):
                    return done
                if done == "stale":
                    if stale_left > 0 and "cancel_ignores_version" not in self.capabilities:
                        stale_left -= 1
                        send_cancel()
                        continue
                    return WorkerTurnStopped(
                        False, self.status or "", "the worker refused the stop (stale_version)")
                return unconfirmed()
        finally:
            for extra in opened:
                with contextlib.suppress(Exception):  # noqa: BLE001 - closing a socket already gone
                    extra.close()

    def _stop_wait(self, ws, command_id, send_if_due, refused_by_request, ended,
                   remaining, noted):
        """Read ``ws`` while a stop waits. Returns a :class:`WorkerTurnStopped`
        once the turn's end is known, ``"stale"`` when the cancel was refused
        for a stale version, or ``None`` when the wait ran out. Raises
        ``ConnectionClosed`` when the socket closes."""
        from websockets.exceptions import ConnectionClosed

        from openprogram.interfaces.cli.print_mode.print_stop import STOP_POLL_S

        while True:
            left = remaining()
            if left <= 0:
                return None
            try:
                raw = ws.recv(timeout=min(left, STOP_POLL_S))
            except TimeoutError:
                continue
            except KeyboardInterrupt:
                # A further Ctrl-C under the default handler: leave sooner.
                noted.set()
                continue
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
            if kind == "worker_capabilities":
                self._note_capabilities(data, frame.get("server"))
                send_if_due()
                continue
            if kind == "chat_ack":
                if self._answers_this_send(data) and not self.execution_id:
                    self._take_ack(data)
                    send_if_due()
                continue
            if not self.execution_id:
                if kind == "execution.command.updated":
                    command = frame.get("command") or data.get("command") or {}
                    if command.get("command_id") == command_id and command.get("status") == "rejected":
                        refused_by_request()
                    continue
                # The turn was refused before it started: nothing runs.
                if kind == "action_error":
                    return WorkerTurnStopped(True, "refused")
                if kind == "operation_error" and data.get("session_id") in (None, self.session_id):
                    return WorkerTurnStopped(True, "refused")
                if kind == "chat_response" and self._answers_this_send(data):
                    if data.get("type") == "error":
                        return WorkerTurnStopped(True, "refused")
                    if data.get("type") == "local_command":
                        # A slash command the worker answered itself.
                        return WorkerTurnStopped(True, "completed")
                continue
            if kind == "execution.updated":
                self._observe(frame.get("execution") or data.get("execution"))
                send_if_due()
            elif kind == "execution.replay":
                if (frame.get("execution_id") or data.get("execution_id")) != self.execution_id:
                    continue
                if frame.get("error"):
                    return WorkerTurnStopped(
                        False, "", f"the worker no longer knows the turn ({frame['error']})")
                self._observe(frame.get("snapshot") or data.get("snapshot"))
                send_if_due()
            elif kind == "execution.command.updated":
                command = frame.get("command") or data.get("command") or {}
                if command.get("command_id") != command_id:
                    continue
                self._observe(frame.get("execution") or data.get("execution"))
                if command.get("status") == "rejected":
                    self._observe(command.get("latest_snapshot"))
                    done = ended()
                    if done is not None:
                        return done
                    code = str(command.get("rejection_code") or "rejected")
                    if code == "stale_version":
                        return "stale"
                    return WorkerTurnStopped(
                        False, self.status or "", f"the worker refused the stop ({code})")
            elif kind == "chat_response":
                if data.get("session_id") != self.session_id or self._other_turn(data):
                    continue
                if data.get("type") in ("result", "cancelled", "error"):
                    # The turn's end; its execution's state says how.
                    # Should the socket be closing, the frames already
                    # received still say it.
                    try:
                        self.query_state(ws)
                    except ConnectionClosed:
                        pass
                continue
            else:
                continue
            done = ended()
            if done is not None:
                return done

    def query_state(self, ws) -> None:
        """Ask the worker where the execution stands; the answer is an
        ``execution.replay`` frame."""
        ws.send(json.dumps({
            "action": "execution.replay",
            "execution_id": self.execution_id,
            "after_sequence": 0,
            "snapshot_only": True,
        }))

    @property
    def assistant_msg_id(self) -> str:
        return f"{self.msg_id}_reply" if self.msg_id else ""

    # -- the loop ----------------------------------------------------------

    def follow(self, ws, reconnect: Callable[[], Any] | None = None) -> WorkerTurn:
        """Relay frames on ``ws`` until the turn ends.

        Raises ``ConnectionClosed`` when the worker closes the socket; the
        caller decides whether the turn can be followed elsewhere.
        ``reconnect`` finds the worker again should it restart during a stop.
        """
        try:
            return self._follow(ws)
        except WorkerTurnStopped:
            raise
        except KeyboardInterrupt:
            # Ctrl-C or SIGTERM, wherever in the loop it landed.
            raise self.stop(ws, reconnect) from None

    def _follow(self, ws) -> WorkerTurn:
        heard = time.monotonic()
        while True:
            wait = _SIGNAL_POLL_S
            if self.reattached:
                wait = max(0.0, min(wait, heard + _SNAPSHOT_INTERVAL_S - time.monotonic()))
            try:
                raw = ws.recv(timeout=wait)
            except TimeoutError:
                if self.reattached and time.monotonic() - heard >= _SNAPSHOT_INTERVAL_S:
                    # Quiet socket after a reattach: the turn may have ended
                    # with frames this socket never saw.
                    self.query_state(ws)
                    heard = time.monotonic()
                continue
            heard = time.monotonic()
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            data = frame.get("data")
            if not isinstance(data, dict):
                data = {}

            if kind == "worker_capabilities":
                self._note_capabilities(data, frame.get("server"))
                continue
            if kind == "chat_ack":
                if self._answers_this_send(data):
                    self._take_ack(data)
                continue
            if kind == "execution.updated":
                self._observe(frame.get("execution") or data.get("execution"))
                continue
            if kind == "action_error":
                raise WorkerTurnError(
                    str(frame.get("message") or data.get("message")
                        or "the worker refused the turn"),
                    code=frame.get("code") or data.get("code"),
                )
            if kind == "operation_error":
                if data.get("session_id") in (None, self.session_id):
                    raise WorkerTurnError(
                        str(data.get("message") or "the worker refused the turn"),
                        code=data.get("code"),
                    )
                continue
            if kind == "execution.replay":
                if (frame.get("execution_id") or data.get("execution_id")) != self.execution_id:
                    continue
                self._on_state(ws, frame)
                continue
            if kind == "session_loaded":
                if self.awaiting_reply and data.get("id") == self.session_id:
                    return self._reply_from_session(data)
                continue
            if kind != "chat_response" or data.get("session_id") != self.session_id:
                continue
            if self._other_turn(data):
                continue
            if not self.execution_id and data.get("client_request_id") != self.request_id:
                # Before the ack names the turn, only a frame echoing this
                # send is about it (a refusal, a slash command's answer);
                # another branch of the session may end or stop meanwhile.
                continue

            what = data.get("type")
            if what == "stream_event":
                event = data.get("event")
                if (self.on_text is not None and isinstance(event, dict)
                        and event.get("type") == "text"):
                    from openprogram.state.reply_text import stream_text
                    self.on_text(stream_text(event))
                elif (self.on_notice is not None and isinstance(event, dict)
                        and event.get("type") == "retry" and event.get("reason") == "credential"
                        and isinstance(event.get("credential"), dict) and event["credential"].get("to")):
                    self.on_notice(event["credential"])
            elif what == "result":
                if self.status in _STOPPING_STATUSES:
                    # A stop from elsewhere (the Web UI's stop button): the
                    # worker still sends the result frame, with the partial
                    # reply in its blocks, but the turn did not complete.
                    raise WorkerTurnCancelled("cancelled")
                return WorkerTurn(
                    reply=str(data.get("content") or ""),
                    structured_output=data.get("structured_output"),
                    session_id=self.session_id,
                    msg_id=str(data.get("msg_id") or self.msg_id or ""),
                    assistant_msg_id=str(data.get("assistant_msg_id") or ""),
                    tool_calls=_tool_blocks(data.get("blocks")),
                    restarted=self.restarted,
                    wire_facts=data.get("wire_facts") if isinstance(
                        data.get("wire_facts"), list) else None,
                )
            elif what == "local_command":
                # A slash command the worker answered itself; no model turn
                # follows.
                return WorkerTurn(
                    reply=str(data.get("content") or ""),
                    session_id=self.session_id,
                    local_command=str(data.get("command") or "") or None,
                )
            elif what == "error":
                if not self._answers_this_send(data):
                    continue
                raise WorkerTurnError(
                    str(data.get("content") or data.get("message") or "turn failed"),
                    code=data.get("code"),
                    issues=data.get("issues"),
                    reason=data.get("reason"),
                    retryable=data.get("retryable"),
                    kind=data.get("kind"),
                )
            elif what == "cancelled":
                raise WorkerTurnCancelled(str(data.get("content") or "cancelled"))
            elif what == "follow_up_question":
                answer = None
                if self.ask is not None:
                    answer = self.ask(
                        str(data.get("question") or ""),
                        str(data.get("function") or ""),
                    )
                ws.send(json.dumps({
                    "action": "follow_up_answer",
                    "session_id": self.session_id,
                    "answer": answer if answer is not None else {"_cancelled": True},
                }, ensure_ascii=False))

    def _on_state(self, ws, frame: dict) -> None:
        """Act on the execution's state read after a reattach.

        A live execution is followed on; one that ended while no socket was
        open ends the relay the way its frames would have: the reply is read
        from the session, a failure or cancellation is raised.
        """
        error = frame.get("error")
        if error:
            raise WorkerTurnError(
                f"the worker no longer knows the turn's execution ({error}); "
                "the Web UI shows the session"
            )
        snapshot = frame.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        self._observe(snapshot)
        status = str(snapshot.get("status") or "")
        reason = snapshot.get("reason_code")
        if status in _LIVE_STATUSES or not status:
            return
        if status == "completed":
            if not self.awaiting_reply:
                self.awaiting_reply = True
                ws.send(json.dumps({"action": "load_session", "session_id": self.session_id}))
            return
        if status == "cancelled":
            raise WorkerTurnCancelled("cancelled")
        if status == "interrupted" and self._follows_restart(ws):
            return
        if status == "reconciliation_required":
            raise WorkerTurnError(
                "the turn stopped: an operation's outcome is unknown and needs "
                "checking in the Web UI", code=reason or status,
            )
        detail = f" ({reason})" if reason else ""
        raise WorkerTurnError(f"the turn {status} on the worker{detail}", code=reason or status)

    def _follows_restart(self, ws) -> bool:
        """Whether an interrupted turn goes on as the turn a Worker restart
        continues it with; if so, the relay now follows that turn or waits
        for it.

        What decides is the restart intent recorded on the turn, not the
        reason it was interrupted with: a turn whose Worker died is
        interrupted as ``owner_lost`` and only continued once the next
        Worker settles the intent, seconds later. While the intent is
        unsettled the state is read again; it must settle within
        :data:`_REATTACH_WINDOW_S`.
        """
        outcome, successor = _restart_continuation(str(self.execution_id))
        if outcome == "admitted":
            self._continuation_deadline = None
            self._follow_continuation(ws, str(successor))
            return True
        if outcome != "pending":
            return False
        now = time.monotonic()
        if self._continuation_deadline is None:
            self._continuation_deadline = now + _REATTACH_WINDOW_S
        if now >= self._continuation_deadline:
            raise WorkerTurnError(
                "the worker restarted and has not continued the turn; "
                "the Web UI shows it", code="restart_pending",
            )
        time.sleep(_REATTACH_INTERVAL_S)
        self.query_state(ws)
        return True

    def _follow_continuation(self, ws, execution_id: str) -> None:
        """Follow the turn a Worker restart continued this one as.

        It is admitted with the instruction ``<execution>_user`` and the reply
        ``<execution>_user_reply``; its frames carry that message id, and a
        stop from here on cancels it.
        """
        self.execution_id = execution_id
        self.msg_id = f"{execution_id}_user"
        self.status = None
        self.status_version = None
        self.restarted = True
        self.query_state(ws)

    def _reply_from_session(self, data: dict) -> WorkerTurn:
        wanted = self.assistant_msg_id
        for row in data.get("messages") or ():
            if isinstance(row, dict) and row.get("id") == wanted:
                return WorkerTurn(
                    reply=_reply_text(row.get("content")),
                    session_id=self.session_id,
                    msg_id=self.msg_id or "",
                    assistant_msg_id=wanted,
                    restarted=self.restarted,
                )
        raise WorkerTurnError(
            "the turn finished while the worker restarted, but its reply is not "
            "in the session; the Web UI shows what happened"
        )


def _restart_continuation(execution_id: str) -> tuple[str, str | None]:
    """Where a Worker restart left ``execution_id``: ``("admitted", id)``
    once the turn continuing it was admitted, ``("pending", None)`` while
    its latest restart intent asks for a new turn and is not settled yet,
    ``("none", None)`` otherwise (no intent, another mode, any other
    outcome). Read from this profile's execution store, which the Worker
    writes and this process can read; the continuation's id derives from
    the intent (``execution_tree.continuation_key``)."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.execution_tree import (
        RESTART_REQUESTED,
        RESTART_SETTLED,
        continuation_key,
    )

    try:
        store = default_store().fresh_view()
        execution = store.get_execution(execution_id)
        if execution is None:
            return "none", None
        events = store.list_events(execution_id)
        requests = [event for event in events if event.kind == RESTART_REQUESTED]
        if not requests or (requests[-1].payload or {}).get("mode") != "new_turn":
            return "none", None
        intent = requests[-1]
        outcomes = [(event.payload or {}).get("outcome") for event in events
                    if event.kind == RESTART_SETTLED
                    and (event.payload or {}).get("request_sequence") == intent.sequence]
        if not outcomes:
            return "pending", None
        if outcomes[-1] != "new_turn_admitted":
            return "none", None
        successor = store.admission_execution_id(
            execution.session_id, continuation_key(execution_id, intent.sequence))
    except Exception:  # noqa: BLE001 - reported as a turn the Web UI shows
        _log.warning("continuation of %s unreadable", execution_id, exc_info=True)
        return "none", None
    if successor is None or store.get_execution(successor) is None:
        return "none", None
    return "admitted", successor


def _reattach(relay: _Relay, resolve: Callable[[], Any], closed: Exception):
    """Reach a worker again after one closed the socket mid-turn.

    Returns an open socket on which the execution's state has been asked
    for, or raises :class:`WorkerTurnError` once the window passes.
    """
    deadline = time.monotonic() + _REATTACH_WINDOW_S
    while time.monotonic() < deadline:
        time.sleep(_REATTACH_INTERVAL_S)
        try:
            endpoint = resolve()
        except Exception:  # noqa: BLE001 - the worker's state files mid-write
            endpoint = None
        if endpoint is None:
            continue
        try:
            ws = _open(endpoint)
        except WorkerTurnError:
            continue
        relay.reattached = True
        try:
            relay.query_state(ws)
        except Exception:  # noqa: BLE001 - closed again at once; try the next
            ws.close()
            continue
        return ws
    raise WorkerTurnError(
        "the worker closed the connection before the turn finished "
        f"({closed}) and none came back within {int(_REATTACH_WINDOW_S)} s; "
        "the turn continues on the worker and the Web UI shows it"
    ) from closed


def run_turn_via_worker(
    endpoint,
    session_id: str,
    text: str,
    *,
    response_format=None,
    agent_id: str | None = None,
    model: str | None = None,
    project_id: str | None = None,
    non_interactive: bool = False,
    on_text: Callable[[str], None] | None = None,
    ask: Callable[[str, str], str | None] | None = _terminal_ask,
    reconnect: Callable[[], Any] | None = None,
    on_stop: Callable[[], threading.Event] | None = None,
    on_execution: Callable[[str], None] | None = None,
    effort: str | None = None,
    on_server: Callable[[dict], None] | None = None,
    permission_mode: str | None = None,
    branch_id: str | None = None,
    no_web: bool = False,
    on_ack: Callable[[dict], None] | None = None,
    on_notice: Callable[[dict], None] | None = None,
) -> WorkerTurn:
    """Send one chat turn to the worker and wait for its result.

    Frames of other sessions share the socket (the worker broadcasts every
    ``chat_response``) and are skipped. ``on_text`` receives the streamed
    text deltas as they arrive; ``ask`` answers a ``follow_up_question``
    frame, ``None`` meaning no answer.

    A worker restart (a deploy, an update) closes the socket while the
    execution goes on, or resumes, on the worker that comes back. Once the
    ack has named the execution, the relay reconnects (``reconnect`` finds
    the worker again, :func:`resolve_worker` by default), reads where the
    execution stands and follows it to its end; a turn that finished in the
    meantime has its reply read from the session.

    Raises :class:`WorkerTurnError` for an error frame, an ``action_error``
    or ``operation_error`` that answers the send, a socket that closes
    before the ack, a worker that does not come back in time, or an
    execution that failed or was interrupted while no socket was open;
    :class:`WorkerTurnCancelled` when the execution was cancelled
    elsewhere; :class:`WorkerTurnStopped` (a ``KeyboardInterrupt``) on
    Ctrl-C or SIGTERM, once the relay has cancelled the execution and
    waited for the worker to end it, saying whether it did. ``on_stop`` is
    called when that stop begins and returns the event a further signal
    sets, which shortens the wait. ``on_execution`` receives the
    execution id once the ack names it, however the turn then ends;
    ``on_server`` who the Worker says it is, on connect and in the ack
    (``handshake.server_identity``); ``on_ack`` the ack's data, which names
    the permission mode the turn runs with. The command says who sent it
    (``client``), and the returned turn carries the Worker's version and
    capabilities.

    ``model`` (``provider/id``), ``effort`` and ``permission_mode`` apply to
    this turn only (``turn_model``, ``turn_effort``,
    ``turn_permission_mode``): nothing is saved on the conversation or its
    branch. ``project_id`` binds a conversation the turn starts, as the Web
    composer's folder does on a first message. ``branch_id`` runs the turn
    on that branch of the conversation.

    ``non_interactive`` tells the worker no one is behind the turn (no
    terminal to ask on), as the in-process turn's authority does: an
    operation that would ask for approval is refused at once and the
    question tool is not offered, so the turn never waits on a card no one
    will answer.
    """
    from websockets.exceptions import ConnectionClosed

    request_id = uuid.uuid4().hex
    command: dict[str, Any] = {
        "action": "chat",
        "text": text,
        "session_id": session_id,
        "client_request_id": request_id,
        "client": _client_identity(),
    }
    if agent_id:
        command["agent_id"] = agent_id
    # This turn's own choices, never saved (``handshake`` ``turn_overrides``).
    if model:
        command["turn_model"] = model
    if effort:
        command["turn_effort"] = effort
    if permission_mode:
        command["turn_permission_mode"] = permission_mode
    if no_web:
        # ``--no-web`` (``handshake`` ``no_web``): no web tool this turn.
        command["turn_no_web"] = True
    if branch_id:
        command["branch_id"] = branch_id
    if project_id:
        command["project_id"] = project_id
    if non_interactive:
        command["interaction"] = "non-interactive"
    payload = _response_format_payload(response_format)
    if payload is not None:
        command["response_format"] = payload

    relay = _Relay(session_id, request_id, on_text, ask, on_stop, on_execution, on_server,
                   on_ack, on_notice)
    find = reconnect or resolve_worker
    ws = _open(endpoint)
    try:
        ws.send(json.dumps(command, ensure_ascii=False))
        while True:
            try:
                turn = relay.follow(ws, find)
                return dataclasses.replace(
                    turn, worker_version=relay.worker_version,
                    worker_capabilities=frozenset(relay.capabilities))
            except ConnectionClosed as exc:
                ws.close()
                ws = None
                if not relay.execution_id:
                    raise WorkerTurnError(
                        "the worker closed the connection before the turn "
                        f"started ({exc})"
                    ) from exc
                try:
                    ws = _reattach(relay, find, exc)
                except WorkerTurnStopped:
                    raise
                except KeyboardInterrupt:
                    # Stopped while looking for the worker: the stop goes on
                    # looking, and cancels the turn on the one that comes back.
                    raise relay.stop(None, find) from None
    finally:
        if ws is not None:
            ws.close()


def compact_via_worker(endpoint, session_id: str, *,
                       keep_recent_tokens: int | None = None) -> str:
    """Compact ``session_id`` on the Worker, as the Web UI's ``/compact``
    does (the ``compact`` action), and wait for it to end. Returns what the
    Worker said; raises :class:`WorkerTurnError` for a refusal or a failed
    compaction."""
    command: dict[str, Any] = {"action": "compact", "session_id": session_id}
    if keep_recent_tokens is not None:
        command["keep_recent_tokens"] = keep_recent_tokens
    ws = _open(endpoint)
    try:
        ws.send(json.dumps(command))
        while True:
            try:
                raw = ws.recv(timeout=_SIGNAL_POLL_S)
            except TimeoutError:
                continue
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict) or frame.get("type") != "chat_response":
                continue
            data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
            if data.get("session_id") not in (None, session_id):
                continue
            what = data.get("type")
            if what == "compaction_finished":
                return str(data.get("content") or data.get("summary") or "Compacted.")
            if what == "local_command" and data.get("msg_id") in (None, "compact"):
                return str(data.get("content") or "")
            if what in ("compaction_failed", "error"):
                raise WorkerTurnError(
                    str(data.get("error") or data.get("content") or "compact failed"),
                    code=data.get("code"), kind=data.get("kind"))
    finally:
        ws.close()


#: The name this command gives itself in a ``chat`` command.
CLIENT_NAME = "openprogram-cli"


def _client_identity() -> dict[str, str]:
    """Who sends the command (``handshake.client_identity`` reads it)."""
    from openprogram.interfaces.server.handshake import package_version

    version = package_version()
    return {"name": CLIENT_NAME, **({"version": version} if version else {})}


def structured_output_error(error: WorkerTurnError):
    """The :class:`StructuredOutputError` a worker-side structured-output
    failure stands for, so the CLI exits with the same code either way."""
    from openprogram.models.providers.structured_output import (
        StructuredOutputError,
        StructuredOutputSchemaError,
        StructuredOutputUnsupportedError,
    )
    code = error.code or "generation_failed"
    if code == "invalid_schema":
        cls = StructuredOutputSchemaError
    elif code == "unsupported":
        cls = StructuredOutputUnsupportedError
    else:
        cls = StructuredOutputError
    return cls(str(error), code=code, issues=error.issues)


__all__ = [
    "IN_PROCESS_ENV",
    "WorkerTurn",
    "WorkerTurnCancelled",
    "WorkerTurnError",
    "WorkerTurnStopped",
    "compact_via_worker",
    "resolve_worker",
    "run_turn_via_worker",
    "structured_output_error",
]
