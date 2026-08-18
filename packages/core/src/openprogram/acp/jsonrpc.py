"""JSON-RPC 2.0 over newline-delimited JSON on a byte stream.

ACP frames one JSON object per line (ndjson) — no Content-Length headers,
unlike LSP. This module is the transport half only: read lines, write
lines, match responses to the requests we sent. It knows nothing about ACP
methods; ``server.py`` owns those.

Peer requests are handled on worker threads so a long-running
``session/prompt`` never blocks the reader — which must stay free to
deliver ``session/cancel`` and permission answers while a turn runs.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, IO, Optional

_log = logging.getLogger(__name__)

# JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RPCError(Exception):
    """A handler failure that should become a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class Connection:
    """A JSON-RPC peer on a line-delimited stream.

    ``handler(method, params) -> result`` serves incoming requests; raise
    ``RPCError`` for a protocol error response. Notifications (no ``id``)
    take the same path and their return value is dropped.
    """

    def __init__(
        self,
        reader: IO[str],
        writer: IO[str],
        handler: Callable[[str, dict], Any],
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._handler = handler
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    # -- outgoing ---------------------------------------------------------

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False, default=str)
        with self._write_lock:
            self._writer.write(line + "\n")
            self._writer.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = 600.0) -> Any:
        """Call the client and block until it answers.

        Used for ``session/request_permission``, which runs on the turn's
        worker thread while the reader thread routes the response back.
        """
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            ev = threading.Event()
            slot: list = []
            self._pending[rid] = (ev, slot)
        self._send({"jsonrpc": "2.0", "id": rid,
                    "method": method, "params": params})
        got = ev.wait(timeout)
        with self._lock:
            self._pending.pop(rid, None)
        if not got:
            raise RPCError(INTERNAL_ERROR, f"{method} timed out")
        kind, payload = slot[0]
        if kind == "error":
            raise RPCError(int(payload.get("code", INTERNAL_ERROR)),
                           str(payload.get("message", "client error")))
        return payload

    # -- incoming ---------------------------------------------------------

    def _serve_one(self, msg: dict) -> None:
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            result = self._handler(str(method), params)
        except RPCError as exc:
            if rid is not None:
                err: dict[str, Any] = {"code": exc.code, "message": exc.message}
                if exc.data is not None:
                    err["data"] = exc.data
                self._send({"jsonrpc": "2.0", "id": rid, "error": err})
            return
        except Exception as exc:  # noqa: BLE001 - never kill the connection
            _log.exception("acp handler %s failed", method)
            if rid is not None:
                self._send({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": INTERNAL_ERROR,
                                      "message": str(exc)}})
            return
        if rid is not None:
            self._send({"jsonrpc": "2.0", "id": rid,
                        "result": result if result is not None else {}})

    def _dispatch(self, msg: dict) -> None:
        # A response to something we sent: hand it to the waiter.
        if "method" not in msg and "id" in msg:
            with self._lock:
                entry = self._pending.get(msg["id"])
            if entry is not None:
                ev, slot = entry
                if "error" in msg:
                    slot.append(("error", msg["error"]))
                else:
                    slot.append(("result", msg.get("result") or {}))
                ev.set()
            return
        # An incoming request or notification: serve it off-thread so the
        # reader keeps draining (cancel must arrive during a live prompt).
        t = threading.Thread(target=self._serve_one, args=(msg,), daemon=True)
        self._workers.append(t)
        t.start()

    def serve_forever(self) -> None:
        """Read until EOF. Returns when the client closes the stream."""
        for line in self._reader:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                self._send({"jsonrpc": "2.0", "id": None,
                            "error": {"code": PARSE_ERROR,
                                      "message": "invalid JSON"}})
                continue
            if not isinstance(msg, dict):
                continue
            self._dispatch(msg)
        # Let in-flight turns finish writing before the process exits.
        for t in list(self._workers):
            t.join(timeout=5.0)
