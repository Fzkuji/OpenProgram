"""Owner-authenticated application catalog and instance APIs."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from urllib.parse import quote
from contextlib import asynccontextmanager

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response

from openprogram.programs._host.ui import catalog, loader, state
from openprogram.programs._host.ui.service import ApplicationService


CSP = "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"


def register(app):
    service = ApplicationService()
    app.state.applications = service
    previous_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(current):
        async with previous_lifespan(current) as value:
            service.recover()
            # Installed applications load in the worker, from their sources.
            await asyncio.to_thread(loader.sync)
            try:
                yield value
            finally:
                await service.close()
                await asyncio.to_thread(loader.unload_all)
    app.router.lifespan_context = lifespan

    async def guarded(action):
        try:
            value = await action()
            return JSONResponse(value)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            from jsonschema.exceptions import ValidationError, SchemaError
            if isinstance(exc, (ValidationError, SchemaError)):
                return JSONResponse({"error": exc.message}, status_code=400)
            raise

    @app.get("/api/applications")
    async def listing():
        from openprogram.programs._host.registry import builtin_application_states
        return {"applications": catalog.installed(), "builtin": builtin_application_states()}

    @app.post("/api/applications/install")
    async def install(body: dict):
        async def apply():
            async with service.lock:
                result = await asyncio.to_thread(catalog.install, body["path"], replace=body.get("replace", False), trust=body.get("trust", False))
                if await asyncio.to_thread(loader.reload, result["id"]) is None:
                    raise ValueError(loader.error(result["id"]) or "application did not load")
                return result
        return await guarded(apply)

    @app.patch("/api/applications/{app_id}")
    async def configure(app_id: str, body: dict):
        async def apply():
            async with service.lock:
                if body.get("enabled") is False:
                    await service.revoke(app_id)
                result = catalog.configure(app_id, **body)
                # Disable disposes the application's context; enable loads it.
                if "enabled" in body or "display_title" in body:
                    await asyncio.to_thread(loader.reload, app_id)
                return result
        return await guarded(apply)

    @app.delete("/api/applications/{app_id}")
    async def remove(app_id: str):
        async def apply():
            async with service.lock:
                await service.revoke(app_id)
                catalog.remove(app_id)
                await asyncio.to_thread(loader.unload, app_id)
                return {"ok": True}
        return await guarded(apply)

    def ui_url(key, definition):
        return f"/application-assets/{key}/{definition['digest']}/{state.asset_token(key, definition['digest'])}/{quote(definition['ui']['entry'], safe='/')}"

    @app.get("/api/applications/{app_id}/launch-context")
    async def launch_context(app_id: str):
        async def apply():
            catalog.get(app_id)
            from pathlib import Path
            from openprogram.runtime.permissions.authority import owner_principal_id
            from openprogram.state.store.project import list_projects
            projects = [{"id": p.id, "name": p.name, "path": p.path} for p in list_projects()
                        if p.path and not p.hidden and Path(p.path).is_dir()]
            valid = {p["id"] for p in projects}
            with state.connect() as db:
                bindings = [r[0] for r in db.execute("SELECT project_id FROM instances WHERE app_id=? AND owner=? ORDER BY rowid DESC",
                                                    (app_id, owner_principal_id())) if r[0] in valid]
            return {"projects": projects, "bindings": bindings}
        return await guarded(apply)

    @app.post("/api/applications/{app_id}/open")
    async def open_application(app_id: str, body: dict):
        """Open an instance; with ``session_id`` the window belongs to that chat.

        While a session holds a window of the application, the session's
        deferred-tool catalog says it is open and how to load its entries;
        opening it loads nothing (deferred-tools.html).
        """
        async def apply():
            definition = catalog.get(app_id)
            instance = state.instance(definition, body.get("project_id", ""))
            if body.get("session_id"):
                from openprogram.programs._host.ui.bindings import bind
                bind(str(body["session_id"]), instance["id"])
            return {"instance_id": instance["id"], "application": definition,
                    "ui_url": ui_url(instance["id"], definition)}
        return await guarded(apply)

    @app.post("/api/applications/{app_id}/close")
    async def close_application(app_id: str, body: dict):
        """The window of ``instance_id`` in ``session_id`` closed."""
        async def apply():
            from openprogram.programs._host.ui.bindings import release
            instance = state.get_instance(str(body["instance_id"]))
            if instance["app_id"] != app_id:
                raise ValueError("instance belongs to another application")
            release(str(body["session_id"]), instance["id"])
            return {"ok": True}
        return await guarded(apply)

    @app.get("/api/application-instances/{key}")
    async def instance_info(key: str):
        async def apply():
            instance = state.get_instance(key)
            definition = catalog.get(instance["app_id"])
            with state.connect() as db:
                ids = [r[0] for r in db.execute("SELECT id FROM operations WHERE instance_id=? ORDER BY rowid DESC LIMIT 100", (key,))]
            return {"instance": instance, "application": definition, "ui_url": ui_url(key, definition), "runs": [service.describe(i) for i in ids]}
        return await guarded(apply)

    @app.post("/api/application-instances/{key}/operations/{operation}")
    async def invoke(key: str, operation: str, body: dict):
        async def apply():
            current = catalog.get(state.get_instance(key)["app_id"])
            if body.get("digest") != current["digest"]:
                raise ValueError("application was updated; reopen before starting a new operation")
            return await service.submit(key, operation, body.get("input", {}), body.get("request_key", ""), agent=body.get("agent", False))
        return await guarded(apply)

    @app.get("/api/application-instances/{key}/state")
    async def load_state(key: str):
        async def apply():
            definition = catalog.get(state.get_instance(key)["app_id"])
            if "storage.app" not in definition["capabilities"]:
                raise ValueError("application does not declare storage.app")
            return state.data(key)
        return await guarded(apply)

    @app.put("/api/application-instances/{key}/state")
    async def save_state(key: str, body: dict):
        async def apply():
            definition = catalog.get(state.get_instance(key)["app_id"])
            if "storage.app" not in definition["capabilities"] or type(body.get("version")) is not int:
                raise ValueError("storage capability and expected version required")
            return state.data(key, body.get("value"), expected_version=body["version"])
        return await guarded(apply)

    @app.get("/api/application-runs/{run_id}")
    async def run(run_id: str, after: int = 0):
        async def apply():
            info = service.describe(run_id)
            with state.connect() as db:
                rows = db.execute("SELECT sequence,payload FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 200", (run_id, after)).fetchall()
            return {**info, "events": [{"sequence": r[0], **json.loads(r[1])} for r in rows]}
        return await guarded(apply)

    @app.post("/api/application-runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await guarded(lambda: service.cancel(run_id))

    @app.post("/api/application-runs/{run_id}/answer")
    async def answer(run_id: str, body: dict):
        return await guarded(lambda: service.answer(run_id, body.get("request_id", ""), body.get("answer")))

    @app.get("/application-assets/{key}/{digest}/{token}/{relative:path}")
    async def resource(key: str, digest: str, token: str, relative: str, request: Request):
        try:
            definition = state.asset_definition(key, digest, token)
            if digest != definition["digest"]:
                raise FileNotFoundError("application version changed; reopen application")
            # The UI is served from the application's own directory while
            # its context keeps it loaded under this digest.
            from pathlib import Path
            root = catalog.contained(Path(definition["path"]), definition["ui"]["root"])
            target = catalog.contained(root, relative)
            if not target.is_file():
                raise FileNotFoundError("application asset not found")
            asset_base = str(request.base_url).rstrip('/') + f"/application-assets/{key}/{digest}/{token}/"
            policy = CSP.replace("connect-src 'none'", "connect-src " + asset_base) + "; frame-ancestors 'self'"
            headers = {"Access-Control-Allow-Origin": "*", "Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
            if target.suffix.lower() == ".html":
                sdk = (Path(__file__).parent.parent / "application-sdk.js").read_text(encoding="utf-8")
                html = target.read_text(encoding="utf-8")
                bootstrap = "<script>" + sdk + "</script>"
                doctype = re.match(r"\s*<!doctype[^>]*>", html, re.IGNORECASE)
                offset = doctype.end() if doctype else 0
                return Response(html[:offset] + bootstrap + html[offset:], media_type="text/html", headers=headers)
            return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", headers=headers)
        except FileNotFoundError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
