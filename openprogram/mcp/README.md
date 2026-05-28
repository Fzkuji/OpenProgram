# `openprogram/mcp/`

> MCP (Model Context Protocol) client integration.

## Overview

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

## Files in this directory

- **`adapter.py`** — Translate between MCP wire types and our AgentTool framework types
- **`client.py`** — Single MCP server client
- **`config.py`** — Config schema + loader for MCP servers
- **`oauth_flow.py`** — Browser-based OAuth 2.1 PKCE flow plumbing for remote MCP servers
- **`registry.py`** — Manager for the set of MCP servers attached to this worker
- **`sampling.py`** — Sampling
- **`token_storage.py`** — File-backed ``TokenStorage`` for the MCP SDK's ``OAuthClientProvider``

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `_gen_dir_readmes.py` to refresh._
