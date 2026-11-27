"""Talking to a running app's own local API: HTTP on loopback or on a Unix socket.

After yetone/magpie's agents that change a running app through the API its
own settings use (``alma.go``, ``kun.go``, ``nowledgemem.go``, ``hanako.go``,
``shelley.go``, ``avibe.go``; MIT License, Copyright (c) yetone). Each call
is one JSON request with a short timeout; an app that does not answer is
:class:`NotRunning`, which an agent reads as "nothing to read or keep
current", never as an error to show. An answer outside 2xx is
:class:`ApiError` with the app's own message.
"""
from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Optional
from urllib.parse import urlparse

TIMEOUT = 5.0


class NotRunning(Exception):
    """Nothing answers at the app's address: it is not running."""


class ApiError(Exception):
    """The app answered outside 2xx."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body  # the answer's JSON, when it was JSON


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def _message(status: int, raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300] or f"HTTP {status}"
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message") or json.dumps(value)
            if isinstance(value, str) and value:
                return value
    return text[:300] or f"HTTP {status}"


def request(method: str, url: str, body: Any = None, *, headers: Optional[dict[str, str]] = None,
            unix: str = "", timeout: float = TIMEOUT) -> Any:
    """``method url`` with ``body`` as JSON; the answer's JSON (``None`` for none).

    ``unix`` is a socket path the request goes to instead of ``url``'s host
    (only its path and query are used then)."""
    parsed = urlparse(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        if unix:
            conn: http.client.HTTPConnection = _UnixConnection(unix, timeout)
        elif parsed.scheme == "https":
            conn = http.client.HTTPSConnection(parsed.hostname or "127.0.0.1", parsed.port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port, timeout=timeout)
    except (OSError, ValueError) as exc:
        raise NotRunning(str(exc)) from exc
    sent = {"Accept": "application/json", **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json"
    try:
        conn.request(method, target, body=payload, headers=sent)
        response = conn.getresponse()
        raw = response.read()
    except (ConnectionError, socket.timeout, FileNotFoundError) as exc:
        raise NotRunning(str(exc)) from exc
    except OSError as exc:
        raise NotRunning(str(exc)) from exc
    finally:
        conn.close()
    if not 200 <= response.status < 300:
        raise ApiError(response.status, _message(response.status, raw), _json(raw))
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ApiError(response.status, f"the answer is not JSON: {exc}") from exc
