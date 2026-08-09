"""Memory routes — topic pages, the timeline, and core memory.

Pure filesystem reads over ``openprogram.memory.store`` paths; no
server.py module state.

The route names still say ``wiki`` and ``journal``. They now serve
``topics/`` and ``timeline/``, which occupy the same two places in the
UI — curated pages, and a time axis. The paths are kept so the memory
tab keeps working; renaming them means changing the frontend too, and
that is a rename, not a behaviour change.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import JSONResponse


def _title_of(text: str, fallback: str) -> str:
    """The first Markdown heading, which is how topic files name themselves."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def register(app):
    @app.get("/api/memory/wiki")
    async def list_topic_pages():
        from openprogram.memory import store
        root = store.topics_dir()
        pages = []
        for path in sorted(root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            stat = path.stat()
            relative = path.relative_to(root)
            pages.append({
                "path": str(relative),
                "title": _title_of(text, path.stem),
                # The subject grouping is the directory: topics/people/…
                "type": relative.parts[0] if len(relative.parts) > 1 else "",
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return JSONResponse(content=pages)

    @app.get("/api/memory/wiki/{path:path}")
    async def get_topic_page(path: str):
        from openprogram.memory import store
        root = store.topics_dir()
        target = (root / path).resolve()
        if not str(target).startswith(str(root.resolve())):
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        return JSONResponse(content={
            "path": path,
            "content": target.read_text(encoding="utf-8"),
        })

    @app.delete("/api/memory/wiki/{path:path}")
    async def delete_topic_page(path: str):
        from openprogram.memory import store
        root = store.topics_dir()
        target = (root / path).resolve()
        if not str(target).startswith(str(root.resolve())):
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        target.unlink()
        return JSONResponse(content={"ok": True})

    @app.get("/api/memory/journal")
    async def list_timeline_days():
        """The dates the timeline covers, newest first.

        ``timeline/`` nests by year and month, so the day files are
        found by glob rather than by listing one directory.
        """
        from openprogram.memory import store
        root = store.timeline_dir()
        if not root.is_dir():
            return JSONResponse(content=[])
        days = sorted(
            {
                "-".join(path.relative_to(root).with_suffix("").parts)
                for path in root.rglob("*.md")
            },
            reverse=True,
        )
        return JSONResponse(content=days)

    @app.get("/api/memory/journal/{date}")
    async def get_timeline_day(date: str):
        from openprogram.memory import store
        root = store.timeline_dir()
        target = (root / Path(*date.split("-"))).with_suffix(".md")
        resolved = target.resolve()
        if not str(resolved).startswith(str(root.resolve())):
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not resolved.is_file():
            return JSONResponse(content={"date": date, "content": ""})
        return JSONResponse(content={
            "date": date,
            "content": resolved.read_text(encoding="utf-8"),
        })

    @app.get("/api/memory/core")
    async def get_core():
        from openprogram.memory import store
        path = store.core()
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        return JSONResponse(content={"content": content})

    @app.get("/api/memory/wiki-system")
    async def list_derived_views():
        """Views the runtime maintains, which are read-only to everyone else."""
        from openprogram.memory import store
        root = store.root()
        names = ("recent_events.jsonl", "relations.json")
        return JSONResponse(content=[
            {"path": name, "size": (root / name).stat().st_size}
            for name in names if (root / name).is_file()
        ])
