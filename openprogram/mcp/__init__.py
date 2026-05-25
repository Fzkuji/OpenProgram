"""MCP (Model Context Protocol) client integration.

External MCP servers — listed in ``~/.agentic/mcp_servers.json`` — are
spawned as stdio subprocesses on worker startup. Their ``tools/list``
output is wrapped as :class:`~openprogram.agent.types.AgentTool` and
registered in the shared registry so the LLM sees them alongside
``@function``-decorated local tools.

Design mirrors opencode's MCP client (TypeScript), translated to Python:

  * Lazy-friendly but eagerly-loaded: ``load_mcp_servers()`` blocks
    on each server's ``initialize`` + ``tools/list`` so the registry
    is complete before the first dispatcher turn runs.
  * Persistent connections: a supervisor task per server holds the
    ``stdio_client`` + ``ClientSession`` async-context-manager pair
    for the worker's lifetime. Reduces per-call latency vs. respawn-
    on-every-call, and matches the protocol's intent (servers may
    push notifications, list_resources changes, etc.).
  * Namespaced tool names: ``{server}__{tool}`` (double underscore),
    chosen so the tool names stay valid under OpenAI's tool-name
    regex (no colons). Collisions across servers are still possible
    if two servers expose identically-named tools — last one wins
    in the registry, matching the rest of the framework.

Public surface:

  * :func:`load_mcp_servers` — call from worker startup
  * :func:`shutdown_mcp_servers` — call from worker shutdown
  * :func:`server_status` — diagnostic status dump (for the webui)
"""
from __future__ import annotations

from .registry import (
    add_server,
    get_server,
    load_mcp_servers,
    remove_server,
    restart_server,
    server_status,
    shutdown_mcp_servers,
)

__all__ = [
    "add_server",
    "get_server",
    "load_mcp_servers",
    "remove_server",
    "restart_server",
    "server_status",
    "shutdown_mcp_servers",
]
