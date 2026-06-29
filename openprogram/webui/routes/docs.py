"""Serve the static design-documentation site at /docs.

The site is built by ``tools.docs_site.build`` into ``docs/_site/``. We mount it
as static files so every page, asset, and the search index are served from the
same single port as the rest of the web UI.

Auto-rebuild: before serving, we compare the newest mtime under ``docs/``
(sources) against ``docs/_site/`` (output). If a source is newer, we rebuild
once. So editing a doc + refreshing the browser is enough — no manual build
step, no watcher process. The check stats the tree (no file reads) and is
debounced, so it adds negligible latency.
"""
from __future__ import annotations

import time
from pathlib import Path


def _repo_root() -> Path:
    # openprogram/webui/routes/docs.py → repo_root/
    return Path(__file__).resolve().parents[3]


def _docs_dir() -> Path:
    return _repo_root() / "docs"


def _site_dir() -> Path:
    return _docs_dir() / "_site"


def _newest_mtime(root: Path, *, skip: set[str]) -> float:
    """Newest mtime of any .md/.html under root, skipping named subdirs."""
    newest = 0.0
    for p in root.rglob("*"):
        if p.suffix not in (".md", ".html"):
            continue
        if any(part in skip for part in p.relative_to(root).parts):
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest = m
    return newest


def _rebuild() -> None:
    # Reload the build modules from disk each time so edits to the build
    # scripts take effect without restarting the worker (otherwise the worker
    # keeps running — and re-emitting — the code it imported at startup, which
    # would overwrite freshly hand-built output with stale logic).
    import importlib
    from tools.docs_site import nav, search, template, build as _build
    for m in (nav, search, template, _build):
        importlib.reload(m)
    _build.build()


# Debounce: don't re-scan the tree on every single asset request in a burst.
_LAST_CHECK = 0.0
_CHECK_INTERVAL = 2.0  # seconds


def _maybe_rebuild() -> None:
    global _LAST_CHECK
    now = time.time()
    if now - _LAST_CHECK < _CHECK_INTERVAL:
        return
    _LAST_CHECK = now
    docs = _docs_dir()
    site = _site_dir()
    src_mtime = _newest_mtime(docs, skip={"_site", "images", "slides"})
    # Compare against the built index.html (always regenerated on each build).
    index = site / "index.html"
    try:
        out_mtime = index.stat().st_mtime
    except OSError:
        out_mtime = 0.0
    if src_mtime > out_mtime:
        try:
            _rebuild()
        except Exception as e:  # never let a build error 500 the whole route
            print(f"[docs] auto-rebuild failed: {e}")


def register(app) -> None:
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.types import Receive, Scope, Send

    repo_root = _repo_root()
    site = _site_dir()
    site.mkdir(parents=True, exist_ok=True)  # ensure mountable even pre-build

    class AutoRebuildStatic(StaticFiles):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                _maybe_rebuild()
            await super().__call__(scope, receive, send)

    # Make `tools` importable (it's a top-level package at the repo root).
    import sys
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # html=True → /docs/ resolves to index.html; extensionless paths fall back
    # to <name>.html.
    app.mount("/docs", AutoRebuildStatic(directory=str(site), html=True), name="docs")
