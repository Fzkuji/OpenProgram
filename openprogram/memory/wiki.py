"""Wiki access — path-based API over the Obsidian-style vault.

This is the thin read/find layer. Writes go through agentic ingest
(``ingest.py``) or pure-Python ops in ``wiki_ops.py``.

Vault model (hybrid RAH + nashsu):
  * Folder hierarchy IS the taxonomy.
  * Filename IS the node id.
  * `type:` frontmatter carries a 7-valued semantic role.
"""
from __future__ import annotations

from pathlib import Path

from . import store
from . import wiki_helpers as h


def root() -> Path:
    return store.wiki_dir()


def find(name: str) -> Path | None:
    """Find a page by filename stem (case-insensitive)."""
    return h.find_node(root(), name)


def read(target: str | Path) -> str | None:
    """Read a page by path (relative to vault root) or by filename stem.
    Returns the full markdown text or ``None`` if not found."""
    if isinstance(target, Path):
        path = target
    else:
        s = str(target).strip()
        if "/" in s or s.endswith(".md"):
            path = root() / s
            if not path.suffix:
                path = path.with_suffix(".md")
        else:
            found = find(s)
            if found is None:
                return None
            path = found
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def tree(*, max_depth: int = 8) -> str:
    return h.folder_tree(root(), max_depth=max_depth)


def iter_pages():
    yield from h.iter_md_files(root())


def page_type(path: Path) -> str | None:
    """Read the ``type:`` frontmatter value of a page, or ``None``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, _ = h.parse_frontmatter(text)
    t = fm.get("type")
    return t if isinstance(t, str) else None


def pages_of_type(t: str) -> list[Path]:
    """All pages where ``type: t`` (case-insensitive). Linear scan."""
    out: list[Path] = []
    for p in iter_pages():
        if page_type(p) == t:
            out.append(p)
    return out
