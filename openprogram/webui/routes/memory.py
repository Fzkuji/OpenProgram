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
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

# How long a save waits for the background writer to finish its transaction
# before giving up. Long enough to cover one write, short enough that a
# click does not look hung.
WRITE_LOCK_TIMEOUT_S = 5.0


def _title_of(text: str, fallback: str) -> str:
    """The first Markdown heading, which is how topic files name themselves."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _within(root: Path, relative: str) -> Path | None:
    """Resolve inside root, or None if the path climbs out of it.

    A shared string prefix is not containment: ``topics-private/`` begins
    with the same characters as ``topics/`` and is a different directory.
    Resolving first is what collapses ``..`` and follows symlinks, so the
    answer is about where the path actually lands.
    """
    root = root.resolve()
    target = (root / relative).resolve()
    return target if target.is_relative_to(root) else None


def _staged_edit(
    root: Path,
    write: Callable[[Path], None],
    *,
    deleting: str = "",
) -> tuple[bool, str]:
    """Apply a hand edit through the workspace stage, or not at all.

    ``write`` edits the staging copy the way it would edit the real tree.
    Someone editing a topic file by hand can drop a block ID or strand a
    footnote, and nothing else would notice until a later write failed, so
    the check runs here while the person who made the edit is still looking
    at it.

    Two things make that check mean something. The baseline is read from
    the committed workspace *before* anything is staged — read afterwards
    it would measure the edit against itself, and a dropped block ID would
    look like there never was one. And the edit lands only by installing a
    validated stage, so a rejected edit leaves the committed workspace
    byte-for-byte as it was rather than needing to be undone.

    ``deleting`` names a topic whose block IDs go away on purpose. Every
    other committed ID must still be reachable after the edit.
    """
    from openprogram.memory.scriptorium.management import MemoryWorkspace
    from openprogram.memory.scriptorium.management.transaction import (
        TransactionError, committed_baseline, install_state,
        workspace_write_lock,
    )

    try:
        # The lock spans staging, validation and install: the background
        # writer stages from this same tree and would otherwise install
        # over the edit, or be installed over by it.
        with workspace_write_lock(root, timeout_s=WRITE_LOCK_TIMEOUT_S):
            with closing(MemoryWorkspace(root)) as space:
                units, block_ids = committed_baseline(space)
                if deleting:
                    block_ids -= {
                        unit.memory_id for unit in units
                        if unit.topic_path == deleting
                    }
                write(space.stage_dir)
                install_state(space, units, block_ids)
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

    @app.delete("/api/memory/topics/{path:path}")
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
        from openprogram.memory.scriptorium.markdown.models import (
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
        content = (await request.json()).get("content", "")

        def write(stage: Path) -> None:
            (stage / "core.md").write_text(content, encoding="utf-8")

        # core.md is on every system prompt, so an oversized or malformed
        # one is refused here rather than trimmed later.
        ok, message = _staged_edit(root, write)
        if not ok:
            return JSONResponse(content={"error": message}, status_code=400)
        return JSONResponse(content={"ok": True})
