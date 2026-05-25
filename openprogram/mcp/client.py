"""Single MCP server client — stdio / streamable-http / sse transports.

Owns one ``ClientSession`` per server, held open by a supervisor
``asyncio.Task`` for the worker's lifetime. ``start()`` blocks until
``initialize`` + ``list_tools`` complete or the supervisor fails;
``call_tool()`` reuses the live session; ``stop()`` flags the
supervisor to exit, releasing the underlying transport.

Why a supervisor task instead of opening ``async with`` per call:
the official Python SDK exposes the session as a nested async-context-
manager (``async with stdio_client(...) as (r, w): async with
ClientSession(r, w) as session: ...``), and Python doesn't let us
"hold" a context manager across function calls without keeping its
``__aexit__`` pending — so the simplest cross-call-lifetime model is
to wrap the whole nested block inside one long-lived coroutine.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import threading
from typing import Any, Optional

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from .config import AUTH_BEARER, AUTH_OAUTH, HTTP, LOCAL, SSE, MCPServerConfig


# Recognise errors that indicate "the session/transport died but is
# probably recoverable by reconnecting" — claude-code calls this
# ``isMcpSessionExpiredError`` and uses the same signal to bounce.
#
# Conservative on purpose: false positives mean an extra reconnect
# attempt (cheap); false negatives mean a stuck-broken session that
# stays broken until the user notices.
_SESSION_EXPIRED_HINTS = (
    "session expired",
    "session not found",
    "Bad Request",          # streamable_http on a closed session_id
    "ClosedResourceError",  # anyio: peer closed the channel
    "EndOfStream",          # anyio: server hung up
    "ConnectError",         # httpx: TCP / TLS dropped
    "RemoteProtocolError",  # httpx: half-closed
    "PoolTimeout",          # httpx: queue stalled
    "401",                  # bearer / cookie auth expired
)


def _is_session_expired(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(h.lower() in text for h in _SESSION_EXPIRED_HINTS)


# -- list_roots_callback shared across every session ----------------
#
# Called by the SDK whenever a server sends ``roots/list``. Reads
# host configuration fresh each time (load_roots is cheap — a small
# JSON file). Returning ``ErrorData`` would tell the server "this
# host doesn't support roots"; we return an empty list when no roots
# are configured because the host CAN support the capability, it
# just has no allowed paths to share yet.

# -- logging notifications ------------------------------------------
#
# Server-side ``notifications/message`` lines flow into the worker
# log + (when present) a process-wide subscriber list. The webui can
# tail logs via the existing event stream; here we just normalise the
# wire payload (level / logger / data) into one human-readable line
# per notification.

# Process-wide subscribers — webui SSE / WebSocket handlers register
# a sync callback here; we invoke each on every new log line. Kept
# untyped (a callable) to dodge a circular import on the webui types.
_log_subscribers: list = []
_log_subscribers_lock = threading.Lock()


def add_log_subscriber(fn) -> None:  # noqa: ANN001 — fn(server, level, logger, text)
    """Register a synchronous subscriber to MCP server log lines.

    Called with ``(server: str, level: str, logger: Optional[str],
    text: str, raw_data: Any)`` whenever any connected server emits a
    ``notifications/message``. Subscribers should be cheap and
    non-raising — exceptions are caught and dropped so a misbehaving
    handler can't take down the MCP supervisor.
    """
    with _log_subscribers_lock:
        _log_subscribers.append(fn)


def remove_log_subscriber(fn) -> None:  # noqa: ANN001
    with _log_subscribers_lock:
        try:
            _log_subscribers.remove(fn)
        except ValueError:
            pass


# Recent-history ring buffer so a webui that connects late still sees
# something useful. 200 lines × N servers ≈ a few KB; small.
_log_history: list[dict] = []
_log_history_max = 200


def get_log_history() -> list[dict]:
    """Snapshot of recent log entries across all servers."""
    return list(_log_history)


async def _handle_log_notification(server: str, params) -> None:  # noqa: ANN001
    level = getattr(params, "level", "info") or "info"
    logger_name = getattr(params, "logger", None)
    data = getattr(params, "data", None)
    if isinstance(data, (dict, list)):
        try:
            import json as _json
            text = _json.dumps(data, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = repr(data)
    else:
        text = str(data) if data is not None else ""

    # Worker log — short header so grepping by server / level works.
    prefix = f"[mcp][{server}][{level}]"
    if logger_name:
        prefix += f"[{logger_name}]"
    print(f"{prefix} {text}", file=sys.stderr, flush=True)

    entry = {
        "server": server,
        "level": level,
        "logger": logger_name,
        "text": text,
        "data": data,
        "ts": _now_ts(),
    }
    _log_history.append(entry)
    if len(_log_history) > _log_history_max:
        del _log_history[: len(_log_history) - _log_history_max]

    # Fan out to live subscribers (webui streams).
    with _log_subscribers_lock:
        subs = list(_log_subscribers)
    for fn in subs:
        try:
            fn(server, level, logger_name, text, data)
        except Exception:  # noqa: BLE001
            pass


def _now_ts() -> float:
    import time as _t
    return _t.time()


async def _list_roots_callback(context):  # noqa: ANN001 — SDK type
    from mcp import types as _mcp_types
    from .config import load_roots
    entries = load_roots()
    return _mcp_types.ListRootsResult(
        roots=[
            _mcp_types.Root(uri=e["uri"], name=e.get("name"))
            for e in entries
        ],
    )


class MCPClient:
    """Holds one MCP server connection for the worker's lifetime."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.tools: list[Tool] = []
        self.error: Optional[str] = None
        # Coarse classifier for the UI / management API:
        #   None             — healthy or never connected
        #   "transient"      — connection / session dropped; supervisor
        #                      is backing off + auto-reconnecting
        #   "needs_reauth"   — OAuth refresh_token rejected; supervisor
        #                      has stopped, user must clear tokens and
        #                      restart the server (the management UI
        #                      surfaces a Re-authenticate button)
        #   "fatal"          — non-recoverable startup failure (bad
        #                      command, unreachable URL, etc.)
        self.error_kind: Optional[str] = None
        self._session: Optional[ClientSession] = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        # Fires when call_tool / etc. notice the session is stale —
        # supervisor sees it, drops the current session block, and the
        # outer retry loop reconnects on the next iteration.
        self._reconnect_signal = asyncio.Event()
        self._supervisor_task: Optional[asyncio.Task] = None
        # Serialise tool calls per server. The MCP SDK's ClientSession
        # is single-flight by default; concurrent call_tool on the same
        # session would interleave JSON-RPC requests. Cheap to hold a
        # lock when most servers will see one call at a time anyway.
        self._call_lock = asyncio.Lock()

    @property
    def is_ready(self) -> bool:
        return self._session is not None and self.error is None

    async def start(self) -> None:
        """Spawn the subprocess (or open the HTTP session), initialize,
        fetch tool list.

        Returns when ``self._ready`` fires — supervisor sets it both on
        successful initialize+list_tools and on any startup failure.
        Caller should check ``self.error`` after start returns.
        """
        if self._supervisor_task is not None:
            return
        self._supervisor_task = asyncio.create_task(
            self._supervisor(),
            name=f"mcp-supervisor:{self.config.name}",
        )
        try:
            # Bound the wait so a hung subprocess / unresponsive server
            # doesn't pin worker startup.
            await asyncio.wait_for(self._ready.wait(),
                                    timeout=self.config.timeout_seconds)
        except asyncio.TimeoutError:
            self.error = (
                f"server did not become ready within "
                f"{self.config.timeout_seconds}s"
            )
            self._shutdown.set()
            return

    async def stop(self) -> None:
        """Trigger shutdown and wait for the supervisor to clean up."""
        self._shutdown.set()
        if self._supervisor_task is not None:
            try:
                await asyncio.wait_for(self._supervisor_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._supervisor_task.cancel()

    # -- resources / prompts (MCP protocol primitives besides tools) --

    async def list_resources(self) -> list[dict]:
        """Server's resources/list response, flattened to dict list.

        Returns ``[]`` (not error) for servers that don't expose any
        resources, so the listing tool can join across N servers
        without each having to support resources.
        """
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        try:
            result = await self._session.list_resources()
        except Exception:  # noqa: BLE001 — servers without capability raise
            return []
        return [r.model_dump(mode="json", exclude_none=True)
                for r in result.resources]

    async def read_resource(self, uri: str) -> list[dict]:
        """Server's resources/read response — list of content blocks."""
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        from pydantic import AnyUrl
        result = await self._session.read_resource(AnyUrl(uri))
        return [c.model_dump(mode="json", exclude_none=True)
                for c in result.contents]

    async def list_prompts(self) -> list[dict]:
        """Server's prompts/list response, flattened to dict list.

        Returns ``[]`` for servers without prompt support.
        """
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        try:
            result = await self._session.list_prompts()
        except Exception:  # noqa: BLE001
            return []
        return [p.model_dump(mode="json", exclude_none=True)
                for p in result.prompts]

    async def get_prompt(self, name: str,
                         arguments: Optional[dict] = None) -> dict:
        """Server's prompts/get response — rendered messages."""
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        result = await self._session.get_prompt(name, arguments or {})
        return result.model_dump(mode="json", exclude_none=True)

    async def complete_argument(self, *,
                                ref_kind: str, ref_name: str,
                                arg_name: str, arg_value: str,
                                context_arguments: Optional[dict] = None,
                                ) -> dict:
        """Ask the server for completion candidates for one argument
        of a prompt or resource template.

        ``ref_kind`` is ``"prompt"`` or ``"resource"``. The shape of
        the return is the standard MCP CompleteResult envelope
        (``completion.values``, ``completion.total``, ``completion.hasMore``).
        Returns an empty completion result for servers that don't
        support the capability rather than raising.
        """
        from mcp.types import PromptReference, ResourceTemplateReference

        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        if ref_kind == "prompt":
            ref: Any = PromptReference(type="ref/prompt", name=ref_name)
        elif ref_kind == "resource":
            ref = ResourceTemplateReference(
                type="ref/resource", uri=ref_name,
            )
        else:
            raise ValueError(
                f"ref_kind must be 'prompt' or 'resource', got {ref_kind!r}"
            )

        try:
            result = await self._session.complete(
                ref,
                argument={"name": arg_name, "value": arg_value},
                context_arguments=context_arguments,
            )
        except Exception:  # noqa: BLE001 — server may not support it
            return {"completion": {"values": [], "total": 0, "hasMore": False}}
        return result.model_dump(mode="json", exclude_none=True)

    async def call_tool(self, tool_name: str,
                        arguments: Optional[dict]) -> CallToolResult:
        """Dispatch a ``tools/call`` to the server.

        Auto-reconnects once if the call fails with a session-expired
        error. Beyond that, propagates. Raises :class:`RuntimeError`
        if the session can't be re-established within a short window.
        """
        timeout_delta = datetime.timedelta(seconds=self.config.timeout_seconds)
        for attempt in range(2):
            await self._await_session_ready()
            try:
                async with self._call_lock:
                    return await self._session.call_tool(
                        tool_name,
                        arguments=arguments or {},
                        read_timeout_seconds=timeout_delta,
                    )
            except Exception as e:  # noqa: BLE001
                if attempt == 0 and _is_session_expired(e):
                    # Tell the supervisor to bounce — it'll re-init
                    # the session, our next loop iteration uses the
                    # fresh one.
                    self._signal_reconnect()
                    await self._await_session_ready(timeout=15)
                    continue
                raise
        # Loop only exits via return; ``raise`` above handles failure.
        raise RuntimeError("unreachable")

    async def _await_session_ready(self, *, timeout: float = 0.0) -> None:
        """Wait until ``self._session`` is populated by the supervisor.

        ``timeout=0`` (default) returns immediately — caller already
        checked. After a reconnect we pass a short timeout so callers
        don't race the supervisor.
        """
        if self._session is not None and self.error_kind != "needs_reauth":
            return
        if self.error_kind in ("needs_reauth", "fatal"):
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        if timeout <= 0:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        # Wait for the supervisor to set _ready again after a reconnect.
        self._ready.clear()
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"MCP server '{self.config.name}' reconnect timed out"
            )
        if self.error_kind in ("needs_reauth", "fatal"):
            raise RuntimeError(
                f"MCP server '{self.config.name}' reconnect failed: "
                f"{self.error}"
            )

    def _signal_reconnect(self) -> None:
        """Notify the supervisor to drop + rebuild the session."""
        self._reconnect_signal.set()

    # -- supervisor ---------------------------------------------------
    async def _supervisor(self) -> None:
        """Long-lived task — holds the transport + ClientSession open.

        Retry policy: on transient transport failures (network drop,
        session expiry that refresh fixed silently, remote restart)
        the supervisor sleeps with exponential backoff and reconnects.
        ``OAuthRegistrationError`` and ``OAuthTokenError`` mean the
        user has to re-authorise — we stop trying and set
        ``error_kind="needs_reauth"`` so the UI can surface a re-auth
        button. ``CancelledError`` propagates.
        """
        # Resolve exception classes once. The OAuth ones only exist
        # for HTTP-transport remote servers; we import lazily so a
        # stdio-only worker doesn't pull the auth submodule.
        try:
            from mcp.client.auth import (
                OAuthRegistrationError,
                OAuthTokenError,
            )
            _REAUTH_ERRORS: tuple = (OAuthRegistrationError, OAuthTokenError)
        except Exception:  # noqa: BLE001
            _REAUTH_ERRORS = ()

        backoff = 1.0
        attempt = 0
        max_transient_attempts = 6     # ~63s cumulative across the back-offs

        while not self._shutdown.is_set():
            try:
                self._reconnect_signal.clear()
                if self.config.type == LOCAL:
                    await self._run_local()
                elif self.config.type == HTTP:
                    await self._run_http()
                elif self.config.type == SSE:
                    await self._run_sse()
                else:
                    raise RuntimeError(
                        f"unsupported transport: {self.config.type}"
                    )
                # Clean exit from the session block — either
                # ``_shutdown`` was set (stop()) or
                # ``_reconnect_signal`` fired (call_tool flagged a
                # stale session). Either way, the ``async with`` ran
                # to completion without raising; no error to record.
                if self._shutdown.is_set():
                    return
                # Otherwise it was a reconnect request — reset the
                # backoff (this isn't a failure) and loop to reconnect.
                attempt = 0
                backoff = 1.0
                self.error = None
                self.error_kind = None
                continue
            except asyncio.CancelledError:
                raise
            except _REAUTH_ERRORS as e:
                # refresh_token rejected, dynamic client registration
                # rejected — the user must clear tokens and walk the
                # OAuth flow again. Stop retrying.
                self.error = f"{type(e).__name__}: {e}"
                self.error_kind = "needs_reauth"
                self._ready.set()
                return
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                # First-attempt failure on startup is fatal — the
                # config is probably wrong (bad command, dead URL,
                # missing API key). Don't auto-retry: noisy and
                # misleading. Mid-life failures (we were healthy then
                # dropped) are transient until ``max_transient_attempts``.
                healthy_before = self._ready.is_set() and self._session is not None
                self._session = None
                if healthy_before and attempt < max_transient_attempts:
                    self.error_kind = "transient"
                    attempt += 1
                    print(
                        f"[mcp] '{self.config.name}' transient error "
                        f"(attempt {attempt}/{max_transient_attempts}): "
                        f"{self.error}; retrying in {backoff:.1f}s",
                        file=sys.stderr,
                    )
                    try:
                        await asyncio.wait_for(self._shutdown.wait(),
                                                timeout=backoff)
                        return  # stop() arrived mid-backoff
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(backoff * 2, 30.0)
                    continue
                # First-attempt failure or transient-retry budget
                # exhausted — record + stop.
                self.error_kind = "fatal"
                self._ready.set()
                return
            finally:
                # Session reference is cleared when the ``async with``
                # exits; defensive nil-out so callers don't see a
                # stale ClientSession reference.
                if not self._reconnect_signal.is_set() or self._shutdown.is_set():
                    self._session = None

    async def _run_local(self) -> None:
        cmd = self.config.command
        params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:],
            env={**os.environ, **self.config.env},
        )
        async with stdio_client(params) as (read, write):
            await self._run_session(read, write)

    async def _run_http(self) -> None:
        from mcp.client.streamable_http import streamablehttp_client
        headers, auth = self._build_remote_auth()
        async with streamablehttp_client(
            self.config.url,
            headers=headers,
            auth=auth,
            timeout=self.config.timeout_seconds,
        ) as (read, write, _get_session_id):
            await self._run_session(read, write)

    async def _run_sse(self) -> None:
        from mcp.client.sse import sse_client
        headers, auth = self._build_remote_auth()
        async with sse_client(
            self.config.url,
            headers=headers,
            auth=auth,
            timeout=self.config.timeout_seconds,
        ) as (read, write):
            await self._run_session(read, write)

    async def _run_session(self, read, write) -> None:
        from mcp.types import SamplingCapability
        from .sampling import sampling_callback

        # Bind the server name into the logging callback so log lines
        # show which server emitted them — a single shared callback
        # would lose that.
        server_name = self.config.name

        async def _logging_callback(params) -> None:
            await _handle_log_notification(server_name, params)

        async with ClientSession(
            read, write,
            list_roots_callback=_list_roots_callback,
            sampling_callback=sampling_callback,
            sampling_capabilities=SamplingCapability(),
            logging_callback=_logging_callback,
        ) as session:
            await session.initialize()
            result = await session.list_tools()
            self._session = session
            self.tools = list(result.tools)
            # Once we've reached this point at least once, clear any
            # leftover transient-error message so a successful
            # reconnection stops surfacing the prior failure to the UI.
            self.error = None
            self.error_kind = None
            self._ready.set()
            # Hold the session open until either shutdown
            # (stop / worker exit) or a reconnect signal (call_tool
            # caught a session-expired error and wants the supervisor
            # to drop + rebuild). Either way, returning here exits the
            # ``async with`` and closes the underlying session.
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            reconnect_task = asyncio.create_task(self._reconnect_signal.wait())
            try:
                done, _ = await asyncio.wait(
                    {shutdown_task, reconnect_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for t in (shutdown_task, reconnect_task):
                    if not t.done():
                        t.cancel()
            # Either way, exit the with-block so the session closes
            # cleanly. The supervisor's outer loop reads the events
            # to decide whether to retry or stop.

    # -- remote auth --------------------------------------------------
    def _build_remote_auth(self) -> tuple[dict[str, str],
                                          Optional[httpx.Auth]]:
        """Construct headers + ``httpx.Auth`` for the remote transport.

        Bearer tokens get folded into the request headers directly
        (cheaper than wrapping httpx.Auth for a static token). OAuth
        is delegated to the MCP SDK's ``OAuthClientProvider`` — we
        only provide the file-backed token storage + the browser
        redirect / localhost callback glue.
        """
        headers = dict(self.config.headers)
        if self.config.auth_kind == AUTH_BEARER:
            if self.config.bearer_token:
                # Don't clobber a user-supplied Authorization header.
                headers.setdefault(
                    "Authorization",
                    f"Bearer {self.config.bearer_token}",
                )
            else:
                print(f"[mcp] '{self.config.name}': bearer auth "
                      f"selected but no token configured",
                      file=sys.stderr)
            return headers, None

        if self.config.auth_kind == AUTH_OAUTH:
            from mcp.client.auth import OAuthClientProvider
            from mcp.shared.auth import OAuthClientMetadata

            from .oauth_flow import LocalhostCallback, _make_redirect_handler
            from .token_storage import FileTokenStorage

            oauth_cfg = self.config.oauth
            # OAuthSettings defaults already applied by parse_entry,
            # but be defensive in case a remote config came in without
            # the oauth block (kind=oauth, no settings).
            client_name = (oauth_cfg.client_name if oauth_cfg
                           else "OpenProgram")
            requested_port = (oauth_cfg.redirect_port if oauth_cfg
                              else 0)
            scope = oauth_cfg.scope if oauth_cfg else None
            client_id = oauth_cfg.client_id if oauth_cfg else None

            # Resolve the callback port ONCE per supervisor lifetime.
            # The SDK reads redirect_uris[0] from client_metadata when
            # building the auth URL and again when exchanging the code,
            # so it must not drift across attempts.
            if requested_port:
                port = int(requested_port)
            else:
                from .oauth_flow import _pick_free_port
                port = _pick_free_port("127.0.0.1")
            redirect_uri = f"http://127.0.0.1:{port}/callback"

            client_metadata = OAuthClientMetadata(
                client_name=client_name,
                redirect_uris=[redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method=(
                    "client_secret_post" if (oauth_cfg and
                                             oauth_cfg.client_secret)
                    else "none"
                ),
                scope=scope,
            )

            storage = FileTokenStorage(self.config.name)

            # If the user pre-registered a client, seed storage with
            # client_info so the SDK skips dynamic registration.
            if client_id:
                from mcp.shared.auth import OAuthClientInformationFull

                async def _seed_client_info() -> None:
                    info = await storage.get_client_info()
                    if info is not None:
                        return
                    await storage.set_client_info(
                        OAuthClientInformationFull(
                            client_id=client_id,
                            client_secret=(
                                oauth_cfg.client_secret if oauth_cfg
                                else None
                            ),
                            redirect_uris=[redirect_uri],
                        )
                    )

                # Fire-and-forget at supervisor build time — the
                # OAuthClientProvider reads storage lazily.
                asyncio.create_task(_seed_client_info())

            async def _callback_handler() -> tuple[str, Optional[str]]:
                cb = LocalhostCallback(
                    port=port,
                    timeout=self.config.timeout_seconds * 2 + 60,
                )
                await cb.start()
                try:
                    return await cb.wait()
                finally:
                    await cb.close()

            provider = OAuthClientProvider(
                server_url=self.config.url,
                client_metadata=client_metadata,
                storage=storage,
                redirect_handler=_make_redirect_handler(port),
                callback_handler=_callback_handler,
                timeout=self.config.timeout_seconds * 2 + 60,
            )
            return headers, provider

        return headers, None

    # -- diagnostics --------------------------------------------------
    def auth_status(self) -> dict[str, Any]:
        """Snapshot of authentication state for the management UI."""
        out: dict[str, Any] = {
            "kind": self.config.auth_kind,
            "authenticated": True,
        }
        if not self.config.is_remote:
            return out
        if self.config.auth_kind == AUTH_BEARER:
            out["authenticated"] = bool(self.config.bearer_token)
        elif self.config.auth_kind == AUTH_OAUTH:
            from .token_storage import FileTokenStorage
            out["authenticated"] = FileTokenStorage(
                self.config.name).has_tokens()
        return out
