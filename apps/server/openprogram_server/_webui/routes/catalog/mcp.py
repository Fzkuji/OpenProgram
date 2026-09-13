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

import asyncio
import re
import tempfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from openprogram.auth.credentials import is_redacted_value as _is_redacted
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
    load_configs_with_revision,
    parse_entry,
    save_configs_revision,
)
from openprogram.auth.credentials import PrivateAtomicWriteError


def _save_expected(configs: list[MCPServerConfig], revision: str) -> str:
    try:
        return save_configs_revision(configs, expected_revision=revision)
    except PrivateAtomicWriteError as exc:
        if exc.code == "conflict":
            raise HTTPException(
                status_code=409,
                detail="MCP config changed concurrently; retry the request",
            ) from exc
        raise


async def _resync_server_from_disk(name: str) -> tuple[int, dict[str, str]] | None:
    """Match runtime to disk, returning a sanitized failure outcome."""
    current, _revision_value = load_configs_with_revision(include_disabled=True)
    match = next((cfg for cfg in current if cfg.name == name), None)
    if match is None:
        cleanup_failed = False
        try:
            await remove_server(name)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            cleanup_failed = True
        if cleanup_failed:
            return (
                500,
                {
                    "code": "mcp_runtime_state_unknown",
                    "persisted_config": "unchanged",
                    "runtime_state": "unknown",
                    "action": "retry_or_restart",
                },
            )
        return

    resync_failed = False
    try:
        await restart_server(name, new_cfg=match)
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        resync_failed = True
    if not resync_failed:
        return None

    cleanup_failed = False
    try:
        await remove_server(name)
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        cleanup_failed = True
    if cleanup_failed:
        return (
            500,
            {
                "code": "mcp_runtime_state_unknown",
                "persisted_config": "unchanged",
                "runtime_state": "unknown",
                "action": "retry_or_restart",
            },
        )
    return (
        503,
        {
            "code": "mcp_runtime_resync_failed",
            "persisted_config": "unchanged",
            "runtime_state": "stopped",
            "action": "retry_or_restart",
        },
    )


async def _restart_then_publish(
    name: str,
    *,
    previous: MCPServerConfig,
    updated: MCPServerConfig,
    configs: list[MCPServerConfig],
    expected_revision: str,
) -> dict:
    """Validate runtime first, then conditionally publish the same snapshot."""
    restart_failed = False
    rollback_failed = False
    try:
        status = await restart_server(name, new_cfg=updated)
    except Exception:  # noqa: BLE001
        restart_failed = True
        try:
            await restart_server(name, new_cfg=previous)
        except Exception:  # noqa: BLE001
            rollback_failed = True
    if restart_failed:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "mcp_runtime_restart_failed",
                "persisted_config": "unchanged",
                "runtime_state": "unknown" if rollback_failed else "restored",
                "action": "retry_or_restart",
            },
        )

    conflict = False
    uncommitted_error = None
    try:
        save_configs_revision(configs, expected_revision=expected_revision)
    except PrivateAtomicWriteError as exc:
        if exc.code == "conflict":
            conflict = True
        elif exc.committed:
            raise
        else:
            uncommitted_error = exc

    if conflict or uncommitted_error is not None:
        resync_failure = await _resync_server_from_disk(name)
        if resync_failure is not None:
            status_code, detail = resync_failure
            raise HTTPException(status_code=status_code, detail=detail)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="MCP config changed concurrently; runtime was resynced",
        )
    if uncommitted_error is not None:
        raise uncommitted_error
    return status


def _require_local_request(request: Request) -> None:
    """Allow loopback clients; browser requests must also be same-origin."""
    from urllib.parse import urlsplit
    from openprogram.backend_endpoint import is_loopback_host

    client_host = getattr(getattr(request, "client", None), "host", "")
    host = request.headers.get("host", "").strip().lower()
    origin = request.headers.get("origin", "").strip().lower()
    site = request.headers.get("sec-fetch-site", "").strip().lower()
    try:
        origin_host = urlsplit(origin).netloc.lower()
    except ValueError:
        origin_host = ""
    if (not is_loopback_host(client_host)
            or (origin and (
                not origin_host
                or origin_host != host
                or site not in ("same-origin", "none")
            ))):
        raise HTTPException(
            status_code=403,
            detail="one-shot MCP tests require a local request",
        )


def _one_shot_client(cfg: MCPServerConfig, sandbox_cwd: str):
    from openprogram.mcp.client import MCPClient
    return MCPClient(cfg, force_sandbox=True, sandbox_cwd=sandbox_cwd)


async def _fetch_catalog_json(url: str):
    from openprogram.security import safe_http
    from openprogram.security.url_policy import OwnerURLException, normalize_origin

    consumer = "webui.mcp.catalog"
    try:
        async with safe_http.configured_safe_async_client(
            consumer,
            url,
            owner_exception=OwnerURLException(
                consumer=consumer, origin=normalize_origin(url)
            ),
        ) as client:
            response = await client.get(url, timeout=15.0)
            safe_http.raise_for_status_sanitized(response)
            safe_http.require_json_mime(response)
            return response.json()
    except Exception as e:
        detail = f"{type(e).__name__} for {normalize_origin(url)}"
        if isinstance(e, RuntimeError) and str(e).startswith("HTTP "):
            detail = str(e)
        raise RuntimeError(detail) from None

BUNDLED_CATALOG_URL = "openprogram://bundled"
OFFICIAL_REGISTRY_URL = (
    "https://registry.modelcontextprotocol.io/v0.1/servers"
    "?version=latest&limit=80"
)


def _bundled_catalog() -> dict:
    """Local OpenProgram-shaped catalog — no network required."""
    return {
        "name": "OpenProgram bundled catalog",
        "description": (
            "Filesystem, git, fetch, sequential-thinking, time, memory — "
            "local reference servers."
        ),
        "servers": [
            {
                "name": "filesystem",
                "description": "Read/write files in a sandboxed root directory.",
                "type": "local",
                "command": [
                    "npx", "-y",
                    "@modelcontextprotocol/server-filesystem",
                    tempfile.gettempdir(),
                ],
            },
            {
                "name": "git",
                "description": (
                    "Inspect commits, branches, diffs from any git repo."
                ),
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-git"],
            },
            {
                "name": "fetch",
                "description": (
                    "HTTP fetch with safe parsing — read URLs the model "
                    "would otherwise hallucinate."
                ),
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
            },
            {
                "name": "sequential-thinking",
                "description": (
                    "Chain-of-thought scratchpad tool — the model writes "
                    "reasoning to a private buffer."
                ),
                "type": "local",
                "command": [
                    "npx", "-y",
                    "@modelcontextprotocol/server-sequential-thinking",
                ],
            },
            {
                "name": "time",
                "description": "Current time and timezone conversion.",
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-time"],
            },
            {
                "name": "memory",
                "description": (
                    "Persistent knowledge-graph memory across conversations."
                ),
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
            },
        ],
    }


def _catalog_display_name(raw: str, used: set[str]) -> str:
    """Last path segment, sanitized; names with ``/`` break server routes."""
    segment = (raw or "").strip().rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "-", segment).strip(".-_") or "server"
    base = name
    n = 2
    while name in used:
        name = f"{base}-{n}"
        n += 1
    used.add(name)
    return name


def _package_arg_values(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            if item:
                out.append(item)
        elif isinstance(item, dict) and item.get("value") is not None:
            out.append(str(item["value"]))
    return out


def _package_command(pkg: dict) -> list[str] | None:
    if not isinstance(pkg, dict):
        return None
    kind = str(pkg.get("registryType") or "").lower()
    ident = pkg.get("identifier")
    if not isinstance(ident, str) or not ident.strip():
        return None
    extra = (
        _package_arg_values(pkg.get("runtimeArguments"))
        + _package_arg_values(pkg.get("packageArguments"))
    )
    ident = ident.strip()
    prefix = _bundled_catalog()["servers"][0]["command"][:2]
    if kind == 'npm':
        return [*prefix, ident, *extra]
    if kind == 'pypi':
        return ['uvx', ident, *extra]
    return None


def _from_official_server(item: dict, used: set[str]) -> dict | None:
    if not isinstance(item, dict):
        return None
    server = item["server"] if isinstance(item.get("server"), dict) else item
    raw_name = server.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    entry: dict | None = None
    remotes = server.get("remotes") or []
    if isinstance(remotes, list):
        for remote in remotes:
            if not isinstance(remote, dict):
                continue
            rtype = str(remote.get("type") or "").lower()
            rurl = remote.get("url")
            if not isinstance(rurl, str) or not rurl.strip():
                continue
            if rtype in ("streamable-http", "http"):
                entry = {"type": "http", "url": rurl.strip()}
                break
            if rtype == "sse":
                entry = {"type": "sse", "url": rurl.strip()}
                break
    if entry is None:
        packages = server.get("packages") or []
        if isinstance(packages, list):
            for pkg in packages:
                cmd = _package_command(pkg) if isinstance(pkg, dict) else None
                if cmd:
                    entry = {"type": "local", "command": cmd}
                    break
    if entry is None:
        return None
    entry["name"] = _catalog_display_name(raw_name.strip(), used)
    description = server.get("description")
    if isinstance(description, str) and description.strip():
        entry["description"] = description
    homepage = server.get("websiteUrl") or server.get("homepage")
    if isinstance(homepage, str) and homepage.strip():
        entry["homepage"] = homepage
    return entry


def _is_openprogram_entry(item: dict) -> bool:
    if not isinstance(item, dict) or isinstance(item.get("server"), dict):
        return False
    command = item.get("command")
    if isinstance(command, list) and command:
        return True
    url = item.get("url")
    return isinstance(url, str) and bool(url.strip())


def _normalize_catalog_servers(data):
    catalog_name = None
    catalog_desc = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        name = data.get("name")
        desc = data.get("description")
        catalog_name = name if isinstance(name, str) and name.strip() else None
        catalog_desc = desc if isinstance(desc, str) else None
        items = data.get("servers") or []
        if not isinstance(items, list):
            items = []
    else:
        items = []
    used: set[str] = set()
    raw_servers: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _is_openprogram_entry(item):
            entry = dict(item)
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                entry["name"] = _catalog_display_name(name.strip(), used)
            raw_servers.append(entry)
            continue
        converted = _from_official_server(item, used)
        if converted is not None:
            raw_servers.append(converted)
    return catalog_name, catalog_desc, raw_servers

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
        all_cfgs, revision = load_configs_with_revision(include_disabled=True)
        if any(c.name == cfg.name for c in all_cfgs):
            raise HTTPException(status_code=409,
                                detail=f"server '{cfg.name}' already exists")
        all_cfgs.append(cfg)
        _save_expected(all_cfgs, revision)
        status = await add_server(cfg)
        return JSONResponse(content=status, status_code=201)

    @app.patch("/api/mcp/servers/{name}")
    async def patch_one(name: str, body: dict):
        """Body may include any of ``command`` / ``env`` / ``enabled``
        / ``timeout_seconds`` / ``type``. The server is restarted with
        the new config.

        Secret-bearing fields (``env`` and ``headers`` values, the
        bearer token, the OAuth client secret) follow preserve /
        replace / delete: a name the body omits keeps its stored value,
        a name carrying a new value replaces it, and a name carrying an
        explicit empty string deletes it. Display masks and redaction
        sentinels are invalid. See :func:`_merge_secret_map`.
        """
        all_cfgs, revision = load_configs_with_revision(include_disabled=True)
        match = next((c for c in all_cfgs if c.name == name), None)
        if match is None:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not in config")
        merged = match.to_storage_dict()
        for k in ("type", "command", "url",
                  "enabled", "timeout_seconds", "always_load"):
            if k in body:
                merged[k] = body[k]
        for k in ("env", "headers"):
            if k in body:
                merged[k] = _merge_secret_map(merged.get(k) or {}, body[k])
        if "auth" in body:
            merged["auth"] = _merge_auth(merged.get("auth") or {}, body["auth"])
        new_cfg = parse_entry(name, merged)
        if new_cfg is None:
            raise HTTPException(status_code=400, detail="invalid config")
        new_list = [c if c.name != name else new_cfg for c in all_cfgs]
        status = await _restart_then_publish(
            name,
            previous=match,
            updated=new_cfg,
            configs=new_list,
            expected_revision=revision,
        )
        return JSONResponse(content=status)

    @app.delete("/api/mcp/servers/{name}")
    async def delete_one(name: str):
        all_cfgs, revision = load_configs_with_revision(include_disabled=True)
        new_list = [c for c in all_cfgs if c.name != name]
        if len(new_list) == len(all_cfgs):
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not in config")
        _save_expected(new_list, revision)
        await remove_server(name)
        return JSONResponse(content={"removed": name})

    @app.post("/api/mcp/servers/{name}/restart")
    async def restart_one(name: str):
        try:
            status = await restart_server(name)
        except KeyError:
            failure = HTTPException(
                status_code=404,
                detail=f"server '{name}' not loaded",
            )
        except Exception:  # noqa: BLE001
            failure = HTTPException(
                status_code=500,
                detail={
                    "code": "mcp_runtime_restart_failed",
                    "kind": "runtime",
                    "action": "retry_or_restart",
                },
            )
        else:
            return JSONResponse(content=status)
        raise failure from None

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

    @app.get("/api/mcp/catalog/diff")
    async def diff_catalog(url: str = ""):
        """Compare a catalog's current entries against the local
        servers that were installed from it.

        Returns ``{outdated, up_to_date, missing, orphaned, removed}``:

          * outdated   — local name → ``{old_hash, new_hash, entry}``
                         where ``entry`` is the fresh catalog config.
                         "I have a server from this catalog whose
                         upstream config drifted; offer to update."
          * up_to_date — names whose stored hash still matches.
          * missing    — catalog entries the user never installed.
          * orphaned   — local servers tagged with a different catalog
                         URL (info only — they update from their own
                         catalog, not this one).
          * removed    — local servers whose catalog URL matches but
                         the catalog no longer lists that name (entry
                         was deleted upstream; user can keep or drop).

        When ``url`` is omitted, scans every catalog URL referenced by
        any locally-installed server — handy for a global "any
        updates?" check at startup.
        """
        from openprogram.mcp.config import (
            catalog_entry_hash,
            config_to_catalog_dict,
            load_configs,
        )

        local_configs = load_configs(include_disabled=True)
        targets: list[str]
        if url:
            if not url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400,
                                    detail="url must be an http(s) URL")
            targets = [url]
        else:
            targets = sorted({
                c.source_catalog_url for c in local_configs
                if c.source_catalog_url
            })

        result: dict = {
            "outdated": {},
            "up_to_date": [],
            "missing": [],
            "orphaned": [],
            "removed": [],
            "catalog_errors": {},
        }

        # Servers with no catalog provenance can't drift — they're
        # orphaned w.r.t. any catalog. Surface for transparency.
        if url:
            result["orphaned"] = [
                c.name for c in local_configs
                if c.source_catalog_url and c.source_catalog_url != url
            ]
        else:
            result["orphaned"] = [
                c.name for c in local_configs
                if not c.source_catalog_url
            ]

        origin_counts: dict[str, int] = {}
        for cat_url in targets:
            from openprogram.security.url_policy import normalize_origin
            origin = normalize_origin(cat_url)
            ordinal = origin_counts.get(origin, 0) + 1
            origin_counts[origin] = ordinal
            safe_catalog = origin if ordinal == 1 else f"{origin}#{ordinal}"
            try:
                data = await _fetch_catalog_json(cat_url)
            except Exception as e:  # noqa: BLE001
                result["catalog_errors"][safe_catalog] = (
                    f"{type(e).__name__}: {e}"
                )
                continue
            if not isinstance(data, dict):
                result["catalog_errors"][safe_catalog] = "root is not an object"
                continue
            raw_servers = data.get("servers") or []
            if not isinstance(raw_servers, list):
                result["catalog_errors"][safe_catalog] = "servers is not a list"
                continue

            # Index catalog entries by name + compute their hashes.
            catalog_by_name: dict[str, tuple[str, dict]] = {}
            for entry in raw_servers:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                cfg = parse_entry(name.strip(), entry)
                if cfg is None:
                    continue
                fresh = config_to_catalog_dict(cfg)
                catalog_by_name[name.strip()] = (
                    catalog_entry_hash(fresh),
                    {**cfg.to_response_dict(), "name": name.strip()},
                )

            local_from_this_catalog = [
                c for c in local_configs if c.source_catalog_url == cat_url
            ]
            for cfg in local_from_this_catalog:
                pair = catalog_by_name.get(cfg.name)
                if pair is None:
                    result["removed"].append(cfg.name)
                    continue
                new_hash, fresh_entry = pair
                if cfg.source_entry_hash and cfg.source_entry_hash == new_hash:
                    result["up_to_date"].append(cfg.name)
                else:
                    result["outdated"][cfg.name] = {
                        "old_hash": cfg.source_entry_hash,
                        "new_hash": new_hash,
                        "entry": fresh_entry,
                    }

            local_names = {c.name for c in local_from_this_catalog}
            for cat_name in catalog_by_name.keys():
                if cat_name not in local_names:
                    result["missing"].append(cat_name)

        # Dedup missing across multiple catalogs (rare but possible).
        result["missing"] = sorted(set(result["missing"]))
        return JSONResponse(content=result)

    @app.post("/api/mcp/servers/{name}/update_from_catalog")
    async def update_from_catalog(name: str):
        """Re-pull this server's catalog and apply the upstream entry.

        Preserves user-local toggles (``enabled``, ``always_load``)
        while overwriting the connection / auth fields with what the
        catalog now says. Equivalent to PATCH-ing the server with the
        fresh catalog entry's body and restarting.

        404 if the local server isn't catalog-installed; 502 if the
        catalog can't be fetched or no longer lists this server.
        """
        from openprogram.mcp.config import (
            catalog_entry_hash,
            config_to_catalog_dict,
        )

        all_cfgs, revision = load_configs_with_revision(include_disabled=True)
        match = next((c for c in all_cfgs if c.name == name), None)
        if match is None:
            raise HTTPException(status_code=404,
                                detail=f"server '{name}' not in config")
        if not match.source_catalog_url:
            raise HTTPException(
                status_code=400,
                detail=(f"server '{name}' was not installed from a catalog "
                        f"— update_from_catalog only works for catalog-"
                        f"installed servers."))

        try:
            data = await _fetch_catalog_json(match.source_catalog_url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502,
                                detail=f"catalog fetch failed: {e}")

        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, list):
            raise HTTPException(status_code=502,
                                detail="catalog has no servers list")
        catalog_entry = next(
            (e for e in servers
             if isinstance(e, dict) and e.get("name") == name),
            None,
        )
        if catalog_entry is None:
            from openprogram.security.url_policy import normalize_origin

            raise HTTPException(
                status_code=502,
                detail=(f"server '{name}' no longer in catalog "
                        f"{normalize_origin(match.source_catalog_url)}"),
            )

        # Build the merged config: take catalog's connection/auth
        # fields, keep the user's local toggles.
        new_cfg = parse_entry(name, catalog_entry)
        if new_cfg is None:
            raise HTTPException(status_code=502,
                                detail="catalog entry no longer valid")
        new_cfg.enabled = match.enabled
        new_cfg.always_load = match.always_load
        new_cfg.source_catalog_url = match.source_catalog_url
        new_cfg.source_entry_hash = catalog_entry_hash(
            config_to_catalog_dict(new_cfg)
        )

        new_list = [c if c.name != name else new_cfg for c in all_cfgs]
        status = await _restart_then_publish(
            name,
            previous=match,
            updated=new_cfg,
            configs=new_list,
            expected_revision=revision,
        )
        return JSONResponse(content=status)

    @app.get("/api/mcp/catalog/suggested")
    async def mcp_catalog_suggested():
        """Return a curated list of well-known MCP catalog URLs so the
        Browse-catalog dialog has one-click options without needing
        the user to type a URL.

        The catalog files themselves are still fetched on click by the
        existing ``GET /api/mcp/catalog?url=...`` handler — this list
        just provides starting points."""
        return JSONResponse(content={
            "suggested": [
                {
                    "label": "Official MCP Registry",
                    "url": OFFICIAL_REGISTRY_URL,
                    "description": "Latest servers from the official MCP registry.",
                },
                {
                    "label": "OpenProgram bundled catalog",
                    "url": BUNDLED_CATALOG_URL,
                    "description": "Local reference servers, no network required.",
                },
            ],
            # A handful of one-click entries that don't require a separate
            # catalog fetch — useful when the user is offline or just wants
            # to install the most common server fast.
            "quick_install": [
                {
                    "name": "filesystem",
                    "description": "Read/write files in a sandboxed root directory.",
                    "type": "local",
                    # tempfile.gettempdir() → %TEMP% on Windows, /tmp on
                    # POSIX (literal "/tmp" doesn't exist on Windows).
                    "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", tempfile.gettempdir()],
                },
                {
                    "name": "git",
                    "description": "Inspect commits, branches, diffs from any git repo.",
                    "type": "local",
                    "command": ["npx", "-y", "@modelcontextprotocol/server-git"],
                },
                {
                    "name": "fetch",
                    "description": "HTTP fetch with safe parsing — read URLs the model would otherwise hallucinate.",
                    "type": "local",
                    "command": ["npx", "-y", "@modelcontextprotocol/server-fetch"],
                },
                {
                    "name": "sequential-thinking",
                    "description": "Chain-of-thought scratchpad tool — the model writes reasoning to a private buffer.",
                    "type": "local",
                    "command": ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"],
                },
            ],
        })

    @app.get("/api/mcp/catalog")
    async def fetch_catalog(url: str):
        """Pull a JSON catalog of installable MCP servers from ``url``.

        Catalog format — a single JSON object with a ``servers`` array
        of entries shaped like our local ``mcp_servers.json``
        entries (type / command / env for local; type / url / auth
        for remote). Anything beyond ``name`` + a transport-valid
        shape is dropped. Optional top-level fields:

        ``{"name": "...", "description": "...", "homepage": "...",
        "servers": [{"name": "linear", "type": "http",
                     "url": "https://mcp.linear.app/mcp", ...}]}``

        Used by the /mcp page's "Browse catalog" picker. Lazy-fetched
        per call (small response, no need to cache server-side; the
        browser caches via HTTP semantics).
        """
        if url in (BUNDLED_CATALOG_URL, "bundled"):
            data = _bundled_catalog()
        elif url.startswith(("http://", "https://")):
            try:
                data = await _fetch_catalog_json(url)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=502,
                                    detail=f"catalog parse failed: {type(e).__name__}: {e}")
        else:
            raise HTTPException(
                status_code=400,
                detail="url must be an http(s) URL",
            )

        catalog_name, catalog_desc, raw_servers = _normalize_catalog_servers(data)

        # Validate each entry against the existing parser so the UI
        # never shows an entry it couldn't actually install. We feed
        # a copy through ``parse_entry`` and drop any that don't
        # round-trip — same code that POST /api/mcp/servers uses.
        from openprogram.mcp.config import (
            catalog_entry_hash,
            config_to_catalog_dict,
        )
        valid: list[dict] = []
        for entry in raw_servers:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            cfg = parse_entry(name.strip(), entry)
            if cfg is None:
                continue
            # Echo back the (canonicalised) installable config + carry
            # any catalog-only annotations along for display. Masked
            # form: a catalog entry can ship a placeholder token, and
            # this response is a route like any other.
            out: dict = cfg.to_response_dict()
            out["name"] = name.strip()
            # Hash uses the canonical config shape so install and
            # later-diff see the same digest.
            out["source_entry_hash"] = catalog_entry_hash(
                config_to_catalog_dict(cfg)
            )
            for k in ("description", "homepage", "logo", "tags"):
                if k in entry:
                    out[k] = entry[k]
            valid.append(out)

        return JSONResponse(content={
            "catalog_name": catalog_name or url,
            "description": catalog_desc,
            "homepage": data.get("homepage") if isinstance(data, dict) else None,
            "servers": valid,
            "skipped": max(0, len(raw_servers) - len(valid)),
        })

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
            raise HTTPException(
                status_code=409,
                detail={"code": "mcp_server_unavailable", "kind": "runtime"},
            )
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
    async def test_config(body: dict, request: Request):
        """Spawn a config in a one-shot sandbox to verify the command
        actually starts up and returns a ``tools/list``. Doesn't write
        to disk and doesn't touch the live registry.

        Body: same shape as ``POST /api/mcp/servers``.
        """
        _require_local_request(request)
        cfg = _parse_body(body)
        with tempfile.TemporaryDirectory(prefix="openprogram-mcp-test-") as cwd:
            client = _one_shot_client(cfg, cwd)
            try:
                await client.start()
                ok = client.is_ready and client.error is None
                return JSONResponse(content={
                    "ok": ok,
                    "ready": client.is_ready,
                    "error": "mcp_server_unavailable" if client.error else None,
                    "tool_count": len(client.tools),
                    "tools": [t.name for t in client.tools],
                    "sandboxed": cfg.type == "local",
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


def _merge_secret_map(stored: dict, submitted: object) -> dict:
    """Apply a submitted ``env`` / ``headers`` patch to the stored map.

    Per name:

      * absent from the patch  → **preserve** the stored value
      * present with a value   → **replace** with that value
      * present, empty string  → **delete** the name

    A submitted display mask or redaction sentinel is invalid. Only omission
    preserves a stored value.
    """
    if not isinstance(submitted, dict):
        return dict(stored)
    out = dict(stored)
    for key, value in submitted.items():
        name = str(key)
        if isinstance(value, dict):
            raise HTTPException(status_code=400, detail="invalid credential value")
        if value is None or str(value) == "":
            out.pop(name, None)            # explicit empty → delete
        elif _is_redacted(value):
            raise HTTPException(status_code=400, detail="invalid credential value")
        else:
            out[name] = str(value)         # new value → replace
    return out


def _merge_auth(stored: dict, submitted: object) -> dict:
    """Apply an ``auth`` patch under the same preserve/replace/delete rule.

    Non-secret fields (``kind``, ``client_name``, ``scope``,
    ``client_id``, ``redirect_port``) replace outright. The two secrets
    — ``token`` and ``client_secret`` — preserve when omitted, replace
    when a value arrives, and delete on an explicit empty string.

    Switching ``kind`` drops the other kind's secret: a server moving
    from bearer to oauth has no use for the old bearer token, and
    keeping it around would leave a live credential nobody can see.
    """
    if not isinstance(submitted, dict):
        return dict(stored)
    secret_fields = ("token", "client_secret")
    kind = submitted.get("kind", stored.get("kind"))
    out = {k: v for k, v in stored.items() if k not in secret_fields}
    out.update({k: v for k, v in submitted.items()
                if k not in secret_fields})
    out["kind"] = kind
    keep_secrets = kind == stored.get("kind")
    for field_name in secret_fields:
        if field_name in submitted:
            value = submitted[field_name]
            if value is None or str(value) == "":
                continue                   # explicit empty → delete
            if isinstance(value, dict) or _is_redacted(value):
                raise HTTPException(
                    status_code=400,
                    detail="invalid credential value",
                )
            out[field_name] = str(value)   # new value → replace
        elif keep_secrets and field_name in stored:
            out[field_name] = stored[field_name]   # omitted → preserve
    # Response-shape presence flags are never storage fields.
    for flag in ("has_token", "masked_token",
                 "has_client_secret", "masked_client_secret",
                 "authenticated"):
        out.pop(flag, None)
    return out


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
    # Catalog provenance — preserved so /api/mcp/catalog/diff can
    # detect upstream changes later. Both fields optional; only set
    # when the install came from the marketplace path.
    if isinstance(body.get("source_catalog_url"), str):
        entry["source_catalog_url"] = body["source_catalog_url"]
    if isinstance(body.get("source_entry_hash"), str):
        entry["source_entry_hash"] = body["source_entry_hash"]
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
