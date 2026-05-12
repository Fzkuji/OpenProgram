"""Memory routes — wiki pages, journal, core, governance pages.

These handlers don't touch server.py module state; they're pure
filesystem ops over ``openprogram.memory.store`` paths. That makes
this group the safest extraction to do first.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/memory/wiki")
    async def list_wiki_pages():
        from openprogram.memory import store
        from openprogram.memory.wiki import helpers as h
        wdir = store.wiki_dir()
        pages = []
        for p in sorted(wdir.rglob("*.md")):
            if p.name in store.GOVERNANCE_PAGES:
                continue
            rel = str(p.relative_to(wdir))
            try:
                text = p.read_text(encoding="utf-8")
                fm, _ = h.parse_frontmatter(text)
            except Exception:
                fm = {}
            stat = p.stat()
            pages.append({
                "path": rel,
                "title": fm.get("title", p.stem),
                "type": fm.get("type", ""),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return JSONResponse(content=pages)

    @app.get("/api/memory/wiki/{path:path}")
    async def get_wiki_page(path: str):
        from openprogram.memory import store
        from openprogram.memory.wiki import helpers as h
        wdir = store.wiki_dir()
        target = (wdir / path).resolve()
        if not str(target).startswith(str(wdir.resolve())):
            return JSONResponse(content={"error": "invalid path"}, status_code=400)
        if not target.exists():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        text = target.read_text(encoding="utf-8")
        fm, _ = h.parse_frontmatter(text)
        return JSONResponse(content={"path": path, "content": text, "frontmatter": fm})

    @app.put("/api/memory/wiki/{path:path}")
    async def update_wiki_page(path: str, request: Request):
        from openprogram.memory import store
        wdir = store.wiki_dir()
        target = (wdir / path).resolve()
        if not str(target).startswith(str(wdir.resolve())):
            return JSONResponse(content={"error": "invalid path"}, status_code=400)
        body = await request.json()
        content = body.get("content", "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return JSONResponse(content={"ok": True})

    @app.delete("/api/memory/wiki/{path:path}")
    async def delete_wiki_page(path: str):
        from openprogram.memory import store
        wdir = store.wiki_dir()
        target = (wdir / path).resolve()
        if not str(target).startswith(str(wdir.resolve())):
            return JSONResponse(content={"error": "invalid path"}, status_code=400)
        if not target.exists():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        target.unlink()
        return JSONResponse(content={"ok": True})

    @app.get("/api/memory/journal")
    async def list_journal():
        from openprogram.memory import store
        files = []
        for p in sorted(store.journal_dir().glob("*.md")):
            stat = p.stat()
            files.append({"date": p.stem, "size": stat.st_size, "mtime": stat.st_mtime})
        return JSONResponse(content=files)

    @app.get("/api/memory/journal/{date}")
    async def get_journal(date: str):
        from openprogram.memory import store
        p = store.journal_for(date)
        if not p.exists():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        return JSONResponse(content={"date": date, "content": p.read_text(encoding="utf-8")})

    @app.get("/api/memory/core")
    async def get_core():
        from openprogram.memory import store
        p = store.core()
        content = p.read_text(encoding="utf-8") if p.exists() else ""
        size = p.stat().st_size if p.exists() else 0
        mtime = int(p.stat().st_mtime) if p.exists() else 0
        return JSONResponse(content={"content": content, "size": size, "mtime": mtime})

    @app.put("/api/memory/core")
    async def put_core(body: dict):
        from openprogram.memory import store
        p = store.core()
        p.write_text(body.get("content", ""), encoding="utf-8")
        return JSONResponse(content={"ok": True})

    @app.get("/api/memory/wiki-system")
    async def get_wiki_system():
        from openprogram.memory import store
        names = ["index.md", "log.md", "overview.md", "reflections.md"]
        result = []
        for name in names:
            p = store.wiki_dir() / name
            if p.exists():
                stat = p.stat()
                result.append({
                    "path": name,
                    "title": name.replace(".md", "").capitalize(),
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                })
        return JSONResponse(content=result)
