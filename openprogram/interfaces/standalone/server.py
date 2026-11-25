"""The standalone host: one application package, its runtime and its page.

``serve`` runs the server in the foreground; ``launch`` (what a launcher
runs) starts it in the background when it is not running yet and opens
its window: on macOS a native one in the launcher's process
(:mod:`.window`), elsewhere a browser's. The server

- keeps everything in the data folder (``OPENPROGRAM_STATE_DIR``) and
  takes the computer's model and voice settings first (:mod:`.settings`);
- mounts the package as the App mounts a built-in application and opens
  its one instance;
- serves the application routes the App's worker serves
  (``routes/catalog/applications.py``), the page that frames the window
  (``shell/``), a stream of change events and the application's
  conversations;
- listens on 127.0.0.1 only, answers a request only with the session
  cookie the launcher's link signs in, and only for its own host name;
- stops by itself once no window has been open for ``--idle-minutes``
  and nothing records or runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Module level: the routes' annotations (``Request``) resolve here.
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

SHELL = Path(__file__).parent / "shell"
COOKIE = "openprogram_standalone"
#: Paths a page reaches before it is signed in: the shell itself, sign-in
#: and the window's resources, which carry their own read-only token.
OPEN_PATHS = ("/", "/api/standalone/sign-in", "/api/standalone/health")
OPEN_PREFIXES = ("/shell/", "/application-assets/")


def _runtime_folder(data: Path) -> Path:
    folder = data / "standalone"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(data, 0o700)
    return folder


def token(data: Path) -> str:
    path = _runtime_folder(data) / "token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def _server_file(data: Path) -> Path:
    return _runtime_folder(data) / "server.json"


def running(data: Path) -> int | None:
    """The port of this application's server when it answers, else ``None``."""
    try:
        port = int(json.loads(_server_file(data).read_text(encoding="utf-8"))["port"])
        # Its own loopback port: never through a proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/standalone/health", timeout=2) as response:
            if json.loads(response.read()).get("data") == str(data.resolve()):
                return port
    except (OSError, ValueError, KeyError):
        pass
    return None


# --------------------------------------------------------------------------- the app

def build(package: Path, data: Path, *, idle_minutes: float = 10.0):
    """The ASGI app serving ``package``; the data folder must already be set."""

    import openprogram.interfaces.speech  # noqa: F401 — registers speech to text with the core
    from openprogram.host._platform import changes
    from openprogram.interfaces.server.routes.catalog import applications
    from openprogram.programs._host.registry import program_context
    from openprogram.programs._host.ui import audio, catalog, loader, state
    from . import conversations

    definition = catalog.manifest(package)
    if definition["scope"] != "global":
        raise ValueError("only applications with scope global run on their own")
    if "chat" in definition["capabilities"]:
        raise ValueError("capability chat needs the App's composer; it does not run on its own")
    conversations.install()
    context = program_context(f"application:{definition['id']}", definition["id"], str(package))
    definition = loader.mount(context, package)
    instance = state.instance(definition)
    secret = token(data)
    activity = {"windows": 0, "since": time.monotonic()}

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    applications.register(app)
    app.state.standalone = {"definition": definition, "instance": instance, "activity": activity,
                            "idle_seconds": idle_minutes * 60}

    @app.middleware("http")
    async def signed_in(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"error": "unknown host"}, status_code=403)
        path = request.url.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return await call_next(request)
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), secret):
            return JSONResponse({"error": "sign in from the application's launcher"}, status_code=401)
        return await call_next(request)

    no_store = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    @app.get("/")
    async def shell_page():
        html = (SHELL / "index.html").read_text(encoding="utf-8")
        title = definition.get("display_title") or definition["title"]
        html = html.replace("{{title}}", title.replace("&", "&amp;").replace("<", "&lt;"))
        return Response(html, media_type="text/html", headers={
            **no_store, "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"})

    @app.get("/shell/{relative:path}")
    async def shell_file(relative: str):
        target = (SHELL / relative).resolve()
        if SHELL.resolve() not in target.parents or not target.is_file() or target.name == "index.html":
            return JSONResponse({"error": "not found"}, status_code=404)
        import mimetypes
        kind = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2"}.get(target.suffix) \
            or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=kind, headers=no_store)

    @app.get("/api/standalone/health")
    async def health():
        return {"ok": True, "data": str(data.resolve()), "application": definition["id"]}

    @app.post("/api/standalone/sign-in")
    async def sign_in(body: dict):
        if not secrets.compare_digest(str(body.get("token") or ""), secret):
            return JSONResponse({"error": "this link is not this application's"}, status_code=401)
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, secret, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/standalone")
    async def describe():
        current = catalog.get(definition["id"])
        from urllib.parse import quote
        address = (f"/application-assets/{instance['id']}/{current['digest']}/"
                   f"{state.asset_token(instance['id'], current['digest'])}/{quote(current['ui']['entry'], safe='/')}")
        return {"instance_id": instance["id"], "application": current, "ui_url": address}

    @app.get("/api/standalone/conversations/{session_id}")
    async def conversation(session_id: str):
        if not state.started_conversation(instance["id"], session_id):
            return JSONResponse({"error": "unknown conversation"}, status_code=404)
        return conversations.describe(session_id)

    @app.post("/api/standalone/conversations/{session_id}/messages")
    async def follow_up(session_id: str, body: dict):
        from openprogram.programs._host.ui.host_calls import HostCallError
        try:
            return await asyncio.to_thread(conversations.ask, instance, {"session_id": session_id, "text": body.get("text")})
        except HostCallError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/standalone/events")
    async def events(request: Request):
        """Server-sent events: ``state``, ``audio`` and ``conversation`` changes of this instance."""
        listener = changes.listen("application-state", "application-audio", "standalone-conversation")
        activity["windows"] += 1

        async def stream():
            try:
                yield "retry: 1000\n\n"
                while not await request.is_disconnected():
                    marks = await asyncio.to_thread(listener.wait, 15)
                    sent = False
                    for mark in sorted(marks):
                        topic, _, key = mark.partition(":")
                        if topic == "application-state" and key == instance["id"]:
                            yield "event: state\ndata: {}\n\n"
                        elif topic == "application-audio" and key == instance["id"]:
                            yield "event: audio\ndata: {}\n\n"
                        elif topic == "standalone-conversation":
                            yield f"event: conversation\ndata: {json.dumps(key)}\n\n"
                        else:
                            continue
                        sent = True
                    if not sent:
                        yield ": still here\n\n"
            finally:
                listener.close()
                activity["windows"] -= 1
                activity["since"] = time.monotonic()

        return StreamingResponse(stream(), media_type="text/event-stream", headers=no_store)

    def busy() -> bool:
        if activity["windows"] > 0 or app.state.applications.tasks:
            return True
        try:
            status = audio.status(instance["id"])
        except Exception:  # noqa: BLE001 — an unreadable recording counts as one still going
            return True
        return bool(status and status["state"] in ("recording", "processing"))

    app.state.standalone["busy"] = busy
    return app


def serve(package: Path, data: Path, *, settings_from: Path | None, port: int = 0,
          idle_minutes: float = 10.0) -> None:
    data.mkdir(parents=True, exist_ok=True)
    from . import settings
    settings.sync(settings_from, data)
    import uvicorn

    app = build(package, data, idle_minutes=idle_minutes)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    chosen = sock.getsockname()[1]
    server_file = _server_file(data)
    server_file.write_text(json.dumps({"port": chosen, "pid": os.getpid()}), encoding="utf-8")
    # Open windows hold event streams: stopping does not wait for them long.
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", timeout_graceful_shutdown=3))

    def watch_idle() -> None:
        while not server.should_exit:
            time.sleep(5)
            standalone = app.state.standalone
            if standalone["busy"]():
                standalone["activity"]["since"] = time.monotonic()
            elif time.monotonic() - standalone["activity"]["since"] > standalone["idle_seconds"]:
                server.should_exit = True

    threading.Thread(target=watch_idle, name="standalone-idle", daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        try:
            if json.loads(server_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                server_file.unlink()
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- launch

CHROMIUM_MAC = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc")


def open_window(url: str) -> None:
    """Where no native window can be drawn (``window.py``): a Chromium app window, else the default browser."""
    if sys.platform == "darwin":
        for name in CHROMIUM_MAC:
            if Path(f"/Applications/{name}.app").is_dir() and name != "Arc":
                subprocess.Popen(["open", "-na", name, "--args", f"--app={url}"])
                return
        subprocess.Popen(["open", url])
        return
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "", "msedge", f"--app={url}"], shell=False)
        return
    import shutil
    for command in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        if shutil.which(command):
            subprocess.Popen([command, f"--app={url}"], start_new_session=True)
            return
    import webbrowser
    webbrowser.open(url)


def launch(package: Path, data: Path, *, settings_from: Path | None, idle_minutes: float,
           open_it: bool = True, wait_s: float = 60.0) -> str:
    """Start the server unless it runs; open its window. Returns the window's address."""
    port = running(data)
    if port is None:
        log = _runtime_folder(data) / "server.log"
        command = [sys.executable, "-m", "openprogram.interfaces.standalone", "serve",
                   "--package", str(package), "--data", str(data), "--idle-minutes", str(idle_minutes)]
        if settings_from is not None:
            command += ["--settings-from", str(settings_from)]
        with open(log, "ab") as output:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             start_new_session=True, close_fds=True, env=dict(os.environ))
        deadline = time.monotonic() + wait_s
        while port is None:
            if time.monotonic() > deadline:
                raise RuntimeError(f"the application did not start; see {log}")
            time.sleep(0.3)
            port = running(data)
    url = f"http://127.0.0.1:{port}/#token={token(data)}"
    if open_it:
        from . import window
        if window.available():
            window.run(url, _title(package))
        else:
            open_window(url)
    return url


def _title(package: Path) -> str:
    try:
        manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
        return str(manifest.get("display_title") or manifest.get("title") or package.name)
    except (OSError, ValueError):
        return package.name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m openprogram.interfaces.standalone")
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("serve", "launch"):
        p = sub.add_parser(verb)
        p.add_argument("--package", required=True, type=Path, help="The application package folder")
        p.add_argument("--data", required=True, type=Path, help="The application's data folder")
        p.add_argument("--settings-from", type=Path, default=None,
                       help="An OpenProgram state folder to take model and voice settings from")
        p.add_argument("--idle-minutes", type=float, default=10.0,
                       help="Stop after this long with no window, recording or running operation")
        if verb == "serve":
            p.add_argument("--port", type=int, default=0)
        else:
            p.add_argument("--no-open", action="store_true", help="Print the address instead of opening it")
    args = parser.parse_args(argv)
    package, data = args.package.expanduser().resolve(), args.data.expanduser().absolute()
    # Before anything imports ``openprogram.programs``, which loads every
    # Program of the state folder it finds: here only this package, and
    # the state folder is the application's data folder.
    if "openprogram.programs" in sys.modules:
        raise RuntimeError("start the standalone host in a process of its own")
    os.environ["OPENPROGRAM_STATE_DIR"] = str(data)
    os.environ["OPENPROGRAM_STANDALONE_APPLICATION"] = str(package)
    if args.verb == "serve":
        serve(package, data, settings_from=args.settings_from, port=args.port, idle_minutes=args.idle_minutes)
    else:
        url = launch(package, data, settings_from=args.settings_from, idle_minutes=args.idle_minutes,
                     open_it=not args.no_open)
        if args.no_open:
            print(url)


__all__ = ["build", "launch", "main", "open_window", "running", "serve", "token"]
