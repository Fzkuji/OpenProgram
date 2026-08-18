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
        from openprogram.store import _store
        shim = _store.get()
        return shim.session_id if shim is not None else None
    except Exception:
        return None


def mark_seen(abs_path: str, *, session_id: Optional[str] = None) -> None:
    """Record the file's current on-disk state as the agent's baseline.

    Called after a successful read (the agent now knows this content)
    and after a successful write (the agent just produced this content).
    No-op outside a session, or if the file can't be fingerprinted.
    """
    if not abs_path:
        return
    sid = session_id or _current_session()
    if not sid:
        return
    fp = _fingerprint(abs_path)
    if fp is None:
        return
    key = os.path.abspath(abs_path)
    with _lock:
        _seen.setdefault(sid, {})[key] = fp


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
    key = os.path.abspath(abs_path)
    with _lock:
        baseline = _seen.get(sid, {}).get(key)
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
            f"Error: {abs_path} has not been read in this session. "
            f"Read the file first so you're editing its current contents, "
            f"then retry."
        )
    # STALE
    return (
        f"Error: {abs_path} has changed on disk since you last read it "
        f"(edited by the user, a linter, or another process). Re-read the "
        f"file to see the current contents, then redo your edit. Your edit "
        f"was NOT applied, so nothing was overwritten."
    )


def forget_session(session_id: str) -> None:
    """Drop a session's baselines (e.g. on session delete). Best-effort."""
    with _lock:
        _seen.pop(session_id, None)


__all__ = [
    "mark_seen", "check_fresh", "stale_message", "forget_session",
    "FRESH", "UNTRACKED", "NEVER_READ", "STALE",
]
