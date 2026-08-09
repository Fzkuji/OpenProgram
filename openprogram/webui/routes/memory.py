"""Memory routes — topics, timeline, recent, and core.

Four surfaces, matching the four things memory holds:

  topics/   the editable semantic memory, one file per subject
  timeline/ the derived time axis
  recent    the last units written, derived
  core.md   the always-on block

``sources/`` is deliberately absent: it is the append-only evidence
record, reachable through the footnotes of whatever cites it, and a
browser for it would invite editing what must not be edited.

Reads are plain filesystem. Writes go through the workspace so a hand
edit that drops a block ID or strands a footnote is refused rather than
silently breaking the views that reach through them.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse


def _title_of(text: str, fallback: str) -> str:
    """The first Markdown heading, which is how topic files name themselves."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _within(root: Path, relative: str) -> Path | None:
    """Resolve inside root, or None if the path climbs out of it."""
    target = (root / relative).resolve()
    if not str(target).startswith(str(root.resolve())):
        return None
    return target


def _revalidate(root: Path) -> tuple[bool, str]:
    """Reparse the workspace and rebuild derived views after a hand edit.

    Someone editing a topic file by hand can drop a block ID or strand a
    footnote. Nothing else would notice until a later write failed, so
    the check runs here, while the person who made the edit is still
    looking at it.
    """
    from openprogram.memory.management import MemoryWorkspace
    from openprogram.memory.management.transaction import (
        TransactionError, committed_baseline, install_state,
    )

    try:
        space = MemoryWorkspace(root)
        units, ids = committed_baseline(space)
        install_state(space, units, ids)
    except TransactionError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, ""


def register(app):
    # -- topics ------------------------------------------------------------

    @app.get("/api/memory/topics")
    async def list_topics():
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

    @app.get("/api/memory/topics/{path:path}")
    async def get_topic(path: str):
        from openprogram.memory import store
        target = _within(store.topics_dir(), path)
        if target is None:
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        return JSONResponse(content={
            "path": path,
            "content": target.read_text(encoding="utf-8"),
        })

    @app.put("/api/memory/topics/{path:path}")
    async def save_topic(path: str, request: Request):
        from openprogram.memory import store
        root = store.ensure()
        target = _within(store.topics_dir(), path)
        if target is None:
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        body = await request.json()
        previous = target.read_text(encoding="utf-8") if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get("content", ""), encoding="utf-8")
        ok, message = _revalidate(root)
        if not ok:
            # A rejected edit must not be left half-applied.
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(previous, encoding="utf-8")
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})

    @app.delete("/api/memory/topics/{path:path}")
    async def delete_topic(path: str):
        from openprogram.memory import store
        root = store.ensure()
        target = _within(store.topics_dir(), path)
        if target is None:
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        target.unlink()
        _revalidate(root)
        return JSONResponse(content={"ok": True})

    # -- timeline ----------------------------------------------------------

    @app.get("/api/memory/timeline")
    async def list_timeline_days():
        """The dates the timeline covers, newest first.

        ``timeline/`` nests by year and month, so the day files are found
        by glob rather than by listing one directory.
        """
        from openprogram.memory import store
        root = store.timeline_dir()
        if not root.is_dir():
            return JSONResponse(content=[])
        days = []
        for path in root.rglob("*.md"):
            stat = path.stat()
            days.append({
                "date": "-".join(path.relative_to(root).with_suffix("").parts),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        days.sort(key=lambda day: day["date"], reverse=True)
        return JSONResponse(content=days)

    @app.get("/api/memory/timeline/{date}")
    async def get_timeline_day(date: str):
        from openprogram.memory import store
        root = store.timeline_dir()
        target = _within(root, str(Path(*date.split("-")).with_suffix(".md")))
        if target is None:
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"date": date, "content": ""})
        return JSONResponse(content={
            "date": date,
            "content": target.read_text(encoding="utf-8"),
        })

    # -- recent ------------------------------------------------------------

    @app.get("/api/memory/recent")
    async def list_recent():
        """The last units written, newest first.

        Rebuilt from topics after every write, so this is a view of what
        memory learned lately rather than a store of its own.
        """
        from openprogram.memory import store
        path = store.root() / "recent_events.jsonl"
        if not path.is_file():
            return JSONResponse(content=[])
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        events.reverse()
        return JSONResponse(content=events)

    # -- core --------------------------------------------------------------

    @app.get("/api/memory/core")
    async def get_core():
        from openprogram.memory import store
        path = store.core()
        if not path.is_file():
            return JSONResponse(content={"content": "", "size": 0, "mtime": 0})
        stat = path.stat()
        return JSONResponse(content={
            "content": path.read_text(encoding="utf-8"),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })

    @app.put("/api/memory/core")
    async def save_core(request: Request):
        from openprogram.memory import store
        root = store.ensure()
        path = store.core()
        body = await request.json()
        previous = path.read_text(encoding="utf-8") if path.is_file() else None
        path.write_text(body.get("content", ""), encoding="utf-8")
        ok, message = _revalidate(root)
        if not ok:
            # core.md is on every system prompt, so an oversized or
            # malformed one is refused here rather than trimmed later.
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous, encoding="utf-8")
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})
