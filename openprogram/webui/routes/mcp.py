"""MCP server management endpoints.

Used by the webui ``/mcp`` settings page, the CLI ``openprogram mcp``
subcommands, and the TUI ``/mcp`` slash command. All three frontends
talk to this single backend so config edits propagate to the live
worker without requiring a process restart.

Endpoints
---------

``GET    /api/mcp/servers``               list all (incl. disabled)
``GET    /api/mcp/servers/{name}``        single server + tool schemas
``POST   /api/mcp/servers``               add a new server
``PATCH  /api/mcp/servers/{name}``        edit an existing server
``DELETE /api/mcp/servers/{name}``        remove
``POST   /api/mcp/servers/{name}/restart``  stop + respawn one server
``POST   /api/mcp/servers/{name}/enable``   shortcut: set enabled=true + restart
``POST   /api/mcp/servers/{name}/disable``  shortcut: set enabled=false + stop
``POST   /api/mcp/test``                  spawn a config in a sandbox without persisting

All write endpoints persist to ``<state>/mcp_servers.json`` so the
server set survives worker restarts.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from openprogram.mcp import (
    add_server,
    get_server,
    remove_server,
    restart_server,
    server_status,
)
from openprogram.mcp.config import (
    MCPServerConfig,
    load_configs,
    parse_entry,
    save_configs,
)


def register(app: FastAPI) -> None:
    @app.get("/api/mcp/servers")
    async def list_servers():
        """List every loaded server (enabled and disabled). The
        list is built from the in-memory registry, so it reflects
        actual run-time state, not just on-disk config.
        """
        return JSONResponse(content={"servers": server_status()})

    @app.get("/api/mcp/servers/{name}")
    async def get_one(name: str):
        snap = get_server(name)
        if snap is None:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not loaded")
        return JSONResponse(content=snap)

    @app.post("/api/mcp/servers")
    async def add_one(body: dict):
        """Body shape::

            {"name": "drawio", "type": "local",
             "command": ["npx", "-y", "@drawio/mcp"],
             "env": {...},
             "enabled": true,
             "timeout_seconds": 30}
        """
        cfg = _parse_body(body)
        # Persist alongside existing entries (read-modify-write).
        all_cfgs = load_configs(include_disabled=True)
        if any(c.name == cfg.name for c in all_cfgs):
            raise HTTPException(status_code=409,
                                detail=f"server '{cfg.name}' already exists")
        all_cfgs.append(cfg)
        save_configs(all_cfgs)
        status = await add_server(cfg)
        return JSONResponse(content=status, status_code=201)

    @app.patch("/api/mcp/servers/{name}")
    async def patch_one(name: str, body: dict):
        """Body may include any of ``command`` / ``env`` / ``enabled``
        / ``timeout_seconds`` / ``type``. The server is restarted with
        the new config.
        """
        all_cfgs = load_configs(include_disabled=True)
        match = next((c for c in all_cfgs if c.name == name), None)
        if match is None:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not in config")
        merged = match.to_dict()
        for k in ("type", "command", "env", "url", "headers", "auth",
                  "enabled", "timeout_seconds", "always_load"):
            if k in body:
                merged[k] = body[k]
        new_cfg = parse_entry(name, merged)
        if new_cfg is None:
            raise HTTPException(status_code=400, detail="invalid config")
        # Replace in list, persist, then restart.
        new_list = [c if c.name != name else new_cfg for c in all_cfgs]
        save_configs(new_list)
        try:
            status = await restart_server(name, new_cfg=new_cfg)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"restart failed: {type(e).__name__}: {e}")
        return JSONResponse(content=status)

    @app.delete("/api/mcp/servers/{name}")
    async def delete_one(name: str):
        all_cfgs = load_configs(include_disabled=True)
        new_list = [c for c in all_cfgs if c.name != name]
        if len(new_list) == len(all_cfgs):
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not in config")
        save_configs(new_list)
        await remove_server(name)
        return JSONResponse(content={"removed": name})

    @app.post("/api/mcp/servers/{name}/restart")
    async def restart_one(name: str):
        try:
            status = await restart_server(name)
        except KeyError:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not loaded")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500,
                                detail=f"restart failed: {type(e).__name__}: {e}")
        return JSONResponse(content=status)

    @app.post("/api/mcp/servers/{name}/enable")
    async def enable_one(name: str):
        return await patch_one(name, {"enabled": True})

    @app.post("/api/mcp/servers/{name}/disable")
    async def disable_one(name: str):
        return await patch_one(name, {"enabled": False})

    @app.post("/api/mcp/servers/{name}/auth/reauth")
    async def reauth_one(name: str):
        """Tear down stored tokens + restart so a fresh OAuth flow runs.

        Shortcut wired to the "Re-authenticate" button in the server
        detail panel when ``error_kind == 'needs_reauth'``. Same effect
        as POST /auth/clear (which is kept around for backwards
        compatibility with anyone scripting the older endpoint).
        """
        from openprogram.mcp.token_storage import FileTokenStorage
        FileTokenStorage(name).clear()
        try:
            status = await restart_server(name)
        except KeyError:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not loaded")
        return JSONResponse(content=status)

    @app.post("/api/mcp/servers/{name}/complete")
    async def complete_argument(name: str, body: dict):
        """Forward a completion request to an MCP server.

        Body shape::

            {
              "ref_kind": "prompt" | "resource",
              "ref_name": "<prompt name or resource URI template>",
              "arg_name": "<argument name>",
              "arg_value": "<partial value to complete>",
              "context_arguments": {...optional...}
            }

        Returns the MCP CompleteResult envelope unchanged so the
        caller can render ``completion.values`` (the list of
        suggestions) directly.
        """
        from openprogram.mcp.registry import get_client
        client = get_client(name)
        if client is None:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not loaded")
        if not client.is_ready:
            raise HTTPException(status_code=409,
                                detail=f"server '{name}' not ready: "
                                       f"{client.error or 'no session'}")
        ref_kind = body.get("ref_kind")
        ref_name = body.get("ref_name")
        arg_name = body.get("arg_name")
        arg_value = body.get("arg_value", "")
        if not isinstance(ref_kind, str) or ref_kind not in ("prompt", "resource"):
            raise HTTPException(status_code=400,
                                detail="ref_kind must be 'prompt' or 'resource'")
        if not isinstance(ref_name, str) or not ref_name:
            raise HTTPException(status_code=400,
                                detail="ref_name required")
        if not isinstance(arg_name, str) or not arg_name:
            raise HTTPException(status_code=400,
                                detail="arg_name required")
        try:
            result = await client.complete_argument(
                ref_kind=ref_kind,
                ref_name=ref_name,
                arg_name=arg_name,
                arg_value=str(arg_value),
                context_arguments=body.get("context_arguments") or None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return JSONResponse(content=result)

    @app.get("/api/mcp/logs")
    async def get_logs(server: Optional[str] = None,
                       level: Optional[str] = None,
                       limit: int = 100):
        """Return recent MCP server log notifications.

        Servers can push log lines via the standard
        ``notifications/message`` once they observe the host has the
        logging capability advertised. We tail the in-memory ring
        buffer + optionally filter by server and minimum level.
        """
        from openprogram.mcp.client import get_log_history
        history = get_log_history()
        level_order = {"debug": 0, "info": 1, "notice": 2, "warning": 3,
                       "error": 4, "critical": 5, "alert": 6, "emergency": 7}
        if server:
            history = [e for e in history if e["server"] == server]
        if level and level in level_order:
            cutoff = level_order[level]
            history = [e for e in history
                       if level_order.get(e["level"], 1) >= cutoff]
        # Most recent last (consistent with log files); tail to limit.
        if limit > 0:
            history = history[-limit:]
        return JSONResponse(content={"entries": history})

    @app.get("/api/mcp/roots")
    async def list_roots():
        """Return the host-advertised roots — workspace URIs every
        MCP server can request via the standard ``roots/list``.
        """
        from openprogram.mcp.config import load_roots
        return JSONResponse(content={"roots": load_roots()})

    @app.put("/api/mcp/roots")
    async def set_roots(body: dict):
        """Replace the global roots list.

        Body: ``{"roots": [{"uri": "file:///abs/path", "name": "Label"}, ...]}``.
        ``name`` is optional and defaults to the path basename / hostname.
        After save, every connected MCP server is sent the standard
        ``notifications/roots/list_changed`` so it can re-query.
        """
        from openprogram.mcp.config import save_roots
        roots = body.get("roots") if isinstance(body, dict) else None
        if not isinstance(roots, list):
            raise HTTPException(status_code=400,
                                detail="body.roots must be a list")
        # Tolerate string entries by upgrading them to {uri: str} —
        # cli quick-set is the common shape ("/api/mcp/roots" with
        # body {"roots": ["file:///x"]}).
        normalised: list[dict] = []
        for entry in roots:
            if isinstance(entry, str):
                normalised.append({"uri": entry})
            elif isinstance(entry, dict):
                normalised.append(entry)
        save_roots(normalised)
        # Tell every live MCP server the list changed so it can
        # re-call roots/list. Spec-defined notification.
        try:
            from openprogram.mcp.registry import list_clients
            from mcp.types import (
                ClientNotification,
                RootsListChangedNotification,
            )
            for client in list_clients():
                if client.is_ready and client._session is not None:
                    try:
                        await client._session.send_notification(  # noqa: SLF001
                            ClientNotification(RootsListChangedNotification()),
                        )
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        from openprogram.mcp.config import load_roots
        return JSONResponse(content={"roots": load_roots()})

    @app.get("/api/mcp/auth/pending")
    async def pending_auth():
        """List in-progress OAuth flows + their authorisation URLs.

        Used by headless deployments where the worker's stderr isn't
        visible (managed services, Docker, systemd). Operators fetch
        this to get the URL they need to open in a browser on a
        machine that has one. The callback port shown also tells
        them which port to ``ssh -L`` if the worker is remote.

        Returns ``{"pending": [{"callback_port": <int>, "url": <str>}, ...]}``.
        """
        from openprogram.mcp.oauth_flow import get_all_pending_auth
        items = [
            {"callback_port": port, "url": url}
            for port, url in get_all_pending_auth().items()
        ]
        return JSONResponse(content={"pending": items})

    @app.post("/api/mcp/servers/{name}/auth/clear")
    async def clear_auth(name: str):
        """Wipe stored OAuth tokens + (dynamic) client info for a
        remote MCP server, then restart it. Used when the upstream
        revokes our refresh token or when switching accounts.
        """
        from openprogram.mcp.token_storage import FileTokenStorage
        removed = FileTokenStorage(name).clear()
        try:
            status = await restart_server(name)
        except KeyError:
            status = None
        return JSONResponse(content={
            "name": name,
            "tokens_cleared": removed,
            "server": status,
        })

    @app.post("/api/mcp/test")
    async def test_config(body: dict):
        """Spawn a config in a one-shot sandbox to verify the command
        actually starts up and returns a ``tools/list``. Doesn't write
        to disk and doesn't touch the live registry.

        Body: same shape as ``POST /api/mcp/servers``.
        """
        cfg = _parse_body(body)
        from openprogram.mcp.client import MCPClient
        client = MCPClient(cfg)
        try:
            await client.start()
            ok = client.is_ready and client.error is None
            return JSONResponse(content={
                "ok": ok,
                "ready": client.is_ready,
                "error": client.error,
                "tool_count": len(client.tools),
                "tools": [t.name for t in client.tools],
            })
        finally:
            try:
                await client.stop()
            except Exception:  # noqa: BLE001
                pass

    @app.get("/api/mcp/config-path")
    async def config_path():
        from openprogram.mcp.config import get_config_path as _p
        return JSONResponse(content={"path": str(_p())})


def _parse_body(body: dict) -> MCPServerConfig:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400,
                            detail="missing/empty 'name'")
    entry: dict = {
        "type": body.get("type", "local"),
        "enabled": body.get("enabled", True),
        "timeout_seconds": body.get("timeout_seconds", 30.0),
        "always_load": body.get("always_load", False),
    }
    if entry["type"] == "local":
        entry["command"] = body.get("command")
        entry["env"] = body.get("env", {})
    else:
        entry["url"] = body.get("url")
        entry["headers"] = body.get("headers", {})
        entry["auth"] = body.get("auth") or {"kind": "none"}
    cfg = parse_entry(name.strip(), entry)
    if cfg is None:
        raise HTTPException(status_code=400, detail="invalid config")
    return cfg
