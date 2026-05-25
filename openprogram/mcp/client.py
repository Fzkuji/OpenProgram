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
from typing import Any, Optional

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from .config import AUTH_BEARER, AUTH_OAUTH, HTTP, LOCAL, SSE, MCPServerConfig


class MCPClient:
    """Holds one MCP server connection for the worker's lifetime."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.tools: list[Tool] = []
        self.error: Optional[str] = None
        self._session: Optional[ClientSession] = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
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

    async def call_tool(self, tool_name: str,
                        arguments: Optional[dict]) -> CallToolResult:
        """Dispatch a ``tools/call`` to the server.

        Raises :class:`RuntimeError` if the session is not ready
        (either start failed or stop has been called).
        """
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' not connected "
                f"({self.error or 'no session'})"
            )
        timeout_delta = datetime.timedelta(seconds=self.config.timeout_seconds)
        async with self._call_lock:
            return await self._session.call_tool(
                tool_name,
                arguments=arguments or {},
                read_timeout_seconds=timeout_delta,
            )

    # -- supervisor ---------------------------------------------------
    async def _supervisor(self) -> None:
        """Long-lived task — holds the transport + ClientSession open.

        On any exception (subprocess crash, protocol error, HTTP
        failure), records the error and sets ``_ready`` so ``start()``
        can return. Cleanup happens via the ``async with`` exits.
        """
        try:
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
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
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
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            self._session = session
            self.tools = list(result.tools)
            self._ready.set()
            # Hold the session open until shutdown.
            await self._shutdown.wait()

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
