"""Serve the static design-documentation site at /docs.

The site is built by ``python -m tools.docs_site.build`` into ``docs/_site/``.
We mount it as static files so every page, asset, and the search index are
served from the same single port as the rest of the web UI.
"""
from __future__ import annotations

from pathlib import Path


def _site_dir() -> Path:
    # openprogram/webui/routes/docs.py → repo_root/openprogram/webui/routes/
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "docs" / "_site"


def register(app) -> None:
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    site = _site_dir()
    if site.is_dir():
        # html=True → serving /docs/ resolves to index.html, and extensionless
        # paths fall back to <name>.html.
        app.mount("/docs", StaticFiles(directory=str(site), html=True), name="docs")
    else:
        @app.get("/docs")
        async def docs_missing():
            return JSONResponse(
                status_code=404,
                content={
                    "error": "docs site not built",
                    "hint": "run: python -m tools.docs_site.build",
                },
            )
