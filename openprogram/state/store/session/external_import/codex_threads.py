"""The Codex threads a person had, as Codex itself lists them.

Codex keeps one row per thread in its state database
(``<CODEX_HOME>/state_<n>.sqlite``, table ``threads``): where the rollout
is, the folder, the title, the model and the provider it runs on, and
whether it is archived. Reading it is how the Codex app's own history is
read without opening thousands of rollouts (``codex exec`` runs and
subagents are rows too). The rows are only read here, except a thread's
provider: Codex lists, and resumes, a thread only on the provider it was
saved with (magpie, ``codex_provider.go``), so moving where Codex sends
its requests moves the person's threads with it.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

#: Where a thread was started from that counts as a conversation: the
#: Codex app and the terminal. ``exec``, ``mcp`` and subagents are not.
INTERACTIVE_SOURCES = ("vscode", "cli")
#: The originator a thread OpenProgram's Codex engine started carries.
OPENPROGRAM_ORIGINATOR = "OpenProgram"


@dataclass
class Thread:
    id: str
    path: Path
    cwd: Optional[str]
    title: str
    model: str
    provider: str
    updated_at: float
    originator: str


def state_db(home: Path) -> Optional[Path]:
    """The newest ``state_<n>.sqlite`` of a Codex home."""
    best: tuple[int, Optional[Path]] = (-1, None)
    for path in home.glob("state_*.sqlite"):
        match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
        if match and int(match.group(1)) > best[0]:
            best = (int(match.group(1)), path)
    return best[1]


def _connect(path: Path, write: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode={'rw' if write else 'ro'}"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def threads(home: Path) -> list[Thread]:
    """The person's Codex conversations, newest first: not archived, started
    in the app or the terminal. (``has_user_event`` is not kept up to date
    by Codex; a thread with nothing said in it is found empty when read.)"""
    db = state_db(home)
    if db is None:
        return []
    marks = ",".join("?" for _ in INTERACTIVE_SOURCES)
    try:
        with _connect(db) as connection:
            rows = connection.execute(
                f"SELECT id, rollout_path, cwd, title, name, model, model_provider, updated_at,"
                f" updated_at_ms, originator FROM threads WHERE archived = 0"
                f" AND source IN ({marks}) ORDER BY updated_at DESC", INTERACTIVE_SOURCES).fetchall()
    except sqlite3.Error as exc:
        _log.info("Codex threads not read from %s: %s", db, exc)
        return []
    found = []
    for row in rows:
        from .codex import rollout_file
        path = rollout_file(Path(row["rollout_path"] or "")) if row["rollout_path"] else None
        if not row["id"] or path is None:
            continue
        updated = (row["updated_at_ms"] or 0) / 1000 or float(row["updated_at"] or 0)
        found.append(Thread(
            id=row["id"], path=path, cwd=row["cwd"] or None,
            title=(row["name"] or row["title"] or "").strip(), model=row["model"] or "",
            provider=row["model_provider"] or "openai", updated_at=updated,
            originator=row["originator"] or "",
        ))
    return found


def originator(path: Path) -> str:
    """The client that started a rollout, from its ``session_meta``."""
    try:
        from .codex import open_rollout
        with open_rollout(path) as fh:
            for line in fh:
                record = json.loads(line)
                if record.get("type") == "session_meta":
                    return str((record.get("payload") or {}).get("originator") or "")
                break
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def saved_provider(home: Path, thread_id: str) -> Optional[str]:
    """The provider Codex resumes ``thread_id`` on."""
    db = state_db(home)
    if db is None:
        return None
    try:
        with _connect(db) as connection:
            row = connection.execute("SELECT model_provider FROM threads WHERE id = ?", (thread_id,)).fetchone()
    except sqlite3.Error:
        return None
    return row["model_provider"] if row else None


def move_provider(home: Path, to_provider: str, *, from_provider: Optional[str] = None,
                  thread_ids: Optional[Iterable[str]] = None) -> int:
    """Save threads on ``to_provider``: those named, or every conversation
    on ``from_provider``. Only the row changes (Codex reads a paginated
    thread's provider there); the rollout and its messages stay as they
    are. How many moved."""
    db = state_db(home)
    if db is None:
        return 0
    where, args = ["model_provider != ?"], [to_provider]
    if from_provider is not None:
        where.append("model_provider = ?")
        args.append(from_provider)
    if thread_ids is not None:
        ids = list(thread_ids)
        if not ids:
            return 0
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        args += ids
    else:
        where.append(f"source IN ({','.join('?' for _ in INTERACTIVE_SOURCES)})")
        args += list(INTERACTIVE_SOURCES)
    try:
        with _connect(db, write=True) as connection:
            cursor = connection.execute(
                f"UPDATE threads SET model_provider = ? WHERE {' AND '.join(where)}", [to_provider, *args])
            return cursor.rowcount or 0
    except sqlite3.Error as exc:
        _log.warning("Codex threads not moved to %s: %s", to_provider, exc)
        return 0
