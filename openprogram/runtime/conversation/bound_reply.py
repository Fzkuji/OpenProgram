"""Deliver a bound chat's assistant replies to its channel conversation.

A chat bound to a channel account (its session names ``channel`` and
``account_id``: picked on the composer's channel chip, or a conversation
that arrived from the channel) gets every assistant reply in that channel,
whatever started the turn. The source of the turn decides who sends it:

* a turn that came in from the chat's own channel (``source`` is that
  channel) is answered by the channels' inbound dispatcher, so it is
  skipped here, and nothing is sent twice;
* a component turn of another execution (``agent_spawn``) is not a reply
  of this chat and is skipped;
* a background turn (an always-on check-in, a scheduled task's run) sends
  nothing here; what it has to say goes out once, as its report
  (``always_on.runtime``);
* a turn that receives a handoff message (📨) answers the conversation
  that sent it, so it is skipped; the reply that comes back (↩️) is sent;
* every other turn (typed in the App, a Goal turn, a reminder) is sent
  from here.

The recipient is the peer whose messages the chat receives: its routing
alias for the bound account, else the peer of a conversation that arrived
from the channel, else the account owner's chat (a WeChat bot's owner,
who scanned its QR code). A chat with no known peer (a Telegram, Discord
or Slack account picked without one) sends nothing rather than guess.

Only the reply text goes out, through the channels' own reply path
(``channel_link.open_reply``: paragraphs as they stream, text before a
tool call before the tool runs, files as attachments). A failed send is
logged and shown in the App as a ``channel_delivery_failed`` chat frame.
A response sent again after a transport failure drops its unsent text;
paragraphs already sent stay, and are not sent again when the new
response repeats them.

The end of a delivery (the rest of the reply, its files) runs on a
background lane per recipient, in order, so a slow platform never holds
the turn, its result in the App or the next turn of the chat. A turn
that pauses (an approval) sends everything it streamed before it stops;
the attempt that resumes it sends only what it streams itself, plus the
turn's files, so nothing before the pause goes out twice, however many
times the turn paused and whichever path sent the first part.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

_log = logging.getLogger(__name__)

#: Turn sources that are never a reply of the chat they run in.
_NOT_REPLIES = frozenset({"agent_spawn"})

_CHANNEL_LABELS = {
    "wechat": "WeChat", "telegram": "Telegram", "discord": "Discord", "slack": "Slack",
}

# Seconds an idle delivery lane waits for more work before its thread ends.
_LANE_IDLE_S = 5.0


class _Lanes:
    """Background work per recipient, run in the order it was queued."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._queues: dict[tuple, "queue.Queue[Callable[[], None]]"] = {}
        self._threads: dict[tuple, threading.Thread] = {}
        self._pending = 0

    def submit(self, key: tuple, work: Callable[[], None]) -> None:
        with self._lock:
            lane = self._queues.setdefault(key, queue.Queue())
            lane.put(work)
            self._pending += 1
            thread = self._threads.get(key)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=self._run, args=(key, lane), daemon=True,
                                          name=f"bound-reply-{key[0]}")
                self._threads[key] = thread
                thread.start()

    def _run(self, key: tuple, lane: "queue.Queue[Callable[[], None]]") -> None:
        while True:
            try:
                work = lane.get(timeout=_LANE_IDLE_S)
            except queue.Empty:
                with self._lock:
                    if lane.empty():
                        # submit() queues under the same lock, so nothing
                        # lands here after this check.
                        self._threads.pop(key, None)
                        self._queues.pop(key, None)
                        return
                continue
            try:
                work()
            except Exception:  # noqa: BLE001 - one delivery must not stop the lane
                _log.warning("bound reply delivery raised", exc_info=True)
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending == 0:
                        self._idle.notify_all()

    def wait(self, timeout: Optional[float] = None) -> bool:
        with self._lock:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)


_LANES = _Lanes()


def wait_for_deliveries(timeout: Optional[float] = None) -> bool:
    """Block until every queued delivery has ended; False on timeout.

    A process that exits right after its turn (a scheduled job's child)
    calls this first, or the rest of the reply dies with it."""
    return _LANES.wait(timeout)


@dataclass(frozen=True)
class ReplyTarget:
    channel: str
    account_id: str
    peer_id: str


def flattens_tables(session_id: str) -> bool:
    """An always-on Agent's conversation: its tables reach the chat app flattened."""
    from openprogram.runtime.conversation.always_on.runtime import agent_for_session
    try:
        return agent_for_session(session_id) is not None
    except Exception:  # noqa: BLE001 - a lookup failure must not lose the reply
        _log.exception("always-on lookup failed for %s", session_id)
        return False


def reply_target(session_id: str, session: Optional[dict] = None) -> Optional[ReplyTarget]:
    """Where ``session_id``'s replies go, or None for an unbound chat or
    one whose recipient is unknown."""
    if session is None:
        from openprogram.runtime.context.session_db import default_db
        session = default_db().get_session(session_id)
    if not session:
        return None
    channel = str(session.get("channel") or "").strip().lower()
    account_id = str(session.get("account_id") or "").strip()
    if not channel or not account_id:
        return None
    from openprogram.runtime.agent_registry import session_aliases
    for alias in session_aliases.list_for_session(session_id):
        if alias.get("channel") != channel or alias.get("account_id") != account_id:
            continue
        peer_id = str((alias.get("peer") or {}).get("id") or "")
        if peer_id and peer_id != "*":
            return ReplyTarget(channel, account_id, peer_id)
    if session.get("peer_id") and session.get("source") == channel:
        return ReplyTarget(channel, account_id, str(session["peer_id"]))
    from openprogram.runtime import channel_link
    owner = channel_link.owner_peer(channel, account_id)
    return ReplyTarget(channel, account_id, owner) if owner else None


def delivered_here(source: Optional[str], channel: str, *, resumed: bool = False) -> bool:
    """Whether a turn from ``source`` in a chat bound to ``channel`` is
    sent by this module (False: the inbound dispatcher sends it, or it is
    no reply of the chat).

    A ``resumed`` turn continues from a checkpoint in a new attempt (after
    an approval, or a restart that interrupted it). The inbound dispatcher
    only waits for a turn's first attempt, so a resumed turn from the
    chat's own channel is sent from here too.
    """
    source = (source or "").strip().lower()
    if source in _NOT_REPLIES:
        return False
    return resumed or source != channel


class BoundReply:
    """One turn's reply going out to its chat's channel conversation.

    ``resumed``: this attempt continues a turn that paused (or was
    interrupted); the earlier attempt sent what it wrote, so only this
    attempt's own stream and the turn's files go out.
    """

    def __init__(self, req: Any, target: ReplyTarget, assistant_msg_id: str, *,
                 resumed: bool = False) -> None:
        self.session_id = req.session_id
        self.target = target
        self.assistant_msg_id = assistant_msg_id
        self.resumed = resumed
        self._own_ids = {None, req.user_msg_id, assistant_msg_id,
                         assistant_msg_id.removesuffix("_reply")}
        self._delivery: Any = None
        self._open_failure: Optional[_Failure] = None
        self._failed = False
        self._done = False

    # -- stream ---------------------------------------------------------

    def tap(self, on_event: Callable[[dict], None]) -> Callable[[dict], None]:
        """``on_event`` that also streams this turn's reply text out."""
        def _on_event(env: dict) -> None:
            on_event(env)
            try:
                self._observe(env)
            except Exception:
                # The App transcript is the record; a failed send is
                # reported when the turn ends.
                _log.warning("bound reply stream failed for %s", self.session_id,
                             exc_info=True)
        return _on_event

    def _observe(self, env: dict) -> None:
        if self._done or self._open_failure is not None or env.get("type") != "chat_response":
            return
        data = env.get("data") or {}
        if (data.get("type") != "stream_event"
                or data.get("session_id") not in (None, self.session_id)
                or data.get("msg_id") not in self._own_ids):
            return
        event = data.get("event") or {}
        kind = event.get("type")
        if kind == "text" and event.get("text"):
            delivery = self._open()
            if delivery is not None:
                from openprogram.state.reply_text import stream_text
                delivery.feed(stream_text(event))
        elif kind == "tool_use" and self._delivery is not None:
            self._delivery.flush(response_ended=not event.get("provider_executed"))
        elif kind == "retry" and event.get("restart") and self._delivery is not None:
            # The request is sent again after a transport failure and what it
            # streamed is void (``streaming/resend.py``).
            self._delivery.restart()

    def _open(self) -> Any:
        """The delivery, opened on first use; None once opening failed
        (the failure is reported when the turn ends, once)."""
        if self._delivery is None and self._open_failure is None:
            from openprogram.runtime import channel_link
            t = self.target
            try:
                self._delivery = channel_link.open_reply(
                    t.channel, t.account_id, t.peer_id,
                    flatten=flattens_tables(self.session_id))
            except Exception as exc:  # noqa: BLE001 - reported at the end of the turn
                self._open_failure = _failure_of(exc)
                _log.warning("bound reply of %s not opened: %s", self.session_id,
                             self._open_failure.error_detail)
        return self._delivery

    # -- end of turn ----------------------------------------------------

    def finish(self, final_text: str, *, attached_files: Sequence[str] = ()) -> None:
        """The turn ended with ``final_text``: send what is still unsent.

        ``attached_files`` are the files the turn's ``send_file`` calls
        recorded; a marker naming any other file uploads nothing.
        """
        if self._done:
            return
        self._done = True
        if self._delivery is None and self._open_failure is None and not (final_text or "").strip():
            return
        self._end(lambda delivery: delivery.finish(final_text or "",
                                                   attached_files=attached_files,
                                                   streamed_only=self.resumed))

    def pause(self) -> None:
        """The turn stops at a safe point and resumes later: send all it
        streamed so far. The resumed attempt sends only what follows."""
        if self._done:
            return
        self._done = True
        if self._delivery is None and self._open_failure is None:
            return
        self._end(lambda delivery: delivery.finish("", streamed_only=True))

    def abort(self) -> None:
        """The turn failed: send the paragraphs already queued, nothing more."""
        if self._done:
            return
        self._done = True
        if self._delivery is None and self._open_failure is None:
            return
        self._end(lambda delivery: delivery.close())

    def _end(self, step: Callable[[Any], list]) -> None:
        """Run the last step of the delivery on its recipient's lane."""
        def _work() -> None:
            delivery = self._open()
            if delivery is None:
                failures: list = [self._open_failure] if self._open_failure else []
            else:
                try:
                    failures = step(delivery)
                except Exception as exc:  # noqa: BLE001 - reported, never raised into the turn
                    failures = [_failure_of(exc)]
            self._report(failures)
        t = self.target
        _LANES.submit((t.channel, t.account_id, t.peer_id), _work)

    def _report(self, failures: list) -> None:
        if not failures or self._failed:
            return
        self._failed = True
        first = failures[0]
        kind = str(getattr(first, "error_kind", "") or "unknown")
        detail = str(getattr(first, "error_detail", "") or "")[:300]
        t = self.target
        _log.warning("reply of %s not delivered to %s:%s peer %s: %s %s",
                     self.session_id, t.channel, t.account_id, t.peer_id, kind, detail)
        from openprogram.runtime.events import emit_ws_frame
        emit_ws_frame(failure_frame(
            session_id=self.session_id, assistant_msg_id=self.assistant_msg_id,
            channel=t.channel, account_id=t.account_id, error_kind=kind, error_detail=detail,
        ))


def failure_frame(*, session_id: str, assistant_msg_id: str, channel: str, account_id: str,
                  error_kind: str, error_detail: str = "") -> dict:
    """The ``channel_delivery_failed`` chat frame the App shows as a notice."""
    label = _CHANNEL_LABELS.get(channel, channel)
    return {"type": "chat_response", "data": {
        "type": FAILURE_FRAME,
        "session_id": session_id,
        "assistant_msg_id": assistant_msg_id,
        "channel": channel,
        "channel_label": label,
        "account_id": account_id,
        "error_kind": error_kind,
        "error_detail": error_detail,
        "content": f"The reply was not delivered to {label}: {error_kind}",
    }}


#: ``data.type`` of the chat frame that reports a reply not delivered.
FAILURE_FRAME = "channel_delivery_failed"


def is_failure_frame(frame: Any) -> bool:
    return (isinstance(frame, dict) and frame.get("type") == "chat_response"
            and (frame.get("data") or {}).get("type") == FAILURE_FRAME)


@dataclass(frozen=True)
class _Failure:
    error_kind: str
    error_detail: str = ""


def _failure_of(exc: Exception) -> _Failure:
    from openprogram.runtime import channel_link
    kind = "channels_unavailable" if isinstance(exc, channel_link.ChannelsUnavailable) else "unknown"
    return _Failure(kind, f"{type(exc).__name__}: {exc}")


def attach(req: Any, assistant_msg_id: str, *, resumed: bool = False) -> Optional[BoundReply]:
    """The reply delivery of this turn, or None when it sends nothing:
    the chat is unbound, its recipient is unknown, or the turn's source
    says another path sends it. ``resumed`` marks a continuation from a
    checkpoint (see ``delivered_here``). A background turn (an always-on
    check-in) and a turn receiving a handoff message are not replies of
    the chat and send nothing."""
    from openprogram.runtime.agent.turn_request import owns_conversation_effects
    from openprogram.runtime.permissions.identity import HANDOFF_MESSAGE
    if (not owns_conversation_effects(req)
            or getattr(req, "automation", None) == HANDOFF_MESSAGE):
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        session = default_db().get_session(req.session_id)
        channel = str((session or {}).get("channel") or "").strip().lower()
        if not channel or not delivered_here(getattr(req, "source", None), channel,
                                             resumed=resumed):
            return None
        target = reply_target(req.session_id, session)
    except Exception as exc:  # noqa: BLE001 - the turn runs; the miss is logged
        _log.warning("bound reply target of %s not read: %s: %s",
                     req.session_id, type(exc).__name__, exc)
        return None
    if target is None:
        return None
    return BoundReply(req, target, assistant_msg_id, resumed=resumed)


__all__ = [
    "FAILURE_FRAME", "BoundReply", "ReplyTarget", "attach", "delivered_here",
    "failure_frame", "is_failure_frame", "reply_target", "wait_for_deliveries",
]
