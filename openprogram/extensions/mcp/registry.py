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

import logging
import threading
from typing import Any, Optional

from .adapter import register_remote_tool
from .client import MCPClient
from .config import MCPServerConfig, load_configs, mask_secret_map

_log = logging.getLogger(__name__)


# Module-level state — single set of MCP clients per process.
_clients: dict[str, MCPClient] = {}
# Per-server registered AgentTool names — needed to unregister
# precisely when a server is removed or restarted.
_registered_tool_names: dict[str, list[str]] = {}
_loaded = False
_loaded_lock = threading.Lock()


def get_client(name: str) -> Optional[MCPClient]:
    """Live :class:`MCPClient` for ``name`` or ``None`` if unknown.

    Used by the resource/prompt meta-tools (list_mcp_resources,
    read_mcp_resource, list_mcp_prompts, get_mcp_prompt) to reach the
    persistent session without going through HTTP.
    """
    return _clients.get(name)


def list_clients() -> list[MCPClient]:
    """Snapshot of every loaded MCPClient (ready, broken, or disabled).

    Order matches insertion / config-file order so callers iterating
    across all servers produce stable results.
    """
    return list(_clients.values())


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
        "always_load": cfg.always_load,
        "ready": client.is_ready,
        "error": _public_error_code(client),
        "error_kind": _public_error_kind(client),
        "source_catalog_url": cfg.source_catalog_url,
        "source_entry_hash": cfg.source_entry_hash,
        "tool_count": len(client.tools),
        "tools": [t.name for t in client.tools],
        "registered_tool_names": list(
            _registered_tool_names.get(name, [])
        ),
    }
    # Masked values only — this dict is what ``GET /api/mcp/servers``
    # and ``GET /api/mcp/servers/{name}`` return, and ``env`` / ``headers``
    # routinely hold API keys. Names stay visible so the UI can show what
    # is configured; values are presence + mask.
    if cfg.type == "local":
        out["command"] = list(cfg.command)
        out["env"] = mask_secret_map(cfg.env)
    else:
        out["url"] = cfg.url
        out["headers"] = mask_secret_map(cfg.headers)
        # ``auth_status`` reports kind + whether a credential is present;
        # the stored bearer / client secret is masked alongside it.
        out["auth"] = {**client.auth_status(),
                       **cfg.to_response_dict()["auth"]}
    return out


def _public_error_kind(client: MCPClient) -> str | None:
    if not client.error:
        return None
    kind = str(client.error_kind or "fatal").lower()
    return kind if kind in {"needs_reauth", "transient", "fatal"} else "fatal"


def _public_error_code(client: MCPClient) -> str | None:
    if not client.error:
        return None
    return "mcp_disabled" if client.error == "disabled" else "mcp_server_unavailable"


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
    # Nobody is waiting on these connections: a server that needs a sign-in
    # keeps its link pending and opens no browser (oauth_flow.unattended).
    from .oauth_flow import unattended
    attended = unattended.set(True)
    try:
        for queued in load_configs(include_disabled=True):
            # Management may delete, disable or replace later entries while an
            # earlier connection awaits transport or consent. Do not replay its
            # stale startup configuration over the owner's current state.
            if queued.name in _clients:
                continue
            current = next((cfg for cfg in load_configs(include_disabled=True)
                            if cfg.name == queued.name), None)
            if current is not None:
                await _spawn_and_register(current)
    finally:
        unattended.reset(attended)

    # Fold every connected server's prompts into the unified
    # slash-command registry so users see them in the composer
    # alongside user / project / plugin commands.
    try:
        from openprogram.runtime.commands.registry import sync_mcp_prompts
        await sync_mcp_prompts()
    except Exception:
        # Prompt sync is optional: the servers stay usable, only their
        # slash commands are missing until the next sync.
        _log.warning("MCP prompt sync into slash commands failed",
                     exc_info=True)


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
    try:
        from openprogram.runtime.commands.registry import sync_mcp_prompts
        await sync_mcp_prompts()
    except Exception:
        # Prompt sync is optional: the servers stay usable, only their
        # slash commands are missing until the next sync.
        _log.warning("MCP prompt sync into slash commands failed",
                     exc_info=True)
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
    try:
        from openprogram.runtime.commands.registry import sync_mcp_prompts
        await sync_mcp_prompts()
    except Exception:
        # Prompt sync is optional: the servers stay usable, only their
        # slash commands are missing until the next sync.
        _log.warning("MCP prompt sync into slash commands failed",
                     exc_info=True)
    return _status_dict(cfg.name, _clients[cfg.name])


async def _spawn_and_register(cfg: MCPServerConfig) -> None:
    """Internal helper — spawn + register tools + log."""
    client = MCPClient(cfg)
    # Pending clients must remain visible to management and shutdown.
    _clients[cfg.name] = client
    _registered_tool_names.setdefault(cfg.name, [])
    if not cfg.enabled:
        # Track but don't start, so the UI can flip it back on later.
        client.error = "disabled"
        return

    try:
        await client.start()
    except Exception as exc:  # noqa: BLE001
        # Only the exception's type: its text can carry the server's URL or env.
        _log.error("server '%s' start failed (%s)", cfg.name, type(exc).__name__)
        client.error = client.error or "mcp_server_unavailable"
    if _clients.get(cfg.name) is not client:
        return

    if client.error:
        _log.warning("server '%s' unavailable (%s)", cfg.name, client.error)
        _registered_tool_names.setdefault(cfg.name, [])
        return

    names: list[str] = []
    for tool in client.tools:
        registered = register_remote_tool(client, tool)
        if registered:
            names.append(registered)
    _registered_tool_names[cfg.name] = names

    _log.info("server '%s' ready — %s tool(s)", cfg.name, len(client.tools))


async def _stop_and_unregister(name: str) -> None:
    """Internal helper — stop the server + remove its tools from the
    shared AgentTool registry."""
    client = _clients.pop(name, None)
    if client is not None:
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            _log.warning("stop '%s' failed", name, exc_info=True)
    tool_names = _registered_tool_names.pop(name, [])
    if tool_names:
        # Defer the import to avoid a cycle: functions._runtime imports
        # nothing from us, but pulling it in at module load time would
        # tie startup ordering tighter than necessary.
        from openprogram.programs._host.runtime import _registry, mark_unavailable
        # A conversation that loaded one keeps its definition; a call of it
        # says the server is disconnected (deferred-tools.html).
        mark_unavailable(tool_names, f"MCP server '{name}' is disconnected")
        for tname in tool_names:
            _registry.pop(tname, None)
