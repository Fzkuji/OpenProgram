"""Manager for the set of MCP servers attached to this worker.

Holds the process-wide map of live :class:`~.client.MCPClient`
instances and the per-server set of AgentTool names that came from
each.

Two lifecycle entry points:

  * :func:`load_mcp_servers` — called once at worker startup. Reads
    ``mcp_servers.json``, spawns one :class:`~.client.MCPClient` per
    enabled server, registers each remote tool.
  * :func:`shutdown_mcp_servers` — worker shutdown.

Plus runtime mutations (driven by the webui / CLI / TUI management
endpoints):

  * :func:`add_server` — spawn a new server from a fresh config
  * :func:`remove_server` — stop + unregister
  * :func:`restart_server` — stop, re-load config from disk, spawn
"""
from __future__ import annotations

import sys
import threading
from typing import Any, Optional

from .adapter import register_remote_tool
from .client import MCPClient
from .config import MCPServerConfig, load_configs


# Module-level state — single set of MCP clients per process.
_clients: dict[str, MCPClient] = {}
# Per-server registered AgentTool names — needed to unregister
# precisely when a server is removed or restarted.
_registered_tool_names: dict[str, list[str]] = {}
_loaded = False
_loaded_lock = threading.Lock()


def server_status() -> list[dict[str, Any]]:
    """Diagnostic snapshot for the webui / CLI to render in settings."""
    out: list[dict[str, Any]] = []
    for name, client in _clients.items():
        out.append(_status_dict(name, client))
    return out


def get_server(name: str) -> Optional[dict[str, Any]]:
    """Single-server status snapshot, with full tool schemas (not just
    names). Returns ``None`` if no server with that name is loaded.
    """
    client = _clients.get(name)
    if client is None:
        return None
    snap = _status_dict(name, client)
    snap["tool_schemas"] = [
        {
            "name": t.name,
            "title": getattr(t, "title", None),
            "description": t.description,
            "input_schema": t.inputSchema,
        }
        for t in client.tools
    ]
    return snap


def _status_dict(name: str, client: MCPClient) -> dict[str, Any]:
    cfg = client.config
    out: dict[str, Any] = {
        "name": name,
        "type": cfg.type,
        "enabled": cfg.enabled,
        "timeout_seconds": cfg.timeout_seconds,
        "ready": client.is_ready,
        "error": client.error,
        "tool_count": len(client.tools),
        "tools": [t.name for t in client.tools],
        "registered_tool_names": list(
            _registered_tool_names.get(name, [])
        ),
    }
    if cfg.type == "local":
        out["command"] = list(cfg.command)
        out["env"] = dict(cfg.env)
    else:
        out["url"] = cfg.url
        out["headers"] = dict(cfg.headers)
        out["auth"] = client.auth_status()
    return out


async def load_mcp_servers() -> None:
    """Spawn every enabled server in ``mcp_servers.json`` and register
    its tools. Idempotent — subsequent calls are no-ops.

    Server failures are non-fatal: a misconfigured or crashing MCP
    server logs a warning and the worker keeps booting. The MCP layer
    is opt-in; the platform stays usable without it.
    """
    global _loaded
    with _loaded_lock:
        if _loaded:
            return
        _loaded = True

    # Include disabled entries — they still need to appear in the
    # management UI's left nav so users can re-enable them. The
    # ``_spawn_and_register`` helper handles the "disabled" branch:
    # marks the client with ``error='disabled'``, skips ``start()``.
    for cfg in load_configs(include_disabled=True):
        await _spawn_and_register(cfg)


async def shutdown_mcp_servers() -> None:
    """Tear down every server. Safe to call multiple times."""
    global _loaded
    for name in list(_clients.keys()):
        await _stop_and_unregister(name)
    _clients.clear()
    _registered_tool_names.clear()
    _loaded = False


async def add_server(cfg: MCPServerConfig) -> dict[str, Any]:
    """Spawn one new MCP server. Caller is expected to have already
    written the config to disk if it wants the server to come back
    after a worker restart.

    Returns the status dict (success → ``ready=True``, failure →
    ``error`` populated and ``ready=False``).
    """
    if cfg.name in _clients:
        await _stop_and_unregister(cfg.name)
    await _spawn_and_register(cfg)
    return _status_dict(cfg.name, _clients[cfg.name])


async def remove_server(name: str) -> bool:
    """Stop + unregister. Caller handles config-file persistence.

    Returns True if the server existed, False otherwise.
    """
    if name not in _clients:
        return False
    await _stop_and_unregister(name)
    return True


async def restart_server(name: str,
                         new_cfg: Optional[MCPServerConfig] = None) -> dict[str, Any]:
    """Stop + respawn one server.

    ``new_cfg`` lets the caller replace the previous config (e.g. the
    user edited it in the UI). If omitted, reuses the existing one.
    """
    if name not in _clients and new_cfg is None:
        raise KeyError(name)
    cfg = new_cfg or _clients[name].config
    if name in _clients:
        await _stop_and_unregister(name)
    await _spawn_and_register(cfg)
    return _status_dict(cfg.name, _clients[cfg.name])


async def _spawn_and_register(cfg: MCPServerConfig) -> None:
    """Internal helper — spawn + register tools + log."""
    client = MCPClient(cfg)
    if not cfg.enabled:
        # Track but don't start, so the UI can flip it back on later.
        client.error = "disabled"
        _clients[cfg.name] = client
        _registered_tool_names.setdefault(cfg.name, [])
        return

    try:
        await client.start()
    except Exception as e:  # noqa: BLE001
        print(f"[mcp] server '{cfg.name}' start raised: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        client.error = client.error or str(e)
    _clients[cfg.name] = client

    if client.error:
        print(f"[mcp] server '{cfg.name}' unavailable: {client.error}",
              file=sys.stderr)
        _registered_tool_names.setdefault(cfg.name, [])
        return

    names: list[str] = []
    for tool in client.tools:
        registered = register_remote_tool(client, tool)
        if registered:
            names.append(registered)
    _registered_tool_names[cfg.name] = names

    print(f"[mcp] server '{cfg.name}' ready — "
          f"{len(client.tools)} tool(s)", file=sys.stderr)


async def _stop_and_unregister(name: str) -> None:
    """Internal helper — stop the server + remove its tools from the
    shared AgentTool registry."""
    client = _clients.pop(name, None)
    if client is not None:
        try:
            await client.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[mcp] stop '{name}' raised: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    tool_names = _registered_tool_names.pop(name, [])
    if tool_names:
        # Defer the import to avoid a cycle: functions._runtime imports
        # nothing from us, but pulling it in at module load time would
        # tie startup ordering tighter than necessary.
        from openprogram.functions._runtime import _registry
        for tname in tool_names:
            _registry.pop(tname, None)
