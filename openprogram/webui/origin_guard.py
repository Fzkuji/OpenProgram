"""Browser-origin guard for the local web surface.

The API and the ``/ws`` endpoint are unauthenticated: binding to loopback is
the only thing separating them from the network. Loopback is not a trust
boundary against a *browser*, though. Two attacks reach a localhost server
from a page the user merely visits:

* **Cross-site WebSocket hijacking.** The same-origin policy does not apply
  to WebSockets. Any page can ``new WebSocket("ws://127.0.0.1:18100/ws")``
  and drive the agent — which owns a ``bash`` tool.
* **DNS rebinding.** ``evil.example`` re-resolves to ``127.0.0.1`` after the
  page loads, so the page's own origin *becomes* the local server and every
  same-origin check passes. That reads plaintext provider keys off
  ``/api/providers/{provider}/accounts/{name}/reveal``.

Both are closed at the ASGI layer, before routing, for ``http`` and
``websocket`` alike. The rules follow the MCP Streamable HTTP spec ("servers
MUST validate the ``Origin`` header on all incoming connections to prevent
DNS rebinding attacks"), openclaw's ``src/gateway/origin-check.ts``, and
opencode's ``packages/server/src/cors.ts``:

1. ``Host`` must name a loopback address while the server is bound to
   loopback. Blocks rebinding, whose whole trick is a foreign ``Host``.
2. ``Sec-Fetch-Site: cross-site`` is refused. The browser sets this header
   itself, so page JavaScript cannot forge it.
3. ``Origin``, when present, must match ``Host`` or name a loopback address.
4. A request with no ``Origin`` is not a browser request — the terminal UI
   (npm ``ws``), ``curl`` and the Python clients never send one — and passes.

Starlette's ``TrustedHostMiddleware`` covers rule 1 alone, but its host
parsing is ``host.split(":")[0]``, which turns ``[::1]:18100`` into ``[``.
The IPv6 literal is a real address for this server, so the check lives here
where it is parsed properly.
"""
from __future__ import annotations

import ipaddress
import json
from typing import Iterable, Optional
from urllib.parse import urlsplit

# Names that resolve to a loopback address by convention rather than by
# parsing as one.
_LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


def hostname_of(host_header: str) -> str:
    """The bare hostname in a ``Host`` header value, port and brackets gone.

    ``127.0.0.1:18100`` → ``127.0.0.1``; ``[::1]:18100`` → ``::1``;
    ``::1`` (unbracketed, therefore portless) → ``::1``.
    """
    host = (host_header or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[1:end]
        return ""
    # An unbracketed value with several colons can only be a portless IPv6
    # literal; splitting it on ":" would mangle it.
    if host.count(":") > 1:
        return host
    return host.split(":")[0]


def is_loopback_hostname(name: str) -> bool:
    name = (name or "").strip().lower()
    if not name:
        return False
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _origin_host(origin: str) -> Optional[str]:
    """``http://127.0.0.1:18100`` → ``127.0.0.1:18100``. None if unusable."""
    value = (origin or "").strip()
    if not value or value == "null":
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if not parts.netloc:
        return None
    return parts.netloc.lower()


def deny_reason(
    *,
    host: str,
    origin: str,
    sec_fetch_site: str,
    allowed_origins: Iterable[str] = (),
    enforce_loopback_host: bool = True,
) -> Optional[str]:
    """``None`` to serve the request, else a short reason to refuse it."""
    if enforce_loopback_host:
        # An empty Host is HTTP/1.0 or a raw socket client, never a browser.
        name = hostname_of(host)
        if name and not is_loopback_hostname(name):
            return "host_not_loopback"

    raw_origin = (origin or "").strip().lower().rstrip("/")
    allowed = {o.strip().lower().rstrip("/") for o in allowed_origins if o and o.strip()}
    if raw_origin and raw_origin in allowed:
        return None

    if (sec_fetch_site or "").strip().lower() == "cross-site":
        return "cross_site_request"

    if not raw_origin:
        # No Origin at all: not a browser request. The terminal UI, curl
        # and the Python clients land in this branch.
        return None
    if raw_origin == "null":
        # A sandboxed iframe on any site sends this, and it names no host
        # to check — so it is refused, as it is in openclaw and opencode.
        return "origin_opaque"

    origin_host = _origin_host(origin)
    if origin_host is None:
        return "origin_unparseable"
    if origin_host == (host or "").strip().lower():
        return None
    if is_loopback_hostname(hostname_of(origin_host)):
        return None
    return "origin_not_allowed"


class BrowserOriginGuard:
    """ASGI middleware applying :func:`deny_reason` to http and websocket."""

    def __init__(
        self,
        app,
        *,
        allowed_origins: Iterable[str] = (),
        enforce_loopback_host: bool = True,
    ) -> None:
        self.app = app
        self.allowed_origins = tuple(allowed_origins)
        self.enforce_loopback_host = enforce_loopback_host

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        reason = deny_reason(
            host=headers.get("host", ""),
            origin=headers.get("origin", ""),
            sec_fetch_site=headers.get("sec-fetch-site", ""),
            allowed_origins=self.allowed_origins,
            enforce_loopback_host=self.enforce_loopback_host,
        )
        if reason is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Refusing the handshake: close instead of accepting. Sent
            # before any accept, uvicorn turns this into an HTTP 403.
            await receive()          # consume websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return

        body = json.dumps({"error": reason}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
