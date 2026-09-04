"""Serve the Next.js static export (``apps/web/out/``) from the worker itself.

Single-port architecture (docs/reference/design/cli/single-port.md): the
FastAPI backend is the sole origin. ``mount_frontend`` registers, LAST in
``create_app()``, a catch-all GET that serves files from ``apps/web/out/`` and
falls back to the SPA shell for client-routed paths — so every /api, /ws,
/docs, /files route registered before it always wins.

``ensure_frontend_built`` is the build gate: called by the worker at
startup, it rebuilds ``out/`` with ``npx next build`` when it is missing
or older than the ``web/`` sources. Node is a build-time dependency only;
a machine with a prebuilt ``out/`` never needs npm.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def web_dir() -> Path:
    """Repo-root ``apps/web`` directory for a source checkout."""
    # apps/server/openprogram_server/_webui/frontend.py → repo_root/apps/web
    return Path(__file__).resolve().parents[3] / "web"


def packaged_out_dir() -> Path:
    """Static export installed as package data in release wheels."""
    return Path(__file__).resolve().with_name("_frontend")


def repo_out_dir() -> Path:
    return web_dir() / "out"


def out_dir() -> Path:
    """Use package data outside a source checkout; keep source builds live."""
    bundled = packaged_out_dir()
    if (bundled / "index.html").is_file() and not (web_dir() / "package.json").is_file():
        return bundled
    return repo_out_dir()


# --- build gate -------------------------------------------------------------

# Source roots whose newest mtime invalidates the export.
_SOURCE_ROOTS = ("app", "components", "lib", "package.json")

_PRECOMPRESSED_MEDIA_TYPES = frozenset({
    "application/gzip",
    "application/pdf",
    "application/x-7z-compressed",
    "application/x-gzip",
    "application/x-rar-compressed",
    "application/zip",
    "font/woff",
    "font/woff2",
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/x-icon",
})


def _is_precompressed_media_type(value: str) -> bool:
    media_type = value.partition(";")[0].strip().lower()
    return (
        media_type in _PRECOMPRESSED_MEDIA_TYPES
        or media_type.startswith(("audio/", "video/"))
    )


def _accepts_gzip(value: str) -> bool:
    """Select gzip with explicit coding precedence over ``*``."""
    explicit: list[float] = []
    wildcard: list[float] = []
    for member in value.split(","):
        parts = [part.strip() for part in member.split(";")]
        coding = parts[0].lower()
        if coding not in {"gzip", "*"}:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, raw = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(raw.strip())
                except ValueError:
                    quality = 0.0
                if not 0.0 <= quality <= 1.0:
                    quality = 0.0
                break
        (explicit if coding == "gzip" else wildcard).append(quality)
    selected = explicit if explicit else wildcard
    return bool(selected and max(selected) > 0.0)


def _newest_source_mtime(wd: Path) -> float:
    newest = 0.0
    for name in _SOURCE_ROOTS:
        p = wd / name
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                try:
                    if f.is_file():
                        newest = max(newest, f.stat().st_mtime)
                except OSError:
                    continue
    return newest


def _run(cmd: list[str], wd: Path, what: str) -> None:
    from openprogram._compat import node_tool_cmd
    r = subprocess.run(
        node_tool_cmd(cmd), cwd=str(wd), capture_output=True, text=True,
    )
    if r.returncode != 0:
        tail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()[-2000:]
        raise RuntimeError(f"{what} failed (rc={r.returncode}):\n{tail}")


def ensure_frontend_built() -> None:
    """Build ``apps/web/out/`` if missing or stale. Raises on build failure.

    Freshness contract: ``out/index.html`` exists (export completed) and
    is no older than the newest file under app/, components/, lib/ or
    package.json. No BUILD_ID dance — the export has no server process
    caching manifests, files on disk are the whole truth.
    """
    wd = web_dir()
    marker = out_dir() / "index.html"
    if wd.exists():
        stale = (
            not marker.exists()
            or marker.stat().st_mtime < _newest_source_mtime(wd)
        )
    else:
        stale = False  # installed package without sources — serve what's there
    if not stale:
        if not marker.exists():
            raise RuntimeError(
                f"frontend export not found at {out_dir()} and web/ sources "
                "are unavailable — reinstall with a prebuilt apps/web/out/."
            )
        return

    if shutil.which("npm") is None:
        if marker.exists():
            print("[worker] web: npm not found — serving existing (stale) apps/web/out/")
            return
        raise RuntimeError(
            f"frontend export missing at {out_dir()} and npm is not in PATH. "
            "Install Node.js (build-time only) or ship a prebuilt apps/web/out/."
        )

    repo_root = wd.parents[1]
    print("[worker] web: verifying npm workspace deps…")
    _run(["npm", "install", "--silent"], repo_root, "npm install")
    print("[worker] web: building frontend export (npm workspace build)…")
    _run(
        ["npm", "run", "build", "--workspace", "apps/web"],
        repo_root,
        "npm workspace build",
    )
    if not marker.exists():
        raise RuntimeError(f"next build succeeded but {marker} was not produced")
    print("[worker] web: frontend export ready")


# --- serving ----------------------------------------------------------------


def mount_frontend(app) -> None:
    """Register static serving + SPA fallback. Call LAST in create_app()."""
    from fastapi.responses import FileResponse, PlainTextResponse
    from starlette.datastructures import Headers
    from starlette.middleware.gzip import (
        GZipMiddleware,
        GZipResponder,
        IdentityResponder,
    )

    class _MimeAwareGZipResponder(GZipResponder):
        async def send_with_compression(self, message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = Headers(raw=message["headers"])
                if _is_precompressed_media_type(headers.get("content-type", "")):
                    self.initial_message = message
                    self.content_encoding_set = "content-encoding" in headers
                    self.content_type_is_excluded = True
                    return
            await super().send_with_compression(message)

    class _MimeAwareGZipMiddleware(GZipMiddleware):
        async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            headers = Headers(scope=scope)
            if _accepts_gzip(headers.get("Accept-Encoding", "")):
                responder = _MimeAwareGZipResponder(
                    self.app,
                    self.minimum_size,
                    compresslevel=self.compresslevel,
                )
            else:
                responder = IdentityResponder(self.app, self.minimum_size)
            await responder(scope, receive, send)

    app.add_middleware(_MimeAwareGZipMiddleware, minimum_size=500, compresslevel=6)

    out = out_dir()

    def _file_response(path: Path) -> FileResponse:
        if path.suffix == ".html":
            headers = {"Cache-Control": "no-cache"}
        elif "/_next/static/" in path.as_posix():
            # Content-hashed filenames — safe to cache forever.
            headers = {"Cache-Control": "public, max-age=31536000, immutable"}
        else:
            headers = {"Cache-Control": "no-cache"}
        return FileResponse(path, headers=headers)

    def _spa_fallback(rel: str):
        # /skills/foo → skills.html, /settings/providers/x →
        # settings/providers.html … nearest ancestor page, then the chat
        # shell (the app's home), then the export root.
        parts = [p for p in rel.split("/") if p]
        while parts:
            candidate = out / ("/".join(parts) + ".html")
            if candidate.is_file():
                return _file_response(candidate)
            parts.pop()
        for name in ("chat.html", "index.html"):
            candidate = out / name
            if candidate.is_file():
                return _file_response(candidate)
        return PlainTextResponse(
            "frontend not built — run `npx next build` in web/ or restart the worker",
            status_code=503,
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_frontend(full_path: str):  # noqa: ANN202
        rel = full_path.strip("/")
        # Machine prefixes never fall back to HTML: an unregistered
        # /api/... (typo'd fetch, removed endpoint) must 404, not return
        # the SPA shell with a 200.
        if rel.split("/", 1)[0] in ("api", "ws", "files"):
            return PlainTextResponse("not found", status_code=404)
        target = (out / rel) if rel else (out / "index.html")
        try:
            resolved = target.resolve()
            resolved.relative_to(out.resolve())  # path-traversal guard
        except (OSError, ValueError):
            return _spa_fallback("")
        if resolved.is_file():
            return _file_response(resolved)
        if resolved.is_dir() and (resolved / "index.html").is_file():
            return _file_response(resolved / "index.html")
        # extensionless page path → its exported .html
        html = resolved.with_suffix(".html") if not resolved.suffix else None
        if html is not None and html.is_file():
            return _file_response(html)
        return _spa_fallback(rel)
