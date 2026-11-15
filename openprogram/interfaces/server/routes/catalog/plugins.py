"""Plugins HTTP API。

宿主在 server.py 里调用 ``plugins.register(app)``。本模块不假设 server.py
形态，也不写 WS。
"""
from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)


def _plugin_to_dict(p) -> dict[str, Any]:
    mf = p.manifest
    return {
        "name": mf.name,
        "version": mf.version,
        "description": mf.description,
        "deprecated": mf.deprecated,
        "compatibility": mf.compatibility,
        "source": mf.source_kind,
        "manifest_form": mf.manifest_form,
        "root": mf.root,
        "entrypoints": mf.entrypoints,
        "sidebar": mf.sidebar,
        "code": list(mf.code),
        "options_schema": mf.options,
        "contributions": dict(p.ctx.contributions) if p.ctx is not None else {},
        "enabled": p.enabled,
        "loaded": p.loaded,
        "error": p.error,
    }


def _options_path(name: str) -> Path:
    from openprogram.extensions.plugins import paths as pp
    return pp.options_dir() / f"{name}.json"


def register(app):
    @app.get("/api/plugins")
    async def list_plugins_api():
        from openprogram.extensions.plugins import loader, trust
        plugs = loader.list_plugins()
        rows = []
        for p in plugs:
            row = _plugin_to_dict(p)
            row["trust"] = trust.get_trust(p.name)
            rows.append(row)
        return JSONResponse(content={"plugins": rows, "errors": loader.all_errors()})

    @app.get("/api/plugins/inspect")
    async def inspect_api():
        from openprogram.extensions.plugins.inspect import describe
        return JSONResponse(content=describe())

    @app.get("/api/plugins/marketplaces")
    async def list_marketplaces_api():
        from openprogram.extensions.plugins import marketplace
        return JSONResponse(content={"marketplaces": marketplace.list_marketplaces()})

    @app.get("/api/plugins/updates")
    async def plugin_updates_api(force: bool = False):
        """Return plugins with a newer upstream version detected by the
        autoupdate scanner. ``?force=1`` triggers a fresh scan instead
        of returning the cached result."""
        from openprogram.extensions.plugins import autoupdate as _au
        if force:
            updates = _au.force_check_now()
        else:
            updates = _au.get_available_updates()
        return JSONResponse(content={"updates": updates})

    @app.get("/api/plugins/commands")
    async def plugin_commands_api():
        """Return every slash command contributed by an enabled plugin.
        The composer's slash menu fetches this and lists the commands
        alongside built-in commands + skills."""
        from openprogram.extensions.plugins.loader import list_plugins
        out: list[dict] = []
        for p in list_plugins():
            if not p.enabled or not p.loaded:
                continue
            cmds = p.contrib.get("_commands") or []
            for c in cmds:
                out.append({
                    "plugin": p.name,
                    "name": c.get("name", ""),
                    "description": c.get("description", ""),
                    "prompt": c.get("prompt", ""),
                })
        return JSONResponse(content={"commands": out})

    @app.get("/api/plugins/builtin")
    async def builtin_plugins_api():
        """Curated built-in plugin catalog — always available even when
        no marketplace URL is registered. Mirrors skills discovery's
        suggested-sources pattern but returns full plugin entries
        directly (no separate fetch step)."""
        from openprogram.extensions.plugins import marketplace
        return JSONResponse(content={"items": marketplace.builtin_plugins()})

    @app.post("/api/plugins/marketplaces")
    async def add_marketplace_api(body: dict):
        from openprogram.extensions.plugins import marketplace
        try:
            entry = marketplace.add_marketplace(body.get("url", ""), body.get("name", ""))
        except ValueError as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)
        return JSONResponse(content=entry)

    @app.delete("/api/plugins/marketplaces/{mid}")
    async def remove_marketplace_api(mid: str):
        from openprogram.extensions.plugins import marketplace
        ok = marketplace.remove_marketplace(mid)
        return JSONResponse(content={"ok": ok})

    @app.get("/api/plugins/marketplace/{mid}/index")
    async def fetch_marketplace_index(mid: str):
        from openprogram.extensions.plugins import marketplace
        try:
            items = await marketplace.fetch_index(mid)
        except KeyError:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=502)
        return JSONResponse(content={"items": items})

    @app.post("/api/plugins/install")
    async def install_api(body: dict):
        from openprogram.extensions.plugins import installer, loader
        source = str(body.get("source", ""))
        spec = str(body.get("spec", ""))
        ref = body.get("ref")
        result = installer.install(source, spec, ref=ref)
        # 重新扫描
        loader.rescan()
        if result.get("success"):
            from openprogram.runtime.events import emit_ws_frame
            emit_ws_frame({"type": "plugins:changed", "data": {"plugin": result.get("name", "")}})
        return JSONResponse(content=result)

    @app.get("/api/plugins/{name}")
    async def get_plugin_api(name: str):
        from openprogram.extensions.plugins import paths as pp, loader, trust
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        p = loader.get_plugin(name)
        if not p:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        row = _plugin_to_dict(p)
        row["trust"] = trust.get_trust(name)
        return JSONResponse(content=row)

    @app.post("/api/plugins/{name}/uninstall")
    async def uninstall_api(name: str):
        from openprogram.extensions.plugins import paths as pp, installer, loader
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        # 卸载前先 unload
        try:
            loader.unload_plugin(name)
        except Exception:
            # The plugin may not be loaded; uninstall proceeds either way.
            _log.debug("plugin %s unload before uninstall failed", name, exc_info=True)
        result = installer.uninstall(name)
        loader.rescan()
        return JSONResponse(content=result)

    @app.post("/api/plugins/{name}/toggle")
    async def toggle_api(name: str, body: dict):
        from openprogram.extensions.plugins import paths as pp, loader
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        enabled = bool(body.get("enabled", False))
        try:
            if enabled:
                p = loader.load_plugin(name)
            else:
                p = loader.unload_plugin(name)
        except KeyError:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        except PermissionError as e:
            from openprogram.extensions.plugins.manifest import PluginContainsCode
            code = "contains_code" if isinstance(e, PluginContainsCode) else "trust"
            return JSONResponse(content={"error": str(e), "code": code}, status_code=403)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
        return JSONResponse(content=_plugin_to_dict(p))

    @app.post("/api/plugins/{name}/reload")
    async def reload_api(name: str):
        from openprogram.extensions.plugins import paths as pp, loader
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        try:
            p = loader.reload_plugin(name)
        except KeyError:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)
        return JSONResponse(content=_plugin_to_dict(p))

    @app.post("/api/plugins/{name}/validate")
    async def validate_api(name: str):
        """Dry-run：解析 manifest + 入口文件存在性 + compatibility，不真正 load。"""
        from openprogram.extensions.plugins import paths as pp, loader, manifest as mfmod
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        p = loader.get_plugin(name)
        if not p:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        checks: list[dict[str, Any]] = []
        mf = p.manifest
        checks.append({"name": "manifest", "ok": True, "detail": mf.manifest_form})
        checks.append({"name": "declarative", "ok": not mf.code,
                       "detail": mfmod.code_refusal(mf) or "skills, commands, agents, hooks, MCP servers, providers"})
        ok, reason = mfmod.check_compatibility(mf.compatibility, "0.1.0")
        checks.append({"name": "compatibility", "ok": ok, "detail": reason})
        # 入口文件存在性
        base = Path(mf.root) if mf.root else Path.cwd()
        for k, v in (mf.entrypoints or {}).items():
            if k.startswith("_"):
                continue
            if isinstance(v, str):
                target = Path(v)
                if not target.is_absolute():
                    target = base / target
                checks.append({"name": f"entry:{k}", "ok": target.exists(), "detail": str(target)})
        return JSONResponse(content={"checks": checks, "all_ok": all(c["ok"] for c in checks)})

    @app.get("/api/plugins/{name}/options")
    async def get_options(name: str):
        from openprogram.extensions.plugins import paths as pp
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        f = _options_path(name)
        if not f.exists():
            return JSONResponse(content={"options": {}})
        try:
            return JSONResponse(content={"options": json.loads(f.read_text(encoding="utf-8"))})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post("/api/plugins/{name}/options")
    async def set_options(name: str, body: dict):
        from openprogram.extensions.plugins import paths as pp
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        opts = body.get("options", {})
        _options_path(name).write_text(
            json.dumps(opts, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return JSONResponse(content={"ok": True})

    @app.post("/api/plugins/{name}/trust")
    async def set_trust_api(name: str, body: dict):
        from openprogram.extensions.plugins import paths as pp, trust
        try:
            pp.sanitize_name(name)
        except ValueError:
            return JSONResponse(content={"error": "invalid name"}, status_code=400)
        level = str(body.get("level", "")).strip()
        try:
            trust.set_trust(name, level)
        except ValueError as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)
        return JSONResponse(content={"ok": True, "level": level})
