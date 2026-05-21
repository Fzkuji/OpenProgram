"""Single MCP server client — stdio transport, persistent session.

Owns one ``ClientSession`` per server, held open by a supervisor
``asyncio.Task`` for the worker's lifetime. ``start()`` blocks until
``initialize`` + ``list_tools`` complete or the supervisor fails;
``call_tool()`` reuses the live session; ``stop()`` flags the
supervisor to exit, releasing the stdio subprocess.

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
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from .config import MCPServerConfig


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
        """Spawn the subprocess, initialize, fetch tool list.

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
            # Bound the wait so a hung subprocess doesn't pin worker startup.
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

    async def _supervisor(self) -> None:
        """Long-lived task — holds the stdio_client + ClientSession open.

        On any exception (subprocess crash, protocol error), records
        the error and sets ``_ready`` so ``start()`` can return.
        Cleanup happens via the ``async with`` exit, which closes
        the subprocess.
        """
        cmd = self.config.command
        params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:],
            env={**os.environ, **self.config.env},
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    self._session = session
                    self.tools = list(result.tools)
                    self._ready.set()
                    # Hold the session open until shutdown.
                    await self._shutdown.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
            self._session = None
