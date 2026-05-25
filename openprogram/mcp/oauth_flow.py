"""Browser-based OAuth 2.1 PKCE flow plumbing for remote MCP servers.

Two pieces glued to :class:`mcp.client.auth.OAuthClientProvider`:

  * :func:`open_browser_redirect` — opens the authorisation URL in the
    user's default browser. Hands control back immediately; the user
    completes consent in the browser.
  * :class:`LocalhostCallback` — an ephemeral HTTP server that captures
    the redirect, returning ``(code, state)`` to the SDK once received.

The SDK drives both via callbacks passed to its constructor. We never
do the OAuth dance ourselves — the SDK handles metadata discovery,
dynamic client registration, PKCE, token exchange, refresh, step-up.

Lifecycle: ``LocalhostCallback`` is built per OAuth attempt and torn
down as soon as the code arrives (or the context manager exits).
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional


_SUCCESS_BODY = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>OpenProgram MCP — authorised</title>"
    "<style>body{font-family:system-ui;padding:48px;max-width:560px;"
    "margin:auto;color:#1a1a1a}h1{font-size:20px}"
    "code{background:#f3f3f3;padding:2px 6px;border-radius:4px}</style>"
    "</head><body><h1>✓ Authorisation captured</h1>"
    "<p>You can close this tab and return to OpenProgram.</p>"
    "</body></html>"
)

_ERROR_BODY_FMT = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>OpenProgram MCP — authorisation failed</title>"
    "</head><body><h1>Authorisation failed</h1>"
    "<pre>{msg}</pre></body></html>"
)


async def open_browser_redirect(url: str) -> None:
    """Redirect handler passed to ``OAuthClientProvider``.

    Tries the system browser first; if that fails (headless box,
    missing $DISPLAY, etc.) prints the URL so a human can paste it
    elsewhere. Either way returns immediately — the callback server
    is what actually blocks waiting for the response.
    """
    try:
        opened = webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        opened = False
    if not opened:
        print(f"[mcp][oauth] open this URL in a browser to "
              f"authorise:\n  {url}", file=sys.stderr)


class LocalhostCallback:
    """Ephemeral HTTP server that captures the OAuth redirect.

    Usage::

        cb = LocalhostCallback(port=0)
        await cb.start()
        try:
            # Pass cb.redirect_uri to OAuthClientProvider via the
            # client_metadata.redirect_uris; pass cb.wait to its
            # callback_handler.
            ...
        finally:
            await cb.close()

    ``port=0`` picks a free ephemeral port; explicit ports are needed
    only when the MCP server pre-allowlists redirect URIs.
    """

    def __init__(self, *, port: int = 0,
                 host: str = "127.0.0.1",
                 timeout: float = 300.0) -> None:
        self._host = host
        self._requested_port = int(port)
        self._timeout = float(timeout)

        # Populated in start():
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._result_event = threading.Event()
        self._code: Optional[str] = None
        self._state: Optional[str] = None
        self._error: Optional[str] = None
        self._port: int = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def redirect_uri(self) -> str:
        return f"http://{self._host}:{self._port}/callback"

    async def start(self) -> None:
        # http.server.HTTPServer wants a sync socket. We bind ourselves
        # so port=0 still gives us the chosen port before the thread
        # runs, then hand the socket to HTTPServer.
        port = self._requested_port or _pick_free_port(self._host)
        handler = self._make_handler()
        self._server = HTTPServer((self._host, port), handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"mcp-oauth-callback:{self._port}",
            daemon=True,
        )
        self._thread.start()

    async def wait(self) -> tuple[str, Optional[str]]:
        """Block until the redirect arrives (or timeout).

        Returns ``(code, state)`` on success; raises on timeout or
        OAuth error response.
        """
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(
            None, self._result_event.wait, self._timeout,
        )
        if not ok:
            raise TimeoutError(
                f"OAuth callback not received within {self._timeout}s"
            )
        if self._error:
            raise RuntimeError(f"OAuth error: {self._error}")
        if not self._code:
            raise RuntimeError("OAuth callback missing 'code' parameter")
        return self._code, self._state

    async def close(self) -> None:
        srv = self._server
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _make_handler(self):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            # Silence the default stderr access-log spam.
            def log_message(self, fmt, *args):  # noqa: N802 — stdlib signature
                return

            def do_GET(self):  # noqa: N802 — stdlib signature
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path not in ("/callback", "/"):
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                err = qs.get("error", [None])[0]
                if err:
                    desc = qs.get("error_description", [""])[0]
                    msg = f"{err}: {desc}" if desc else err
                    outer._error = msg
                    self._send_html(400, _ERROR_BODY_FMT.format(msg=msg))
                else:
                    outer._code = qs.get("code", [None])[0]
                    outer._state = qs.get("state", [None])[0]
                    self._send_html(200, _SUCCESS_BODY)
                outer._result_event.set()

            def _send_html(self, status: int, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return _Handler


def _pick_free_port(host: str) -> int:
    """Reserve and immediately release a free TCP port. There is a
    tiny race window between release and HTTPServer's own bind, but
    the cost of a stale collision (OAuth flow fails, user retries) is
    much lower than the cost of a longer-held socket.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port
