"""``core.md`` — always-on memory snapshot.

Tiny (<2 KB) document injected into every agent's system prompt at
session start. Frozen during a session so the prefix cache survives.

Source under the new schema:

  1. The body of the wiki page named ``Core`` (any folder), or
  2. The body of ``User Preferences`` if no Core page, or
  3. A short folder-tree snippet as last-resort placeholder.

Sleep's deep phase calls :func:`refresh_from_wiki` to rewrite this.
"""
from __future__ import annotations

from pathlib import Path

from . import store
from .schema import today_iso

CORE_BUDGET_CHARS = 2048
CORE_HEADER = "OpenProgram memory (machine-wide)"


def read() -> str:
    path = store.core()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_raw(body: str, *, last_consolidated: str = "") -> Path:
    rule = "═" * 60
    when = last_consolidated or today_iso()
    body = body.strip()
    used = len(body)
    pct = int(round(used / CORE_BUDGET_CHARS * 100))
    head = (
        f"{rule}\n"
        f"{CORE_HEADER} — {pct}% ({used}/{CORE_BUDGET_CHARS} chars), "
        f"last consolidated {when}\n"
        f"{rule}\n\n"
    )
    foot = "\n\n[for full context start with `memory_browse`]\n"
    path = store.core()
    path.write_text(head + body + foot, encoding="utf-8")
    return path


def system_prompt_block() -> str:
    raw = read().strip()
    has_wiki = any(
        p for p in store.wiki_dir().rglob("*.md")
        if p.name not in store.GOVERNANCE_PAGES
    )
    if not raw and not has_wiki:
        return ""

    pointer = (
        "Memory tools: `memory_browse` (folder tree + recent days), "
        "`memory_get(target)` (read a wiki page by filename or a "
        "`YYYY-MM-DD` short-term day), `memory_recall(query)` (FTS "
        "fallback), `memory_reflect(query)` (multi-page synthesis), "
        "`memory_note(...)` (record observation), `memory_ingest` "
        "(manual consolidation), `memory_lint` (health check). "
        "Browse before recalling."
    )
    if raw:
        return raw.rstrip() + "\n\n" + pointer
    return pointer


def refresh_from_wiki() -> Path:
    """Rewrite ``core.md`` from the wiki state.

    Hunts for a top-level Core / User Preferences page; falls back to
    a folder-tree snippet.
    """
    from . import wiki
    from .wiki_helpers import parse_frontmatter

    body = ""
    for name in ("Core", "User Preferences", "User"):
        p = wiki.find(name)
        if p is None:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        _fm, page_body = parse_frontmatter(text)
        body = page_body.strip()
        if body:
            break

    if not body:
        tree = wiki.tree(max_depth=2).strip()
        if tree:
            body = (
                "Top-level topics — use `memory_browse` for the catalog, "
                "`memory_get <Name>` to read a page.\n\n"
                f"```\n{tree}\n```\n"
            )

    return write_raw(body)
