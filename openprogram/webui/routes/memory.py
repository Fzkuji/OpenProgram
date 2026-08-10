"""Memory routes — topics, timeline, recent, and core.

Four surfaces, matching the four things memory holds:

  topics/   the editable semantic memory, one file per subject
  timeline/ the derived time axis
  recent    the last units written, derived
  core.md   the always-on block, rendered from topics/core.md

``sources/`` is deliberately absent: it is the append-only evidence
record, reachable through the footnotes of whatever cites it, and a
browser for it would invite editing what must not be edited.

Reads are plain filesystem. Writes go through the workspace so a hand
edit that drops a block ID or strands a footnote is refused rather than
silently breaking the views that reach through them.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

# How long a save waits for the background writer to finish its transaction
# before giving up. Long enough to cover one write, short enough that a
# click does not look hung.
WRITE_LOCK_TIMEOUT_S = 5.0


def _require_memory_enabled() -> None:
    from openprogram.memory import DISABLED_MESSAGE, is_enabled

    if not is_enabled():
        raise HTTPException(
            status_code=503,
            detail={"code": "MEMORY_DISABLED", "message": DISABLED_MESSAGE},
        )


def _title_of(text: str, fallback: str) -> str:
    """The first Markdown heading, which is how topic files name themselves."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _within(root: Path, relative: str) -> Path | None:
    """Resolve inside root, or None if the path climbs out of it."""
    from openprogram.memory.workspace_layout import resolve_within
    return resolve_within(root, relative)


def _staged_edit(
    root: Path,
    write: Callable[[Path], None],
    *,
    deleting: str = "",
) -> tuple[bool, str]:
    """Bind this module's lock timeout to the shared staged edit.

    The same transaction serves ``openprogram memory edit``; see
    ``management.transaction.staged_edit`` for why the baseline is read
    before anything is staged.
    """
    from openprogram.memory.management.transaction import staged_edit
    return staged_edit(
        root, write, deleting=deleting, timeout_s=WRITE_LOCK_TIMEOUT_S
    )


def register(app):
    router = APIRouter(dependencies=[Depends(_require_memory_enabled)])

    @router.get("/api/memory/status")
    def get_status():
        from openprogram.memory import store
        from openprogram.memory.retrieval import inspect

        return JSONResponse(content=inspect.status(store.ensure()))

    # -- topics ------------------------------------------------------------

    @router.get("/api/memory/topics")
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

    @router.get("/api/memory/topics/{path:path}")
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

    @router.put("/api/memory/topics/{path:path}")
    async def save_topic(path: str, request: Request):
        from openprogram.memory import store
        root = store.ensure()
        topics = store.topics_dir()
        target = _within(topics, path)
        if target is None or target == topics.resolve():
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        relative = target.relative_to(topics.resolve())
        content = (await request.json()).get("content", "")

        def write(stage: Path) -> None:
            staged = stage / "topics" / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(content, encoding="utf-8")

        ok, message = _staged_edit(root, write)
        if not ok:
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})

    @router.delete("/api/memory/topics/{path:path}")
    async def delete_topic(path: str):
        from openprogram.memory import store
        root = store.ensure()
        topics = store.topics_dir()
        target = _within(topics, path)
        if target is None:
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse(content={"error": "not found"}, status_code=404)
        relative = target.relative_to(topics.resolve())

        def write(stage: Path) -> None:
            (stage / "topics" / relative).unlink(missing_ok=True)

        # Dropping this topic's own blocks is the point of the request. A
        # block another topic still links to is a different matter, and the
        # install refuses it.
        ok, message = _staged_edit(root, write, deleting=relative.as_posix())
        if not ok:
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})

    # -- timeline ----------------------------------------------------------

    @router.get("/api/memory/timeline")
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

    @router.get("/api/memory/timeline/{date}")
    async def get_timeline_day(date: str):
        from openprogram.memory import store
        from openprogram.memory.markdown.models import (
            is_valid_temporal_value,
        )
        # The date becomes a path, so it is checked as a date first:
        # ``..`` split on "-" is a single part and has no suffix to replace.
        if not is_valid_temporal_value(date):
            return JSONResponse(content={"error": "forbidden"}, status_code=403)
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

    @router.get("/api/memory/recent")
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

    def _core_master() -> Path:
        """The file the always-on block is rendered from.

        Reading and editing both land on ``topics/core.md``. The root
        ``core.md`` is the render, cut to a token budget, so editing that
        would hand back a truncated file and save it as the whole thing.
        A workspace whose block has not been rendered yet still has its
        content at the root.
        """
        from openprogram.memory import store
        master = store.topics_dir() / "core.md"
        return master if master.is_file() else store.core()

    @router.get("/api/memory/core")
    async def get_core():
        path = _core_master()
        if not path.is_file():
            return JSONResponse(content={"content": "", "size": 0, "mtime": 0})
        stat = path.stat()
        return JSONResponse(content={
            "content": path.read_text(encoding="utf-8"),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })

    @router.put("/api/memory/core")
    async def save_core(request: Request):
        from openprogram.memory import store
        root = store.ensure()
        content = (await request.json()).get("content", "")

        def write(stage: Path) -> None:
            target = stage / "topics" / "core.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        # The block reaches every system prompt, so a malformed edit is
        # refused here rather than breaking the next render.
        ok, message = _staged_edit(root, write)
        if not ok:
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})

    app.include_router(router)
