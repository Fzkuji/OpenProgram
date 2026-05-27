"""Cross-session message search backed by ``ripgrep``.

Replaces SQLite FTS5. ``ripgrep`` is invoked as a subprocess against
the ``~/.agentic/sessions-git/*/history/*.json`` tree. Matches are
parsed, mapped back to ``{session_id, node_id}`` tuples, and rendered
into the same message-dict shape the old ``DagSessionDB.search_messages``
returned.

If ripgrep is not on PATH, falls back to a pure-Python scan (slower
but functional). Both paths cap at ``limit`` results.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_store import SessionStore


def _have_rg() -> bool:
    return shutil.which("rg") is not None


def _parse_history_path(p: Path) -> Optional[tuple[str, str]]:
    """Path → (session_id, node_id) or None.

    Expected shape: ``<root>/<session_id>/history/<NNNN>-<role>-<id>.json``.
    """
    try:
        if p.parent.name != "history":
            return None
        sid = p.parent.parent.name
        # Filename: NNNN-r-nodeid.json
        m = re.match(r"\d+-\w-(.+)\.json$", p.name)
        if not m:
            return None
        return sid, m.group(1)
    except (AttributeError, IndexError):
        return None


def search_messages(
    store: "SessionStore",
    query: str,
    *,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` message dicts matching ``query``.

    Scoped to one session if ``session_id`` given. ``agent_id`` further
    filters by the owning agent (applied via per-session meta lookup).
    """
    root = store.root_path
    if not root.exists() or not query.strip():
        return []
    # Where to search: one session subdir or the whole tree.
    target_dir = root / session_id / "history" if session_id else root

    hits: list[tuple[str, str]] = []
    if _have_rg():
        hits = _rg_search(target_dir, query, limit * 5)
    else:
        hits = _python_search(target_dir, query, limit * 5)

    # Filter by agent_id if requested; enrich each result like the
    # old DagSessionDB.search_messages did.
    out: list[dict[str, Any]] = []
    sess_cache: dict[str, dict] = {}

    def _sess_for(sid: str) -> Optional[dict]:
        if sid not in sess_cache:
            sess_cache[sid] = store.get_session(sid) or {}
        return sess_cache[sid]

    for sid, nid in hits:
        if agent_id is not None:
            sess = _sess_for(sid)
            if (sess or {}).get("agent_id") != agent_id:
                continue
        # Pull the node back through the store so we get the canonical
        # message-dict shape (with extra fields, session_title, etc.).
        msgs = store.get_messages(sid)
        node = next((m for m in msgs if m.get("id") == nid), None)
        if node is None:
            continue
        sess = _sess_for(sid)
        node = dict(node)  # don't mutate cache
        node["session_title"] = sess.get("title") or ""
        node["session_source"] = sess.get("source") or ""
        node["session_agent_id"] = sess.get("agent_id") or ""
        out.append(node)
        if len(out) >= limit:
            break
    return out


def _rg_search(target_dir: Path, query: str, max_hits: int) -> list[tuple[str, str]]:
    """Run ``rg --files-with-matches`` and parse paths into (sid, nid)."""
    try:
        cp = subprocess.run(
            ["rg", "--files-with-matches", "--no-messages",
             "--glob", "*.json", "--max-count", "1",
             "-i", query, str(target_dir)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode not in (0, 1):  # 1 = no match (not an error)
        return []
    out: list[tuple[str, str]] = []
    for line in cp.stdout.splitlines():
        if not line.strip():
            continue
        ref = _parse_history_path(Path(line))
        if ref:
            out.append(ref)
        if len(out) >= max_hits:
            break
    return out


def _python_search(target_dir: Path, query: str, max_hits: int) -> list[tuple[str, str]]:
    """Slow fallback when ripgrep isn't installed. Linear scan."""
    needle = query.lower()
    out: list[tuple[str, str]] = []
    # If target is a session's history dir, look only there; otherwise
    # iterate <sid>/history/*.json across all sessions.
    if target_dir.name == "history":
        paths = list(target_dir.glob("*.json"))
    else:
        paths = list(target_dir.glob("*/history/*.json"))
    for p in paths:
        try:
            content = p.read_text(errors="ignore", encoding="utf-8")
        except OSError:
            continue
        if needle in content.lower():
            ref = _parse_history_path(p)
            if ref:
                out.append(ref)
                if len(out) >= max_hits:
                    break
    return out
