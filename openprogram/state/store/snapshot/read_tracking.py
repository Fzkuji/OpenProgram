"""Read-before-edit freshness tracking (Claude-Code-style).

Stops the agent from clobbering a file the user changed concurrently.
Mirrors Claude Code's Edit/Write contract:

  * Before the agent edits an EXISTING file, it must have *read* that
    file in this session, AND the file must not have changed on disk
    since that read. If it changed (the user edited it in their editor,
    a linter rewrote it, …), the edit is refused and the agent is told
    to re-read — so it never overwrites work it hasn't seen.
  * NEW files skip the check (you can't read what doesn't exist).
  * A successful read OR write updates the baseline, so the agent can
    keep editing a file it just touched without re-reading every time.

State is per-session, in memory, keyed by absolute path. We snapshot a
cheap fingerprint — ``(mtime_ns, size, sha1)`` — at read/write time and
compare it at the next write. We include the content hash (not just
mtime) because mtime alone is fragile: a fast user edit can land in the
same mtime tick, and some tools preserve mtime. The hash is only taken
on files we're about to read/write anyway, so it's not extra I/O of note.

Resolution of "which session" reuses the same ``_store`` ContextVar the
file-backup helper uses, so it's a no-op outside a dispatcher-driven
turn (standalone tool calls, unit tests) — exactly like backups.

Inside a turn the baseline belongs to the branch, and is derived from the
branch's own history (Claude Code rebuilds its read state from the
conversation the same way, ``extractReadFilesFromMessages``): each read or
write is recorded against the turn it happened in (its reply node) in the
conversation's ``read_baselines.jsonl``, and a check takes the newest
record for that file whose turn lies on this branch's line, after the
latest compaction on it. So the baseline does not depend on which process
ran the read (a scheduled run, the CLI, the Worker), a branch made from an
older point sees only the reads above that point, a rewind's retracted
turns no longer count, two branches never share one, and a compaction
makes the model read again. Paths are compared as
``os.path.normcase(os.path.abspath(p))``; an SSH environment's paths are
POSIX paths and compared as they are, under their own ``env`` key.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class _Fingerprint:
    mtime_ns: int
    size: int
    sha1: str


# session_id -> { abs_path -> _Fingerprint }. In-memory; rebuilt per
# process. A stale entry only ever causes a *conservative* refusal
# (agent re-reads), never a silent clobber, so we don't persist it.
_seen: dict[str, dict[str, _Fingerprint]] = {}
_lock = threading.Lock()


def _fingerprint(abs_path: str) -> Optional[_Fingerprint]:
    """Cheap content fingerprint, or None if the file can't be read."""
    try:
        st = os.stat(abs_path)
        with open(abs_path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()
        return _Fingerprint(mtime_ns=st.st_mtime_ns, size=st.st_size, sha1=digest)
    except OSError:
        return None


def _current_session() -> Optional[str]:
    """The active session id (via the dispatcher's ContextVar), or None
    when there's no turn in flight — in which case tracking no-ops and
    the freshness check always passes (standalone/unit usage)."""
    try:
        from openprogram.state.store import _store
        shim = _store.get()
        return shim.session_id if shim is not None else None
    except Exception:
        return None


LOCAL_ENV = "local"
LEDGER_FILE = "read_baselines.jsonl"


def path_key(path: str, env: str = LOCAL_ENV) -> str:
    """How a path is compared: case- and separator-normalized on the local
    disk (Windows), as given on a remote POSIX host."""
    if env != LOCAL_ENV:
        return path
    return os.path.normcase(os.path.abspath(path))


def _current_anchor() -> Optional[str]:
    """The node of the turn in flight that a read is recorded against: its
    reply (a tool run in a subprocess carries ``<reply>|node:<id>``)."""
    try:
        from openprogram.state.store import _current_turn_id
        raw = _current_turn_id.get() or ""
    except Exception:
        return None
    raw = raw.split("|node:", 1)[0]
    if raw.startswith("pred:"):
        raw = raw[len("pred:"):]
    return raw if raw and raw != "ROOT" else None


def _ledger(session_id: str):
    try:
        from openprogram.state.store.current import current_store
        db = current_store()
        if not db._session_dir(session_id).exists():  # noqa: SLF001 - as todos.json
            return None
        return db.state_file(session_id, LEDGER_FILE)
    except Exception:
        return None


def _line_of(session_id: str, anchor: str) -> Optional[tuple[set, float]]:
    """The node ids on the line ending at ``anchor`` and the time of the
    latest compaction on it (reads before it no longer count)."""
    try:
        from openprogram.state.store.current import current_store
        chain = current_store().get_branch(session_id, anchor) or []
    except Exception:
        return None
    if not chain:
        return None
    since = 0.0
    for message in chain:
        if str(message.get("id") or "").startswith("summary_"):
            stamp = message.get("timestamp") or (message.get("metadata") or {}).get("compacted_at") or 0
            try:
                since = max(since, float(stamp))
            except (TypeError, ValueError):
                pass
    return {message.get("id") for message in chain}, since


def record_baseline(session_id: str, env: str, path: str, digest: str, size: int) -> Optional[dict]:
    """Record what the turn in flight saw of ``path``; the record, or
    ``None`` outside a turn (then only this process remembers it)."""
    anchor = _current_anchor()
    ledger = _ledger(session_id) if anchor else None
    if ledger is None:
        return None
    import json
    import time
    entry = {"node": anchor, "env": env, "path": path_key(path, env), "hash": digest,
             "size": size, "ts": time.time()}
    with _lock:
        lines = _ledger_lines(ledger)
        lines.append(json.dumps(entry))
        ledger.write_text("\n".join(lines[-LEDGER_LIMIT:]) + "\n", encoding="utf-8")
    return entry


LEDGER_LIMIT = 2000


def _ledger_lines(ledger) -> list[str]:
    try:
        return [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def branch_baseline(session_id: str, env: str, path: str) -> tuple[bool, Optional[dict]]:
    """``(tracked, record)``: whether the branch's history decides (a turn
    is in flight on a recorded line) and, if so, the newest record of
    ``path`` on that line since its latest compaction."""
    anchor = _current_anchor()
    ledger = _ledger(session_id) if anchor else None
    if ledger is None:
        return False, None
    line = _line_of(session_id, anchor)
    if line is None:
        return False, None
    on_line, since = line
    key = path_key(path, env)
    import json
    found = None
    with _lock:
        lines = _ledger_lines(ledger)
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if (entry.get("path") == key and entry.get("env") == env
                and entry.get("node") in on_line and float(entry.get("ts") or 0) > since):
            found = entry
    return True, found


def mark_seen(abs_path: str, *, session_id: Optional[str] = None) -> Optional[dict]:
    """Record the file's current on-disk state as the agent's baseline.

    Called after a successful read (the agent now knows this content)
    and after a successful write (the agent just produced this content).
    Inside a turn the record goes on the branch's history (returned);
    outside one only this process remembers it. No-op outside a session,
    or if the file can't be fingerprinted.
    """
    if not abs_path:
        return None
    sid = session_id or _current_session()
    if not sid:
        return None
    fp = _fingerprint(abs_path)
    if fp is None:
        return None
    key = path_key(abs_path)
    with _lock:
        _seen.setdefault(sid, {})[key] = fp
    return record_baseline(sid, LOCAL_ENV, abs_path, fp.sha1, fp.size)


# Outcome codes from :func:`check_fresh`.
FRESH = "fresh"             # safe to write
UNTRACKED = "untracked"     # no session in flight → tracking disabled, allow
NEVER_READ = "never-read"   # existing file the agent never read → refuse
STALE = "stale"             # changed on disk since the agent last saw it → refuse


def check_fresh(abs_path: str, *, session_id: Optional[str] = None) -> str:
    """Is it safe for the agent to write ``abs_path`` right now?

    Returns one of :data:`FRESH` / :data:`UNTRACKED` / :data:`NEVER_READ`
    / :data:`STALE`. The caller treats FRESH and UNTRACKED as "go ahead"
    (UNTRACKED = no active turn, so the Claude-Code contract doesn't
    apply); NEVER_READ and STALE are refusals with distinct messages.

    Only meaningful for EXISTING files — the caller skips the check for
    new-file creation.
    """
    sid = session_id or _current_session()
    if not sid:
        return UNTRACKED
    tracked, entry = branch_baseline(sid, LOCAL_ENV, abs_path)
    if tracked:
        baseline = (_Fingerprint(mtime_ns=0, size=int(entry.get("size") or 0), sha1=str(entry.get("hash")))
                    if entry is not None else None)
    else:
        with _lock:
            baseline = _seen.get(sid, {}).get(path_key(abs_path))
    if baseline is None:
        return NEVER_READ
    current = _fingerprint(abs_path)
    if current is None:
        # File vanished since we saw it — treat as stale (something else
        # touched it); the agent should re-read / re-evaluate.
        return STALE
    if current.sha1 != baseline.sha1 or current.size != baseline.size:
        return STALE
    return FRESH


def stale_message(abs_path: str, outcome: str) -> str:
    """The Claude-Code-style refusal text for a failed freshness check."""
    if outcome == NEVER_READ:
        return (
            f"Error: {abs_path} already exists but has not been read in this "
            f"session (a shell command may have created it). Read the file "
            f"first so you're editing its current contents, then retry."
        )
    # STALE
    return (
        f"Error: {abs_path} has changed on disk since you last read it "
        f"(by a shell command, the user, a linter, or another process). Re-read the "
        f"file to see the current contents, then redo your edit. Your edit "
        f"was NOT applied, so nothing was overwritten."
    )


def forget_session(session_id: str) -> None:
    """Drop a session's baselines (e.g. on session delete). Best-effort."""
    with _lock:
        _seen.pop(session_id, None)


__all__ = [
    "mark_seen", "check_fresh", "stale_message", "forget_session",
    "record_baseline", "branch_baseline", "path_key", "LOCAL_ENV",
    "FRESH", "UNTRACKED", "NEVER_READ", "STALE",
]
