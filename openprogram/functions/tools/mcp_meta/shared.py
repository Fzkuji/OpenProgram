"""Shared MCP client plumbing for the mcp_meta tool family.

All four tools resolve a named server to a ready client; the two list_*
tools additionally fan out the same "query every loaded server" loop,
differing only in which client method they call.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def ready_client(server: str):
    """Resolve ``server`` to a ready MCP client.

    Returns ``(client, None)`` on success, ``(None, error_string)``
    when the server is unknown or not ready.
    """
    from openprogram.mcp.registry import get_client

    client = get_client(server)
    if client is None:
        return None, f"Error: no MCP server named {server!r}"
    if not client.is_ready:
        return None, (f"Error: MCP server {server!r} not ready "
                      f"({client.error or 'no session'})")
    return client, None


async def list_across_servers(server: Optional[str], method: str) -> str:
    """Run ``client.<method>()`` on one named server, or every loaded
    server, and return the union as JSON — each item tagged with a
    ``server`` field. Servers without support return nothing and are
    skipped silently; per-server failures surface as ``_error`` rows.
    """
    from openprogram.mcp.registry import list_clients

    if server:
        client, err = ready_client(server)
        if err:
            return err
        items = await getattr(client, method)()
        out: list[dict[str, Any]] = [{**it, "server": server} for it in items]
    else:
        out = []
        for client in list_clients():
            if not client.is_ready:
                continue
            try:
                items = await getattr(client, method)()
            except Exception as e:  # noqa: BLE001
                out.append({"server": client.config.name,
                            "_error": f"{type(e).__name__}: {e}"})
                continue
            out.extend({**it, "server": client.config.name} for it in items)
    return json.dumps(out, ensure_ascii=False, indent=2)
