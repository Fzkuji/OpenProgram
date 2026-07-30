"""Serve the Next.js static export (``web/out/``) from the worker itself.

Single-port architecture (docs/reference/design/cli/single-port.md): the
FastAPI backend is the sole origin. ``mount_frontend`` registers, LAST in
``create_app()``, a catch-all GET that serves files from ``web/out/`` and
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
    """Repo-root ``web/`` directory."""
    # openprogram/webui/frontend.py → repo_root/web
    return Path(__file__).resolve().parents[2] / "web"


def out_dir() -> Path:
    return web_dir() / "out"


# --- build gate -------------------------------------------------------------

# Source roots whose newest mtime invalidates the export.
_SOURCE_ROOTS = ("app", "components", "lib", "package.json")


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
    """Build ``web/out/`` if missing or stale. Raises on build failure.

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
                "are unavailable — reinstall with a prebuilt web/out/."
            )
        return

    if shutil.which("npm") is None:
        if marker.exists():
            print("[worker] web: npm not found — serving existing (stale) web/out/")
            return
        raise RuntimeError(
            f"frontend export missing at {out_dir()} and npm is not in PATH. "
            "Install Node.js (build-time only) or ship a prebuilt web/out/."
        )

    if not (wd / "node_modules").exists():
        print("[worker] web: installing npm deps (first run, may take a while)…")
        _run(["npm", "install", "--silent"], wd, "npm install")
    print("[worker] web: building frontend export (npx next build)…")
    _run(["npx", "next", "build"], wd, "next build")
    if not marker.exists():
        raise RuntimeError(f"next build succeeded but {marker} was not produced")
    print("[worker] web: frontend export ready")


# --- serving ----------------------------------------------------------------


def mount_frontend(app) -> None:
    """Register static serving + SPA fallback. Call LAST in create_app()."""
    from fastapi.responses import FileResponse, PlainTextResponse

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
