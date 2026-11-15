"""Cross-session message search backed by ``ripgrep``.

Replaces SQLite FTS5. ``ripgrep`` is invoked as a subprocess against
the ``~/.openprogram/projects/*/chats/*/main.jsonl`` logs. Matches are
parsed, mapped back to ``{session_id, node_id}`` tuples, and rendered
into the same message-dict shape the old ``DagSessionDB.search_messages``
returned.

If ripgrep is not on PATH, falls back to a pure-Python scan (slower
but functional). Both paths return the newest ``limit`` results.
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

# ``openprogram.runtime.permissions.identity.CHECK_IN_SPEAKER``; state does not import runtime.
_CHECK_IN_SPEAKER = "runtime/always_on"


def _have_rg() -> bool:
    return shutil.which("rg") is not None


def search_messages(
    store: "SessionStore",
    query: str,
    *,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the ``limit`` newest messages whose text matches ``query``.

    A message matches on its own content, not on its stored metadata. Prompts
    the chat does not draw, scheduled runs, always-on check-ins and merged
    branches are left out.

    Scoped to one session if ``session_id`` given. ``agent_id`` further
    filters by the owning agent (applied via per-session meta lookup).
    """
    root = store.root_path
    if not root.exists() or not query.strip():
        return []
    # Where to search: one session's history or every project's chats.
    if session_id:
        targets = [store._session_dir(session_id)]
    else:
        targets = sorted(d for d in root.glob("*/chats") if d.is_dir())
    targets = [t for t in targets if t.is_dir()]
    if not targets:
        return []

    sessions = _rg_search(targets, query) if _have_rg() else _python_search(targets, query)
    try:
        matcher = re.compile(query, re.IGNORECASE)
    except re.error:
        matcher = re.compile(re.escape(query), re.IGNORECASE)

    hits: list[dict[str, Any]] = []
    for sid in sessions:
        sess = store.get_session(sid) or {}
        if agent_id is not None and sess.get("agent_id") != agent_id:
            continue
        for node in _shown_matches(store, sid, matcher):
            node["session_title"] = sess.get("title") or ""
            node["session_source"] = sess.get("source") or ""
            node["session_agent_id"] = sess.get("agent_id") or ""
            hits.append(node)
    hits.sort(key=lambda node: _time_of(node.get("timestamp")), reverse=True)
    return hits[:limit]


def _time_of(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "" if content is None else json.dumps(content, ensure_ascii=False, default=str)


def _shown_matches(store: "SessionStore", session_id: str, matcher: re.Pattern) -> list[dict[str, Any]]:
    """The session's messages whose own text matches and that a person can read."""
    rows = store.get_messages(session_id)
    matches = [row for row in rows if matcher.search(_text_of(row))]
    if not matches:
        return []
    hidden = _hidden_branch_nodes(store, session_id, rows)
    return [dict(row) for row in matches  # don't mutate cache
            if row.get("display") != "runtime" and row.get("id") not in hidden]


def _automatic_prompt(row: dict[str, Any]) -> bool:
    """The prompt of a scheduled run or an always-on check-in."""
    return row.get("source") == "scheduler" or row.get("speaker_id") == _CHECK_IN_SPEAKER


def _typed_message(row: dict[str, Any]) -> bool:
    return row.get("role") == "user" and row.get("display") != "runtime" and row.get("speaker_kind") != "runtime"


def _hidden_branch_nodes(store: "SessionStore", session_id: str, rows: list[dict[str, Any]]) -> set[Any]:
    """Nodes of automatic runs, and nodes only on a merged branch.

    A check-in or scheduled run is hidden from its own prompt on, finished or
    not, so it says nothing about the line it left. Where a person replied on
    such a branch, that reply and what follows are an ordinary chat.
    """
    by_id = {row.get("id"): row for row in rows}

    def ancestors(tips: Any) -> set[Any]:
        seen: set[Any] = set()
        stack = list(tips)
        while stack:
            node_id = stack.pop()
            if node_id in by_id and node_id not in seen:
                seen.add(node_id)
                stack.append(by_id[node_id].get("predecessor"))
        return seen

    def spread(nodes: set[Any]) -> set[Any]:
        for row in rows:  # The log is in write order: a parent comes before its children.
            if (_automatic_prompt(row) or row.get("caller") in nodes
                    or (row.get("predecessor") in nodes and not _typed_message(row))):
                nodes.add(row.get("id"))
        return nodes

    hidden = spread(set())
    merged = store.merged_heads(session_id) - hidden
    if not merged:
        return hidden
    # The line the conversation is on counts even when a merged branch is its tip.
    head = (store.get_session(session_id) or {}).get("head_id")
    tips = {branch["head_msg_id"] for branch in store.list_branches(session_id)} | {head}
    return spread(hidden | (ancestors(merged) - ancestors(tips - hidden)))


def _log_files(target: Path) -> list[Path]:
    """The log files of one session directory or of a project's chats."""
    if (target / "main.jsonl").exists() or (target / "agents").is_dir():
        return [*target.glob("main.jsonl"), *target.glob("agents/*.jsonl")]
    return [*target.glob("*/main.jsonl"), *target.glob("*/agents/*.jsonl")]


def _session_of(log_file: Path) -> str:
    parent = log_file.parent
    return parent.parent.name if parent.name == "agents" else parent.name


def _rg_search(targets: list[Path], query: str) -> list[str]:
    """Session ids whose logs mention ``query``."""
    try:
        cp = subprocess.run(
            ["rg", "--files-with-matches", "--no-messages",
             "--glob", "*.jsonl", "--max-count", "1",
             "-i", "--", query, *map(str, targets)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if cp.returncode not in (0, 1):  # 1 = no match (not an error)
        return []
    found = [Path(line) for line in cp.stdout.splitlines() if line.strip()]
    return list(dict.fromkeys(
        _session_of(path) for path in found
        if path.name == "main.jsonl" or path.parent.name == "agents"
    ))


def _python_search(targets: list[Path], query: str) -> list[str]:
    """Slow fallback when ripgrep isn't installed. Linear scan."""
    needle = query.lower()
    out: dict[str, None] = {}
    for target in targets:
        for path in _log_files(target):
            try:
                content = path.read_text(errors="ignore", encoding="utf-8")
            except OSError:
                continue
            if needle in content.lower():
                out[_session_of(path)] = None
    return list(out)
