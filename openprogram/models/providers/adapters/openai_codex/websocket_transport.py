"""A Codex turn over the ChatGPT backend's WebSocket, sent as a delta.

The ChatGPT backend takes a streamed Responses turn over a WebSocket as
well as over HTTP, the way Codex's own client sends it (codex-rs
``WebsocketSession``): the handshake carries the turn's headers with
``OpenAI-Beta: responses_websockets=2026-02-06``, the message is the HTTP
body with ``"type": "response.create"`` ahead of it, and every event comes
back as one text frame, the same JSON the HTTP stream carries.

Two things make it worth having:

* **Delta turns.** A conversation's socket is kept between its turns. A turn
  that carries on from the last one (every field but ``input`` as the last
  turn sent it, and its input starting with the last turn's input and then
  that turn's output items) goes as only its new input after
  ``previous_response_id``. With ``store: false`` the backend holds a
  response only for the socket it was made on, so the id is only ever named
  on that socket. Anything else goes whole on the kept socket.
* **Long turns.** On some networks a streamed HTTP reply from chatgpt.com is
  cut at about 120 s; the same turn over the WebSocket runs to its end.

Anything the socket cannot do falls back to HTTP, before any of the reply
is handed on (:class:`SocketUnavailable`): a refused handshake (that host
is then spoken HTTP for :data:`SHUN_S`), a connection limit, a socket that
ends or will not take the message, and a message as big as one the host
closed 1009 (message too big) on. A kept socket that fails before the reply
starts (closed while kept, ``previous_response_not_found``) is dropped and
the turn goes whole on a new socket.

Sockets are per event loop: a socket belongs to the loop that opened it.
The design follows magpie's ``codex_socket*.go`` (yetone/magpie, MIT).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from openprogram.models.providers.utils.http_client import register_loop_closer
from openprogram.models.providers.utils.stream_retry import ProviderStreamError

_log = logging.getLogger(__name__)

BETA = "responses_websockets=2026-02-06"
# The handshake's bound, as Codex's own websocket_connect_timeout.
DIAL_TIMEOUT_S = 30.0
# How long a kept socket waits for its conversation's next turn.
KEEP_IDLE_S = 10 * 60.0
# How long a socket is used from its handshake (the backend ends one at 60 minutes).
KEEP_AGE_S = 55 * 60.0
# Sockets kept at most per event loop; the least recently used goes first.
KEEP_MAX = 32
# How long a host that refused the socket is spoken HTTP before it is tried again.
SHUN_S = 10 * 60.0
# Frames read ahead of the reply proper, to tell an error before anything is handed on.
PRELUDE_FRAMES = 8
# Frames that are the transport's own and never part of the reply.
# ``codex.rate_limits`` carries the plan windows; it goes to the turn's
# ``on_frame`` (quota capture) and not into the reply.
_TRANSPORT_FRAMES = frozenset({
    "codex.rate_limits",
    "codex.response.metadata",
    "responsesapi.websocket_timing",
})
_TERMINAL = frozenset({"response.completed", "response.incomplete", "response.failed", "response.done"})
# Item fields the backend writes that a client does not send back.
_NOT_COMPARED = frozenset({"id", "status", "phase"})
# Handshake headers that belong to the HTTP request only.
_HTTP_ONLY_HEADERS = frozenset({
    "content-type", "content-length", "content-encoding", "accept",
    "accept-encoding", "host", "openai-beta",
})


class SocketUnavailable(Exception):
    """The turn goes over HTTP: nothing of it has reached the caller."""


class _Again(Exception):
    """A kept socket failed before the reply began: try a new socket."""


def enabled() -> bool:
    return os.environ.get("OPENPROGRAM_CODEX_WEBSOCKET", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def socket_url(base_url: str) -> str:
    """The WebSocket URL of a Codex base URL's turn endpoint."""
    url = f"{base_url.rstrip('/')}/codex/responses"
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    return url


# ---------------------------------------------------------------------------
# Host memory: refused handshakes and message-size caps
# ---------------------------------------------------------------------------

_shunned: dict[str, float] = {}
_too_big: dict[str, tuple[int, float]] = {}


def _is_shunned(host: str) -> bool:
    until = _shunned.get(host)
    if until is None:
        return False
    if time.monotonic() >= until:
        _shunned.pop(host, None)
        return False
    return True


def _shun(host: str, why: str) -> None:
    if host not in _shunned:
        _log.info("codex: %s refused the WebSocket (%s); its turns go over HTTP for %ds", host, why, SHUN_S)
    _shunned[host] = time.monotonic() + SHUN_S


def _message_too_big(host: str, size: int) -> bool:
    entry = _too_big.get(host)
    if entry is None:
        return False
    if time.monotonic() >= entry[1]:
        _too_big.pop(host, None)
        return False
    return size >= entry[0]


def _refused_too_big(host: str, size: int) -> None:
    previous = _too_big.get(host)
    smallest = size if previous is None else min(size, previous[0])
    if previous is None:
        _log.info("codex: %s closed the WebSocket on a %d-byte turn as too big; turns that big go over HTTP", host, size)
    _too_big[host] = (smallest, time.monotonic() + SHUN_S)


# ---------------------------------------------------------------------------
# Turn shape: what of a turn the next one is compared with
# ---------------------------------------------------------------------------

def _same_json(text: str) -> Any:
    """A tool call's ``arguments`` as the JSON it says, however it is spaced."""
    try:
        return {"json": json.loads(text)}
    except (TypeError, ValueError):
        return text


def _bare(value: Any) -> Any:
    """``value`` without nulls, empty lists, annotations or logprobs at any depth.

    A tool call's ``arguments`` compare as the JSON they hold: the backend
    writes them compact, a client re-serializes them with its own spacing.
    """
    if isinstance(value, dict):
        return {
            key: _same_json(item) if key == "arguments" and isinstance(item, str) else _bare(item)
            for key, item in value.items()
            if key not in ("annotations", "logprobs")
            and item is not None
            and not (isinstance(item, list) and not item)
        }
    if isinstance(value, list):
        return [_bare(item) for item in value]
    return value


def item_digest(item: Any) -> str | None:
    """An input or output item's digest as the backend and the client both have it.

    The item's own ``id`` and ``status`` are left out (a client sends items
    back without the ids the backend gave), and so is anything empty, and a
    message's ``phase``, which the backend writes and a client need not keep:
    the continued turn reads the backend's own copy of the item.
    """
    if not isinstance(item, dict):
        return None
    bare = _bare({key: value for key, value in item.items() if key not in _NOT_COMPARED})
    raw = json.dumps(bare, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TurnShape:
    fields: dict[str, str]
    input: list[Any]
    digests: list[str]

    @classmethod
    def of(cls, body: dict[str, Any]) -> "TurnShape | None":
        """The body's shape; ``None`` for one that cannot be continued from."""
        if "previous_response_id" in body or not isinstance(body.get("input"), list):
            return None
        digests = [item_digest(item) for item in body["input"]]
        if any(digest is None for digest in digests):
            return None
        fields = {
            key: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            for key, value in body.items()
            if key not in ("input", "client_metadata", "stream_options")
        }
        return cls(fields=fields, input=list(body["input"]), digests=digests)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Kept sockets
# ---------------------------------------------------------------------------

@dataclass
class _Kept:
    connection: Any
    key: str
    host: str
    opened: float
    used: float = 0.0
    fields: dict[str, str] | None = None
    input: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    reply_id: str = ""

    def delta(self, body: dict[str, Any], shape: TurnShape) -> dict[str, Any] | None:
        """``body`` as its new input after ``previous_response_id``, when it carries on."""
        if not self.reply_id or self.fields is None or shape.fields != self.fields:
            return None
        carried = self.input + self.output
        if len(shape.digests) <= len(carried) or shape.digests[: len(carried)] != carried:
            return None
        return {
            "previous_response_id": self.reply_id,
            **{key: value for key, value in body.items() if key != "input"},
            "input": shape.input[len(carried):],
        }

    async def close(self) -> None:
        try:
            await self.connection.close()
        except Exception:
            _log.debug("closing a Codex WebSocket failed", exc_info=True)


_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, _Kept]]" = weakref.WeakKeyDictionary()


def _pool() -> dict[str, _Kept]:
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        pool = _pools[loop] = {}
    return pool


#: Closes started without waiting for them, per loop; :func:`drain` awaits
#: what is left of them before the loop closes.
_closing: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Task]]" = weakref.WeakKeyDictionary()


def _close_later(kept: _Kept) -> None:
    """Close ``kept`` without making the caller wait for the round trip."""
    loop = asyncio.get_running_loop()
    task = loop.create_task(kept.close())
    tasks = _closing.setdefault(loop, set())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def keep_key(url: str, headers: dict[str, str], body: dict[str, Any]) -> str:
    """The conversation's key among kept sockets; ``""`` for a turn that names none.

    It covers the sign-in, so another account's turn never finds the socket.
    """
    conversation = body.get("prompt_cache_key") or ""
    if not conversation:
        return ""
    lowered = {name.lower(): value for name, value in headers.items()}
    parts = [url, lowered.get("authorization", ""), lowered.get("chatgpt-account-id", ""), str(conversation)]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _take(key: str) -> _Kept | None:
    if not key:
        return None
    kept = _pool().pop(key, None)
    if kept is None:
        return None
    now = time.monotonic()
    if now - kept.opened >= KEEP_AGE_S or now - kept.used >= KEEP_IDLE_S:
        _close_later(kept)
        return None
    return kept


def _put(kept: _Kept) -> None:
    now = time.monotonic()
    if not kept.key or now - kept.opened >= KEEP_AGE_S:
        _close_later(kept)
        return
    kept.used = now
    pool = _pool()
    previous = pool.pop(kept.key, None)
    if previous is not None and previous is not kept:
        _close_later(previous)
    pool[kept.key] = kept
    for stale_key in [k for k, v in pool.items() if now - v.used >= KEEP_IDLE_S or now - v.opened >= KEEP_AGE_S]:
        _close_later(pool.pop(stale_key))
    while len(pool) > KEEP_MAX:
        oldest = min(pool, key=lambda k: pool[k].used)
        _close_later(pool.pop(oldest))


async def drain() -> None:
    """Close every socket kept on the running loop, and finish the closes
    already under way."""
    pool = _pool()
    kept = list(pool.values())
    pool.clear()
    closing = list(_closing.pop(asyncio.get_running_loop(), ()))
    await asyncio.gather(*(k.close() for k in kept), *closing, return_exceptions=True)


# Kept sockets close with the loop's shared HTTP clients.
register_loop_closer(drain)


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------

def _close_code(exc: BaseException) -> int | None:
    received = getattr(exc, "rcvd", None)
    return getattr(received, "code", None)


def _error_status(frame: dict[str, Any]) -> int:
    for key in ("status", "status_code"):
        value = frame.get(key)
        if isinstance(value, int):
            return value
    return 0


class SocketTurn:
    """A turn whose reply has begun on a socket: its events, as the HTTP stream's."""

    def __init__(self, kept: _Kept, shape: TurnShape | None, prelude: list[dict[str, Any]], size: int,
                 on_frame: Any = None) -> None:
        self._kept = kept
        self._on_frame = on_frame
        self._shape = shape
        self._prelude = prelude
        self._size = size
        self._output: list[str] = []
        self._finished = False

    def _note(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "response.output_item.done" and self._shape is not None:
            digest = item_digest(frame.get("item"))
            if digest is None:
                self._shape = None
            else:
                self._output.append(digest)
        elif kind == "response.completed":
            self._finished = True
            reply_id = (frame.get("response") or {}).get("id") or ""
            if reply_id and self._shape is not None:
                self._kept.fields = self._shape.fields
                self._kept.input = self._shape.digests
                self._kept.output = self._output
                self._kept.reply_id = reply_id
                _put(self._kept)
            else:
                _close_later(self._kept)
        elif kind in _TERMINAL or kind == "error":
            self._finished = True
            _close_later(self._kept)

    async def events(self, receive_next) -> AsyncIterator[dict[str, Any]]:
        """The reply's events; ``receive_next`` reads one frame under the caller's budgets.

        A socket left before its turn's end is closed, never kept.
        """
        try:
            for frame in self._prelude:
                self._note(frame)
                yield frame
            while not self._finished:
                try:
                    raw = await receive_next(self._kept.connection)
                except Exception as exc:
                    if type(exc).__name__.startswith("ConnectionClosed"):
                        raise ProviderStreamError(
                            "Codex WebSocket ended before a terminal response event",
                            retryable=True,
                        ) from exc
                    raise
                frame = _parse(raw)
                if frame is None or frame.get("type") in _TRANSPORT_FRAMES:
                    _hand_on(frame, self._on_frame)
                    continue
                self._note(frame)
                yield frame
        finally:
            if not self._finished:
                self._finished = True
                await self._kept.close()


def _hand_on(frame: dict[str, Any] | None, on_frame: Any) -> None:
    """Give a transport frame to the turn's observer; it never fails the turn."""
    if frame is None or on_frame is None:
        return
    try:
        on_frame(frame)
    except Exception:
        _log.debug("codex: transport frame observer failed", exc_info=True)


def _parse(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return frame if isinstance(frame, dict) else None


_connection_class: Any = None


def _connection() -> Any:
    """The client connection that ends its TCP connection once its close is answered.

    A plain websockets client that closes waits, after the backend has
    answered its close frame, for the backend to drop the TCP connection
    (up to ``close_timeout``); through chatgpt.com that takes about two
    seconds, and a turn's loop closing its kept socket waited for it. Once
    both close frames have crossed, the WebSocket is closed and nothing more
    can arrive on it, so this end drops the connection itself (RFC 6455
    7.1.1 lets a client do so). A close the backend started is answered and
    left to the backend to end, as before.

    Through an HTTP proxy websockets hands the tunnel to this connection and
    starts TLS inside it before it calls ``connection_made``; asyncio's TLS
    layer counts the connection as made from the start, so a tunnel that
    ends during that handshake (the far end refuses) calls
    ``connection_lost`` on a connection that never began. The dial has
    already failed with that error and nothing was set up, so there is
    nothing to end.
    """
    global _connection_class
    if _connection_class is None:
        from websockets.asyncio.client import ClientConnection

        class _Connection(ClientConnection):
            def connection_lost(self, exc: Exception | None) -> None:
                if not hasattr(self, "recv_messages"):
                    return
                super().connection_lost(exc)

            def data_received(self, data: bytes) -> None:
                super().data_received(data)
                protocol = self.protocol
                if (
                    protocol.close_sent is not None
                    and protocol.close_rcvd is not None
                    and protocol.close_rcvd_then_sent is False
                    and not self.transport.is_closing()
                ):
                    self.transport.abort()

        _connection_class = _Connection
    return _connection_class


def _install_proxy_protocol() -> None:
    """Make a given-up CONNECT through an HTTP proxy end cleanly.

    websockets 16 waits for the proxy's answer on a future and closes the
    proxy connection only when that wait raises an ``Exception``. A dial
    cancelled while it waits (the turn stopped, the open timeout) cancels
    the future and leaves the connection open, so the proxy's late
    ``200 Connection established`` sets a result on the cancelled future and
    asyncio logs ``InvalidStateError`` from ``data_received`` and again from
    ``connection_lost``. This protocol closes the connection as soon as
    nothing waits for it and leaves a settled future alone; a proxy that
    refuses, times out or drops the connection while the dial waits still
    fails the dial, which then goes over HTTP. The answer's stream is ended
    once: a proxy that closes before answering is seen ending by
    ``eof_received`` and again by ``connection_lost``.
    """
    from websockets.asyncio import client

    if getattr(client.HTTPProxyConnection, "_settles_once", False):
        return

    class _ProxyConnection(client.HTTPProxyConnection):
        _settles_once = True

        def connection_made(self, transport: Any) -> None:
            super().connection_made(transport)
            self.response.add_done_callback(self._given_up)

        def _given_up(self, response: asyncio.Future) -> None:
            if response.cancelled() and not self.transport.is_closing():
                self.transport.close()

        def run_parser(self) -> None:
            if not self.response.done():
                super().run_parser()

        def eof_received(self) -> None:
            if not self.reader.eof:
                self.reader.feed_eof()
            self.run_parser()

        def connection_lost(self, exc: Exception | None) -> None:
            if not self.reader.eof:
                self.reader.feed_eof()
            if exc is not None and not self.response.done():
                self.response.set_exception(exc)

    client.HTTPProxyConnection = _ProxyConnection


async def _dial(url: str, headers: dict[str, str], key: str, host: str) -> _Kept:
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    from openprogram.models.providers.utils.http_proxy import proxy_for_url

    _install_proxy_protocol()
    handshake = {
        name: value for name, value in headers.items()
        if name.lower() not in _HTTP_ONLY_HEADERS
    }
    handshake["OpenAI-Beta"] = BETA
    try:
        connection = await connect(
            url,
            additional_headers=handshake,
            proxy=proxy_for_url(url),
            open_timeout=DIAL_TIMEOUT_S,
            max_size=None,
            # The backend's own pings keep the socket; ours would time out
            # a socket parked in the pool between turns.
            ping_interval=None,
            user_agent_header=None,
            create_connection=_connection(),
        )
    except InvalidStatus as exc:
        status = exc.response.status_code
        # An account's or a passing fault is told by HTTP as it is.
        if status not in (401, 429) and status < 500:
            _shun(host, f"HTTP {status}")
        raise SocketUnavailable(f"handshake answered {status}") from exc
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _shun(host, type(exc).__name__)
        raise SocketUnavailable(str(exc)) from exc
    now = time.monotonic()
    return _Kept(connection=connection, key=key, host=host, opened=now, used=now)


async def _begin(
    kept: _Kept,
    message: str,
    shape: TurnShape | None,
    *,
    reused: bool,
    receive_next,
    on_frame: Any = None,
) -> SocketTurn:
    """Send the message and read the frames ahead of the reply proper."""
    def unavailable(exc: BaseException | None, why: str) -> Exception:
        if exc is not None and _close_code(exc) == 1009:
            _refused_too_big(kept.host, len(message))
        return _Again(why) if reused else SocketUnavailable(why)

    try:
        await kept.connection.send(message)
    except asyncio.CancelledError:
        await kept.close()
        raise
    except Exception as exc:
        await kept.close()
        raise unavailable(exc, "the socket would not take the turn") from exc

    prelude: list[dict[str, Any]] = []
    for _ in range(PRELUDE_FRAMES):
        try:
            raw = await receive_next(kept.connection)
        except asyncio.CancelledError:
            await kept.close()
            raise
        except ProviderStreamError:
            await kept.close()
            raise
        except Exception as exc:
            await kept.close()
            raise unavailable(exc, "the socket ended before the reply") from exc
        frame = _parse(raw)
        if frame is None or frame.get("type") in _TRANSPORT_FRAMES:
            _hand_on(frame, on_frame)
            continue
        if frame.get("type") == "error":
            await kept.close()
            error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
            if reused:
                # previous_response_not_found, the connection limit, any fault:
                # a new socket answers for itself.
                raise _Again(str(error.get("code") or "error frame"))
            if error.get("code") == "websocket_connection_limit_reached":
                raise SocketUnavailable("connection limit reached")
            status = _error_status(frame)
            text = json.dumps(error or frame, ensure_ascii=False)
            if 400 <= status <= 599:
                from openprogram.models.providers.utils.stream_retry import is_retryable_status

                raise ProviderStreamError(
                    f"HTTP {status}: {text}",
                    http_status=status,
                    error_text=text,
                    retryable=is_retryable_status(status, text),
                )
        prelude.append(frame)
        break
    return SocketTurn(kept, shape, prelude, len(message), on_frame)


async def open_turn(
    *,
    base_url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    receive_next,
    on_frame: Any = None,
) -> SocketTurn:
    """Begin a streamed turn on a socket; :class:`SocketUnavailable` means use HTTP.

    ``receive_next(connection)`` reads one frame, applying the caller's idle,
    stall and cancel budgets. ``on_frame(frame)`` is given each transport
    frame (``codex.rate_limits`` and the like) that is not part of the reply.
    """
    url = socket_url(base_url)
    host = urlsplit(url).netloc
    if not enabled() or not body.get("stream") or _is_shunned(host):
        raise SocketUnavailable("WebSocket not in use for this turn")

    key = keep_key(url, headers, body)
    shape = TurnShape.of(body) if key else None
    if shape is None:
        key = ""
    whole = json.dumps({"type": "response.create", **body}, ensure_ascii=False)

    kept = _take(key)
    if kept is not None:
        delta = kept.delta(body, shape) if shape is not None else None
        message = (
            json.dumps({"type": "response.create", **delta}, ensure_ascii=False)
            if delta is not None
            else whole
        )
        _log.debug(
            "codex: turn on the kept socket %s (%d bytes)",
            f"as a delta of {len(delta['input'])} item(s)" if delta is not None else "whole",
            len(message),
        )
        if _message_too_big(host, len(message)):
            await kept.close()
        else:
            try:
                return await _begin(kept, message, shape, reused=True, receive_next=receive_next,
                                    on_frame=on_frame)
            except _Again as exc:
                _log.debug("codex: kept socket failed before the reply (%s); opening a new one", exc)

    if _message_too_big(host, len(whole)):
        raise SocketUnavailable("turn as big as one the host refused")
    fresh = await _dial(url, headers, key, host)
    return await _begin(fresh, whole, shape, reused=False, receive_next=receive_next, on_frame=on_frame)
