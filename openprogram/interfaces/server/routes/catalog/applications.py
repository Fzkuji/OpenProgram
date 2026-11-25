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

from openprogram.programs._host.ui import audio, catalog, loader, state
from openprogram.programs._host.ui.service import ApplicationService


# Application windows ship their own fonts (font-src 'self'); not every system maps the type.
mimetypes.add_type("font/woff2", ".woff2")

CSP = "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"


def register(app):
    service = ApplicationService()
    # The CLI installs, enables and removes applications by writing the
    # catalog; open Launchpads hear of it from the file.
    from openprogram.host._platform import changes
    catalog_file = catalog.catalog_store.path()
    changes.watch(catalog_file.parent, "applications", names={catalog_file.name})
    app.state.applications = service
    previous_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(current):
        async with previous_lifespan(current) as value:
            service.recover()
            # Recordings hand their chunks to operations through this service.
            audio.attach(service, asyncio.get_running_loop())
            # Installed applications load in the worker, from their sources.
            await asyncio.to_thread(loader.sync)
            try:
                yield value
            finally:
                await asyncio.to_thread(audio.stop_all)
                audio.detach()
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

    # Software of its own (interfaces/standalone/export.py): the window
    # writes it into a folder the owner chose and may then show it there.
    exported: set[str] = set()

    @app.post("/api/applications/{app_id}/export")
    async def export_application(app_id: str, body: dict):
        async def apply():
            import sys
            from pathlib import Path
            from openprogram.interfaces.standalone.export import export
            folder = str(body.get("output") or "").strip() or "~/Downloads"
            if not Path(folder).expanduser().is_dir():
                raise ValueError(f"{folder} is not a folder")
            # The page draws the application's icon and sends it as a PNG data URL.
            icon = None
            drawn = str(body.get("icon") or "")
            if drawn.startswith("data:image/png;base64,"):
                import base64
                import binascii
                try:
                    icon = base64.b64decode(drawn.split(",", 1)[1], validate=True)
                except binascii.Error:
                    raise ValueError("the icon is not a PNG") from None
            try:
                result = await asyncio.to_thread(
                    export, app_id, Path(folder), format="app" if sys.platform == "darwin" else "dir",
                    next_free=True, icon=icon)
            except (PermissionError, FileExistsError) as exc:  # the folder chosen: say so beside the form
                raise ValueError(str(exc)) from None
            exported.add(result["path"])
            return result
        return await guarded(apply)

    @app.post("/api/applications/exports/reveal")
    async def reveal_export(body: dict):
        """Show an export this worker wrote in the file manager; no other path."""
        async def apply():
            path = str(body.get("path") or "")
            if path not in exported:
                raise FileNotFoundError("not an export of this session")
            from openprogram.interfaces.server.ws_actions.files.mutations import reveal_in_file_manager
            result = await asyncio.to_thread(reveal_in_file_manager, path)
            if result.get("error"):
                raise ValueError(result["error"])
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
            from openprogram.state.store.project.project_store import folder_is_here
            # An application runs on this computer: only folders here can be its project.
            projects = [{"id": p.id, "name": p.name, "path": p.path} for p in list_projects()
                        if p.path and not p.hidden and folder_is_here(p) and Path(p.path).is_dir()]
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
            # A window is opening: its first operations should not wait for Python.
            service.prepare_soon(definition)
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

    @app.post("/api/application-instances/{key}/conversations")
    async def conversation(key: str, body: dict):
        """The conversation the window keeps under ``body.key``, working in
        ``body.folder`` (``state.conversation``)."""
        return await guarded(lambda: asyncio.to_thread(
            state.conversation, key, body.get("key"), body.get("folder") or ""))

    @app.post("/api/application-instances/{key}/conversations/{session_id}/show")
    async def show_conversation(key: str, session_id: str):
        """Whether the window may show ``session_id``: only a conversation its backend opened."""
        async def apply():
            definition = catalog.get(state.get_instance(key)["app_id"])
            if "conversation" not in definition["capabilities"]:
                raise ValueError("application does not declare conversation")
            if not state.started_conversation(key, session_id):
                raise FileNotFoundError("the application did not open this conversation")
            return {"session_id": session_id}
        return await guarded(apply)

    # Recordings (capability audio.record): the host records, the application's operations take the chunks.
    @app.get("/api/application-instances/{key}/audio")
    async def audio_status(key: str):
        async def apply():
            return {"recording": await asyncio.to_thread(audio.status, key), "system_audio": audio.support()}
        return await guarded(apply)

    @app.post("/api/application-instances/{key}/audio")
    async def audio_start(key: str, body: dict):
        async def apply():
            return await asyncio.to_thread(audio.start, key, body.get("key"),
                                           tracks=body.get("tracks") or audio.TRACKS,
                                           max_seconds=body.get("max_seconds") or audio.MAX_SECONDS)
        return await guarded(apply)

    @app.post("/api/application-instances/{key}/audio/{recording_id}/chunks")
    async def audio_chunk(key: str, recording_id: str, request: Request):
        async def apply():
            params = request.query_params
            try:
                seq = int(params.get("seq", ""))
                t_ms = int(float(params.get("t_ms", "")))
            except ValueError:
                raise ValueError("seq and t_ms must be numbers") from None
            content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            suffix = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
                      "audio/wav": "wav", "audio/x-wav": "wav"}.get(content_type, "webm")
            data = await request.body()
            return await asyncio.to_thread(audio.take, key, recording_id, params.get("track", ""), seq, t_ms,
                                           data, suffix)
        return await guarded(apply)

    @app.post("/api/application-instances/{key}/audio/{recording_id}/stop")
    async def audio_stop(key: str, recording_id: str):
        return await guarded(lambda: asyncio.to_thread(audio.stop, key, recording_id, "done"))

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
            # The frame's origin is opaque (sandbox), and WebKit reads 'self'
            # there as that opaque origin, refusing the application's own
            # scripts: name the application's folder and the host as well.
            origin = str(request.base_url).rstrip('/')
            policy = CSP.replace("connect-src 'none'", "connect-src " + asset_base) \
                .replace("'self'", "'self' " + asset_base) + "; frame-ancestors 'self' " + origin
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
